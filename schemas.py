from pydantic import BaseModel, EmailStr
from typing import Optional, List, Dict, Any
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


class AgentUpdate(BaseModel):
    name: Optional[str] = None
    ip_address: Optional[str] = None
    port: Optional[int] = None
    hostname: Optional[str] = None
    is_active: Optional[bool] = None


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


# Storage Config schemas (универсальное хранилище)
class StorageConfigBase(BaseModel):
    name: str
    storage_type: str  # s3, sftp, nfs, local
    config_data: Dict[str, Any]  # JSON с параметрами хранилища


class StorageConfigCreate(StorageConfigBase):
    pass


class StorageConfigUpdate(BaseModel):
    name: Optional[str] = None
    storage_type: Optional[str] = None
    config_data: Optional[Dict[str, Any]] = None


# Report schemas
class ReportBase(BaseModel):
    name: str
    description: Optional[str] = None
    agent_ids: List[int]
    postgres_task_ids: List[int]
    send_to_mattermost: bool = False
    enabled: bool = True
    schedule_type: str  # daily, weekly, hourly, custom_hours
    schedule_hour: Optional[int] = None
    schedule_minute: Optional[int] = None
    schedule_day_of_week: Optional[int] = None
    schedule_hours_interval: Optional[int] = None


class ReportCreate(ReportBase):
    pass


class ReportUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    agent_ids: Optional[List[int]] = None
    postgres_task_ids: Optional[List[int]] = None
    send_to_mattermost: Optional[bool] = None
    enabled: Optional[bool] = None
    schedule_type: Optional[str] = None
    schedule_hour: Optional[int] = None
    schedule_minute: Optional[int] = None
    schedule_day_of_week: Optional[int] = None
    schedule_hours_interval: Optional[int] = None


class ReportResponse(ReportBase):
    id: int
    last_sent: Optional[datetime] = None
    next_send: Optional[datetime] = None
    created_at: datetime
    updated_at: Optional[datetime] = None
    
    class Config:
        from_attributes = True


class ReportHistoryResponse(BaseModel):
    id: int
    report_id: int
    sent_at: datetime
    status: str
    error_message: Optional[str] = None
    mattermost_response: Optional[str] = None
    
    class Config:
        from_attributes = True


class StorageConfigResponse(StorageConfigBase):
    id: int
    created_at: datetime
    updated_at: Optional[datetime]
    last_check: Optional[datetime]
    free_space_gb: Optional[float]
    total_space_gb: Optional[float]
    used_space_gb: Optional[float]
    connection_error: Optional[str]
    
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
    s3_config_id: Optional[int] = None  # Для обратной совместимости
    storage_config_id: Optional[int] = None  # Новое универсальное хранилище
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


class PostgresBackupTaskUpdate(BaseModel):
    name: Optional[str] = None
    s3_config_id: Optional[int] = None
    storage_config_id: Optional[int] = None
    host: Optional[str] = None
    port: Optional[int] = None
    username: Optional[str] = None
    password: Optional[str] = None
    database: Optional[str] = None
    backup_format: Optional[str] = None
    compression_level: Optional[int] = None
    include_schema: Optional[bool] = None
    include_data: Optional[bool] = None
    include_roles: Optional[bool] = None
    include_tablespaces: Optional[bool] = None
    schedule_cron: Optional[str] = None
    schedule_enabled: Optional[bool] = None
    cleanup_enabled: Optional[bool] = None
    cleanup_days: Optional[int] = None
    is_active: Optional[bool] = None


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
