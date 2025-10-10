"""Сервис авторизации через веб-интерфейс Wildberries"""

import asyncio
import json
from pathlib import Path
from typing import Dict, Optional, Tuple
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, WebDriverException
from loguru import logger

from app.config.settings import settings
from app.utils.browser_config import create_undetectable_chrome_options, setup_undetectable_chrome


class WBWebAuthError(Exception):
    """Базовый класс для ошибок веб-авторизации"""
    pass


class WBWebAuthService:
    """Сервис для авторизации через веб-интерфейс Wildberries"""
    
    def __init__(self, user_id: Optional[int] = None):
        self.driver: Optional[webdriver.Chrome] = None
        self.wait: Optional[WebDriverWait] = None
        self._phone_number: Optional[str] = None  # Сохраняем номер телефона для второго этапа
        self.user_id = user_id  # Сохраняем ID пользователя для создания уникальной директории профиля
        self._profile_dir = self._resolve_profile_dir()

    def _resolve_profile_dir(self) -> str:
        """Определить (и создать) директорию профиля браузера для пользователя"""
        base_dir = Path(settings.WB_BROWSER_PROFILES_DIR).expanduser().resolve()
        try:
            base_dir.mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            logger.warning(f"Could not create base browser profile dir {base_dir}: {exc}")
            raise

        profile_path = base_dir / f"wb_bot_user_{self.user_id or 'shared'}"
        profile_path.mkdir(parents=True, exist_ok=True)
        return str(profile_path)
    
    async def _ensure_browser_ready(self):
        """Убедиться, что браузер готов к работе"""
        if not self.driver:
            logger.info("Browser not ready, initializing...")
            await self._initialize_browser()
    
    async def _initialize_browser(self):
        """Инициализировать браузер"""
        try:
            if self.driver:
                return

            # Создаем настройки браузера с защитой от детекции
            options = create_undetectable_chrome_options(profile_dir=self._profile_dir)
            
            # Запускаем браузер с дополнительными настройками для работы под root
            from selenium.webdriver.chrome.service import Service
            
            try:
                # Пытаемся запустить браузер стандартным способом
                self.driver = webdriver.Chrome(options=options)
                logger.info("Browser started successfully with default Chrome")
            except Exception as e:
                logger.warning(f"Could not start Chrome with default method: {e}")
                try:
                    # Пробуем указать явно путь к chromium для Linux-систем
                    options.binary_location = "/usr/bin/chromium"
                    self.driver = webdriver.Chrome(options=options)
                    logger.info("Browser started using explicit chromium path")
                except Exception as e2:
                    logger.error(f"Failed to start Chrome with explicit path: {e2}")
                    # Последняя попытка - использовать chromium-browser
                    try:
                        options.binary_location = "/usr/bin/chromium-browser"
                        self.driver = webdriver.Chrome(options=options)
                        logger.info("Browser started using chromium-browser path")
                    except Exception as e3:
                        logger.error(f"All browser initialization methods failed: {e3}")
                        raise
            
            # Увеличиваем время ожидания для стабильности
            self.wait = WebDriverWait(self.driver, 15)
            
            # Настраиваем защиту от детекции
            setup_undetectable_chrome(self.driver)
            
            logger.info("Browser initialized successfully")
            
        except Exception as e:
            logger.error(f"Error initializing browser: {e}")
            await self._cleanup()
            raise
    
    async def _cleanup(self):
        """Очистить ресурсы браузера"""
        try:
            if self.driver:
                self.driver.quit()
        except:
            pass
        
        self.driver = None
        self.wait = None
        self._phone_number = None
    
    async def start_session(self):
        """Начать новую сессию (инициализировать браузер)"""
        await self._initialize_browser()
    
    async def close_session(self):
        """Закрыть сессию (закрыть браузер)"""
        await self._cleanup()
    
    def _log_network_requests(self):
        """Логировать сетевые запросы"""
        try:
            # Получаем логи сетевых запросов
            logs = self.driver.get_log('performance')
            for log in logs:
                message = json.loads(log['message'])
                if message['message']['method'] == 'Network.responseReceived':
                    response = message['message']['params']['response']
                    url = response['url']
                    status = response['status']
                    method = response.get('method', 'GET')
                    
                    # Логируем только запросы к Wildberries
                    if 'wildberries.ru' in url:
                        if status == 200:
                            logger.info(f"🌐 {method} {url} → {status}")
                        else:
                            logger.warning(f"🌐 {method} {url} → {status}")
        except Exception as e:
            logger.debug(f"Could not log network requests: {e}")
        
    async def __aenter__(self):
        """Async context manager entry"""
        await self._initialize_browser()
        return self
        
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit"""
        await self._cleanup()
    
    async def request_sms_code(self, phone_number: str) -> bool:
        """Запросить SMS код для номера телефона"""
        try:
            logger.info(f"Requesting SMS code for phone: {phone_number}")
            
            # Убеждаемся, что браузер готов
            await self._ensure_browser_ready()
            
            # Переходим на страницу входа
            logger.info("🌐 Navigating to: https://seller-auth.wildberries.ru/ru/")
            self.driver.get("https://seller-auth.wildberries.ru/ru/")
            await asyncio.sleep(3)  # Даем время на загрузку
            
            # Логируем сетевые запросы
            self._log_network_requests()
            
            # Сохраняем скриншот для отладки
            try:
                self.driver.save_screenshot("debug_phone_page.png")
                logger.info("Screenshot saved: debug_phone_page.png")
            except:
                pass
            
            # Ждем появления поля для ввода телефона
            try:
                phone_input = self.wait.until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, 'input[data-testid="phone-input"]'))
                )
                logger.info("Phone input field found")
            except TimeoutException:
                logger.error("Phone input not found with primary selector")
                # Попробуем найти альтернативные селекторы
                alternative_selectors = [
                    'input[type="text"]',
                    'input[placeholder*="999"]',
                    'input[inputmode="numeric"]'
                ]
                
                phone_input = None
                for selector in alternative_selectors:
                    try:
                        phone_input = self.driver.find_element(By.CSS_SELECTOR, selector)
                        if phone_input:
                            logger.info(f"Found phone input with selector: {selector}")
                            break
                    except:
                        continue
                
                if not phone_input:
                    raise WBWebAuthError("Не найдено поле для ввода номера телефона")
            
            # Очищаем поле и вводим номер (только цифры без +7)
            phone_input.click()  # Кликаем на поле
            await asyncio.sleep(0.5)
            phone_input.clear()  # Очищаем поле
            await asyncio.sleep(0.5)
            
            # Убираем +7 из номера, так как код страны уже выбран
            phone_digits = phone_number[2:] if phone_number.startswith('+7') else phone_number
            
            # Вводим номер по одной цифре для более реалистичного поведения
            for digit in phone_digits:
                phone_input.send_keys(digit)
                await asyncio.sleep(0.1)  # Небольшая задержка между символами
            
            await asyncio.sleep(1)
            
            # Ждем, пока кнопка станет активной
            await asyncio.sleep(1)
            
            # Ищем кнопку отправки
            submit_selectors = [
                'button[data-testid="submit-phone-button"]',
                'button[type="submit"]'
            ]
            
            submit_button = None
            for selector in submit_selectors:
                try:
                    submit_button = self.driver.find_element(By.CSS_SELECTOR, selector)
                    if submit_button:
                        # Ждем, пока кнопка станет активной (максимум 5 секунд)
                        for _ in range(10):  # 10 попыток по 0.5 секунды
                            is_disabled = submit_button.get_attribute('disabled')
                            if not is_disabled:
                                logger.info(f"Found active submit button with selector: {selector}")
                                break
                            await asyncio.sleep(0.5)
                        else:
                            logger.info(f"Submit button still disabled: {selector}")
                            submit_button = None
                        
                        if submit_button:
                            break
                except:
                    continue
            
            if not submit_button:
                raise WBWebAuthError("Не найдена активная кнопка отправки SMS кода")
            
            # Нажимаем кнопку с более реалистичным поведением
            try:
                # Наводим курсор на кнопку
                from selenium.webdriver.common.action_chains import ActionChains
                ActionChains(self.driver).move_to_element(submit_button).perform()
                await asyncio.sleep(0.5)
                
                # Нажимаем кнопку
                submit_button.click()
                logger.info("Submit button clicked, waiting for SMS code form...")
                
                # Логируем сетевые запросы после отправки
                await asyncio.sleep(2)
                self._log_network_requests()
                
                # Ждем появления формы ввода кода (до 10 секунд)
                try:
                    # Ждем появления формы с полями для ввода кода
                    self.wait.until(
                        EC.presence_of_element_located((By.CSS_SELECTOR, 'form.CodeInputContentView__form-mgPTHveibl'))
                    )
                    logger.info("SMS code form appeared")
                except TimeoutException:
                    # Альтернативный селектор для формы
                    try:
                        self.wait.until(
                            EC.presence_of_element_located((By.CSS_SELECTOR, 'ul.SimpleCodeInput-016VPGuQ+E'))
                        )
                        logger.info("SMS code input list appeared")
                    except TimeoutException:
                        # Ждем появления хотя бы одного поля для ввода кода
                        self.wait.until(
                            EC.presence_of_element_located((By.CSS_SELECTOR, 'input[data-testid="sms-code-input"]'))
                        )
                        logger.info("SMS code input field appeared")
                
                # Дополнительная пауза для стабилизации
                await asyncio.sleep(2)
                    
            except Exception as e:
                if "Timeout" in str(e):
                    raise WBWebAuthError("Превышено время ожидания появления формы ввода кода")
                raise
            
            # Проверяем, что форма кода действительно появилась
            code_inputs = self.driver.find_elements(By.CSS_SELECTOR, 'input[data-testid="sms-code-input"]')
            if not code_inputs:
                # Проверяем, не появилась ли ошибка
                error_elements = self.driver.find_elements(By.CSS_SELECTOR, '.error, .alert, [class*="error"]')
                if error_elements:
                    error_text = error_elements[0].text
                    raise WBWebAuthError(f"Ошибка при запросе SMS: {error_text}")
                
                raise WBWebAuthError("Не найдено поле для ввода SMS кода")
            
            # Сохраняем номер телефона для использования в verify_sms_code
            self._phone_number = phone_number
            
            logger.info(f"SMS code requested successfully for phone: {phone_number}")
            return True
            
        except Exception as e:
            error_msg = str(e)
            logger.error(f"Error requesting SMS code: {e}")
            
            # Проверяем тип ошибки
            if "WebDriverException" in error_msg or "browser has been closed" in error_msg:
                raise WBWebAuthError("Браузер был закрыт во время выполнения операции. Попробуйте еще раз.")
            elif "Timeout" in error_msg:
                raise WBWebAuthError("Превышено время ожидания. Попробуйте еще раз.")
            else:
                raise WBWebAuthError(f"Ошибка запроса SMS кода: {error_msg}")
    
    async def verify_sms_code(self, sms_code: str) -> Tuple[bool, Optional[Dict]]:
        """Проверить SMS код и получить данные сессии"""
        try:
            logger.info("Verifying SMS code...")
            
            # Убеждаемся, что браузер готов
            await self._ensure_browser_ready()
            
            # Ищем поля для ввода кода
            code_inputs = self.driver.find_elements(By.CSS_SELECTOR, 'input[data-testid="sms-code-input"]')
            
            if not code_inputs:
                # Попробуем альтернативные селекторы
                code_selectors = [
                    'input[type="text"]',
                    'input[name="code"]',
                    'input[placeholder*="код"]',
                    'input[placeholder*="code"]'
                ]
                
                for selector in code_selectors:
                    code_inputs = self.driver.find_elements(By.CSS_SELECTOR, selector)
                    if code_inputs:
                        break
            
            if not code_inputs:
                raise WBWebAuthError("Не найдено поле для ввода SMS кода")
            
            # Вводим SMS код (6 отдельных полей)
            logger.info(f"Found {len(code_inputs)} code input fields")
            
            if len(code_inputs) >= len(sms_code):
                for i, digit in enumerate(sms_code):
                    if i < len(code_inputs):
                        code_inputs[i].clear()  # Очищаем поле
                        code_inputs[i].send_keys(digit)
                        await asyncio.sleep(0.3)  # Задержка между вводами
                        logger.info(f"Entered digit {digit} in field {i+1}")
            else:
                # Если не нашли отдельные поля, пробуем ввести в первое поле
                code_inputs[0].clear()  # Очищаем поле
                code_inputs[0].send_keys(sms_code)
                logger.info(f"Entered full code {sms_code} in single field")
            
            await asyncio.sleep(2)  # Даем время на автоматическую отправку
            
            # Проверяем, есть ли кнопка подтверждения (может быть автоматическая отправка)
            submit_button = None
            submit_selectors = [
                'button[type="submit"]',
                '.submit-button',
                '[data-testid="submit-button"]'
            ]
            
            for selector in submit_selectors:
                try:
                    submit_button = self.driver.find_element(By.CSS_SELECTOR, selector)
                    if submit_button:
                        logger.info(f"Found submit button: {selector}")
                        break
                except:
                    continue
            
            if submit_button:
                # Нажимаем кнопку, если она есть
                submit_button.click()
                logger.info("Submit button clicked")
                await asyncio.sleep(3)
                
                # Логируем сетевые запросы после отправки SMS кода
                self._log_network_requests()
            else:
                # Если кнопки нет, возможно форма отправляется автоматически
                logger.info("No submit button found, waiting for automatic submission...")
                await asyncio.sleep(3)
                
                # Логируем сетевые запросы
                self._log_network_requests()
            
            # Проверяем, не появилась ли ошибка
            error_elements = self.driver.find_elements(By.CSS_SELECTOR, '.error, .alert, [class*="error"]')
            if error_elements:
                error_text = error_elements[0].text
                if error_text and "неверный" in error_text.lower():
                    raise WBWebAuthError("Неверный SMS код")
                elif error_text:
                    raise WBWebAuthError(f"Ошибка авторизации: {error_text}")
            
            # Ждем перехода в кабинет или появления признаков успешной авторизации
            try:
                # Ждем перехода на страницу кабинета
                self.wait.until(
                    lambda driver: 'seller.wildberries.ru' in driver.current_url
                )
            except TimeoutException:
                # Если не дождались перехода, проверяем текущий URL
                current_url = self.driver.current_url
                if not current_url or 'seller.wildberries.ru' not in current_url:
                    raise WBWebAuthError("Не удалось войти в кабинет")
            
            # Получаем cookies и session data
            cookies = self.driver.get_cookies()
            session_data = {
                'cookies': cookies,
                'local_storage': self.driver.execute_script('return { ...localStorage }'),
                'session_storage': self.driver.execute_script('return { ...sessionStorage }'),
                'user_agent': self.driver.execute_script('return navigator.userAgent')
            }
            
            # Получаем ИНН из кабинета
            inn_value = None
            seller_name = "Не указано"
            
            try:
                # Переходим на страницу с реквизитами
                logger.info("🌐 Navigating to: https://seller.wildberries.ru/supplier-settings/supplier-card")
                self.driver.get("https://seller.wildberries.ru/supplier-settings/supplier-card")
                await asyncio.sleep(2)
                
                # Логируем сетевые запросы
                self._log_network_requests()
                
                # Ищем поле с ИНН
                inn_selectors = [
                    '#taxpayerCode',
                    'input[name="taxpayerCode"]',
                    '[data-testid="taxpayer-code"]'
                ]
                
                for selector in inn_selectors:
                    try:
                        inn_element = self.driver.find_element(By.CSS_SELECTOR, selector)
                        if inn_element:
                            inn_value = inn_element.get_attribute('value')
                            if inn_value and len(inn_value) >= 10:  # ИНН должен быть длинным
                                break
                    except:
                        continue
                
                # Если не нашли через input, ищем в тексте
                if not inn_value:
                    inn_text_elements = self.driver.find_elements(By.CSS_SELECTOR, '*')
                    for element in inn_text_elements:
                        try:
                            text = element.text
                            if text and len(text) >= 10 and text.isdigit():
                                inn_value = text
                                break
                        except:
                            continue
                
                # Ищем название продавца
                seller_selectors = [
                    '[data-testid="seller-name"]',
                    '.seller-name',
                    'h1',
                    'h2',
                    '.company-name'
                ]
                
                for selector in seller_selectors:
                    try:
                        seller_element = self.driver.find_element(By.CSS_SELECTOR, selector)
                        if seller_element:
                            seller_name = seller_element.text
                            if seller_name and len(seller_name.strip()) > 0:
                                seller_name = seller_name.strip()
                                break
                    except:
                        continue
                        
            except Exception as e:
                logger.warning(f"Could not get INN and seller name: {e}")
            
            auth_data = {
                'session_data': session_data,
                'inn': inn_value or "Не найден",
                'seller_name': seller_name,
                'phone_number': self._phone_number  # Используем сохраненный номер телефона
            }
            
            logger.info(f"✅ Successfully authenticated user with INN: {inn_value}, Seller: {seller_name}")
            return True, auth_data
            
        except WBWebAuthError:
            raise
        except Exception as e:
            logger.error(f"Error verifying SMS code: {e}")
            raise WBWebAuthError(f"Ошибка проверки SMS кода: {str(e)}")
    
    async def test_session(self, session_data: Dict) -> bool:
        """Проверить, действительна ли сессия без полного восстановления"""
        try:
            await self._ensure_browser_ready()

            current_url = self.driver.current_url or ""
            if not current_url:
                self.driver.get("https://seller.wildberries.ru/")
                await asyncio.sleep(1)
                current_url = self.driver.current_url or ""

            if 'seller-auth.wildberries.ru' in current_url:
                logger.warning("❌ Session invalid - driver on auth page")
                return False

            try:
                user_markers = self.driver.find_elements(By.CSS_SELECTOR, '[data-testid*="user"], [class*="header-user"], [class*="profile"]')
                if user_markers:
                    logger.info("✅ Session is valid - user elements present")
                    return True
            except Exception as exc:
                logger.debug(f"Could not inspect DOM for auth markers: {exc}")

            await self.ensure_supplies_page()
            return True
        except WBWebAuthError:
            logger.warning("❌ Session invalid after navigation attempt")
            return False
        except Exception as e:
            logger.error(f"Error testing session: {e}")
            return False

    async def _restore_cookies_only(self, session_data: Dict):
        if not session_data.get('cookies'):
            logger.warning("No cookies in session_data to restore")
            return

        try:
            logger.info(f"🔑 Restoring {len(session_data['cookies'])} cookies for user {self.user_id}")
            self.driver.delete_all_cookies()
            restored_count = 0
            for cookie in session_data['cookies']:
                cookie_copy = dict(cookie)
                if 'expiry' in cookie_copy and isinstance(cookie_copy['expiry'], float):
                    cookie_copy['expiry'] = int(cookie_copy['expiry'])
                cookie_copy.pop('sameSite', None)
                cookie_copy.pop('priority', None)
                if 'domain' not in cookie_copy or not cookie_copy['domain']:
                    cookie_copy['domain'] = '.wildberries.ru'
                if 'path' not in cookie_copy or not cookie_copy['path']:
                    cookie_copy['path'] = '/'
                try:
                    self.driver.add_cookie(cookie_copy)
                    restored_count += 1
                except Exception as exc:
                    logger.debug(f"Could not add cookie {cookie.get('name', 'unknown')}: {exc}")
            
            logger.info(f"✅ Restored {restored_count}/{len(session_data['cookies'])} cookies")

            # Восстанавливаем localStorage и sessionStorage
            if 'local_storage' in session_data:
                try:
                    for key, value in session_data['local_storage'].items():
                        # Экранируем кавычки в значении
                        value_escaped = str(value).replace("'", "\\'").replace('"', '\\"')
                        self.driver.execute_script(f"localStorage.setItem('{key}', '{value_escaped}');")
                    logger.debug(f"Restored {len(session_data['local_storage'])} localStorage items")
                except Exception as exc:
                    logger.debug(f"Could not restore localStorage: {exc}")
            
            if 'session_storage' in session_data:
                try:
                    for key, value in session_data['session_storage'].items():
                        # Экранируем кавычки в значении
                        value_escaped = str(value).replace("'", "\\'").replace('"', '\\"')
                        self.driver.execute_script(f"sessionStorage.setItem('{key}', '{value_escaped}');")
                    logger.debug(f"Restored {len(session_data['session_storage'])} sessionStorage items")
                except Exception as exc:
                    logger.debug(f"Could not restore sessionStorage: {exc}")

            logger.debug("Cookies and storage restored, refreshing page once")
            self.driver.refresh()
            await asyncio.sleep(1)
        except Exception as exc:
            logger.warning(f"Lightweight cookie restore failed: {exc}")

    async def ensure_supplies_page(self, force_reload: bool = False):
        """Убедиться, что открыт раздел поставок"""
        await self._ensure_browser_ready()

        target_url = "https://seller.wildberries.ru/supplies-management/all-supplies"
        current_url = self.driver.current_url or ""

        if 'seller-auth.wildberries.ru' in current_url:
            raise WBWebAuthError("Необходима переавторизация")

        if force_reload or 'supplies-management/all-supplies' not in current_url:
            logger.info(f"🌐 Navigating to supplies page: {target_url}")
            self.driver.get(target_url)
        else:
            logger.info("🔄 Refreshing supplies page for fresh data")
            self.driver.refresh()

        await asyncio.sleep(1)

        try:
            self.wait.until(
                EC.presence_of_element_located((By.CSS_SELECTOR, 'table[class^="Table__table"] tbody'))
            )
            logger.info("✅ Supplies page ready")
        except TimeoutException:
            current_url = self.driver.current_url or ''
            logger.error(f"❌ Supplies page not loaded, current URL: {current_url}")
            raise WBWebAuthError("Не удалось открыть страницу поставок")

    async def get_unplanned_order_numbers(self, session_data: Dict) -> list[str]:
        """Загрузить страницу всех поставок и вернуть номера заказов со статусом 'не запланировано'."""
        max_retries = 2
        retry_count = 0
        
        while retry_count < max_retries:
            try:
                # Проверяем, готов ли браузер
                await self._ensure_browser_ready()

                # Восстанавливаем cookies из session_data
                current_url = self.driver.current_url or ""
                if not current_url or 'seller.wildberries.ru' not in current_url:
                    # Если мы не на странице WB, сначала переходим туда
                    self.driver.get("https://seller.wildberries.ru/")
                    await asyncio.sleep(1)
                
                # Восстанавливаем cookies
                await self._restore_cookies_only(session_data)

                # Каждый запрос списка выполняем с полной перезагрузкой страницы,
                # чтобы гарантированно получить актуальные данные
                await self.ensure_supplies_page(force_reload=True)

                # Даём странице обновить данные после перезагрузки
                await asyncio.sleep(1)

                # Ждем появления таблицы
                try:
                    self.wait.until(
                        EC.presence_of_element_located((By.CSS_SELECTOR, 'table[class^="Table__table"] tbody'))
                    )
                except TimeoutException:
                    # Проверяем, не перекинуло ли на страницу авторизации
                    current_url = self.driver.current_url or ''
                    if 'seller-auth.wildberries.ru' in current_url:
                        logger.warning(f"Redirected to auth page, attempt {retry_count + 1}/{max_retries}")
                        if retry_count < max_retries - 1:
                            # Попробуем обновить сессию
                            await asyncio.sleep(2)
                            retry_count += 1
                            continue
                        else:
                            raise WBWebAuthError('AUTH_REQUIRED')
                    logger.warning("Supplies table not found")
                    return []

                # Если дошли сюда, значит авторизация прошла успешно
                rows = self.driver.find_elements(By.CSS_SELECTOR, 'table[class^="Table__table"] tbody tr')
                order_numbers: list[str] = []

                for row in rows:
                    try:
                        # Первая колонка — номер заказа
                        first_cell = row.find_element(By.CSS_SELECTOR, 'td:nth-child(1) .Table__td-content__OpbOC9lNW1')
                        order_text = (first_cell.text or '').strip()
                        if not order_text or order_text == '-':
                            continue

                        # Ищем badge статуса в строке
                        badge_elements = row.find_elements(By.CSS_SELECTOR, '.Status-name-cell__8hNdIcukfX [data-name="Badge"], .Status-name-cell__status__CtSiazcngL [data-name="Badge"], span[data-name="Badge"]')
                        status_text = ''
                        if badge_elements:
                            status_text = (badge_elements[-1].text or '').strip().lower()
                        else:
                            # Фолбэк: попытка найти текст статуса в ячейках ближе к концу
                            try:
                                status_cell = row.find_elements(By.CSS_SELECTOR, 'td')[-2]
                                status_text = (status_cell.text or '').strip().lower()
                            except Exception:
                                status_text = ''

                        if 'не запланировано' in status_text:
                            order_numbers.append(order_text)
                    except Exception:
                        continue

                logger.info(f"Successfully retrieved {len(order_numbers)} unplanned orders")
                return order_numbers

            except WBWebAuthError as e:
                if str(e) == 'AUTH_REQUIRED':
                    raise
                logger.error(f"WBWebAuthError in get_unplanned_order_numbers: {e}")
                return []
            except Exception as e:
                logger.error(f"Error collecting unplanned order numbers (attempt {retry_count + 1}): {e}")
                if retry_count < max_retries - 1:
                    retry_count += 1
                    await asyncio.sleep(2)
                    continue
                else:
                    return []
        
        return []


# Словарь для хранения экземпляров сервиса авторизации по пользователям
_user_wb_auth_services: Dict[int, WBWebAuthService] = {}


def get_wb_auth_service(user_id: int = None) -> WBWebAuthService:
    """
    Получить экземпляр сервиса авторизации для пользователя
    
    Args:
        user_id: ID пользователя Telegram. Если None, создается временный экземпляр
    """
    global _user_wb_auth_services
    
    # Если ID пользователя не указан, создаем временный экземпляр
    if user_id is None:
        return WBWebAuthService()
    
    # Если для пользователя еще нет экземпляра, создаем новый
    if user_id not in _user_wb_auth_services:
        logger.info(f"Creating new WBWebAuthService for user {user_id}")
        _user_wb_auth_services[user_id] = WBWebAuthService(user_id=user_id)
    
    return _user_wb_auth_services[user_id]


async def cleanup_wb_auth_service(user_id: int = None):
    """
    Очистить экземпляр сервиса авторизации
    
    Args:
        user_id: ID пользователя Telegram. Если None, очищаются все экземпляры
    """
    global _user_wb_auth_services
    
    if user_id is None:
        # Очищаем все экземпляры
        logger.info(f"Cleaning up all {len(_user_wb_auth_services)} WBWebAuthService instances")
        for service in _user_wb_auth_services.values():
            await service.close_session()
        _user_wb_auth_services.clear()
    elif user_id in _user_wb_auth_services:
        # Очищаем только экземпляр указанного пользователя
        logger.info(f"Cleaning up WBWebAuthService for user {user_id}")
        await _user_wb_auth_services[user_id].close_session()
        del _user_wb_auth_services[user_id]
