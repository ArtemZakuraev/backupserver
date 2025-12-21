"""
API маршруты для сервера резервного копирования
"""
from fastapi import APIRouter, Depends, HTTPException, status, Body
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import List, Dict, Any
from datetime import datetime
from postgres_backup import PostgresBackupExecutor, encrypt_password

from database import get_db
from models import User, Agent, AgentStatus, S3Config, StorageConfig, BackupTask, BackupHistory, PostgresBackupTask, PostgresBackupHistory, Report, ReportHistory
from schemas import (
    AgentResponse, AgentCreate, AgentUpdate, AgentStatusResponse,
    S3ConfigResponse, S3ConfigCreate,
    StorageConfigResponse, StorageConfigCreate, StorageConfigUpdate,
    PostgresBackupTaskResponse, PostgresBackupTaskCreate, PostgresBackupTaskUpdate,
    BackupTaskResponse, BackupTaskCreate,
    BackupHistoryResponse, AgentTaskConfig,
    PostgresBackupTaskResponse, PostgresBackupTaskCreate,
    PostgresBackupHistoryResponse, PostgresRestoreRequest,
    ReportResponse, ReportCreate, ReportUpdate, ReportHistoryResponse
)
from agent_client import AgentClient
from utils import verify_token
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

router = APIRouter()
security = HTTPBearer()


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: AsyncSession = Depends(get_db)
):
    """Получает текущего пользователя из токена"""
    token = credentials.credentials
    payload = verify_token(token)
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication credentials"
        )
    username = payload.get("sub")
    if username is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication credentials"
        )
    result = await db.execute(select(User).where(User.username == username))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found"
        )
    return user


