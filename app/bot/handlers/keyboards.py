"""Клавиатуры для мониторинга слотов"""

from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from datetime import datetime
import calendar


def create_coefficient_keyboard() -> InlineKeyboardMarkup:
    """Создать клавиатуру выбора коэффициента"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="🟢 Только бесплатные (0)", callback_data="coeff_0"),
            InlineKeyboardButton(text="🟡 До x1 (0-1)", callback_data="coeff_1")
        ],
        [
            InlineKeyboardButton(text="🟡 До x2 (0-2)",
                                 callback_data="coeff_2"),
            InlineKeyboardButton(text="🟡 До x3 (0-3)", callback_data="coeff_3")
        ],
        [
            InlineKeyboardButton(text="🟡 До x4 (0-4)",
                                 callback_data="coeff_4"),
            InlineKeyboardButton(text="🟡 До x5 (0-5)", callback_data="coeff_5")
        ],
        [
            InlineKeyboardButton(text="🟠 До x6 (0-6)",
                                 callback_data="coeff_6"),
            InlineKeyboardButton(text="🟠 До x7 (0-7)", callback_data="coeff_7")
        ],
        [
            InlineKeyboardButton(text="🟠 До x8 (0-8)",
                                 callback_data="coeff_8"),
            InlineKeyboardButton(text="🟠 До x9 (0-9)", callback_data="coeff_9")
        ],
        [
            InlineKeyboardButton(text="🟠 До x10 (0-10)",
                                 callback_data="coeff_10"),
            InlineKeyboardButton(text="🔴 До x20 (0-20)",
                                 callback_data="coeff_20")
        ],
        [InlineKeyboardButton(text="🌟 Любой коэффициент",
                              callback_data="coeff_any")],
        [InlineKeyboardButton(
            text="❌ Отмена", callback_data="cancel_monitoring")]
    ])


def create_box_type_keyboard() -> InlineKeyboardMarkup:
    """Создать клавиатуру выбора типа упаковки"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📦 Короба", callback_data="box_type_2")],
        [InlineKeyboardButton(text="🛒 Монопаллеты",
                              callback_data="box_type_5")],
        [InlineKeyboardButton(text="🚛 Суперсейф", callback_data="box_type_6")],
        [InlineKeyboardButton(text="🌟 Любой тип",
                              callback_data="box_type_any")],
        [InlineKeyboardButton(
            text="⬅️ Назад", callback_data="back_to_coefficient")],
        [InlineKeyboardButton(
            text="❌ Отмена", callback_data="cancel_monitoring")]
    ])


def create_logistics_shoulder_keyboard() -> InlineKeyboardMarkup:
    """Создать клавиатуру выбора логистического плеча"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="🚚 0 дней (готов к отправке)", callback_data="logistics_0")],
        [InlineKeyboardButton(text="🚚 1 день", callback_data="logistics_1")],
        [InlineKeyboardButton(text="🚚 2 дня", callback_data="logistics_2")],
        [InlineKeyboardButton(text="🚚 3 дня", callback_data="logistics_3")],
        [InlineKeyboardButton(text="🚚 4 дня", callback_data="logistics_4")],
        [InlineKeyboardButton(
            text="⬅️ Назад", callback_data="back_to_box_type")],
        [InlineKeyboardButton(
            text="❌ Отмена", callback_data="cancel_monitoring")]
    ])


def create_date_range_keyboard() -> InlineKeyboardMarkup:
    """Создать клавиатуру выбора периода мониторинга"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📅 Ближайшие 7 дней",
                              callback_data="date_range_week")],
        [InlineKeyboardButton(text="📅 Ближайшие 14 дней",
                              callback_data="date_range_2weeks")],
        [InlineKeyboardButton(text="📅 Ближайший месяц",
                              callback_data="date_range_month")],
        [InlineKeyboardButton(text="📝 Указать свои даты",
                              callback_data="date_range_custom")],
        [InlineKeyboardButton(
            text="⬅️ Назад", callback_data="back_to_logistics")],
        [InlineKeyboardButton(
            text="❌ Отмена", callback_data="cancel_monitoring")]
    ])


