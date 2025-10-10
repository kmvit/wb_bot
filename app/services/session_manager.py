"""Менеджер сессий для автоматического обновления истекших сессий"""

import asyncio
from datetime import datetime, timedelta
from typing import Dict, Optional, Tuple
from loguru import logger

from app.database.database import AsyncSessionLocal
from app.database.repositories.user_repo import UserRepository
from app.services.wb_web_auth import WBWebAuthService, WBWebAuthError


class SessionManager:
    """Менеджер для управления сессиями пользователей"""
    
    def __init__(self):
        self.wb_auth_service = WBWebAuthService()
        self._session_cache: Dict[int, Dict] = {}  # Кэш активных сессий
        self._last_check: Dict[int, datetime] = {}  # Время последней проверки
    
    async def get_valid_session(self, user_id: int) -> Optional[Dict]:
        """Получить валидную сессию пользователя с автоматическим обновлением"""
        try:
            # Проверяем кэш
            if user_id in self._session_cache:
                cached_session = self._session_cache[user_id]
                if await self._is_session_recent(user_id):
                    logger.info(f"Using cached session for user {user_id}")
                    return cached_session
            
            # Получаем сессию из БД
            async with AsyncSessionLocal() as session:
                user_repo = UserRepository(session)
                user = await user_repo.get_by_telegram_id(user_id)
                
                if not user or not user.has_phone_auth():
                    logger.warning(f"User {user_id} has no phone auth")
                    return None
                
                session_data = await user_repo.get_phone_auth_session(user)
                if not session_data:
                    logger.warning(f"No session data for user {user_id}")
                    return None
                
                # Проверяем валидность сессии
                if await self._test_session_validity(session_data):
                    logger.info(f"Session is valid for user {user_id}")
                    # Обновляем кэш
                    self._session_cache[user_id] = session_data
                    self._last_check[user_id] = datetime.now()
                    return session_data
                else:
                    logger.warning(f"Session expired for user {user_id}, attempting refresh")
                    return await self._refresh_user_session(user_id, user)
        
        except Exception as e:
            logger.error(f"Error getting valid session for user {user_id}: {e}")
            return None
    
    async def _is_session_recent(self, user_id: int) -> bool:
        """Проверить, недавно ли проверялась сессия"""
        # Проверяем сессию не чаще чем раз в 5 минут
        last_check = self._last_check.get(user_id, datetime.min)
        return datetime.now() - last_check < timedelta(minutes=5)
    
    async def _test_session_validity(self, session_data: Dict) -> bool:
        """Проверить валидность сессии"""
        try:
            return await self.wb_auth_service.test_session(session_data)
        except Exception as e:
            logger.error(f"Error testing session validity: {e}")
            return False
    
    async def _refresh_user_session(self, user_id: int, user) -> Optional[Dict]:
        """Обновить сессию пользователя"""
        try:
            logger.info(f"Attempting to refresh session for user {user_id}")
            
            # Здесь можно реализовать автоматическое обновление
            # Например, через повторную авторизацию или refresh token
            
            # Пока что просто удаляем истекшую сессию
            async with AsyncSessionLocal() as session:
                user_repo = UserRepository(session)
                await user_repo.remove_phone_auth(user)
                await session.commit()
            
            # Удаляем из кэша
            self._session_cache.pop(user_id, None)
            self._last_check.pop(user_id, None)
            
            logger.info(f"Cleared expired session for user {user_id}")
            return None
            
        except Exception as e:
            logger.error(f"Error refreshing session for user {user_id}: {e}")
            return None
    
    async def handle_auth_error(self, user_id: int) -> Optional[Dict]:
        """Обработать ошибку авторизации"""
        try:
            logger.info(f"Handling auth error for user {user_id}")
            
            # Очищаем кэш
            self._session_cache.pop(user_id, None)
            self._last_check.pop(user_id, None)
            
            # Очищаем сессию в БД
            async with AsyncSessionLocal() as session:
                user_repo = UserRepository(session)
                user = await user_repo.get_by_telegram_id(user_id)
                if user:
                    await user_repo.remove_phone_auth(user)
                    await session.commit()
            
            logger.info(f"Cleared invalid session for user {user_id}")
            return None
            
        except Exception as e:
            logger.error(f"Error handling auth error for user {user_id}: {e}")
            return None
    
    async def cleanup_expired_sessions(self):
        """Периодическая очистка истекших сессий"""
        try:
            current_time = datetime.now()
            expired_users = []
            
            for user_id, last_check in self._last_check.items():
                if current_time - last_check > timedelta(hours=1):
                    expired_users.append(user_id)
            
            for user_id in expired_users:
                self._session_cache.pop(user_id, None)
                self._last_check.pop(user_id, None)
                logger.info(f"Cleaned up expired session cache for user {user_id}")
                
        except Exception as e:
            logger.error(f"Error cleaning up expired sessions: {e}")


# Глобальный экземпляр менеджера сессий
session_manager = SessionManager()


async def get_valid_session_for_user(user_id: int) -> Optional[Dict]:
    """Получить валидную сессию для пользователя"""
    return await session_manager.get_valid_session(user_id)


async def handle_session_auth_error(user_id: int) -> Optional[Dict]:
    """Обработать ошибку авторизации сессии"""
    return await session_manager.handle_auth_error(user_id)
