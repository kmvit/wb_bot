"""Интеграция с Wildberries API"""

import asyncio
import ssl
from typing import Dict, List, Optional, Any
from datetime import datetime, timedelta
import aiohttp
import certifi
from loguru import logger

from app.config.settings import settings


class WildberriesAPIError(Exception):
    """Базовый класс для ошибок Wildberries API"""
    pass


class WildberriesAuthError(WildberriesAPIError):
    """Ошибка авторизации в Wildberries API"""
    pass


class WildberriesAPI:
    """Клиент для работы с Wildberries API"""
    
    def __init__(self):
        self.statistics_url = settings.WB_STATISTICS_API_URL
        self.content_url = settings.WB_CONTENT_API_URL
        self.marketplace_url = settings.WB_MARKETPLACE_API_URL
        self.common_url = settings.WB_COMMON_API_URL
        self.supplies_url = settings.WB_SUPPLIES_API_URL
        self.session: Optional[aiohttp.ClientSession] = None
        # Отслеживание rate limiting для Supplies API
        self._last_supplies_request = 0.0
        self._supplies_min_interval = 10.0  # Минимум 10 секунд между запросами
    
    async def __aenter__(self):
        """Async context manager entry"""
        # Создаем SSL контекст с certifi для корректной работы с сертификатами
        ssl_context = ssl.create_default_context(cafile=certifi.where())
        
        # Создаем коннектор с SSL контекстом
        connector = aiohttp.TCPConnector(ssl=ssl_context)
        
        self.session = aiohttp.ClientSession(
            connector=connector,
            timeout=aiohttp.ClientTimeout(total=30),
            headers={
                'User-Agent': 'WildberriesBot/1.0',
                'Content-Type': 'application/json'
            }
        )
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit"""
        if self.session:
            await self.session.close()
    
    async def _ensure_supplies_rate_limit(self):
        """Обеспечить соблюдение rate limit для Supplies API"""
        import time
        current_time = time.time()
        time_since_last = current_time - self._last_supplies_request
        
        if time_since_last < self._supplies_min_interval:
            sleep_time = self._supplies_min_interval - time_since_last
            logger.info(f"Rate limiting: waiting {sleep_time:.1f}s before next Supplies API request")
            await asyncio.sleep(sleep_time)
        
        self._last_supplies_request = time.time()
    
    async def _make_request(
        self, 
        method: str, 
        url: str, 
        headers: Dict[str, str], 
        **kwargs
    ) -> Dict[str, Any]:
        """Выполнить HTTP запрос к API"""
        if not self.session or self.session.closed:
            # Пересоздаем сессию если она закрыта
            ssl_context = ssl.create_default_context(cafile=certifi.where())
            timeout = aiohttp.ClientTimeout(total=30, connect=10)
            self.session = aiohttp.ClientSession(
                timeout=timeout,
                connector=aiohttp.TCPConnector(ssl=ssl_context),
                headers={
                    'User-Agent': 'WildberriesBot/1.0',
                    'Content-Type': 'application/json'
                }
            )
        
        try:
            async with self.session.request(method, url, headers=headers, **kwargs) as response:
                response_text = await response.text()
                
                if response.status == 401:
                    raise WildberriesAuthError("Неверный API токен")
                elif response.status == 403:
                    raise WildberriesAuthError("Доступ запрещен. Проверьте права API токена")
                elif response.status == 429:
                    # Получаем заголовок Retry-After если есть
                    retry_after = response.headers.get('Retry-After', '60')
                    raise WildberriesAPIError(f"Превышен лимит запросов. Повторите через {retry_after} секунд")
                elif response.status >= 400:
                    raise WildberriesAPIError(f"HTTP {response.status}: {response_text}")
                
                # Проверяем, что ответ не пустой
                if not response_text.strip():
                    return {}
                
                return await response.json()
                
        except aiohttp.ClientError as e:
            logger.error(f"Network error in Wildberries API: {e}")
            raise WildberriesAPIError(f"Сетевая ошибка: {e}")
        except Exception as e:
            logger.error(f"Unexpected error in Wildberries API: {e}")
            raise WildberriesAPIError(f"Неожиданная ошибка: {e}")
    
    
    async def get_seller_info(self, api_token: str) -> Dict[str, Any]:
        """Получить информацию о продавце через Common API"""
        url = f"{self.common_url}/api/v1/seller-info"
        
        headers = {
            'Authorization': api_token
        }
        
        try:
            response = await self._make_request('GET', url, headers)
            return response
        except WildberriesAuthError:
            raise
        except Exception as e:
            logger.error(f"Error getting seller info: {e}")
            raise
    
    async def validate_api_token(self, api_token: str) -> bool:
        """Проверить валидность API токена через seller-info"""
        try:
            await self.get_seller_info(api_token)
            return True
        except WildberriesAuthError:
            return False
        except Exception as e:
            logger.warning(f"Error validating API token: {e}")
            return False
    
    async def get_warehouses(self, api_token: str) -> List[Dict[str, Any]]:
        """Получить список всех складов WB через Supplies API"""
        # Соблюдаем rate limit для Supplies API
        await self._ensure_supplies_rate_limit()
        
        headers = {
            'Authorization': api_token
        }
        
        url = f"{self.supplies_url}/api/v1/warehouses"
        response = await self._make_request('GET', url, headers)
        
        return response if isinstance(response, list) else []
    
    async def get_acceptance_coefficients(self, api_token: str, warehouse_ids: List[int] = None) -> List[Dict[str, Any]]:
        """Получить коэффициенты приёмки для складов WB"""
        # Соблюдаем rate limit для Supplies API
        await self._ensure_supplies_rate_limit()
        
        headers = {
            'Authorization': api_token
        }
        
        # Используем Supplies API endpoint
        url = f"{self.supplies_url}/api/v1/acceptance/coefficients"        
        params = {}
        if warehouse_ids:
            params['warehouseIDs'] = ','.join(map(str, warehouse_ids))
        
        response = await self._make_request('GET', url, headers, params=params)
        return response if isinstance(response, list) else []
    
    async def get_acceptance_options(self, api_token: str, goods_data: List[Dict[str, Any]], warehouse_id: int = None) -> Dict[str, Any]:
        """Получить опции приёмки для товаров (какие склады и типы упаковки доступны)"""
        # Соблюдаем rate limit для Supplies API
        await self._ensure_supplies_rate_limit()
        
        headers = {
            'Authorization': api_token,
            'Content-Type': 'application/json'
        }
        
        # Используем Supplies API endpoint
        url = f"{self.supplies_url}/api/v1/acceptance/options"
        
        params = {}
        if warehouse_id:
            params['warehouseID'] = str(warehouse_id)
        
        response = await self._make_request('POST', url, headers, params=params, json=goods_data)
        return response if isinstance(response, dict) else {}
    
    async def get_available_warehouses_for_monitoring(self, api_token: str, slot_type: str) -> List[Dict[str, Any]]:
        """Получить склады WB доступные для мониторинга с учетом типа поставки"""
        try:
            # Сначала получаем коэффициенты приемки для всех складов
            coefficients = await self.get_acceptance_coefficients(api_token)
            
            if not coefficients:
                return []
            
            # Фильтруем склады по доступности приемки
            available_warehouses = []
            
            for coeff_data in coefficients:
                # Приёмка доступна только при coefficient 0 или 1 и allowUnload = true
                if (coeff_data.get('coefficient') in [0, 1] and 
                    coeff_data.get('allowUnload') is True):
                    
                    warehouse_info = {
                        'id': coeff_data.get('warehouseID'),
                        'name': coeff_data.get('warehouseName'),
                        'date': coeff_data.get('date'),
                        'coefficient': coeff_data.get('coefficient'),
                        'boxTypeName': coeff_data.get('boxTypeName'),
                        'boxTypeID': coeff_data.get('boxTypeID'),
                        'isSortingCenter': coeff_data.get('isSortingCenter', False)
                    }
                    
                    # Проверяем соответствие типу поставки
                    box_type = coeff_data.get('boxTypeName', '').lower()
                    
                    if slot_type == 'both':
                        available_warehouses.append(warehouse_info)
                    elif slot_type == 'fbs' and ('коробки' in box_type or 'box' in box_type):
                        available_warehouses.append(warehouse_info)
                    elif slot_type == 'fbo' and ('монопаллеты' in box_type or 'суперсейф' in box_type):
                        available_warehouses.append(warehouse_info)
            
            # Убираем дубликаты по ID склада
            seen_ids = set()
            unique_warehouses = []
            for warehouse in available_warehouses:
                if warehouse['id'] not in seen_ids:
                    seen_ids.add(warehouse['id'])
                    unique_warehouses.append(warehouse)
            
            return unique_warehouses
            
        except Exception as e:
            logger.error(f"Error getting available warehouses for monitoring: {e}")
            return []
    
    async def get_supplies_drafts(self, api_token: str) -> List[Dict[str, Any]]:
        """Получить черновики поставок"""
        headers = {
            'Authorization': api_token
        }
        
        url = f"{self.marketplace_url}/api/v3/supplies"
        params = {'status': 'draft'}
        
        response = await self._make_request('GET', url, headers, params=params)
        return response.get('supplies', []) if isinstance(response, dict) else []
    
    # DEPRECATED: Этот метод использует несуществующий API endpoint
    # Используйте get_acceptance_coefficients() для получения информации о доступности приемки
    # async def get_available_slots(...):
    
    async def book_slot(
        self, 
        api_token: str, 
        supply_id: str, 
        warehouse_id: int, 
        slot_date: datetime, 
        slot_time: str
    ) -> Dict[str, Any]:
        """Забронировать слот"""
        headers = {
            'Authorization': api_token
        }
        
        url = f"{self.marketplace_url}/api/v3/supplies/{supply_id}/book"
        data = {
            'warehouseId': warehouse_id,
            'date': slot_date.strftime('%Y-%m-%d'),
            'time': slot_time
        }
        
        response = await self._make_request('POST', url, headers, json=data)
        return response
    
    async def get_cabinet_info(self, api_token: str) -> Dict[str, Any]:
        """Получить информацию о кабинете через seller-info API"""
        try:
            # Получаем информацию о продавце
            seller_info = await self.get_seller_info(api_token)
            
            return {
                'seller_info': seller_info,
                'api_token_valid': True,
                'token_test_passed': True
            }
                
        except Exception as e:
            logger.error(f"Error getting cabinet info: {e}")
            raise


# Создаем глобальный экземпляр для использования в других модулях
wb_api = WildberriesAPI()
