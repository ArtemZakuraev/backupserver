"""
Планировщик для автоматического выполнения PostgreSQL бэкапов
"""
import asyncio
from datetime import datetime
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_
from typing import List
import logging

from database import async_session_maker
from models import PostgresBackupTask, PostgresBackupHistory, S3Config, StorageConfig
from postgres_backup import PostgresBackupExecutor
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from s3_client import S3Client
from datetime import timedelta

logger = logging.getLogger(__name__)


class PostgresBackupScheduler:
    """Планировщик для PostgreSQL бэкапов"""
    
    def __init__(self):
        self.scheduler = AsyncIOScheduler()
        self.running = False
        self.job_ids = {}
    
    async def start(self):
        """Запускает планировщик"""
        self.running = True
        logger.info("Starting PostgreSQL backup scheduler")
        
        # Загружаем задачи из БД
        await self.load_tasks()
        
        # Запускаем планировщик
        self.scheduler.start()
        
        # Периодически обновляем задачи (каждые 5 минут)
        while self.running:
            await asyncio.sleep(300)  # 5 минут
            await self.load_tasks()
    
    def stop(self):
        """Останавливает планировщик"""
        self.running = False
        self.scheduler.shutdown()
        logger.info("PostgreSQL backup scheduler stopped")
    
    async def load_tasks(self):
        """Загружает задачи из БД и обновляет расписание"""
        async with async_session_maker() as session:
            result = await session.execute(
                select(PostgresBackupTask).where(
                    and_(
                        PostgresBackupTask.is_active == True,
                        PostgresBackupTask.schedule_enabled == True
                    )
                )
            )
            tasks = result.scalars().all()
            
            # Удаляем старые задачи
            for job_id in list(self.job_ids.keys()):
                if job_id not in [f"postgres_task_{t.id}" for t in tasks]:
                    try:
                        self.scheduler.remove_job(job_id)
                        del self.job_ids[job_id]
                    except:
                        pass
            
            # Добавляем/обновляем задачи
            for task in tasks:
                job_id = f"postgres_task_{task.id}"
                
                # Удаляем старую задачу если есть
                if job_id in self.job_ids:
                    try:
                        self.scheduler.remove_job(job_id)
                    except:
                        pass
                
                    # Парсим cron выражение
                try:
                    cron_parts = task.schedule_cron.split()
                    if len(cron_parts) == 5:
                        # Стандартный формат: minute hour day month day_of_week
                        trigger = CronTrigger.from_crontab(task.schedule_cron)
                    elif len(cron_parts) == 6:
                        # С секундами: second minute hour day month day_of_week
                        # APScheduler не поддерживает секунды напрямую, используем только минуты
                        _, minute, hour, day, month, day_of_week = cron_parts
                        # Создаем trigger без секунд
                        trigger = CronTrigger(
                            minute=minute if minute != "*" else "*",
                            hour=hour if hour != "*" else "*",
                            day=day if day != "*" else "*",
                            month=month if month != "*" else "*",
                            day_of_week=day_of_week if day_of_week != "*" else "*"
                        )
                    else:
                        logger.error(f"Invalid cron format for task {task.id}: {task.schedule_cron}")
                        continue
                    
                    # Добавляем задачу
                    self.scheduler.add_job(
                        self.execute_backup_task,
                        trigger=trigger,
                        args=[task.id],
                        id=job_id,
                        replace_existing=True
                    )
                    self.job_ids[job_id] = task.id
                    logger.info(f"Scheduled PostgreSQL backup task {task.id}: {task.name}")
                    
                except Exception as e:
                    logger.error(f"Error scheduling task {task.id}: {e}")
    
    async def execute_backup_task(self, task_id: int):
        """Выполняет задачу резервного копирования"""
        async with async_session_maker() as session:
            result = await session.execute(
                select(PostgresBackupTask).where(PostgresBackupTask.id == task_id)
            )
            task = result.scalar_one_or_none()
            
            if not task:
                logger.error(f"PostgreSQL backup task {task_id} not found")
                return
            
            # Получаем конфигурацию хранилища
            storage_config = None
            s3_config = None
            
            if hasattr(task, 'storage_config_id') and task.storage_config_id:
                result_storage = await session.execute(
                    select(StorageConfig).where(StorageConfig.id == task.storage_config_id)
                )
                storage_config = result_storage.scalar_one_or_none()
                if not storage_config:
                    logger.error(f"Storage config not found for task {task_id}")
                    return
            elif task.s3_config_id:
                result_s3 = await session.execute(
                    select(S3Config).where(S3Config.id == task.s3_config_id)
                )
                s3_config = result_s3.scalar_one_or_none()
                if not s3_config:
                    logger.error(f"S3 config not found for task {task_id}")
                    return
            else:
                logger.error(f"No storage configuration found for task {task_id}")
                return
            
            # Создаем запись в истории
            history = PostgresBackupHistory(
                task_id=task.id,
                status="running",
                started_at=datetime.utcnow()
            )
            session.add(history)
            await session.commit()
            
            # Обновляем статус задачи
            task.last_status = "running"
            task.last_run = datetime.utcnow()
            await session.commit()
            
            try:
                # Выполняем бэкап
                executor = PostgresBackupExecutor(task, storage_config=storage_config, s3_config=s3_config)
                result = await executor.execute_backup()
                
                # Обновляем историю
                history.finished_at = datetime.utcnow()
                history.duration_seconds = int((history.finished_at - history.started_at).total_seconds())
                
                if result["success"]:
                    history.status = "success"
                    history.dump_size_mb = result["dump_size_mb"]
                    history.s3_path = result["s3_path"]
                    history.dump_filename = result["dump_filename"]
                    task.last_status = "success"
                    logger.info(f"PostgreSQL backup task {task_id} completed successfully")
                else:
                    history.status = "error"
                    history.error_message = result.get("error", "Unknown error")
                    task.last_status = "error"
                    task.last_error = result.get("error", "Unknown error")
                    logger.error(f"PostgreSQL backup task {task_id} failed: {result.get('error')}")
                
            except Exception as e:
                history.status = "error"
                history.error_message = str(e)
                history.finished_at = datetime.utcnow()
                task.last_status = "error"
                task.last_error = str(e)
                logger.error(f"Error executing PostgreSQL backup task {task_id}: {e}")
            
            await session.commit()
            
            # Очистка старых бэкапов
            if task.cleanup_enabled:
                await self.cleanup_old_backups(task, storage_config=storage_config, s3_config=s3_config, session=session)
    
    async def cleanup_old_backups(self, task, storage_config=None, s3_config=None, session=None):
        """Удаляет старые бэкапы из хранилища"""
        try:
            from storage_manager import StorageManager
            from datetime import timedelta
            
            cutoff_date = datetime.utcnow() - timedelta(days=task.cleanup_days)
            db_name_safe = task.database.replace("/", "_").replace("\\", "_")
            prefix = f"postgres_backups/{db_name_safe}/"
            
            # Определяем тип хранилища и конфигурацию
            if storage_config:
                storage_type = storage_config.storage_type
                config_data = storage_config.config_data if isinstance(storage_config.config_data, dict) else {}
            elif s3_config:
                storage_type = "s3"
                config_data = {
                    "endpoint": s3_config.endpoint,
                    "access_key": s3_config.access_key,
                    "secret_key": s3_config.secret_key,
                    "bucket_name": s3_config.bucket_name,
                    "region": s3_config.region,
                    "use_ssl": s3_config.use_ssl
                }
            else:
                logger.error("No storage configuration for cleanup")
                return
            
            # Получаем список файлов
            files = await StorageManager.list_backups(storage_type, config_data, prefix)
            
            # Удаляем старые файлы
            # Для универсального хранилища нужно получать метаданные файлов
            # Пока удаляем все файлы старше cutoff_date
            deleted_count = 0
            for file_path in files:
                try:
                    # Для упрощения удаляем все файлы в префиксе
                    # В реальности нужно проверять дату модификации файла
                    full_path = f"{prefix}{file_path}" if not file_path.startswith(prefix) else file_path
                    await StorageManager.delete_backup(storage_type, config_data, full_path)
                    deleted_count += 1
                    logger.info(f"Deleted old PostgreSQL backup: {full_path}")
                except Exception as e:
                    logger.error(f"Error deleting old backup {full_path}: {e}")
            
            if deleted_count > 0:
                logger.info(f"Cleaned up {deleted_count} old PostgreSQL backups for task {task.id}")
                
        except Exception as e:
            logger.error(f"Error during cleanup for task {task.id}: {e}")

