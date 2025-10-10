"""AES-256 шифрование данных для безопасного хранения API токенов"""

import base64
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from loguru import logger

from app.config.settings import settings


class EncryptionService:
    """Сервис для шифрования/расшифровки токенов"""
    
    def __init__(self):
        self._fernet = None
        self._initialize_encryption()
    
    def _initialize_encryption(self):
        """Инициализация шифрования"""
        try:
            # Декодируем ключ из base64
            key_bytes = base64.b64decode(settings.ENCRYPTION_KEY.encode())
            
            if len(key_bytes) != 32:
                raise ValueError("Encryption key must be 32 bytes")
            
            # Создаем Fernet объект
            self._fernet = Fernet(base64.urlsafe_b64encode(key_bytes))
            logger.info("Encryption service initialized successfully")
            
        except Exception as e:
            logger.error(f"Failed to initialize encryption: {e}")
            raise
    
    def encrypt_token(self, token: str) -> str:
        """Зашифровать API токен"""
        try:
            if not self._fernet:
                raise ValueError("Encryption not initialized")
            
            # Шифруем токен
            encrypted_bytes = self._fernet.encrypt(token.encode('utf-8'))
            
            # Возвращаем в base64 для хранения в БД
            return base64.b64encode(encrypted_bytes).decode('utf-8')
            
        except Exception as e:
            logger.error(f"Failed to encrypt token: {e}")
            raise
    
    def decrypt_token(self, encrypted_token: str) -> str:
        """Расшифровать API токен"""
        try:
            if not self._fernet:
                raise ValueError("Encryption not initialized")
            
            # Декодируем из base64
            encrypted_bytes = base64.b64decode(encrypted_token.encode('utf-8'))
            
            # Расшифровываем
            decrypted_bytes = self._fernet.decrypt(encrypted_bytes)
            
            return decrypted_bytes.decode('utf-8')
            
        except Exception as e:
            logger.error(f"Failed to decrypt token: {e}")
            raise
    
    @staticmethod
    def generate_key() -> str:
        """Сгенерировать новый ключ шифрования (32 байта в base64)"""
        key = Fernet.generate_key()
        # Извлекаем 32 байта из ключа Fernet
        key_bytes = base64.urlsafe_b64decode(key)[:32]
        return base64.b64encode(key_bytes).decode('utf-8')


# Глобальный экземпляр сервиса шифрования
encryption_service = None

def get_encryption_service():
    """Получить экземпляр сервиса шифрования (ленивая инициализация)"""
    global encryption_service
    if encryption_service is None:
        encryption_service = EncryptionService()
    return encryption_service

