"""
Модуль для периодического опроса агентов
"""
import asyncio
from datetime import datetime
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import List
import logging

from database import async_session_maker
from models import Agent, AgentStatus, AgentBackupInfo, BackupTask
from agent_client import AgentClient
from s3_client import S3Client
from mattermost_client import MattermostClient

logger = logging.getLogger(__name__)


class AgentPoller:
    """Класс для периодического опроса агентов"""
    
    def __init__(self, poll_interval: int = 60):
        self.poll_interval = poll_interval
        self.running = False
    
    async def start(self):
        """Запускает периодический опрос"""
        self.running = True
        logger.info(f"Starting agent poller with interval {self.poll_interval} seconds")
        
        while self.running:
            try:
                await self.poll_all_agents()
            except Exception as e:
                logger.error(f"Error during agent polling: {e}")
            
            await asyncio.sleep(self.poll_interval)
    
    def stop(self):
        """Останавливает опрос"""
        self.running = False
        logger.info("Stopping agent poller")
    
    async def poll_all_agents(self):
        """Опрашивает всех активных агентов"""
        async with async_session_maker() as session:
            result = await session.execute(
                select(Agent).where(Agent.is_active == True)
            )
            agents = result.scalars().all()
            
            for agent in agents:
                try:
                    await self.poll_agent(agent, session)
                except Exception as e:
                    logger.error(f"Error polling agent {agent.id} ({agent.ip_address}): {e}")
    
    async def poll_agent(self, agent: Agent, session: AsyncSession):
        """Опрашивает одного агента"""
        client = AgentClient(agent.ip_address, agent.port)
        
        # Проверяем доступность
        is_online = await client.ping()
        
        # Получаем или создаем статус
        result = await session.execute(
            select(AgentStatus).where(AgentStatus.agent_id == agent.id)
        )
        agent_status = result.scalar_one_or_none()
        
        if not agent_status:
            agent_status = AgentStatus(agent_id=agent.id)
            session.add(agent_status)
        
        if is_online:
            # Получаем системную информацию
            system_info = await client.get_system_info()
            if system_info:
                agent_status.disk_free_gb = system_info.disk_free_gb
                agent_status.disk_total_gb = system_info.disk_total_gb
                agent_status.memory_free_mb = system_info.memory_free_mb
                agent_status.memory_total_mb = system_info.memory_total_mb
                agent_status.cpu_load_percent = system_info.cpu_load_percent
                agent_status.network_rx_mb = system_info.network_rx_mb
                agent_status.network_tx_mb = system_info.network_tx_mb
                agent.last_seen = datetime.utcnow()
            
            # Получаем информацию о бэкапах
            backup_info = await client.get_backup_info()
            if backup_info:
                await self.update_backup_info(agent, agent_status, backup_info, session)
        
        agent_status.is_online = is_online
        agent_status.last_update = datetime.utcnow()
        await session.commit()
    
    async def update_backup_info(self, agent: Agent, agent_status: AgentStatus, 
                                backup_info: List[dict], session: AsyncSession):
        """Обновляет информацию о бэкапах от агента"""
        from models import Settings
        
        # Получаем настройки для Mattermost
        result_settings = await session.execute(select(Settings))
        app_settings = result_settings.scalar_one_or_none()
        
        mattermost_client = None
        if app_settings and app_settings.mattermost_enabled and app_settings.mattermost_webhook_url:
            mattermost_client = MattermostClient(app_settings.mattermost_webhook_url)
        
        # Удаляем старую информацию
        result = await session.execute(
            select(AgentBackupInfo).where(AgentBackupInfo.agent_id == agent.id)
        )
        old_backups = result.scalars().all()
        old_backup_statuses = {b.task_id: b.status for b in old_backups}
        
        for old_backup in old_backups:
            await session.delete(old_backup)
        
        # Добавляем новую информацию
        for backup_data in backup_info:
            # Находим задачу по source_path
            result = await session.execute(
                select(BackupTask).where(
                    BackupTask.agent_id == agent.id,
                    BackupTask.source_path == backup_data.get("source_path")
                )
            )
            task = result.scalar_one_or_none()
            
            if task:
                new_status = backup_data.get("status", "unknown")
                
                # Обрабатываем даты
                backup_date = None
                if backup_data.get("backup_date"):
                    from dateutil import parser
                    try:
                        backup_date = parser.isoparse(backup_data["backup_date"])
                    except:
                        pass
                
                s3_upload_date = None
                if backup_data.get("s3_upload_date"):
                    from dateutil import parser
                    try:
                        s3_upload_date = parser.isoparse(backup_data["s3_upload_date"])
                    except:
                        pass
                
                backup_info_obj = AgentBackupInfo(
                    agent_id=agent.id,
                    agent_status_id=agent_status.id,
                    task_id=task.id,
                    source_path=backup_data.get("source_path"),
                    archive_name=backup_data.get("archive_name"),
                    backup_date=backup_date,
                    s3_upload_date=s3_upload_date,
                    archive_size_mb=backup_data.get("archive_size_mb"),
                    s3_path=backup_data.get("s3_path"),
                    status=new_status
                )
                session.add(backup_info_obj)
                
                # Отправляем уведомление если статус изменился на ошибку
                if new_status == "error" and mattermost_client:
                    old_status = old_backup_statuses.get(task.id, "unknown")
                    if old_status != "error":
                        await mattermost_client.send_backup_alert(
                            task.name,
                            backup_data.get("error_message", "Unknown error")
                        )

