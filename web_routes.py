"""
Веб-маршруты для сервера резервного копирования
"""
from fastapi import APIRouter, Request, Depends, HTTPException, Form, Cookie
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import Optional, List
from datetime import datetime, timedelta

from database import get_db
from models import User, Agent, AgentStatus, S3Config, StorageConfig, BackupTask, BackupHistory, AgentBackupInfo, Settings, PostgresBackupTask, PostgresBackupHistory, Report, ReportHistory
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
    
    # Получаем информацию о S3 и Storage
    result_s3 = await db.execute(select(S3Config))
    s3_configs = result_s3.scalars().all()
    
    result_storage = await db.execute(select(StorageConfig))
    storage_configs = result_storage.scalars().all()
    
    # Статистика хранилища (объединяем S3 и Storage)
    total_storage_gb = 0
    used_storage_gb = 0
    free_storage_gb = 0
    s3_info = []
    
    for s3 in s3_configs:
        if s3.total_space_gb:
            total_storage_gb += s3.total_space_gb
            used_storage_gb += (s3.used_space_gb or 0)
            free_storage_gb += (s3.free_space_gb or 0)
        s3_info.append({
            "name": s3.name,
            "used_space_gb": s3.used_space_gb,
            "free_space_gb": s3.free_space_gb,
            "total_space_gb": s3.total_space_gb,
            "last_check": s3.last_check
        })
    
    for storage in storage_configs:
        if storage.total_space_gb:
            total_storage_gb += storage.total_space_gb
            used_storage_gb += (storage.used_space_gb or 0)
            free_storage_gb += (storage.free_space_gb or 0)
    
    # Вычисляем "другое" как разницу между общим и использованным+свободным
    other_storage_gb = max(0, total_storage_gb - used_storage_gb - free_storage_gb)
    
    # Статистика агентов
    online_agents = len([a for a in agents if a.is_active])
    offline_agents = agents_count - online_agents
    
    # Статистика задач
    result_pg_tasks = await db.execute(select(PostgresBackupTask))
    pg_tasks = result_pg_tasks.scalars().all()
    all_tasks_count = tasks_count + len(pg_tasks)
    
    # Статусы задач
    success_tasks = len([t for t in tasks if t.last_status == "success"])
    error_tasks = len([t for t in tasks if t.last_status == "error"])
    running_tasks = len([t for t in tasks if t.last_status == "running"])
    
    success_pg_tasks = len([t for t in pg_tasks if t.last_status == "success"])
    error_pg_tasks = len([t for t in pg_tasks if t.last_status == "error"])
    running_pg_tasks = len([t for t in pg_tasks if t.last_status == "running"])
    
    total_success = success_tasks + success_pg_tasks
    total_error = error_tasks + error_pg_tasks
    total_running = running_tasks + running_pg_tasks
    
    # Последние оповещения (ошибки и предупреждения)
    alerts = []
    for task in tasks:
        if task.last_error:
            alerts.append({
                "type": "error",
                "message": f"Задача '{task.name}': {task.last_error[:50]}...",
                "time": task.last_run,
                "task_id": task.id
            })
    for task in pg_tasks:
        if task.last_error:
            alerts.append({
                "type": "error",
                "message": f"PostgreSQL задача '{task.name}': {task.last_error[:50]}...",
                "time": task.last_run,
                "task_id": task.id
            })
    
    # Сортируем по времени
    alerts.sort(key=lambda x: x["time"] if x["time"] else datetime.min, reverse=True)
    latest_alerts = alerts[:5]
    
    # График активности (действия по дням за последние 7 дней)
    activity_by_day = {}
    for i in range(7):
        date = (datetime.now() - timedelta(days=i)).date()
        activity_by_day[date.strftime("%d %b")] = 0
    
    # Подсчитываем действия по дням
    all_history = []
    result_all_history = await db.execute(
        select(BackupHistory)
        .order_by(BackupHistory.started_at.desc())
        .limit(100)
    )
    all_history.extend(result_all_history.scalars().all())
    
    result_pg_history = await db.execute(
        select(PostgresBackupHistory)
        .order_by(PostgresBackupHistory.started_at.desc())
        .limit(100)
    )
    all_history.extend(result_pg_history.scalars().all())
    
    for history in all_history:
        if history.started_at:
            date_key = history.started_at.date().strftime("%d %b")
            if date_key in activity_by_day:
                activity_by_day[date_key] += 1
    
    # Все действия для таблицы (объединяем BackupHistory и PostgresBackupHistory)
    all_actions = []
    for history in recent_history:
        result_task = await db.execute(select(BackupTask).where(BackupTask.id == history.task_id))
        task = result_task.scalar_one_or_none()
        all_actions.append({
            "result": history.status,
            "action_name": f"Backup: {task.name if task else f'Task #{history.task_id}'}",
            "plan_name": task.name if task else "n/a",
            "start_time": history.started_at,
            "end_time": history.finished_at,
            "duration": history.duration_seconds,
            "initiated_by": "-"
        })
    
    # Добавляем PostgreSQL бэкапы
    result_pg_history_recent = await db.execute(
        select(PostgresBackupHistory)
        .order_by(PostgresBackupHistory.started_at.desc())
        .limit(10)
    )
    pg_history_recent = result_pg_history_recent.scalars().all()
    for history in pg_history_recent:
        result_task = await db.execute(select(PostgresBackupTask).where(PostgresBackupTask.id == history.task_id))
        task = result_task.scalar_one_or_none()
        all_actions.append({
            "result": history.status,
            "action_name": f"PostgreSQL Backup: {task.name if task else f'Task #{history.task_id}'}",
            "plan_name": task.name if task else "n/a",
            "start_time": history.started_at,
            "end_time": history.finished_at,
            "duration": history.duration_seconds,
            "initiated_by": "-"
        })
    
    # Сортируем по времени начала
    all_actions.sort(key=lambda x: x["start_time"] if x["start_time"] else datetime.min, reverse=True)
    all_actions = all_actions[:10]
    
    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "user": user,
        "app_name": settings.app_name,
        "agents_count": agents_count,
        "tasks_count": tasks_count,
        "active_tasks": active_tasks,
        "recent_history": recent_history,
        "has_backup_issues": has_backup_issues,
        "failed_backups": failed_backups[:5],
        "disk_warnings": disk_warnings,
        "s3_info": s3_info,
        # Новые данные для графиков
        "storage_stats": {
            "total_gb": total_storage_gb,
            "used_gb": used_storage_gb,
            "free_gb": free_storage_gb,
            "other_gb": other_storage_gb
        },
        "agents_stats": {
            "total": agents_count,
            "online": online_agents,
            "offline": offline_agents
        },
        "tasks_stats": {
            "total": all_tasks_count,
            "success": total_success,
            "error": total_error,
            "running": total_running,
            "ok": total_success
        },
        "latest_alerts": latest_alerts,
        "activity_by_day": activity_by_day,
        "all_actions": all_actions
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
    
    # Получаем статусы агентов и информацию о дисках
    agents_with_status = []
    for agent in agents:
        result_status = await db.execute(
            select(AgentStatus).where(AgentStatus.agent_id == agent.id)
        )
        status = result_status.scalar_one_or_none()
        
        # Получаем информацию о дисках от агента
        disks = []
        from agent_client import AgentClient
        client = AgentClient(agent.ip_address, agent.port)
        disks_data = await client.get_all_disks()
        if disks_data:
            disks = disks_data
        
        agents_with_status.append({
            "agent": agent,
            "status": status,
            "disks": disks
        })
    
    # Получаем доступные хранилища (только проверенные и без ошибок)
    result_storage = await db.execute(
        select(StorageConfig)
        .where(StorageConfig.connection_error.is_(None))
        .where(StorageConfig.last_check.isnot(None))
    )
    available_storage = result_storage.scalars().all()
    
    return templates.TemplateResponse("agents.html", {
        "request": request,
        "user": user,
        "app_name": settings.app_name,
        "agents": agents_with_status,
        "available_storage": available_storage
    })


