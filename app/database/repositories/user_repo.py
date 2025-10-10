"""Репозиторий для работы с пользователями"""

import json
from datetime import datetime
from typing import Optional, Dict, Any, List
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from loguru import logger

from app.database.models import User
from app.utils.encryption import get_encryption_service


class UserRepository:
    """Репозиторий для работы с пользователями"""
    
    def __init__(self, session: AsyncSession):
        self.session = session
    
    async def get_by_telegram_id(self, telegram_id: int) -> Optional[User]:
        """Получить пользователя по Telegram ID"""
        try:
            result = await self.session.execute(
                select(User).where(User.telegram_id == telegram_id)
            )
            return result.scalar_one_or_none()
        except Exception as e:
            logger.error(f"Error getting user by telegram_id {telegram_id}: {e}")
            return None
    
    async def create_user(
        self, 
        telegram_id: int, 
        username: str = None, 
        first_name: str = None, 
        last_name: str = None
    ) -> User:
        """Создать нового пользователя"""
        try:
            user = User(
                telegram_id=telegram_id,
                username=username,
                first_name=first_name,
                last_name=last_name
            )
            
            self.session.add(user)
            await self.session.commit()
            await self.session.refresh(user)
            
            logger.info(f"Created new user: {telegram_id}")
            return user
            
        except Exception as e:
            await self.session.rollback()
            logger.error(f"Error creating user {telegram_id}: {e}")
            raise
    
    async def get_or_create_user(
        self, 
        telegram_id: int, 
        username: str = None, 
        first_name: str = None, 
        last_name: str = None
    ) -> User:
        """Получить пользователя или создать, если не существует"""
        user = await self.get_by_telegram_id(telegram_id)
        
        if user is None:
            user = await self.create_user(telegram_id, username, first_name, last_name)
        else:
            # Обновляем информацию пользователя, если она изменилась
            updated = False
            if user.username != username:
                user.username = username
                updated = True
            if user.first_name != first_name:
                user.first_name = first_name
                updated = True
            if user.last_name != last_name:
                user.last_name = last_name
                updated = True
            
            if updated:
                user.updated_at = datetime.utcnow()
                await self.session.commit()
                logger.info(f"Updated user info: {telegram_id}")
        
        return user
    
    async def save_wb_token(self, user: User, wb_token: str) -> None:
        """Сохранить WB токен для пользователя"""
        try:
            # Шифруем токен
            encryption_service = get_encryption_service()
            encrypted_token = encryption_service.encrypt_token(wb_token)
            
            # Сохраняем в БД
            user.encrypted_wb_token = encrypted_token
            user.wb_token_created_at = datetime.utcnow()
            user.wb_token_last_used_at = datetime.utcnow()
            user.updated_at = datetime.utcnow()
            
            await self.session.commit()
            logger.info(f"Saved WB token for user: {user.telegram_id}")
            
        except Exception as e:
            await self.session.rollback()
            logger.error(f"Error saving WB token for user {user.telegram_id}: {e}")
            raise
    
    async def get_wb_token(self, user: User) -> Optional[str]:
        """Получить расшифрованный WB токен пользователя"""
        try:
            if not user.encrypted_wb_token:
                return None
            
            # Расшифровываем токен
            encryption_service = get_encryption_service()
            decrypted_token = encryption_service.decrypt_token(user.encrypted_wb_token)
            
            # Обновляем время последнего использования
            user.wb_token_last_used_at = datetime.utcnow()
            await self.session.commit()
            
            return decrypted_token
            
        except Exception as e:
            logger.error(f"Error getting WB token for user {user.telegram_id}: {e}")
            return None
    
    async def remove_wb_token(self, user: User) -> None:
        """Удалить WB токен пользователя"""
        try:
            user.encrypted_wb_token = None
            user.wb_token_created_at = None
            user.wb_token_last_used_at = None
            user.updated_at = datetime.utcnow()
            
            await self.session.commit()
            logger.info(f"Removed WB token for user: {user.telegram_id}")
            
        except Exception as e:
            await self.session.rollback()
            logger.error(f"Error removing WB token for user {user.telegram_id}: {e}")
            raise
    
    async def save_phone_auth(self, user: User, auth_data: Dict[str, Any]) -> None:
        """Сохранить данные авторизации по телефону для пользователя"""
        try:
            # Шифруем данные сессии
            encryption_service = get_encryption_service()
            encrypted_session = encryption_service.encrypt_token(json.dumps(auth_data['session_data']))
            
            # Сохраняем в БД
            user.phone_number = auth_data['phone_number']
            user.encrypted_wb_session = encrypted_session
            user.wb_inn = auth_data['inn']
            user.wb_seller_name = auth_data['seller_name']
            user.phone_auth_created_at = datetime.utcnow()
            user.phone_auth_last_used_at = datetime.utcnow()
            user.updated_at = datetime.utcnow()
            
            await self.session.commit()
            logger.info(f"Saved phone auth for user: {user.telegram_id} with INN: {auth_data['inn']}")
            
        except Exception as e:
            await self.session.rollback()
            logger.error(f"Error saving phone auth for user {user.telegram_id}: {e}")
            raise
    
    async def get_phone_auth_session(self, user: User) -> Optional[Dict[str, Any]]:
        """Получить расшифрованные данные сессии авторизации по телефону"""
        try:
            if not user.encrypted_wb_session:
                return None
            
            # Расшифровываем данные сессии
            encryption_service = get_encryption_service()
            decrypted_session = encryption_service.decrypt_token(user.encrypted_wb_session)
            
            # Парсим JSON
            session_data = json.loads(decrypted_session)
            
            # Обновляем время последнего использования
            user.phone_auth_last_used_at = datetime.utcnow()
            await self.session.commit()
            
            return session_data
            
        except Exception as e:
            logger.error(f"Error getting phone auth session for user {user.telegram_id}: {e}")
            return None
    
    async def remove_phone_auth(self, user: User) -> None:
        """Удалить данные авторизации по телефону пользователя"""
        try:
            user.phone_number = None
            user.encrypted_wb_session = None
            user.wb_inn = None
            user.wb_seller_name = None
            user.phone_auth_created_at = None
            user.phone_auth_last_used_at = None
            user.updated_at = datetime.utcnow()
            
            await self.session.commit()
            logger.info(f"Removed phone auth for user: {user.telegram_id}")
            
        except Exception as e:
            await self.session.rollback()
            logger.error(f"Error removing phone auth for user {user.telegram_id}: {e}")
            raise
    
    async def get_phone_auth_info(self, user: User) -> Optional[Dict[str, Any]]:
        """Получить информацию об авторизации по телефону (без сессии)"""
        try:
            if not user.has_phone_auth():
                return None
            
            return {
                'phone_number': user.phone_number,
                'inn': user.wb_inn,
                'seller_name': user.wb_seller_name,
                'created_at': user.phone_auth_created_at,
                'last_used_at': user.phone_auth_last_used_at
            }
            
        except Exception as e:
            logger.error(f"Error getting phone auth info for user {user.telegram_id}: {e}")
            return None
    
    async def get_all_users(self) -> List[User]:
        """Получить всех пользователей"""
        try:
            result = await self.session.execute(
                select(User).order_by(User.created_at.desc())
            )
            return result.scalars().all()
        except Exception as e:
            logger.error(f"Error getting all users: {e}")
            return []

    async def delete_user(self, telegram_id: int) -> bool:
        """Удалить пользователя по Telegram ID (каскадное удаление)"""
        try:
            from sqlalchemy import delete
            
            # Удаляем пользователя (каскадное удаление удалит все связанные данные)
            result = await self.session.execute(
                delete(User).where(User.telegram_id == telegram_id)
            )
            
            if result.rowcount > 0:
                await self.session.commit()
                logger.info(f"Deleted user with telegram_id: {telegram_id}")
                return True
            else:
                logger.warning(f"User with telegram_id {telegram_id} not found")
                return False
                
        except Exception as e:
            await self.session.rollback()
            logger.error(f"Error deleting user {telegram_id}: {e}")
            return False