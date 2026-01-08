"""
Модуль для работы с S3-совместимыми хранилищами (MinIO, AWS S3 и т.д.)
"""
import logging
from typing import Dict, Any, Optional
from pathlib import Path
from minio import Minio
from minio.error import S3Error
from storage_manager import StorageInterface

logger = logging.getLogger(__name__)


class S3Storage(StorageInterface):
    """Реализация хранилища S3"""
    
    def __init__(self, config_data: Dict[str, Any]):
        self.endpoint = config_data.get("endpoint", "").replace("http://", "").replace("https://", "")
        self.access_key = config_data.get("access_key", "")
        self.secret_key = config_data.get("secret_key", "")
        self.bucket_name = config_data.get("bucket_name", "")
        self.region = config_data.get("region", "us-east-1")
        self.use_ssl = config_data.get("use_ssl", False) or config_data.get("endpoint", "").startswith("https://")
        
        self.client = Minio(
            self.endpoint,
            access_key=self.access_key,
            secret_key=self.secret_key,
            secure=self.use_ssl,
            region=self.region
        )
    
    async def upload_file(self, local_path: str, remote_path: str) -> str:
        """Загружает файл в S3"""
        try:
            # Проверяем существование bucket
            if not self.client.bucket_exists(self.bucket_name):
                self.client.make_bucket(self.bucket_name, location=self.region)
            
            # Загружаем файл
            self.client.fput_object(
                self.bucket_name,
                remote_path,
                local_path
            )
            
            return f"s3://{self.bucket_name}/{remote_path}"
        except S3Error as e:
            logger.error(f"S3 upload error: {e}")
            raise
    
    async def download_file(self, remote_path: str, local_path: str) -> None:
        """Скачивает файл из S3"""
        try:
            # Убираем префикс s3://bucket/ если есть
            if remote_path.startswith(f"s3://{self.bucket_name}/"):
                object_name = remote_path.replace(f"s3://{self.bucket_name}/", "")
            else:
                object_name = remote_path
            
            # Создаем директорию если нужно
            Path(local_path).parent.mkdir(parents=True, exist_ok=True)
            
            # Скачиваем файл
            self.client.fget_object(self.bucket_name, object_name, local_path)
        except S3Error as e:
            logger.error(f"S3 download error: {e}")
            raise
    
    async def list_files(self, prefix: str = "") -> list:
        """Список файлов в S3"""
        try:
            objects = self.client.list_objects(self.bucket_name, prefix=prefix, recursive=True)
            backups = []
            for obj in objects:
                # Полный путь для восстановления
                object_name = obj.object_name
                # Имя файла для отображения
                file_name = object_name
                if "/" in file_name:
                    file_name = file_name.split("/")[-1]
                
                # Преобразуем last_modified в ISO строку
                last_modified = ""
                if obj.last_modified:
                    if hasattr(obj.last_modified, 'isoformat'):
                        last_modified = obj.last_modified.isoformat()
                    else:
                        last_modified = str(obj.last_modified)
                
                backups.append({
                    "name": object_name,  # Полный путь для восстановления
                    "object_name": object_name,  # Полный путь для совместимости
                    "display_name": file_name,  # Только имя файла для отображения
                    "size": obj.size,
                    "last_modified": last_modified,
                    "last_modified_time": last_modified,  # Для совместимости
                    "etag": obj.etag or ""
                })
            return backups
        except S3Error as e:
            logger.error(f"S3 list error: {e}")
            return []
    
    async def delete_file(self, remote_path: str) -> None:
        """Удаляет файл из S3"""
        try:
            # Убираем префикс s3://bucket/ если есть
            if remote_path.startswith(f"s3://{self.bucket_name}/"):
                object_name = remote_path.replace(f"s3://{self.bucket_name}/", "")
            else:
                object_name = remote_path
            
            self.client.remove_object(self.bucket_name, object_name)
        except S3Error as e:
            logger.error(f"S3 delete error: {e}")
            raise
    
    async def get_space_info(self) -> Dict[str, float]:
        """Получает информацию о свободном месте в S3"""
        try:
            # Для S3/MinIO получаем информацию о размере bucket
            total_size = 0
            count = 0
            
            objects = self.client.list_objects(self.bucket_name, recursive=True)
            for obj in objects:
                total_size += obj.size
                count += 1
            
            # Конвертируем в GB
            used_gb = total_size / (1024 ** 3)
            
            # Попытка получить информацию о диске через MinIO Admin API
            # Это работает только если у нас есть доступ к Admin API
            free_space_gb = None
            total_space_gb = None
            
            try:
                # Пытаемся получить информацию о диске через MinIO Admin API
                # Это требует дополнительных прав доступа и может не работать для всех конфигураций
                import aiohttp
                import json
                
                # Формируем URL для MinIO Admin API (если доступен)
                admin_url = None
                if self.endpoint:
                    # Пытаемся определить админ URL
                    if self.use_ssl:
                        admin_url = f"https://{self.endpoint}/minio/admin/v3/info"
                    else:
                        admin_url = f"http://{self.endpoint}/minio/admin/v3/info"
                
                # Примечание: MinIO Admin API требует специальных учетных данных
                # и обычно недоступен через стандартный S3 API
                # Для получения информации о диске нужно использовать MinIO Client (mc) или Admin API
                # с правильными учетными данными администратора
                
                # Для большинства случаев S3/MinIO хранилищ, информация о свободном месте
                # недоступна через стандартный S3 API, поэтому возвращаем только использованное место
                
            except Exception as e:
                logger.debug(f"Could not get disk space info from MinIO Admin API: {e}")
            
            return {
                "used_space_gb": used_gb,
                "free_space_gb": free_space_gb,  # Обычно недоступно для S3/MinIO через стандартный API
                "total_space_gb": total_space_gb  # Обычно недоступно для S3/MinIO через стандартный API
            }
        except S3Error as e:
            logger.error(f"S3 space info error: {e}")
            return {"used_space_gb": 0, "free_space_gb": None, "total_space_gb": None}
    
    async def test_connection(self) -> tuple[bool, Optional[str]]:
        """Проверяет подключение к S3"""
        try:
            if not self.client.bucket_exists(self.bucket_name):
                # Пытаемся создать bucket для проверки
                try:
                    self.client.make_bucket(self.bucket_name, location=self.region)
                except S3Error as e:
                    return False, f"Cannot access or create bucket: {str(e)}"
            return True, None
        except Exception as e:
            return False, str(e)





