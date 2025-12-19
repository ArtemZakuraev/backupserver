"""
Веб-маршруты для сервера резервного копирования
"""
from fastapi import APIRouter, Request, Depends, HTTPException, Form, Cookie
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import Optional
from datetime import datetime

from database import get_db
from models import User, Agent, AgentStatus, S3Config, BackupTask, BackupHistory, AgentBackupInfo, Settings, PostgresBackupTask, PostgresBackupHistory
from sqlalchemy.orm import selectinload
from schemas import UserCreate, AgentCreate, S3ConfigCreate, BackupTaskCreate
from utils import verify_password, get_password_hash, create_access_token, verify_token
from config import settings

router = APIRouter()
templates = Jinja2Templates(directory="templates")


async def get_current_user_web(
    request: Request,
    token: Optional[str] = Cookie(None),
    db: AsyncSession = Depends(get_db)
):
    """Получает текущего пользователя из cookie"""
    if token:
        payload = verify_token(token)
        if payload:
            username = payload.get("sub")
            if username:
                result = await db.execute(select(User).where(User.username == username))
                user = result.scalar_one_or_none()
                if user:
                    return user
    return None


@router.get("/", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: Optional[User] = Depends(get_current_user_web)
):
    """Главная страница"""
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    
    # Получаем статистику
    result_agents = await db.execute(select(Agent))
    agents = result_agents.scalars().all()
    agents_count = len(agents)
    
    result_tasks = await db.execute(select(BackupTask))
    tasks = result_tasks.scalars().all()
    tasks_count = len(tasks)
    
    # Активные задачи
    active_tasks = len([t for t in tasks if t.is_active])
    
    # Последние выполнения
    result_history = await db.execute(
        select(BackupHistory)
        .order_by(BackupHistory.started_at.desc())
        .limit(10)
    )
    recent_history = result_history.scalars().all()
    
    # Получаем информацию о бэкапах и проблемах
    result_backups = await db.execute(
        select(AgentBackupInfo)
        .order_by(AgentBackupInfo.backup_date.desc())
        .limit(50)
    )
    all_backups = result_backups.scalars().all()
    
    # Проверяем успешность бэкапов
    failed_backups = [b for b in all_backups if b.status == "error"]
    has_backup_issues = len(failed_backups) > 0
    
    # Получаем информацию о дисках агентов
    result_agents_status = await db.execute(
        select(AgentStatus).options(selectinload(AgentStatus.agent))
    )
    agents_status = result_agents_status.scalars().all()
    
    disk_warnings = []
    for status in agents_status:
        if status.agent and status.disk_total_gb and status.disk_free_gb:
            free_percent = (status.disk_free_gb / status.disk_total_gb) * 100
            if free_percent < 10:
                disk_warnings.append({
                    "agent": status.agent.name,
                    "free_percent": free_percent,
                    "free_gb": status.disk_free_gb
                })
    
    # Получаем информацию о S3
    result_s3 = await db.execute(select(S3Config))
    s3_configs = result_s3.scalars().all()
    s3_info = []
    for s3 in s3_configs:
        s3_info.append({
            "name": s3.name,
            "used_space_gb": s3.used_space_gb,
            "free_space_gb": s3.free_space_gb,
            "total_space_gb": s3.total_space_gb,
            "last_check": s3.last_check
        })
    
    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "user": user,
        "app_name": settings.app_name,
        "agents_count": agents_count,
        "tasks_count": tasks_count,
        "active_tasks": active_tasks,
        "recent_history": recent_history,
        "has_backup_issues": has_backup_issues,
        "failed_backups": failed_backups[:5],  # Показываем только последние 5
        "disk_warnings": disk_warnings,
        "s3_info": s3_info
    })


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    """Страница входа"""
    return templates.TemplateResponse("login.html", {
        "request": request,
        "app_name": settings.app_name
    })


@router.post("/login")
async def login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: AsyncSession = Depends(get_db)
):
    """Обработка входа"""
    result = await db.execute(select(User).where(User.username == username))
    user = result.scalar_one_or_none()
    
    if not user or not verify_password(password, user.password_hash):
        return templates.TemplateResponse("login.html", {
            "request": request,
            "app_name": settings.app_name,
            "error": "Неверное имя пользователя или пароль"
        })
    
    # Создаем токен
    token = create_access_token(data={"sub": user.username})
    
    response = RedirectResponse(url="/", status_code=302)
    response.set_cookie(key="token", value=token, httponly=True, max_age=3600*24)
    return response


