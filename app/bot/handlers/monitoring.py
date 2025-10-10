"""Обработчики мониторинга слотов"""

from datetime import datetime, timedelta
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from loguru import logger

from app.services.wildberries_api import wb_api, WildberriesAPIError, WildberriesAuthError
from app.services.booking_service import get_booking_service, BookingServiceError
from app.database.database import AsyncSessionLocal
from app.database.repositories.user_repo import UserRepository
from app.database.repositories.slot_monitoring_repo import SlotMonitoringRepository
from app.database.models import MonitoringStatus
from .keyboards import (
    create_coefficient_keyboard, create_box_type_keyboard, create_logistics_shoulder_keyboard,
    create_date_range_keyboard, create_calendar, create_warehouse_keyboard,
    create_edit_monitoring_keyboard, create_edit_coefficient_keyboard,
    create_edit_box_type_keyboard, create_edit_logistics_shoulder_keyboard,
    create_edit_date_range_keyboard, create_edit_quick_period_keyboard,
    create_edit_confirm_keyboard, create_my_monitorings_keyboard,
    create_no_monitorings_keyboard, create_monitoring_success_keyboard,
    create_slot_notification_keyboard, create_warehouse_error_keyboard,
    create_delete_confirmation_keyboard
)

# Создаем роутер для обработчиков мониторинга
monitoring_router = Router()


async def handle_calendar_navigation(callback: CallbackQuery, state: FSMContext):
    """Универсальная функция для навигации по календарю"""
    if callback.data.startswith("cal_prev_") or callback.data.startswith("cal_next_"):
        parts = callback.data.split("_")
        year, month = int(parts[2]), int(parts[3])

        if callback.data.startswith("cal_prev_"):
            month -= 1
            if month == 0:
                month = 12
                year -= 1
        else:
            month += 1
            if month == 13:
                month = 1
                year += 1

        calendar_keyboard = create_calendar(year, month)
        await callback.message.edit_reply_markup(reply_markup=calendar_keyboard)


async def handle_date_selection(callback: CallbackQuery, state: FSMContext, date_type: str):
    """Универсальная функция для выбора даты"""
    if callback.data.startswith("cal_select_"):
        try:
            # Извлекаем дату
            parts = callback.data.split("_")
            year = int(parts[2])
            month = int(parts[3])
            day = int(parts[4])

            selected_date = datetime(year, month, day).date()
            today = datetime.now().date()

            if selected_date < today:
                await callback.answer("❌ Нельзя выбрать прошедшую дату", show_alert=True)
                return

            if date_type == "from":
                # Сохраняем начальную дату
                await state.update_data(date_from=selected_date)

                # Показываем календарь для конечной даты
                calendar_keyboard = create_calendar(year, month)

                await callback.message.edit_text(
                    f"📅 <b>Выберите конечную дату мониторинга</b>\n\n"
                    f"Начальная дата: {selected_date.strftime('%d.%m.%Y')}\n"
                    f"Выберите дату окончания мониторинга.",
                    reply_markup=calendar_keyboard,
                    parse_mode="HTML"
                )
                await state.set_state(MonitoringStates.selecting_date_to)

            elif date_type == "to":
                # Получаем начальную дату
                data = await state.get_data()
                date_from = data.get("date_from")

                if not date_from:
                    await callback.answer("❌ Ошибка: начальная дата не найдена", show_alert=True)
                    return

                if selected_date <= date_from:
                    await callback.answer("❌ Конечная дата должна быть позже начальной", show_alert=True)
                    return

                # Сохраняем конечную дату
                await state.update_data(date_to=selected_date)

                # Проверяем, находимся ли мы в режиме редактирования
                if data.get("editing_monitoring_id"):
                    # Режим редактирования - возвращаемся в меню редактирования
                    monitoring_id = data.get("editing_monitoring_id")
                    period_text = f"{date_from.strftime('%d.%m.%Y')} - {selected_date.strftime('%d.%m.%Y')}"

                    await callback.message.edit_text(
                        f"✅ <b>Период обновлен</b>\n\n"
                        f"• Новый период: {period_text}\n"
                        f"• Длительность: {(selected_date - date_from).days} дней\n\n"
                        f"<b>Мониторинг #{monitoring_id}</b>\n"
                        f"Что еще хотите изменить?",
                        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                            [InlineKeyboardButton(
                                text="📊 Коэффициенты", callback_data="edit_coefficient")],
                            [InlineKeyboardButton(
                                text="🚚 Логистическое плечо", callback_data="edit_logistics_shoulder")],
                            [InlineKeyboardButton(
                                text="✅ Сохранить изменения", callback_data="confirm_edit")],
                            [InlineKeyboardButton(
                                text="❌ Отмена", callback_data="my_monitorings")]
                        ]),
                        parse_mode="HTML"
                    )
                    await state.set_state(MonitoringStates.editing_monitoring)
                else:
                    # Режим создания - применяем логистическое плечо к датам
                    logistics_shoulder = data.get('logistics_shoulder', 0)
                    date_from_with_shoulder = date_from + timedelta(days=logistics_shoulder)
                    date_to_with_shoulder = selected_date  # Конечная дата остается без изменений
                    
                    # Сохраняем даты с учетом логистического плеча
                    await state.update_data(
                        date_from=date_from_with_shoulder,
                        date_to=date_to_with_shoulder
                    )
                    
                    period_text = f"{date_from.strftime('%d.%m.%Y')} - {selected_date.strftime('%d.%m.%Y')}"
                    await state.update_data(period_text=period_text)

                    text = f"""
🎯 <b>Настройка мониторинга слотов</b>

✅ Коэффициенты: {data.get('coefficient_text')}
✅ Период: {period_text}

<b>Шаг 3/4: Выберите склады для мониторинга</b>

Сейчас загрузим список доступных складов...
                    """

                    await callback.message.edit_text(text, parse_mode="HTML")

                    # Создаем фиктивный callback для использования существующей функции
                    class FakeCallback:
                        def __init__(self, callback_obj):
                            self.from_user = callback_obj.from_user
                            self.message = callback_obj.message

                    fake_callback = FakeCallback(callback)
                    await load_warehouses_for_selection(fake_callback, state)

        except Exception as e:
            logger.error(f"Error handling date selection: {e}")
            await callback.answer("❌ Ошибка выбора даты", show_alert=True)


class MonitoringStates(StatesGroup):
    """Состояния для процесса настройки мониторинга"""
    selecting_coefficient = State()
    selecting_box_type = State()
    selecting_logistics_shoulder = State()
    selecting_date_range = State()
    selecting_date_from = State()
    selecting_date_to = State()
    selecting_warehouses = State()
    selecting_acceptance_options = State()
    confirming_monitoring = State()

    # Состояния для редактирования мониторинга
    editing_monitoring = State()
    editing_coefficient = State()
    editing_logistics_shoulder = State()
    editing_date_range = State()
    editing_date_from = State()
    editing_date_to = State()
    confirming_edit = State()


