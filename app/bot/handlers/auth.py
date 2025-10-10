"""Обработчики управления API-токенами Wildberries (только авторизация)"""

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from loguru import logger

from app.services.wildberries_api import wb_api, WildberriesAPIError, WildberriesAuthError
from app.services.wb_web_auth import get_wb_auth_service, cleanup_wb_auth_service, WBWebAuthError
from app.database.database import AsyncSessionLocal
from app.database.repositories.user_repo import UserRepository


# Создаем роутер для обработчиков авторизации
auth_router = Router()


class AuthStates(StatesGroup):
    """Состояния для процесса авторизации"""
    waiting_for_api_token = State()
    waiting_for_phone = State()  # Ожидание номера телефона
    waiting_for_sms_code = State()  # Ожидание SMS кода


@auth_router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    """Обработчик команды /start"""
    await state.clear()
    
    welcome_text = """
🤖 <b>Добро пожаловать в Wildberries Bot!</b>

Этот бот поможет вам быстро находить и бронировать слоты для поставок на Wildberries с реакцией менее 0.3 секунды.

<b>Для начала работы нужно добавить API-токен:</b>
• Войдите в личный кабинет Wildberries
• Перейдите в раздел "Настройки" → "Доступ к API"
• Создайте новый токен с правами на чтение и управление поставками
• Отправьте токен боту командой /add_token

<b>Доступные команды:</b>
/add_token - Добавить API-токен
/cabinet_info - Информация о кабинете
/help - Помощь
    """
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Информация о кабинете", callback_data="cabinet_info")],
        [InlineKeyboardButton(text="➕ Добавить API-токен", callback_data="add_token")],
        [InlineKeyboardButton(text="ℹ️ Помощь", callback_data="help")]
    ])
    
    await message.answer(welcome_text, reply_markup=keyboard, parse_mode="HTML")


@auth_router.message(Command("add_token"))
@auth_router.callback_query(F.data == "add_token")
async def cmd_add_token(event: Message | CallbackQuery, state: FSMContext):
    """Обработчик команды добавления API-токена"""
    text = """
🔑 <b>Добавление API-токена</b>

Для получения API-токена:

1️⃣ Войдите в личный кабинет поставщика Wildberries
2️⃣ Перейдите в "Настройки" → "Доступ к API"
3️⃣ Создайте новый токен со следующими правами:
   • Маркетплейс (чтение)
   • Поставки (чтение и запись)
   • Статистика (чтение)

4️⃣ Скопируйте токен и отправьте его следующим сообщением

<b>⚠️ Важно:</b> Токен будет зашифрован и безопасно сохранен
    """
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")]
    ])
    
    if isinstance(event, Message):
        await event.answer(text, reply_markup=keyboard, parse_mode="HTML")
    else:
        await event.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    
    await state.set_state(AuthStates.waiting_for_api_token)