@router.get("/logout")
async def logout():
    """Выход"""
    response = RedirectResponse(url="/login", status_code=302)
    response.delete_cookie(key="token")
    return response


@router.get("/agents", response_class=HTMLResponse)
async def agents_page(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: Optional[User] = Depends(get_current_user_web)
):
    """Страница управления агентами"""
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    
    result = await db.execute(select(Agent))
    agents = result.scalars().all()
    
    # Получаем статусы агентов
    agents_with_status = []
    for agent in agents:
        result_status = await db.execute(
            select(AgentStatus).where(AgentStatus.agent_id == agent.id)
        )
        status = result_status.scalar_one_or_none()
        agents_with_status.append({
            "agent": agent,
            "status": status
        })
    
    return templates.TemplateResponse("agents.html", {
        "request": request,
        "user": user,
        "app_name": settings.app_name,
        "agents": agents_with_status
    })


@router.post("/agents/add")
async def add_agent(
    request: Request,
    name: str = Form(...),
    ip_address: str = Form(...),
    port: int = Form(11540),
    hostname: Optional[str] = Form(None),
    db: AsyncSession = Depends(get_db),
    user: Optional[User] = Depends(get_current_user_web)
):
    """Добавление агента"""
    if not user or not user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    
    agent = AgentCreate(
        name=name,
        ip_address=ip_address,
        port=port,
        hostname=hostname
    )
    
    # Проверяем доступность
    from agent_client import AgentClient
    client = AgentClient(ip_address, port)
    if not await client.ping():
        return templates.TemplateResponse("agents.html", {
            "request": request,
            "user": user,
            "app_name": settings.app_name,
            "error": "Агент недоступен"
        })
    
    db_agent = Agent(**agent.dict())
    db.add(db_agent)
    await db.commit()
    await db.refresh(db_agent)
    
    # Создаем статус
    agent_status = AgentStatus(agent_id=db_agent.id)
    db.add(agent_status)
    await db.commit()
    
    return RedirectResponse(url="/agents", status_code=302)


@router.get("/tasks", response_class=HTMLResponse)
async def tasks_page(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: Optional[User] = Depends(get_current_user_web)
):
    """Страница управления задачами"""
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    
    from sqlalchemy.orm import selectinload
    result_tasks = await db.execute(
        select(BackupTask).options(selectinload(BackupTask.agent))
    )
    tasks = result_tasks.scalars().all()
    
    result_agents = await db.execute(select(Agent))
    agents = result_agents.scalars().all()
    
    result_s3 = await db.execute(select(S3Config))
    s3_configs = result_s3.scalars().all()
    
    return templates.TemplateResponse("tasks.html", {
        "request": request,
        "user": user,
        "app_name": settings.app_name,
        "tasks": tasks,
        "agents": agents,
        "s3_configs": s3_configs
    })


