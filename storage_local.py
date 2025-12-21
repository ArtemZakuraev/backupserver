"""
Модуль для работы с локальным хранилищем
"""
import os
import logging
import shutil
from typing import Dict, Any, Optional
from pathlib import Path
from storage_manager import StorageInterface

logger = logging.getLogger(__name__)


class LocalStorage(StorageInterface):
    """Реализация локального хранилища"""
    
    def __init__(self, config_data: Dict[str, Any]):
        self.base_path = config_data.get("base_path", "/var/backups")
        
        # Создаем базовую директорию если её нет
        Path(self.base_path).mkdir(parents=True, exist_ok=True)
    
    def _get_full_path(self, path: str) -> str:
        """Возвращает полный путь с учетом base_path"""
        return os.path.join(self.base_path, path)
    
    async def upload_file(self, local_path: str, remote_path: str) -> str:
        """Копирует файл в локальное хранилище"""
        try:
            full_remote_path = self._get_full_path(remote_path)
            
            # Создаем директорию если нужно
            Path(full_remote_path).parent.mkdir(parents=True, exist_ok=True)
            
            # Копируем файл
            shutil.copy2(local_path, full_remote_path)
            
            return f"local://{full_remote_path}"
        except Exception as e:
            logger.error(f"Local storage upload error: {e}")
            raise
    
    async def download_file(self, remote_path: str, local_path: str) -> None:
        """Копирует файл из локального хранилища"""
        try:
            # Убираем префикс local:// если есть
            if remote_path.startswith("local://"):
                full_remote_path = remote_path.replace("local://", "")
            else:
                full_remote_path = self._get_full_path(remote_path)
            
            # Создаем директорию для локального файла
            Path(local_path).parent.mkdir(parents=True, exist_ok=True)
            
            # Копируем файл
            shutil.copy2(full_remote_path, local_path)
        except Exception as e:
            logger.error(f"Local storage download error: {e}")
            raise
    
    async def list_files(self, prefix: str = "") -> list:
        """Список файлов в локальном хранилище"""
        try:
            files = []
            base_dir = self._get_full_path(prefix) if prefix else self.base_path
            
            if not os.path.exists(base_dir):
                return []
            
            for root, dirs, filenames in os.walk(base_dir):
                for filename in filenames:
                    full_path = os.path.join(root, filename)
                    # Относительный путь от base_path
                    rel_path = os.path.relpath(full_path, self.base_path)
                    files.append(rel_path)
            
            return files
        except Exception as e:
            logger.error(f"Local storage list error: {e}")
            return []
    
    async def delete_file(self, remote_path: str) -> None:
        """Удаляет файл из локального хранилища"""
        try:
            # Убираем префикс local:// если есть
            if remote_path.startswith("local://"):
                full_remote_path = remote_path.replace("local://", "")
            else:
                full_remote_path = self._get_full_path(remote_path)
            
            os.remove(full_remote_path)
        except Exception as e:
            logger.error(f"Local storage delete error: {e}")
            raise
    
    async def get_space_info(self) -> Dict[str, float]:
        """Получает информацию о свободном месте на локальном диске"""
        try:
            stat = os.statvfs(self.base_path)
            
            # Вычисляем размеры
            total_bytes = stat.f_blocks * stat.f_frsize
            free_bytes = stat.f_bavail * stat.f_frsize
            used_bytes = (stat.f_blocks - stat.f_bavail) * stat.f_frsize
            
            # Конвертируем в GB
            total_gb = total_bytes / (1024 ** 3)
            free_gb = free_bytes / (1024 ** 3)
            used_gb = used_bytes / (1024 ** 3)
            
            return {
                "used_space_gb": used_gb,
                "free_space_gb": free_gb,
                "total_space_gb": total_gb
            }
        except Exception as e:
            logger.error(f"Local storage space info error: {e}")
            return {"used_space_gb": 0, "free_space_gb": 0, "total_space_gb": 0}
    
    async def test_connection(self) -> tuple[bool, Optional[str]]:
        """Проверяет доступность локального хранилища"""
        try:
            # Проверяем существование базовой директории
            if not os.path.exists(self.base_path):
                try:
                    os.makedirs(self.base_path, exist_ok=True)
                except Exception as e:
                    return False, f"Cannot create base directory: {str(e)}"
            
            # Проверяем права на запись
            test_file = os.path.join(self.base_path, ".test_write")
            try:
                with open(test_file, "w") as f:
                    f.write("test")
                os.remove(test_file)
            except Exception as e:
                return False, f"Cannot write to directory: {str(e)}"
            
            return True, None
        except Exception as e:
            return False, str(e)


