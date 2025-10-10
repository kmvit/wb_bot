"""SQLAlchemy модели базы данных"""

from datetime import datetime
from typing import Optional
from sqlalchemy import Column, Integer, String, DateTime, Text, Boolean, Float, ForeignKey, JSON
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from enum import Enum as PyEnum

Base = declarative_base()


class MonitoringStatus(PyEnum):
    """Статусы мониторинга"""
    ACTIVE = "active"
    PAUSED = "paused"
    STOPPED = "stopped"
    COMPLETED = "completed"


class User(Base):
    """Модель пользователя"""
    __tablename__ = 'users'
    
    id = Column(Integer, primary_key=True, index=True)
    telegram_id = Column(Integer, unique=True, nullable=False, index=True)
    username = Column(String(255), nullable=True)
    first_name = Column(String(255), nullable=True)
    last_name = Column(String(255), nullable=True)
    
    # Зашифрованный API токен Wildberries
    encrypted_wb_token = Column(Text, nullable=True)
    wb_token_created_at = Column(DateTime, nullable=True)
    wb_token_last_used_at = Column(DateTime, nullable=True)
    
    # Авторизация по номеру телефона
    phone_number = Column(String(20), nullable=True)  # Номер телефона
    encrypted_wb_session = Column(Text, nullable=True)  # Зашифрованная сессия WB
    wb_inn = Column(String(20), nullable=True)  # ИНН из кабинета WB
    wb_seller_name = Column(String(255), nullable=True)  # Название продавца
    phone_auth_created_at = Column(DateTime, nullable=True)
    phone_auth_last_used_at = Column(DateTime, nullable=True)
    
    # Настройки пользователя
    is_active = Column(Boolean, default=True)
    
    # Временные метки
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Связи
    slot_monitorings = relationship("SlotMonitoring", back_populates="user", cascade="all, delete-orphan")
    
    def has_wb_token(self) -> bool:
        """Проверить, есть ли у пользователя API токен"""
        return self.encrypted_wb_token is not None and len(self.encrypted_wb_token.strip()) > 0
    
    def has_phone_auth(self) -> bool:
        """Проверить, есть ли у пользователя авторизация по телефону"""
        return self.encrypted_wb_session is not None and len(self.encrypted_wb_session.strip()) > 0
    
    def __repr__(self):
        return f"<User(telegram_id={self.telegram_id}, username={self.username})>"


class SlotMonitoring(Base):
    """Модель мониторинга слотов"""
    __tablename__ = 'slot_monitoring'
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False, index=True)
    
    # Параметры мониторинга
    coefficient_min = Column(Float, nullable=False, default=1.0)  # Минимальный коэффициент
    coefficient_max = Column(Float, nullable=False, default=5.0)  # Максимальный коэффициент
    
    # Логистическое плечо (дни на доставку)
    logistics_shoulder = Column(Integer, nullable=False, default=0)  # Дни на доставку товара на склад
    
    # Тип упаковки
    box_type_id = Column(Integer, nullable=True)  # ID типа поставки (2-Короба, 5-Монопаллеты, 6-Суперсейф)
    
    # Настройки приемки
    acceptance_options = Column(JSON, nullable=True)  # Опции приемки в JSON
    warehouse_ids = Column(JSON, nullable=False)  # Список ID складов для мониторинга
    
    # Номер заказа для автобронирования
    order_number = Column(String(50), nullable=True)  # Номер заказа, для которого создается мониторинг
    
    # Статус мониторинга
    status = Column(String(20), nullable=False, default=MonitoringStatus.ACTIVE.value)
    
    # Временные рамки
    date_from = Column(DateTime, nullable=True)  # С какой даты искать слоты
    date_to = Column(DateTime, nullable=True)    # До какой даты искать слоты
    
    # Результаты
    last_check_at = Column(DateTime, nullable=True)  # Время последней проверки
    
    # Отслеживание неудачных дат для автобронирования
    failed_booking_dates = Column(JSON, nullable=True)  # Список дат, на которые не удалось забронировать слот
    
    # Временные метки
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Связи
    user = relationship("User", back_populates="slot_monitorings")
    
    def __repr__(self):
        return f"<SlotMonitoring(id={self.id}, user_id={self.user_id}, status={self.status})>"



class Warehouse(Base):
    """Модель склада для кэширования"""
    __tablename__ = 'warehouses'
    
    id = Column(Integer, primary_key=True, index=True)
    wb_warehouse_id = Column(Integer, unique=True, nullable=False, index=True)  # ID склада в WB
    name = Column(String(255), nullable=False)
    address = Column(Text, nullable=True)
    
    # Информация о складе
    is_active = Column(Boolean, default=True)
    accepts_fbs = Column(Boolean, default=True)
    accepts_fbo = Column(Boolean, default=True)
    
    # Дополнительная информация
    warehouse_info = Column(JSON, nullable=True)
    
    # Временные метки
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    def __repr__(self):
        return f"<Warehouse(id={self.wb_warehouse_id}, name={self.name})>"

