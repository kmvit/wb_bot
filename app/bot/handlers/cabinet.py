"""Обработчики для работы с кабинетом продавца"""

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.filters import Command
from loguru import logger

from app.services.wildberries_api import wb_api, WildberriesAPIError, WildberriesAuthError
from app.database.database import AsyncSessionLocal
from app.database.repositories.user_repo import UserRepository
from app.services.wb_web_auth import get_wb_auth_service, WBWebAuthError


# Создаем роутер для обработчиков кабинета
cabinet_router = Router()


@cabinet_router.message(Command("cabinet_info"))
@cabinet_router.callback_query(F.data == "cabinet_info")
async def cmd_cabinet_info(event: Message | CallbackQuery):
    """Показать информацию о кабинете"""
    user_id = event.from_user.id if isinstance(event, Message) else event.from_user.id
    
    # Показываем индикатор загрузки
    if isinstance(event, Message):
        processing_msg = await event.answer("🔄 Получаю информацию о кабинете...")
    else:
        processing_msg = event.message
        await processing_msg.edit_text("🔄 Получаю информацию о кабинете...")
    
    try:
        async with AsyncSessionLocal() as session:
            user_repo = UserRepository(session)
            
            # Получаем пользователя
            user = await user_repo.get_by_telegram_id(user_id)
            
            if not user or not user.has_wb_token():
                text = """
📊 <b>Информация о кабинете</b>

❌ API-токен не найден

Сначала добавьте API-токен командой /add_token
                """
                
                keyboard = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="➕ Добавить токен", callback_data="add_token")]
                ])
                
                await processing_msg.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
                return
            
            # Получаем токен
            wb_token = await user_repo.get_wb_token(user)
            
            if not wb_token:
                await processing_msg.edit_text(
                    "❌ <b>Ошибка расшифровки токена</b>\n\n"
                    "Попробуйте добавить токен заново.",
                    parse_mode="HTML"
                )
                return
            
            # Получаем информацию о кабинете через API
            async with wb_api:
                cabinet_info = await wb_api.get_cabinet_info(wb_token)
                seller_info = cabinet_info.get('seller_info', {})
                
                # Формируем информацию для отображения
                token_status = "✅ Активен" if cabinet_info.get('api_token_valid') else "❌ Неактивен"
                test_status = "✅ Пройден" if cabinet_info.get('token_test_passed') else "❌ Не пройден"
                
                # Информация о продавце из seller-info API согласно документации
                seller_name = seller_info.get('name', 'Не указано')
                seller_id = seller_info.get('sid', 'Не указано')
                trade_mark = seller_info.get('tradeMark', 'Не указано')
                
                # Информация о токене
                token_created = user.wb_token_created_at.strftime('%d.%m.%Y %H:%M') if user.wb_token_created_at else "Неизвестно"
                token_last_used = user.wb_token_last_used_at.strftime('%d.%m.%Y %H:%M') if user.wb_token_last_used_at else "Никогда"
                
                text = f"""
📊 <b>Информация о продавце</b>

👤 <b>Данные продавца:</b>
• Название: {seller_name}
• ID продавца: {seller_id}
• Торговая марка: {trade_mark}

🔑 <b>API-токен:</b>
• Статус: {token_status}
• Тест подключения: {test_status}
• Добавлен: {token_created}
• Последнее использование: {token_last_used}

<b>Доступные действия:</b>
• Мои мониторинги - просмотр активных мониторингов
• Автобронирование - автоматическое бронирование найденных слотов
• Аккаунты - просмотр всех пользователей бота
• Обновить список складов - обновить список складов для автобронирования
• Удалить токен - удалить токен для автобронирования
• Добавить новый токен - добавить новый токен для автобронирования
                """
                
                keyboard = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="📊 Мои мониторинги", callback_data="my_monitorings")],
                    [InlineKeyboardButton(text="🤖 Автобронирование", callback_data="auto_booking")],
                    [InlineKeyboardButton(text="👥 Аккаунты", callback_data="view_accounts")],
                    [InlineKeyboardButton(text="🏪 Обновить список складов", callback_data="update_warehouses")],
                    [InlineKeyboardButton(text="🗑 Удалить токен", callback_data="remove_token")],
                    [InlineKeyboardButton(text="➕ Добавить новый токен", callback_data="add_token")]
                ])
                
                await processing_msg.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    
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
                f"❌ <b>Ошибка API</b>\n\n{error_text}",
                parse_mode="HTML"
            )
    
    except Exception as e:
        logger.error(f"Error in cabinet_info for user {user_id}: {e}")
        await processing_msg.edit_text(
            "❌ <b>Произошла ошибка</b>\n\n"
            "Попробуйте позже или обратитесь в поддержку.",
            parse_mode="HTML"
        )


