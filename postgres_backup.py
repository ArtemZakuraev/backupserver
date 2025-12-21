"""
Модуль для резервного копирования PostgreSQL баз данных
"""
import asyncio
import subprocess
import os
import gzip
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any
import logging

from config import settings
from cryptography.fernet import Fernet
import base64

logger = logging.getLogger(__name__)

# Ключ для шифрования паролей (в продакшене должен быть в переменных окружения)
ENCRYPTION_KEY = os.getenv("BACKUP_ENCRYPTION_KEY", settings.secret_key[:32].ljust(32, '0'))


def encrypt_password(password: str) -> str:
    """Шифрует пароль для хранения в БД"""
    try:
        # Используем секретный ключ приложения
        key_bytes = ENCRYPTION_KEY.encode()[:32].ljust(32, b'0')
        key = base64.urlsafe_b64encode(key_bytes)
        f = Fernet(key)
        return f.encrypt(password.encode()).decode()
    except Exception as e:
        logger.error(f"Error encrypting password: {e}")
        # В случае ошибки возвращаем как есть (небезопасно, но для отладки)
        return password


def decrypt_password(encrypted: str) -> str:
    """Расшифровывает пароль из БД"""
    try:
        key_bytes = ENCRYPTION_KEY.encode()[:32].ljust(32, b'0')
        key = base64.urlsafe_b64encode(key_bytes)
        f = Fernet(key)
        return f.decrypt(encrypted.encode()).decode()
    except Exception as e:
        logger.error(f"Error decrypting password: {e}")
        return ""


