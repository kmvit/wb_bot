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
            await self._click_calendar_date(target_date, target_warehouse_id)
            
            # Подтверждаем бронирование
            await self._confirm_booking()
            
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
    
    async def _click_calendar_date(self, target_date: datetime, target_warehouse_id: int):
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
                            await self._confirm_booking()
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
    
    async def _confirm_booking(self):
        """Подтвердить бронирование"""
        try:
            logger.info("🔍 Looking for 'Запланировать' confirmation button...")
            
            # Логируем все кнопки в календарном блоке для отладки
            try:
                calendar_buttons = self.driver.find_elements(By.CSS_SELECTOR, 'div[class*="Calendar-plan-buttons"] button')
                logger.info(f"📋 Found {len(calendar_buttons)} buttons in calendar block")
                for i, btn in enumerate(calendar_buttons):
                    try:
                        btn_text = btn.text.strip()
                        btn_class = btn.get_attribute('class') or ''
                        btn_span = ""
                        try:
                            spans = btn.find_elements(By.TAG_NAME, 'span')
                            for span in spans:
                                btn_span += span.text.strip() + " "
                        except:
                            pass
                        logger.info(f"Calendar Button {i}: text='{btn_text}', span='{btn_span.strip()}', class='{btn_class[:100]}...'")
                    except Exception as e:
                        logger.debug(f"Error getting calendar button {i} info: {e}")
            except Exception as e:
                logger.debug(f"Error logging calendar buttons: {e}")
            
            # Сначала пробуем найти кнопку сразу
            confirm_selectors = [
                # Точные селекторы для кнопки "Запланировать" из HTML
                '//span[@class="caption__0iy-jJu+aV" and contains(text(), "Запланировать")]/parent::button',
                'button.button__I8dwnFm136.m__-jdYj6QZL1.fullWidth__7XwuGaP7I+',
                'div.Calendar-plan-buttons__transfer button.button__I8dwnFm136',
                'div[class*="Calendar-plan-buttons__transfer"] button[class*="button__I8dwnFm136"]',
                'div[class*="Calendar-plan-buttons__content"] button[class*="button__I8dwnFm136"]',
                'div[class*="Calendar-plan-buttons__wrapper"] button[class*="button__I8dwnFm136"]',
                # Общие селекторы
                '//button[contains(text(), "Запланировать")]',
                '//button[text()="Запланировать"]',
                '//span[contains(text(), "Запланировать")]/parent::button',
                '//span[contains(@class, "caption") and contains(text(), "Запланировать")]/parent::button',
                'div[class*="Calendar-plan-buttons__content"] button',
                'div[class*="Calendar-plan-buttons__wrapper"] button',
                'div[class*="Calendar-plan-buttons__transfer"] button',
                'button[class*="button__I8dwnFm136"]',
                'button[class*="button__ymbakhzRxO"]',
                'button[class*="fullWidth"]',
                'button[class*="fullWidth__7XwuGaP7I+"]',
                'button[class*="fullWidth__7wrfVPCWJP"]',
                'button[class*="confirm"]',
                'button[class*="submit"]',
                'button[class*="primary"]',
                'div[class*="modal"] button',
                'div[class*="Modal"] button',
                'div[class*="popup"] button',
                'div[class*="Popup"] button',
                'button[data-testid*="confirm"]',
                'button[data-testid*="submit"]',
                'button[data-testid*="plan"]'
            ]
            
            confirm_button = None
            for selector in confirm_selectors:
                try:
                    if selector.startswith('//'):
                        # XPath селектор
                        elements = self.driver.find_elements(By.XPATH, selector)
                    else:
                        # CSS селектор
                        elements = self.driver.find_elements(By.CSS_SELECTOR, selector)
                    
                        for element in elements:
                            if element.is_displayed():
                                button_text = element.text.strip()
                                button_class = element.get_attribute('class') or ''
                                
                                # Проверяем текст кнопки и текст внутри span элементов
                                span_text = ""
                                try:
                                    spans = element.find_elements(By.TAG_NAME, 'span')
                                    for span in spans:
                                        span_text += span.text.strip() + " "
                                except:
                                    pass
                                
                                full_text = (button_text + " " + span_text).lower()
                                
                                # Проверяем, что это именно кнопка "Запланировать" (не "Отменить")
                                if (any(keyword in full_text for keyword in ["запланировать", "plan"]) and 
                                    'button__ymbakhzRxO' not in button_class):  # Исключаем кнопку "Отменить"
                                    confirm_button = element
                                    logger.info(f"✅ Found 'Запланировать' button with selector: {selector}")
                                    logger.info(f"Button text: '{button_text}', Span text: '{span_text.strip()}', Class: '{button_class[:100]}...'")
                                    break
                    
                    if confirm_button:
                        break
                        
                except Exception as e:
                    logger.debug(f"Selector {selector} failed: {e}")
                    continue
            
            # Если не нашли сразу, пробуем специфичные селекторы для календарного блока
            if not confirm_button:
                logger.info("🔍 Trying calendar-specific selectors...")
                calendar_selectors = [
                    '//span[@class="caption__0iy-jJu+aV" and contains(text(), "Запланировать")]/parent::button',
                    'div.Calendar-plan-buttons__transfer button.button__I8dwnFm136',
                    'div[class*="Calendar-plan-buttons__transfer"] button[class*="button__I8dwnFm136"]',
                    'div[class*="Calendar-plan-buttons__content"] button[class*="button__I8dwnFm136"]',
                    'button.button__I8dwnFm136.fullWidth__7XwuGaP7I+',
                    'div[class*="Calendar-plan-buttons__wrapper"] button[class*="button__I8dwnFm136"]'
                ]
                
                for selector in calendar_selectors:
                    try:
                        if selector.startswith('//'):
                            elements = self.driver.find_elements(By.XPATH, selector)
                        else:
                            elements = self.driver.find_elements(By.CSS_SELECTOR, selector)
                        
                        for element in elements:
                            if element.is_displayed():
                                button_text = element.text.strip()
                                button_class = element.get_attribute('class') or ''
                                
                                # Проверяем, что это именно кнопка "Запланировать" (не "Отменить")
                                if (any(keyword in button_text.lower() for keyword in ["запланировать", "plan"]) and 
                                    'button__ymbakhzRxO' not in button_class):  # Исключаем кнопку "Отменить"
                                    confirm_button = element
                                    logger.info(f"✅ Found 'Запланировать' button with calendar selector: {selector}")
                                    logger.info(f"Button text: '{button_text}', Class: '{button_class[:100]}...'")
                                    break
                        
                        if confirm_button:
                            break
                            
                    except Exception as e:
                        logger.debug(f"Calendar selector {selector} failed: {e}")
                        continue
            
            # Если все еще не нашли, ждем появления с дополнительными попытками
            if not confirm_button:
                logger.info("⏳ Button not found with specific selectors, waiting for appearance...")
                
                # Даем дополнительное время для обновления DOM
                await asyncio.sleep(0.3)
                
                try:
                    # Пробуем найти кнопку с более широким поиском
                    confirm_button = self.wait.until(
                        EC.element_to_be_clickable((By.XPATH, '//span[contains(text(), "Запланировать")]/parent::button'))
                    )
                    logger.info("✅ 'Запланировать' button appeared after waiting")
                except TimeoutException:
                    # Пробуем альтернативные селекторы
                    try:
                        confirm_button = self.wait.until(
                            EC.element_to_be_clickable((By.CSS_SELECTOR, 'div[class*="Calendar-plan-buttons__transfer"] button[class*="button__I8dwnFm136"]'))
                        )
                        logger.info("✅ Found 'Запланировать' button with CSS selector after waiting")
                    except TimeoutException:
                        # Последняя попытка - ищем любую кнопку с текстом "Запланировать"
                        try:
                            confirm_button = self.wait.until(
                                EC.element_to_be_clickable((By.XPATH, '//button[contains(text(), "Запланировать")]'))
                            )
                            logger.info("✅ Found 'Запланировать' button after extended waiting")
                        except TimeoutException:
                            # Финальная попытка - ищем по классу кнопки
                            try:
                                confirm_button = self.wait.until(
                                    EC.element_to_be_clickable((By.CSS_SELECTOR, 'button.button__I8dwnFm136.fullWidth__7XwuGaP7I+'))
                                )
                                logger.info("✅ Found 'Запланировать' button by class after extended waiting")
                            except TimeoutException:
                                raise BookingServiceError("Кнопка 'Запланировать' не найдена после всех попыток")
            
            # Ждем, пока кнопка станет активной (не в состоянии loading)
            logger.info("⏳ Waiting for button to become active (not loading)...")
            try:
                # Ждем, пока кнопка станет кликабельной
                self.wait.until(EC.element_to_be_clickable((By.XPATH, '//span[contains(text(), "Запланировать")]/parent::button')))
                logger.info("✅ Button became clickable")
            except TimeoutException:
                logger.warning("⚠️ Button did not become clickable, trying anyway...")
            
            # Находим кнопку заново (чтобы избежать stale element reference)
            logger.info("🔍 Re-finding button to avoid stale element reference...")
            try:
                # Ищем именно кнопку "Запланировать" с правильным классом
                confirm_button = self.driver.find_element(By.CSS_SELECTOR, 'div[class*="Calendar-plan-buttons__transfer"] button[class*="button__I8dwnFm136"]')
                logger.info("✅ Button re-found successfully with correct class")
            except NoSuchElementException:
                # Пробуем альтернативные селекторы
                try:
                    confirm_button = self.driver.find_element(By.XPATH, '//span[contains(text(), "Запланировать")]/parent::button[contains(@class, "button__I8dwnFm136")]')
                    logger.info("✅ Button found with XPath selector")
                except NoSuchElementException:
                    # Последняя попытка - ищем по тексту и проверяем класс
                    try:
                        all_buttons = self.driver.find_elements(By.CSS_SELECTOR, 'div[class*="Calendar-plan-buttons"] button')
                        for btn in all_buttons:
                            btn_text = btn.text.strip()
                            btn_class = btn.get_attribute('class') or ''
                            if 'запланировать' in btn_text.lower() and 'button__I8dwnFm136' in btn_class:
                                confirm_button = btn
                                logger.info("✅ Button found by text and class verification")
                                break
                        else:
                            raise NoSuchElementException("Button not found")
                    except NoSuchElementException:
                        raise BookingServiceError("Кнопка 'Запланировать' не найдена после повторного поиска")
            
            # Проверяем состояние кнопки
            try:
                button_enabled = confirm_button.is_enabled()
                button_displayed = confirm_button.is_displayed()
                button_text = confirm_button.text.strip()
                button_class = confirm_button.get_attribute('class') or ''
                logger.info(f"🔍 Button state: enabled={button_enabled}, displayed={button_displayed}, text='{button_text}', class='{button_class[:100]}...'")
                
                # Проверяем, не заблокирована ли кнопка
                if not button_enabled:
                    logger.warning("⚠️ Button is disabled, waiting for it to become enabled...")
                    await asyncio.sleep(0.5)
                    # Перепроверяем состояние
                    button_enabled = confirm_button.is_enabled()
                    logger.info(f"🔍 Button enabled after wait: {button_enabled}")
                
            except Exception as e:
                logger.warning(f"Error checking button state: {e}")
            
            # Дополнительная задержка перед кликом для стабильности
            await asyncio.sleep(0.2)
            
            # Кликаем по кнопке с обработкой перекрытия элементов
            try:
                logger.info("🖱️ Clicking 'Запланировать' button...")
                
                # Сначала пробуем прокрутить к кнопке и убрать перекрывающие элементы
                try:
                    self.driver.execute_script("arguments[0].scrollIntoView({behavior: 'smooth', block: 'center'});", confirm_button)
                    await asyncio.sleep(0.2)
                    
                    # Пробуем убрать перекрывающие элементы
                    self.driver.execute_script("""
                        // Убираем перекрывающие элементы
                        var overlays = document.querySelectorAll('[class*="Calendar-cell__cell-content"]');
                        overlays.forEach(function(overlay) {
                            if (overlay.style) {
                                overlay.style.pointerEvents = 'none';
                            }
                        });
                    """)
                    await asyncio.sleep(0.1)
                except Exception as e:
                    logger.debug(f"Error preparing button for click: {e}")
                
                # Пробуем обычный клик
                confirm_button.click()
                logger.info("✅ Button clicked successfully")
                
            except Exception as e:
                logger.warning(f"Regular click failed: {e}, trying JavaScript click...")
                try:
                    # JavaScript клик с дополнительными проверками
                    self.driver.execute_script("""
                        // Убираем все перекрывающие элементы
                        var overlays = document.querySelectorAll('[class*="Calendar-cell__cell-content"], [class*="modal-overlay"], [class*="backdrop"]');
                        overlays.forEach(function(overlay) {
                            if (overlay.style) {
                                overlay.style.pointerEvents = 'none';
                                overlay.style.zIndex = '-1';
                            }
                        });
                        
                        // Кликаем по кнопке
                        arguments[0].click();
                    """, confirm_button)
                    logger.info("✅ JavaScript click successful")
                except Exception as e2:
                    logger.error(f"JavaScript click also failed: {e2}")
                    
                    # Последняя попытка - клик по координатам
                    try:
                        logger.info("🔄 Trying click by coordinates...")
                        location = confirm_button.location
                        size = confirm_button.size
                        x = location['x'] + size['width'] // 2
                        y = location['y'] + size['height'] // 2
                        
                        from selenium.webdriver.common.action_chains import ActionChains
                        actions = ActionChains(self.driver)
                        actions.move_to_element(confirm_button).click().perform()
                        logger.info("✅ Click by coordinates successful")
                    except Exception as e3:
                        logger.error(f"Click by coordinates also failed: {e3}")
                        raise BookingServiceError(f"Не удалось кликнуть по кнопке 'Запланировать': {e3}")
            
            # Небольшая задержка после клика
            await asyncio.sleep(1.0)
            
            # Проверяем, что что-то изменилось на странице после клика
            try:
                # Проверяем, есть ли изменения в DOM или URL
                current_url = self.driver.current_url
                logger.info(f"📍 Current URL after click: {current_url}")
                
                # Проверяем, появились ли новые элементы или изменились существующие
                modals_after_click = self.driver.find_elements(By.CSS_SELECTOR, '[class*="modal"], [class*="Modal"], [class*="popup"], [class*="Popup"]')
                visible_modals_after = [m for m in modals_after_click if m.is_displayed()]
                logger.info(f"📋 Visible modals after click: {len(visible_modals_after)}")
                
                # Проверяем, изменился ли статус заказа
                try:
                    status_elements = self.driver.find_elements(By.CSS_SELECTOR, '[class*="badge"], [class*="Badge"]')
                    for status_elem in status_elements:
                        if status_elem.is_displayed():
                            status_text = status_elem.text.strip()
                            logger.info(f"📊 Status after click: '{status_text}'")
                            if any(keyword in status_text.lower() for keyword in ['запланировано', 'запланирован', 'планируется']):
                                logger.info("✅ Status changed to 'Запланировано' - booking successful!")
                                return
                except Exception as e:
                    logger.debug(f"Error checking status: {e}")
                
                # Проверяем, исчезла ли кнопка "Запланировать" (признак успешного бронирования)
                try:
                    plan_buttons = self.driver.find_elements(By.CSS_SELECTOR, 'div[class*="Calendar-plan-buttons"] button')
                    plan_button_count = len([btn for btn in plan_buttons if btn.is_displayed() and 'запланировать' in btn.text.lower()])
                    if plan_button_count == 0:
                        logger.info("✅ 'Запланировать' button disappeared - booking likely successful!")
                        return
                except Exception as e:
                    logger.debug(f"Error checking button disappearance: {e}")
                
                # Проверяем, исчезло ли модальное окно (признак успешного бронирования)
                try:
                    modals = self.driver.find_elements(By.CSS_SELECTOR, '[class*="modal"], [class*="Modal"], [class*="popup"], [class*="Popup"]')
                    visible_modals = [m for m in modals if m.is_displayed()]
                    if len(visible_modals) == 0:
                        logger.info("✅ All modal windows closed - booking likely successful!")
                        return
                except Exception as e:
                    logger.debug(f"Error checking modal visibility: {e}")
                    
            except Exception as e:
                logger.debug(f"Error checking page changes: {e}")
            
            # Ждем завершения бронирования
            try:
                logger.info("⏳ Waiting for booking confirmation...")
                
                # Проверяем несколько признаков успешного бронирования
                confirmation_indicators = [
                    # Исчезновение модального окна календаря
                    EC.invisibility_of_element_located((By.CSS_SELECTOR, 'div[class*="Calendar-plan-buttons"]')),
                    # Исчезновение модального окна
                    EC.invisibility_of_element_located((By.CSS_SELECTOR, '[class*="modal"], [class*="Modal"], [class*="popup"], [class*="Popup"]')),
                    # Появление уведомления об успехе
                    EC.presence_of_element_located((By.CSS_SELECTOR, '[class*="success"], [class*="Success"], [class*="notification"]')),
                    # Изменение URL
                    EC.url_contains('supplies-management'),
                    # Изменение текста статуса на "Запланировано"
                    EC.text_to_be_present_in_element((By.CSS_SELECTOR, '[class*="badge"], [class*="Badge"]'), 'Запланировано'),
                    # Появление уведомления об ошибке
                    EC.presence_of_element_located((By.CSS_SELECTOR, '[class*="error"], [class*="Error"], [class*="alert"]'))
                ]
                
                # Ждем любого из признаков
                self.wait.until(EC.any_of(*confirmation_indicators))
                logger.info("✅ Booking confirmation completed")
                
                # Дополнительная проверка - убеждаемся, что модальное окно закрылось
                try:
                    modals = self.driver.find_elements(By.CSS_SELECTOR, '[class*="modal"], [class*="Modal"], [class*="popup"], [class*="Popup"]')
                    visible_modals = [m for m in modals if m.is_displayed()]
                    if not visible_modals:
                        logger.info("✅ Modal window closed - booking successful")
                    else:
                        logger.warning(f"⚠️ {len(visible_modals)} modal windows still visible")
                except Exception as e:
                    logger.debug(f"Error checking modal visibility: {e}")
            except TimeoutException:
                logger.warning("⚠️ Timeout waiting for booking confirmation")
                
                # Проверяем, есть ли еще модальное окно
                try:
                    modal_still_open = self.driver.find_elements(By.CSS_SELECTOR, '[class*="modal"], [class*="Modal"], [class*="popup"], [class*="Popup"]')
                    if modal_still_open:
                        logger.error("❌ Modal window still open - booking was not completed")
                        raise BookingServiceError("Бронирование не завершено - модальное окно все еще открыто")
                    
                    # Проверяем текущее состояние страницы
                    current_url = self.driver.current_url
                    logger.info(f"📍 Current URL after timeout: {current_url}")
                    
                    # Если мы все еще на странице деталей заказа, бронирование не удалось
                    if 'supply-detail' in current_url:
                        logger.error("❌ Still on supply detail page - booking failed")
                        raise BookingServiceError("Бронирование не удалось - остались на странице деталей заказа")
                    
                except BookingServiceError:
                    raise
                except Exception as e:
                    logger.debug(f"Error checking booking status: {e}")
                
                # Проверяем, есть ли модальные окна или уведомления
                try:
                    modals = self.driver.find_elements(By.CSS_SELECTOR, '[class*="modal"], [class*="Modal"], [class*="popup"], [class*="Popup"]')
                    for modal in modals:
                        if modal.is_displayed():
                            logger.info(f"📋 Found modal: {modal.text[:100]}...")
                            # Логируем HTML модального окна для отладки
                            logger.debug(f"Modal HTML: {modal.get_attribute('outerHTML')[:500]}...")
                    
                    alerts = self.driver.find_elements(By.CSS_SELECTOR, '[class*="alert"], [class*="Alert"], [class*="notification"]')
                    for alert in alerts:
                        if alert.is_displayed():
                            logger.info(f"🔔 Found alert: {alert.text[:100]}...")
                except Exception as e:
                    logger.debug(f"Error checking modals/alerts: {e}")
            
        except BookingServiceError:
            raise
        except Exception as e:
            logger.error(f"Error confirming booking: {e}")
            raise BookingServiceError(f"Ошибка подтверждения бронирования: {str(e)}")
    
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