from pydantic import BaseModel, EmailStr
from typing import Optional, List
from datetime import datetime


# User schemas
class UserBase(BaseModel):
    username: str
    email: EmailStr
    is_admin: bool = False


class UserCreate(UserBase):
    password: str


class UserResponse(UserBase):
    id: int
    created_at: datetime
    
    class Config:
        from_attributes = True


# Agent schemas
class AgentBase(BaseModel):
    name: str
    ip_address: str
    port: int = 11540
    hostname: Optional[str] = None


class AgentCreate(AgentBase):
    pass


class AgentResponse(AgentBase):
    id: int
    is_active: bool
    last_seen: Optional[datetime]
    created_at: datetime
    updated_at: Optional[datetime]
    
    class Config:
        from_attributes = True


class AgentStatusResponse(BaseModel):
    agent_id: int
    disk_free_gb: Optional[float]
    disk_total_gb: Optional[float]
    memory_free_mb: Optional[float]
    memory_total_mb: Optional[float]
    cpu_load_percent: Optional[float]
    network_rx_mb: Optional[float]
    network_tx_mb: Optional[float]
    is_online: bool
    last_update: datetime
    
    class Config:
        from_attributes = True


# S3 Config schemas
class S3ConfigBase(BaseModel):
    name: str
    endpoint: str
    access_key: str
    secret_key: str
    bucket_name: str
    region: str = "us-east-1"
    use_ssl: bool = False


class S3ConfigCreate(S3ConfigBase):
    pass


class S3ConfigResponse(S3ConfigBase):
    id: int
    created_at: datetime
    updated_at: Optional[datetime]
    
    class Config:
        from_attributes = True


# Backup Task schemas
class BackupTaskBase(BaseModel):
    name: str
    agent_id: int
    s3_config_id: int
    source_path: str
    schedule_cron: str
    create_archive: bool = True
    archive_format: str = "tar.gz"
    is_docker_compose: bool = False
    docker_compose_path: Optional[str] = None
    cleanup_enabled: bool = False
    cleanup_days: int = 30
    schedule_enabled: bool = True


class BackupTaskCreate(BackupTaskBase):
    pass


class BackupTaskResponse(BackupTaskBase):
    id: int
    filesystem: Optional[str]
    is_active: bool
    last_run: Optional[datetime]
    next_run: Optional[datetime]
    last_status: Optional[str]
    last_error: Optional[str]
    created_at: datetime
    updated_at: Optional[datetime]
    
    class Config:
        from_attributes = True


# Backup History schemas
class BackupHistoryResponse(BaseModel):
    id: int
    task_id: int
    status: str
    started_at: datetime
    finished_at: Optional[datetime]
    duration_seconds: Optional[int]
    archive_size_mb: Optional[float]
    files_count: Optional[int]
    error_message: Optional[str]
    s3_path: Optional[str]
    
    class Config:
        from_attributes = True


# Agent API schemas (для взаимодействия с агентом)
class AgentSystemInfo(BaseModel):
    disk_free_gb: float
    disk_total_gb: float
    memory_free_mb: float
    memory_total_mb: float
    cpu_load_percent: float
    network_rx_mb: float
    network_tx_mb: float


class AgentFilesystemInfo(BaseModel):
    filesystem: str
    mount_point: str
    available_gb: float
    total_gb: float


class AgentTaskConfig(BaseModel):
    task_id: int
    source_path: str
    create_archive: bool
    archive_format: str
    s3_endpoint: str
    s3_access_key: str
    s3_secret_key: str
    s3_bucket: str
    s3_region: str
    cleanup_enabled: bool
    cleanup_days: int
    is_docker_compose: bool
    docker_compose_path: Optional[str] = None
    schedule_cron: str


class AgentTaskExecute(BaseModel):
    task_id: int
    source_path: str
    create_archive: bool
    archive_format: str
    s3_endpoint: str
    s3_access_key: str
    s3_secret_key: str
    s3_bucket: str
    s3_region: str
    cleanup_enabled: bool
    cleanup_days: int
    is_docker_compose: bool
    docker_compose_path: Optional[str] = None


class AgentBackupInfoResponse(BaseModel):
    source_path: str
    archive_name: str
    backup_date: Optional[datetime]
    s3_upload_date: Optional[datetime]
    archive_size_mb: Optional[float]
    s3_path: Optional[str]
    status: str


# PostgreSQL Backup schemas
class PostgresBackupTaskBase(BaseModel):
    name: str
    s3_config_id: int
    host: str
    port: int = 5432
    username: str
    password: str
    database: str
    backup_format: str = "custom"
    compression_level: int = 6
    include_schema: bool = True
    include_data: bool = True
    include_roles: bool = False
    include_tablespaces: bool = False
    schedule_cron: str
    schedule_enabled: bool = True
    cleanup_enabled: bool = True
    cleanup_days: int = 30


class PostgresBackupTaskCreate(PostgresBackupTaskBase):
    pass


class PostgresBackupTaskResponse(PostgresBackupTaskBase):
    id: int
    is_active: bool
    last_run: Optional[datetime]
    next_run: Optional[datetime]
    last_status: Optional[str]
    last_error: Optional[str]
    created_at: datetime
    updated_at: Optional[datetime]
    
    class Config:
        from_attributes = True


class PostgresBackupHistoryResponse(BaseModel):
    id: int
    task_id: int
    status: str
    started_at: datetime
    finished_at: Optional[datetime]
    duration_seconds: Optional[int]
    dump_size_mb: Optional[float]
    s3_path: Optional[str]
    dump_filename: Optional[str]
    error_message: Optional[str]
    
    class Config:
        from_attributes = True


class PostgresRestoreRequest(BaseModel):
    task_id: int
    s3_path: str
    target_database: Optional[str] = None