@router.post("/agents/add")
async def add_agent(
    request: Request,
    name: str = Form(...),
    ip_address: str = Form(...),
    port: int = Form(11540),
    hostname: Optional[str] = Form(None),
    storage_config_id: Optional[int] = Form(None),
    db: AsyncSession = Depends(get_db),
    user: Optional[User] = Depends(get_current_user_web)
):
    """Добавление агента"""
    if not user or not user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    
    # Проверяем доступность хранилища, если указано
    if storage_config_id:
        result_storage = await db.execute(
            select(StorageConfig).where(StorageConfig.id == storage_config_id)
        )
        storage_config = result_storage.scalar_one_or_none()
        if not storage_config:
            raise HTTPException(status_code=404, detail="Storage config not found")
        if storage_config.connection_error:
            raise HTTPException(status_code=400, detail=f"Storage config has connection error: {storage_config.connection_error}")
    
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
    if storage_config_id:
        db_agent.storage_config_id = storage_config_id
    db.add(db_agent)
    await db.commit()
    await db.refresh(db_agent)
    
    # Создаем статус
    agent_status = AgentStatus(agent_id=db_agent.id)
    db.add(agent_status)
    await db.commit()
    
    # Отправляем конфигурацию хранилища агенту, если указано
    if storage_config_id and storage_config:
        import json
        storage_config_json = json.dumps({
            "storage_type": storage_config.storage_type,
            "config_data": storage_config.config_data
        })
        # TODO: Отправить конфигурацию хранилища агенту через API
    
    return RedirectResponse(url="/agents", status_code=302)


