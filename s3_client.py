"""
Клиент для работы с S3/MinIO
"""
from minio import Minio
from minio.error import S3Error
from typing import List, Dict, Optional
from datetime import datetime, timedelta
import logging

logger = logging.getLogger(__name__)


class S3Client:
    """Клиент для работы с S3/MinIO"""
    
    def __init__(self, endpoint: str, access_key: str, secret_key: str, 
                 bucket_name: str, region: str = "us-east-1", use_ssl: bool = False):
        self.endpoint = endpoint.replace("http://", "").replace("https://", "")
        self.access_key = access_key
        self.secret_key = secret_key
        self.bucket_name = bucket_name
        self.region = region
        self.use_ssl = use_ssl
        
        self.client = Minio(
            self.endpoint,
            access_key=access_key,
            secret_key=secret_key,
            secure=use_ssl,
            region=region
        )
    
    def get_bucket_info(self) -> Optional[Dict]:
        """Получает информацию о bucket (свободное место)"""
        try:
            # MinIO не предоставляет прямого API для получения размера bucket
            # Используем статистику объектов
            total_size = 0
            object_count = 0
            
            objects = self.client.list_objects(self.bucket_name, recursive=True)
            for obj in objects:
                total_size += obj.size
                object_count += 1
            
            # Для MinIO обычно нет ограничения на размер, но можно получить информацию о диске
            # Это приблизительная оценка
            return {
                "used_space_gb": total_size / (1024 ** 3),
                "object_count": object_count,
                "total_space_gb": None,  # MinIO не предоставляет эту информацию напрямую
                "free_space_gb": None
            }
        except S3Error as e:
            logger.error(f"Error getting bucket info: {e}")
            return None
    
    def list_backups(self, prefix: str = "") -> List[Dict]:
        """Список всех бэкапов в bucket"""
        backups = []
        try:
            objects = self.client.list_objects(self.bucket_name, prefix=prefix, recursive=True)
            for obj in objects:
                backups.append({
                    "name": obj.object_name,
                    "size": obj.size,
                    "last_modified": obj.last_modified,
                    "etag": obj.etag
                })
        except S3Error as e:
            logger.error(f"Error listing backups: {e}")
        return backups
    
    def cleanup_old_backups(self, days: int, prefix: str = "") -> int:
        """Удаляет бэкапы старше указанного количества дней"""
        cutoff_date = datetime.utcnow() - timedelta(days=days)
        deleted_count = 0
        
        try:
            objects = self.client.list_objects(self.bucket_name, prefix=prefix, recursive=True)
            for obj in objects:
                if obj.last_modified.replace(tzinfo=None) < cutoff_date:
                    try:
                        self.client.remove_object(self.bucket_name, obj.object_name)
                        deleted_count += 1
                        logger.info(f"Deleted old backup: {obj.object_name}")
                    except S3Error as e:
                        logger.error(f"Error deleting {obj.object_name}: {e}")
        except S3Error as e:
            logger.error(f"Error during cleanup: {e}")
        
        return deleted_count
    
    def check_backup_exists(self, object_name: str) -> bool:
        """Проверяет существование объекта в bucket"""
        try:
            self.client.stat_object(self.bucket_name, object_name)
            return True
        except S3Error:
            return False







