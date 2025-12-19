"""
Модуль для генерации и отправки ежедневных отчетов
"""
import asyncio
from datetime import datetime, timedelta
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_
from typing import Dict, Any
import logging

from database import async_session_maker
from models import BackupTask, BackupHistory, AgentStatus, Settings
from mattermost_client import MattermostClient

logger = logging.getLogger(__name__)


class DailyReportGenerator:
    """Генератор ежедневных отчетов"""
    
    def __init__(self):
        self.running = False
    
    async def start(self):
        """Запускает генератор отчетов"""
        self.running = True
        logger.info("Daily report generator started")
        
        # Проверяем каждую минуту, нужно ли отправить отчет
        while self.running:
            try:
                await self.check_and_send_report()
            except Exception as e:
                logger.error(f"Error in daily report generator: {e}")
            
            await asyncio.sleep(60)  # Проверяем каждую минуту
    
    def stop(self):
        """Останавливает генератор"""
        self.running = False
        logger.info("Daily report generator stopped")
    
    async def check_and_send_report(self):
        """Проверяет, нужно ли отправить отчет и отправляет его"""
        async with async_session_maker() as session:
            result = await session.execute(select(Settings))
            settings = result.scalar_one_or_none()
            
            if not settings or not settings.mattermost_enabled or not settings.mattermost_daily_report:
                return
            
            if not settings.mattermost_webhook_url:
                return
            
            # Проверяем время
            report_time = settings.mattermost_report_time
            try:
                hour, minute = map(int, report_time.split(":"))
                now = datetime.now()
                if now.hour == hour and now.minute == minute:
                    # Отправляем отчет
                    report_data = await self.generate_report(session)
                    client = MattermostClient(settings.mattermost_webhook_url)
                    await client.send_daily_report(report_data)
                    logger.info("Daily report sent")
            except Exception as e:
                logger.error(f"Error parsing report time: {e}")
    
    async def generate_report(self, session: AsyncSession) -> Dict[str, Any]:
        """Генерирует отчет о бэкапах"""
        # Получаем все задачи
        result = await session.execute(select(BackupTask).where(BackupTask.is_active == True))
        tasks = result.scalars().all()
        
        total_tasks = len(tasks)
        successful = 0
        failed = 0
        warnings = 0
        failed_tasks = []
        disk_warnings = []
        
        # Проверяем последние выполнения за последние 24 часа
        yesterday = datetime.utcnow() - timedelta(days=1)
        
        for task in tasks:
            # Получаем последнее выполнение
            result = await session.execute(
                select(BackupHistory)
                .where(
                    and_(
                        BackupHistory.task_id == task.id,
                        BackupHistory.started_at >= yesterday
                    )
                )
                .order_by(BackupHistory.started_at.desc())
                .limit(1)
            )
            last_history = result.scalar_one_or_none()
            
            if last_history:
                if last_history.status == "success":
                    successful += 1
                elif last_history.status == "error":
                    failed += 1
                    failed_tasks.append({
                        "name": task.name,
                        "error": last_history.error_message or "Unknown error"
                    })
            else:
                # Нет выполнения за последние 24 часа
                warnings += 1
            
            # Проверяем место на диске агента
            if task.agent and task.agent.agent_status:
                status = task.agent.agent_status
                if status.disk_total_gb and status.disk_free_gb:
                    free_percent = (status.disk_free_gb / status.disk_total_gb) * 100
                    if free_percent < 10:
                        disk_warnings.append(
                            f"{task.agent.name}: осталось {free_percent:.1f}% свободного места"
                        )
        
        return {
            "total_tasks": total_tasks,
            "successful": successful,
            "failed": failed,
            "warnings": warnings,
            "failed_tasks": failed_tasks,
            "disk_warnings": disk_warnings
        }