@auth_router.message(StateFilter(AuthStates.waiting_for_api_token))
async def process_api_token(message: Message, state: FSMContext):
    """Обработка полученного API-токена"""
    api_token = message.text.strip()
    
    if not api_token:
        await message.answer("❌ Токен не может быть пустым. Попробуйте еще раз:")
        return
    
    # Удаляем сообщение с токеном для безопасности
    try:
        await message.delete()
    except:
        pass
    
    # Показываем индикатор обработки
    processing_msg = await message.answer("🔄 Проверяю API-токен...")
    
    try:
        # Проверяем токен через API
        async with wb_api:
            is_valid = await wb_api.validate_api_token(api_token)
            
            if not is_valid:
                await processing_msg.edit_text(
                    "❌ <b>Неверный API-токен</b>\n\n"
                    "Проверьте правильность токена и его права доступа.\n"
                    "Попробуйте еще раз или нажмите /add_token",
                    parse_mode="HTML"
                )
                await state.clear()
                return
            
            # Получаем информацию о кабинете
            cabinet_info = await wb_api.get_cabinet_info(api_token)
            seller_info = cabinet_info.get('seller_info', {})
            
            # Сохраняем токен в базу данных
            async with AsyncSessionLocal() as session:
                user_repo = UserRepository(session)
                
                # Получаем или создаем пользователя
                user = await user_repo.get_or_create_user(
                    telegram_id=message.from_user.id,
                    username=message.from_user.username,
                    first_name=message.from_user.first_name,
                    last_name=message.from_user.last_name
                )
                
                # Сохраняем токен
                await user_repo.save_wb_token(user, api_token)
            
            # Формируем информацию для отображения
            token_status = "✅ Активен" if cabinet_info.get('api_token_valid') else "❌ Неактивен"
            test_status = "✅ Пройден" if cabinet_info.get('token_test_passed') else "❌ Не пройден"
            
            # Извлекаем основную информацию о продавце согласно API документации
            seller_name = seller_info.get('name', 'Не указано')
            seller_id = seller_info.get('sid', 'Не указано')
            trade_mark = seller_info.get('tradeMark', 'Не указано')
            
            success_text = f"""
✅ <b>API-токен успешно добавлен!</b>

📊 <b>Информация о продавце:</b>
• Название: {seller_name}
• ID продавца: {seller_id}
• Торговая марка: {trade_mark}
• Статус токена: {token_status}
• Тест подключения: {test_status}

<b>Что дальше?</b>
• /cabinet_info - Подробная информация о кабинете
• Мои мониторинги - Просмотр активных мониторингов
• Автобронирование - Автоматическое бронирование найденных слотов
• Обновить список складов - Обновить список складов для автобронирования
• Удалить токен - Удалить токен для автобронирования
• Добавить новый токен - Добавить новый токен для автобронирования


            """
            
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="📊 Информация о кабинете", callback_data="cabinet_info")],
                [InlineKeyboardButton(text="📊 Мои мониторинги", callback_data="my_monitorings")]
            ])
            
            await processing_msg.edit_text(success_text, reply_markup=keyboard, parse_mode="HTML")
            
    except WildberriesAuthError as e:
        await processing_msg.edit_text(
            f"❌ <b>Ошибка авторизации</b>\n\n{str(e)}\n\n"
            "Проверьте правильность токена и попробуйте еще раз.",
            parse_mode="HTML"
        )
        logger.warning(f"Auth error for user {message.from_user.id}: {e}")
        
    except WildberriesAPIError as e:
        error_text = str(e)
        if "лимит запросов" in error_text.lower():
            await processing_msg.edit_text(
                f"⏳ <b>Лимит запросов API</b>\n\n"
                f"{error_text}\n\n"
                "Это нормально - Wildberries ограничивает частоту запросов.\n"
                "Попробуйте через минуту.",
                parse_mode="HTML"
            )
        else:
            await processing_msg.edit_text(
                f"❌ <b>Ошибка API</b>\n\n{error_text}\n\n"
                "Попробуйте позже или обратитесь в поддержку.",
                parse_mode="HTML"
            )
        logger.error(f"API error for user {message.from_user.id}: {e}")
        
    except Exception as e:
        await processing_msg.edit_text(
            "❌ <b>Произошла неожиданная ошибка</b>\n\n"
            "Попробуйте позже или обратитесь в поддержку.",
            parse_mode="HTML"
        )
        logger.error(f"Unexpected error processing token for user {message.from_user.id}: {e}")
    
    finally:
        await state.clear()


@auth_router.callback_query(F.data == "cancel")
async def callback_cancel(callback: CallbackQuery, state: FSMContext):
    """Отмена текущего действия"""
    await state.clear()
    await callback.message.edit_text(
        "❌ Действие отменено.\n\nВоспользуйтесь командой /start для начала работы."
    )


