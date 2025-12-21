from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey, Text, JSON, Float
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from database import Base


class User(Base):
    __tablename__ = "users"
    
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(50), unique=True, index=True, nullable=False)
    email = Column(String(100), unique=True, index=True, nullable=False)
    password_hash = Column(String(255), nullable=False)
    is_admin = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())


class Agent(Base):
    __tablename__ = "agents"
    
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    ip_address = Column(String(45), nullable=False)  # IPv4 или IPv6
    port = Column(Integer, default=11540)
    hostname = Column(String(255))
    is_active = Column(Boolean, default=True)
    last_seen = Column(DateTime(timezone=True))
    storage_config_id = Column(Integer, ForeignKey("storage_configs.id"), nullable=True)  # Хранилище по умолчанию для агента
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    
    # Связи
    backup_tasks = relationship("BackupTask", back_populates="agent")
    agent_status = relationship("AgentStatus", back_populates="agent", uselist=False)
    storage_config = relationship("StorageConfig", foreign_keys=[storage_config_id])


class AgentStatus(Base):
    __tablename__ = "agent_status"
    
    id = Column(Integer, primary_key=True, index=True)
    agent_id = Column(Integer, ForeignKey("agents.id"), unique=True, nullable=False)
    
    # Мониторинг системы
    disk_free_gb = Column(Float)
    disk_total_gb = Column(Float)
    memory_free_mb = Column(Float)
    memory_total_mb = Column(Float)
    cpu_load_percent = Column(Float)
    network_rx_mb = Column(Float)  # Получено данных
    network_tx_mb = Column(Float)  # Отправлено данных
    
    # Статус
    is_online = Column(Boolean, default=False)
    last_update = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    
    # Связи
    agent = relationship("Agent", back_populates="agent_status")
    monitored_backups = relationship("AgentBackupInfo", back_populates="agent_status")


class AgentBackupInfo(Base):
    """Информация о бэкапах от агента"""
    __tablename__ = "agent_backup_info"
    
    id = Column(Integer, primary_key=True, index=True)
    agent_id = Column(Integer, ForeignKey("agents.id"), nullable=False)
    agent_status_id = Column(Integer, ForeignKey("agent_status.id"), nullable=False)
    task_id = Column(Integer, ForeignKey("backup_tasks.id"), nullable=False)
    
    # Информация о бэкапе
    source_path = Column(String(500), nullable=False)
    archive_name = Column(String(500))  # IP + путь + дата
    backup_date = Column(DateTime(timezone=True))  # Когда снималась копия
    s3_upload_date = Column(DateTime(timezone=True))  # Когда отправлялась на S3
    archive_size_mb = Column(Float)
    s3_path = Column(String(500))
    status = Column(String(50))  # success, error, uploading
    
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    
    # Связи
    agent = relationship("Agent")
    agent_status = relationship("AgentStatus", back_populates="monitored_backups")
    task = relationship("BackupTask")


class StorageConfig(Base):
    """Универсальная конфигурация хранилища для бэкапов"""
    __tablename__ = "storage_configs"
    
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False, unique=True)
    storage_type = Column(String(20), nullable=False)  # s3, sftp, nfs, local
    
    # Общие параметры
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    
    # Статус хранилища
    last_check = Column(DateTime(timezone=True))
    free_space_gb = Column(Float)
    total_space_gb = Column(Float)
    used_space_gb = Column(Float)
    connection_error = Column(Text)  # Ошибка подключения
    
    # Параметры хранилища (JSON для гибкости)
    # Для S3: endpoint, access_key, secret_key, bucket_name, region, use_ssl
    # Для SFTP: host, port, username, password, base_path
    # Для NFS: server, export_path, mount_point, options
    # Для Local: base_path
    config_data = Column(JSON, nullable=False)  # JSON с параметрами хранилища
    
    # Связи
    backup_tasks = relationship("BackupTask", back_populates="storage_config")
    postgres_backup_tasks = relationship("PostgresBackupTask", back_populates="storage_config")