@cabinet_router.callback_query(F.data == "delete_account")
async def callback_delete_account(callback: CallbackQuery):
    """Показать список аккаунтов для удаления (только для админов)"""
    try:
        user_id = callback.from_user.id
        
        # Проверяем, является ли пользователь админом
        from app.config.settings import is_admin
        if not is_admin(user_id):
            await callback.answer("❌ У вас нет прав для удаления аккаунтов", show_alert=True)
            return
        
        # Показываем индикатор загрузки
        await callback.message.edit_text("🔄 Загружаю список аккаунтов для удаления...")
        
        async with AsyncSessionLocal() as session:
            user_repo = UserRepository(session)
            
            # Получаем всех пользователей
            all_users = await user_repo.get_all_users()
            
            if not all_users:
                text = """
🗑 <b>Удаление аккаунта</b>

❌ Аккаунты не найдены
                """
                
                keyboard = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="◀️ Назад к списку", callback_data="view_accounts")]
                ])
                
                await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
                return
            
            # Формируем список аккаунтов для удаления
            text = "🗑 <b>Выберите аккаунт для удаления</b>\n\n"
            text += "⚠️ <b>Внимание:</b> Удаление аккаунта приведет к полному удалению всех данных пользователя!\n\n"
            
            keyboard_buttons = []
            
            for i, user in enumerate(all_users[:10], 1):  # Ограничиваем до 10 аккаунтов
                # Форматируем имя пользователя
                username = f"@{user.username}" if user.username else "Без username"
                display_name = user.first_name or "Без имени"
                
                text += f"{i}. <b>{display_name}</b>\n"
                text += f"   ID: {user.telegram_id}\n"
                text += f"   Username: {username}\n\n"
                
                # Добавляем кнопку для удаления
                keyboard_buttons.append([
                    InlineKeyboardButton(
                        text=f"🗑 Удалить {display_name}",
                        callback_data=f"confirm_delete_account:{user.telegram_id}"
                    )
                ])
            
            # Добавляем кнопку "Назад"
            keyboard_buttons.append([
                InlineKeyboardButton(text="◀️ Назад к списку", callback_data="view_accounts")
            ])
            
            keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)
            
            await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
            
    except Exception as e:
        logger.error(f"Error showing delete account list for admin {user_id}: {e}")
        await callback.message.edit_text(
            "❌ <b>Ошибка загрузки списка аккаунтов</b>\n\n"
            "Попробуйте позже или обратитесь в поддержку.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="◀️ Назад к списку", callback_data="view_accounts")]
            ]),
            parse_mode="HTML"
        )