@auth_router.message(Command("help"))
async def cmd_help(message: Message):
    """Показать помощь по команде /help"""
    help_text = """
🤖 <b>Помощь по использованию бота</b>

<b>Основные команды:</b>
/start - Начать работу с ботом
/add_token - Добавить API-токен Wildberries
/cabinet_info - Информация о кабинете
/help - Эта справка

<b>Как получить API-токен:</b>
1. Войдите в кабинет поставщика Wildberries
2. Настройки → Доступ к API
3. Создайте токен с правами на поставки и статистику
4. Скопируйте и отправьте боту

<b>Безопасность:</b>
• Все токены шифруются перед сохранением
• Сообщения с токенами автоматически удаляются
• Доступ только у вас

<b>Поддержка:</b>
Если возникли проблемы, обратитесь к разработчику.
    """
    
    await message.answer(help_text, parse_mode="HTML")


@auth_router.callback_query(F.data == "help")
async def callback_help(callback: CallbackQuery):
    """Показать помощь по callback"""
    help_text = """
🤖 <b>Помощь по использованию бота</b>

<b>Основные команды:</b>
/start - Начать работу с ботом
/add_token - Добавить API-токен Wildberries
/cabinet_info - Информация о кабинете
/help - Эта справка

<b>Как получить API-токен:</b>
1. Войдите в кабинет поставщика Wildberries
2. Настройки → Доступ к API
3. Создайте токен с правами на поставки и статистику
4. Скопируйте и отправьте боту

<b>Безопасность:</b>
• Все токены шифруются перед сохранением
• Сообщения с токенами автоматически удаляются
• Доступ только у вас

<b>Поддержка:</b>
Если возникли проблемы, обратитесь к разработчику.
    """
    
    await callback.message.edit_text(help_text, parse_mode="HTML")


@auth_router.callback_query(F.data == "start")
async def callback_start(callback: CallbackQuery):
    """Обработчик callback для главного меню"""
    welcome_text = """
🤖 <b>Добро пожаловать в Wildberries Bot!</b>

Этот бот поможет вам быстро находить и бронировать слоты для поставок на Wildberries с реакцией менее 0.3 секунды.

<b>Для начала работы нужно добавить API-токен:</b>
• Войдите в личный кабинет Wildberries
• Перейдите в раздел "Настройки" → "Доступ к API"
• Создайте новый токен с правами на чтение и управление поставками
• Отправьте токен боту командой /add_token

<b>Доступные команды:</b>
/add_token - Добавить API-токен
/cabinet_info - Информация о кабинете
/help - Помощь
    """
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Информация о кабинете", callback_data="cabinet_info")],
        [InlineKeyboardButton(text="➕ Добавить API-токен", callback_data="add_token")],
        [InlineKeyboardButton(text="ℹ️ Помощь", callback_data="help")]
    ])
    
    await callback.message.edit_text(welcome_text, reply_markup=keyboard, parse_mode="HTML")