# Обратная совместимость - оставляем S3Config для миграции
class S3Config(Base):
    __tablename__ = "s3_configs"
    
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False, unique=True)
    endpoint = Column(String(255), nullable=False)
    access_key = Column(String(255), nullable=False)
    secret_key = Column(String(255), nullable=False)
    bucket_name = Column(String(100), nullable=False)
    region = Column(String(50), default="us-east-1")
    use_ssl = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    
    # Статус S3
    last_check = Column(DateTime(timezone=True))
    free_space_gb = Column(Float)
    total_space_gb = Column(Float)
    used_space_gb = Column(Float)
    connection_error = Column(Text)  # Ошибка подключения к S3
    
    # Связи (для обратной совместимости)
    backup_tasks = relationship("BackupTask", back_populates="s3_config", foreign_keys="BackupTask.s3_config_id")


class Settings(Base):
    """Настройки системы"""
    __tablename__ = "settings"
    
    id = Column(Integer, primary_key=True, index=True)
    
    # Mattermost настройки
    mattermost_webhook_url = Column(String(500))
    mattermost_enabled = Column(Boolean, default=False)
    mattermost_daily_report = Column(Boolean, default=False)
    mattermost_report_time = Column(String(10), default="09:00")  # Время отправки отчета
    
    # Настройки мониторинга
    agent_poll_interval = Column(Integer, default=60)  # Интервал опроса агентов в секундах
    s3_check_interval = Column(Integer, default=86400)  # Интервал проверки S3 в секундах (24 часа)
    
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())


class BackupTask(Base):
    __tablename__ = "backup_tasks"
    
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(200), nullable=False)
    agent_id = Column(Integer, ForeignKey("agents.id"), nullable=False)
    s3_config_id = Column(Integer, ForeignKey("s3_configs.id"), nullable=True)  # Для обратной совместимости
    storage_config_id = Column(Integer, ForeignKey("storage_configs.id"), nullable=True)  # Новое хранилище
    
    # Параметры резервного копирования
    source_path = Column(String(500), nullable=False)  # Путь к папке для бэкапа
    filesystem = Column(String(100))  # Файловая система (получена от агента)
    
    # Расписание
    schedule_cron = Column(String(100), nullable=False)  # Cron выражение
    schedule_enabled = Column(Boolean, default=True)
    
    # Параметры архивации
    create_archive = Column(Boolean, default=True)
    archive_format = Column(String(10), default="tar.gz")
    
    # Docker Compose параметры
    is_docker_compose = Column(Boolean, default=False)
    docker_compose_path = Column(String(500))  # Путь к docker-compose.yml
    
    # Очистка старых бэкапов
    cleanup_enabled = Column(Boolean, default=False)
    cleanup_days = Column(Integer, default=30)  # Удалять бэкапы старше N дней
    
    # Статус
    is_active = Column(Boolean, default=True)
    last_run = Column(DateTime(timezone=True))
    next_run = Column(DateTime(timezone=True))
    last_status = Column(String(50))  # success, error, running
    last_error = Column(Text)
    
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    
    # Связи
    agent = relationship("Agent", back_populates="backup_tasks")
    s3_config = relationship("S3Config", back_populates="backup_tasks", foreign_keys=[s3_config_id])
    storage_config = relationship("StorageConfig", back_populates="backup_tasks", foreign_keys=[storage_config_id])
    backup_history = relationship("BackupHistory", back_populates="task")


class BackupHistory(Base):
    __tablename__ = "backup_history"
    
    id = Column(Integer, primary_key=True, index=True)
    task_id = Column(Integer, ForeignKey("backup_tasks.id"), nullable=False)
    
    # Результаты выполнения
    status = Column(String(50), nullable=False)  # success, error, running
    started_at = Column(DateTime(timezone=True), server_default=func.now())
    finished_at = Column(DateTime(timezone=True))
    duration_seconds = Column(Integer)
    
    # Детали
    archive_size_mb = Column(Float)
    files_count = Column(Integer)
    error_message = Column(Text)
    s3_path = Column(String(500))  # Путь в S3
    
    # Связи
    task = relationship("BackupTask", back_populates="backup_history")