@cabinet_router.callback_query(F.data.startswith("confirm_delete_account:"))
async def callback_confirm_delete_account(callback: CallbackQuery):
    """Подтверждение удаления аккаунта"""
    try:
        user_id = callback.from_user.id
        
        # Проверяем, является ли пользователь админом
        from app.config.settings import is_admin
        if not is_admin(user_id):
            await callback.answer("❌ У вас нет прав для удаления аккаунтов", show_alert=True)
            return
        
        # Извлекаем ID пользователя для удаления
        target_user_id = int(callback.data.split(":")[1])
        
        async with AsyncSessionLocal() as session:
            user_repo = UserRepository(session)
            
            # Получаем информацию о пользователе
            target_user = await user_repo.get_by_telegram_id(target_user_id)
            
            if not target_user:
                await callback.answer("❌ Пользователь не найден", show_alert=True)
                return
            
            # Форматируем имя пользователя
            username = f"@{target_user.username}" if target_user.username else "Без username"
            display_name = target_user.first_name or "Без имени"
            
            text = f"""
🗑 <b>Подтверждение удаления аккаунта</b>

👤 <b>Пользователь:</b> {display_name}
🆔 <b>ID:</b> {target_user.telegram_id}
📱 <b>Username:</b> {username}

⚠️ <b>ВНИМАНИЕ!</b>
Удаление аккаунта приведет к:
• Полному удалению всех данных пользователя
• Удалению всех мониторингов
• Удалению API токенов
• Удалению истории авторизации

❌ <b>Это действие необратимо!</b>
            """
            
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="✅ Да, удалить",
                        callback_data=f"execute_delete_account:{target_user_id}"
                    ),
                    InlineKeyboardButton(
                        text="❌ Отмена",
                        callback_data="delete_account"
                    )
                ],
                [InlineKeyboardButton(text="◀️ Назад к списку", callback_data="view_accounts")]
            ])
            
            await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
            
    except Exception as e:
        logger.error(f"Error confirming account deletion for admin {user_id}: {e}")
        await callback.answer("❌ Ошибка при подтверждении удаления", show_alert=True)


@cabinet_router.callback_query(F.data.startswith("execute_delete_account:"))
async def callback_execute_delete_account(callback: CallbackQuery):
    """Выполнение удаления аккаунта"""
    try:
        user_id = callback.from_user.id
        
        # Проверяем, является ли пользователь админом
        from app.config.settings import is_admin
        if not is_admin(user_id):
            await callback.answer("❌ У вас нет прав для удаления аккаунтов", show_alert=True)
            return
        
        # Извлекаем ID пользователя для удаления
        target_user_id = int(callback.data.split(":")[1])
        
        # Показываем индикатор загрузки
        await callback.message.edit_text("🔄 Удаляю аккаунт...")
        
        async with AsyncSessionLocal() as session:
            user_repo = UserRepository(session)
            
            # Получаем информацию о пользователе
            target_user = await user_repo.get_by_telegram_id(target_user_id)
            
            if not target_user:
                await callback.answer("❌ Пользователь не найден", show_alert=True)
                return
            
            # Форматируем имя пользователя
            username = f"@{target_user.username}" if target_user.username else "Без username"
            display_name = target_user.first_name or "Без имени"
            
            try:
                # Удаляем пользователя (каскадное удаление удалит все связанные данные)
                success = await user_repo.delete_user(target_user_id)
                
                if success:
                    text = f"""
✅ <b>Аккаунт успешно удален</b>

👤 <b>Удаленный пользователь:</b> {display_name}
🆔 <b>ID:</b> {target_user_id}
📱 <b>Username:</b> {username}

🗑 <b>Удалены:</b>
• Все данные пользователя
• Все мониторинги
• API токены
• История авторизации
                    """
                    
                    keyboard = InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text="👥 К списку аккаунтов", callback_data="view_accounts")],
                        [InlineKeyboardButton(text="◀️ В кабинет", callback_data="cabinet_info")]
                    ])
                    
                    await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
                    
                    # Логируем удаление
                    logger.info(f"Admin {user_id} deleted account {target_user_id} ({display_name})")
                    
                else:
                    await callback.answer("❌ Ошибка при удалении аккаунта", show_alert=True)
                    
            except Exception as e:
                logger.error(f"Error deleting account {target_user_id} by admin {user_id}: {e}")
                await callback.answer("❌ Ошибка при удалении аккаунта", show_alert=True)
            
    except Exception as e:
        logger.error(f"Error executing account deletion for admin {user_id}: {e}")
        await callback.answer("❌ Ошибка при удалении аккаунта", show_alert=True)


