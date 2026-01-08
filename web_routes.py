"""
Веб-маршруты для сервера резервного копирования
"""
from fastapi import APIRouter, Request, Depends, HTTPException, Form, Cookie, Query, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, FileResponse
import os
import glob
import shutil
from pathlib import Path
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import Optional, List
from datetime import datetime, timedelta

from database import get_db
from models import User, Agent, AgentStatus, S3Config, StorageConfig, BackupTask, BackupHistory, AgentBackupInfo, Settings, PostgresBackupTask, PostgresBackupHistory, Report, ReportHistory
from sqlalchemy.orm import selectinload
from schemas import UserCreate, UserCreateAdmin, AgentCreate, S3ConfigCreate, BackupTaskCreate
from utils import verify_password, get_password_hash, create_access_token, verify_token
from config import settings
import logging

logger = logging.getLogger(__name__)

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
        s3_info.append({
            "name": storage.name,
            "used_space_gb": storage.used_space_gb,
            "free_space_gb": storage.free_space_gb,
            "total_space_gb": storage.total_space_gb,
            "last_check": storage.last_check if hasattr(storage, 'last_check') else None
        })
    
    # Вычисляем размер удачных бэкапов
    # Суммируем размеры успешных бэкапов из истории
    result_success_backups = await db.execute(
        select(BackupHistory)
        .where(BackupHistory.status == "success")
        .where(BackupHistory.archive_size_mb.isnot(None))
    )
    success_backups = result_success_backups.scalars().all()
    total_success_backups_mb = sum(b.archive_size_mb for b in success_backups if b.archive_size_mb)
    
    # Суммируем размеры успешных PostgreSQL бэкапов
    result_success_pg_backups = await db.execute(
        select(PostgresBackupHistory)
        .where(PostgresBackupHistory.status == "success")
        .where(PostgresBackupHistory.dump_size_mb.isnot(None))
    )
    success_pg_backups = result_success_pg_backups.scalars().all()
    total_success_pg_backups_mb = sum(b.dump_size_mb for b in success_pg_backups if b.dump_size_mb)
    
    # Общий размер удачных бэкапов в ГБ
    total_success_backups_gb = (total_success_backups_mb + total_success_pg_backups_mb) / 1024
    
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
    # Показываем ошибки только если после них не было успешного выполнения
    alerts = []
    
    # Для обычных задач
    for task in tasks:
        if task.last_error:
            # Проверяем, был ли успешный запуск после ошибки
            result_history = await db.execute(
                select(BackupHistory)
                .where(BackupHistory.task_id == task.id)
                .order_by(BackupHistory.started_at.desc())
                .limit(1)
            )
            last_history = result_history.scalar_one_or_none()
            
            # Показываем ошибку только если последний статус - ошибка
            if not last_history or last_history.status == "error":
                alerts.append({
                    "type": "error",
                    "message": f"Задача '{task.name}': {task.last_error[:50]}...",
                    "time": task.last_run,
                    "task_id": task.id
                })
    
    # Для PostgreSQL задач
    for task in pg_tasks:
        if task.last_error:
            # Проверяем, был ли успешный запуск после ошибки
            result_history = await db.execute(
                select(PostgresBackupHistory)
                .where(PostgresBackupHistory.task_id == task.id)
                .order_by(PostgresBackupHistory.started_at.desc())
                .limit(1)
            )
            last_history = result_history.scalar_one_or_none()
            
            # Показываем ошибку только если последний статус - ошибка
            if not last_history or last_history.status == "error":
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
            "other_gb": other_storage_gb,
            "success_backups_gb": total_success_backups_gb
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
async def login_page(
    request: Request,
    db: AsyncSession = Depends(get_db)
):
    """Страница входа"""
    result = await db.execute(select(Settings))
    settings_obj = result.scalar_one_or_none()
    
    return templates.TemplateResponse("login.html", {
        "request": request,
        "app_name": settings.app_name,
        "settings": settings_obj
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
    
    # Формируем конфигурацию задачи для агента
    import json
    from models import StorageConfig
    
    storage_type = None
    storage_config_json = None
    s3_endpoint = None
    s3_access_key = None
    s3_secret_key = None
    s3_bucket = None
    s3_region = None
    
    if db_task.storage_config_id:
        # Используем новое универсальное хранилище
        result_storage = await db.execute(
            select(StorageConfig).where(StorageConfig.id == db_task.storage_config_id)
        )
        storage_config = result_storage.scalar_one_or_none()
        if storage_config:
            storage_type = storage_config.storage_type
            if storage_type == "local":
                # Для типа local нужно получить IP и порт агента-хранилища
                agent_id = storage_config.config_data.get("agent_id")
                if agent_id:
                    result_storage_agent = await db.execute(
                        select(Agent).where(Agent.id == agent_id)
                    )
                    storage_agent = result_storage_agent.scalar_one_or_none()
                    if storage_agent:
                        local_config = {
                            "agent_ip": storage_agent.ip_address,
                            "agent_port": storage_agent.port,
                            "base_path": storage_config.config_data.get("base_path", "/var/backups")
                        }
                        storage_config_json = json.dumps(local_config)
            elif storage_type == "s3":
                # Для S3 формируем из config_data
                s3_endpoint = storage_config.config_data.get("endpoint")
                s3_access_key = storage_config.config_data.get("access_key")
                s3_secret_key = storage_config.config_data.get("secret_key")
                s3_bucket = storage_config.config_data.get("bucket_name")
                s3_region = storage_config.config_data.get("region", "us-east-1")
    elif s3_config:
        # Обратная совместимость со старым S3
        storage_type = "s3"
        s3_endpoint = s3_config.endpoint
        s3_access_key = s3_config.access_key
        s3_secret_key = s3_config.secret_key
        s3_bucket = s3_config.bucket_name
        s3_region = s3_config.region
    
    # Отправляем конфигурацию агенту
    task_config = AgentTaskConfig(
        task_id=db_task.id,
        source_path=db_task.source_path,
        create_archive=db_task.create_archive,
        archive_format=db_task.archive_format,
        s3_endpoint=s3_endpoint,
        s3_access_key=s3_access_key,
        s3_secret_key=s3_secret_key,
        s3_bucket=s3_bucket,
        s3_region=s3_region,
        storage_type=storage_type,
        storage_config=storage_config_json,
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


@router.post("/tasks/{task_id}/edit")
async def edit_task(
    request: Request,
    task_id: int,
    name: str = Form(...),
    agent_id: int = Form(...),
    s3_config_id: int = Form(...),
    source_path: str = Form(...),
    schedule_cron: Optional[str] = Form(None),
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
    is_active: bool = Form(True),
    db: AsyncSession = Depends(get_db),
    user: Optional[User] = Depends(get_current_user_web)
):
    """Редактирование задачи резервного копирования"""
    if not user or not user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    
    result = await db.execute(select(BackupTask).where(BackupTask.id == task_id))
    task = result.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail="Backup task not found")
    
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
    
    # Проверяем агента
    result_agent = await db.execute(select(Agent).where(Agent.id == agent_id))
    agent = result_agent.scalar_one_or_none()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    
    # Проверяем S3 конфигурацию
    result_s3 = await db.execute(select(S3Config).where(S3Config.id == s3_config_id))
    s3_config = result_s3.scalar_one_or_none()
    if not s3_config:
        raise HTTPException(status_code=404, detail="S3 config not found")
    
    # Обновляем поля
    task.name = name
    task.agent_id = agent_id
    task.s3_config_id = s3_config_id
    task.source_path = source_path
    task.schedule_cron = final_schedule_cron
    task.create_archive = create_archive
    task.archive_format = archive_format
    task.is_docker_compose = is_docker_compose
    task.docker_compose_path = docker_compose_path
    task.cleanup_enabled = cleanup_enabled
    task.cleanup_days = cleanup_days
    task.is_active = is_active
    
    # Обновляем информацию о файловой системе, если изменился путь или агент
    from agent_client import AgentClient
    client = AgentClient(agent.ip_address, agent.port)
    filesystem_info = await client.get_filesystem_info(source_path)
    if filesystem_info:
        task.filesystem = filesystem_info.filesystem
    
    await db.commit()
    
    # Отправляем обновленную конфигурацию агенту
    import json
    from models import StorageConfig, Agent as AgentModel
    from schemas import AgentTaskConfig
    
    storage_type = "s3"
    storage_config_json = None
    s3_endpoint = s3_config.endpoint
    s3_access_key = s3_config.access_key
    s3_secret_key = s3_config.secret_key
    s3_bucket = s3_config.bucket_name
    s3_region = s3_config.region
    
    task_config = AgentTaskConfig(
        task_id=task.id,
        source_path=task.source_path,
        create_archive=task.create_archive,
        archive_format=task.archive_format,
        s3_endpoint=s3_endpoint,
        s3_access_key=s3_access_key,
        s3_secret_key=s3_secret_key,
        s3_bucket=s3_bucket,
        s3_region=s3_region,
        storage_type=storage_type,
        storage_config=storage_config_json,
        cleanup_enabled=task.cleanup_enabled,
        cleanup_days=task.cleanup_days,
        is_docker_compose=task.is_docker_compose,
        docker_compose_path=task.docker_compose_path,
        schedule_cron=task.schedule_cron
    )
    await client.send_task_config(task_config)
    
    return RedirectResponse(url="/tasks", status_code=302)


@router.get("/api/web/storage-configs/{config_id}")
async def get_storage_config_web(
    config_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: Optional[User] = Depends(get_current_user_web)
):
    """Получает конфигурацию хранилища по ID через веб-эндпоинт (использует cookie авторизацию)"""
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")
    
    result = await db.execute(select(StorageConfig).where(StorageConfig.id == config_id))
    config = result.scalar_one_or_none()
    if config is None:
        raise HTTPException(status_code=404, detail="Storage config not found")
    
    # Преобразуем в словарь для JSON
    config_dict = {
        "id": config.id,
        "name": config.name,
        "storage_type": config.storage_type,
        "config_data": config.config_data if isinstance(config.config_data, dict) else {},
        "created_at": config.created_at.isoformat() if config.created_at else None,
        "updated_at": config.updated_at.isoformat() if config.updated_at else None,
        "last_check": config.last_check.isoformat() if config.last_check else None,
        "free_space_gb": config.free_space_gb,
        "total_space_gb": config.total_space_gb,
        "used_space_gb": config.used_space_gb,
        "connection_error": config.connection_error
    }
    
    return JSONResponse(content=config_dict)


@router.get("/api/web/backup-tasks/{task_id}")
async def get_backup_task_web(
    task_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: Optional[User] = Depends(get_current_user_web)
):
    """Получает задачу резервного копирования по ID через веб-эндпоинт (использует cookie авторизацию)"""
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")
    
    result = await db.execute(select(BackupTask).where(BackupTask.id == task_id))
    task = result.scalar_one_or_none()
    if task is None:
        raise HTTPException(status_code=404, detail="Backup task not found")
    
    # Преобразуем в словарь для JSON
    task_dict = {
        "id": task.id,
        "name": task.name,
        "agent_id": task.agent_id,
        "s3_config_id": task.s3_config_id,
        "storage_config_id": task.storage_config_id,
        "source_path": task.source_path,
        "filesystem": task.filesystem,
        "schedule_cron": task.schedule_cron,
        "schedule_enabled": task.schedule_enabled,
        "create_archive": task.create_archive,
        "archive_format": task.archive_format,
        "is_docker_compose": task.is_docker_compose,
        "docker_compose_path": task.docker_compose_path,
        "cleanup_enabled": task.cleanup_enabled,
        "cleanup_days": task.cleanup_days,
        "is_active": task.is_active,
        "last_run": task.last_run.isoformat() if task.last_run else None,
        "next_run": task.next_run.isoformat() if task.next_run else None,
        "last_status": task.last_status,
        "last_error": task.last_error,
        "created_at": task.created_at.isoformat() if task.created_at else None,
        "updated_at": task.updated_at.isoformat() if task.updated_at else None
    }
    
    return JSONResponse(content=task_dict)


@router.get("/api/web/reports/{report_id}")
async def get_report_web(
    report_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: Optional[User] = Depends(get_current_user_web)
):
    """Получает отчет по ID через веб-эндпоинт (использует cookie авторизацию)"""
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")
    
    result = await db.execute(select(Report).where(Report.id == report_id))
    report = result.scalar_one_or_none()
    if report is None:
        raise HTTPException(status_code=404, detail="Report not found")
    
    # Преобразуем в словарь для JSON
    report_dict = {
        "id": report.id,
        "name": report.name,
        "description": report.description,
        "agent_ids": report.agent_ids if isinstance(report.agent_ids, list) else (report.agent_ids.split(',') if report.agent_ids else []),
        "postgres_task_ids": report.postgres_task_ids if isinstance(report.postgres_task_ids, list) else (report.postgres_task_ids.split(',') if report.postgres_task_ids else []),
        "send_to_mattermost": report.send_to_mattermost,
        "enabled": report.enabled,
        "schedule_type": report.schedule_type,
        "schedule_hour": report.schedule_hour,
        "schedule_minute": report.schedule_minute,
        "schedule_day_of_week": report.schedule_day_of_week,
        "schedule_hours_interval": report.schedule_hours_interval,
        "last_sent": report.last_sent.isoformat() if report.last_sent else None,
        "next_send": report.next_send.isoformat() if report.next_send else None,
        "created_at": report.created_at.isoformat() if report.created_at else None,
        "updated_at": report.updated_at.isoformat() if report.updated_at else None
    }
    
    return JSONResponse(content=report_dict)


@router.get("/api/web/postgres-backup-tasks/{task_id}")
async def get_postgres_backup_task_web(
    task_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: Optional[User] = Depends(get_current_user_web)
):
    """Получает задачу PostgreSQL бэкапа по ID через веб-эндпоинт (использует cookie авторизацию)"""
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")
    
    result = await db.execute(select(PostgresBackupTask).where(PostgresBackupTask.id == task_id))
    task = result.scalar_one_or_none()
    if task is None:
        raise HTTPException(status_code=404, detail="PostgreSQL backup task not found")
    
    # Преобразуем в словарь для JSON
    task_dict = {
        "id": task.id,
        "name": task.name,
        "agent_id": task.agent_id,
        "s3_config_id": task.s3_config_id,
        "storage_config_id": task.storage_config_id,
        "host": task.host,
        "port": task.port,
        "username": task.username,
        "password": "***",  # Не возвращаем реальный пароль
        "database": task.database,
        "backup_format": task.backup_format,
        "compression_level": task.compression_level,
        "include_schema": task.include_schema,
        "include_data": task.include_data,
        "include_roles": task.include_roles,
        "include_tablespaces": task.include_tablespaces,
        "schedule_cron": task.schedule_cron,
        "schedule_enabled": task.schedule_enabled,
        "cleanup_enabled": task.cleanup_enabled,
        "cleanup_days": task.cleanup_days,
        "is_active": task.is_active,
        "last_run": task.last_run.isoformat() if task.last_run else None,
        "next_run": task.next_run.isoformat() if task.next_run else None,
        "last_status": task.last_status,
        "last_error": task.last_error,
        "created_at": task.created_at.isoformat() if task.created_at else None,
        "updated_at": task.updated_at.isoformat() if task.updated_at else None
    }
    
    return JSONResponse(content=task_dict)


@router.delete("/api/web/storage-configs/{config_id}")
async def delete_storage_config_web(
    config_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: Optional[User] = Depends(get_current_user_web)
):
    """Удаляет конфигурацию хранилища через веб-эндпоинт (использует cookie авторизацию)"""
    if not user or not user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    
    result = await db.execute(select(StorageConfig).where(StorageConfig.id == config_id))
    config = result.scalar_one_or_none()
    if config is None:
        raise HTTPException(status_code=404, detail="Storage config not found")
    
    # Проверяем, используется ли хранилище
    result_tasks = await db.execute(
        select(BackupTask).where(BackupTask.storage_config_id == config_id)
    )
    tasks = result_tasks.scalars().all()
    if tasks:
        raise HTTPException(
            status_code=400, 
            detail=f"Cannot delete storage config: it is used by {len(tasks)} backup task(s)"
        )
    
    result_postgres = await db.execute(
        select(PostgresBackupTask).where(PostgresBackupTask.storage_config_id == config_id)
    )
    postgres_tasks = result_postgres.scalars().all()
    if postgres_tasks:
        raise HTTPException(
            status_code=400, 
            detail=f"Cannot delete storage config: it is used by {len(postgres_tasks)} PostgreSQL backup task(s)"
        )
    
    result_agents = await db.execute(
        select(Agent).where(Agent.storage_config_id == config_id)
    )
    agents = result_agents.scalars().all()
    if agents:
        raise HTTPException(
            status_code=400, 
            detail=f"Cannot delete storage config: it is used by {len(agents)} agent(s)"
        )
    
    db.delete(config)
    await db.commit()
    
    return JSONResponse(content={"success": True, "message": "Storage config deleted successfully"})


@router.delete("/api/web/backup-tasks/{task_id}")
async def delete_backup_task_web(
    task_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: Optional[User] = Depends(get_current_user_web)
):
    """Удаляет задачу резервного копирования через веб-эндпоинт (использует cookie авторизацию)"""
    if not user or not user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    
    result = await db.execute(select(BackupTask).where(BackupTask.id == task_id))
    task = result.scalar_one_or_none()
    if task is None:
        raise HTTPException(status_code=404, detail="Backup task not found")
    
    # Проверяем, есть ли история бэкапов для этой задачи
    result_history = await db.execute(
        select(BackupHistory).where(BackupHistory.task_id == task_id)
    )
    history_records = result_history.scalars().all()
    if history_records:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot delete backup task: it has {len(history_records)} history record(s). Delete history first."
        )
    
    db.delete(task)
    await db.commit()
    
    return JSONResponse(content={"success": True, "message": "Backup task deleted successfully"})


@router.delete("/api/web/reports/{report_id}")
async def delete_report_web(
    report_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: Optional[User] = Depends(get_current_user_web)
):
    """Удаляет отчет через веб-эндпоинт (использует cookie авторизацию)"""
    if not user or not user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    
    result = await db.execute(select(Report).where(Report.id == report_id))
    report = result.scalar_one_or_none()
    if report is None:
        raise HTTPException(status_code=404, detail="Report not found")
    
    db.delete(report)
    await db.commit()
    
    return JSONResponse(content={"success": True, "message": "Report deleted successfully"})


@router.delete("/api/web/postgres-backup-tasks/{task_id}")
async def delete_postgres_backup_task_web(
    task_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: Optional[User] = Depends(get_current_user_web)
):
    """Удаляет задачу PostgreSQL бэкапа через веб-эндпоинт (использует cookie авторизацию)"""
    if not user or not user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    
    result = await db.execute(select(PostgresBackupTask).where(PostgresBackupTask.id == task_id))
    task = result.scalar_one_or_none()
    if task is None:
        raise HTTPException(status_code=404, detail="PostgreSQL backup task not found")
    
    # Проверяем, есть ли история бэкапов для этой задачи
    result_history = await db.execute(
        select(PostgresBackupHistory).where(PostgresBackupHistory.task_id == task_id)
    )
    history_records = result_history.scalars().all()
    if history_records:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot delete PostgreSQL backup task: it has {len(history_records)} history record(s). Delete history first."
        )
    
    db.delete(task)
    await db.commit()
    
    return JSONResponse(content={"success": True, "message": "PostgreSQL backup task deleted successfully"})


