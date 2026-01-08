"""
Модуль для проверки свободного места на дисках агентов и отправки уведомлений
"""
import asyncio
from datetime import datetime
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import List, Dict, Any
import logging

from database import async_session_maker
from models import Agent, AgentDisk, Settings
from agent_client import AgentClient
from mattermost_client import MattermostClient

logger = logging.getLogger(__name__)


class DiskSpaceChecker:
    """Класс для проверки свободного места на дисках агентов"""
    
    def __init__(self, check_interval: int = 3600):
        self.check_interval = check_interval
        self.running = False
        self.last_warnings: Dict[str, datetime] = {}  # Кэш последних предупреждений для каждого диска
    
    async def start(self):
        """Запускает периодическую проверку"""
        self.running = True
        logger.info(f"Starting disk space checker with interval {self.check_interval} seconds")
        
        while self.running:
            try:
                await self.check_all_agents_disks()
            except Exception as e:
                logger.error(f"Error during disk space check: {e}")
            
            await asyncio.sleep(self.check_interval)
    
    def stop(self):
        """Останавливает проверку"""
        self.running = False
        logger.info("Stopping disk space checker")
    
    async def check_all_agents_disks(self):
        """Проверяет свободное место на дисках всех активных агентов"""
        async with async_session_maker() as session:
            # Получаем настройки
            result_settings = await session.execute(select(Settings))
            app_settings = result_settings.scalar_one_or_none()
            
            if not app_settings:
                logger.warning("Settings not found, skipping disk space check")
                return
            
            warning_threshold = app_settings.disk_space_warning_threshold or 10
            
            # Получаем всех активных агентов
            result_agents = await session.execute(
                select(Agent).where(Agent.is_active == True)
            )
            agents = result_agents.scalars().all()
            
            # Получаем информацию о дисках из базы данных
            for agent in agents:
                try:
                    result_disks = await session.execute(
                        select(AgentDisk).where(AgentDisk.agent_id == agent.id)
                    )
                    disks = result_disks.scalars().all()
                    
                    for disk in disks:
                        await self.check_disk_space(
                            agent, disk, warning_threshold, app_settings, session
                        )
                except Exception as e:
                    logger.error(f"Error checking disks for agent {agent.id} ({agent.ip_address}): {e}")
    
    async def check_disk_space(
        self, 
        agent: Agent, 
        disk: AgentDisk, 
        warning_threshold: int,
        settings: Settings,
        session: AsyncSession
    ):
        """Проверяет свободное место на конкретном диске и отправляет уведомление при необходимости"""
        if not disk.total_gb or disk.total_gb <= 0:
            return
        
        # Вычисляем процент свободного места
        free_percent = (disk.available_gb / disk.total_gb) * 100
        
        # Проверяем, достигнут ли порог
        if free_percent <= warning_threshold:
            # Создаем уникальный ключ для этого диска
            disk_key = f"{agent.id}_{disk.mount_point}"
            
            # Проверяем, не отправляли ли мы уже предупреждение недавно (чтобы не спамить)
            last_warning = self.last_warnings.get(disk_key)
            now = datetime.utcnow()
            
            # Отправляем предупреждение, если:
            # 1. Это первое предупреждение для этого диска
            # 2. Прошло более часа с последнего предупреждения
            # 3. Свободное место уменьшилось еще больше (на 5% или больше)
            should_send = False
            if not last_warning:
                should_send = True
            elif (now - last_warning).total_seconds() > 3600:  # Прошло более часа
                should_send = True
            
            if should_send and settings.mattermost_enabled and settings.mattermost_webhook_url:
                await self.send_disk_space_warning(
                    agent, disk, free_percent, warning_threshold, settings
                )
                self.last_warnings[disk_key] = now
    
    async def send_disk_space_warning(
        self,
        agent: Agent,
        disk: AgentDisk,
        free_percent: float,
        warning_threshold: int,
        settings: Settings
    ):
        """Отправляет предупреждение о нехватке свободного места в Mattermost"""
        try:
            mattermost_client = MattermostClient(settings.mattermost_webhook_url)
            
            # Определяем уровень критичности
            if free_percent <= 5:
                severity = "🔴 КРИТИЧЕСКОЕ"
                color = "#FF0000"
            elif free_percent <= warning_threshold * 0.5:
                severity = "🟠 ВЫСОКОЕ"
                color = "#FF8800"
            else:
                severity = "🟡 ПРЕДУПРЕЖДЕНИЕ"
                color = "#FFAA00"
            
            message = f"""
## {severity}: Нехватка свободного места на диске

**Агент:** {agent.name} ({agent.ip_address})
**Устройство:** {disk.device}
**Точка монтирования:** {disk.mount_point}
**Файловая система:** {disk.filesystem or 'неизвестно'}

**Статистика:**
- Всего места: {disk.total_gb:.2f} GB
- Использовано: {disk.used_gb:.2f} GB ({disk.used_percent:.1f}%)
- **Свободно: {disk.available_gb:.2f} GB ({free_percent:.1f}%)**

**Порог предупреждения:** {warning_threshold}%

⚠️ Рекомендуется освободить место на диске или увеличить его размер.
"""
            
            await mattermost_client.send_message(
                message,
                channel=settings.mattermost_channel
            )
            
            logger.info(
                f"Sent disk space warning for agent {agent.id} ({agent.ip_address}), "
                f"disk {disk.mount_point}: {free_percent:.1f}% free"
            )
        except Exception as e:
            logger.error(f"Error sending disk space warning to Mattermost: {e}")
