"""Репозиторий для работы с мониторингом слотов"""

from datetime import datetime
from typing import List, Optional, Dict, Any
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update, delete
from sqlalchemy.orm import selectinload
from loguru import logger

from app.database.models import SlotMonitoring, User, MonitoringStatus


class SlotMonitoringRepository:
    """Репозиторий для работы с мониторингом слотов"""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def create_monitoring(
        self,
        user: User,
        coefficient_min: float,
        coefficient_max: float,
        warehouse_ids: List[int],
        logistics_shoulder: int = 0,
        box_type_id: Optional[int] = None,
        acceptance_options: Dict[str, Any] = None,
        date_from: datetime = None,
        date_to: datetime = None,
        order_number: Optional[str] = None
    ) -> SlotMonitoring:
        """Создать новый мониторинг слотов"""
        try:
            monitoring = SlotMonitoring(
                user_id=user.id,
                coefficient_min=coefficient_min,
                coefficient_max=coefficient_max,
                logistics_shoulder=logistics_shoulder,
                box_type_id=box_type_id,
                warehouse_ids=warehouse_ids,
                acceptance_options=acceptance_options or {},
                date_from=date_from,
                date_to=date_to,
                order_number=order_number,
                status=MonitoringStatus.ACTIVE.value
            )

            self.session.add(monitoring)
            await self.session.commit()
            await self.session.refresh(monitoring)

            logger.info(
                f"Created slot monitoring: {monitoring.id} for user {user.telegram_id}")
            return monitoring

        except Exception as e:
            await self.session.rollback()
            logger.error(
                f"Error creating slot monitoring for user {user.telegram_id}: {e}")
            raise

    async def get_active_monitorings(self, user: User) -> List[SlotMonitoring]:
        """Получить активные мониторинги пользователя"""
        try:
            result = await self.session.execute(
                select(SlotMonitoring)
                .where(
                    SlotMonitoring.user_id == user.id,
                    SlotMonitoring.status == MonitoringStatus.ACTIVE.value
                )
            )
            return result.scalars().all()
        except Exception as e:
            logger.error(
                f"Error getting active monitorings for user {user.telegram_id}: {e}")
            return []

    async def get_all_active_monitorings(self) -> List[SlotMonitoring]:
        """Получить все активные мониторинги всех пользователей"""
        try:
            result = await self.session.execute(
                select(SlotMonitoring)
                .where(SlotMonitoring.status == MonitoringStatus.ACTIVE.value)
                .options(selectinload(SlotMonitoring.user))
            )
            return result.scalars().all()
        except Exception as e:
            logger.error(f"Error getting all active monitorings: {e}")
            return []

    async def get_monitoring_by_id(self, monitoring_id: int) -> Optional[SlotMonitoring]:
        """Получить мониторинг по ID"""
        try:
            result = await self.session.execute(
                select(SlotMonitoring)
                .options(selectinload(SlotMonitoring.user))
                .where(SlotMonitoring.id == monitoring_id)
            )
            return result.scalar_one_or_none()
        except Exception as e:
            logger.error(
                f"Error getting monitoring by id {monitoring_id}: {e}")
            return None

    async def update_monitoring_status(
        self,
        monitoring_id: int,
        status: MonitoringStatus
    ) -> bool:
        """Обновить статус мониторинга"""
        try:
            await self.session.execute(
                update(SlotMonitoring)
                .where(SlotMonitoring.id == monitoring_id)
                .values(
                    status=status.value,
                    updated_at=datetime.utcnow()
                )
            )
            await self.session.commit()
            logger.info(
                f"Updated monitoring {monitoring_id} status to {status.value}")
            return True
        except Exception as e:
            await self.session.rollback()
            logger.error(
                f"Error updating monitoring status {monitoring_id}: {e}")
            return False

    async def update_last_check(self, monitoring_id: int) -> bool:
        """Обновить время последней проверки"""
        try:
            await self.session.execute(
                update(SlotMonitoring)
                .where(SlotMonitoring.id == monitoring_id)
                .values(
                    last_check_at=datetime.utcnow(),
                    updated_at=datetime.utcnow()
                )
            )
            await self.session.commit()
            return True
        except Exception as e:
            await self.session.rollback()
            logger.error(
                f"Error updating last check for monitoring {monitoring_id}: {e}")
            return False

    async def delete_monitoring(self, monitoring_id: int, user: User) -> bool:
        """Удалить мониторинг пользователя"""
        try:
            await self.session.execute(
                delete(SlotMonitoring)
                .where(
                    SlotMonitoring.id == monitoring_id,
                    SlotMonitoring.user_id == user.id
                )
            )
            await self.session.commit()
            logger.info(
                f"Deleted monitoring {monitoring_id} for user {user.telegram_id}")
            return True
        except Exception as e:
            await self.session.rollback()
            logger.error(
                f"Error deleting monitoring {monitoring_id} for user {user.telegram_id}: {e}")
            return False

    async def update_monitoring(self, monitoring_id: int, **kwargs) -> bool:
        """Обновить мониторинг слотов"""
        try:
            # Строим запрос обновления
            update_query = update(SlotMonitoring).where(
                SlotMonitoring.id == monitoring_id
            )

            # Добавляем только переданные поля
            if kwargs:
                update_query = update_query.values(**kwargs)
                await self.session.execute(update_query)
                await self.session.commit()

                logger.info(
                    f"Updated monitoring {monitoring_id} with fields: {list(kwargs.keys())}")
                return True
            else:
                logger.warning(
                    f"No fields to update for monitoring {monitoring_id}")
                return False

        except Exception as e:
            await self.session.rollback()
            logger.error(f"Error updating monitoring {monitoring_id}: {e}")
            return False

    async def add_failed_booking_date(self, monitoring_id: int, failed_date: datetime) -> bool:
        """Добавить дату в список неудачных попыток бронирования"""
        try:
            # Получаем текущий мониторинг
            monitoring = await self.get_monitoring_by_id(monitoring_id)
            if not monitoring:
                logger.error(f"Monitoring {monitoring_id} not found")
                return False
            
            # Получаем текущий список неудачных дат
            failed_dates = monitoring.failed_booking_dates or []
            
            # Добавляем новую дату (в формате строки для JSON)
            date_str = failed_date.strftime('%Y-%m-%d')
            if date_str not in failed_dates:
                failed_dates.append(date_str)
                
                # Обновляем в базе данных
                await self.session.execute(
                    update(SlotMonitoring)
                    .where(SlotMonitoring.id == monitoring_id)
                    .values(
                        failed_booking_dates=failed_dates,
                        updated_at=datetime.utcnow()
                    )
                )
                await self.session.commit()
                
                logger.info(f"Added failed booking date {date_str} for monitoring {monitoring_id}")
                return True
            else:
                logger.info(f"Date {date_str} already in failed dates for monitoring {monitoring_id}")
                return True
                
        except Exception as e:
            await self.session.rollback()
            logger.error(f"Error adding failed booking date for monitoring {monitoring_id}: {e}")
            return False

    async def get_failed_booking_dates(self, monitoring_id: int) -> list:
        """Получить список неудачных дат бронирования"""
        try:
            monitoring = await self.get_monitoring_by_id(monitoring_id)
            if not monitoring:
                return []
            
            return monitoring.failed_booking_dates or []
            
        except Exception as e:
            logger.error(f"Error getting failed booking dates for monitoring {monitoring_id}: {e}")
            return []

    async def clear_failed_booking_dates(self, monitoring_id: int) -> bool:
        """Очистить список неудачных дат бронирования"""
        try:
            await self.session.execute(
                update(SlotMonitoring)
                .where(SlotMonitoring.id == monitoring_id)
                .values(
                    failed_booking_dates=None,
                    updated_at=datetime.utcnow()
                )
            )
            await self.session.commit()
            
            logger.info(f"Cleared failed booking dates for monitoring {monitoring_id}")
            return True
            
        except Exception as e:
            await self.session.rollback()
            logger.error(f"Error clearing failed booking dates for monitoring {monitoring_id}: {e}")
            return False
