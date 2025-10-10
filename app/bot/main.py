"""Точка входа бота"""

import asyncio
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from loguru import logger

from app.config.settings import settings
from app.config.logging import setup_logging
from app.database.database import init_database, AsyncSessionLocal
from app.database.repositories.slot_monitoring_repo import SlotMonitoringRepository
from app.database.models import MonitoringStatus
from app.bot.handlers.auth import auth_router
from app.bot.handlers.cabinet import cabinet_router
from app.bot.handlers.monitoring import monitoring_router
from app.services.slot_monitor import get_slot_monitor_service
from app.services.session_manager import session_manager


async def clear_all_active_monitorings():
    """Очистить все активные мониторинги при запуске бота"""
    try:
        logger.info("🧹 Clearing all active monitorings on bot startup...")
        
        async with AsyncSessionLocal() as session:
            slot_repo = SlotMonitoringRepository(session)
            
            # Получаем все активные мониторинги
            active_monitorings = await slot_repo.get_all_active_monitorings()
            
            if not active_monitorings:
                logger.info("✅ No active monitorings found")
                return None
            
            logger.info(f"📊 Found {len(active_monitorings)} active monitorings to clear")
            
            # Группируем мониторинги по пользователям для отправки уведомлений
            user_monitorings = {}
            for monitoring in active_monitorings:
                user_id = monitoring.user.telegram_id
                if user_id not in user_monitorings:
                    user_monitorings[user_id] = []
                user_monitorings[user_id].append(monitoring)
            
            # Останавливаем все активные мониторинги
            cleared_count = 0
            for monitoring in active_monitorings:
                try:
                    # Обновляем статус на "stopped"
                    success = await slot_repo.update_monitoring_status(
                        monitoring.id, 
                        MonitoringStatus.STOPPED
                    )
                    
                    if success:
                        cleared_count += 1
                        logger.info(f"✅ Stopped monitoring #{monitoring.id} for user {monitoring.user.telegram_id}")
                    else:
                        logger.warning(f"⚠️ Failed to stop monitoring #{monitoring.id}")
                        
                except Exception as e:
                    logger.error(f"❌ Error stopping monitoring #{monitoring.id}: {e}")
            
            # Сохраняем изменения в базе данных
            await session.commit()
            
            logger.info(f"🎯 Successfully cleared {cleared_count}/{len(active_monitorings)} active monitorings")
            
            # Возвращаем информацию о пользователях для отправки уведомлений
            return user_monitorings
            
    except Exception as e:
        logger.error(f"❌ Error clearing active monitorings: {e}")
        return None


async def notify_users_about_cleared_monitorings(bot, user_monitorings: dict):
    """Уведомить пользователей об остановленных мониторингах"""
    try:
        for user_id, monitorings in user_monitorings.items():
            try:
                # Формируем сообщение для пользователя
                if len(monitorings) == 1:
                    message = f"""
🔄 <b>Мониторинг остановлен</b>

📊 Мониторинг #{monitorings[0].id} был остановлен при перезапуске бота.

💡 <b>Что делать:</b>
• Создайте новый мониторинг через автобронирование
• Все настройки сохранились в базе данных
                    """
                else:
                    monitoring_ids = [str(m.id) for m in monitorings]
                    message = f"""
🔄 <b>Мониторинги остановлены</b>

📊 Остановлено мониторингов: {len(monitorings)}
🆔 ID мониторингов: {', '.join(monitoring_ids)}

💡 <b>Что делать:</b>
• Создайте новые мониторинги через автобронирование
• Все настройки сохранились в базе данных
                    """
                
                # Отправляем уведомление
                await bot.send_message(
                    chat_id=user_id,
                    text=message,
                    parse_mode="HTML"
                )
                
                logger.info(f"📤 Sent restart notification to user {user_id}")
                
            except Exception as e:
                logger.error(f"❌ Error sending notification to user {user_id}: {e}")
                
    except Exception as e:
        logger.error(f"❌ Error notifying users about cleared monitorings: {e}")


async def main():
    """Главная функция запуска бота"""
    # Настраиваем логирование
    setup_logging()
    
    logger.info("Starting Wildberries Bot...")
    
    # Инициализируем базу данных
    await init_database()
    
    # Очищаем все активные мониторинги при запуске
    user_monitorings = await clear_all_active_monitorings()
    
    # Создаем бота и диспетчер
    bot = Bot(
        token=settings.BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML)
    )
    
    # Отправляем уведомления пользователям об остановленных мониторингах
    if user_monitorings:
        await notify_users_about_cleared_monitorings(bot, user_monitorings)
    
    dp = Dispatcher()
    
    # Подключаем роутеры
    dp.include_router(auth_router)
    dp.include_router(cabinet_router)
    dp.include_router(monitoring_router)
    
    try:
        # Запускаем сервис мониторинга слотов
        slot_monitor = get_slot_monitor_service(bot)
        await slot_monitor.start_monitoring()
        
        # Запускаем периодическую очистку сессий
        session_cleanup_task = asyncio.create_task(periodic_session_cleanup())
        
        # Запускаем бота
        logger.info("Bot started successfully")
        await dp.start_polling(bot)
    except Exception as e:
        logger.error(f"Error running bot: {e}")
    finally:
        # Останавливаем сервис мониторинга
        slot_monitor = get_slot_monitor_service(bot)
        await slot_monitor.stop_monitoring()
        
        # Останавливаем очистку сессий
        if 'session_cleanup_task' in locals():
            session_cleanup_task.cancel()
            try:
                await session_cleanup_task
            except asyncio.CancelledError:
                pass
        
        await bot.session.close()


async def periodic_session_cleanup():
    """Периодическая очистка истекших сессий"""
    while True:
        try:
            await asyncio.sleep(3600)  # Каждый час
            await session_manager.cleanup_expired_sessions()
            logger.info("Session cleanup completed")
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Error during session cleanup: {e}")


if __name__ == "__main__":
    asyncio.run(main())

