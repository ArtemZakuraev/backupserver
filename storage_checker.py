"""
Модуль для проверки хранилищ всех типов
"""
import asyncio
from datetime import datetime
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
import logging

from database import async_session_maker
from models import StorageConfig, Agent
from agent_client import AgentClient
from s3_client import S3Client
from storage_local import LocalStorage
from storage_s3 import S3Storage
from storage_sftp import SFTPStorage
from storage_nfs import NFSStorage

logger = logging.getLogger(__name__)


class StorageChecker:
    """Класс для проверки всех типов хранилищ"""
    
    def __init__(self, check_interval: int = 86400):
        self.check_interval = check_interval
        self.running = False
    
    async def start(self):
        """Запускает периодическую проверку хранилищ"""
        self.running = True
        logger.info(f"Starting storage checker with interval {self.check_interval} seconds")
        
        while self.running:
            try:
                await self.check_all_storages()
            except Exception as e:
                logger.error(f"Error during storage check: {e}")
            
            await asyncio.sleep(self.check_interval)
    
    def stop(self):
        """Останавливает проверку"""
        self.running = False
        logger.info("Stopping storage checker")
    
    async def check_all_storages(self):
        """Проверяет все хранилища"""
        async with async_session_maker() as session:
            result = await session.execute(select(StorageConfig))
            storage_configs = result.scalars().all()
            
            for storage_config in storage_configs:
                try:
                    await self.check_storage_config(storage_config, session)
                except Exception as e:
                    logger.error(f"Error checking storage config {storage_config.id}: {e}")
    
    async def check_storage_config(self, storage_config: StorageConfig, session: AsyncSession):
        """Проверяет одну конфигурацию хранилища"""
        try:
            if storage_config.storage_type == "local":
                await self._check_local_storage(storage_config, session)
            elif storage_config.storage_type == "s3":
                await self._check_s3_storage(storage_config, session)
            elif storage_config.storage_type == "sftp":
                await self._check_sftp_storage(storage_config, session)
            elif storage_config.storage_type == "nfs":
                await self._check_nfs_storage(storage_config, session)
            else:
                logger.warning(f"Unknown storage type: {storage_config.storage_type}")
                storage_config.connection_error = f"Unknown storage type: {storage_config.storage_type}"
                storage_config.last_check = datetime.utcnow()
            
            await session.commit()
        except Exception as e:
            logger.error(f"Error checking storage {storage_config.id}: {e}")
            storage_config.connection_error = str(e)
            storage_config.last_check = datetime.utcnow()
            await session.commit()
    
    async def _check_local_storage(self, storage_config: StorageConfig, session: AsyncSession):
        """Проверяет локальное хранилище через агента"""
        try:
            # Получаем агента-хранилища
            agent_id = storage_config.config_data.get("agent_id")
            if not agent_id:
                storage_config.connection_error = "agent_id not found in config_data"
                storage_config.last_check = datetime.utcnow()
                return
            
            result = await session.execute(select(Agent).where(Agent.id == agent_id))
            agent = result.scalar_one_or_none()
            if not agent:
                storage_config.connection_error = f"Agent {agent_id} not found"
                storage_config.last_check = datetime.utcnow()
                return
            
            # Получаем базовый путь
            base_path = storage_config.config_data.get("base_path", "/var/backups")
            
            # Проверяем доступность агента
            client = AgentClient(agent.ip_address, agent.port)
            if not await client.ping():
                storage_config.connection_error = "Agent is not reachable"
                storage_config.last_check = datetime.utcnow()
                return
            
            # Получаем информацию о месте на диске
            space_info = await client.get_storage_space(base_path)
            if space_info:
                storage_config.used_space_gb = space_info.get("used_space_gb")
                storage_config.free_space_gb = space_info.get("free_space_gb")
                storage_config.total_space_gb = space_info.get("total_space_gb")
                storage_config.connection_error = None
            else:
                storage_config.connection_error = "Failed to get storage space info"
            
            storage_config.last_check = datetime.utcnow()
        except Exception as e:
            logger.error(f"Error checking local storage: {e}")
            storage_config.connection_error = str(e)
            storage_config.last_check = datetime.utcnow()
    
    async def _check_s3_storage(self, storage_config: StorageConfig, session: AsyncSession):
        """Проверяет S3 хранилище"""
        try:
            # Создаем клиент S3
            storage = S3Storage(storage_config.config_data)
            
            # Проверяем подключение
            is_connected, error = await storage.test_connection()
            if not is_connected:
                storage_config.connection_error = error
                storage_config.last_check = datetime.utcnow()
                return
            
            # Получаем информацию о месте
            space_info = await storage.get_space_info()
            if space_info:
                storage_config.used_space_gb = space_info.get("used_space_gb")
                storage_config.free_space_gb = space_info.get("free_space_gb")
                storage_config.total_space_gb = space_info.get("total_space_gb")
            
            storage_config.connection_error = None
            storage_config.last_check = datetime.utcnow()
        except Exception as e:
            logger.error(f"Error checking S3 storage: {e}")
            storage_config.connection_error = str(e)
            storage_config.last_check = datetime.utcnow()
    
    async def _check_sftp_storage(self, storage_config: StorageConfig, session: AsyncSession):
        """Проверяет SFTP хранилище"""
        try:
            storage = SFTPStorage(storage_config.config_data)
            
            # Проверяем подключение
            is_connected, error = await storage.test_connection()
            if not is_connected:
                storage_config.connection_error = error
                storage_config.last_check = datetime.utcnow()
                return
            
            # Получаем информацию о месте
            space_info = await storage.get_space_info()
            if space_info:
                storage_config.used_space_gb = space_info.get("used_space_gb")
                storage_config.free_space_gb = space_info.get("free_space_gb")
                storage_config.total_space_gb = space_info.get("total_space_gb")
            
            storage_config.connection_error = None
            storage_config.last_check = datetime.utcnow()
        except Exception as e:
            logger.error(f"Error checking SFTP storage: {e}")
            storage_config.connection_error = str(e)
            storage_config.last_check = datetime.utcnow()
    
    async def _check_nfs_storage(self, storage_config: StorageConfig, session: AsyncSession):
        """Проверяет NFS хранилище"""
        try:
            storage = NFSStorage(storage_config.config_data)
            
            # Проверяем подключение
            is_connected, error = await storage.test_connection()
            if not is_connected:
                storage_config.connection_error = error
                storage_config.last_check = datetime.utcnow()
                return
            
            # Получаем информацию о месте
            space_info = await storage.get_space_info()
            if space_info:
                storage_config.used_space_gb = space_info.get("used_space_gb")
                storage_config.free_space_gb = space_info.get("free_space_gb")
                storage_config.total_space_gb = space_info.get("total_space_gb")
            
            storage_config.connection_error = None
            storage_config.last_check = datetime.utcnow()
        except Exception as e:
            logger.error(f"Error checking NFS storage: {e}")
            storage_config.connection_error = str(e)
            storage_config.last_check = datetime.utcnow()