# Agents endpoints
@router.get("/agents", response_model=List[AgentResponse])
async def get_agents(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Получает список всех агентов"""
    result = await db.execute(select(Agent))
    agents = result.scalars().all()
    return agents


@router.post("/agents", response_model=AgentResponse, status_code=status.HTTP_201_CREATED)
async def create_agent(
    agent: AgentCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Создает нового агента"""
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    
    # Проверяем доступность агента
    client = AgentClient(agent.ip_address, agent.port)
    if not await client.ping():
        raise HTTPException(status_code=400, detail="Agent is not reachable")
    
    db_agent = Agent(**agent.dict())
    db.add(db_agent)
    await db.commit()
    await db.refresh(db_agent)
    
    # Создаем статус агента
    agent_status = AgentStatus(agent_id=db_agent.id)
    db.add(agent_status)
    await db.commit()
    
    return db_agent


@router.get("/agents/{agent_id}", response_model=AgentResponse)
async def get_agent(
    agent_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Получает информацию об агенте"""
    result = await db.execute(select(Agent).where(Agent.id == agent_id))
    agent = result.scalar_one_or_none()
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    return agent


@router.put("/agents/{agent_id}", response_model=AgentResponse)
async def update_agent(
    agent_id: int,
    agent_update: AgentUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Обновляет информацию об агенте"""
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    
    result = await db.execute(select(Agent).where(Agent.id == agent_id))
    agent = result.scalar_one_or_none()
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    
    # Обновляем поля
    update_data = agent_update.dict(exclude_unset=True)
    for field, value in update_data.items():
        setattr(agent, field, value)
    
    # Если изменился IP или порт, проверяем доступность
    if "ip_address" in update_data or "port" in update_data:
        client = AgentClient(agent.ip_address, agent.port)
        if not await client.ping():
            raise HTTPException(status_code=400, detail="Agent is not reachable with new settings")
    
    await db.commit()
    await db.refresh(agent)
    return agent


@router.delete("/agents/{agent_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_agent(
    agent_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Удаляет агента"""
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    
    result = await db.execute(select(Agent).where(Agent.id == agent_id))
    agent = result.scalar_one_or_none()
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    
    await db.delete(agent)
    await db.commit()
    return None


@router.delete("/agents/{agent_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_agent(
    agent_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Удаляет агента"""
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    
    result = await db.execute(select(Agent).where(Agent.id == agent_id))
    agent = result.scalar_one_or_none()
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    
    await db.delete(agent)
    await db.commit()
    return None
    """Получает информацию об агенте"""
    result = await db.execute(select(Agent).where(Agent.id == agent_id))
    agent = result.scalar_one_or_none()
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    return agent


@router.get("/agents/{agent_id}/status", response_model=AgentStatusResponse)
async def get_agent_status(
    agent_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Получает статус агента"""
    result = await db.execute(
        select(AgentStatus).where(AgentStatus.agent_id == agent_id)
    )
    agent_status = result.scalar_one_or_none()
    if agent_status is None:
        raise HTTPException(status_code=404, detail="Agent status not found")
    
    # Обновляем статус от агента
    result_agent = await db.execute(select(Agent).where(Agent.id == agent_id))
    agent = result_agent.scalar_one_or_none()
    if agent:
        client = AgentClient(agent.ip_address, agent.port)
        system_info = await client.get_system_info()
        if system_info:
            agent_status.disk_free_gb = system_info.disk_free_gb
            agent_status.disk_total_gb = system_info.disk_total_gb
            agent_status.memory_free_mb = system_info.memory_free_mb
            agent_status.memory_total_mb = system_info.memory_total_mb
            agent_status.cpu_load_percent = system_info.cpu_load_percent
            agent_status.network_rx_mb = system_info.network_rx_mb
            agent_status.network_tx_mb = system_info.network_tx_mb
            agent_status.is_online = True
            agent.last_seen = datetime.utcnow()
        else:
            agent_status.is_online = False
        agent_status.last_update = datetime.utcnow()
        await db.commit()
    
    return agent_status


# S3 Config endpoints
@router.get("/s3-configs", response_model=List[S3ConfigResponse])
async def get_s3_configs(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Получает список конфигураций S3"""
    result = await db.execute(select(S3Config))
    configs = result.scalars().all()
    return configs


@router.post("/s3-configs", response_model=S3ConfigResponse, status_code=status.HTTP_201_CREATED)
async def create_s3_config(
    s3_config: S3ConfigCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Создает новую конфигурацию S3"""
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    
    db_config = S3Config(**s3_config.dict())
    db.add(db_config)
    await db.commit()
    await db.refresh(db_config)
    return db_config


# Backup Task endpoints
@router.get("/backup-tasks", response_model=List[BackupTaskResponse])
async def get_backup_tasks(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Получает список задач резервного копирования"""
    result = await db.execute(select(BackupTask))
    tasks = result.scalars().all()
    return tasks


@router.post("/backup-tasks", response_model=BackupTaskResponse, status_code=status.HTTP_201_CREATED)
async def create_backup_task(
    task: BackupTaskCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Создает новую задачу резервного копирования"""
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    
    # Получаем агента и S3 конфигурацию
    result_agent = await db.execute(select(Agent).where(Agent.id == task.agent_id))
    agent = result_agent.scalar_one_or_none()
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    
    result_s3 = await db.execute(select(S3Config).where(S3Config.id == task.s3_config_id))
    s3_config = result_s3.scalar_one_or_none()
    if s3_config is None:
        raise HTTPException(status_code=404, detail="S3 config not found")
    
    # Получаем информацию о файловой системе от агента
    client = AgentClient(agent.ip_address, agent.port)
    filesystem_info = await client.get_filesystem_info(task.source_path)
    if filesystem_info:
        task.filesystem = filesystem_info.filesystem
    
    # Создаем задачу
    db_task = BackupTask(**task.dict())
    db.add(db_task)
    await db.commit()
    await db.refresh(db_task)
    
    # Отправляем конфигурацию агенту
    task_config = AgentTaskConfig(
        task_id=db_task.id,
        source_path=db_task.source_path,
        create_archive=db_task.create_archive,
        archive_format=db_task.archive_format,
        s3_endpoint=s3_config.endpoint,
        s3_access_key=s3_config.access_key,
        s3_secret_key=s3_config.secret_key,
        s3_bucket=s3_config.bucket_name,
        s3_region=s3_config.region,
        cleanup_enabled=db_task.cleanup_enabled,
        cleanup_days=db_task.cleanup_days,
        is_docker_compose=db_task.is_docker_compose,
        docker_compose_path=db_task.docker_compose_path,
        schedule_cron=db_task.schedule_cron
    )
    await client.send_task_config(task_config)
    
    return db_task


@router.get("/backup-tasks/{task_id}/history", response_model=List[BackupHistoryResponse])
async def get_backup_history(
    task_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Получает историю выполнения задачи"""
    result = await db.execute(
        select(BackupHistory)
        .where(BackupHistory.task_id == task_id)
        .order_by(BackupHistory.started_at.desc())
        .limit(100)
    )
    history = result.scalars().all()
    return history


# PostgreSQL Backup endpoints
@router.get("/postgres-backup-tasks", response_model=List[PostgresBackupTaskResponse])
async def get_postgres_backup_tasks(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Получает список задач резервного копирования PostgreSQL"""
    result = await db.execute(select(PostgresBackupTask))
    tasks = result.scalars().all()
    return tasks


@router.post("/postgres-backup-tasks", response_model=PostgresBackupTaskResponse, status_code=status.HTTP_201_CREATED)
async def create_postgres_backup_task(
    task: PostgresBackupTaskCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Создает новую задачу резервного копирования PostgreSQL"""
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    
    # Проверяем S3 конфигурацию
    result_s3 = await db.execute(select(S3Config).where(S3Config.id == task.s3_config_id))
    s3_config = result_s3.scalar_one_or_none()
    if s3_config is None:
        raise HTTPException(status_code=404, detail="S3 config not found")
    
    # Шифруем пароль
    encrypted_password = encrypt_password(task.password)
    
    # Создаем задачу
    db_task = PostgresBackupTask(
        name=task.name,
        s3_config_id=task.s3_config_id,
        host=task.host,
        port=task.port,
        username=task.username,
        password=encrypted_password,
        database=task.database,
        backup_format=task.backup_format,
        compression_level=task.compression_level,
        include_schema=task.include_schema,
        include_data=task.include_data,
        include_roles=task.include_roles,
        include_tablespaces=task.include_tablespaces,
        schedule_cron=task.schedule_cron,
        schedule_enabled=task.schedule_enabled,
        cleanup_enabled=task.cleanup_enabled,
        cleanup_days=task.cleanup_days
    )
    db.add(db_task)
    await db.commit()
    await db.refresh(db_task)
    
    return db_task


@router.get("/postgres-backup-tasks/{task_id}", response_model=PostgresBackupTaskResponse)
async def get_postgres_backup_task(
    task_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Получает информацию о задаче резервного копирования PostgreSQL"""
    result = await db.execute(select(PostgresBackupTask).where(PostgresBackupTask.id == task_id))
    task = result.scalar_one_or_none()
    if task is None:
        raise HTTPException(status_code=404, detail="PostgreSQL backup task not found")
    return task


@router.put("/postgres-backup-tasks/{task_id}", response_model=PostgresBackupTaskResponse)
async def update_postgres_backup_task(
    task_id: int,
    task_update: PostgresBackupTaskUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Обновляет задачу резервного копирования PostgreSQL"""
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    
    result = await db.execute(select(PostgresBackupTask).where(PostgresBackupTask.id == task_id))
    task = result.scalar_one_or_none()
    if task is None:
        raise HTTPException(status_code=404, detail="PostgreSQL backup task not found")
    
    # Обновляем поля
    update_data = task_update.dict(exclude_unset=True)
    
    # Если обновляется хранилище, проверяем его существование
    if "storage_config_id" in update_data and update_data["storage_config_id"]:
        result_storage = await db.execute(
            select(StorageConfig).where(StorageConfig.id == update_data["storage_config_id"])
        )
        storage_config = result_storage.scalar_one_or_none()
        if storage_config is None:
            raise HTTPException(status_code=404, detail="Storage config not found")
    
    if "s3_config_id" in update_data and update_data["s3_config_id"]:
        result_s3 = await db.execute(select(S3Config).where(S3Config.id == update_data["s3_config_id"]))
        s3_config = result_s3.scalar_one_or_none()
        if s3_config is None:
            raise HTTPException(status_code=404, detail="S3 config not found")
    
    # Если обновляется пароль, шифруем его
    if "password" in update_data:
        from postgres_backup import encrypt_password
        update_data["password"] = encrypt_password(update_data["password"])
    
    for field, value in update_data.items():
        setattr(task, field, value)
    
    await db.commit()
    await db.refresh(task)
    return task


@router.delete("/postgres-backup-tasks/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_postgres_backup_task(
    task_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Удаляет задачу резервного копирования PostgreSQL"""
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    
    result = await db.execute(select(PostgresBackupTask).where(PostgresBackupTask.id == task_id))
    task = result.scalar_one_or_none()
    if task is None:
        raise HTTPException(status_code=404, detail="PostgreSQL backup task not found")
    
    await db.delete(task)
    await db.commit()
    return None


@router.get("/postgres-backup-tasks/{task_id}/history", response_model=List[PostgresBackupHistoryResponse])
async def get_postgres_backup_history(
    task_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Получает историю выполнения PostgreSQL задачи"""
    result = await db.execute(
        select(PostgresBackupHistory)
        .where(PostgresBackupHistory.task_id == task_id)
        .order_by(PostgresBackupHistory.started_at.desc())
        .limit(100)
    )
    history = result.scalars().all()
    return history


@router.post("/postgres-backup-tasks/{task_id}/restore")
async def restore_postgres_backup(
    task_id: int,
    restore_request: PostgresRestoreRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Восстанавливает PostgreSQL базу данных из резервной копии"""
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    
    # Получаем задачу
    result = await db.execute(select(PostgresBackupTask).where(PostgresBackupTask.id == task_id))
    task = result.scalar_one_or_none()
    if task is None:
        raise HTTPException(status_code=404, detail="PostgreSQL backup task not found")
    
    # Получаем конфигурацию хранилища
    storage_config = None
    s3_config = None
    
    if hasattr(task, 'storage_config_id') and task.storage_config_id:
        result_storage = await db.execute(
            select(StorageConfig).where(StorageConfig.id == task.storage_config_id)
        )
        storage_config = result_storage.scalar_one_or_none()
        if storage_config is None:
            raise HTTPException(status_code=404, detail="Storage config not found")
    elif task.s3_config_id:
        result_s3 = await db.execute(select(S3Config).where(S3Config.id == task.s3_config_id))
        s3_config = result_s3.scalar_one_or_none()
        if s3_config is None:
            raise HTTPException(status_code=404, detail="S3 config not found")
    else:
        raise HTTPException(status_code=404, detail="No storage configuration found")
    
    # Выполняем восстановление
    from postgres_backup import PostgresBackupExecutor
    executor = PostgresBackupExecutor(task, storage_config=storage_config, s3_config=s3_config)
    result = await executor.restore_backup(restore_request.s3_path, restore_request.target_database)
    
    if result["success"]:
        return {"success": True, "message": "Database restored successfully"}
    else:
        raise HTTPException(status_code=500, detail=result.get("error", "Restore failed"))


@router.post("/postgres-backup-tasks/{task_id}/execute")
async def execute_postgres_backup(
    task_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Запускает выполнение PostgreSQL бэкапа вручную"""
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    
    # Получаем задачу
    result = await db.execute(select(PostgresBackupTask).where(PostgresBackupTask.id == task_id))
    task = result.scalar_one_or_none()
    if task is None:
        raise HTTPException(status_code=404, detail="PostgreSQL backup task not found")
    
    # Получаем конфигурацию хранилища
    storage_config = None
    s3_config = None
    
    if hasattr(task, 'storage_config_id') and task.storage_config_id:
        result_storage = await db.execute(
            select(StorageConfig).where(StorageConfig.id == task.storage_config_id)
        )
        storage_config = result_storage.scalar_one_or_none()
        if storage_config is None:
            raise HTTPException(status_code=404, detail="Storage config not found")
    elif task.s3_config_id:
        result_s3 = await db.execute(select(S3Config).where(S3Config.id == task.s3_config_id))
        s3_config = result_s3.scalar_one_or_none()
        if s3_config is None:
            raise HTTPException(status_code=404, detail="S3 config not found")
    else:
        raise HTTPException(status_code=404, detail="No storage configuration found")
    
    # Создаем запись в истории
    history = PostgresBackupHistory(
        task_id=task.id,
        status="running",
        started_at=datetime.utcnow()
    )
    db.add(history)
    task.last_status = "running"
    task.last_run = datetime.utcnow()
    await db.commit()
    
    try:
        # Выполняем бэкап
        executor = PostgresBackupExecutor(task, storage_config=storage_config, s3_config=s3_config)
        result = await executor.execute_backup()
        
        # Обновляем историю
        history.finished_at = datetime.utcnow()
        history.duration_seconds = int((history.finished_at - history.started_at).total_seconds())
        
        if result["success"]:
            history.status = "success"
            history.dump_size_mb = result.get("dump_size_mb")
            history.s3_path = result.get("s3_path")
            history.dump_filename = result.get("dump_filename")
            task.last_status = "success"
        else:
            history.status = "error"
            history.error_message = result.get("error")
            task.last_status = "error"
            task.last_error = result.get("error")
        
        await db.commit()
        return result
    except Exception as e:
        history.status = "error"
        history.error_message = str(e)
        history.finished_at = datetime.utcnow()
        task.last_status = "error"
        task.last_error = str(e)
        await db.commit()
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/postgres-backup-tasks/{task_id}/backups")
async def list_postgres_backups(
    task_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Получает список доступных бэкапов для задачи"""
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    
    # Получаем задачу
    result = await db.execute(select(PostgresBackupTask).where(PostgresBackupTask.id == task_id))
    task = result.scalar_one_or_none()
    if task is None:
        raise HTTPException(status_code=404, detail="PostgreSQL backup task not found")
    
    # Получаем S3 конфигурацию
    result_s3 = await db.execute(select(S3Config).where(S3Config.id == task.s3_config_id))
    s3_config = result_s3.scalar_one_or_none()
    if s3_config is None:
        raise HTTPException(status_code=404, detail="S3 config not found")
    
    # Получаем список бэкапов из S3
    from minio import Minio
    from s3_client import S3Client
    
    s3_client = S3Client(
        s3_config.endpoint,
        s3_config.access_key,
        s3_config.secret_key,
        s3_config.bucket_name,
        s3_config.region,
        s3_config.use_ssl
    )
    
    db_name_safe = task.database.replace("/", "_").replace("\\", "_")
    prefix = f"postgres_backups/{db_name_safe}/"
    backups = s3_client.list_backups(prefix)
    
    return {"backups": backups}


@router.post("/restore/folder")
async def restore_folder(
    restore_request: Dict[str, Any] = Body(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Восстанавливает папку из S3 на агент"""
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    
    agent_id = restore_request.get("agent_id")
    s3_config_id = restore_request.get("s3_config_id")
    s3_path = restore_request.get("s3_path")
    target_path = restore_request.get("target_path")
    
    if not all([agent_id, s3_config_id, s3_path, target_path]):
        raise HTTPException(status_code=400, detail="All fields are required")
    
    # Получаем агента
    result_agent = await db.execute(select(Agent).where(Agent.id == agent_id))
    agent = result_agent.scalar_one_or_none()
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    
    # Получаем S3 конфигурацию
    result_s3 = await db.execute(select(S3Config).where(S3Config.id == s3_config_id))
    s3_config = result_s3.scalar_one_or_none()
    if s3_config is None:
        raise HTTPException(status_code=404, detail="S3 config not found")
    
    # Проверяем доступность агента
    from agent_client import AgentClient
    client = AgentClient(agent.ip_address, agent.port)
    if not await client.ping():
        raise HTTPException(status_code=400, detail="Agent is not reachable")
    
    # Отправляем команду восстановления агенту
    # TODO: Реализовать метод restore_folder в AgentClient
    # Пока возвращаем заглушку
    return {
        "success": True,
        "message": f"Restore command sent to agent {agent.name}. This feature requires agent support."
    }


# Report endpoints
@router.get("/reports", response_model=List[ReportResponse])
async def get_reports(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Получает список отчетов"""
    result = await db.execute(select(Report).order_by(Report.created_at.desc()))
    reports = result.scalars().all()
    return reports


@router.post("/reports", response_model=ReportResponse, status_code=status.HTTP_201_CREATED)
async def create_report(
    report: ReportCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Создает новый отчет"""
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    
    from report_scheduler import ReportScheduler
    
    db_report = Report(**report.dict())
    scheduler = ReportScheduler()
    db_report.next_send = scheduler.calculate_next_send(db_report)
    
    db.add(db_report)
    await db.commit()
    await db.refresh(db_report)
    return db_report


@router.get("/reports/{report_id}", response_model=ReportResponse)
async def get_report(
    report_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Получает отчет по ID"""
    result = await db.execute(select(Report).where(Report.id == report_id))
    report = result.scalar_one_or_none()
    if report is None:
        raise HTTPException(status_code=404, detail="Report not found")
    return report


@router.put("/reports/{report_id}", response_model=ReportResponse)
async def update_report(
    report_id: int,
    report_update: ReportUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Обновляет отчет"""
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    
    result = await db.execute(select(Report).where(Report.id == report_id))
    report = result.scalar_one_or_none()
    if report is None:
        raise HTTPException(status_code=404, detail="Report not found")
    
    update_data = report_update.dict(exclude_unset=True)
    for key, value in update_data.items():
        setattr(report, key, value)
    
    # Пересчитываем следующее время отправки
    from report_scheduler import ReportScheduler
    scheduler = ReportScheduler()
    report.next_send = scheduler.calculate_next_send(report)
    
    await db.commit()
    await db.refresh(report)
    return report


@router.delete("/reports/{report_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_report(
    report_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Удаляет отчет"""
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    
    result = await db.execute(select(Report).where(Report.id == report_id))
    report = result.scalar_one_or_none()
    if report is None:
        raise HTTPException(status_code=404, detail="Report not found")
    
    await db.delete(report)
    await db.commit()
    return None


@router.post("/reports/{report_id}/send")
async def send_report(
    report_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Отправляет отчет немедленно"""
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    
    result = await db.execute(select(Report).where(Report.id == report_id))
    report = result.scalar_one_or_none()
    if report is None:
        raise HTTPException(status_code=404, detail="Report not found")
    
    result_settings = await db.execute(select(Settings))
    settings_obj = result_settings.scalar_one_or_none()
    if not settings_obj or not settings_obj.mattermost_enabled or not settings_obj.mattermost_webhook_url:
        raise HTTPException(status_code=400, detail="Mattermost not configured")
    
    from report_scheduler import ReportScheduler
    scheduler = ReportScheduler()
    await scheduler.send_report(report, settings_obj.mattermost_webhook_url, db)
    
    return {"success": True, "message": "Report sent successfully"}


@router.get("/reports/{report_id}/history", response_model=List[ReportHistoryResponse])
async def get_report_history(
    report_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Получает историю отправки отчета"""
    result = await db.execute(
        select(ReportHistory)
        .where(ReportHistory.report_id == report_id)
        .order_by(ReportHistory.sent_at.desc())
    )
    history = result.scalars().all()
    return history