@router.post("/agents/{agent_id}/edit")
async def edit_agent(
    request: Request,
    agent_id: int,
    name: str = Form(...),
    ip_address: str = Form(...),
    port: int = Form(11540),
    hostname: Optional[str] = Form(None),
    is_active: bool = Form(True),
    db: AsyncSession = Depends(get_db),
    user: Optional[User] = Depends(get_current_user_web)
):
    """Редактирование агента"""
    if not user or not user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    
    result = await db.execute(select(Agent).where(Agent.id == agent_id))
    agent = result.scalar_one_or_none()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    
    # Обновляем поля
    agent.name = name
    agent.ip_address = ip_address
    agent.port = port
    agent.hostname = hostname
    agent.is_active = is_active
    
    # Проверяем доступность, если изменился IP или порт
    from agent_client import AgentClient
    client = AgentClient(ip_address, port)
    if not await client.ping():
        return templates.TemplateResponse("agents.html", {
            "request": request,
            "user": user,
            "app_name": settings.app_name,
            "error": "Агент недоступен с новыми настройками"
        })
    
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


@router.get("/storage-configs", response_class=HTMLResponse)
async def storage_configs_page(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: Optional[User] = Depends(get_current_user_web)
):
    """Страница управления хранилищами"""
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    
    # Получаем все хранилища (StorageConfig)
    result_storage = await db.execute(select(StorageConfig).order_by(StorageConfig.created_at.desc()))
    storage_configs = result_storage.scalars().all()
    
    # Получаем старые S3 конфигурации для обратной совместимости
    result_s3 = await db.execute(select(S3Config).order_by(S3Config.id.desc()))
    s3_configs = result_s3.scalars().all()
    
    # Получаем список агентов для выбора локального хранилища
    result_agents = await db.execute(select(Agent))
    agents = result_agents.scalars().all()
    
    # Создаем словарь агентов для быстрого поиска
    agents_dict = {agent.id: agent for agent in agents}
    
    # Обогащаем хранилища информацией об агентах для локальных хранилищ
    storage_configs_list = []
    for config in storage_configs:
        config_dict = {
            "id": config.id,
            "name": config.name,
            "storage_type": config.storage_type,
            "config_data": config.config_data,
            "last_check": config.last_check,
            "free_space_gb": config.free_space_gb,
            "total_space_gb": config.total_space_gb,
            "used_space_gb": config.used_space_gb,
            "connection_error": config.connection_error,
            "created_at": config.created_at,
            "updated_at": config.updated_at
        }
        # Для локального хранилища добавляем информацию об агенте
        if config.storage_type == "local" and config.config_data.get("agent_id"):
            agent_id = config.config_data.get("agent_id")
            if agent_id in agents_dict:
                config_dict["agent"] = agents_dict[agent_id]
        storage_configs_list.append(config_dict)
    
    return templates.TemplateResponse("storage_configs.html", {
        "request": request,
        "user": user,
        "app_name": settings.app_name,
        "storage_configs": storage_configs_list,
        "s3_configs": s3_configs,  # Для обратной совместимости
        "agents": agents,
        "agents_dict": agents_dict
    })


