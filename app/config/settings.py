"""Настройки приложения"""

import os
from typing import Optional
from pydantic import Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Основные настройки приложения"""
    
    # Telegram Bot
    BOT_TOKEN: str = Field(..., description="Токен Telegram бота")
    WEBHOOK_URL: Optional[str] = Field(None, description="URL для webhook")
    WEBHOOK_PATH: str = Field("/webhook", description="Путь для webhook")
    WEBHOOK_HOST: str = Field("0.0.0.0", description="Хост для webhook")
    WEBHOOK_PORT: int = Field(8080, description="Порт для webhook")
    
    # База данных
    DATABASE_URL: str = Field(..., description="URL подключения к PostgreSQL")
    DB_ECHO: bool = Field(False, description="Логирование SQL запросов")
    
    # Redis
    REDIS_URL: str = Field("redis://localhost:6379/0", description="URL подключения к Redis")
    
    # Шифрование
    ENCRYPTION_KEY: str = Field(..., description="Ключ для AES-256 шифрования (32 байта в base64)")
    
    # Wildberries API (обновлённые URL с 30.01.2025)
    WB_CONTENT_API_URL: str = Field("https://content-api.wildberries.ru", description="URL Content API")
    WB_MARKETPLACE_API_URL: str = Field("https://marketplace-api.wildberries.ru", description="URL Marketplace API")
    WB_STATISTICS_API_URL: str = Field("https://statistics-api.wildberries.ru", description="URL Statistics API")
    WB_COMMON_API_URL: str = Field("https://common-api.wildberries.ru", description="URL Common API")
    WB_SUPPLIES_API_URL: str = Field("https://supplies-api.wildberries.ru", description="URL Supplies API")
    
    # Мониторинг слотов (согласно лимитам WB API: 6 запросов/мин, интервал 10 сек)
    SLOT_CHECK_INTERVAL: float = Field(12.0, description="Интервал проверки слотов в секундах")
    MAX_CONCURRENT_CHECKS: int = Field(100, description="Максимальное количество одновременных проверок")
    
    # Безопасность
    RATE_LIMIT_PER_MINUTE: int = Field(60, description="Лимит запросов в минуту на пользователя")
    MAX_API_TOKENS_PER_USER: int = Field(5, description="Максимальное количество API токенов на пользователя")
    
    # Администраторы
    ADMIN_IDS: str = Field("", description="ID администраторов через запятую")
    
    # Браузер
    WB_BROWSER_PROFILES_DIR: str = Field("~/.wb_bot_browser_profiles", description="Директория для профилей браузера")
    
    # Логирование
    LOG_LEVEL: str = Field("INFO", description="Уровень логирования")
    LOG_FORMAT: str = Field(
        "{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} - {message}",
        description="Формат логов"
    )
    
    # Режим разработки
    DEBUG: bool = Field(False, description="Режим отладки")
    ENVIRONMENT: str = Field("production", description="Окружение (development/production)")
    
    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = True
        extra = "ignore"  # Игнорируем дополнительные переменные окружения


# Создаем глобальный экземпляр настроек
settings = Settings()


def get_database_url() -> str:
    """Получить URL базы данных для SQLAlchemy"""
    return settings.DATABASE_URL


def is_development() -> bool:
    """Проверить, запущено ли приложение в режиме разработки"""
    return settings.ENVIRONMENT == "development" or settings.DEBUG


def get_webhook_url() -> Optional[str]:
    """Получить полный URL для webhook"""
    if settings.WEBHOOK_URL:
        return f"{settings.WEBHOOK_URL}{settings.WEBHOOK_PATH}"
    return None


def get_admin_ids() -> list[int]:
    """Получить список ID администраторов"""
    if not settings.ADMIN_IDS:
        return []
    
    try:
        return [int(admin_id.strip()) for admin_id in settings.ADMIN_IDS.split(',') if admin_id.strip()]
    except ValueError:
        return []


def is_admin(user_id: int) -> bool:
    """Проверить, является ли пользователь администратором"""
    return user_id in get_admin_ids()