@monitoring_router.callback_query(F.data == "start_monitoring")
async def start_monitoring_setup(callback: CallbackQuery, state: FSMContext):
    """Начать настройку мониторинга слотов"""
    user_id = callback.from_user.id

    try:
        async with AsyncSessionLocal() as session:
            user_repo = UserRepository(session)
            user = await user_repo.get_by_telegram_id(user_id)

            if not user or not user.has_wb_token():
                await callback.message.edit_text(
                    "❌ <b>API-токен не найден</b>\n\n"
                    "Сначала добавьте API-токен командой /add_token",
                    parse_mode="HTML"
                )
                return

            # Проверяем активные мониторинги
            slot_repo = SlotMonitoringRepository(session)
            active_monitorings = await slot_repo.get_active_monitorings(user)

            if len(active_monitorings) >= 3:  # Лимит активных мониторингов
                await callback.message.edit_text(
                    "⚠️ <b>Достигнут лимит активных мониторингов</b>\n\n"
                    f"У вас уже запущено {len(active_monitorings)} мониторингов.\n"
                    "Остановите один из них, чтобы создать новый.",
                    parse_mode="HTML"
                )
                return

            # Получаем данные состояния (включая выбранный заказ)
            state_data = await state.get_data()
            selected_order = state_data.get('selected_order_number')
            
            # Формируем текст с информацией о заказе
            if selected_order:
                order_info = f"📦 <b>Выбранный заказ:</b> {selected_order}\n\n"
                step_info = "<b>Шаг 1/4: Выберите максимальный коэффициент приемки</b>"
            else:
                order_info = ""
                step_info = "<b>Шаг 1/4: Выберите максимальный коэффициент приемки</b>"
            
            text = f"""
🎯 <b>Настройка мониторинга слотов</b>

{order_info}{step_info}

Коэффициент определяет стоимость приемки на складе WB:
• <b>0</b> - Бесплатная приемка 🟢
• <b>1-5</b> - Низкие коэффициенты 🟡
• <b>6-10</b> - Средние коэффициенты 🟠
• <b>11-20</b> - Высокие коэффициенты 🔴

<b>Выберите максимальный коэффициент (от 0 до выбранного):</b>
            """

            keyboard = create_coefficient_keyboard()

            await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
            await state.set_state(MonitoringStates.selecting_coefficient)

    except Exception as e:
        logger.error(
            f"Error starting monitoring setup for user {user_id}: {e}")
        await callback.message.edit_text(
            "❌ <b>Произошла ошибка</b>\n\n"
            "Попробуйте позже или обратитесь в поддержку.",
            parse_mode="HTML"
        )


@monitoring_router.callback_query(StateFilter(MonitoringStates.selecting_coefficient))
async def select_coefficient(callback: CallbackQuery, state: FSMContext):
    """Обработка выбора коэффициента"""
    coefficient_data = {
        "coeff_0": (0, 0, "🟢 Только бесплатные (0)"),
        "coeff_1": (0, 1, "🟡 До x1 (0-1)"),
        "coeff_2": (0, 2, "🟡 До x2 (0-2)"),
        "coeff_3": (0, 3, "🟡 До x3 (0-3)"),
        "coeff_4": (0, 4, "🟡 До x4 (0-4)"),
        "coeff_5": (0, 5, "🟡 До x5 (0-5)"),
        "coeff_6": (0, 6, "🟠 До x6 (0-6)"),
        "coeff_7": (0, 7, "🟠 До x7 (0-7)"),
        "coeff_8": (0, 8, "🟠 До x8 (0-8)"),
        "coeff_9": (0, 9, "🟠 До x9 (0-9)"),
        "coeff_10": (0, 10, "🟠 До x10 (0-10)"),
        "coeff_20": (0, 20, "🔴 До x20 (0-20)"),
        "coeff_any": (0, 100, "🌟 Любой коэффициент")
    }

    if callback.data in coefficient_data:
        coeff_min, coeff_max, coeff_text = coefficient_data[callback.data]

        # Сохраняем выбранные коэффициенты
        await state.update_data(
            coefficient_min=coeff_min,
            coefficient_max=coeff_max,
            coefficient_text=coeff_text
        )

        # Переходим к выбору типа упаковки
        text = f"""
🎯 <b>Настройка мониторинга слотов</b>

✅ Коэффициенты: {coeff_text}

<b>Шаг 2/5: Выберите тип упаковки</b>

Тип упаковки определяет способ поставки товара на склад WB:

<b>Выберите тип упаковки:</b>
        """

        keyboard = create_box_type_keyboard()

        await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
        await state.set_state(MonitoringStates.selecting_box_type)


@monitoring_router.callback_query(StateFilter(MonitoringStates.selecting_box_type))
async def select_box_type(callback: CallbackQuery, state: FSMContext):
    """Обработка выбора типа упаковки"""
    if callback.data == "back_to_coefficient":
        # Возвращаемся к выбору коэффициента
        await start_monitoring_setup(callback, state)
        return

    box_type_data = {
        "box_type_2": (2, "📦 Короба"),
        "box_type_5": (5, "🛒 Монопаллеты"),
        "box_type_6": (6, "🚛 Суперсейф"),
        "box_type_any": (None, "🌟 Любой тип")
    }

    if callback.data in box_type_data:
        box_type_id, box_type_text = box_type_data[callback.data]

        # Сохраняем выбранный тип упаковки
        await state.update_data(
            box_type_id=box_type_id,
            box_type_text=box_type_text
        )

        # Получаем данные для отображения
        data = await state.get_data()

        # Переходим к выбору логистического плеча
        text = f"""
🎯 <b>Настройка мониторинга слотов</b>

✅ Коэффициенты: {data.get('coefficient_text')}
✅ Тип упаковки: {box_type_text}

<b>Шаг 3/5: Выберите логистическое плечо</b>

Логистическое плечо - это дни на доставку товара на склад WB.
Если вы создаете мониторинг 10.09.2025 с плечом 2 дня, 
то система будет искать слоты с 12.09.2025.

<b>Выберите количество дней на доставку:</b>
        """

        keyboard = create_logistics_shoulder_keyboard()

        await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
        await state.set_state(MonitoringStates.selecting_logistics_shoulder)


@monitoring_router.callback_query(StateFilter(MonitoringStates.selecting_logistics_shoulder))
async def select_logistics_shoulder(callback: CallbackQuery, state: FSMContext):
    """Обработка выбора логистического плеча"""
    if callback.data == "back_to_box_type":
        # Возвращаемся к выбору типа упаковки
        data = await state.get_data()
        text = f"""
🎯 <b>Настройка мониторинга слотов</b>

✅ Коэффициенты: {data.get('coefficient_text')}

<b>Шаг 2/5: Выберите тип упаковки</b>

Тип упаковки определяет способ поставки товара на склад WB:

<b>Выберите тип упаковки:</b>
        """

        keyboard = create_box_type_keyboard()

        await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
        await state.set_state(MonitoringStates.selecting_box_type)
        return

    if callback.data.startswith("logistics_"):
        logistics_days = int(callback.data.split("_")[1])

        # Сохраняем выбранное логистическое плечо
        await state.update_data(logistics_shoulder=logistics_days)

        # Получаем данные для отображения
        data = await state.get_data()

        # Переходим к выбору периода мониторинга
        logistics_text = f"{logistics_days} дней" if logistics_days > 0 else "готов к отправке"

        text = f"""
🎯 <b>Настройка мониторинга слотов</b>

✅ Коэффициенты: {data.get('coefficient_text')}
✅ Логистическое плечо: {logistics_text}

<b>Шаг 3/5: Выберите период мониторинга</b>

Выберите, на какой период искать доступные слоты:
        """

        keyboard = create_date_range_keyboard()

        await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
        await state.set_state(MonitoringStates.selecting_date_range)