@auth_router.callback_query(F.data == "phone_auth")
async def start_phone_auth(callback: CallbackQuery, state: FSMContext):
    """Начать авторизацию по номеру телефона"""
    user_id = callback.from_user.id
    
    # Проверяем, не авторизован ли уже пользователь
    async with AsyncSessionLocal() as session:
        user_repo = UserRepository(session)
        user = await user_repo.get_by_telegram_id(user_id)
        
        if user and user.has_phone_auth():
            # Проверяем актуальность сохраненной веб-сессии
            session_data = await user_repo.get_phone_auth_session(user)
            is_session_valid = False
            if session_data:
                try:
                    wb_auth_for_test = get_wb_auth_service(user_id=user_id)
                    is_session_valid = await wb_auth_for_test.test_session(session_data)
                    
                    if is_session_valid:
                        logger.info(f"Session is valid for user {user_id}")
                    else:
                        logger.warning(f"Session expired for user {user_id}, clearing...")
                        # Очищаем истекшую сессию
                        await user_repo.clear_phone_auth(user)
                        await session.commit()
                        
                except Exception as e:
                    logger.error(f"Error testing session for user {user_id}: {e}")
                    is_session_valid = False
                    # Очищаем сессию при ошибке
                    await user_repo.clear_phone_auth(user)
                    await session.commit()

            if is_session_valid:
                # Если сессия валидна — сразу переходим к экрану автобронирования (список заказов)
                try:
                    callback.data = "auto_booking"
                    from app.bot.handlers.cabinet import callback_auto_booking
                    await callback_auto_booking(callback, state)
                    return
                except Exception as e:
                    logger.error(f"Error opening auto booking for user {user_id}: {e}")
                    # На случай ошибки — предлагаем явный переход в автобронирование
                    text = (
                        "❌ <b>Не удалось открыть экран автобронирования</b>\n\n"
                        "Нажмите кнопку ниже, чтобы перейти."
                    )
                    keyboard = InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text="🤖 Автобронирование", callback_data="auto_booking")],
                        [InlineKeyboardButton(text="🔄 Переавторизоваться", callback_data="phone_reauth")]
                    ])
                    await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
                    return
            # Если сессия невалидна — продолжаем ниже обычный процесс авторизации (ввод телефона)
    
    # Пользователь не авторизован - показываем стандартный интерфейс
    text = """
📱 <b>Авторизация по номеру телефона</b>

Для авторизации через номер телефона:
1️⃣ Введите номер телефона в формате +7XXXXXXXXXX
2️⃣ На ваш номер придет SMS с кодом
3️⃣ Введите полученный код

<b>⚠️ Важно:</b> Данные сессии будут зашифрованы и безопасно сохранены

<b>🔒 Безопасность:</b> Номер телефона и SMS код будут удалены после авторизации
    """
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")]
    ])
    
    await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    if state:  # Проверяем, что state не None (для совместимости с вызовом из cabinet.py)
        await state.set_state(AuthStates.waiting_for_phone)


@auth_router.message(StateFilter(AuthStates.waiting_for_phone))
async def process_phone_number(message: Message, state: FSMContext):
    """Обработка номера телефона"""
    phone = message.text.strip()
    user_id = message.from_user.id
    
    logger.info(f"📱 User {user_id} entered phone number: {phone}")
    
    # Валидация номера телефона
    if not phone.startswith('+7') or len(phone) != 12 or not phone[1:].isdigit():
        logger.warning(f"❌ User {user_id} entered invalid phone format: {phone}")
        await message.answer("❌ Неверный формат номера. Введите номер в формате +7XXXXXXXXXX")
        return
    
    # Удаляем сообщение с номером для безопасности
    try:
        await message.delete()
    except:
        pass
    
    # Показываем индикатор обработки
    processing_msg = await message.answer("🔄 Запрашиваем SMS код...")
    
    try:
        # Запрашиваем SMS код
        wb_auth = get_wb_auth_service(user_id=user_id)
        await wb_auth.start_session()  # Начинаем сессию браузера
        
        success = await wb_auth.request_sms_code(phone)
        
        if not success:
            await wb_auth.close_session()  # Закрываем браузер при ошибке
            await processing_msg.edit_text(
                "❌ <b>Ошибка запроса SMS</b>\n\n"
                "Не удалось отправить SMS код. Проверьте номер телефона и попробуйте еще раз.",
                parse_mode="HTML"
            )
            await state.clear()
            return
        
        # Сохраняем номер телефона в состоянии
        await state.update_data(phone_number=phone)
        await state.set_state(AuthStates.waiting_for_sms_code)
        
        # Создаем клавиатуру с кнопкой отмены
        cancel_keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Отменить авторизацию", callback_data="cancel_phone_auth")]
        ])
        
        await processing_msg.edit_text(
            f"✅ <b>SMS код отправлен</b>\n\n"
            f"На номер {phone} отправлен SMS код.\n"
            f"Введите полученный код (браузер остается открытым):\n\n"
            f"⚠️ <i>Браузер будет открыт до ввода кода или отмены</i>",
            parse_mode="HTML",
            reply_markup=cancel_keyboard
        )
            
    except WBWebAuthError as e:
        await cleanup_wb_auth_service()  # Закрываем браузер при ошибке
        await processing_msg.edit_text(
            f"❌ <b>Ошибка авторизации</b>\n\n{str(e)}\n\n"
            "Попробуйте еще раз или обратитесь в поддержку.",
            parse_mode="HTML"
        )
        logger.error(f"WB Web Auth error for user {message.from_user.id}: {e}")
        await state.clear()
        
    except Exception as e:
        await cleanup_wb_auth_service()  # Закрываем браузер при ошибке
        await processing_msg.edit_text(
            "❌ <b>Произошла ошибка</b>\n\n"
            "Попробуйте позже или обратитесь в поддержку.",
            parse_mode="HTML"
        )
        logger.error(f"Error requesting SMS for user {message.from_user.id}: {e}")
        await state.clear()


