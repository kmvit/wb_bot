"""Сервис автоматического бронирования слотов"""

import asyncio
from datetime import datetime, timedelta
from typing import Dict, Optional, Tuple, Any
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, WebDriverException, NoSuchElementException
from loguru import logger

from app.services.wb_web_auth import WBWebAuthService, WBWebAuthError
from app.utils.browser_config import create_undetectable_chrome_options, setup_undetectable_chrome


class BookingServiceError(Exception):
    """Базовый класс для ошибок сервиса бронирования"""
    pass


class BookingService:
    """Сервис для автоматического бронирования слотов"""
    
    def __init__(self, auth_service: Optional[WBWebAuthService] = None):
        self.wb_auth_service = auth_service or WBWebAuthService()
        self.driver: Optional[webdriver.Chrome] = None
        self.wait: Optional[WebDriverWait] = None
    
    async def _ensure_browser_ready(self):
        """Убедиться, что браузер готов к работе"""
        if not self.driver:
            # Сначала пытаемся использовать существующий браузер из сервиса авторизации
            if self.wb_auth_service.driver:
                logger.info("Using existing browser from auth service for booking...")
                self.driver = self.wb_auth_service.driver
                self.wait = self.wb_auth_service.wait
            else:
                logger.info("No existing browser found, initializing new one for booking...")
                await self._initialize_browser()
    
    async def _initialize_browser(self):
        """Инициализировать браузер для бронирования"""
        try:
            # Персистентный профиль браузера для сохранения сессии
            import os
            import tempfile
            import uuid
            profile_dir = os.path.join(tempfile.gettempdir(), f'wb_bot_booking_profile_{uuid.uuid4().hex[:8]}')
            
            # Создаем настройки браузера с защитой от детекции
            options = create_undetectable_chrome_options(profile_dir=profile_dir)
            
            # Запускаем браузер
            self.driver = webdriver.Chrome(options=options)
            self.wait = WebDriverWait(self.driver, 15)  # Увеличиваем время ожидания для бронирования
            
            # Настраиваем защиту от детекции
            setup_undetectable_chrome(self.driver)
            
            logger.info("Booking browser initialized successfully")
            
        except Exception as e:
            logger.error(f"Error initializing booking browser: {e}")
            await self._cleanup()
            raise
    
    async def _cleanup(self):
        """Очистить ресурсы браузера"""
        try:
            # Не закрываем браузер, если он используется из сервиса авторизации
            if self.driver and self.driver != self.wb_auth_service.driver:
                self.driver.quit()
        except:
            pass
        
        # Сбрасываем ссылки на драйвер, но не закрываем его
        self.driver = None
        self.wait = None
    
    async def book_slot(
        self, 
        session_data: Dict[str, Any], 
        order_number: str, 
        target_date: datetime,
        target_warehouse_id: int
    ) -> Tuple[bool, str]:
        """
        Забронировать слот для указанного заказа
        
        Args:
            session_data: Данные сессии пользователя
            order_number: Номер заказа для бронирования
            target_date: Целевая дата слота
            target_warehouse_id: ID целевого склада
            
        Returns:
            Tuple[bool, str]: (успех, сообщение)
        """
        try:
            logger.info(f"Starting booking process for order {order_number}, date {target_date.date()}, warehouse {target_warehouse_id}")
            
            # Убеждаемся, что браузер готов
            await self._ensure_browser_ready()
            
            # Восстанавливаем сессию
            await self._restore_session(session_data)
            
            # Переходим напрямую на страницу деталей поставки
            await self._navigate_to_supply_detail(order_number)
            
            # Проверяем, не перекинуло ли на авторизацию
            current_url = self.driver.current_url or ''
            if 'seller-auth.wildberries.ru' in current_url:
                raise BookingServiceError("Session expired, need to reauthorize")
            
            # Проверяем, что мы на правильной странице
            if 'supply-detail' not in self.driver.current_url:
                raise BookingServiceError(f"Failed to navigate to order details page for order {order_number}")
            
            # Нажимаем кнопку "Запланировать поставку"
            await self._click_plan_supply_button()
            
            # Ждем загрузки календаря
            await asyncio.sleep(1)
            
            # Ищем и кликаем по нужной дате в календаре
            await self._click_calendar_date(target_date, target_warehouse_id, order_number)
            
            logger.info(f"✅ Successfully booked slot for order {order_number}")
            return True, f"Слот успешно забронирован для заказа {order_number} на {target_date.strftime('%d.%m.%Y')}"
            
        except BookingServiceError:
            raise
        except Exception as e:
            logger.error(f"Error booking slot for order {order_number}: {e}")
            raise BookingServiceError(f"Ошибка бронирования: {str(e)}")
        finally:
            await self._cleanup()
    
    async def _restore_session(self, session_data: Dict[str, Any]):
        """Восстановить сессию пользователя или проверить существующую"""
        try:
            # Если используем браузер из сервиса авторизации, проверяем, авторизован ли он
            if self.driver == self.wb_auth_service.driver:
                logger.info("🌐 Using existing browser from auth service, checking current state...")
                
                # Проверяем текущий URL
                current_url = self.driver.current_url or ''
                
                # Если уже на странице поставок, не переходим никуда
                if 'supplies-management' in current_url:
                    logger.info("✅ Already on supplies page, skipping navigation")
                    return
                
                # Если уже в кабинете, но не на странице поставок, просто возвращаемся
                # (мы сразу перейдем на страницу деталей поставки)
                if 'seller.wildberries.ru' in current_url and 'seller-auth.wildberries.ru' not in current_url:
                    logger.info("✅ Already in seller cabinet, ready for direct navigation to supply detail")
                    return
                
                # Если не авторизован, восстанавливаем сессию
                if 'seller-auth.wildberries.ru' in current_url:
                    logger.info("🔑 Browser not authorized, restoring session...")
                    await self._restore_session_data(session_data)
                else:
                    # Готовы к прямому переходу на страницу деталей поставки
                    logger.info("🌐 Ready for direct navigation to supply detail page")
            else:
                # Если используем новый браузер, восстанавливаем сессию
                logger.info("🌐 Restoring session in new browser...")
                await self._restore_session_data(session_data)
            
        except Exception as e:
            logger.error(f"Error restoring session: {e}")
            raise BookingServiceError(f"Ошибка восстановления сессии: {str(e)}")
    
    async def _restore_session_data(self, session_data: Dict[str, Any]):
        """Восстановить данные сессии в браузере"""
        # Восстанавливаем cookies
        if 'cookies' in session_data:
            # Очищаем существующие cookies
            self.driver.delete_all_cookies()
            
            # Добавляем сохраненные cookies
            restored_count = 0
            for cookie in session_data['cookies']:
                try:
                    cookie_copy = dict(cookie)
                    # Selenium ожидает int для expiry
                    if 'expiry' in cookie_copy and isinstance(cookie_copy['expiry'], float):
                        cookie_copy['expiry'] = int(cookie_copy['expiry'])
                    # Некоторые поля не поддерживаются Selenium
                    cookie_copy.pop('sameSite', None)
                    cookie_copy.pop('priority', None)
                    
                    # Устанавливаем правильный domain если его нет
                    if 'domain' not in cookie_copy or not cookie_copy['domain']:
                        cookie_copy['domain'] = '.wildberries.ru'
                    
                    self.driver.add_cookie(cookie_copy)
                    restored_count += 1
                except Exception as e:
                    logger.debug(f"Could not add cookie: {e}")
            
            logger.info(f"🔑 Successfully restored {restored_count} cookies")
            
            # Восстанавливаем localStorage и sessionStorage
            if 'local_storage' in session_data:
                try:
                    for key, value in session_data['local_storage'].items():
                        self.driver.execute_script(f"localStorage.setItem('{key}', '{value}');")
                except Exception as e:
                    logger.debug(f"Could not restore localStorage: {e}")
            
            if 'session_storage' in session_data:
                try:
                    for key, value in session_data['session_storage'].items():
                        self.driver.execute_script(f"sessionStorage.setItem('{key}', '{value}');")
                except Exception as e:
                    logger.debug(f"Could not restore sessionStorage: {e}")
            
            # Перезагружаем страницу с восстановленными cookies
            logger.info("🔄 Refreshing page with restored session")
            self.driver.refresh()
            await asyncio.sleep(3)
            
            # Проверяем, что сессия восстановлена
            current_url = self.driver.current_url or ''
            if 'seller-auth.wildberries.ru' in current_url:
                raise BookingServiceError("Session restoration failed - still on auth page")
    
    async def _navigate_to_supply_detail(self, order_number: str):
        """Перейти напрямую на страницу деталей поставки по номеру заказа"""
        try:
            logger.info(f"🚀 Navigating directly to supply detail page for order: {order_number}")
            
            # Формируем URL для страницы деталей поставки
            supply_detail_url = f"https://seller.wildberries.ru/supplies-management/all-supplies/supply-detail?preorderId={order_number}&supplyId"
            
            # Переходим на страницу деталей поставки
            self.driver.get(supply_detail_url)
            
            # Ждем загрузки страницы
            try:
                self.wait.until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, 'div[class*="Supply-detail"]'))
                )
                logger.info("✅ Successfully navigated to supply detail page")
            except TimeoutException:
                logger.warning("⚠️ Timeout waiting for supply detail page to load")
                # Проверяем, что мы на правильной странице
                current_url = self.driver.current_url or ''
                if 'supply-detail' in current_url and order_number in current_url:
                    logger.info("✅ URL contains correct order number, continuing...")
                else:
                    raise BookingServiceError(f"Failed to navigate to supply detail page for order {order_number}")
            
        except BookingServiceError:
            raise
        except Exception as e:
            logger.error(f"Error navigating to supply detail: {e}")
            raise BookingServiceError(f"Failed to navigate to supply detail page: {e}")
    
    async def _click_plan_supply_button(self):
        """Нажать кнопку 'Запланировать поставку'"""
        try:
            logger.info("🔍 Looking for 'Запланировать поставку' button...")
            
            # Логируем все кнопки на странице для отладки
            try:
                all_buttons = self.driver.find_elements(By.TAG_NAME, 'button')
                logger.info(f"📋 Found {len(all_buttons)} buttons on page")
                for i, btn in enumerate(all_buttons[:10]):  # Логируем только первые 10
                    try:
                        btn_text = btn.text.strip()
                        btn_class = btn.get_attribute('class') or ''
                        if btn_text or 'запланировать' in btn_class.lower():
                            logger.info(f"Button {i}: text='{btn_text}', class='{btn_class[:100]}...'")
                    except Exception as e:
                        logger.debug(f"Error getting button {i} info: {e}")
            except Exception as e:
                logger.debug(f"Error logging buttons: {e}")
            
            # Сначала пробуем найти кнопку сразу без ожидания
            button_selectors = [
                '//button[contains(text(), "Запланировать поставку")]',
                '//button[contains(text(), "Запланировать")]',
                '//span[contains(text(), "Запланировать поставку")]/parent::button',
                '//span[contains(text(), "Запланировать")]/parent::button',
                'div[class*="Supply-detail-options__plan-desktop-button"] button',
                'div[class*="Supply-detail-options__plan-desktop-button__-N407e2FDC"] button',
                'button[class*="button__ymbakhzRxO"]',
                'button[class*="fullWidth"]',
                'button[class*="fullWidth__7wrfVPCWJP"]',
                'div[class*="Supply-detail-options__buttons"] button',
                'div[class*="Supply-detail-options__wrapper"] button',
                'div[class*="Supply-detail"] button',
                'button[data-testid*="plan"]',
                'button[data-testid*="supply"]'
            ]
            
            button = None
            for selector in button_selectors:
                try:
                    if selector.startswith('//'):
                        # XPath селектор
                        elements = self.driver.find_elements(By.XPATH, selector)
                    else:
                        # CSS селектор
                        elements = self.driver.find_elements(By.CSS_SELECTOR, selector)
                    
                    for element in elements:
                        if element.is_displayed() and element.is_enabled():
                            button_text = element.text.strip()
                            # Проверяем текст кнопки и текст внутри span элементов
                            span_text = ""
                            try:
                                spans = element.find_elements(By.TAG_NAME, 'span')
                                for span in spans:
                                    span_text += span.text.strip() + " "
                            except:
                                pass
                            
                            full_text = (button_text + " " + span_text).lower()
                            if any(keyword in full_text for keyword in ["запланировать", "поставку", "plan"]):
                                button = element
                                logger.info(f"✅ Found 'Запланировать поставку' button with selector: {selector}")
                                logger.info(f"Button text: '{button_text}', Span text: '{span_text.strip()}'")
                                break
                    
                    if button:
                        break
                        
                except Exception as e:
                    logger.debug(f"Selector {selector} failed: {e}")
                    continue
            
            # Если не нашли сразу, пробуем более специфичные селекторы
            if not button:
                logger.info("🔍 Trying more specific selectors...")
                specific_selectors = [
                    '//span[@class="caption__kqFcIewCT5" and contains(text(), "Запланировать поставку")]/parent::button',
                    '//span[contains(@class, "caption") and contains(text(), "Запланировать")]/parent::button',
                    'div.Supply-detail-options__plan-desktop-button__-N407e2FDC button',
                    'div[class*="Supply-detail-options__plan-desktop-button"] button.button__ymbakhzRxO',
                    'button.button__ymbakhzRxO.fullWidth__7wrfVPCWJP'
                ]
                
                for selector in specific_selectors:
                    try:
                        if selector.startswith('//'):
                            elements = self.driver.find_elements(By.XPATH, selector)
                        else:
                            elements = self.driver.find_elements(By.CSS_SELECTOR, selector)
                        
                        for element in elements:
                            if element.is_displayed() and element.is_enabled():
                                button = element
                                logger.info(f"✅ Found 'Запланировать поставку' button with specific selector: {selector}")
                                break
                        
                        if button:
                            break
                            
                    except Exception as e:
                        logger.debug(f"Specific selector {selector} failed: {e}")
                        continue
            
            # Если все еще не нашли, ждем появления
            if not button:
                logger.info("⏳ Button not found with specific selectors, waiting for appearance...")
                try:
                    button = self.wait.until(
                        EC.element_to_be_clickable((By.XPATH, '//span[contains(text(), "Запланировать поставку")]/parent::button'))
                    )
                    logger.info("✅ 'Запланировать поставку' button appeared after waiting")
                except TimeoutException:
                    # Последняя попытка - ищем любую кнопку с текстом "Запланировать"
                    try:
                        button = self.wait.until(
                            EC.element_to_be_clickable((By.XPATH, '//button[contains(text(), "Запланировать")]'))
                        )
                        logger.info("✅ Found 'Запланировать' button after extended waiting")
                    except TimeoutException:
                        raise BookingServiceError("'Запланировать поставку' button not found")
            
            # Кликаем по кнопке
            try:
                logger.info("🖱️ Clicking 'Запланировать поставку' button...")
                button.click()
            except Exception as e:
                logger.warning(f"Regular click failed: {e}, trying JavaScript click...")
                self.driver.execute_script("arguments[0].click();", button)
            
            # Ждем появления модального окна с календарем
            try:
                logger.info("⏳ Waiting for modal window with calendar...")
                # Ждем появления модального окна
                modal = self.wait.until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, '[class*="modal"], [class*="Modal"], [class*="popup"], [class*="Popup"]'))
                )
                logger.info("✅ Modal window appeared")
                
                # Ждем появления календаря в модальном окне
                calendar_element = self.wait.until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, 'table[class*="Calendar-plan-table-view"]'))
                )
                logger.info("✅ Calendar appeared in modal window")
                return
                
            except TimeoutException:
                logger.error("❌ Timeout waiting for modal window or calendar")
                raise BookingServiceError("Модальное окно с календарем не появилось")
            
        except BookingServiceError:
            raise
        except Exception as e:
            logger.error(f"Error clicking plan supply button: {e}")
            raise BookingServiceError(f"Ошибка нажатия кнопки 'Запланировать поставку': {str(e)}")
    
    async def _click_calendar_date(self, target_date: datetime, target_warehouse_id: int, order_number: str):
        """Найти и кликнуть по нужной дате в календаре"""
        try:
            logger.info(f"🔍 Looking for date {target_date.strftime('%d.%m.%Y')} in calendar...")
            
            # Определяем переменные для поиска в начале метода
            target_day = target_date.strftime('%d').lstrip('0')  # Убираем ведущий ноль (1 вместо 01)
            target_day_padded = target_date.strftime('%d').zfill(2)  # С ведущим нулем (01)
            
            # Русские названия месяцев
            russian_months = {
                1: 'января', 2: 'февраля', 3: 'марта', 4: 'апреля',
                5: 'мая', 6: 'июня', 7: 'июля', 8: 'августа',
                9: 'сентября', 10: 'октября', 11: 'ноября', 12: 'декабря'
            }
            target_month = russian_months[target_date.month]
            
            logger.info(f"Looking for day: {target_day} or {target_day_padded}, month: {target_month}")
            
            # Ждем появления календаря
            try:
                self.wait.until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, 'table[class*="Calendar-plan-table-view"]'))
                )
            except TimeoutException:
                raise BookingServiceError("Calendar table not found")
            
            # Ищем ячейки календаря
            calendar_cells = self.driver.find_elements(By.CSS_SELECTOR, 'td[data-testid^="calendar-cell"]')
            logger.info(f"Found {len(calendar_cells)} calendar cells")
            
            # Логируем все найденные даты для отладки
            for i, cell in enumerate(calendar_cells[:10]):  # Логируем первые 10 для отладки
                try:
                    date_elements = cell.find_elements(By.CSS_SELECTOR, 'span[data-name="Text"]')
                    for date_element in date_elements:
                        date_text = (date_element.text or '').strip()
                        if date_text and any(month in date_text.lower() for month in ['января', 'февраля', 'марта', 'апреля', 'мая', 'июня', 'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря']):
                            logger.info(f"Cell {i}: Found date text: '{date_text}'")
                            break
                except Exception as e:
                    logger.debug(f"Error checking cell {i}: {e}")
            
            # Альтернативный способ - поиск по data-testid с номером дня
            logger.info(f"Also trying to find cell with day number: {target_day}")
            
            # Пробуем найти ячейку по data-testid, содержащему номер дня
            for cell in calendar_cells:
                test_id = cell.get_attribute('data-testid') or ''
                if f'-{target_day}' in test_id or f'-{target_day}-' in test_id:
                    logger.info(f"Found potential cell by testid: {test_id}")
                    # Проверяем, что это действительно нужная дата
                    try:
                        date_elements = cell.find_elements(By.CSS_SELECTOR, 'span[data-name="Text"]')
                        for date_element in date_elements:
                            date_text = (date_element.text or '').strip().lower()
                            if (target_day in date_text or target_day_padded in date_text) and target_month in date_text:
                                logger.info(f"✅ Found matching date cell by testid: {date_text}")
                                # Переходим к обработке этой ячейки
                                break
                    except Exception as e:
                        logger.debug(f"Error checking cell by testid: {e}")
                        continue
            
            for cell in calendar_cells:
                try:
                    # Проверяем, что ячейка не заблокирована
                    if 'Calendar-cell--is-disabled' in cell.get_attribute('class'):
                        continue
                    
                    # Ищем дату в ячейке
                    date_elements = cell.find_elements(By.CSS_SELECTOR, 'span[data-name="Text"]')
                    for date_element in date_elements:
                        date_text = (date_element.text or '').strip().lower()
                        logger.debug(f"Checking date text: '{date_text}'")
                        
                        # Проверяем, содержит ли текст нужную дату (ищем и с ведущим нулем, и без)
                        if ((target_day in date_text or target_day_padded in date_text) and 
                            target_month in date_text):
                            logger.info(f"✅ Found matching date cell: {date_text}")
                            
                            # Дополнительная проверка - ищем точное совпадение
                            expected_text_1 = f"{target_day} {target_month}"
                            expected_text_2 = f"{target_day_padded} {target_month}"
                            if expected_text_1 in date_text or expected_text_2 in date_text:
                                logger.info(f"✅ Exact match found: '{expected_text_1}' or '{expected_text_2}' in '{date_text}'")
                            else:
                                logger.info(f"✅ Partial match found: day '{target_day}'/'{target_day_padded}' and month '{target_month}' in '{date_text}'")
                            
                            # Алгоритм: сначала кликаем по ячейке, потом ищем кнопку "Выбрать"
                            logger.info("🖱️ Step 1: Clicking on date cell...")
                            try:
                                cell.click()
                                logger.info("✅ Clicked on date cell successfully")
                            except Exception as e:
                                logger.warning(f"Regular click failed: {e}, trying JavaScript click...")
                                self.driver.execute_script("arguments[0].click();", cell)
                                logger.info("✅ Clicked on date cell with JavaScript")
                            
                            # Ждем появления кнопки "Выбрать" после клика по ячейке
                            logger.info("🔍 Step 2: Looking for 'Выбрать' button...")
                            
                            # Сначала пробуем найти кнопку сразу в ячейке
                            choose_button = None
                            choose_selectors = [
                                './/button[contains(text(), "Выбрать")]',
                                './/button[text()="Выбрать"]',
                                'button[data-testid*="choose"]',
                                'button[data-testid*="select"]',
                                'button[data-testid*="Выбрать"]',
                                'div[class*="button-container"] button',
                                'div[class*="Calendar-cell__button-container"] button',
                                'button[class*="choose"]',
                                'button[class*="select"]'
                            ]
                            
                            # Ищем кнопку в самой ячейке
                            for selector in choose_selectors:
                                try:
                                    if selector.startswith('.//'):
                                        # XPath селектор
                                        buttons = cell.find_elements(By.XPATH, selector)
                                    else:
                                        # CSS селектор
                                        buttons = cell.find_elements(By.CSS_SELECTOR, selector)
                                    
                                    for button in buttons:
                                        if button.is_displayed() and button.is_enabled():
                                            button_text = button.text.strip()
                                            if button_text == "Выбрать" or "выбрать" in button_text.lower():
                                                choose_button = button
                                                logger.info(f"✅ Found 'Выбрать' button in cell with selector: {selector}")
                                                break
                                    
                                    if choose_button:
                                        break
                                        
                                except Exception as e:
                                    logger.debug(f"Selector {selector} failed: {e}")
                                    continue
                            
                            # Если не нашли в ячейке, ищем в модальном окне
                            if not choose_button:
                                logger.info("🔍 Button not found in cell, searching in modal...")
                                for selector in choose_selectors:
                                    try:
                                        if selector.startswith('.//'):
                                            # XPath селектор - убираем точку в начале
                                            xpath = selector[2:]
                                            buttons = self.driver.find_elements(By.XPATH, xpath)
                                        else:
                                            # CSS селектор
                                            buttons = self.driver.find_elements(By.CSS_SELECTOR, selector)
                                        
                                        for button in buttons:
                                            if button.is_displayed() and button.is_enabled():
                                                button_text = button.text.strip()
                                                if button_text == "Выбрать" or "выбрать" in button_text.lower():
                                                    choose_button = button
                                                    logger.info(f"✅ Found 'Выбрать' button in modal with selector: {selector}")
                                                    break
                                        
                                        if choose_button:
                                            break
                                            
                                    except Exception as e:
                                        logger.debug(f"Modal selector {selector} failed: {e}")
                                        continue
                            
                            # Если все еще не нашли, ждем появления
                            if not choose_button:
                                logger.info("⏳ Button not found immediately, waiting for appearance...")
                                try:
                                    choose_button = self.wait.until(
                                        EC.element_to_be_clickable((By.XPATH, '//button[contains(text(), "Выбрать")]'))
                                    )
                                    logger.info("✅ 'Выбрать' button appeared after waiting")
                                except TimeoutException:
                                    logger.error("❌ 'Выбрать' button did not appear after clicking cell")
                                    raise BookingServiceError("Кнопка 'Выбрать' не появилась после клика по ячейке")
                            
                            # Кликаем по кнопке "Выбрать"
                            try:
                                logger.info("🖱️ Clicking 'Выбрать' button...")
                                choose_button.click()
                                logger.info("✅ Clicked 'Выбрать' button successfully")
                            except Exception as e:
                                logger.warning(f"Regular click failed: {e}, trying JavaScript click...")
                                self.driver.execute_script("arguments[0].click();", choose_button)
                                logger.info("✅ Clicked 'Выбрать' button with JavaScript")
                            
                            # Ждем, пока кнопка "Запланировать" станет активной после выбора даты
                            logger.info("⏳ Waiting for 'Запланировать' button to become active after date selection...")
                            await asyncio.sleep(0.5)  # Даем время DOM обновиться
                            
                            # Логируем состояние кнопок после выбора даты
                            try:
                                calendar_buttons_after = self.driver.find_elements(By.CSS_SELECTOR, 'div[class*="Calendar-plan-buttons"] button')
                                logger.info(f"📋 Found {len(calendar_buttons_after)} buttons in calendar block after date selection")
                                for i, btn in enumerate(calendar_buttons_after):
                                    try:
                                        btn_text = btn.text.strip()
                                        btn_class = btn.get_attribute('class') or ''
                                        btn_enabled = btn.is_enabled()
                                        btn_displayed = btn.is_displayed()
                                        logger.info(f"Calendar Button {i} after selection: text='{btn_text}', enabled={btn_enabled}, displayed={btn_displayed}, class='{btn_class[:100]}...'")
                                    except Exception as e:
                                        logger.debug(f"Error getting calendar button {i} info after selection: {e}")
                            except Exception as e:
                                logger.debug(f"Error logging calendar buttons after selection: {e}")
                            
                            # Дополнительно ждем, пока кнопка станет кликабельной
                            try:
                                self.wait.until(
                                    EC.element_to_be_clickable((By.XPATH, '//span[contains(text(), "Запланировать")]/parent::button'))
                                )
                                logger.info("✅ 'Запланировать' button became clickable after date selection")
                            except TimeoutException:
                                logger.warning("⚠️ 'Запланировать' button did not become clickable, proceeding anyway...")
                            
                            # Переходим к подтверждению бронирования
                            logger.info("🚀 Step 3: Proceeding to booking confirmation...")
                            await self._confirm_booking(order_number)
                            return
                            logger.error("❌ 'Выбрать' button not found in date cell - booking cannot proceed")
                            raise BookingServiceError("Кнопка 'Выбрать' не найдена в ячейке календаря")
                            
                except Exception as e:
                    logger.debug(f"Error checking calendar cell: {e}")
                    continue
            
            raise BookingServiceError(f"Date {target_date.strftime('%d.%m.%Y')} not found in calendar")
            
        except BookingServiceError:
            raise
        except Exception as e:
            logger.error(f"Error clicking calendar date {target_date.strftime('%d.%m.%Y')}: {e}")
            raise BookingServiceError(f"Ошибка выбора даты {target_date.strftime('%d.%m.%Y')}: {str(e)}")
    
    async def _confirm_booking(self, order_number: str):
        """Подтвердить бронирование - нажать кнопку 'Запланировать' и проверить успешность"""
        try:
            logger.info("🔍 Looking for 'Запланировать' confirmation button...")
            
            # Ищем кнопку "Запланировать" в календарном блоке
            try:
                # Ждем появления кнопки
                confirm_button = self.wait.until(
                    EC.element_to_be_clickable((By.CSS_SELECTOR, 'div[class*="Calendar-plan-buttons__transfer"] button[class*="button__I8dwnFm136"]'))
                )
                logger.info("✅ Found 'Запланировать' button")
                
                # Проверяем, что кнопка активна
                if not confirm_button.is_enabled():
                    logger.warning("⚠️ Button is disabled, waiting for it to become enabled...")
                    await asyncio.sleep(0.5)
                    if not confirm_button.is_enabled():
                        raise BookingServiceError("Кнопка 'Запланировать' заблокирована")
                
                # Нажимаем кнопку
                logger.info("🖱️ Clicking 'Запланировать' button...")
                try:
                    confirm_button.click()
                    logger.info("✅ Button clicked successfully")
                except Exception as e:
                    logger.warning(f"Regular click failed: {e}, trying JavaScript click...")
                    self.driver.execute_script("arguments[0].click();", confirm_button)
                    logger.info("✅ JavaScript click successful")
                
                # Небольшая задержка после клика для обработки запроса
                await asyncio.sleep(1.0)
                
                # Проверяем успешность бронирования через переход на страницу поставок
                logger.info("🔍 Checking booking success by navigating to supplies page...")
                await self._check_booking_success(order_number)
                
            except TimeoutException:
                raise BookingServiceError("Кнопка 'Запланировать' не найдена или не стала активной")
            except Exception as e:
                logger.error(f"Error clicking 'Запланировать' button: {e}")
                raise BookingServiceError(f"Ошибка при нажатии кнопки 'Запланировать': {e}")
        
        except BookingServiceError:
            raise
        except Exception as e:
            logger.error(f"Unexpected error in _confirm_booking: {e}")
            raise BookingServiceError(f"Неожиданная ошибка при подтверждении бронирования: {e}")
    
    async def _check_booking_success(self, order_number: str):
        """Проверить успешность бронирования через переход на страницу поставок"""
        try:
            logger.info(f"📋 Using order number: {order_number}")
            
            # Переходим на страницу поставок
            logger.info("🌐 Navigating to supplies page to check booking status...")
            self.driver.get("https://seller.wildberries.ru/supplies-management/all-supplies")
            await asyncio.sleep(2)
            
            # Ищем заказ по номеру и проверяем его статус
            logger.info(f"🔍 Looking for order {order_number} in supplies list...")
            
            # Ищем строку с заказом
            order_row = None
            max_scroll_attempts = 5
            
            for scroll_attempt in range(max_scroll_attempts):
                try:
                    # Ищем все строки таблицы поставок
                    rows = self.driver.find_elements(By.CSS_SELECTOR, 'tr[class*="Table-row"], div[class*="Table-row"], [class*="supply-row"]')
                    
                    for row in rows:
                        try:
                            row_text = row.text
                            if order_number in row_text:
                                order_row = row
                                logger.info(f"✅ Found order {order_number} in row")
                                break
                        except:
                            continue
                    
                    if order_row:
                        break
                    
                    # Если не нашли, прокручиваем вниз
                    if scroll_attempt < max_scroll_attempts - 1:
                        logger.info(f"📜 Order not found, scrolling down (attempt {scroll_attempt + 1})...")
                        self.driver.execute_script("window.scrollBy(0, 500);")
                        await asyncio.sleep(1)
                    
                except Exception as e:
                    logger.debug(f"Error searching for order row: {e}")
                    continue
            
            if not order_row:
                raise BookingServiceError(f"Заказ {order_number} не найден в списке поставок")
            
            # Проверяем статус заказа
            try:
                # Ищем элементы статуса в строке
                status_elements = order_row.find_elements(By.CSS_SELECTOR, 
                    '[class*="badge"], [class*="Badge"], [class*="status"], [class*="Status"], [class*="tag"], [class*="Tag"]')
                
                status_found = False
                for status_elem in status_elements:
                    if status_elem.is_displayed():
                        status_text = status_elem.text.strip()
                        logger.info(f"📊 Found status: '{status_text}'")
                        
                        if any(keyword in status_text.lower() for keyword in ['запланировано', 'запланирован', 'планируется']):
                            logger.info("✅ Booking successful! Status changed to 'Запланировано'")
                            return
                        status_found = True
                
                if not status_found:
                    # Если не нашли элементы статуса, ищем текст в самой строке
                    row_text = order_row.text
                    logger.info(f"📋 Row text: {row_text[:200]}...")
                    
                    if any(keyword in row_text.lower() for keyword in ['запланировано', 'запланирован', 'планируется']):
                        logger.info("✅ Booking successful! Found 'Запланировано' in row text")
                        return
                
                # Если статус не изменился, бронирование не удалось
                logger.error(f"❌ Booking failed - status did not change to 'Запланировано'")
                raise BookingServiceError(f"Бронирование не удалось - статус заказа {order_number} не изменился на 'Запланировано'")
                
            except Exception as e:
                logger.error(f"Error checking order status: {e}")
                raise BookingServiceError(f"Ошибка при проверке статуса заказа: {e}")
                
        except BookingServiceError:
            raise
        except Exception as e:
            logger.error(f"Unexpected error in _check_booking_success: {e}")
            raise BookingServiceError(f"Неожиданная ошибка при проверке успешности бронирования: {e}")
    
    async def __aenter__(self):
        """Async context manager entry"""
        await self._initialize_browser()
        return self
        
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit"""
        await self._cleanup()


# Глобальный экземпляр сервиса бронирования
_global_booking_service: Optional[BookingService] = None


def get_booking_service(auth_service: Optional[WBWebAuthService] = None) -> BookingService:
    """Получить экземпляр сервиса бронирования"""
    global _global_booking_service
    if _global_booking_service is None:
        _global_booking_service = BookingService(auth_service)
    return _global_booking_service


async def cleanup_booking_service():
    """Очистить глобальный экземпляр сервиса бронирования"""
    global _global_booking_service
    if _global_booking_service is not None:
        await _global_booking_service._cleanup()
        _global_booking_service = None