@monitoring_router.callback_query(MonitoringStates.selecting_date_range)
async def select_date_range(callback: CallbackQuery, state: FSMContext):
    """Обработка выбора диапазона дат"""
    if callback.data == "back_to_logistics":
        # Возвращаемся к выбору логистического плеча
        data = await state.get_data()
        text = f"""
🎯 <b>Настройка мониторинга слотов</b>

✅ Коэффициенты: {data.get('coefficient_text')}

<b>Шаг 2/5: Выберите логистическое плечо</b>

Логистическое плечо - это дни на доставку товара на склад WB.
Если вы создаете мониторинг 10.09.2025 с плечом 2 дня, 
то система будет искать слоты с 12.09.2025.

<b>Выберите количество дней на доставку:</b>
        """

        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text="🚚 0 дней (готов к отправке)", callback_data="logistics_0")],
            [InlineKeyboardButton(
                text="🚚 1 день", callback_data="logistics_1")],
            [InlineKeyboardButton(
                text="🚚 2 дня", callback_data="logistics_2")],
            [InlineKeyboardButton(
                text="🚚 3 дня", callback_data="logistics_3")],
            [InlineKeyboardButton(
                text="🚚 4 дня", callback_data="logistics_4")],
            [InlineKeyboardButton(
                text="⬅️ Назад", callback_data="back_to_coefficient")],
            [InlineKeyboardButton(
                text="❌ Отмена", callback_data="cancel_monitoring")]
        ])

        await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
        await state.set_state(MonitoringStates.selecting_logistics_shoulder)
        return

    if callback.data == "back_to_date_selection":
        # Возвращаемся к выбору периода
        data = await state.get_data()
        text = f"""
🎯 <b>Настройка мониторинга слотов</b>

✅ Коэффициенты: {data.get('coefficient_text')}

<b>Шаг 2/4: Выберите период мониторинга</b>

Выберите, на какой период искать доступные слоты:
        """

        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📅 Ближайшие 7 дней",
                                  callback_data="date_range_week")],
            [InlineKeyboardButton(text="📅 Ближайшие 14 дней",
                                  callback_data="date_range_2weeks")],
            [InlineKeyboardButton(text="📅 Ближайший месяц",
                                  callback_data="date_range_month")],
            [InlineKeyboardButton(text="📝 Указать свои даты",
                                  callback_data="date_range_custom")],
            [InlineKeyboardButton(
                text="⬅️ Назад", callback_data="back_to_coefficient")],
            [InlineKeyboardButton(
                text="❌ Отмена", callback_data="cancel_monitoring")]
        ])

        await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
        return

    from datetime import datetime, timedelta

    # Определяем диапазон дат
    now = datetime.now()
    date_ranges = {
        "date_range_week": (now, now + timedelta(days=7), "7 дней"),
        "date_range_2weeks": (now, now + timedelta(days=14), "14 дней"),
        "date_range_month": (now, now + timedelta(days=30), "месяц"),
    }

    if callback.data == "date_range_custom":
        # Показываем календарь для выбора даты начала
        now = datetime.now()

        data = await state.get_data()
        text = f"""
🎯 <b>Настройка мониторинга слотов</b>

✅ Коэффициенты: {data.get('coefficient_text')}

<b>Шаг 2/4: Выберите дату начала мониторинга</b>

Выберите дату начала периода:
        """

        calendar_kb = create_calendar(now.year, now.month)

        # Добавляем кнопки управления
        calendar_kb.inline_keyboard.extend([
            [InlineKeyboardButton(
                text="⬅️ Назад к выбору периода", callback_data="back_to_date_selection")],
            [InlineKeyboardButton(
                text="❌ Отмена", callback_data="cancel_monitoring")]
        ])

        await state.set_state(MonitoringStates.selecting_date_from)
        await callback.message.edit_text(text, reply_markup=calendar_kb, parse_mode="HTML")
        return

    if callback.data in date_ranges:
        date_from, date_to, period_text = date_ranges[callback.data]

        # Получаем логистическое плечо
        data = await state.get_data()
        logistics_shoulder = data.get('logistics_shoulder', 0)

        # Применяем логистическое плечо только к начальной дате
        date_from_with_shoulder = date_from + timedelta(days=logistics_shoulder)
        date_to_with_shoulder = date_to  # Конечная дата остается без изменений

        # Сохраняем выбранный период с учетом логистического плеча
        await state.update_data(
            date_from=date_from_with_shoulder,
            date_to=date_to_with_shoulder,
            period_text=period_text
        )

        # Получаем данные для отображения
        data = await state.get_data()

        # Переходим к выбору складов
        logistics_text = f"{logistics_shoulder} дней" if logistics_shoulder > 0 else "готов к отправке"

        text = f"""
🎯 <b>Настройка мониторинга слотов</b>

✅ Коэффициенты: {data.get('coefficient_text')}
✅ Логистическое плечо: {logistics_text}
✅ Период: {period_text}

<b>Шаг 4/5: Выберите склады для мониторинга</b>

Сейчас загрузим список доступных складов...
        """

        await callback.message.edit_text(text, parse_mode="HTML")

        # Получаем список складов через API
        await load_warehouses_for_selection(callback, state)


@monitoring_router.callback_query(MonitoringStates.selecting_date_from)
async def handle_date_from_calendar(callback: CallbackQuery, state: FSMContext):
    """Обработка календаря для выбора даты начала"""
    await callback.answer()

    if callback.data == "back_to_date_selection":
        # Возвращаемся к выбору периода
        data = await state.get_data()
        text = f"""
🎯 <b>Настройка мониторинга слотов</b>

✅ Коэффициенты: {data.get('coefficient_text')}

<b>Шаг 2/4: Выберите период мониторинга</b>

Выберите, на какой период искать доступные слоты:
        """

        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📅 Ближайшие 7 дней",
                                  callback_data="date_range_week")],
            [InlineKeyboardButton(text="📅 Ближайшие 14 дней",
                                  callback_data="date_range_2weeks")],
            [InlineKeyboardButton(text="📅 Ближайший месяц",
                                  callback_data="date_range_month")],
            [InlineKeyboardButton(text="📝 Указать свои даты",
                                  callback_data="date_range_custom")],
            [InlineKeyboardButton(
                text="⬅️ Назад", callback_data="back_to_coefficient")],
            [InlineKeyboardButton(
                text="❌ Отмена", callback_data="cancel_monitoring")]
        ])

        await state.set_state(MonitoringStates.selecting_date_range)
        await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
        return

    # Используем универсальные функции
    await handle_calendar_navigation(callback, state)
    await handle_date_selection(callback, state, "from")


@monitoring_router.callback_query(MonitoringStates.selecting_date_to)
async def handle_date_to_calendar(callback: CallbackQuery, state: FSMContext):
    """Обработка календаря для выбора даты окончания"""
    await callback.answer()

    data = await state.get_data()
    date_from = data.get('date_from')

    if callback.data == "back_to_date_from":
        # Возвращаемся к выбору даты начала
        now = datetime.now()
        text = f"""
🎯 <b>Настройка мониторинга слотов</b>

✅ Коэффициенты: {data.get('coefficient_text')}

<b>Шаг 2/4: Выберите дату начала мониторинга</b>

Выберите дату начала периода:
        """

        calendar_kb = create_calendar(now.year, now.month)
        calendar_kb.inline_keyboard.extend([
            [InlineKeyboardButton(
                text="⬅️ Назад к выбору периода", callback_data="back_to_date_selection")],
            [InlineKeyboardButton(
                text="❌ Отмена", callback_data="cancel_monitoring")]
        ])

        await state.set_state(MonitoringStates.selecting_date_from)
        await callback.message.edit_text(text, reply_markup=calendar_kb, parse_mode="HTML")
        return

    # Используем универсальные функции
    await handle_calendar_navigation(callback, state)
    await handle_date_selection(callback, state, "to")