@router.get("/s3-configs", response_class=HTMLResponse)
async def s3_configs_page_redirect(
    request: Request,
    user: Optional[User] = Depends(get_current_user_web)
):
    """Редирект со старой страницы S3 на новую страницу хранилищ"""
    return RedirectResponse(url="/storage-configs", status_code=302)


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
    
    # Получаем доступные хранилища (только проверенные и без ошибок)
    result_storage = await db.execute(
        select(StorageConfig)
        .where(StorageConfig.connection_error.is_(None))
        .where(StorageConfig.last_check.isnot(None))
    )
    available_storage = result_storage.scalars().all()
    
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
    storage_config_id: Optional[int] = Form(None),
    s3_config_id: Optional[int] = Form(None),  # Для обратной совместимости
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
        storage_config_id=storage_config_id,
        s3_config_id=s3_config_id if not storage_config_id else None,  # Для обратной совместимости
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


@router.post("/postgres-backups/{task_id}/edit")
async def edit_postgres_backup(
    request: Request,
    task_id: int,
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
    schedule_cron: Optional[str] = Form(None),
    schedule_type: Optional[str] = Form(None),
    schedule_hour: Optional[int] = Form(None),
    schedule_minute: Optional[int] = Form(None),
    schedule_minute_hourly: Optional[int] = Form(None),
    schedule_day_of_week: Optional[int] = Form(None),
    schedule_enabled: bool = Form(True),
    cleanup_enabled: bool = Form(True),
    cleanup_days: int = Form(30),
    is_active: bool = Form(True),
    db: AsyncSession = Depends(get_db),
    user: Optional[User] = Depends(get_current_user_web)
):
    """Редактирование задачи PostgreSQL бэкапа"""
    if not user or not user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    
    result = await db.execute(select(PostgresBackupTask).where(PostgresBackupTask.id == task_id))
    task = result.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail="PostgreSQL backup task not found")
    
    # Конвертируем расписание в cron, если нужно
    final_schedule_cron = schedule_cron or task.schedule_cron
    if schedule_type:
        from cron_converter import convert_to_cron
        try:
            if schedule_type == "hourly":
                final_schedule_cron = convert_to_cron(schedule_type=schedule_type, minute=schedule_minute_hourly)
            elif schedule_type == "weekly":
                final_schedule_cron = convert_to_cron(schedule_type=schedule_type, hour=schedule_hour, minute=schedule_minute, day_of_week=schedule_day_of_week)
            elif schedule_type == "daily":
                final_schedule_cron = convert_to_cron(schedule_type=schedule_type, hour=schedule_hour, minute=schedule_minute)
            elif schedule_type == "minutely":
                final_schedule_cron = convert_to_cron(schedule_type=schedule_type)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Invalid schedule parameters: {str(e)}")
    
    # Проверяем S3 конфигурацию
    result_s3 = await db.execute(select(S3Config).where(S3Config.id == s3_config_id))
    s3_config = result_s3.scalar_one_or_none()
    if not s3_config:
        raise HTTPException(status_code=404, detail="S3 config not found")
    
    # Шифруем пароль, если он изменился
    from postgres_backup import encrypt_password
    if password and password != "***":
        encrypted_password = encrypt_password(password)
    else:
        encrypted_password = task.password  # Оставляем старый пароль
    
    # Обновляем поля
    task.name = name
    task.s3_config_id = s3_config_id
    task.host = host
    task.port = port
    task.username = username
    task.password = encrypted_password
    task.database = database
    task.backup_format = backup_format
    task.compression_level = compression_level
    task.include_schema = include_schema
    task.include_data = include_data
    task.include_roles = include_roles
    task.include_tablespaces = include_tablespaces
    task.schedule_cron = final_schedule_cron
    task.schedule_enabled = schedule_enabled
    task.cleanup_enabled = cleanup_enabled
    task.cleanup_days = cleanup_days
    task.is_active = is_active
    
    await db.commit()
    return RedirectResponse(url="/postgres-backups", status_code=302)


