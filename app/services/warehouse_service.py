"""Сервис для работы со складами"""

from typing import List, Dict, Any, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from loguru import logger

from app.database.repositories.warehouse_repo import WarehouseRepository
from app.services.wildberries_api import wb_api, WildberriesAPIError


class WarehouseService:
    """Сервис для работы со складами"""
    
    def __init__(self, session: AsyncSession):
        self.session = session
        self.warehouse_repo = WarehouseRepository(session)
    
    async def get_cached_warehouses(self) -> List[Dict[str, Any]]:
        """Получить кэшированные склады из базы данных"""
        try:
            warehouses = await self.warehouse_repo.get_all_warehouses()
            
            # Преобразуем в формат, совместимый с API
            result = []
            for warehouse in warehouses:
                result.append({
                    'ID': warehouse.wb_warehouse_id,
                    'id': warehouse.wb_warehouse_id,
                    'Name': warehouse.name,
                    'name': warehouse.name,
                    'Address': warehouse.address,
                    'address': warehouse.address,
                    'accepts_fbs': warehouse.accepts_fbs,
                    'accepts_fbo': warehouse.accepts_fbo,
                    'warehouse_info': warehouse.warehouse_info or {}
                })
            
            logger.info(f"Retrieved {len(result)} cached warehouses")
            return result
            
        except Exception as e:
            logger.error(f"Error getting cached warehouses: {e}")
            return []
    
    async def sync_warehouses_from_api(self, api_token: str) -> Dict[str, int]:
        """Синхронизировать склады из API с базой данных"""
        try:
            # Получаем склады из API
            async with wb_api:
                api_warehouses = await wb_api.get_warehouses(api_token)
            
            if not api_warehouses:
                logger.warning("No warehouses returned from API")
                return {'created': 0, 'updated': 0, 'total': 0}
            
            # Синхронизируем с базой данных
            stats = await self.warehouse_repo.sync_warehouses_from_api(api_warehouses)
            
            logger.info(f"Warehouse sync completed: {stats}")
            return stats
            
        except WildberriesAPIError as e:
            logger.error(f"Wildberries API error during warehouse sync: {e}")
            raise
        except Exception as e:
            logger.error(f"Error syncing warehouses: {e}")
            raise
    
    async def get_warehouses_for_monitoring(self, api_token: str, force_refresh: bool = False) -> List[Dict[str, Any]]:
        """Получить склады для мониторинга (с кэшированием)"""
        try:
            # Если не требуется принудительное обновление, используем кэш
            if not force_refresh:
                cached_warehouses = await self.get_cached_warehouses()
                if cached_warehouses:
                    logger.info(f"Using {len(cached_warehouses)} cached warehouses")
                    return cached_warehouses
            
            # Если кэш пуст или требуется обновление, получаем из API
            logger.info("Fetching warehouses from API")
            async with wb_api:
                api_warehouses = await wb_api.get_warehouses(api_token)
            
            if not api_warehouses:
                logger.warning("No warehouses returned from API, trying cached data")
                return await self.get_cached_warehouses()
            
            # Обновляем кэш
            await self.warehouse_repo.sync_warehouses_from_api(api_warehouses)
            
            logger.info(f"Updated cache with {len(api_warehouses)} warehouses from API")
            return api_warehouses
            
        except WildberriesAPIError as e:
            logger.error(f"Wildberries API error: {e}")
            # В случае ошибки API, пытаемся использовать кэш
            cached_warehouses = await self.get_cached_warehouses()
            if cached_warehouses:
                logger.info(f"Using {len(cached_warehouses)} cached warehouses as fallback")
                return cached_warehouses
            raise
        except Exception as e:
            logger.error(f"Error getting warehouses for monitoring: {e}")
            raise
    
    async def get_warehouse_by_id(self, wb_warehouse_id: int) -> Optional[Dict[str, Any]]:
        """Получить склад по ID"""
        try:
            warehouse = await self.warehouse_repo.get_warehouse_by_wb_id(wb_warehouse_id)
            if not warehouse:
                return None
            
            return {
                'ID': warehouse.wb_warehouse_id,
                'id': warehouse.wb_warehouse_id,
                'Name': warehouse.name,
                'name': warehouse.name,
                'Address': warehouse.address,
                'address': warehouse.address,
                'accepts_fbs': warehouse.accepts_fbs,
                'accepts_fbo': warehouse.accepts_fbo,
                'warehouse_info': warehouse.warehouse_info or {}
            }
            
        except Exception as e:
            logger.error(f"Error getting warehouse by ID {wb_warehouse_id}: {e}")
            return None
    
    async def get_warehouses_count(self) -> int:
        """Получить количество активных складов"""
        try:
            return await self.warehouse_repo.get_warehouses_count()
        except Exception as e:
            logger.error(f"Error getting warehouses count: {e}")
            return 0
    
    async def is_warehouse_cached(self) -> bool:
        """Проверить, есть ли кэшированные склады"""
        try:
            count = await self.get_warehouses_count()
            return count > 0
        except Exception as e:
            logger.error(f"Error checking warehouse cache: {e}")
            return False