class PostgresBackupExecutor:
    """Класс для выполнения резервного копирования PostgreSQL"""
    
    def __init__(self, task, storage_config=None, s3_config=None):
        self.task = task
        self.storage_config = storage_config  # Новое универсальное хранилище
        self.s3_config = s3_config  # Для обратной совместимости
        self.temp_dir = Path("/tmp/postgres_backups")
        self.temp_dir.mkdir(exist_ok=True, parents=True)
    
    async def execute_backup(self) -> Dict[str, Any]:
        """Выполняет резервное копирование PostgreSQL базы данных"""
        start_time = datetime.utcnow()
        result = {
            "success": False,
            "dump_filename": None,
            "dump_size_mb": 0,
            "s3_path": None,
            "error": None
        }
        
        try:
            # Расшифровываем пароль
            password = decrypt_password(self.task.password)
            
            # Формируем имя файла дампа
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            db_name_safe = self.task.database.replace("/", "_").replace("\\", "_")
            
            if self.task.backup_format == "custom":
                dump_filename = f"{db_name_safe}_{timestamp}.dump"
            elif self.task.backup_format == "plain":
                dump_filename = f"{db_name_safe}_{timestamp}.sql"
            elif self.task.backup_format == "tar":
                dump_filename = f"{db_name_safe}_{timestamp}.tar"
            else:
                dump_filename = f"{db_name_safe}_{timestamp}.sql"
            
            dump_path = self.temp_dir / dump_filename
            
            # Формируем команду pg_dump
            cmd = [
                "pg_dump",
                f"--host={self.task.host}",
                f"--port={self.task.port}",
                f"--username={self.task.username}",
                f"--dbname={self.task.database}",
                f"--format={self.task.backup_format}",
                f"--file={dump_path}",
            ]
            
            # Добавляем опции в зависимости от формата
            if self.task.backup_format == "custom":
                cmd.append(f"--compress={self.task.compression_level}")
            elif self.task.backup_format == "plain":
                cmd.append("--no-owner")
                cmd.append("--no-privileges")
            
            # Опции включения/исключения
            if not self.task.include_schema and not self.task.include_data:
                # Если оба выключены, включаем оба (по умолчанию)
                pass
            elif not self.task.include_schema:
                cmd.append("--data-only")
            elif not self.task.include_data:
                cmd.append("--schema-only")
            
            if self.task.include_roles:
                cmd.append("--roles-only")
            
            if self.task.include_tablespaces:
                cmd.append("--tablespaces")
            
            # Устанавливаем переменную окружения с паролем
            env = os.environ.copy()
            env["PGPASSWORD"] = password
            
            logger.info(f"Executing pg_dump for database {self.task.database} on {self.task.host}")
            
            # Выполняем pg_dump
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env
            )
            
            stdout, stderr = await process.communicate()
            
            if process.returncode != 0:
                error_msg = stderr.decode() if stderr else "Unknown error"
                logger.error(f"pg_dump failed: {error_msg}")
                result["error"] = error_msg
                return result
            
            # Проверяем размер файла
            if dump_path.exists():
                dump_size = dump_path.stat().st_size
                result["dump_size_mb"] = dump_size / (1024 * 1024)
                result["dump_filename"] = dump_filename
                
                # Загружаем в хранилище (S3, SFTP, NFS или локальное)
                from storage_manager import StorageManager
                
                # Определяем тип хранилища и конфигурацию
                if self.storage_config:
                    # Используем новое универсальное хранилище
                    storage_type = self.storage_config.storage_type
                    config_data = self.storage_config.config_data if isinstance(self.storage_config.config_data, dict) else {}
                    remote_path = f"postgres_backups/{db_name_safe}/{dump_filename}"
                elif self.s3_config:
                    # Обратная совместимость с S3Config
                    storage_type = "s3"
                    config_data = {
                        "endpoint": self.s3_config.endpoint,
                        "access_key": self.s3_config.access_key,
                        "secret_key": self.s3_config.secret_key,
                        "bucket_name": self.s3_config.bucket_name,
                        "region": self.s3_config.region,
                        "use_ssl": self.s3_config.use_ssl
                    }
                    remote_path = f"postgres_backups/{db_name_safe}/{dump_filename}"
                else:
                    result["error"] = "No storage configuration provided"
                    return result
                
                try:
                    storage_path = await StorageManager.upload_backup(
                        storage_type,
                        config_data,
                        str(dump_path),
                        remote_path
                    )
                    result["s3_path"] = storage_path  # Сохраняем как s3_path для совместимости
                    result["success"] = True
                    logger.info(f"Backup uploaded to {storage_type}: {storage_path}")
                except Exception as e:
                    logger.error(f"Failed to upload to {storage_type}: {e}")
                    result["error"] = f"Storage upload failed: {str(e)}"
                    return result
                
                # Удаляем локальный файл после успешной загрузки
                try:
                    dump_path.unlink()
                    logger.info(f"Local dump file removed: {dump_path}")
                except Exception as e:
                    logger.warning(f"Failed to remove local dump file: {e}")
            else:
                result["error"] = "Dump file was not created"
                return result
            
        except Exception as e:
            logger.error(f"Error during PostgreSQL backup: {e}")
            result["error"] = str(e)
        
        return result
    
    async def restore_backup(self, storage_path: str, target_database: Optional[str] = None) -> Dict[str, Any]:
        """Восстанавливает базу данных из резервной копии"""
        result = {
            "success": False,
            "error": None
        }
        
        try:
            # Расшифровываем пароль
            password = decrypt_password(self.task.password)
            
            # Определяем целевую БД
            restore_db = target_database or self.task.database
            
            # Загружаем файл из хранилища
            from storage_manager import StorageManager
            
            # Определяем тип хранилища и конфигурацию
            if self.storage_config:
                # Используем новое универсальное хранилище
                storage_type = self.storage_config.storage_type
                config_data = self.storage_config.config_data if isinstance(self.storage_config.config_data, dict) else {}
            elif self.s3_config:
                # Обратная совместимость с S3Config
                storage_type = "s3"
                config_data = {
                    "endpoint": self.s3_config.endpoint,
                    "access_key": self.s3_config.access_key,
                    "secret_key": self.s3_config.secret_key,
                    "bucket_name": self.s3_config.bucket_name,
                    "region": self.s3_config.region,
                    "use_ssl": self.s3_config.use_ssl
                }
            else:
                result["error"] = "No storage configuration provided"
                return result
            
            # Извлекаем имя файла из пути хранилища
            # Формат пути: s3://bucket/path, sftp://host/path, nfs://server/path, local://path
            if "/" in storage_path:
                filename = storage_path.split("/")[-1]
            else:
                filename = Path(storage_path).name
            
            local_file = self.temp_dir / filename
            
            try:
                await StorageManager.download_backup(
                    storage_type,
                    config_data,
                    storage_path,
                    str(local_file)
                )
                logger.info(f"Downloaded backup from {storage_type}: {local_file}")
            except Exception as e:
                result["error"] = f"Failed to download from {storage_type}: {str(e)}"
                return result
            
            # Определяем формат по расширению
            file_ext = local_file.suffix.lower()
            if file_ext == ".dump" or file_ext == ".custom":
                format_type = "custom"
                restore_cmd = "pg_restore"
            elif file_ext == ".tar":
                format_type = "tar"
                restore_cmd = "pg_restore"
            else:
                format_type = "plain"
                restore_cmd = "psql"
            
            # Формируем команду восстановления
            if restore_cmd == "pg_restore":
                cmd = [
                    "pg_restore",
                    f"--host={self.task.host}",
                    f"--port={self.task.port}",
                    f"--username={self.task.username}",
                    f"--dbname={restore_db}",
                    "--clean",
                    "--if-exists",
                    str(local_file)
                ]
            else:
                # Для plain SQL используем psql
                cmd = [
                    "psql",
                    f"--host={self.task.host}",
                    f"--port={self.task.port}",
                    f"--username={self.task.username}",
                    f"--dbname={restore_db}",
                    "--file", str(local_file)
                ]
            
            env = os.environ.copy()
            env["PGPASSWORD"] = password
            
            logger.info(f"Restoring database {restore_db} from {local_file}")
            
            # Выполняем восстановление
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env
            )
            
            stdout, stderr = await process.communicate()
            
            if process.returncode != 0:
                error_msg = stderr.decode() if stderr else "Unknown error"
                logger.error(f"Restore failed: {error_msg}")
                result["error"] = error_msg
            else:
                result["success"] = True
                logger.info(f"Database {restore_db} restored successfully")
            
            # Удаляем локальный файл
            try:
                local_file.unlink()
            except Exception as e:
                logger.warning(f"Failed to remove local restore file: {e}")
            
        except Exception as e:
            logger.error(f"Error during PostgreSQL restore: {e}")
            result["error"] = str(e)
        
        return result

