"""
Скрипт для создания администратора в базе данных
"""
import asyncio
from sqlalchemy import select
from database import async_session_maker, engine
from models import Base, User
from utils import get_password_hash
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def create_admin():
    """Создает администратора в базе данных"""
    try:
        # Создаем таблицы если их нет
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        logger.info("[OK] Database tables created/verified")
        
        # Создаем администратора
        async with async_session_maker() as session:
            # Проверяем существование
            result = await session.execute(select(User).where(User.username == "admin"))
            admin = result.scalar_one_or_none()
            
            if admin:
                # Обновляем пароль
                admin.password_hash = get_password_hash("admin123")
                admin.is_admin = True
                await session.commit()
                logger.info("[OK] Admin user password updated (admin / admin123)")
            else:
                # Создаем нового
                admin = User(
                    username="admin",
                    email="admin@example.com",
                    password_hash=get_password_hash("admin123"),
                    is_admin=True
                )
                session.add(admin)
                await session.commit()
                logger.info("[OK] Admin user created (admin / admin123)")
        
        logger.info("=" * 60)
        logger.info("SUCCESS! Admin user is ready:")
        logger.info("Username: admin")
        logger.info("Password: admin123")
        logger.info("=" * 60)
        
    except Exception as e:
        logger.error(f"[ERROR] Failed to create admin: {e}")
        import traceback
        traceback.print_exc()
        raise
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(create_admin())