@auth_router.message(StateFilter(AuthStates.waiting_for_sms_code))
async def process_sms_code(message: Message, state: FSMContext):
    """Обработка SMS кода"""
    sms_code = message.text.strip()
    user_id = message.from_user.id
    
    logger.info(f"🔐 User {user_id} entered SMS code: {sms_code}")
    
    if not sms_code.isdigit() or len(sms_code) != 6:
        logger.warning(f"❌ User {user_id} entered invalid SMS code format: {sms_code}")
        await message.answer("❌ Неверный формат кода. Введите 6-значный код из SMS")
        return
    
    # Удаляем сообщение с кодом для безопасности
    try:
        await message.delete()
    except:
        pass
    
    # Показываем индикатор обработки
    processing_msg = await message.answer("🔄 Проверяем код и авторизуемся...")
    
    try:
        # Получаем номер телефона из состояния
        data = await state.get_data()
        phone_number = data.get('phone_number')
        
        if not phone_number:
            await processing_msg.edit_text(
                "❌ <b>Ошибка</b>\n\n"
                "Номер телефона не найден. Начните авторизацию заново.",
                parse_mode="HTML"
            )
            await state.clear()
            return
        
        # Проверяем SMS код и получаем данные сессии (используем существующую сессию браузера)
        wb_auth = get_wb_auth_service(user_id=user_id)
        success, auth_data = await wb_auth.verify_sms_code(sms_code)
        
        # Закрываем браузер после завершения авторизации
        await wb_auth.close_session()
        
        if not success or not auth_data:
            await processing_msg.edit_text(
                "❌ <b>Неверный SMS код</b>\n\n"
                "Проверьте правильность кода и попробуйте еще раз.",
                parse_mode="HTML"
            )
            await state.clear()
            return
        
        # Добавляем номер телефона в данные авторизации
        auth_data['phone_number'] = phone_number
        
        # Сохраняем данные в базу
        async with AsyncSessionLocal() as session:
            user_repo = UserRepository(session)
            
            # Получаем или создаем пользователя
            user = await user_repo.get_or_create_user(
                telegram_id=message.from_user.id,
                username=message.from_user.username,
                first_name=message.from_user.first_name,
                last_name=message.from_user.last_name
            )
            
            # Сохраняем данные авторизации по телефону
            await user_repo.save_phone_auth(user, auth_data)
        
        logger.info(f"✅ User {message.from_user.id} successfully authenticated with INN: {auth_data['inn']}")
        
        # Формируем успешное сообщение
        success_text = f"""
✅ <b>Авторизация успешна!</b>

📱 Номер: {phone_number}
🏢 ИНН: {auth_data['inn']}
👤 Продавец: {auth_data['seller_name']}

<b>🎉 Теперь вы можете использовать автобронирование слотов!</b>

<b>🚀 Что дальше?</b>
• Включите автобронирование в кабинете
• Получайте уведомления о найденных слотах
        """
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📊 Личный кабинет", callback_data="cabinet_info")]
        ])
        
        await processing_msg.edit_text(success_text, reply_markup=keyboard, parse_mode="HTML")
        await state.clear()  # Очищаем состояние после успешной авторизации
        
        # После успешной авторизации сразу открываем экран автобронирования со списком заказов
        try:
            from app.bot.handlers.cabinet import callback_auto_booking
            class FakeCallback:
                def __init__(self, message, from_user):
                    self.message = message
                    self.from_user = from_user
                    self.data = "auto_booking"
            fake_cb = FakeCallback(processing_msg, message.from_user)
            # Создаем пустой state для совместимости
            class FakeState:
                def __init__(self):
                    pass
            fake_state = FakeState()
            await callback_auto_booking(fake_cb, fake_state)
        except Exception as e:
            logger.warning(f"Could not auto-open auto booking screen after auth: {e}")
        
        logger.info(f"Phone auth successful for user {message.from_user.id} with INN: {auth_data['inn']}")
            
    except WBWebAuthError as e:
        await cleanup_wb_auth_service()  # Закрываем браузер при ошибке
        await processing_msg.edit_text(
            f"❌ <b>Ошибка авторизации</b>\n\n{str(e)}\n\n"
            "Попробуйте еще раз или обратитесь в поддержку.",
            parse_mode="HTML"
        )
        logger.error(f"WB Web Auth error processing SMS for user {message.from_user.id}: {e}")
        await state.clear()
        
    except Exception as e:
        await cleanup_wb_auth_service()  # Закрываем браузер при ошибке
        await processing_msg.edit_text(
            "❌ <b>Произошла ошибка</b>\n\n"
            "Попробуйте позже или обратитесь в поддержку.",
            parse_mode="HTML"
        )
        logger.error(f"Error processing SMS code for user {message.from_user.id}: {e}")
        await state.clear()


