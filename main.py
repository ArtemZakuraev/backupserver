"""
Главный модуль сервера резервного копирования
"""
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
import os
import logging

from database import engine, async_session_maker
from models import Base, User, Settings, Report, ReportHistory
from routes import router as api_router
from web_routes import router as web_router
from config import settings
from utils import get_password_hash
from agent_poller import AgentPoller
from s3_checker import S3Checker
from daily_report import DailyReportGenerator
from report_scheduler import ReportScheduler
from postgres_scheduler import PostgresBackupScheduler
import asyncio

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Управление жизненным циклом приложения
    """
    # Startup
    try:
        logger.info("=" * 60)
        logger.info(f"{settings.app_name} v{settings.app_version}")
        logger.info("=" * 60)
        
        # Создаем директории
        os.makedirs("static/uploads", exist_ok=True)
        os.makedirs("templates", exist_ok=True)
        logger.info("[OK] Directories created")
        
        # Проверяем и создаем базу данных
        try:
            # Пытаемся подключиться к БД
            async with engine.begin() as conn:
                # Создаем таблицы
                await conn.run_sync(Base.metadata.create_all)
            
            logger.info("[OK] Database tables created/verified")
            
            # Добавляем колонку connection_error, если её нет (миграция)
            try:
                from sqlalchemy import text
                async with engine.begin() as conn:
                    # Проверяем существование колонки и добавляем, если её нет
                    result = await conn.execute(text("""
                        SELECT column_name 
                        FROM information_schema.columns 
                        WHERE table_name='s3_configs' AND column_name='connection_error'
                    """))
                    if result.scalar() is None:
                        await conn.execute(text("ALTER TABLE s3_configs ADD COLUMN connection_error TEXT"))
                        logger.info("[OK] Added connection_error column to s3_configs table")
                    else:
                        logger.info("[OK] Column connection_error already exists")
            except Exception as e:
                logger.warning(f"[WARNING] Could not add connection_error column: {e}")
            
            # Создаем таблицу storage_configs, если её нет (миграция)
            try:
                from sqlalchemy import text
                async with engine.begin() as conn:
                    # Проверяем существование таблицы
                    result = await conn.execute(text("""
                        SELECT table_name 
                        FROM information_schema.tables 
                        WHERE table_name='storage_configs'
                    """))
                    if result.scalar() is None:
                        # Создаем таблицу storage_configs
                        await conn.execute(text("""
                            CREATE TABLE storage_configs (
                                id SERIAL PRIMARY KEY,
                                name VARCHAR(100) NOT NULL UNIQUE,
                                storage_type VARCHAR(20) NOT NULL,
                                config_data JSONB NOT NULL,
                                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                                updated_at TIMESTAMP WITH TIME ZONE,
                                last_check TIMESTAMP WITH TIME ZONE,
                                free_space_gb REAL,
                                total_space_gb REAL,
                                used_space_gb REAL,
                                connection_error TEXT
                            )
                        """))
                        logger.info("[OK] Created storage_configs table")
                    else:
                        logger.info("[OK] Table storage_configs already exists")
            except Exception as e:
                logger.warning(f"[WARNING] Could not create storage_configs table: {e}")
            
            # Добавляем колонки storage_config_id в backup_tasks и postgres_backup_tasks, если их нет
            try:
                from sqlalchemy import text
                async with engine.begin() as conn:
                    # Проверяем и добавляем storage_config_id в backup_tasks
                    result = await conn.execute(text("""
                        SELECT column_name 
                        FROM information_schema.columns 
                        WHERE table_name='backup_tasks' AND column_name='storage_config_id'
                    """))
                    if result.scalar() is None:
                        await conn.execute(text("ALTER TABLE backup_tasks ADD COLUMN storage_config_id INTEGER REFERENCES storage_configs(id)"))
                        logger.info("[OK] Added storage_config_id column to backup_tasks table")
                    
                    # Проверяем и добавляем storage_config_id в postgres_backup_tasks
                    result = await conn.execute(text("""
                        SELECT column_name 
                        FROM information_schema.columns 
                        WHERE table_name='postgres_backup_tasks' AND column_name='storage_config_id'
                    """))
                    if result.scalar() is None:
                        await conn.execute(text("ALTER TABLE postgres_backup_tasks ADD COLUMN storage_config_id INTEGER REFERENCES storage_configs(id)"))
                        logger.info("[OK] Added storage_config_id column to postgres_backup_tasks table")
                    
                    # Добавляем колонку storage_config_id в agents, если её нет
                    result = await conn.execute(text("""
                        SELECT column_name 
                        FROM information_schema.columns 
                        WHERE table_name='agents' AND column_name='storage_config_id'
                    """))
                    if result.scalar() is None:
                        await conn.execute(text("ALTER TABLE agents ADD COLUMN storage_config_id INTEGER"))
                        await conn.execute(text("ALTER TABLE agents ADD CONSTRAINT fk_agent_storage_config FOREIGN KEY (storage_config_id) REFERENCES storage_configs (id) ON DELETE SET NULL"))
                        logger.info("[OK] Added storage_config_id column to agents table")
                    else:
                        logger.info("[OK] Column storage_config_id already exists in agents")
            except Exception as e:
                logger.warning(f"[WARNING] Could not add storage_config_id columns: {e}")
                # Продолжаем работу, даже если колонка не добавлена
            
            # Создаем таблицы для отчетов, если их нет
            try:
                from sqlalchemy import text
                async with engine.begin() as conn:
                    # Проверяем существование таблицы reports
                    result = await conn.execute(text("""
                        SELECT EXISTS (
                            SELECT 1
                            FROM information_schema.tables
                            WHERE table_name = 'reports'
                        );
                    """))
                    if not result.scalar():
                        await conn.execute(text("""
                            CREATE TABLE reports (
                                id SERIAL PRIMARY KEY,
                                name VARCHAR(200) NOT NULL,
                                description TEXT,
                                agent_ids JSONB NOT NULL,
                                postgres_task_ids JSONB NOT NULL,
                                send_to_mattermost BOOLEAN DEFAULT FALSE,
                                enabled BOOLEAN DEFAULT TRUE,
                                schedule_type VARCHAR(50) NOT NULL,
                                schedule_hour INTEGER,
                                schedule_minute INTEGER,
                                schedule_day_of_week INTEGER,
                                schedule_hours_interval INTEGER,
                                last_sent TIMESTAMP WITH TIME ZONE,
                                next_send TIMESTAMP WITH TIME ZONE,
                                created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                                updated_at TIMESTAMP WITH TIME ZONE
                            );
                        """))
                        logger.info("[OK] Created reports table")
                    else:
                        logger.info("[OK] Table reports already exists")
                    
                    # Проверяем существование таблицы report_history
                    result = await conn.execute(text("""
                        SELECT EXISTS (
                            SELECT 1
                            FROM information_schema.tables
                            WHERE table_name = 'report_history'
                        );
                    """))
                    if not result.scalar():
                        await conn.execute(text("""
                            CREATE TABLE report_history (
                                id SERIAL PRIMARY KEY,
                                report_id INTEGER NOT NULL REFERENCES reports(id) ON DELETE CASCADE,
                                sent_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                                status VARCHAR(50) NOT NULL,
                                error_message TEXT,
                                mattermost_response TEXT
                            );
                        """))
                        logger.info("[OK] Created report_history table")
                    else:
                        logger.info("[OK] Table report_history already exists")
            except Exception as e:
                logger.warning(f"[WARNING] Could not create reports tables: {e}")
            
            # Создаем администратора по умолчанию, если его нет
            try:
                async with async_session_maker() as session:
                    from sqlalchemy import select
                    result = await session.execute(select(User).where(User.username == "admin"))
                    admin = result.scalar_one_or_none()
                    
                    if not admin:
                        admin = User(
                            username="admin",
                            email="admin@example.com",
                            password_hash=get_password_hash("admin123"),
                            is_admin=True
                        )
                        session.add(admin)
                        await session.commit()
                        logger.info("[OK] Default admin created (admin / admin123)")
                    else:
                        logger.info("[OK] Admin user exists")
                        # Проверяем, что пароль правильный (обновляем если нужно)
                        from utils import verify_password
                        try:
                            if not verify_password("admin123", admin.password_hash):
                                logger.info("[INFO] Admin password doesn't match default, resetting...")
                                admin.password_hash = get_password_hash("admin123")
                                await session.commit()
                                logger.info("[OK] Admin password reset to default (admin123)")
                        except Exception as e:
                            logger.warning(f"[WARNING] Could not verify admin password: {e}, resetting...")
                            admin.password_hash = get_password_hash("admin123")
                            await session.commit()
                            logger.info("[OK] Admin password reset to default (admin123)")
            except Exception as e:
                logger.error(f"[ERROR] Failed to create/check admin user: {e}")
                import traceback
                traceback.print_exc()
                logger.warning("[WARNING] You can create admin manually by running: python create_admin.py")
                
                # Создаем настройки по умолчанию
                result = await session.execute(select(Settings))
                app_settings = result.scalar_one_or_none()
                if not app_settings:
                    app_settings = Settings(
                        mattermost_enabled=False,
                        mattermost_daily_report=False,
                        mattermost_report_time="09:00",
                        agent_poll_interval=settings.agent_poll_interval,
                        s3_check_interval=settings.s3_check_interval
                    )
                    session.add(app_settings)
                    await session.commit()
                    logger.info("[OK] Default settings created")
                    
        except Exception as e:
            logger.error(f"[ERROR] Database initialization failed: {e}")
            import traceback
            traceback.print_exc()
        
        # Запускаем периодические задачи
        logger.info("[INFO] Starting background tasks...")
        
        # Запускаем опрос агентов
        agent_poller = AgentPoller(poll_interval=settings.agent_poll_interval)
        asyncio.create_task(agent_poller.start())
        logger.info(f"[OK] Agent poller started (interval: {settings.agent_poll_interval}s)")
        
        # Запускаем проверку S3
        s3_checker = S3Checker(check_interval=settings.s3_check_interval)
        asyncio.create_task(s3_checker.start())
        logger.info(f"[OK] S3 checker started (interval: {settings.s3_check_interval}s)")
        
        # Запускаем генератор ежедневных отчетов
        daily_report = DailyReportGenerator()
        asyncio.create_task(daily_report.start())
        logger.info("[OK] Daily report generator started")
        
        # Запускаем планировщик PostgreSQL бэкапов
        postgres_scheduler = PostgresBackupScheduler()
        asyncio.create_task(postgres_scheduler.start())
        logger.info("[OK] PostgreSQL backup scheduler started")
        
        # Запускаем планировщик отчетов
        report_scheduler = ReportScheduler()
        asyncio.create_task(report_scheduler.start())
        logger.info("[OK] Report scheduler started")
        
        logger.info("=" * 60)
        logger.info("[SUCCESS] Application ready!")
        logger.info(f"[INFO] URL: http://{settings.server_host}:{settings.server_port}")
        logger.info("=" * 60)
        
    except Exception as e:
        logger.error(f"[ERROR] Startup error: {e}")
        import traceback
        traceback.print_exc()
    
    yield
    
    # Shutdown
    logger.info("[SHUTDOWN] Stopping application...")
    await engine.dispose()
    logger.info("[OK] Application stopped")


# Создаем приложение FastAPI
app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description="Сервер резервного копирования отдела развития инженерных практик",
    lifespan=lifespan
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Статические файлы
app.mount("/static", StaticFiles(directory="static"), name="static")
app.mount("/templates", StaticFiles(directory="templates"), name="templates")

# Подключаем роуты
app.include_router(api_router, prefix="/api", tags=["API"])
app.include_router(web_router, tags=["Web"])


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host=settings.server_host,
        port=settings.server_port,
        reload=settings.debug
    )

