"""
Планировщик для автоматической отправки отчетов
"""
import asyncio
import logging
from datetime import datetime, timedelta
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import Optional

from database import async_session_maker
from models import Report, Settings, ReportHistory
from report_generator import ReportGenerator
from mattermost_client import MattermostClient

logger = logging.getLogger(__name__)


class ReportScheduler:
    """Планировщик отчетов"""
    
    def __init__(self):
        self.running = False
    
    async def start(self):
        """Запускает планировщик"""
        self.running = True
        logger.info("Report scheduler started")
        
        while self.running:
            try:
                await self.check_and_send_reports()
            except Exception as e:
                logger.error(f"Error in report scheduler: {e}")
            
            await asyncio.sleep(60)  # Проверяем каждую минуту
    
    def stop(self):
        """Останавливает планировщик"""
        self.running = False
        logger.info("Report scheduler stopped")
    
    async def check_and_send_reports(self):
        """Проверяет и отправляет отчеты по расписанию"""
        async with async_session_maker() as session:
            # Получаем настройки Mattermost
            result = await session.execute(select(Settings))
            settings = result.scalar_one_or_none()
            
            if not settings or not settings.mattermost_enabled:
                return
            
            if not settings.mattermost_webhook_url:
                return
            
            # Получаем все активные отчеты
            result = await session.execute(
                select(Report).where(Report.enabled == True).where(Report.send_to_mattermost == True)
            )
            reports = result.scalars().all()
            
            now = datetime.utcnow()
            
            for report in reports:
                try:
                    # Проверяем, нужно ли отправить отчет
                    should_send = False
                    
                    if report.schedule_type == "daily":
                        # Раз в день в указанное время
                        if report.schedule_hour is not None and report.schedule_minute is not None:
                            if now.hour == report.schedule_hour and now.minute == report.schedule_minute:
                                # Проверяем, не отправляли ли уже сегодня
                                if not report.last_sent or report.last_sent.date() < now.date():
                                    should_send = True
                    
                    elif report.schedule_type == "weekly":
                        # Раз в неделю в указанный день и время
                        if (report.schedule_day_of_week is not None and 
                            report.schedule_hour is not None and 
                            report.schedule_minute is not None):
                            # 0 = понедельник, 6 = воскресенье
                            if (now.weekday() == report.schedule_day_of_week and
                                now.hour == report.schedule_hour and 
                                now.minute == report.schedule_minute):
                                # Проверяем, не отправляли ли уже на этой неделе
                                if not report.last_sent:
                                    should_send = True
                                else:
                                    days_since_last = (now.date() - report.last_sent.date()).days
                                    if days_since_last >= 7:
                                        should_send = True
                    
                    elif report.schedule_type == "hourly":
                        # Каждый час в указанную минуту
                        if report.schedule_minute is not None:
                            if now.minute == report.schedule_minute:
                                # Проверяем, не отправляли ли уже в этот час
                                if not report.last_sent:
                                    should_send = True
                                else:
                                    time_diff = now - report.last_sent
                                    if time_diff.total_seconds() >= 3600:  # Прошло больше часа
                                        should_send = True
                    
                    elif report.schedule_type == "custom_hours":
                        # Каждые N часов в указанную минуту
                        if report.schedule_hours_interval is not None and report.schedule_minute is not None:
                            if now.minute == report.schedule_minute:
                                # Проверяем, прошло ли достаточно времени
                                if not report.last_sent:
                                    should_send = True
                                else:
                                    time_diff = now - report.last_sent
                                    hours_passed = time_diff.total_seconds() / 3600
                                    if hours_passed >= report.schedule_hours_interval:
                                        should_send = True
                    
                    if should_send:
                        await self.send_report(report, settings.mattermost_webhook_url, session)
                
                except Exception as e:
                    logger.error(f"Error processing report {report.id}: {e}")
    
    async def send_report(self, report: Report, webhook_url: str, session: AsyncSession):
        """Отправляет отчет"""
        try:
            # Генерируем отчет
            generator = ReportGenerator(session)
            report_text = await generator.generate_report(
                report.agent_ids or [],
                report.postgres_task_ids or []
            )
            
            # Отправляем в Mattermost
            client = MattermostClient(webhook_url)
            success = await client.send_custom_report(report_text)
            
            # Сохраняем историю
            history = ReportHistory(
                report_id=report.id,
                status="success" if success else "error",
                error_message=None if success else "Failed to send to Mattermost",
                mattermost_response=str(success)
            )
            session.add(history)
            
            # Обновляем отчет
            report.last_sent = datetime.utcnow()
            report.next_send = self.calculate_next_send(report)
            
            await session.commit()
            logger.info(f"Report {report.id} sent successfully")
        
        except Exception as e:
            logger.error(f"Error sending report {report.id}: {e}")
            # Сохраняем ошибку
            history = ReportHistory(
                report_id=report.id,
                status="error",
                error_message=str(e)
            )
            session.add(history)
            await session.commit()
    
    def calculate_next_send(self, report: Report) -> Optional[datetime]:
        """Вычисляет следующее время отправки"""
        now = datetime.utcnow()
        
        if report.schedule_type == "daily":
            if report.schedule_hour is not None and report.schedule_minute is not None:
                next_send = now.replace(hour=report.schedule_hour, minute=report.schedule_minute, second=0, microsecond=0)
                if next_send <= now:
                    next_send += timedelta(days=1)
                return next_send
        
        elif report.schedule_type == "weekly":
            if (report.schedule_day_of_week is not None and 
                report.schedule_hour is not None and 
                report.schedule_minute is not None):
                days_ahead = report.schedule_day_of_week - now.weekday()
                if days_ahead <= 0:
                    days_ahead += 7
                next_send = now + timedelta(days=days_ahead)
                next_send = next_send.replace(hour=report.schedule_hour, minute=report.schedule_minute, second=0, microsecond=0)
                return next_send
        
        elif report.schedule_type == "hourly":
            if report.schedule_minute is not None:
                next_send = now.replace(minute=report.schedule_minute, second=0, microsecond=0)
                if next_send <= now:
                    next_send += timedelta(hours=1)
                return next_send
        
        elif report.schedule_type == "custom_hours":
            if report.schedule_hours_interval is not None and report.schedule_minute is not None:
                next_send = now.replace(minute=report.schedule_minute, second=0, microsecond=0)
                if next_send <= now:
                    next_send += timedelta(hours=report.schedule_hours_interval)
                else:
                    # Если текущая минута меньше указанной, добавляем интервал
                    next_send += timedelta(hours=report.schedule_hours_interval)
                return next_send
        
        return None