def create_calendar(year: int, month: int, selected_dates=None) -> InlineKeyboardMarkup:
    """Создать календарь для выбора даты"""
    if selected_dates is None:
        selected_dates = []

    # Получаем календарь месяца
    cal = calendar.monthcalendar(year, month)

    # Названия месяцев
    month_names = [
        "Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
        "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь"
    ]

    keyboard = []

    # Заголовок с месяцем и годом
    keyboard.append([
        InlineKeyboardButton(
            text="◀️", callback_data=f"cal_prev_{year}_{month}"),
        InlineKeyboardButton(
            text=f"{month_names[month-1]} {year}", callback_data="cal_ignore"),
        InlineKeyboardButton(
            text="▶️", callback_data=f"cal_next_{year}_{month}")
    ])

    # Дни недели
    keyboard.append([
        InlineKeyboardButton(text="Пн", callback_data="cal_ignore"),
        InlineKeyboardButton(text="Вт", callback_data="cal_ignore"),
        InlineKeyboardButton(text="Ср", callback_data="cal_ignore"),
        InlineKeyboardButton(text="Чт", callback_data="cal_ignore"),
        InlineKeyboardButton(text="Пт", callback_data="cal_ignore"),
        InlineKeyboardButton(text="Сб", callback_data="cal_ignore"),
        InlineKeyboardButton(text="Вс", callback_data="cal_ignore")
    ])

    # Дни месяца
    today = datetime.now().date()
    for week in cal:
        row = []
        for day in week:
            if day == 0:
                row.append(InlineKeyboardButton(
                    text=" ", callback_data="cal_ignore"))
            else:
                date = datetime(year, month, day).date()

                # Проверяем, не в прошлом ли дата
                if date < today:
                    row.append(InlineKeyboardButton(
                        text="❌", callback_data="cal_ignore"))
                else:
                    # Проверяем, выбрана ли дата
                    if date in selected_dates:
                        text = f"✅{day}"
                    else:
                        text = str(day)

                    row.append(InlineKeyboardButton(
                        text=text,
                        callback_data=f"cal_select_{year}_{month}_{day}"
                    ))
        keyboard.append(row)

    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def create_warehouse_keyboard(warehouses: list, selected_warehouses: list, page: int = 0) -> InlineKeyboardMarkup:
    """Создать клавиатуру выбора складов"""
    warehouse_buttons = []

    # Пагинация: 10 складов на странице
    warehouses_per_page = 10
    start_idx = page * warehouses_per_page
    end_idx = start_idx + warehouses_per_page
    current_warehouses = warehouses[start_idx:end_idx]

    total_pages = (len(warehouses) - 1) // warehouses_per_page + 1

    for warehouse in current_warehouses:
        # API возвращает ID с большой буквы или маленькой в зависимости от метода
        warehouse_id = warehouse.get('ID') or warehouse.get('id')
        warehouse_name = warehouse.get('name', f'Склад {warehouse_id}')

        # Пропускаем склады без валидного ID
        if warehouse_id is None:
            continue

        # Определяем чекбокс (выбран или нет)
        checkbox = "☑️" if warehouse_id in selected_warehouses else "☐"

        warehouse_buttons.append([
            InlineKeyboardButton(
                text=f"{checkbox} {warehouse_name}",
                callback_data=f"warehouse_{warehouse_id}"
            )
        ])

    # Добавляем кнопки пагинации если нужно
    pagination_buttons = []
    if total_pages > 1:
        nav_buttons = []

        # Кнопка "Назад" по страницам
        if page > 0:
            nav_buttons.append(
                InlineKeyboardButton(
                    text="⬅️ Пред.", callback_data=f"warehouse_page_{page-1}")
            )

        # Показываем текущую страницу
        nav_buttons.append(
            InlineKeyboardButton(
                text=f"{page + 1}/{total_pages}", callback_data="current_page")
        )

        # Кнопка "Вперед" по страницам
        if page < total_pages - 1:
            nav_buttons.append(
                InlineKeyboardButton(
                    text="След. ➡️", callback_data=f"warehouse_page_{page+1}")
            )

        pagination_buttons.append(nav_buttons)

    # Добавляем основные кнопки управления
    warehouse_buttons.extend(pagination_buttons)
    warehouse_buttons.extend([
        [InlineKeyboardButton(text="✅ Выбрать все на странице",
                              callback_data=f"select_page_warehouses_{page}")],
        [InlineKeyboardButton(text="🚀 Запустить мониторинг",
                              callback_data="continue_to_options")],
        [InlineKeyboardButton(
            text="⬅️ Назад", callback_data="back_to_date_selection")],
        [InlineKeyboardButton(
            text="❌ Отмена", callback_data="cancel_monitoring")]
    ])

    return InlineKeyboardMarkup(inline_keyboard=warehouse_buttons)


