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
            return [obj.object_name for obj in objects]
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
            # Для S3 сложно получить точную информацию о месте
            # Используем приблизительную оценку на основе объектов
            total_size = 0
            count = 0
            
            objects = self.client.list_objects(self.bucket_name, recursive=True)
            for obj in objects:
                total_size += obj.size
                count += 1
            
            # Конвертируем в GB
            used_gb = total_size / (1024 ** 3)
            
            # Для S3 обычно нет ограничений, но можно указать максимальный размер bucket
            # Здесь возвращаем только использованное место
            return {
                "used_space_gb": used_gb,
                "free_space_gb": None,  # Неизвестно для S3
                "total_space_gb": None
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