@cabinet_router.callback_query(F.data == "remove_token")
async def callback_remove_token(callback: CallbackQuery):
    """Удалить API-токен пользователя"""
    user_id = callback.from_user.id
    
    try:
        async with AsyncSessionLocal() as session:
            user_repo = UserRepository(session)
            
            # Получаем пользователя
            user = await user_repo.get_by_telegram_id(user_id)
            
            if not user or not user.has_wb_token():
                text = """
🗑 <b>Удаление токена</b>

❌ API-токен не найден

У вас нет сохраненного токена для удаления.
                """
                
                keyboard = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="➕ Добавить токен", callback_data="add_token")],
                    [InlineKeyboardButton(text="◀️ Назад", callback_data="cabinet_info")]
                ])
                
                await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
                return
            
            # Показываем подтверждение удаления
            text = """
🗑 <b>Удаление API-токена</b>

⚠️ <b>Вы уверены, что хотите удалить токен?</b>

После удаления токена:
• Вы потеряете доступ к функциям кабинета
• Все настройки автоброни будут отключены
• Мониторинг слотов будет остановлен

<b>Это действие нельзя отменить!</b>
            """
            
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="✅ Да, удалить", callback_data="confirm_remove_token")],
                [InlineKeyboardButton(text="❌ Отмена", callback_data="cabinet_info")]
            ])
            
            await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    
    except Exception as e:
        logger.error(f"Error in remove_token for user {user_id}: {e}")
        await callback.message.edit_text(
            "❌ <b>Произошла ошибка</b>\n\n"
            "Попробуйте позже или обратитесь в поддержку.",
            parse_mode="HTML"
        )


@cabinet_router.callback_query(F.data == "confirm_remove_token")
async def callback_confirm_remove_token(callback: CallbackQuery):
    """Подтвердить удаление API-токена"""
    user_id = callback.from_user.id
    
    # Показываем индикатор обработки
    await callback.message.edit_text("🔄 Удаляю токен...")
    
    try:
        async with AsyncSessionLocal() as session:
            user_repo = UserRepository(session)
            
            # Получаем пользователя
            user = await user_repo.get_by_telegram_id(user_id)
            
            if not user or not user.has_wb_token():
                await callback.message.edit_text(
                    "❌ <b>Токен не найден</b>\n\n"
                    "Возможно, токен уже был удален.",
                    parse_mode="HTML"
                )
                return
            
            # Удаляем токен
            await user_repo.remove_wb_token(user)
            
            # Показываем успешное сообщение
            text = """
✅ <b>Токен успешно удален</b>

🔒 API-токен был безопасно удален из системы.

<b>Что дальше?</b>
• Добавьте новый токен для продолжения работы
• Или воспользуйтесь другими функциями бота
            """
            
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="➕ Добавить новый токен", callback_data="add_token")],
                [InlineKeyboardButton(text="🏠 Главное меню", callback_data="start")]
            ])
            
            await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
            
            logger.info(f"Token removed for user: {user_id}")
    
    except Exception as e:
        logger.error(f"Error confirming token removal for user {user_id}: {e}")
        await callback.message.edit_text(
            "❌ <b>Ошибка при удалении токена</b>\n\n"
            "Попробуйте позже или обратитесь в поддержку.",
            parse_mode="HTML"
        )