@auth_router.callback_query(F.data == "cancel_phone_auth")
async def cancel_phone_auth(callback: CallbackQuery, state: FSMContext):
    """Отмена авторизации по телефону"""
    try:
        # Закрываем браузер
        await cleanup_wb_auth_service()
        
        # Очищаем состояние
        await state.clear()
        
        await callback.message.edit_text(
            "❌ <b>Авторизация отменена</b>\n\n"
            "Браузер закрыт. Вы можете начать авторизацию заново.",
            parse_mode="HTML"
        )
        
        logger.info(f"Phone auth cancelled by user {callback.from_user.id}")
        
    except Exception as e:
        logger.error(f"Error cancelling phone auth for user {callback.from_user.id}: {e}")
        await callback.answer("❌ Ошибка при отмене", show_alert=True)


@auth_router.callback_query(F.data == "phone_reauth")
async def phone_reauth(callback: CallbackQuery, state: FSMContext):
    """Переавторизация по номеру телефона"""
    user_id = callback.from_user.id
    
    try:
        # Очищаем старую сессию из БД
        async with AsyncSessionLocal() as session:
            user_repo = UserRepository(session)
            user = await user_repo.get_by_telegram_id(user_id)
            
            if user:
                await user_repo.clear_phone_auth(user)
                await session.commit()
                logger.info(f"Cleared old session for user {user_id}")
        
        # Закрываем браузер
        await cleanup_wb_auth_service()
        
        # Запускаем новую авторизацию
        await start_phone_auth(callback, state)
        
    except Exception as e:
        logger.error(f"Error during reauth for user {user_id}: {e}")
        await callback.message.edit_text(
            "❌ <b>Ошибка переавторизации</b>\n\n"
            "Попробуйте еще раз или обратитесь в поддержку.",
            parse_mode="HTML"
        )
