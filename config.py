from pydantic_settings import BaseSettings
from typing import Optional
import os


class Settings(BaseSettings):
    # Основные настройки
    secret_key: str = "your-secret-key-change-this-in-production-backup-server"
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 30
    
    # База данных PostgreSQL
    postgres_host: str = "db"
    postgres_port: int = 5432
    postgres_user: str = "postgres"
    postgres_password: str = "postgres"
    postgres_db: str = "backup_server"
    
    # URL базы данных
    @property
    def database_url(self) -> str:
        return f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
    
    # Настройки приложения
    app_name: str = "Сервер резервного копирования отдела развития инженерных практик"
    app_version: str = "1.0.0"
    debug: bool = True
    
    # Порт сервера
    server_port: int = 8000
    server_host: str = "0.0.0.0"
    
    # Порт агента
    agent_port: int = 11540
    
    # Настройки S3 MinIO по умолчанию
    default_s3_endpoint: str = "http://minio:9000"
    default_s3_access_key: str = "minioadmin"
    default_s3_secret_key: str = "minioadmin"
    default_s3_region: str = "us-east-1"
    
    # Настройки мониторинга
    agent_poll_interval: int = 60  # Интервал опроса агентов в секундах (1 минута)
    s3_check_interval: int = 86400  # Интервал проверки S3 в секундах (24 часа)
    
    class Config:
        env_file = ".env"
        case_sensitive = False


# Создаем экземпляр настроек
settings = Settings()

