"""
Модуль для проверки S3 и контроля глубины хранения бэкапов
"""
import asyncio
from datetime import datetime, timedelta
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
import logging

from database import async_session_maker
from models import S3Config, BackupTask, AgentBackupInfo
from s3_client import S3Client

logger = logging.getLogger(__name__)


class S3Checker:
    """Класс для проверки S3 и контроля бэкапов"""
    
    def __init__(self, check_interval: int = 86400):
        self.check_interval = check_interval
        self.running = False
    
    async def start(self):
        """Запускает периодическую проверку S3"""
        self.running = True
        logger.info(f"Starting S3 checker with interval {self.check_interval} seconds")
        
        while self.running:
            try:
                await self.check_all_s3()
            except Exception as e:
                logger.error(f"Error during S3 check: {e}")
            
            await asyncio.sleep(self.check_interval)
    
    def stop(self):
        """Останавливает проверку"""
        self.running = False
        logger.info("Stopping S3 checker")
    
    async def check_all_s3(self):
        """Проверяет все S3 конфигурации"""
        async with async_session_maker() as session:
            result = await session.execute(select(S3Config))
            s3_configs = result.scalars().all()
            
            for s3_config in s3_configs:
                try:
                    await self.check_s3_config(s3_config, session)
                except Exception as e:
                    logger.error(f"Error checking S3 config {s3_config.id}: {e}")
    
    async def check_s3_config(self, s3_config: S3Config, session: AsyncSession):
        """Проверяет одну S3 конфигурацию"""
        client = S3Client(
            s3_config.endpoint,
            s3_config.access_key,
            s3_config.secret_key,
            s3_config.bucket_name,
            s3_config.region,
            s3_config.use_ssl
        )
        
        # Получаем информацию о bucket
        bucket_info = client.get_bucket_info()
        if bucket_info:
            s3_config.used_space_gb = bucket_info.get("used_space_gb")
            s3_config.free_space_gb = bucket_info.get("free_space_gb")
            s3_config.total_space_gb = bucket_info.get("total_space_gb")
        
        s3_config.last_check = datetime.utcnow()
        
        # Проверяем задачи, использующие этот S3
        result = await session.execute(
            select(BackupTask).where(BackupTask.s3_config_id == s3_config.id)
        )
        tasks = result.scalars().all()
        
        for task in tasks:
            await self.check_task_backups(task, client, session)
        
        await session.commit()
    
    async def check_task_backups(self, task: BackupTask, s3_client: S3Client, session: AsyncSession):
        """Проверяет бэкапы задачи и удаляет старые"""
        if not task.cleanup_enabled or not task.cleanup_days:
            return
        
        # Получаем информацию о бэкапах от агента
        result = await session.execute(
            select(AgentBackupInfo).where(AgentBackupInfo.task_id == task.id)
        )
        backup_infos = result.scalars().all()
        
        # Проверяем каждый бэкап
        cutoff_date = datetime.utcnow() - timedelta(days=task.cleanup_days)
        
        for backup_info in backup_infos:
            if backup_info.s3_upload_date and backup_info.s3_upload_date < cutoff_date:
                # Проверяем существование в S3
                if backup_info.s3_path and s3_client.check_backup_exists(backup_info.s3_path):
                    # Удаляем из S3
                    try:
                        # Извлекаем имя объекта из пути
                        object_name = backup_info.s3_path.replace(f"s3://{s3_client.bucket_name}/", "")
                        s3_client.client.remove_object(s3_client.bucket_name, object_name)
                        logger.info(f"Deleted old backup from S3: {backup_info.s3_path}")
                    except Exception as e:
                        logger.error(f"Error deleting backup from S3: {e}")
                
                # Удаляем запись из БД
                await session.delete(backup_info)