def create_edit_monitoring_keyboard() -> InlineKeyboardMarkup:
    """Создать клавиатуру редактирования мониторинга"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Коэффициенты",
                              callback_data="edit_coefficient")],
        [InlineKeyboardButton(text="📦 Тип упаковки",
                              callback_data="edit_box_type")],
        [InlineKeyboardButton(text="🚚 Логистическое плечо",
                              callback_data="edit_logistics_shoulder")],
        [InlineKeyboardButton(text="📅 Период мониторинга",
                              callback_data="edit_date_range")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="my_monitorings")]
    ])


def create_edit_coefficient_keyboard() -> InlineKeyboardMarkup:
    """Создать клавиатуру редактирования коэффициентов"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🟢 Только бесплатные (0)",
                                 callback_data="edit_coeff_0_0"),
            InlineKeyboardButton(text="🟡 До x1 (0-1)",
                                 callback_data="edit_coeff_0_1")
        ],
        [
            InlineKeyboardButton(text="🟡 До x2 (0-2)",
                                 callback_data="edit_coeff_0_2"),
            InlineKeyboardButton(text="🟡 До x3 (0-3)",
                                 callback_data="edit_coeff_0_3")
        ],
        [
            InlineKeyboardButton(text="🟡 До x4 (0-4)",
                                 callback_data="edit_coeff_0_4"),
            InlineKeyboardButton(text="🟡 До x5 (0-5)",
                                 callback_data="edit_coeff_0_5")
        ],
        [
            InlineKeyboardButton(text="🟠 До x6 (0-6)",
                                 callback_data="edit_coeff_0_6"),
            InlineKeyboardButton(text="🟠 До x7 (0-7)",
                                 callback_data="edit_coeff_0_7")
        ],
        [
            InlineKeyboardButton(text="🟠 До x8 (0-8)",
                                 callback_data="edit_coeff_0_8"),
            InlineKeyboardButton(text="🟠 До x9 (0-9)",
                                 callback_data="edit_coeff_0_9")
        ],
        [
            InlineKeyboardButton(text="🟠 До x10 (0-10)",
                                 callback_data="edit_coeff_0_10"),
            InlineKeyboardButton(text="🔴 До x20 (0-20)",
                                 callback_data="edit_coeff_0_20")
        ],
        [InlineKeyboardButton(text="🌟 Любой коэффициент",
                              callback_data="edit_coeff_0_100")],
    ])


def create_edit_box_type_keyboard() -> InlineKeyboardMarkup:
    """Создать клавиатуру редактирования типа упаковки"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="📦 Короба", callback_data="edit_box_type_2")],
        [InlineKeyboardButton(text="🛒 Монопаллеты",
                              callback_data="edit_box_type_5")],
        [InlineKeyboardButton(text="🚛 Суперсейф",
                              callback_data="edit_box_type_6")],
        [InlineKeyboardButton(text="🌟 Любой тип",
                              callback_data="edit_box_type_any")],
        [InlineKeyboardButton(
            text="⬅️ Назад", callback_data="edit_monitoring_back")]
    ])


def create_edit_logistics_shoulder_keyboard() -> InlineKeyboardMarkup:
    """Создать клавиатуру редактирования логистического плеча"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="1 день", callback_data="edit_logistics_1")],
        [InlineKeyboardButton(text="2 дня", callback_data="edit_logistics_2")],
        [InlineKeyboardButton(text="3 дня", callback_data="edit_logistics_3")],
        [InlineKeyboardButton(text="4 дня", callback_data="edit_logistics_4")]
    ])


