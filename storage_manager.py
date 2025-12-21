"""
Универсальный менеджер хранилищ для бэкапов
Поддерживает: S3, SFTP, NFS, локальные диски
"""
import os
import logging
from typing import Dict, Any, Optional, BinaryIO
from pathlib import Path
from abc import ABC, abstractmethod

logger = logging.getLogger(__name__)


class StorageInterface(ABC):
    """Интерфейс для работы с хранилищем"""
    
    @abstractmethod
    async def upload_file(self, local_path: str, remote_path: str) -> str:
        """Загружает файл в хранилище"""
        pass
    
    @abstractmethod
    async def download_file(self, remote_path: str, local_path: str) -> None:
        """Скачивает файл из хранилища"""
        pass
    
    @abstractmethod
    async def list_files(self, prefix: str = "") -> list:
        """Список файлов в хранилище"""
        pass
    
    @abstractmethod
    async def delete_file(self, remote_path: str) -> None:
        """Удаляет файл из хранилища"""
        pass
    
    @abstractmethod
    async def get_space_info(self) -> Dict[str, float]:
        """Получает информацию о свободном/использованном месте"""
        pass
    
    @abstractmethod
    async def test_connection(self) -> tuple[bool, Optional[str]]:
        """Проверяет подключение к хранилищу"""
        pass


class StorageManager:
    """Менеджер для работы с различными типами хранилищ"""
    
    @staticmethod
    def create_storage(storage_type: str, config_data: Dict[str, Any]) -> StorageInterface:
        """Создает экземпляр хранилища по типу"""
        if storage_type == "s3":
            from storage_s3 import S3Storage
            return S3Storage(config_data)
        elif storage_type == "sftp":
            from storage_sftp import SFTPStorage
            return SFTPStorage(config_data)
        elif storage_type == "nfs":
            from storage_nfs import NFSStorage
            return NFSStorage(config_data)
        elif storage_type == "local":
            from storage_local import LocalStorage
            return LocalStorage(config_data)
        else:
            raise ValueError(f"Unsupported storage type: {storage_type}")
    
    @staticmethod
    async def upload_backup(
        storage_type: str,
        config_data: Dict[str, Any],
        local_path: str,
        remote_path: str
    ) -> str:
        """Загружает бэкап в хранилище"""
        storage = StorageManager.create_storage(storage_type, config_data)
        return await storage.upload_file(local_path, remote_path)
    
    @staticmethod
    async def download_backup(
        storage_type: str,
        config_data: Dict[str, Any],
        remote_path: str,
        local_path: str
    ) -> None:
        """Скачивает бэкап из хранилища"""
        storage = StorageManager.create_storage(storage_type, config_data)
        await storage.download_file(remote_path, local_path)
    
    @staticmethod
    async def list_backups(
        storage_type: str,
        config_data: Dict[str, Any],
        prefix: str = ""
    ) -> list:
        """Получает список бэкапов"""
        storage = StorageManager.create_storage(storage_type, config_data)
        return await storage.list_files(prefix)
    
    @staticmethod
    async def delete_backup(
        storage_type: str,
        config_data: Dict[str, Any],
        remote_path: str
    ) -> None:
        """Удаляет бэкап из хранилища"""
        storage = StorageManager.create_storage(storage_type, config_data)
        await storage.delete_file(remote_path)
    
    @staticmethod
    async def get_storage_info(
        storage_type: str,
        config_data: Dict[str, Any]
    ) -> Dict[str, float]:
        """Получает информацию о хранилище"""
        storage = StorageManager.create_storage(storage_type, config_data)
        return await storage.get_space_info()
    
    @staticmethod
    async def test_storage_connection(
        storage_type: str,
        config_data: Dict[str, Any]
    ) -> tuple[bool, Optional[str]]:
        """Проверяет подключение к хранилищу"""
        try:
            storage = StorageManager.create_storage(storage_type, config_data)
            return await storage.test_connection()
        except Exception as e:
            return False, str(e)