@router.post("/s3-configs/{config_id}/edit")
async def edit_s3_config(
    request: Request,
    config_id: int,
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
    """Редактирование S3 конфигурации"""
    if not user or not user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    
    result = await db.execute(select(S3Config).where(S3Config.id == config_id))
    config = result.scalar_one_or_none()
    if not config:
        raise HTTPException(status_code=404, detail="S3 config not found")
    
    # Обновляем поля
    config.name = name
    config.endpoint = endpoint
    config.access_key = access_key
    config.secret_key = secret_key
    config.bucket_name = bucket_name
    config.region = region
    config.use_ssl = use_ssl
    
    await db.commit()
    return RedirectResponse(url="/storage-configs", status_code=302)


@router.post("/storage-configs/{config_id}/edit")
async def edit_storage_config(
    request: Request,
    config_id: int,
    name: str = Form(...),
    storage_type: str = Form(...),
    # S3 параметры
    endpoint: Optional[str] = Form(None),
    access_key: Optional[str] = Form(None),
    secret_key: Optional[str] = Form(None),
    bucket_name: Optional[str] = Form(None),
    region: Optional[str] = Form("us-east-1"),
    use_ssl: bool = Form(False),
    # SFTP параметры
    sftp_host: Optional[str] = Form(None),
    sftp_port: Optional[int] = Form(22),
    sftp_username: Optional[str] = Form(None),
    sftp_password: Optional[str] = Form(None),
    sftp_base_path: Optional[str] = Form(None),
    # NFS параметры
    nfs_server: Optional[str] = Form(None),
    nfs_export_path: Optional[str] = Form(None),
    nfs_mount_point: Optional[str] = Form(None),
    nfs_options: Optional[str] = Form(None),
    # Local параметры
    local_agent_id: Optional[int] = Form(None),
    local_base_path: Optional[str] = Form(None),
    db: AsyncSession = Depends(get_db),
    user: Optional[User] = Depends(get_current_user_web)
):
    """Редактирование хранилища"""
    if not user or not user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    
    result = await db.execute(select(StorageConfig).where(StorageConfig.id == config_id))
    config = result.scalar_one_or_none()
    if not config:
        raise HTTPException(status_code=404, detail="Storage config not found")
    
    import json
    
    # Формируем config_data в зависимости от типа хранилища
    config_data = {}
    
    if storage_type == "s3":
        config_data = {
            "endpoint": endpoint,
            "access_key": access_key,
            "secret_key": secret_key,
            "bucket_name": bucket_name,
            "region": region or "us-east-1",
            "use_ssl": use_ssl
        }
    elif storage_type == "sftp":
        config_data = {
            "host": sftp_host,
            "port": sftp_port or 22,
            "username": sftp_username,
            "password": sftp_password,
            "base_path": sftp_base_path
        }
    elif storage_type == "nfs":
        config_data = {
            "server": nfs_server,
            "export_path": nfs_export_path,
            "mount_point": nfs_mount_point,
            "options": nfs_options or "rw,sync"
        }
    elif storage_type == "local":
        config_data = {
            "agent_id": local_agent_id,
            "base_path": local_base_path
        }
    
    # Обновляем поля
    config.name = name
    config.storage_type = storage_type
    config.config_data = config_data
    
    await db.commit()
    return RedirectResponse(url="/storage-configs", status_code=302)


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


@router.get("/reports", response_class=HTMLResponse)
async def reports_page(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: Optional[User] = Depends(get_current_user_web)
):
    """Страница управления отчетами"""
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    
    result_reports = await db.execute(select(Report).order_by(Report.created_at.desc()))
    reports = result_reports.scalars().all()
    
    result_agents = await db.execute(select(Agent))
    agents = result_agents.scalars().all()
    
    result_pg_tasks = await db.execute(select(PostgresBackupTask))
    pg_tasks = result_pg_tasks.scalars().all()
    
    return templates.TemplateResponse("reports.html", {
        "request": request,
        "user": user,
        "app_name": settings.app_name,
        "reports": reports,
        "agents": agents,
        "pg_tasks": pg_tasks
    })


@router.post("/reports/add")
async def add_report(
    request: Request,
    name: str = Form(...),
    description: Optional[str] = Form(None),
    agent_ids: str = Form(...),  # Получаем как строку, затем парсим
    postgres_task_ids: str = Form(...),  # Получаем как строку, затем парсим
    send_to_mattermost: bool = Form(False),
    enabled: bool = Form(True),
    schedule_type: str = Form(...),
    schedule_hour: Optional[int] = Form(None),
    schedule_minute: Optional[int] = Form(None),
    schedule_day_of_week: Optional[int] = Form(None),
    schedule_hours_interval: Optional[int] = Form(None),
    db: AsyncSession = Depends(get_db),
    user: Optional[User] = Depends(get_current_user_web)
):
    """Добавление отчета"""
    if not user or not user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    
    # Парсим списки из строк
    agent_ids_list = [int(x.strip()) for x in agent_ids.split(',') if x.strip()] if agent_ids else []
    postgres_task_ids_list = [int(x.strip()) for x in postgres_task_ids.split(',') if x.strip()] if postgres_task_ids else []
    
    from report_scheduler import ReportScheduler
    
    report = Report(
        name=name,
        description=description,
        agent_ids=agent_ids_list,
        postgres_task_ids=postgres_task_ids_list,
        send_to_mattermost=send_to_mattermost,
        enabled=enabled,
        schedule_type=schedule_type,
        schedule_hour=schedule_hour,
        schedule_minute=schedule_minute,
        schedule_day_of_week=schedule_day_of_week,
        schedule_hours_interval=schedule_hours_interval
    )
    
    # Вычисляем следующее время отправки
    scheduler = ReportScheduler()
    report.next_send = scheduler.calculate_next_send(report)
    
    db.add(report)
    await db.commit()
    
    return RedirectResponse(url="/reports", status_code=302)


@router.post("/reports/{report_id}/send")
async def send_report_now(
    report_id: int,
    db: AsyncSession = Depends(get_db),
    user: Optional[User] = Depends(get_current_user_web)
):
    """Отправка отчета немедленно"""
    if not user or not user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    
    result = await db.execute(select(Report).where(Report.id == report_id))
    report = result.scalar_one_or_none()
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")
    
    result_settings = await db.execute(select(Settings))
    settings_obj = result_settings.scalar_one_or_none()
    if not settings_obj or not settings_obj.mattermost_enabled or not settings_obj.mattermost_webhook_url:
        raise HTTPException(status_code=400, detail="Mattermost not configured")
    
    from report_scheduler import ReportScheduler
    scheduler = ReportScheduler()
    await scheduler.send_report(report, settings_obj.mattermost_webhook_url, db)
    
    return RedirectResponse(url="/reports", status_code=302)


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