@router.post("/tasks/add")
async def add_task(
    request: Request,
    name: str = Form(...),
    agent_id: int = Form(...),
    s3_config_id: int = Form(...),
    source_path: str = Form(...),
    schedule_cron: Optional[str] = Form(None),  # Может быть скрытым полем
    schedule_type: Optional[str] = Form(None),
    schedule_hour: Optional[int] = Form(None),
    schedule_minute: Optional[int] = Form(None),
    schedule_minute_hourly: Optional[int] = Form(None),
    schedule_day_of_week: Optional[int] = Form(None),
    create_archive: bool = Form(True),
    archive_format: str = Form("tar.gz"),
    is_docker_compose: bool = Form(False),
    docker_compose_path: Optional[str] = Form(None),
    cleanup_enabled: bool = Form(False),
    cleanup_days: int = Form(30),
    db: AsyncSession = Depends(get_db),
    user: Optional[User] = Depends(get_current_user_web)
):
    """Добавление задачи"""
    if not user or not user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    
    # Конвертируем человекочитаемое расписание в cron, если нужно
    final_schedule_cron = schedule_cron
    if schedule_type:
        from cron_converter import convert_to_cron
        try:
            if schedule_type == "hourly":
                final_schedule_cron = convert_to_cron(
                    schedule_type=schedule_type,
                    minute=schedule_minute_hourly
                )
            elif schedule_type == "weekly":
                final_schedule_cron = convert_to_cron(
                    schedule_type=schedule_type,
                    hour=schedule_hour,
                    minute=schedule_minute,
                    day_of_week=schedule_day_of_week
                )
            elif schedule_type == "daily":
                final_schedule_cron = convert_to_cron(
                    schedule_type=schedule_type,
                    hour=schedule_hour,
                    minute=schedule_minute
                )
            elif schedule_type == "minutely":
                final_schedule_cron = convert_to_cron(schedule_type=schedule_type)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Invalid schedule parameters: {str(e)}")
    
    if not final_schedule_cron:
        raise HTTPException(status_code=400, detail="Schedule cron is required")
    
    task = BackupTaskCreate(
        name=name,
        agent_id=agent_id,
        s3_config_id=s3_config_id,
        source_path=source_path,
        schedule_cron=final_schedule_cron,
        create_archive=create_archive,
        archive_format=archive_format,
        is_docker_compose=is_docker_compose,
        docker_compose_path=docker_compose_path,
        cleanup_enabled=cleanup_enabled,
        cleanup_days=cleanup_days
    )
    
    # Получаем агента и S3
    result_agent = await db.execute(select(Agent).where(Agent.id == agent_id))
    agent = result_agent.scalar_one_or_none()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    
    result_s3 = await db.execute(select(S3Config).where(S3Config.id == s3_config_id))
    s3_config = result_s3.scalar_one_or_none()
    if not s3_config:
        raise HTTPException(status_code=404, detail="S3 config not found")
    
    # Получаем файловую систему
    from agent_client import AgentClient
    from schemas import AgentTaskConfig
    client = AgentClient(agent.ip_address, agent.port)
    
    # Загружаем списки для отображения ошибки
    result_tasks = await db.execute(select(BackupTask))
    tasks = result_tasks.scalars().all()
    result_agents = await db.execute(select(Agent))
    agents = result_agents.scalars().all()
    result_s3 = await db.execute(select(S3Config))
    s3_configs = result_s3.scalars().all()
    
    # Проверяем доступность агента
    if not await client.ping():
        return templates.TemplateResponse("tasks.html", {
            "request": request,
            "user": user,
            "app_name": settings.app_name,
            "tasks": tasks,
            "agents": agents,
            "s3_configs": s3_configs,
            "error": "Агент недоступен. Проверьте подключение."
        })
    
    filesystem_info = await client.get_filesystem_info(source_path)
    
    db_task = BackupTask(**task.dict())
    if filesystem_info:
        db_task.filesystem = filesystem_info.filesystem
    
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
    
    if not await client.send_task_config(task_config):
        # Задача создана в БД, но не отправлена агенту
        # Это не критично, можно отправить позже
        pass
    
    return RedirectResponse(url="/tasks", status_code=302)


@router.get("/s3-configs", response_class=HTMLResponse)
async def s3_configs_page(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: Optional[User] = Depends(get_current_user_web)
):
    """Страница управления S3 конфигурациями"""
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    
    result = await db.execute(select(S3Config))
    configs = result.scalars().all()
    
    # Проверяем подключение к каждому S3 хранилищу
    from s3_client import S3Client
    configs_with_status = []
    for config in configs:
        try:
            s3_client = S3Client(
                config.endpoint,
                config.access_key,
                config.secret_key,
                config.bucket_name,
                config.region,
                config.use_ssl
            )
            bucket_info = s3_client.get_bucket_info()
            if bucket_info:
                config.used_space_gb = bucket_info.get("used_space_gb")
                config.total_space_gb = bucket_info.get("total_space_gb")
                config.free_space_gb = bucket_info.get("free_space_gb")
                config.connection_error = None
            else:
                config.connection_error = "Не удалось получить информацию о bucket"
        except Exception as e:
            config.connection_error = str(e)
            config.used_space_gb = None
            config.total_space_gb = None
            config.free_space_gb = None
        configs_with_status.append(config)
    
    return templates.TemplateResponse("s3_configs.html", {
        "request": request,
        "user": user,
        "app_name": settings.app_name,
        "s3_configs": configs_with_status
    })