@cabinet_router.callback_query(F.data == "update_warehouses")
async def update_warehouses(callback: CallbackQuery):
    """Обновить список складов из API WB"""
    user_id = callback.from_user.id
    
    # Показываем индикатор загрузки
    await callback.message.edit_text("🔄 Обновляю список складов...")
    
    try:
        async with AsyncSessionLocal() as session:
            user_repo = UserRepository(session)
            user = await user_repo.get_by_telegram_id(user_id)
            
            if not user or not user.has_wb_token():
                await callback.message.edit_text(
                    "❌ <b>API-токен не найден</b>\n\n"
                    "Добавьте API-токен для обновления списка складов.",
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text="➕ Добавить токен", callback_data="add_token")],
                        [InlineKeyboardButton(text="📊 Кабинет", callback_data="cabinet_info")]
                    ]),
                    parse_mode="HTML"
                )
                return
            
            wb_token = await user_repo.get_wb_token(user)
            if not wb_token:
                await callback.message.edit_text(
                    "❌ <b>Ошибка получения токена</b>\n\n"
                    "Не удалось расшифровать API-токен.",
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text="📊 Кабинет", callback_data="cabinet_info")]
                    ]),
                    parse_mode="HTML"
                )
                return
            
            # Получаем склады из API
            async with wb_api:
                api_warehouses = await wb_api.get_warehouses(wb_token)
            
            if not api_warehouses:
                await callback.message.edit_text(
                    "❌ <b>Не удалось получить склады</b>\n\n"
                    "API WB не вернул список складов. Возможные причины:\n"
                    "• Токен не имеет прав на поставки\n"
                    "• Временная недоступность API\n"
                    "• Превышен лимит запросов",
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text="🔄 Попробовать снова", callback_data="update_warehouses")],
                        [InlineKeyboardButton(text="📊 Кабинет", callback_data="cabinet_info")]
                    ]),
                    parse_mode="HTML"
                )
                return
            
            # Импортируем репозиторий складов
            from app.database.repositories.warehouse_repo import WarehouseRepository
            
            warehouse_repo = WarehouseRepository(session)
            stats = await warehouse_repo.sync_warehouses_from_api(api_warehouses)
            
            await callback.message.edit_text(
                f"✅ <b>Список складов обновлен</b>\n\n"
                f"📊 <b>Статистика обновления:</b>\n"
                f"• Всего складов: {stats['total']}\n"
                f"• Создано новых: {stats['created']}\n"
                f"• Обновлено: {stats['updated']}\n\n"
                f"Теперь при создании мониторинга будут использоваться актуальные данные складов.",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="📊 Кабинет", callback_data="cabinet_info")]
                ]),
                parse_mode="HTML"
            )
            
            logger.info(f"Warehouses updated for user {user_id}: {stats}")
    
    except WildberriesAPIError as e:
        error_text = str(e)
        if "401" in error_text or "Unauthorized" in error_text:
            error_msg = "❌ <b>Ошибка авторизации</b>\n\nAPI-токен недействителен или истек срок действия."
        elif "403" in error_text or "Forbidden" in error_text:
            error_msg = "❌ <b>Недостаточно прав</b>\n\nAPI-токен не имеет прав на получение списка складов."
        else:
            error_msg = f"❌ <b>Ошибка API Wildberries</b>\n\n{error_text}"
        
        await callback.message.edit_text(
            error_msg,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔄 Попробовать снова", callback_data="update_warehouses")],
                [InlineKeyboardButton(text="📊 Кабинет", callback_data="cabinet_info")]
            ]),
            parse_mode="HTML"
        )
        
    except Exception as e:
        logger.error(f"Error updating warehouses for user {user_id}: {e}")
        await callback.message.edit_text(
            "❌ <b>Ошибка обновления складов</b>\n\n"
            "Произошла неожиданная ошибка. Попробуйте позже.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔄 Попробовать снова", callback_data="update_warehouses")],
                [InlineKeyboardButton(text="📊 Кабинет", callback_data="cabinet_info")]
            ]),
            parse_mode="HTML"
        )