@router.post("/api/web/postgres-backup-tasks/{task_id}/execute")
async def execute_postgres_backup_web(
    task_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: Optional[User] = Depends(get_current_user_web)
):
    """Запускает выполнение PostgreSQL бэкапа вручную через веб-эндпоинт (использует cookie авторизацию)"""
    if not user or not user.is_admin:
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
        from postgres_backup import PostgresBackupExecutor
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
        return JSONResponse(content=result)
    except Exception as e:
        history.status = "error"
        history.error_message = str(e)
        history.finished_at = datetime.utcnow()
        task.last_status = "error"
        task.last_error = str(e)
        await db.commit()
        return JSONResponse(content={"success": False, "error": str(e)}, status_code=500)


@router.post("/api/web/reports/{report_id}/send")
async def send_report_now_web(
    report_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: Optional[User] = Depends(get_current_user_web)
):
    """Отправка отчета немедленно через веб-эндпоинт (использует cookie авторизацию)"""
    if not user or not user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    
    result = await db.execute(select(Report).where(Report.id == report_id))
    report = result.scalar_one_or_none()
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")
    
    result_settings = await db.execute(select(Settings))
    settings_obj = result_settings.scalar_one_or_none()
    if not settings_obj or not settings_obj.mattermost_enabled or not settings_obj.mattermost_webhook_url:
        return JSONResponse(content={"success": False, "error": "Mattermost not configured"}, status_code=400)
    
    try:
        from report_scheduler import ReportScheduler
        scheduler = ReportScheduler()
        await scheduler.send_report(report, settings_obj.mattermost_webhook_url, db)
        
        return JSONResponse(content={"success": True, "message": "Report sent successfully"})
    except Exception as e:
        logger.error(f"Error sending report {report_id}: {e}")
        return JSONResponse(content={"success": False, "error": f"Ошибка при отправке отчета: {str(e)}"}, status_code=500)