@router.post("/s3-configs/add")
async def add_s3_config(
    request: Request,
    name: str = Form(...),
    endpoint: str = Form(...),
    access_key: str = Form(...),
    secret_key: str = Form(...),
    bucket_name: str = Form(...),
    region: str = Form("us-east-1"),
    use_ssl: bool = Form(False),
    db: AsyncSession = Depends(get_db),
    user: Optional[User] = Depends(get_current_user_web)
):
    """Добавление S3 конфигурации"""
    if not user or not user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    
    s3_config = S3ConfigCreate(
        name=name,
        endpoint=endpoint,
        access_key=access_key,
        secret_key=secret_key,
        bucket_name=bucket_name,
        region=region,
        use_ssl=use_ssl
    )
    
    db_config = S3Config(**s3_config.dict())
    db.add(db_config)
    await db.commit()
    
    return RedirectResponse(url="/s3-configs", status_code=302)


@router.get("/postgres-backups", response_class=HTMLResponse)
async def postgres_backups_page(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: Optional[User] = Depends(get_current_user_web)
):
    """Страница управления PostgreSQL бэкапами"""
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    
    result_tasks = await db.execute(select(PostgresBackupTask))
    tasks = result_tasks.scalars().all()
    
    result_s3 = await db.execute(select(S3Config))
    s3_configs = result_s3.scalars().all()
    
    return templates.TemplateResponse("postgres_backups.html", {
        "request": request,
        "user": user,
        "app_name": settings.app_name,
        "tasks": tasks,
        "s3_configs": s3_configs
    })


@router.post("/postgres-backups/add")
async def add_postgres_backup(
    request: Request,
    name: str = Form(...),
    s3_config_id: int = Form(...),
    host: str = Form(...),
    port: int = Form(5432),
    username: str = Form(...),
    password: str = Form(...),
    database: str = Form(...),
    backup_format: str = Form("custom"),
    compression_level: int = Form(6),
    include_schema: bool = Form(True),
    include_data: bool = Form(True),
    include_roles: bool = Form(False),
    include_tablespaces: bool = Form(False),
    schedule_cron: Optional[str] = Form(None),  # Может быть скрытым полем
    schedule_type: Optional[str] = Form(None),
    schedule_hour: Optional[int] = Form(None),
    schedule_minute: Optional[int] = Form(None),
    schedule_minute_hourly: Optional[int] = Form(None),
    schedule_day_of_week: Optional[int] = Form(None),
    schedule_enabled: bool = Form(True),
    cleanup_enabled: bool = Form(True),
    cleanup_days: int = Form(30),
    db: AsyncSession = Depends(get_db),
    user: Optional[User] = Depends(get_current_user_web)
):
    """Добавление задачи PostgreSQL бэкапа"""
    if not user or not user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    
    # Конвертируем человекочитаемое расписание в cron, если нужно
    final_schedule_cron = schedule_cron
    if schedule_type:
        from cron_converter import convert_to_cron
        try:
            if schedule_type == "hourly":
                final_schedule_cron = convert_to_cron(
                    schedule_type=schedule_type,
                    minute=schedule_minute_hourly
                )
            elif schedule_type == "weekly":
                final_schedule_cron = convert_to_cron(
                    schedule_type=schedule_type,
                    hour=schedule_hour,
                    minute=schedule_minute,
                    day_of_week=schedule_day_of_week
                )
            elif schedule_type == "daily":
                final_schedule_cron = convert_to_cron(
                    schedule_type=schedule_type,
                    hour=schedule_hour,
                    minute=schedule_minute
                )
            elif schedule_type == "minutely":
                final_schedule_cron = convert_to_cron(schedule_type=schedule_type)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Invalid schedule parameters: {str(e)}")
    
    if not final_schedule_cron:
        raise HTTPException(status_code=400, detail="Schedule cron is required")
    
    # Проверяем S3 конфигурацию
    result_s3 = await db.execute(select(S3Config).where(S3Config.id == s3_config_id))
    s3_config = result_s3.scalar_one_or_none()
    if not s3_config:
        raise HTTPException(status_code=404, detail="S3 config not found")
    
    # Шифруем пароль
    from postgres_backup import encrypt_password
    encrypted_password = encrypt_password(password)
    
    # Создаем задачу
    db_task = PostgresBackupTask(
        name=name,
        s3_config_id=s3_config_id,
        host=host,
        port=port,
        username=username,
        password=encrypted_password,
        database=database,
        backup_format=backup_format,
        compression_level=compression_level,
        include_schema=include_schema,
        include_data=include_data,
        include_roles=include_roles,
        include_tablespaces=include_tablespaces,
        schedule_cron=final_schedule_cron,
        schedule_enabled=schedule_enabled,
        cleanup_enabled=cleanup_enabled,
        cleanup_days=cleanup_days
    )
    db.add(db_task)
    await db.commit()
    await db.refresh(db_task)
    
    return RedirectResponse(url="/postgres-backups", status_code=302)