async def update_warehouses_page(callback: CallbackQuery, state: FSMContext, page: int):
    """Обновить отображение страницы складов без запроса к API"""
    try:
        data = await state.get_data()
        available_warehouses = data.get('available_warehouses', [])
        selected_warehouses = data.get('selected_warehouses', [])

        if not available_warehouses:
            await callback.answer("❌ Список складов не загружен", show_alert=True)
            return

        # Обновляем текущую страницу
        await state.update_data(current_page=page)

        # Пагинация: 10 складов на странице
        warehouses_per_page = 10
        start_idx = page * warehouses_per_page
        end_idx = start_idx + warehouses_per_page
        current_warehouses = available_warehouses[start_idx:end_idx]

        total_pages = (len(available_warehouses) -
                       1) // warehouses_per_page + 1

        # Создаем клавиатуру со складами
        keyboard = create_warehouse_keyboard(
            available_warehouses, selected_warehouses, page)

        data = await state.get_data()
        updated_text = f"""
🎯 <b>Настройка мониторинга слотов</b>

✅ Коэффициенты: {data.get('coefficient_text')}
✅ Период: {data.get('period_text', '7 дней')}

<b>Шаг 3/4: Выберите склады WB ({len(selected_warehouses)}/5)</b>

📊 <b>Всего складов:</b> {len(available_warehouses)}
📄 <b>Страница:</b> {page + 1} из {total_pages}

Выберите склады, которые вы хотите мониторить.
Система будет проверять выбранные склады на соответствие вашим фильтрам (коэффициент, тип поставки, период).
        """

        await callback.message.edit_text(updated_text, reply_markup=keyboard, parse_mode="HTML")

    except Exception as e:
        logger.error(f"Error updating warehouses page: {e}")
        await callback.answer("❌ Ошибка при обновлении страницы", show_alert=True)


async def load_warehouses_for_selection(callback: CallbackQuery, state: FSMContext, page: int = 0):
    """Загрузить склады для выбора"""
    user_id = callback.from_user.id

    try:
        async with AsyncSessionLocal() as session:
            user_repo = UserRepository(session)
            user = await user_repo.get_by_telegram_id(user_id)
            wb_token = await user_repo.get_wb_token(user)

            # Получаем данные
            data = await state.get_data()

            # Используем сервис складов для получения кэшированных данных
            from app.services.warehouse_service import WarehouseService
            warehouse_service = WarehouseService(session)

            # Проверяем, есть ли кэшированные склады
            is_cached = await warehouse_service.is_warehouse_cached()

            if is_cached:
                # Используем кэшированные склады
                warehouses = await warehouse_service.get_cached_warehouses()
                logger.info(
                    f"Using {len(warehouses)} cached warehouses for user {user_id}")
            else:
                # Если кэша нет, получаем из API и кэшируем
                warehouses = await warehouse_service.get_warehouses_for_monitoring(wb_token, force_refresh=True)
                logger.info(
                    f"Fetched and cached {len(warehouses)} warehouses for user {user_id}")

            if not warehouses:
                await callback.message.edit_text(
                    f"❌ <b>Нет доступных складов WB</b>\n\n"
                    f"Не удалось загрузить список складов.\n\n"
                    "Попробуйте:\n"
                    "• Обновить список складов в кабинете\n"
                    "• Проверить подключение к интернету\n"
                    "• Убедиться, что API токен действителен",
                    reply_markup=create_warehouse_error_keyboard(),
                    parse_mode="HTML"
                )
                return

            # Сохраняем список складов и текущую страницу
            await state.update_data(
                available_warehouses=warehouses,
                current_page=page,
                warehouses_from_cache=is_cached
            )

            # Пагинация: 10 складов на странице
            warehouses_per_page = 10
            start_idx = page * warehouses_per_page
            end_idx = start_idx + warehouses_per_page
            current_warehouses = warehouses[start_idx:end_idx]

            total_pages = (len(warehouses) - 1) // warehouses_per_page + 1

            # Получаем выбранные склады
            data = await state.get_data()
            selected_warehouses = data.get('selected_warehouses', [])

            keyboard = create_warehouse_keyboard(
                warehouses, selected_warehouses, page)

            data = await state.get_data()
            is_cached = data.get('warehouses_from_cache', False)

            # Текст для режима создания
            cache_info = "📦 (из кэша)" if is_cached else "🔄 (обновлено из API)"
            updated_text = f"""
🎯 <b>Настройка мониторинга слотов</b>

✅ Коэффициенты: {data.get('coefficient_text')}
✅ Период: {data.get('period_text', '7 дней')}

<b>Шаг 3/4: Выберите склады WB ({len(selected_warehouses)}/5)</b>

📊 <b>Всего складов:</b> {len(warehouses)} {cache_info}
📄 <b>Страница:</b> {page + 1} из {total_pages}

Выберите склады, которые вы хотите мониторить.
Система будет проверять выбранные склады на соответствие вашим фильтрам (коэффициент, тип поставки, период).
            """

            await callback.message.edit_text(updated_text, reply_markup=keyboard, parse_mode="HTML")

            # Сохраняем текущие выбранные склады (не сбрасываем)
            if page == 0:  # Только при первой загрузке
                await state.update_data(selected_warehouses=[])

            await state.set_state(MonitoringStates.selecting_warehouses)

    except Exception as e:
        logger.error(f"Error loading warehouses for user {user_id}: {e}")
        await callback.message.edit_text(
            "❌ <b>Ошибка получения складов WB</b>\n\n"
            "Возможные причины:\n"
            "• API-токен не имеет прав на поставки\n"
            "• Временная недоступность API WB\n"
            "• Превышен лимит запросов\n\n"
            "Попробуйте позже или проверьте права токена.",
            parse_mode="HTML"
        )


