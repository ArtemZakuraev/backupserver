"""
Модуль для работы с NFS хранилищем
"""
import os
import logging
import shutil
import subprocess
from typing import Dict, Any, Optional
from pathlib import Path
from storage_manager import StorageInterface

logger = logging.getLogger(__name__)


class NFSStorage(StorageInterface):
    """Реализация хранилища NFS"""
    
    def __init__(self, config_data: Dict[str, Any]):
        self.server = config_data.get("server", "")
        self.export_path = config_data.get("export_path", "")
        self.mount_point = config_data.get("mount_point", "/mnt/nfs_backup")
        self.options = config_data.get("options", "rw,sync,hard,intr")
        self.base_path = config_data.get("base_path", "")  # Путь относительно mount_point
        
        self.is_mounted = False
    
    def _mount(self) -> bool:
        """Монтирует NFS раздел"""
        if self.is_mounted:
            return True
        
        try:
            # Создаем точку монтирования
            Path(self.mount_point).mkdir(parents=True, exist_ok=True)
            
            # Проверяем, не смонтирован ли уже
            result = subprocess.run(
                ["mountpoint", "-q", self.mount_point],
                capture_output=True
            )
            if result.returncode == 0:
                self.is_mounted = True
                return True
            
            # Монтируем NFS
            nfs_path = f"{self.server}:{self.export_path}"
            cmd = ["mount", "-t", "nfs", "-o", self.options, nfs_path, self.mount_point]
            result = subprocess.run(cmd, capture_output=True, text=True)
            
            if result.returncode == 0:
                self.is_mounted = True
                return True
            else:
                logger.error(f"NFS mount error: {result.stderr}")
                return False
        except Exception as e:
            logger.error(f"NFS mount exception: {e}")
            return False
    
    def _unmount(self):
        """Размонтирует NFS раздел"""
        if not self.is_mounted:
            return
        
        try:
            subprocess.run(["umount", self.mount_point], capture_output=True)
            self.is_mounted = False
        except Exception as e:
            logger.error(f"NFS unmount error: {e}")
    
    def _get_full_path(self, path: str) -> str:
        """Возвращает полный путь с учетом mount_point и base_path"""
        if self.base_path:
            return os.path.join(self.mount_point, self.base_path, path)
        return os.path.join(self.mount_point, path)
    
    async def upload_file(self, local_path: str, remote_path: str) -> str:
        """Загружает файл в NFS"""
        if not self._mount():
            raise Exception("Failed to mount NFS")
        
        try:
            full_remote_path = self._get_full_path(remote_path)
            
            # Создаем директорию если нужно
            Path(full_remote_path).parent.mkdir(parents=True, exist_ok=True)
            
            # Копируем файл
            shutil.copy2(local_path, full_remote_path)
            
            return f"nfs://{self.server}{self.export_path}/{remote_path}"
        except Exception as e:
            logger.error(f"NFS upload error: {e}")
            raise
    
    async def download_file(self, remote_path: str, local_path: str) -> None:
        """Скачивает файл из NFS"""
        if not self._mount():
            raise Exception("Failed to mount NFS")
        
        try:
            # Убираем префикс nfs://server/export если есть
            if remote_path.startswith("nfs://"):
                parts = remote_path.split("/", 3)
                if len(parts) > 3:
                    remote_path = parts[3]
            
            full_remote_path = self._get_full_path(remote_path)
            
            # Создаем директорию для локального файла
            Path(local_path).parent.mkdir(parents=True, exist_ok=True)
            
            # Копируем файл
            shutil.copy2(full_remote_path, local_path)
        except Exception as e:
            logger.error(f"NFS download error: {e}")
            raise
    
    async def list_files(self, prefix: str = "") -> list:
        """Список файлов в NFS"""
        if not self._mount():
            return []
        
        try:
            files = []
            base_dir = self._get_full_path(prefix) if prefix else self._get_full_path("")
            
            for root, dirs, filenames in os.walk(base_dir):
                for filename in filenames:
                    full_path = os.path.join(root, filename)
                    # Относительный путь от base_dir
                    rel_path = os.path.relpath(full_path, self._get_full_path(""))
                    files.append(rel_path)
            
            return files
        except Exception as e:
            logger.error(f"NFS list error: {e}")
            return []
    
    async def delete_file(self, remote_path: str) -> None:
        """Удаляет файл из NFS"""
        if not self._mount():
            raise Exception("Failed to mount NFS")
        
        try:
            # Убираем префикс nfs://server/export если есть
            if remote_path.startswith("nfs://"):
                parts = remote_path.split("/", 3)
                if len(parts) > 3:
                    remote_path = parts[3]
            
            full_remote_path = self._get_full_path(remote_path)
            os.remove(full_remote_path)
        except Exception as e:
            logger.error(f"NFS delete error: {e}")
            raise
    
    async def get_space_info(self) -> Dict[str, float]:
        """Получает информацию о свободном месте на NFS"""
        if not self._mount():
            return {"used_space_gb": 0, "free_space_gb": 0, "total_space_gb": 0}
        
        try:
            stat = os.statvfs(self.mount_point)
            
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
            logger.error(f"NFS space info error: {e}")
            return {"used_space_gb": 0, "free_space_gb": 0, "total_space_gb": 0}
    
    async def test_connection(self) -> tuple[bool, Optional[str]]:
        """Проверяет подключение к NFS"""
        if not self._mount():
            return False, "Failed to mount NFS share"
        
        try:
            # Проверяем доступность
            test_path = self._get_full_path("")
            if not os.path.exists(test_path):
                os.makedirs(test_path, exist_ok=True)
            
            return True, None
        except Exception as e:
            return False, str(e)


