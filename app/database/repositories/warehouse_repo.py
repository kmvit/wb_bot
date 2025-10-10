"""Репозиторий для работы со складами"""

from datetime import datetime
from typing import List, Optional, Dict, Any
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update, delete
from loguru import logger

from app.database.models import Warehouse


class WarehouseRepository:
    """Репозиторий для работы со складами"""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_all_warehouses(self) -> List[Warehouse]:
        """Получить все склады"""
        try:
            result = await self.session.execute(
                select(Warehouse).where(Warehouse.is_active == True)
            )
            return result.scalars().all()
        except Exception as e:
            logger.error(f"Error getting all warehouses: {e}")
            return []

    async def get_warehouse_by_wb_id(self, wb_warehouse_id: int) -> Optional[Warehouse]:
        """Получить склад по WB ID"""
        try:
            result = await self.session.execute(
                select(Warehouse).where(
                    Warehouse.wb_warehouse_id == wb_warehouse_id)
            )
            return result.scalar_one_or_none()
        except Exception as e:
            logger.error(
                f"Error getting warehouse by wb_id {wb_warehouse_id}: {e}")
            return None

    async def create_warehouse(
        self,
        wb_warehouse_id: int,
        name: str,
        address: str = None,
        accepts_fbs: bool = True,
        accepts_fbo: bool = True,
        warehouse_info: Dict[str, Any] = None
    ) -> Warehouse:
        """Создать новый склад"""
        try:
            warehouse = Warehouse(
                wb_warehouse_id=wb_warehouse_id,
                name=name,
                address=address,
                accepts_fbs=accepts_fbs,
                accepts_fbo=accepts_fbo,
                warehouse_info=warehouse_info or {}
            )

            self.session.add(warehouse)
            await self.session.commit()
            await self.session.refresh(warehouse)

            logger.info(
                f"Created warehouse: {warehouse.id} (WB ID: {wb_warehouse_id})")
            return warehouse

        except Exception as e:
            await self.session.rollback()
            logger.error(f"Error creating warehouse {wb_warehouse_id}: {e}")
            raise

    async def update_warehouse(
        self,
        wb_warehouse_id: int,
        name: str = None,
        address: str = None,
        accepts_fbs: bool = None,
        accepts_fbo: bool = None,
        warehouse_info: Dict[str, Any] = None
    ) -> Optional[Warehouse]:
        """Обновить информацию о складе"""
        try:
            warehouse = await self.get_warehouse_by_wb_id(wb_warehouse_id)
            if not warehouse:
                return None

            if name is not None:
                warehouse.name = name
            if address is not None:
                warehouse.address = address
            if accepts_fbs is not None:
                warehouse.accepts_fbs = accepts_fbs
            if accepts_fbo is not None:
                warehouse.accepts_fbo = accepts_fbo
            if warehouse_info is not None:
                warehouse.warehouse_info = warehouse_info

            warehouse.updated_at = datetime.utcnow()

            await self.session.commit()
            await self.session.refresh(warehouse)

            logger.info(
                f"Updated warehouse: {warehouse.id} (WB ID: {wb_warehouse_id})")
            return warehouse

        except Exception as e:
            await self.session.rollback()
            logger.error(f"Error updating warehouse {wb_warehouse_id}: {e}")
            raise

    async def sync_warehouses_from_api(self, api_warehouses: List[Dict[str, Any]]) -> Dict[str, int]:
        """Синхронизировать склады из API с базой данных"""
        try:
            stats = {
                'created': 0,
                'updated': 0,
                'total': len(api_warehouses)
            }

            for api_warehouse in api_warehouses:
                wb_id = api_warehouse.get('ID') or api_warehouse.get('id')
                if not wb_id:
                    continue

                name = api_warehouse.get('Name') or api_warehouse.get(
                    'name', 'Неизвестный склад')
                address = api_warehouse.get(
                    'Address') or api_warehouse.get('address')

                # Проверяем, существует ли склад
                existing_warehouse = await self.get_warehouse_by_wb_id(wb_id)

                if existing_warehouse:
                    # Обновляем существующий склад
                    await self.update_warehouse(
                        wb_warehouse_id=wb_id,
                        name=name,
                        address=address,
                        warehouse_info=api_warehouse
                    )
                    stats['updated'] += 1
                else:
                    # Создаем новый склад
                    await self.create_warehouse(
                        wb_warehouse_id=wb_id,
                        name=name,
                        address=address,
                        warehouse_info=api_warehouse
                    )
                    stats['created'] += 1

            logger.info(f"Warehouse sync completed: {stats}")
            return stats

        except Exception as e:
            await self.session.rollback()
            logger.error(f"Error syncing warehouses: {e}")
            raise

    async def deactivate_warehouse(self, wb_warehouse_id: int) -> bool:
        """Деактивировать склад"""
        try:
            warehouse = await self.get_warehouse_by_wb_id(wb_warehouse_id)
            if not warehouse:
                return False

            warehouse.is_active = False
            warehouse.updated_at = datetime.utcnow()

            await self.session.commit()
            logger.info(
                f"Deactivated warehouse: {warehouse.id} (WB ID: {wb_warehouse_id})")
            return True

        except Exception as e:
            await self.session.rollback()
            logger.error(
                f"Error deactivating warehouse {wb_warehouse_id}: {e}")
            return False

    async def get_warehouses_count(self) -> int:
        """Получить количество активных складов"""
        try:
            result = await self.session.execute(
                select(Warehouse).where(Warehouse.is_active == True)
            )
            return len(result.scalars().all())
        except Exception as e:
            logger.error(f"Error getting warehouses count: {e}")
            return 0