@monitoring_router.callback_query(StateFilter(MonitoringStates.selecting_warehouses))
async def select_warehouses(callback: CallbackQuery, state: FSMContext):
    """Обработка выбора складов"""
    data = await state.get_data()
    selected_warehouses = data.get('selected_warehouses', [])
    available_warehouses = data.get('available_warehouses', [])
    current_page = data.get('current_page', 0)

    # Обработка пагинации
    if callback.data.startswith("warehouse_page_"):
        page = int(callback.data.split("_")[-1])
        await update_warehouses_page(callback, state, page)
        return

    if callback.data.startswith("select_page_warehouses_"):
        page = int(callback.data.split("_")[-1])
        # Выбираем все склады на текущей странице
        warehouses_per_page = 10
        start_idx = page * warehouses_per_page
        end_idx = start_idx + warehouses_per_page
        current_warehouses = available_warehouses[start_idx:end_idx]

        for warehouse in current_warehouses:
            warehouse_id = warehouse.get('ID') or warehouse.get('id')
            if warehouse_id and warehouse_id not in selected_warehouses and len(selected_warehouses) < 5:
                selected_warehouses.append(warehouse_id)

        await state.update_data(selected_warehouses=selected_warehouses)
        await update_warehouses_page(callback, state, page)
        return

    if callback.data == "current_page":
        # Игнорируем нажатие на индикатор страницы
        await callback.answer()
        return

    if callback.data == "back_to_coefficient":
        await start_monitoring_setup(callback, state)
        return

    if callback.data == "select_all_warehouses":
        # Выбираем все склады (максимум 5)
        all_warehouse_ids = []
        for w in available_warehouses[:5]:
            wh_id = w.get('ID') or w.get('id')
            if wh_id is not None:
                all_warehouse_ids.append(wh_id)
        await state.update_data(selected_warehouses=all_warehouse_ids)
        selected_warehouses = all_warehouse_ids

    elif callback.data.startswith("warehouse_"):
        # Переключаем выбор склада
        warehouse_id_str = callback.data.replace("warehouse_", "")
        try:
            warehouse_id = int(warehouse_id_str)
        except ValueError:
            logger.error(f"Invalid warehouse_id: {warehouse_id_str}")
            await callback.answer("❌ Ошибка: неверный ID склада", show_alert=True)
            return

        if warehouse_id in selected_warehouses:
            selected_warehouses.remove(warehouse_id)
        else:
            if len(selected_warehouses) < 5:
                selected_warehouses.append(warehouse_id)

        await state.update_data(selected_warehouses=selected_warehouses)

    elif callback.data == "continue_to_options":
        if not selected_warehouses:
            await callback.answer("⚠️ Выберите хотя бы один склад", show_alert=True)
            return

        # Запускаем мониторинг с базовыми настройками
        await start_final_monitoring(callback, state)
        return

    # Если это выбор отдельного склада, обновляем страницу
    if callback.data.startswith("warehouse_"):
        await update_warehouses_page(callback, state, current_page)
    else:
        # Обновляем клавиатуру с выбранными складами
        await update_warehouse_keyboard(callback, state, selected_warehouses, available_warehouses)


async def update_warehouse_keyboard(callback: CallbackQuery, state: FSMContext, selected_warehouses: list, available_warehouses: list):
    """Обновить клавиатуру выбора складов"""
    keyboard = create_warehouse_keyboard(
        available_warehouses, selected_warehouses, 0)

    data = await state.get_data()
    updated_text = f"""
🎯 <b>Настройка мониторинга слотов</b>

✅ Коэффициенты: {data.get('coefficient_text')}
✅ Период: {data.get('period_text', '7 дней')}

<b>Шаг 3/4: Выберите склады WB ({len(selected_warehouses)}/5)</b>

Выберите склады, которые вы хотите мониторить.
Система будет проверять выбранные склады на соответствие вашим фильтрам.
    """

    await callback.message.edit_text(updated_text, reply_markup=keyboard, parse_mode="HTML")


async def start_final_monitoring(callback: CallbackQuery, state: FSMContext):
    """Финальный запуск мониторинга"""
    data = await state.get_data()
    user_id = callback.from_user.id

    try:
        async with AsyncSessionLocal() as session:
            user_repo = UserRepository(session)
            user = await user_repo.get_by_telegram_id(user_id)

            slot_repo = SlotMonitoringRepository(session)

            # Создаем мониторинг
            monitoring = await slot_repo.create_monitoring(
                user=user,
                coefficient_min=data.get('coefficient_min'),
                coefficient_max=data.get('coefficient_max'),
                warehouse_ids=data.get('selected_warehouses'),
                logistics_shoulder=data.get('logistics_shoulder', 0),
                box_type_id=data.get('box_type_id'),
                acceptance_options={
                    'period': data.get('period_text', '7 дней'),
                    'notification_type': 'instant'
                },
                date_from=data.get('date_from', datetime.now()),
                date_to=data.get('date_to', datetime.now() + timedelta(days=7)),
                order_number=data.get('selected_order_number')
            )

            # Формируем сводку
            warehouse_names = []
            for warehouse in data.get('available_warehouses', []):
                warehouse_id = warehouse.get('ID') or warehouse.get('id')
                if warehouse_id in data.get('selected_warehouses', []):
                    warehouse_name = warehouse.get(
                        'name', f"Склад {warehouse_id}")
                    warehouse_names.append(
                        f"{warehouse_name} (ID: {warehouse_id})")

            logistics_text = f"{data.get('logistics_shoulder', 0)} дней" if data.get(
                'logistics_shoulder', 0) > 0 else "готов к отправке"
            box_type_text = data.get('box_type_text', '🌟 Любой тип')

            # Добавляем информацию о заказе если он выбран
            order_info = ""
            if data.get('selected_order_number'):
                order_info = f"• Заказ: {data.get('selected_order_number')}\n"
            
            success_text = f"""
✅ <b>Мониторинг запущен!</b>

<b>📊 Параметры мониторинга:</b>
• ID: #{monitoring.id}
{order_info}• Коэффициенты: {data.get('coefficient_text')}
• Тип упаковки: {box_type_text}
• Логистическое плечо: {logistics_text}
• Складов: {len(data.get('selected_warehouses', []))}
• Период: {data.get('period_text', '7 дней')}

<b>🏪 Склады:</b>
{chr(10).join([f"• {name}" for name in warehouse_names[:3]])}
{f"• ... и ещё {len(warehouse_names) - 3}" if len(warehouse_names) > 3 else ""}

<b>🎯 Статус:</b> Активный мониторинг
<b>⏰ Интервал проверки:</b> каждые 12 секунд

Бот будет автоматически искать подходящие слоты и уведомлять вас о найденных вариантах.
            """

            keyboard = create_monitoring_success_keyboard()

            await callback.message.edit_text(success_text, reply_markup=keyboard, parse_mode="HTML")
            await state.clear()

            logger.info(
                f"Started monitoring {monitoring.id} for user {user.telegram_id}")

    except Exception as e:
        logger.error(
            f"Error starting final monitoring for user {user_id}: {e}")
        await callback.message.edit_text(
            "❌ <b>Ошибка запуска мониторинга</b>\n\n"
            "Попробуйте позже или обратитесь в поддержку.",
            parse_mode="HTML"
        )
        await state.clear()


@monitoring_router.callback_query(F.data == "cancel_monitoring")
async def cancel_monitoring(callback: CallbackQuery, state: FSMContext):
    """Отмена настройки мониторинга"""
    await state.clear()
    await callback.message.edit_text(
        "❌ <b>Настройка мониторинга отменена</b>\n\n"
        "Используйте /cabinet_info для возврата в кабинет.",
        parse_mode="HTML"
    )


@monitoring_router.callback_query(F.data == "my_monitorings")
async def show_my_monitorings(callback: CallbackQuery):
    """Показать активные мониторинги пользователя"""
    user_id = callback.from_user.id

    try:
        async with AsyncSessionLocal() as session:
            user_repo = UserRepository(session)
            user = await user_repo.get_by_telegram_id(user_id)

            slot_repo = SlotMonitoringRepository(session)
            monitorings = await slot_repo.get_active_monitorings(user)

            if not monitorings:
                keyboard = create_no_monitorings_keyboard()

                await callback.message.edit_text(
                    "📊 <b>Активные мониторинги</b>\n\n"
                    "❌ У вас нет активных мониторингов\n\n"
                    "Создайте мониторинг через автобронирование в кабинете.",
                    reply_markup=keyboard,
                    parse_mode="HTML"
                )
                return

            text = "📊 <b>Ваши активные мониторинги</b>\n\n"

            for monitoring in monitorings:
                status_emoji = "🟢" if monitoring.status == "active" else "🟡"
                last_check = monitoring.last_check_at.strftime(
                    '%H:%M') if monitoring.last_check_at else "Никогда"

                logistics_text = f"{monitoring.logistics_shoulder} дней" if monitoring.logistics_shoulder > 0 else "готов к отправке"

                # Определяем тип упаковки
                box_type_text = "🌟 Любой тип"
                if monitoring.box_type_id == 2:
                    box_type_text = "📦 Короба"
                elif monitoring.box_type_id == 5:
                    box_type_text = "🛒 Монопаллеты"
                elif monitoring.box_type_id == 6:
                    box_type_text = "🚛 Суперсейф"

                text += f"""
{status_emoji} <b>Мониторинг #{monitoring.id}</b>
• Коэффициенты: {monitoring.coefficient_min}-{monitoring.coefficient_max}
• Тип упаковки: {box_type_text}
• Логистическое плечо: {logistics_text}
• Складов: {len(monitoring.warehouse_ids)}
• Последняя проверка: {last_check}

"""

            keyboard = create_my_monitorings_keyboard(monitorings)

            await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")

    except Exception as e:
        logger.error(f"Error showing monitorings for user {user_id}: {e}")
        await callback.message.edit_text(
            "❌ <b>Ошибка загрузки мониторингов</b>\n\n"
            "Попробуйте позже.",
            parse_mode="HTML"
        )