@router.get("/api/web/postgres-backup-tasks/{task_id}/backups")
async def list_postgres_backups_web(
    task_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: Optional[User] = Depends(get_current_user_web)
):
    """Получает список доступных бэкапов для задачи через веб-эндпоинт (использует cookie авторизацию)"""
    if not user or not user.is_admin:
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
    
    # Получаем список бэкапов
    backups = []
    try:
        if storage_config:
            from storage_manager import StorageManager
            import json
            
            # Парсим config_data если это строка
            config_data = storage_config.config_data
            if isinstance(config_data, str):
                try:
                    config_data = json.loads(config_data)
                except (json.JSONDecodeError, TypeError):
                    logger.error(f"Invalid config_data format for storage {storage_config.id}")
                    config_data = {}
            elif not isinstance(config_data, dict):
                config_data = {}
            
            db_name_safe = task.database.replace("/", "_").replace("\\", "_")
            prefix = f"postgres_backups/{db_name_safe}/"
            backups_list = await StorageManager.list_backups(
                storage_config.storage_type,
                config_data,
                prefix
            )
            # Преобразуем формат для совместимости с S3Client
            # StorageManager.list_backups возвращает список строк (имен файлов) или словарей
            backups = []
            for item in backups_list:
                if isinstance(item, dict):
                    # Используем полный путь (object_name или name) для восстановления
                    object_name = item.get("object_name") or item.get("name", "")
                    display_name = item.get("display_name")
                    if not display_name:
                        # Если display_name не указан, извлекаем из полного пути
                        display_name = object_name
                        if "/" in display_name:
                            display_name = display_name.split("/")[-1]
                    
                    # Получаем дату модификации
                    last_modified = item.get("last_modified") or item.get("last_modified_time", "")
                    if last_modified and not isinstance(last_modified, str):
                        # Если это не строка, преобразуем
                        if hasattr(last_modified, 'isoformat'):
                            last_modified = last_modified.isoformat()
                        else:
                            last_modified = str(last_modified)
                    
                    backups.append({
                        "name": object_name,  # Полный путь для восстановления
                        "display_name": display_name,  # Только имя файла для отображения
                        "size": item.get("size", 0),
                        "last_modified": last_modified,
                        "etag": item.get("etag", "")
                    })
                else:
                    # Если это строка (имя файла)
                    full_path = str(item)
                    file_name = full_path.split("/")[-1] if "/" in full_path else full_path
                    backups.append({
                        "name": full_path,  # Полный путь
                        "display_name": file_name,  # Только имя
                        "size": 0,
                        "last_modified": "",
                        "etag": ""
                    })
        elif s3_config:
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
    except Exception as e:
        import traceback
        logger.error(f"Error listing backups: {e}\n{traceback.format_exc()}")
        return JSONResponse(content={"backups": [], "error": str(e)}, status_code=500)
    
    return JSONResponse(content={"backups": backups})