@cabinet_router.callback_query(F.data == "auto_booking")
async def callback_auto_booking(callback: CallbackQuery, state: FSMContext):
    """Экран автобронирования: показывает список заказов со статусом 'не запланировано'"""
    user_id = callback.from_user.id
    
    # Показываем индикатор загрузки
    await callback.message.edit_text("🔄 Загружаю список заказов...")
    
    try:
        async with AsyncSessionLocal() as session:
            user_repo = UserRepository(session)
            user = await user_repo.get_by_telegram_id(user_id)
            
            if not user or not user.has_phone_auth():
                # Если нет авторизации по телефону, предлагаем авторизоваться
                text = (
                    "🤖 <b>Автобронирование слотов</b>\n\n"
                    "Для просмотра заказов нужно авторизоваться по номеру телефона."
                )
                
                keyboard = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="📱 Авторизация по телефону", callback_data="phone_auth")],
                    [InlineKeyboardButton(text="◀️ Назад в кабинет", callback_data="cabinet_info")]
                ])
                
                await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
                return
            
            # Получаем данные сессии
            session_data = await user_repo.get_phone_auth_session(user)
            if not session_data:
                # Если нет данных сессии, предлагаем переавторизоваться
                text = (
                    "❌ <b>Сессия не найдена</b>\n\n"
                    "Нужно пройти авторизацию заново."
                )
                
                keyboard = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="🔄 Переавторизоваться", callback_data="phone_reauth")],
                    [InlineKeyboardButton(text="◀️ Назад в кабинет", callback_data="cabinet_info")]
                ])
                
                await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
                return
            
            # Получаем список заказов со статусом "не запланировано"
            wb_auth = get_wb_auth_service(user_id=user_id)
            try:
                order_numbers = await wb_auth.get_unplanned_order_numbers(session_data)
                
                if not order_numbers:
                    # Если заказов нет
                    text = (
                        "📋 <b>Список заказов</b>\n\n"
                        "✅ Заказов со статусом 'не запланировано' не найдено.\n\n"
                        "Все заказы уже запланированы или отсутствуют."
                    )
                    
                    keyboard = InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text="🔄 Обновить список", callback_data="auto_booking")],
                        [InlineKeyboardButton(text="◀️ Назад в кабинет", callback_data="cabinet_info")]
                    ])
                    
                else:
                    # Формируем список заказов с кнопками для мониторинга
                    orders_text = "\n".join([f"• {order}" for order in order_numbers[:5]])  # Показываем первые 5
                    if len(order_numbers) > 5:
                        orders_text += f"\n• ... и еще {len(order_numbers) - 5} заказов"
                    
                    text = (
                        f"📋 <b>Выберите заказ для мониторинга слотов</b>\n\n"
                        f"📊 <b>Всего найдено:</b> {len(order_numbers)}\n\n"
                        f"<b>Номера заказов:</b>\n{orders_text}\n\n"
                        f"💡 <i>Выберите заказ, для которого хотите настроить мониторинг слотов</i>"
                    )
                    
                    # Создаем кнопки для каждого заказа (максимум 5)
                    keyboard_buttons = []
                    for i, order in enumerate(order_numbers[:5]):
                        keyboard_buttons.append([
                            InlineKeyboardButton(
                                text=f"🎯 Заказ {order}", 
                                callback_data=f"monitor_order:{order}"
                            )
                        ])
                    
                    # Добавляем служебные кнопки
                    keyboard_buttons.extend([
                        [InlineKeyboardButton(text="🔄 Обновить список", callback_data="auto_booking")],
                        [InlineKeyboardButton(text="◀️ Назад в кабинет", callback_data="cabinet_info")]
                    ])
                    
                    keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)
                
                await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
                
            except WBWebAuthError as e:
                if str(e) == 'AUTH_REQUIRED':
                    # Сессия истекла
                    text = (
                        "❌ <b>Сессия истекла</b>\n\n"
                        "Нужно пройти авторизацию заново."
                    )
                    
                    keyboard = InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text="🔄 Переавторизоваться", callback_data="phone_reauth")],
                        [InlineKeyboardButton(text="◀️ Назад в кабинет", callback_data="cabinet_info")]
                    ])
                    
                    await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
                else:
                    raise
                    
    except Exception as e:
        logger.error(f"Error in auto_booking for user {user_id}: {e}")
        text = (
            "❌ <b>Ошибка загрузки заказов</b>\n\n"
            "Не удалось получить список заказов. Попробуйте позже."
        )
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Попробовать снова", callback_data="auto_booking")],
            [InlineKeyboardButton(text="◀️ Назад в кабинет", callback_data="cabinet_info")]
        ])
        
        await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")


@cabinet_router.callback_query(F.data == "phone_reauth")
async def callback_phone_reauth(callback: CallbackQuery, state: FSMContext):
    """Обработчик кнопки переавторизации"""
    user_id = callback.from_user.id
    
    # Удаляем текущую авторизацию
    async with AsyncSessionLocal() as session:
        user_repo = UserRepository(session)
        user = await user_repo.get_by_telegram_id(user_id)
        
        if user and user.has_phone_auth():
            await user_repo.remove_phone_auth(user)
            logger.info(f"Removed phone auth for user {user_id} for reauth")
    
    # Перенаправляем на стандартный процесс авторизации
    callback.data = "phone_auth"
    from app.bot.handlers.auth import start_phone_auth
    await start_phone_auth(callback, state)