@monitoring_router.callback_query(F.data.startswith("delete_monitoring_"))
async def delete_monitoring(callback: CallbackQuery, state: FSMContext):
    """Удалить конкретный мониторинг"""
    await callback.answer()

    try:
        # Извлекаем ID мониторинга из callback_data
        monitoring_id = int(callback.data.split("_")[-1])
        user_id = callback.from_user.id

        async with AsyncSessionLocal() as session:
            user_repo = UserRepository(session)
            slot_repo = SlotMonitoringRepository(session)

            # Получаем пользователя
            user = await user_repo.get_by_telegram_id(user_id)
            if not user:
                await callback.message.edit_text(
                    "❌ <b>Пользователь не найден</b>",
                    parse_mode="HTML"
                )
                return

            # Проверяем, принадлежит ли мониторинг пользователю
            monitoring = await slot_repo.get_monitoring_by_id(monitoring_id)
            if not monitoring or monitoring.user.telegram_id != user_id:
                await callback.message.edit_text(
                    "❌ <b>Мониторинг не найден</b>\n\n"
                    "Возможно, он уже был удален.",
                    parse_mode="HTML"
                )
                return

            # Удаляем мониторинг
            success = await slot_repo.delete_monitoring(monitoring_id, user)

            if success:
                await callback.message.edit_text(
                    f"🗑️ <b>Мониторинг #{monitoring_id} удален</b>\n\n"
                    f"• Коэффициенты: {monitoring.coefficient_min}-{monitoring.coefficient_max}\n"
                    f"• Складов: {len(monitoring.warehouse_ids)}\n\n"
                    "Мониторинг полностью удален из системы.",
                    reply_markup=create_delete_confirmation_keyboard(
                        monitoring_id),
                    parse_mode="HTML"
                )

                logger.info(
                    f"Monitoring {monitoring_id} deleted by user {user_id}")
            else:
                await callback.message.edit_text(
                    "❌ <b>Ошибка удаления мониторинга</b>\n\n"
                    "Попробуйте позже.",
                    parse_mode="HTML"
                )

    except ValueError:
        await callback.message.edit_text(
            "❌ <b>Неверный ID мониторинга</b>",
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"Error stopping monitoring: {e}")
        await callback.message.edit_text(
            "❌ <b>Ошибка остановки мониторинга</b>\n\n"
            "Попробуйте позже.",
            parse_mode="HTML"
        )


@monitoring_router.callback_query(F.data.startswith("edit_monitoring_"))
async def start_edit_monitoring(callback: CallbackQuery, state: FSMContext):
    """Начать редактирование мониторинга"""
    user_id = callback.from_user.id

    try:
        # Извлекаем ID мониторинга из callback_data
        monitoring_id = int(callback.data.split("_")[-1])

        async with AsyncSessionLocal() as session:
            slot_repo = SlotMonitoringRepository(session)

            # Получаем мониторинг
            monitoring = await slot_repo.get_monitoring_by_id(monitoring_id)

            if not monitoring or monitoring.user.telegram_id != user_id:
                await callback.message.edit_text(
                    "❌ <b>Мониторинг не найден</b>\n\n"
                    "Возможно, он уже был удален.",
                    parse_mode="HTML"
                )
                return

            # Сохраняем ID мониторинга в состоянии
            await state.update_data(editing_monitoring_id=monitoring_id)

            # Определяем тип упаковки
            box_type_text = "🌟 Любой тип"
            if monitoring.box_type_id == 2:
                box_type_text = "📦 Короба"
            elif monitoring.box_type_id == 5:
                box_type_text = "🛒 Монопаллеты"
            elif monitoring.box_type_id == 6:
                box_type_text = "🚛 Суперсейф"

            # Показываем меню редактирования
            await callback.message.edit_text(
                f"✏️ <b>Редактирование мониторинга #{monitoring_id}</b>\n\n"
                f"<b>Текущие настройки:</b>\n"
                f"• Коэффициенты: {monitoring.coefficient_min}-{monitoring.coefficient_max}\n"
                f"• Тип упаковки: {box_type_text}\n"
                f"• Логистическое плечо: {monitoring.logistics_shoulder} дней\n"
                f"• Период: {monitoring.date_from.strftime('%d.%m.%Y')} - {monitoring.date_to.strftime('%d.%m.%Y')}\n"
                f"• Склады: {len(monitoring.warehouse_ids)} шт.\n\n"
                f"<b>Что хотите изменить?</b>",
                reply_markup=create_edit_monitoring_keyboard(),
                parse_mode="HTML"
            )

            await state.set_state(MonitoringStates.editing_monitoring)

    except ValueError:
        await callback.message.edit_text(
            "❌ <b>Неверный ID мониторинга</b>",
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"Error starting edit monitoring: {e}")
        await callback.message.edit_text(
            "❌ <b>Ошибка редактирования мониторинга</b>\n\n"
            "Попробуйте позже.",
            parse_mode="HTML"
        )


@monitoring_router.callback_query(F.data == "edit_coefficient")
async def edit_coefficient(callback: CallbackQuery, state: FSMContext):
    """Редактировать коэффициенты мониторинга"""
    await callback.message.edit_text(
        "📊 <b>Выберите максимальный коэффициент приемки</b>\n\n"
        "Выберите максимальный коэффициент (от 0 до выбранного):",
        reply_markup=create_edit_coefficient_keyboard(),
        parse_mode="HTML"
    )
    await state.set_state(MonitoringStates.editing_coefficient)


@monitoring_router.callback_query(F.data.startswith("edit_coeff_"))
async def select_edit_coefficient(callback: CallbackQuery, state: FSMContext):
    """Обработать выбор коэффициента для редактирования"""
    try:
        # Извлекаем коэффициенты из callback_data
        parts = callback.data.split("_")
        coeff_min = int(parts[2])
        coeff_max = int(parts[3])

        # Сохраняем выбранные коэффициенты
        await state.update_data(
            coefficient_min=coeff_min,
            coefficient_max=coeff_max
        )

        # Получаем данные мониторинга
        data = await state.get_data()
        monitoring_id = data.get("editing_monitoring_id")

        await callback.message.edit_text(
            f"✅ <b>Коэффициенты обновлены</b>\n\n"
            f"• Новые коэффициенты: {coeff_min}-{coeff_max}\n\n"
            f"<b>Мониторинг #{monitoring_id}</b>\n"
            f"Что еще хотите изменить?",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(
                    text="🚚 Логистическое плечо", callback_data="edit_logistics_shoulder")],
                [InlineKeyboardButton(
                    text="📅 Период мониторинга", callback_data="edit_date_range")],
                [InlineKeyboardButton(
                    text="✅ Сохранить изменения", callback_data="confirm_edit")],
                [InlineKeyboardButton(
                    text="❌ Отмена", callback_data="my_monitorings")]
            ]),
            parse_mode="HTML"
        )

        await state.set_state(MonitoringStates.editing_monitoring)

    except Exception as e:
        logger.error(f"Error selecting edit coefficient: {e}")
        await callback.message.edit_text(
            "❌ <b>Ошибка выбора коэффициента</b>",
            parse_mode="HTML"
        )