@router.post("/api/web/postgres-backup-tasks/{task_id}/restore")
async def restore_postgres_backup_web(
    task_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: Optional[User] = Depends(get_current_user_web)
):
    """Восстанавливает PostgreSQL бэкап через веб-эндпоинт (использует cookie авторизацию)"""
    if not user or not user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    
    try:
        body = await request.json()
        s3_path = body.get("s3_path")
        target_database = body.get("target_database")
    except:
        raise HTTPException(status_code=400, detail="Invalid request body")
    
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
    try:
        from postgres_backup import PostgresBackupExecutor
        executor = PostgresBackupExecutor(task, storage_config=storage_config, s3_config=s3_config)
        
        # Получаем параметры для стороннего сервера
        restore_remote = body.get("restore_remote", False)
        remote_host = body.get("remote_host")
        remote_port = body.get("remote_port")
        remote_database = body.get("remote_database")
        remote_username = body.get("remote_username")
        remote_password = body.get("remote_password")
        
        result = await executor.restore_backup(
            s3_path, 
            target_database,
            restore_remote=restore_remote,
            remote_host=remote_host,
            remote_port=remote_port,
            remote_database=remote_database,
            remote_username=remote_username,
            remote_password=remote_password
        )
        
        if result.get("success"):
            return JSONResponse(content={"success": True, "message": "Database restored successfully"})
        else:
            return JSONResponse(content={"success": False, "error": result.get("error", "Unknown error")}, status_code=500)
    except Exception as e:
        return JSONResponse(content={"success": False, "error": str(e)}, status_code=500)


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
    
    # Получаем информацию о дисках всех агентов
    from models import AgentDisk
    result_disks = await db.execute(
        select(AgentDisk).order_by(AgentDisk.agent_id, AgentDisk.mount_point)
    )
    all_disks = result_disks.scalars().all()
    
    # Группируем диски по агентам
    agent_disks_dict = {}
    for disk in all_disks:
        if disk.agent_id not in agent_disks_dict:
            agent_disks_dict[disk.agent_id] = []
        agent_disks_dict[disk.agent_id].append({
            "device": disk.device,
            "mount_point": disk.mount_point,
            "filesystem": disk.filesystem,
            "total_gb": disk.total_gb,
            "used_gb": disk.used_gb,
            "available_gb": disk.available_gb,
            "used_percent": disk.used_percent,
            "last_update": disk.last_update
        })
    
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
    
    # Преобразуем агентов в список словарей для JSON сериализации
    agents_list = []
    for agent in agents:
        agents_list.append({
            "id": agent.id,
            "name": agent.name,
            "ip_address": agent.ip_address,
            "port": agent.port,
            "hostname": agent.hostname,
            "is_active": agent.is_active,
            "storage_config_id": agent.storage_config_id
        })
    
    return templates.TemplateResponse("storage_configs.html", {
        "request": request,
        "user": user,
        "app_name": settings.app_name,
        "storage_configs": storage_configs_list,
        "s3_configs": s3_configs,  # Для обратной совместимости
        "agents": agents_list,  # Теперь это список словарей, а не SQLAlchemy объекты
        "agents_dict": agents_dict,
        "agent_disks": agent_disks_dict  # Информация о дисках агентов
    })


