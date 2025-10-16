#!/usr/bin/env python3
"""
Скрипт для сброса авторизации пользователей
Использование: python reset_auth.py [telegram_id]
Если telegram_id не указан, сбрасывает авторизацию всех пользователей
"""

import asyncio
import sys
from pathlib import Path

# Добавляем корневую директорию проекта в путь
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

from app.database.database import AsyncSessionLocal
from app.database.repositories.user_repo import UserRepository
from app.services.wb_web_auth import cleanup_wb_auth_service
from loguru import logger


async def reset_user_auth(telegram_id: int):
    """Сбросить авторизацию конкретного пользователя"""
    try:
        async with AsyncSessionLocal() as session:
            user_repo = UserRepository(session)
            user = await user_repo.get_by_telegram_id(telegram_id)
            
            if not user:
                logger.error(f"❌ Пользователь с ID {telegram_id} не найден")
                return False
            
            if not user.has_phone_auth():
                logger.info(f"ℹ️ Пользователь {telegram_id} не имеет авторизации по телефону")
                return True
            
            # Удаляем авторизацию по телефону
            await user_repo.remove_phone_auth(user)
            
            # Очищаем сервис авторизации
            await cleanup_wb_auth_service(telegram_id)
            
            logger.info(f"✅ Авторизация пользователя {telegram_id} успешно сброшена")
            return True
            
    except Exception as e:
        logger.error(f"❌ Ошибка при сбросе авторизации пользователя {telegram_id}: {e}")
        return False


async def reset_all_auth():
    """Сбросить авторизацию всех пользователей"""
    try:
        async with AsyncSessionLocal() as session:
            user_repo = UserRepository(session)
            users = await user_repo.get_all_users()
            
            reset_count = 0
            for user in users:
                if user.has_phone_auth():
                    await user_repo.remove_phone_auth(user)
                    await cleanup_wb_auth_service(user.telegram_id)
                    reset_count += 1
                    logger.info(f"✅ Сброшена авторизация пользователя {user.telegram_id}")
            
            logger.info(f"🎉 Сброшена авторизация {reset_count} пользователей")
            return True
            
    except Exception as e:
        logger.error(f"❌ Ошибка при сбросе авторизации всех пользователей: {e}")
        return False


async def main():
    """Основная функция"""
    if len(sys.argv) > 1:
        # Сброс авторизации конкретного пользователя
        try:
            telegram_id = int(sys.argv[1])
            await reset_user_auth(telegram_id)
        except ValueError:
            logger.error("❌ Неверный формат Telegram ID. Используйте число.")
            return
    else:
        # Сброс авторизации всех пользователей
        logger.info("🔄 Сброс авторизации всех пользователей...")
        await reset_all_auth()


if __name__ == "__main__":
    asyncio.run(main())