def create_edit_date_range_keyboard() -> InlineKeyboardMarkup:
    """Создать клавиатуру редактирования периода"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📅 Выбрать даты вручную",
                              callback_data="edit_select_dates")],
        [InlineKeyboardButton(text="📊 Быстрый выбор",
                              callback_data="edit_quick_period")],
        [InlineKeyboardButton(
            text="⬅️ Назад", callback_data="edit_monitoring_back")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="my_monitorings")]
    ])


def create_edit_quick_period_keyboard() -> InlineKeyboardMarkup:
    """Создать клавиатуру быстрого выбора периода"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📅 7 дней", callback_data="edit_period_7")],
        [InlineKeyboardButton(
            text="📅 14 дней", callback_data="edit_period_14")],
        [InlineKeyboardButton(
            text="📅 30 дней", callback_data="edit_period_30")],
        [InlineKeyboardButton(
            text="⬅️ Назад", callback_data="edit_date_range")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="my_monitorings")]
    ])


def create_edit_confirm_keyboard() -> InlineKeyboardMarkup:
    """Создать клавиатуру подтверждения редактирования"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Коэффициенты",
                              callback_data="edit_coefficient")],
        [InlineKeyboardButton(text="📦 Тип упаковки",
                              callback_data="edit_box_type")],
        [InlineKeyboardButton(text="🚚 Логистическое плечо",
                              callback_data="edit_logistics_shoulder")],
        [InlineKeyboardButton(text="📅 Период мониторинга",
                              callback_data="edit_date_range")],
        [InlineKeyboardButton(text="✅ Сохранить изменения",
                              callback_data="confirm_edit")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="my_monitorings")]
    ])


def create_my_monitorings_keyboard(monitorings: list) -> InlineKeyboardMarkup:
    """Создать клавиатуру для списка мониторингов"""
    keyboard_buttons = []

    for monitoring in monitorings:
        if monitoring.status == "active":
            keyboard_buttons.append([
                InlineKeyboardButton(
                    text=f"🗑️ Удалить #{monitoring.id}",
                    callback_data=f"delete_monitoring_{monitoring.id}"
                )
            ])

    # Добавляем только кнопку кабинета (убираем создание мониторинга)
    keyboard_buttons.append([
        InlineKeyboardButton(text="📊 Кабинет", callback_data="cabinet_info")
    ])

    return InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)


def create_no_monitorings_keyboard() -> InlineKeyboardMarkup:
    """Создать клавиатуру когда нет мониторингов"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Мой кабинет",
                              callback_data="cabinet_info")]
    ])


def create_monitoring_success_keyboard() -> InlineKeyboardMarkup:
    """Создать клавиатуру после успешного создания мониторинга"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Мои мониторинги",
                              callback_data="my_monitorings")],
        [InlineKeyboardButton(text="📊 Кабинет", callback_data="cabinet_info")]
    ])


def create_slot_notification_keyboard(monitoring_id: int, slot_date: str = None, warehouse_id: int = None) -> InlineKeyboardMarkup:
    """Создать клавиатуру для уведомления о найденном слоте (автобронирование происходит автоматически)"""
    keyboard = [
        [InlineKeyboardButton(text="✏️ Редактировать мониторинг",
                              callback_data=f"edit_monitoring_{monitoring_id}")],
        [InlineKeyboardButton(text="🗑️ Удалить мониторинг",
                              callback_data=f"delete_monitoring_{monitoring_id}")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def create_warehouse_error_keyboard() -> InlineKeyboardMarkup:
    """Создать клавиатуру при ошибке загрузки складов"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🏪 Обновить склады",
                              callback_data="update_warehouses")],
        [InlineKeyboardButton(text="📊 Кабинет", callback_data="cabinet_info")]
    ])


def create_delete_confirmation_keyboard(monitoring_id: int) -> InlineKeyboardMarkup:
    """Создать клавиатуру подтверждения удаления мониторинга"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Мои мониторинги",
                              callback_data="my_monitorings")],
        [InlineKeyboardButton(text="📊 Кабинет", callback_data="cabinet_info")]
    ])