@router.get("/s3-configs", response_class=HTMLResponse)
async def s3_configs_page_redirect(
    request: Request,
    user: Optional[User] = Depends(get_current_user_web)
):
    """Редирект со старой страницы S3 на новую страницу хранилищ"""
    return RedirectResponse(url="/storage-configs", status_code=302)


@router.post("/storage-configs/add")
async def add_storage_config(
    request: Request,
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
    """Создание нового хранилища"""
    if not user or not user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    
    import json
    
    # Формируем config_data в зависимости от типа хранилища
    config_data = {}
    
    # Загружаем данные для отображения страницы при ошибке
    result_storage = await db.execute(select(StorageConfig).order_by(StorageConfig.created_at.desc()))
    storage_configs = result_storage.scalars().all()
    result_s3 = await db.execute(select(S3Config).order_by(S3Config.id.desc()))
    s3_configs = result_s3.scalars().all()
    result_agents = await db.execute(select(Agent))
    agents = result_agents.scalars().all()
    agents_dict = {agent.id: agent for agent in agents}
    
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
        if config.storage_type == "local" and config.config_data.get("agent_id"):
            agent_id = config.config_data.get("agent_id")
            if agent_id in agents_dict:
                config_dict["agent"] = agents_dict[agent_id]
        storage_configs_list.append(config_dict)
    
    if storage_type == "s3":
        if not all([endpoint, access_key, secret_key, bucket_name]):
            return templates.TemplateResponse("storage_configs.html", {
                "request": request,
                "user": user,
                "app_name": settings.app_name,
                "storage_configs": storage_configs_list,
                "s3_configs": s3_configs,
                "agents": agents,
                "agents_dict": agents_dict,
                "error": "Для S3 хранилища необходимо указать endpoint, access_key, secret_key и bucket_name"
            })
        config_data = {
            "endpoint": endpoint,
            "access_key": access_key,
            "secret_key": secret_key,
            "bucket_name": bucket_name,
            "region": region or "us-east-1",
            "use_ssl": use_ssl
        }
    elif storage_type == "sftp":
        if not all([sftp_host, sftp_username, sftp_password, sftp_base_path]):
            return templates.TemplateResponse("storage_configs.html", {
                "request": request,
                "user": user,
                "app_name": settings.app_name,
                "storage_configs": storage_configs_list,
                "s3_configs": s3_configs,
                "agents": agents,
                "agents_dict": agents_dict,
                "error": "Для SFTP хранилища необходимо указать host, username, password и base_path"
            })
        config_data = {
            "host": sftp_host,
            "port": sftp_port or 22,
            "username": sftp_username,
            "password": sftp_password,
            "base_path": sftp_base_path
        }
    elif storage_type == "nfs":
        if not all([nfs_server, nfs_export_path, nfs_mount_point]):
            return templates.TemplateResponse("storage_configs.html", {
                "request": request,
                "user": user,
                "app_name": settings.app_name,
                "storage_configs": storage_configs_list,
                "s3_configs": s3_configs,
                "agents": agents,
                "agents_dict": agents_dict,
                "error": "Для NFS хранилища необходимо указать server, export_path и mount_point"
            })
        config_data = {
            "server": nfs_server,
            "export_path": nfs_export_path,
            "mount_point": nfs_mount_point,
            "options": nfs_options or "rw,sync"
        }
    elif storage_type == "local":
        if not all([local_agent_id, local_base_path]):
            return templates.TemplateResponse("storage_configs.html", {
                "request": request,
                "user": user,
                "app_name": settings.app_name,
                "storage_configs": storage_configs_list,
                "s3_configs": s3_configs,
                "agents": agents,
                "agents_dict": agents_dict,
                "error": "Для локального хранилища необходимо указать агента и базовый путь"
            })
        # Проверяем существование агента
        result_agent = await db.execute(select(Agent).where(Agent.id == local_agent_id))
        agent = result_agent.scalar_one_or_none()
        if not agent:
            return templates.TemplateResponse("storage_configs.html", {
                "request": request,
                "user": user,
                "app_name": settings.app_name,
                "storage_configs": storage_configs_list,
                "s3_configs": s3_configs,
                "agents": agents,
                "agents_dict": agents_dict,
                "error": "Указанный агент не найден"
            })
        config_data = {
            "agent_id": local_agent_id,
            "base_path": local_base_path
        }
    else:
        return templates.TemplateResponse("storage_configs.html", {
            "request": request,
            "user": user,
            "app_name": settings.app_name,
            "storage_configs": storage_configs_list,
            "s3_configs": s3_configs,
            "agents": agents,
            "agents_dict": agents_dict,
            "error": f"Неизвестный тип хранилища: {storage_type}"
        })
    
    # Проверяем, что хранилище с таким именем не существует
    result_existing = await db.execute(select(StorageConfig).where(StorageConfig.name == name))
    existing = result_existing.scalar_one_or_none()
    if existing:
        return templates.TemplateResponse("storage_configs.html", {
            "request": request,
            "user": user,
            "app_name": settings.app_name,
            "storage_configs": storage_configs_list,
            "s3_configs": s3_configs,
            "agents": agents,
            "agents_dict": agents_dict,
            "error": f"Хранилище с именем '{name}' уже существует"
        })
    
    # Создаем новое хранилище
    new_config = StorageConfig(
        name=name,
        storage_type=storage_type,
        config_data=config_data
    )
    db.add(new_config)
    await db.commit()
    await db.refresh(new_config)
    
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
    
    result_agents = await db.execute(select(Agent))
    agents = result_agents.scalars().all()
    
    return templates.TemplateResponse("postgres_backups.html", {
        "request": request,
        "user": user,
        "app_name": settings.app_name,
        "tasks": tasks,
        "s3_configs": s3_configs,
        "agents": agents,
        "available_storage": available_storage
    })


@router.post("/postgres-backups/add")
async def add_postgres_backup(
    request: Request,
    name: str = Form(...),
    agent_id: int = Form(...),
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
    use_agent_backup: bool = Form(False),
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
        agent_id=agent_id,
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
        use_agent_backup=use_agent_backup,
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
    agent_id: Optional[int] = Form(None),
    s3_config_id: Optional[int] = Form(None),
    storage_config_id: Optional[int] = Form(None),
    host: Optional[str] = Form(None),
    port: Optional[int] = Form(None),
    username: Optional[str] = Form(None),
    password: Optional[str] = Form(None),
    database: Optional[str] = Form(None),
    backup_format: str = Form("custom"),
    compression_level: int = Form(6),
    include_schema: bool = Form(True),
    include_data: bool = Form(True),
    include_roles: bool = Form(False),
    include_tablespaces: bool = Form(False),
    use_agent_backup: bool = Form(False),
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
    
    # Проверяем S3 конфигурацию (если указана)
    s3_config = None
    if s3_config_id:
        result_s3 = await db.execute(select(S3Config).where(S3Config.id == s3_config_id))
        s3_config = result_s3.scalar_one_or_none()
        if not s3_config:
            raise HTTPException(status_code=404, detail="S3 config not found")
    
    # Проверяем хранилище данных (если указано)
    storage_config = None
    if storage_config_id:
        result_storage = await db.execute(select(StorageConfig).where(StorageConfig.id == storage_config_id))
        storage_config = result_storage.scalar_one_or_none()
        if not storage_config:
            raise HTTPException(status_code=404, detail="Storage config not found")
    
    # Шифруем пароль, если он изменился
    from postgres_backup import encrypt_password
    if password and password.strip() and password != "***":
        encrypted_password = encrypt_password(password)
    else:
        encrypted_password = task.password  # Оставляем старый пароль
    
    # Обновляем поля
    task.name = name
    if agent_id is not None:
        task.agent_id = agent_id
    if s3_config_id is not None:
        task.s3_config_id = s3_config_id
    if storage_config_id is not None:
        task.storage_config_id = storage_config_id
    if host is not None:
        task.host = host
    if port is not None:
        task.port = port
    if username is not None:
        task.username = username
    if password and password.strip() and password != "***":
        task.password = encrypted_password
    if database is not None:
        task.database = database
    task.backup_format = backup_format
    task.compression_level = compression_level
    task.include_schema = include_schema
    task.include_data = include_data
    task.include_roles = include_roles
    task.include_tablespaces = include_tablespaces
    task.use_agent_backup = use_agent_backup
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
        # Если secret_key не указан, используем старый из базы
        if not secret_key or secret_key.strip() == "":
            if config.config_data and isinstance(config.config_data, dict):
                secret_key = config.config_data.get("secret_key", "")
        config_data = {
            "endpoint": endpoint,
            "access_key": access_key,
            "secret_key": secret_key,
            "bucket_name": bucket_name,
            "region": region or "us-east-1",
            "use_ssl": use_ssl
        }
    elif storage_type == "sftp":
        # Если пароль не указан, используем старый из базы
        if not sftp_password or sftp_password.strip() == "":
            if config.config_data and isinstance(config.config_data, dict):
                sftp_password = config.config_data.get("password", "")
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
    mattermost_channel: Optional[str] = Form(None),
    mattermost_daily_report: bool = Form(False),
    mattermost_report_time: str = Form("09:00"),
    agent_poll_interval: int = Form(60),
    s3_check_interval: int = Form(86400),
    disk_space_check_interval: int = Form(3600),
    disk_space_warning_threshold: int = Form(10),
    tls_enabled: bool = Form(False),
    tls_cert_folder: Optional[str] = Form(None),
    tls_cert_path: Optional[str] = Form(None),
    tls_key_path: Optional[str] = Form(None),
    logo_file: Optional[UploadFile] = File(None),
    favicon_file: Optional[UploadFile] = File(None),
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
    settings_obj.mattermost_channel = mattermost_channel if mattermost_channel else None
    settings_obj.mattermost_daily_report = mattermost_daily_report
    settings_obj.mattermost_report_time = mattermost_report_time
    settings_obj.agent_poll_interval = agent_poll_interval
    settings_obj.s3_check_interval = s3_check_interval
    settings_obj.disk_space_check_interval = disk_space_check_interval
    # Валидация порога (должен быть от 5 до 50)
    if disk_space_warning_threshold < 5:
        disk_space_warning_threshold = 5
    elif disk_space_warning_threshold > 50:
        disk_space_warning_threshold = 50
    settings_obj.disk_space_warning_threshold = disk_space_warning_threshold
    
    # TLS настройки
    settings_obj.tls_enabled = tls_enabled
    settings_obj.tls_cert_folder = tls_cert_folder if tls_cert_folder else None
    settings_obj.tls_cert_path = tls_cert_path if tls_cert_path else None
    settings_obj.tls_key_path = tls_key_path if tls_key_path else None
    
    # Загрузка логотипа и favicon в /opt/im
    uploads_dir = Path("/opt/im")
    uploads_dir.mkdir(parents=True, exist_ok=True)
    
    if logo_file and logo_file.filename:
        # Сохраняем логотип
        logo_path = uploads_dir / "logo"
        with open(logo_path, "wb") as f:
            shutil.copyfileobj(logo_file.file, f)
        settings_obj.logo_path = str(logo_path)
        logger.info(f"Logo uploaded: {logo_path}")
    
    if favicon_file and favicon_file.filename:
        # Сохраняем favicon
        favicon_path = uploads_dir / "favicon"
        with open(favicon_path, "wb") as f:
            shutil.copyfileobj(favicon_file.file, f)
        settings_obj.favicon_path = str(favicon_path)
        logger.info(f"Favicon uploaded: {favicon_path}")
    
    # Валидация TLS настроек
    if tls_enabled:
        if not tls_cert_path or not tls_key_path:
            return templates.TemplateResponse("settings.html", {
                "request": request,
                "user": user,
                "app_name": settings.app_name,
                "settings": settings_obj,
                "error": "При включении HTTPS необходимо указать оба файла: сертификат и приватный ключ"
            })
        
        # Проверяем существование файлов
        if not os.path.exists(tls_cert_path):
            return templates.TemplateResponse("settings.html", {
                "request": request,
                "user": user,
                "app_name": settings.app_name,
                "settings": settings_obj,
                "error": f"Файл сертификата не найден: {tls_cert_path}"
            })
        
        if not os.path.exists(tls_key_path):
            return templates.TemplateResponse("settings.html", {
                "request": request,
                "user": user,
                "app_name": settings.app_name,
                "settings": settings_obj,
                "error": f"Файл приватного ключа не найден: {tls_key_path}"
            })
    
    await db.commit()
    
    return RedirectResponse(url="/settings?success=1", status_code=302)


@router.get("/static/uploads/logo")
async def get_logo(
    db: AsyncSession = Depends(get_db)
):
    """Возвращает логотип"""
    result = await db.execute(select(Settings))
    settings_obj = result.scalar_one_or_none()
    
    if settings_obj and settings_obj.logo_path and os.path.exists(settings_obj.logo_path):
        return FileResponse(settings_obj.logo_path)
    else:
        raise HTTPException(status_code=404, detail="Logo not found")


@router.get("/static/uploads/favicon")
async def get_favicon(
    db: AsyncSession = Depends(get_db)
):
    """Возвращает favicon"""
    result = await db.execute(select(Settings))
    settings_obj = result.scalar_one_or_none()
    
    if settings_obj and settings_obj.favicon_path and os.path.exists(settings_obj.favicon_path):
        return FileResponse(settings_obj.favicon_path)
    else:
        raise HTTPException(status_code=404, detail="Favicon not found")


@router.post("/settings/test-mattermost")
async def test_mattermost_notification(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: Optional[User] = Depends(get_current_user_web)
):
    """Отправляет тестовое уведомление в Mattermost"""
    if not user or not user.is_admin:
        return JSONResponse(content={"success": False, "error": "Admin access required"}, status_code=403)
    
    result = await db.execute(select(Settings))
    settings_obj = result.scalar_one_or_none()
    
    if not settings_obj or not settings_obj.mattermost_enabled or not settings_obj.mattermost_webhook_url:
        return JSONResponse(content={
            "success": False,
            "error": "Mattermost не настроен. Укажите webhook URL и включите уведомления."
        })
    
    from mattermost_client import MattermostClient
    
    try:
        client = MattermostClient(settings_obj.mattermost_webhook_url)
        success = await client.send_test_message(channel=settings_obj.mattermost_channel)
        
        if success:
            return JSONResponse(content={
                "success": True,
                "message": "Тестовое уведомление успешно отправлено в Mattermost!"
            })
        else:
            return JSONResponse(content={
                "success": False,
                "error": "Не удалось отправить тестовое уведомление. Проверьте webhook URL и настройки канала."
            })
    except Exception as e:
        logger.error(f"Error sending test Mattermost notification: {e}")
        return JSONResponse(content={
            "success": False,
            "error": f"Ошибка при отправке тестового уведомления: {str(e)}"
        })


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


@router.post("/reports/{report_id}/edit")
async def edit_report(
    request: Request,
    report_id: int,
    name: str = Form(...),
    description: Optional[str] = Form(None),
    agent_ids: Optional[str] = Form(None),
    postgres_task_ids: Optional[str] = Form(None),
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
    """Редактирование отчета"""
    if not user or not user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    
    result = await db.execute(select(Report).where(Report.id == report_id))
    report = result.scalar_one_or_none()
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")
    
    # Обновляем поля
    report.name = name
    report.description = description
    report.send_to_mattermost = send_to_mattermost
    report.enabled = enabled
    
    # Обрабатываем agent_ids
    if agent_ids:
        agent_ids_list = [int(id.strip()) for id in agent_ids.split(',') if id.strip()]
        report.agent_ids = agent_ids_list
    else:
        report.agent_ids = []
    
    # Обрабатываем postgres_task_ids
    if postgres_task_ids:
        postgres_task_ids_list = [int(id.strip()) for id in postgres_task_ids.split(',') if id.strip()]
        report.postgres_task_ids = postgres_task_ids_list
    else:
        report.postgres_task_ids = []
    
    # Обновляем расписание
    report.schedule_type = schedule_type
    report.schedule_hour = schedule_hour
    report.schedule_minute = schedule_minute
    report.schedule_day_of_week = schedule_day_of_week
    report.schedule_hours_interval = schedule_hours_interval
    
    # Пересчитываем next_send на основе нового расписания
    from report_scheduler import calculate_next_send
    report.next_send = calculate_next_send(report)
    
    await db.commit()
    await db.refresh(report)
    
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
    db: AsyncSession = Depends(get_db),
    user: Optional[User] = Depends(get_current_user_web)
):
    """Страница о программе"""
    result = await db.execute(select(Settings))
    settings_obj = result.scalar_one_or_none()
    
    return templates.TemplateResponse("about.html", {
        "request": request,
        "user": user,
        "app_name": settings.app_name,
        "app_version": settings.app_version,
        "settings": settings_obj
    })


@router.get("/users", response_class=HTMLResponse)
async def users_page(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: Optional[User] = Depends(get_current_user_web)
):
    """Страница управления пользователями"""
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    
    result = await db.execute(select(User).order_by(User.created_at.desc()))
    users = result.scalars().all()
    
    return templates.TemplateResponse("users.html", {
        "request": request,
        "user": user,
        "app_name": settings.app_name,
        "users": users
    })


@router.post("/users/add")
async def add_user(
    request: Request,
    username: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    db: AsyncSession = Depends(get_db),
    user: Optional[User] = Depends(get_current_user_web)
):
    """Добавление нового пользователя"""
    if not user or not user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    
    # Проверяем, что пользователь с таким username не существует
    result = await db.execute(select(User).where(User.username == username))
    existing_user = result.scalar_one_or_none()
    if existing_user:
        result_all = await db.execute(select(User).order_by(User.created_at.desc()))
        all_users = result_all.scalars().all()
        return templates.TemplateResponse("users.html", {
            "request": request,
            "user": user,
            "app_name": settings.app_name,
            "users": all_users,
            "error": "Пользователь с таким именем уже существует"
        })
    
    # Проверяем, что пользователь с таким email не существует
    result = await db.execute(select(User).where(User.email == email))
    existing_email = result.scalar_one_or_none()
    if existing_email:
        result_all = await db.execute(select(User).order_by(User.created_at.desc()))
        all_users = result_all.scalars().all()
        return templates.TemplateResponse("users.html", {
            "request": request,
            "user": user,
            "app_name": settings.app_name,
            "users": all_users,
            "error": "Пользователь с таким email уже существует"
        })
    
    # Создаем нового пользователя (все с ролью администратора)
    new_user = User(
        username=username,
        email=email,
        password_hash=get_password_hash(password),
        is_admin=True  # Все пользователи - администраторы
    )
    db.add(new_user)
    await db.commit()
    
    return RedirectResponse(url="/users", status_code=302)


@router.get("/change-password", response_class=HTMLResponse)
async def change_password_page(
    request: Request,
    user: Optional[User] = Depends(get_current_user_web)
):
    """Страница изменения пароля"""
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    
    return templates.TemplateResponse("change_password.html", {
        "request": request,
        "user": user,
        "app_name": settings.app_name
    })


@router.post("/change-password")
async def change_password(
    request: Request,
    current_password: str = Form(...),
    new_password: str = Form(...),
    confirm_password: str = Form(...),
    db: AsyncSession = Depends(get_db),
    user: Optional[User] = Depends(get_current_user_web)
):
    """Обработка изменения пароля"""
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    
    # Проверяем текущий пароль
    if not verify_password(current_password, user.password_hash):
        return templates.TemplateResponse("change_password.html", {
            "request": request,
            "user": user,
            "app_name": settings.app_name,
            "error": "Текущий пароль неверен"
        })
    
    # Проверяем, что новый пароль и подтверждение совпадают
    if new_password != confirm_password:
        return templates.TemplateResponse("change_password.html", {
            "request": request,
            "user": user,
            "app_name": settings.app_name,
            "error": "Новый пароль и подтверждение не совпадают"
        })
    
    # Проверяем минимальную длину пароля
    if len(new_password) < 6:
        return templates.TemplateResponse("change_password.html", {
            "request": request,
            "user": user,
            "app_name": settings.app_name,
            "error": "Пароль должен содержать минимум 6 символов"
        })
    
    # Обновляем пароль
    user.password_hash = get_password_hash(new_password)
    await db.commit()
    
    return templates.TemplateResponse("change_password.html", {
        "request": request,
        "user": user,
        "app_name": settings.app_name,
        "success": "Пароль успешно изменен"
    })


@router.get("/api/files/list")
async def list_files(
    folder_path: str = Query(..., description="Путь к папке"),
    file_extensions: Optional[str] = Query(None, description="Расширения файлов через запятую (например: .crt,.key)"),
    user: Optional[User] = Depends(get_current_user_web)
):
    """Получает список файлов в указанной директории"""
    if not user or not user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    
    try:
        # Проверяем, что путь существует и является директорией
        if not os.path.exists(folder_path):
            return JSONResponse(
                status_code=400,
                content={"error": "Директория не существует"}
            )
        
        if not os.path.isdir(folder_path):
            return JSONResponse(
                status_code=400,
                content={"error": "Указанный путь не является директорией"}
            )
        
        # Получаем список файлов
        files = []
        try:
            items = os.listdir(folder_path)
            for item in items:
                item_path = os.path.join(folder_path, item)
                if os.path.isfile(item_path):
                    # Фильтруем по расширениям, если указаны
                    if file_extensions:
                        extensions = [ext.strip().lower() for ext in file_extensions.split(",")]
                        file_ext = os.path.splitext(item)[1].lower()
                        if file_ext in extensions:
                            files.append({
                                "name": item,
                                "path": item_path,
                                "extension": file_ext
                            })
                    else:
                        files.append({
                            "name": item,
                            "path": item_path,
                            "extension": os.path.splitext(item)[1].lower()
                        })
        except PermissionError:
            return JSONResponse(
                status_code=403,
                content={"error": "Нет доступа к директории"}
            )
        
        # Сортируем файлы по имени
        files.sort(key=lambda x: x["name"])
        
        return JSONResponse(content={"files": files})
    
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"error": f"Ошибка при получении списка файлов: {str(e)}"}
        )


@router.get("/api/files/validate-path")
async def validate_path(
    folder_path: str = Query(..., description="Путь к папке"),
    user: Optional[User] = Depends(get_current_user_web)
):
    """Проверяет валидность пути к директории"""
    if not user or not user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    
    try:
        if not os.path.exists(folder_path):
            return JSONResponse(
                status_code=400,
                content={"valid": False, "error": "Путь не существует"}
            )
        
        if not os.path.isdir(folder_path):
            return JSONResponse(
                status_code=400,
                content={"valid": False, "error": "Указанный путь не является директорией"}
            )
        
        # Проверяем доступ на чтение
        if not os.access(folder_path, os.R_OK):
            return JSONResponse(
                status_code=403,
                content={"valid": False, "error": "Нет доступа на чтение к директории"}
            )
        
        return JSONResponse(content={"valid": True})
    
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"valid": False, "error": f"Ошибка при проверке пути: {str(e)}"}
        )