@monitoring_router.callback_query(F.data == "edit_box_type")
async def edit_box_type(callback: CallbackQuery, state: FSMContext):
    """Редактировать тип упаковки мониторинга"""
    await callback.message.edit_text(
        "📦 <b>Выберите тип упаковки</b>\n\n"
        "Тип упаковки определяет способ поставки товара на склад WB:",
        reply_markup=create_edit_box_type_keyboard(),
        parse_mode="HTML"
    )


@monitoring_router.callback_query(F.data.startswith("edit_box_type_"))
async def select_edit_box_type(callback: CallbackQuery, state: FSMContext):
    """Обработать выбор типа упаковки для редактирования"""
    try:
        # Извлекаем тип упаковки из callback_data
        box_type_data = {
            "edit_box_type_2": (2, "📦 Короба"),
            "edit_box_type_5": (5, "🛒 Монопаллеты"),
            "edit_box_type_6": (6, "🚛 Суперсейф"),
            "edit_box_type_any": (None, "🌟 Любой тип")
        }

        if callback.data in box_type_data:
            box_type_id, box_type_text = box_type_data[callback.data]

            # Сохраняем выбранный тип упаковки
            await state.update_data(
                box_type_id=box_type_id,
                box_type_text=box_type_text
            )

            # Получаем данные мониторинга
            data = await state.get_data()
            monitoring_id = data.get("editing_monitoring_id")

            await callback.message.edit_text(
                f"✅ <b>Тип упаковки обновлен</b>\n\n"
                f"• Новый тип: {box_type_text}\n\n"
                f"<b>Мониторинг #{monitoring_id}</b>\n"
                f"Что еще хотите изменить?",
                reply_markup=create_edit_confirm_keyboard(),
                parse_mode="HTML"
            )

            await state.set_state(MonitoringStates.editing_monitoring)

    except Exception as e:
        logger.error(f"Error selecting edit box type: {e}")
        await callback.message.edit_text(
            "❌ <b>Ошибка выбора типа упаковки</b>",
            parse_mode="HTML"
        )


@monitoring_router.callback_query(F.data == "edit_logistics_shoulder")
async def edit_logistics_shoulder(callback: CallbackQuery, state: FSMContext):
    """Редактировать логистическое плечо"""
    await callback.message.edit_text(
        "🚚 <b>Выберите логистическое плечо</b>\n\n"
        "Сколько дней закладываем на доставку товара на склад?",
        reply_markup=create_edit_logistics_shoulder_keyboard(),
        parse_mode="HTML"
    )
    await state.set_state(MonitoringStates.editing_logistics_shoulder)


@monitoring_router.callback_query(F.data.startswith("edit_logistics_"))
async def select_edit_logistics_shoulder(callback: CallbackQuery, state: FSMContext):
    """Обработать выбор логистического плеча для редактирования"""
    try:
        # Извлекаем количество дней
        days = int(callback.data.split("_")[-1])

        # Сохраняем логистическое плечо
        await state.update_data(logistics_shoulder=days)

        # Получаем данные мониторинга
        data = await state.get_data()
        monitoring_id = data.get("editing_monitoring_id")

        await callback.message.edit_text(
            f"✅ <b>Логистическое плечо обновлено</b>\n\n"
            f"• Новое плечо: {days} дней\n\n"
            f"<b>Мониторинг #{monitoring_id}</b>\n"
            f"Что еще хотите изменить?",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(
                    text="📊 Коэффициенты", callback_data="edit_coefficient")],
                [InlineKeyboardButton(
                    text="📅 Период мониторинга", callback_data="edit_date_range")],
                [InlineKeyboardButton(
                    text="✅ Сохранить изменения", callback_data="confirm_edit")],
                [InlineKeyboardButton(
                    text="❌ Отмена", callback_data="my_monitorings")]
            ]),
            parse_mode="HTML"
        )

        await state.set_state(MonitoringStates.editing_monitoring)

    except Exception as e:
        logger.error(f"Error selecting edit logistics shoulder: {e}")
        await callback.message.edit_text(
            "❌ <b>Ошибка выбора логистического плеча</b>",
            parse_mode="HTML"
        )


@monitoring_router.callback_query(F.data == "confirm_edit")
async def confirm_edit_monitoring(callback: CallbackQuery, state: FSMContext):
    """Подтвердить изменения мониторинга"""
    user_id = callback.from_user.id

    try:
        data = await state.get_data()
        monitoring_id = data.get("editing_monitoring_id")

        if not monitoring_id:
            await callback.message.edit_text(
                "❌ <b>Ошибка: ID мониторинга не найден</b>",
                parse_mode="HTML"
            )
            return

        async with AsyncSessionLocal() as session:
            slot_repo = SlotMonitoringRepository(session)

            # Получаем мониторинг
            monitoring = await slot_repo.get_monitoring_by_id(monitoring_id)

            if not monitoring or monitoring.user.telegram_id != user_id:
                await callback.message.edit_text(
                    "❌ <b>Мониторинг не найден</b>",
                    parse_mode="HTML"
                )
                return

            # Обновляем мониторинг с новыми данными
            update_data = {}

            if "coefficient_min" in data:
                update_data["coefficient_min"] = data["coefficient_min"]
            if "coefficient_max" in data:
                update_data["coefficient_max"] = data["coefficient_max"]
            if "box_type_id" in data:
                update_data["box_type_id"] = data["box_type_id"]
            if "logistics_shoulder" in data:
                update_data["logistics_shoulder"] = data["logistics_shoulder"]
            if "date_from" in data:
                update_data["date_from"] = data["date_from"]
            if "date_to" in data:
                update_data["date_to"] = data["date_to"]

            if update_data:
                success = await slot_repo.update_monitoring(monitoring_id, **update_data)

                if success:
                    await session.commit()

                    # Очищаем кэш для обновленного мониторинга
                    from app.services.slot_monitor import slot_monitor_service
                    if slot_monitor_service:
                        slot_monitor_service.clear_monitoring_cache(
                            monitoring_id)

                    await callback.message.edit_text(
                        f"✅ <b>Мониторинг #{monitoring_id} обновлен</b>\n\n"
                        f"Изменения сохранены. Мониторинг продолжает работать с новыми параметрами.\n\n"
                        f"🔄 <b>Кэш очищен</b> - мониторинг начнет поиск с новых параметров.",
                        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                            [InlineKeyboardButton(
                                text="📊 Мои мониторинги", callback_data="my_monitorings")],
                            [InlineKeyboardButton(
                                text="📊 Кабинет", callback_data="cabinet_info")]
                        ]),
                        parse_mode="HTML"
                    )

                    logger.info(
                        f"Monitoring {monitoring_id} updated by user {user_id}, cache cleared")
                else:
                    await callback.message.edit_text(
                        "❌ <b>Ошибка обновления мониторинга</b>\n\n"
                        "Попробуйте позже.",
                        parse_mode="HTML"
                    )
            else:
                await callback.message.edit_text(
                    "ℹ️ <b>Изменений не было</b>\n\n"
                    "Мониторинг остался без изменений.",
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(
                            text="📊 Мои мониторинги", callback_data="my_monitorings")]
                    ]),
                    parse_mode="HTML"
                )

            # Очищаем состояние
            await state.clear()

    except Exception as e:
        logger.error(f"Error confirming edit monitoring: {e}")
        await callback.message.edit_text(
            "❌ <b>Ошибка обновления мониторинга</b>\n\n"
            "Попробуйте позже.",
            parse_mode="HTML"
        )