class PostgresBackupTask(Base):
    """Задача резервного копирования PostgreSQL базы данных"""
    __tablename__ = "postgres_backup_tasks"
    
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(200), nullable=False)
    s3_config_id = Column(Integer, ForeignKey("s3_configs.id"), nullable=True)  # Для обратной совместимости
    storage_config_id = Column(Integer, ForeignKey("storage_configs.id"), nullable=True)  # Новое хранилище
    
    # Параметры подключения к PostgreSQL
    host = Column(String(255), nullable=False)
    port = Column(Integer, default=5432)
    username = Column(String(100), nullable=False)
    password = Column(String(255), nullable=False)  # Зашифрованное
    database = Column(String(100), nullable=False)
    
    # Параметры бэкапа
    backup_format = Column(String(20), default="custom")  # custom, plain, tar, directory
    compression_level = Column(Integer, default=6)  # 0-9 для custom формата
    include_schema = Column(Boolean, default=True)
    include_data = Column(Boolean, default=True)
    include_roles = Column(Boolean, default=False)
    include_tablespaces = Column(Boolean, default=False)
    
    # Расписание
    schedule_cron = Column(String(100), nullable=False)  # Cron выражение
    schedule_enabled = Column(Boolean, default=True)
    
    # Очистка старых бэкапов
    cleanup_enabled = Column(Boolean, default=True)
    cleanup_days = Column(Integer, default=30)  # Удалять бэкапы старше N дней
    
    # Статус
    is_active = Column(Boolean, default=True)
    last_run = Column(DateTime(timezone=True))
    next_run = Column(DateTime(timezone=True))
    last_status = Column(String(50))  # success, error, running
    last_error = Column(Text)
    
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    
    # Связи
    s3_config = relationship("S3Config", foreign_keys=[s3_config_id])
    storage_config = relationship("StorageConfig", back_populates="postgres_backup_tasks", foreign_keys=[storage_config_id])
    backup_history = relationship("PostgresBackupHistory", back_populates="task")


class PostgresBackupHistory(Base):
    """История выполнения PostgreSQL бэкапов"""
    __tablename__ = "postgres_backup_history"
    
    id = Column(Integer, primary_key=True, index=True)
    task_id = Column(Integer, ForeignKey("postgres_backup_tasks.id"), nullable=False)
    
    # Результаты выполнения
    status = Column(String(50), nullable=False)  # success, error, running
    started_at = Column(DateTime(timezone=True), server_default=func.now())
    finished_at = Column(DateTime(timezone=True))
    duration_seconds = Column(Integer)
    
    # Детали
    dump_size_mb = Column(Float)
    s3_path = Column(String(500))  # Путь в S3
    dump_filename = Column(String(500))  # Имя файла дампа
    error_message = Column(Text)
    
    # Связи
    task = relationship("PostgresBackupTask", back_populates="backup_history")


class Report(Base):
    """Шаблон отчета"""
    __tablename__ = "reports"
    
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(200), nullable=False)
    description = Column(Text)
    
    # Выбранные агенты и СУБД для отчета
    agent_ids = Column(JSON, nullable=False)  # Список ID агентов
    postgres_task_ids = Column(JSON, nullable=False)  # Список ID задач PostgreSQL
    
    # Настройки отправки
    send_to_mattermost = Column(Boolean, default=False)
    enabled = Column(Boolean, default=True)
    
    # Периодичность отправки
    schedule_type = Column(String(50), nullable=False)  # daily, weekly, hourly, custom_hours
    schedule_hour = Column(Integer)  # Час для daily/weekly
    schedule_minute = Column(Integer)  # Минута для всех типов
    schedule_day_of_week = Column(Integer)  # День недели для weekly (0-6, где 0=понедельник)
    schedule_hours_interval = Column(Integer)  # Интервал в часах для custom_hours
    
    # Последняя отправка
    last_sent = Column(DateTime(timezone=True))
    next_send = Column(DateTime(timezone=True))
    
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    
    # Связи
    report_history = relationship("ReportHistory", back_populates="report")


class ReportHistory(Base):
    """История отправки отчетов"""
    __tablename__ = "report_history"
    
    id = Column(Integer, primary_key=True, index=True)
    report_id = Column(Integer, ForeignKey("reports.id"), nullable=False)
    
    sent_at = Column(DateTime(timezone=True), server_default=func.now())
    status = Column(String(50), nullable=False)  # success, error
    error_message = Column(Text)
    mattermost_response = Column(Text)  # Ответ от Mattermost
    
    # Связи
    report = relationship("Report", back_populates="report_history")

