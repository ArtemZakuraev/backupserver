"""
Модуль для работы с SFTP хранилищем
"""
import os
import logging
import shutil
from typing import Dict, Any, Optional
from pathlib import Path
import paramiko
from storage_manager import StorageInterface

logger = logging.getLogger(__name__)


class SFTPStorage(StorageInterface):
    """Реализация хранилища SFTP"""
    
    def __init__(self, config_data: Dict[str, Any]):
        self.host = config_data.get("host", "")
        self.port = config_data.get("port", 22)
        self.username = config_data.get("username", "")
        self.password = config_data.get("password", "")
        self.base_path = config_data.get("base_path", "/backups")
        self.private_key = config_data.get("private_key")  # Опционально: путь к приватному ключу
        
        self.transport = None
        self.sftp = None
    
    def _connect(self):
        """Устанавливает соединение с SFTP сервером"""
        try:
            self.transport = paramiko.Transport((self.host, self.port))
            
            # Аутентификация
            if self.private_key:
                # Используем ключ
                private_key_file = paramiko.RSAKey.from_private_key_file(self.private_key)
                self.transport.connect(username=self.username, pkey=private_key_file)
            else:
                # Используем пароль
                self.transport.connect(username=self.username, password=self.password)
            
            self.sftp = paramiko.SFTPClient.from_transport(self.transport)
            return True
        except Exception as e:
            logger.error(f"SFTP connection error: {e}")
            return False
    
    def _disconnect(self):
        """Закрывает соединение"""
        if self.sftp:
            self.sftp.close()
        if self.transport:
            self.transport.close()
    
    def _ensure_path(self, remote_path: str):
        """Создает необходимые директории на удаленном сервере"""
        full_path = f"{self.base_path}/{remote_path}"
        dir_path = os.path.dirname(full_path)
        
        # Создаем директории рекурсивно
        parts = dir_path.split('/')
        current_path = ''
        for part in parts:
            if part:
                current_path += '/' + part
                try:
                    self.sftp.stat(current_path)
                except IOError:
                    self.sftp.mkdir(current_path)
    
    async def upload_file(self, local_path: str, remote_path: str) -> str:
        """Загружает файл через SFTP"""
        if not self._connect():
            raise Exception("Failed to connect to SFTP server")
        
        try:
            # Создаем необходимые директории
            self._ensure_path(remote_path)
            
            # Полный путь на удаленном сервере
            full_remote_path = f"{self.base_path}/{remote_path}"
            
            # Загружаем файл
            self.sftp.put(local_path, full_remote_path)
            
            return f"sftp://{self.host}{full_remote_path}"
        finally:
            self._disconnect()
    
    async def download_file(self, remote_path: str, local_path: str) -> None:
        """Скачивает файл через SFTP"""
        if not self._connect():
            raise Exception("Failed to connect to SFTP server")
        
        try:
            # Убираем префикс sftp://host если есть
            if remote_path.startswith("sftp://"):
                # Извлекаем путь после хоста
                parts = remote_path.split("/", 3)
                if len(parts) > 3:
                    remote_path = "/" + parts[3]
            
            # Если путь не начинается с base_path, добавляем его
            if not remote_path.startswith(self.base_path):
                full_remote_path = f"{self.base_path}/{remote_path}"
            else:
                full_remote_path = remote_path
            
            # Создаем директорию для локального файла
            Path(local_path).parent.mkdir(parents=True, exist_ok=True)
            
            # Скачиваем файл
            self.sftp.get(full_remote_path, local_path)
        finally:
            self._disconnect()
    
    async def list_files(self, prefix: str = "") -> list:
        """Список файлов в SFTP"""
        if not self._connect():
            return []
        
        try:
            files = []
            base_dir = f"{self.base_path}/{prefix}" if prefix else self.base_path
            
            def _list_dir(path):
                try:
                    items = self.sftp.listdir(path)
                    for item in items:
                        full_path = f"{path}/{item}"
                        try:
                            attrs = self.sftp.stat(full_path)
                            if attrs.st_mode & 0o170000 == 0o040000:  # Директория
                                _list_dir(full_path)
                            else:  # Файл
                                # Убираем base_path из пути
                                rel_path = full_path.replace(self.base_path, "").lstrip("/")
                                files.append(rel_path)
                        except:
                            pass
                except:
                    pass
            
            _list_dir(base_dir)
            return files
        finally:
            self._disconnect()
    
    async def delete_file(self, remote_path: str) -> None:
        """Удаляет файл из SFTP"""
        if not self._connect():
            raise Exception("Failed to connect to SFTP server")
        
        try:
            # Убираем префикс sftp://host если есть
            if remote_path.startswith("sftp://"):
                parts = remote_path.split("/", 3)
                if len(parts) > 3:
                    remote_path = "/" + parts[3]
            
            # Если путь не начинается с base_path, добавляем его
            if not remote_path.startswith(self.base_path):
                full_remote_path = f"{self.base_path}/{remote_path}"
            else:
                full_remote_path = remote_path
            
            self.sftp.remove(full_remote_path)
        finally:
            self._disconnect()
    
    async def get_space_info(self) -> Dict[str, float]:
        """Получает информацию о свободном месте на SFTP сервере"""
        if not self._connect():
            return {"used_space_gb": 0, "free_space_gb": 0, "total_space_gb": 0}
        
        try:
            # Получаем статистику файловой системы
            stat = self.sftp.statvfs(self.base_path)
            
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
            logger.error(f"SFTP space info error: {e}")
            return {"used_space_gb": 0, "free_space_gb": 0, "total_space_gb": 0}
        finally:
            self._disconnect()
    
    async def test_connection(self) -> tuple[bool, Optional[str]]:
        """Проверяет подключение к SFTP серверу"""
        if not self._connect():
            return False, "Failed to connect to SFTP server"
        
        try:
            # Пытаемся создать директорию base_path если её нет
            try:
                self.sftp.stat(self.base_path)
            except IOError:
                # Директория не существует, создаем
                try:
                    self.sftp.mkdir(self.base_path)
                except Exception as e:
                    return False, f"Cannot create base directory: {str(e)}"
            
            return True, None
        except Exception as e:
            return False, str(e)
        finally:
            self._disconnect()