@cabinet_router.callback_query(F.data.startswith("monitor_order:"))
async def callback_monitor_order(callback: CallbackQuery, state: FSMContext):
    """Обработчик выбора заказа для мониторинга"""
    user_id = callback.from_user.id
    order_number = callback.data.split(":", 1)[1]
    
    # Сохраняем номер заказа в состоянии
    await state.update_data(selected_order_number=order_number)
    
    # Перенаправляем на настройку мониторинга с предвыбранным заказом
    from app.bot.handlers.monitoring import start_monitoring_setup
    await start_monitoring_setup(callback, state)


@cabinet_router.callback_query(F.data == "view_accounts")
async def callback_view_accounts(callback: CallbackQuery):
    """Показать список всех аккаунтов, использующих бота"""
    try:
        user_id = callback.from_user.id
        
        # Проверяем, является ли пользователь админом
        from app.config.settings import is_admin
        is_user_admin = is_admin(user_id)
        
        # Показываем индикатор загрузки
        await callback.message.edit_text("🔄 Загружаю список аккаунтов...")
        
        async with AsyncSessionLocal() as session:
            user_repo = UserRepository(session)
            
            # Получаем всех пользователей
            all_users = await user_repo.get_all_users()
            
            if not all_users:
                text = """
👥 <b>Список аккаунтов</b>

❌ Аккаунты не найдены
                """
                
                keyboard = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="◀️ Назад в кабинет", callback_data="cabinet_info")]
                ])
                
                await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
                return
            
            # Формируем список аккаунтов
            accounts_text = f"👥 <b>Список аккаунтов ({len(all_users)})</b>\n\n"
            
            for i, user in enumerate(all_users, 1):
                # Определяем статус токена
                token_status = "✅" if user.has_wb_token() else "❌"
                
                # Форматируем имя пользователя
                username = f"@{user.username}" if user.username else "Без username"
                display_name = user.first_name or "Без имени"
                
                # Определяем статус активности
                last_activity = "Неизвестно"
                if user.updated_at:
                    from datetime import datetime, timezone
                    now = datetime.now(timezone.utc)
                    diff = now - user.updated_at.replace(tzinfo=timezone.utc)
                    if diff.days > 0:
                        last_activity = f"{diff.days} дн. назад"
                    elif diff.seconds > 3600:
                        last_activity = f"{diff.seconds // 3600} ч. назад"
                    else:
                        last_activity = "Недавно"
                
                accounts_text += f"{i}. {token_status} <b>{display_name}</b>\n"
                accounts_text += f"   ID: {user.telegram_id}\n"
                accounts_text += f"   Username: {username}\n"
                accounts_text += f"   Активность: {last_activity}\n"
                
                # Добавляем информацию о мониторингах
                from app.database.repositories.slot_monitoring_repo import SlotMonitoringRepository
                slot_repo = SlotMonitoringRepository(session)
                active_monitorings = await slot_repo.get_active_monitorings(user)
                if active_monitorings:
                    accounts_text += f"   📊 Мониторингов: {len(active_monitorings)}\n"
                
                accounts_text += "\n"
                
                # Ограничиваем длину сообщения
                if len(accounts_text) > 3000:
                    accounts_text += f"... и еще {len(all_users) - i} аккаунтов"
                    break
            
            # Создаем клавиатуру
            keyboard_buttons = [
                [InlineKeyboardButton(text="🔄 Обновить", callback_data="view_accounts")],
                [InlineKeyboardButton(text="◀️ Назад в кабинет", callback_data="cabinet_info")]
            ]
            
            # Добавляем кнопку удаления аккаунта только для админов
            if is_user_admin:
                keyboard_buttons.insert(1, [InlineKeyboardButton(text="🗑 Удалить аккаунт", callback_data="delete_account")])
            
            keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)
            
            await callback.message.edit_text(accounts_text, reply_markup=keyboard, parse_mode="HTML")
            
    except Exception as e:
        logger.error(f"Error viewing accounts for user {user_id}: {e}")
        await callback.message.edit_text(
            "❌ <b>Ошибка загрузки аккаунтов</b>\n\n"
            "Попробуйте позже или обратитесь в поддержку.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="◀️ Назад в кабинет", callback_data="cabinet_info")]
            ]),
            parse_mode="HTML"
        )
