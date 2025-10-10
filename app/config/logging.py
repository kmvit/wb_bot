"""Настройка логирования"""

import sys
from loguru import logger
from app.config.settings import settings


def setup_logging():
    """Настроить систему логирования"""
    # Удаляем стандартный обработчик loguru
    logger.remove()
    
    # Добавляем обработчик для консоли
    logger.add(
        sys.stdout,
        format=settings.LOG_FORMAT,
        level=settings.LOG_LEVEL,
        colorize=True
    )
    
    # Добавляем обработчик для файла (если не в режиме разработки)
    if not settings.DEBUG:
        logger.add(
            "logs/bot.log",
            format=settings.LOG_FORMAT,
            level=settings.LOG_LEVEL,
            rotation="1 day",
            retention="30 days",
            compression="gz"
        )
    
    logger.info("Logging configured successfully")