@monitoring_router.callback_query(F.data == "edit_date_range")
async def edit_date_range(callback: CallbackQuery, state: FSMContext):
    """Редактирование периода мониторинга"""
    try:
        data = await state.get_data()
        monitoring_id = data.get("editing_monitoring_id")

        if not monitoring_id:
            await callback.message.edit_text(
                "❌ <b>Ошибка: ID мониторинга не найден</b>",
                parse_mode="HTML"
            )
            return

        async with AsyncSessionLocal() as session:
            slot_repo = SlotMonitoringRepository(session)

            # Получаем мониторинг
            monitoring = await slot_repo.get_monitoring_by_id(monitoring_id)

            if not monitoring:
                await callback.message.edit_text(
                    "❌ <b>Мониторинг не найден</b>",
                    parse_mode="HTML"
                )
                return

            # Показываем меню выбора периода
            await callback.message.edit_text(
                f"📅 <b>Редактирование периода мониторинга #{monitoring_id}</b>\n\n"
                f"<b>Текущий период:</b>\n"
                f"• С: {monitoring.date_from.strftime('%d.%m.%Y')}\n"
                f"• По: {monitoring.date_to.strftime('%d.%m.%Y')}\n\n"
                f"<b>Выберите новый период:</b>",
                reply_markup=create_edit_date_range_keyboard(),
                parse_mode="HTML"
            )

    except Exception as e:
        logger.error(f"Error editing date range: {e}")
        await callback.message.edit_text(
            "❌ <b>Ошибка редактирования периода</b>",
            parse_mode="HTML"
        )


@monitoring_router.callback_query(F.data == "edit_select_dates")
async def edit_select_dates(callback: CallbackQuery, state: FSMContext):
    """Выбор дат вручную для редактирования"""
    try:
        # Показываем календарь для выбора начальной даты
        today = datetime.now()
        calendar_keyboard = create_calendar(today.year, today.month)

        await callback.message.edit_text(
            "📅 <b>Выберите начальную дату мониторинга</b>\n\n"
            "Нажмите на дату, с которой хотите начать мониторинг.",
            reply_markup=calendar_keyboard,
            parse_mode="HTML"
        )

        await state.set_state(MonitoringStates.editing_date_from)

    except Exception as e:
        logger.error(f"Error selecting edit dates: {e}")
        await callback.message.edit_text(
            "❌ <b>Ошибка выбора дат</b>",
            parse_mode="HTML"
        )


@monitoring_router.callback_query(F.data == "edit_quick_period")
async def edit_quick_period(callback: CallbackQuery, state: FSMContext):
    """Быстрый выбор периода для редактирования"""
    try:
        await callback.message.edit_text(
            "📊 <b>Быстрый выбор периода</b>\n\n"
            "Выберите период мониторинга:",
            reply_markup=create_edit_quick_period_keyboard(),
            parse_mode="HTML"
        )

    except Exception as e:
        logger.error(f"Error selecting edit quick period: {e}")
        await callback.message.edit_text(
            "❌ <b>Ошибка выбора периода</b>",
            parse_mode="HTML"
        )


@monitoring_router.callback_query(F.data.startswith("edit_period_"))
async def select_edit_period(callback: CallbackQuery, state: FSMContext):
    """Выбрать период для редактирования"""
    try:
        # Извлекаем количество дней
        days = int(callback.data.split("_")[-1])

        # Рассчитываем даты
        today = datetime.now().date()
        date_from = today
        date_to = today + timedelta(days=days)
        
        # Получаем логистическое плечо
        data = await state.get_data()
        logistics_shoulder = data.get('logistics_shoulder', 0)
        
        # Применяем логистическое плечо только к начальной дате
        date_from_with_shoulder = date_from + timedelta(days=logistics_shoulder)
        date_to_with_shoulder = date_to  # Конечная дата остается без изменений

        # Сохраняем даты с учетом логистического плеча
        await state.update_data(
            date_from=date_from_with_shoulder,
            date_to=date_to_with_shoulder
        )

        # Получаем данные мониторинга
        data = await state.get_data()
        monitoring_id = data.get("editing_monitoring_id")

        await callback.message.edit_text(
            f"✅ <b>Период обновлен</b>\n\n"
            f"• Новый период: {date_from.strftime('%d.%m.%Y')} - {date_to.strftime('%d.%m.%Y')}\n"
            f"• Длительность: {days} дней\n\n"
            f"<b>Мониторинг #{monitoring_id}</b>\n"
            f"Что еще хотите изменить?",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(
                    text="📊 Коэффициенты", callback_data="edit_coefficient")],
                [InlineKeyboardButton(
                    text="📦 Тип упаковки", callback_data="edit_box_type")],
                [InlineKeyboardButton(
                    text="🚚 Логистическое плечо", callback_data="edit_logistics_shoulder")],
                [InlineKeyboardButton(
                    text="✅ Сохранить изменения", callback_data="confirm_edit")],
                [InlineKeyboardButton(
                    text="❌ Отмена", callback_data="my_monitorings")]
            ]),
            parse_mode="HTML"
        )

        await state.set_state(MonitoringStates.editing_monitoring)

    except Exception as e:
        logger.error(f"Error selecting edit period: {e}")
        await callback.message.edit_text(
            "❌ <b>Ошибка выбора периода</b>",
            parse_mode="HTML"
        )


@monitoring_router.callback_query(MonitoringStates.editing_date_from)
async def handle_edit_date_from_calendar(callback: CallbackQuery, state: FSMContext):
    """Обработка выбора начальной даты для редактирования"""
    await callback.answer()

    # Используем универсальные функции
    await handle_calendar_navigation(callback, state)
    await handle_date_selection(callback, state, "from")


@monitoring_router.callback_query(MonitoringStates.editing_date_to)
async def handle_edit_date_to_calendar(callback: CallbackQuery, state: FSMContext):
    """Обработка выбора конечной даты для редактирования"""
    await callback.answer()

    # Используем универсальные функции
    await handle_calendar_navigation(callback, state)
    await handle_date_selection(callback, state, "to")


@monitoring_router.callback_query(F.data == "edit_monitoring_back")
async def back_to_edit_menu(callback: CallbackQuery, state: FSMContext):
    """Вернуться в меню редактирования"""
    data = await state.get_data()
    monitoring_id = data.get("editing_monitoring_id")

    if not monitoring_id:
        await callback.message.edit_text(
            "❌ <b>Ошибка: ID мониторинга не найден</b>",
            parse_mode="HTML"
        )
        return

    # Возвращаемся к началу редактирования
    await start_edit_monitoring(callback, state)


# Обработчик ручного автобронирования удален - теперь бронирование происходит автоматически
