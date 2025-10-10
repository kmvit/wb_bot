"""Конфигурация браузера для защиты от детекции"""

from selenium.webdriver.chrome.options import Options
from selenium import webdriver


def create_undetectable_chrome_options(profile_dir: str = None) -> Options:
    """
    Создать настройки Chrome с защитой от детекции
    
    Args:
        profile_dir: Путь к директории профиля пользователя
        
    Returns:
        Options: Настройки Chrome
    """
    options = Options()
    
    # Персистентный профиль браузера для сохранения сессии
    if profile_dir:
        options.add_argument(f'--user-data-dir={profile_dir}')
    
    # Настройки для обхода защиты
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    options.add_argument('--disable-blink-features=AutomationControlled')
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option('useAutomationExtension', False)
    options.add_argument('--disable-extensions')
    options.add_argument('--disable-plugins')
    options.add_argument('--disable-images')
    # НЕ отключаем JavaScript, он нужен для работы сайта WB
    options.add_argument('--disable-gpu')
    options.add_argument('--no-first-run')
    options.add_argument('--disable-default-apps')
    options.add_argument('--disable-popup-blocking')
    options.add_argument('--disable-translate')
    options.add_argument('--disable-background-networking')
    options.add_argument('--disable-sync')
    options.add_argument('--metrics-recording-only')
    options.add_argument('--no-report-upload')
    options.add_argument('--disable-hang-monitor')
    options.add_argument('--disable-prompt-on-repost')
    options.add_argument('--disable-client-side-phishing-detection')
    options.add_argument('--disable-component-update')
    options.add_argument('--disable-domain-reliability')
    options.add_argument('--disable-features=TranslateUI')
    options.add_argument('--disable-ipc-flooding-protection')
    options.add_argument('--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')
    
    # Включаем Performance Logging для отладки
    options.set_capability('goog:loggingPrefs', {'performance': 'ALL'})
    
    return options


def setup_undetectable_chrome(driver: webdriver.Chrome):
    """
    Настроить браузер для обхода защиты от автоматизации
    
    Args:
        driver: Экземпляр Chrome WebDriver
    """
    # Включаем логирование сетевых запросов
    driver.execute_cdp_cmd('Network.enable', {})
    driver.execute_cdp_cmd('Runtime.enable', {})
    
    # Выполняем скрипты для обхода защиты
    driver.execute_script("""
        Object.defineProperty(navigator, 'webdriver', {
            get: () => undefined,
        });
        
        Object.defineProperty(navigator, 'plugins', {
            get: () => [1, 2, 3, 4, 5],
        });
        
        Object.defineProperty(navigator, 'languages', {
            get: () => ['ru-RU', 'ru', 'en'],
        });
        
        delete window.cdc_adoQpoasnfa76pfcZLmcfl_Array;
        delete window.cdc_adoQpoasnfa76pfcZLmcfl_Promise;
        delete window.cdc_adoQpoasnfa76pfcZLmcfl_Symbol;
    """)