@router.get("/restore", response_class=HTMLResponse)
async def restore_page(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: Optional[User] = Depends(get_current_user_web)
):
    """Страница восстановления резервных копий"""
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    
    # Получаем PostgreSQL задачи
    result_pg_tasks = await db.execute(select(PostgresBackupTask).where(PostgresBackupTask.is_active == True))
    pg_tasks = result_pg_tasks.scalars().all()
    
    # Получаем агентов
    result_agents = await db.execute(select(Agent).where(Agent.is_active == True))
    agents = result_agents.scalars().all()
    
    # Получаем S3 конфигурации
    result_s3 = await db.execute(select(S3Config))
    s3_configs = result_s3.scalars().all()
    
    return templates.TemplateResponse("restore.html", {
        "request": request,
        "user": user,
        "app_name": settings.app_name,
        "pg_tasks": pg_tasks,
        "agents": agents,
        "s3_configs": s3_configs
    })


@router.get("/postgres-backups/{task_id}/history", response_class=HTMLResponse)
async def postgres_backup_history_page(
    request: Request,
    task_id: int,
    db: AsyncSession = Depends(get_db),
    user: Optional[User] = Depends(get_current_user_web)
):
    """Страница истории PostgreSQL бэкапов"""
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    
    result_task = await db.execute(select(PostgresBackupTask).where(PostgresBackupTask.id == task_id))
    task = result_task.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    
    result_history = await db.execute(
        select(PostgresBackupHistory)
        .where(PostgresBackupHistory.task_id == task_id)
        .order_by(PostgresBackupHistory.started_at.desc())
        .limit(100)
    )
    history = result_history.scalars().all()
    
    return templates.TemplateResponse("postgres_backup_history.html", {
        "request": request,
        "user": user,
        "app_name": settings.app_name,
        "task": task,
        "history": history
    })


@router.get("/settings", response_class=HTMLResponse)
async def settings_page(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: Optional[User] = Depends(get_current_user_web)
):
    """Страница настроек"""
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    
    result = await db.execute(select(Settings))
    settings_obj = result.scalar_one_or_none()
    
    if not settings_obj:
        # Создаем настройки по умолчанию
        settings_obj = Settings(
            mattermost_enabled=False,
            mattermost_daily_report=False,
            mattermost_report_time="09:00",
            agent_poll_interval=60,
            s3_check_interval=86400
        )
        db.add(settings_obj)
        await db.commit()
    
    return templates.TemplateResponse("settings.html", {
        "request": request,
        "user": user,
        "app_name": settings.app_name,
        "settings": settings_obj
    })


@router.post("/settings/save")
async def save_settings(
    request: Request,
    mattermost_enabled: bool = Form(False),
    mattermost_webhook_url: Optional[str] = Form(None),
    mattermost_daily_report: bool = Form(False),
    mattermost_report_time: str = Form("09:00"),
    agent_poll_interval: int = Form(60),
    s3_check_interval: int = Form(86400),
    db: AsyncSession = Depends(get_db),
    user: Optional[User] = Depends(get_current_user_web)
):
    """Сохранение настроек"""
    if not user or not user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    
    result = await db.execute(select(Settings))
    settings_obj = result.scalar_one_or_none()
    
    if not settings_obj:
        settings_obj = Settings()
        db.add(settings_obj)
    
    settings_obj.mattermost_enabled = mattermost_enabled
    settings_obj.mattermost_webhook_url = mattermost_webhook_url
    settings_obj.mattermost_daily_report = mattermost_daily_report
    settings_obj.mattermost_report_time = mattermost_report_time
    settings_obj.agent_poll_interval = agent_poll_interval
    settings_obj.s3_check_interval = s3_check_interval
    
    await db.commit()
    
    return RedirectResponse(url="/settings", status_code=302)


@router.get("/about", response_class=HTMLResponse)
async def about_page(
    request: Request,
    user: Optional[User] = Depends(get_current_user_web)
):
    """Страница о программе"""
    return templates.TemplateResponse("about.html", {
        "request": request,
        "user": user,
        "app_name": settings.app_name
    })

