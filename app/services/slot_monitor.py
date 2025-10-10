"""Сервис мониторинга слотов в реальном времени"""

import asyncio
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional
from aiogram import Bot
from loguru import logger

from app.config.settings import settings
from app.services.wildberries_api import wb_api, WildberriesAPIError
from app.services.booking_service import get_booking_service, BookingService, BookingServiceError
from app.database.database import AsyncSessionLocal
from app.database.repositories.user_repo import UserRepository
from app.database.repositories.slot_monitoring_repo import SlotMonitoringRepository
from app.database.models import SlotMonitoring, MonitoringStatus
from app.bot.handlers.keyboards import create_slot_notification_keyboard


class SlotMonitorService:
    """Сервис мониторинга слотов"""

    def __init__(self, bot: Bot):
        self.bot = bot
        self.is_running = False
        self.monitoring_tasks: Dict[int, asyncio.Task] = {}
        # Кеш для отслеживания уведомлений (monitoring_id -> set of slot_keys)
        self.notified_slots_cache: Dict[int, set] = {}
        # Кеш для хранения лучших слотов (monitoring_id -> best_slot)
        self.best_slots_cache: Dict[int, Dict[str, Any]] = {}
        # Кеш для отслеживания попыток бронирования (monitoring_id -> attempt_count)
        self.booking_attempts_cache: Dict[int, int] = {}

    async def start_monitoring(self):
        """Запустить мониторинг всех активных заданий"""
        if self.is_running:
            logger.warning("Slot monitoring is already running")
            return

        self.is_running = True
        logger.info("Starting slot monitoring service...")

        # Запускаем основной цикл мониторинга
        asyncio.create_task(self._monitoring_loop())

    async def stop_monitoring(self):
        """Остановить мониторинг"""
        logger.info("Stopping slot monitoring service...")
        self.is_running = False

        # Останавливаем все задачи мониторинга
        for task in self.monitoring_tasks.values():
            task.cancel()

        self.monitoring_tasks.clear()
        # Очищаем весь кеш уведомлений, лучших слотов и попыток бронирования
        self.notified_slots_cache.clear()
        self.best_slots_cache.clear()
        self.booking_attempts_cache.clear()

    async def _stop_monitoring_for_user(self, monitoring_id: int):
        """Остановить и удалить мониторинг для конкретного пользователя после успешного бронирования"""
        try:
            logger.info(f"🛑 Stopping and deleting monitoring {monitoring_id} after successful booking")
            
            # Сначала помечаем мониторинг как удаляемый в кеше (предотвращаем перезапуск)
            self.booking_attempts_cache[monitoring_id] = -1  # Специальное значение для удаления
            
            # Останавливаем задачу мониторинга
            if monitoring_id in self.monitoring_tasks:
                task = self.monitoring_tasks.pop(monitoring_id)
                task.cancel()
                logger.info(f"✅ Stopped monitoring task for monitoring {monitoring_id}")
            
            # Очищаем кеш для этого мониторинга
            if monitoring_id in self.notified_slots_cache:
                del self.notified_slots_cache[monitoring_id]
            if monitoring_id in self.best_slots_cache:
                del self.best_slots_cache[monitoring_id]
            if monitoring_id in self.booking_attempts_cache:
                del self.booking_attempts_cache[monitoring_id]
            logger.info(f"✅ Cleared cache for monitoring {monitoring_id}")
            
            # Удаляем мониторинг из базы данных после успешного бронирования
            async with AsyncSessionLocal() as session:
                slot_repo = SlotMonitoringRepository(session)
                user_repo = UserRepository(session)
                
                # Получаем мониторинг для получения user_id
                monitoring = await slot_repo.get_monitoring_by_id(monitoring_id)
                if monitoring:
                    # Получаем пользователя по telegram_id из мониторинга
                    user = await user_repo.get_by_telegram_id(monitoring.user.telegram_id)
                    if user:
                        success = await slot_repo.delete_monitoring(monitoring_id, user)
                        if success:
                            logger.info(f"✅ Successfully deleted monitoring {monitoring_id} from database")
                        else:
                            logger.error(f"❌ Failed to delete monitoring {monitoring_id} from database")
                    else:
                        logger.error(f"❌ User not found for monitoring {monitoring_id}")
                else:
                    logger.warning(f"⚠️ Monitoring {monitoring_id} not found in database (may have been already deleted)")
                
        except Exception as e:
            logger.error(f"❌ Error stopping and deleting monitoring {monitoring_id}: {e}")

    async def _monitoring_loop(self):
        """Основной цикл мониторинга"""
        while self.is_running:
            try:
                # Получаем все активные мониторинги
                async with AsyncSessionLocal() as session:
                    slot_repo = SlotMonitoringRepository(session)
                    active_monitorings = await slot_repo.get_all_active_monitorings()

                # Запускаем/обновляем задачи мониторинга
                current_monitoring_ids = set(m.id for m in active_monitorings)
                running_task_ids = set(self.monitoring_tasks.keys())

                # Останавливаем задачи для неактивных мониторингов
                for task_id in running_task_ids - current_monitoring_ids:
                    task = self.monitoring_tasks.pop(task_id, None)
                    if task:
                        task.cancel()
                        # Очищаем кеш для остановленного мониторинга
                        if task_id in self.notified_slots_cache:
                            del self.notified_slots_cache[task_id]
                        if task_id in self.best_slots_cache:
                            del self.best_slots_cache[task_id]
                        logger.info(
                            f"Stopped monitoring task for monitoring {task_id}")

                # Запускаем новые задачи
                for monitoring in active_monitorings:
                    if monitoring.id not in self.monitoring_tasks:
                        # Дополнительная проверка: убеждаемся, что мониторинг все еще активен
                        async with AsyncSessionLocal() as session:
                            slot_repo = SlotMonitoringRepository(session)
                            current_monitoring = await slot_repo.get_monitoring_by_id(monitoring.id)
                            
                            if current_monitoring and current_monitoring.status == MonitoringStatus.ACTIVE.value:
                                # Дополнительная проверка: убеждаемся, что мониторинг не в процессе удаления
                                if (monitoring.id not in self.booking_attempts_cache or 
                                    self.booking_attempts_cache.get(monitoring.id, 0) != -1):
                                    task = asyncio.create_task(
                                        self._monitor_slots_for_user(monitoring)
                                    )
                                    self.monitoring_tasks[monitoring.id] = task
                                    logger.info(
                                        f"Started monitoring task for monitoring {monitoring.id}")
                                else:
                                    logger.info(
                                        f"Monitoring {monitoring.id} is being deleted, skipping task creation")
                            else:
                                logger.info(
                                    f"Monitoring {monitoring.id} is no longer active, skipping task creation")

                # Ждем перед следующей проверкой
                await asyncio.sleep(30)  # Проверяем каждые 30 секунд

            except Exception as e:
                logger.error(f"Error in monitoring loop: {e}")
                await asyncio.sleep(60)  # Ждем дольше при ошибке

    async def _monitor_slots_for_user(self, monitoring: SlotMonitoring):
        """Мониторинг слотов для конкретного пользователя"""
        logger.info(
            f"Starting slot monitoring for user {monitoring.user.telegram_id}, monitoring {monitoring.id}")

        while self.is_running:
            try:
                # Проверяем, что мониторинг все еще активен
                async with AsyncSessionLocal() as session:
                    slot_repo = SlotMonitoringRepository(session)
                    current_monitoring = await slot_repo.get_monitoring_by_id(monitoring.id)
                    
                    if not current_monitoring or current_monitoring.status != MonitoringStatus.ACTIVE.value:
                        logger.info(f"🛑 Monitoring {monitoring.id} is no longer active, stopping task")
                        break
                
                # Получаем токен пользователя
                async with AsyncSessionLocal() as session:
                    user_repo = UserRepository(session)
                    user = await user_repo.get_by_telegram_id(monitoring.user.telegram_id)

                    if not user or not user.has_wb_token():
                        logger.warning(
                            f"User {monitoring.user.telegram_id} has no valid token")
                        break

                    wb_token = await user_repo.get_wb_token(user)
                    if not wb_token:
                        logger.warning(
                            f"Failed to decrypt token for user {monitoring.user.telegram_id}")
                        break

                # Проверяем слоты для каждого склада
                await self._check_slots_for_monitoring(monitoring, wb_token)

                # Обновляем время последней проверки
                async with AsyncSessionLocal() as session:
                    slot_repo = SlotMonitoringRepository(session)
                    await slot_repo.update_last_check(monitoring.id)

                # Ждем интервал проверки
                await asyncio.sleep(settings.SLOT_CHECK_INTERVAL)

            except asyncio.CancelledError:
                logger.info(
                    f"Monitoring task cancelled for monitoring {monitoring.id}")
                break
            except Exception as e:
                logger.error(
                    f"Error monitoring slots for monitoring {monitoring.id}: {e}")
                await asyncio.sleep(5)  # Короткая пауза при ошибке

    async def _check_slots_for_monitoring(self, monitoring: SlotMonitoring, wb_token: str):
        """Проверить слоты для конкретного мониторинга"""
        try:
            logger.debug(
                f"Checking slots for monitoring {monitoring.id}: warehouses={monitoring.warehouse_ids}")

            async with wb_api:
                # Получаем коэффициенты приемки для выбранных складов
                coefficients = await wb_api.get_acceptance_coefficients(
                    api_token=wb_token,
                    warehouse_ids=monitoring.warehouse_ids
                )

                logger.debug(
                    f"Received {len(coefficients)} coefficients for monitoring {monitoring.id}")

                # Фильтруем коэффициенты по критериям мониторинга
                suitable_slots = self._filter_suitable_coefficients(
                    coefficients, monitoring)

                # Обрабатываем слоты по складам
                if suitable_slots:
                    await self._process_slots_by_warehouse(monitoring, suitable_slots)

        except WildberriesAPIError as e:
            error_message = str(e)
            if "лимит запросов" in error_message.lower() or "rate limit" in error_message.lower():
                logger.warning(
                    f"Rate limit hit for monitoring {monitoring.id}. Will retry after delay.")
                # Увеличиваем интервал при превышении лимита
                await asyncio.sleep(120)  # Ждем 2 минуты при превышении лимита
            else:
                logger.warning(
                    f"WB API error for monitoring {monitoring.id}: {e}")
        except Exception as e:
            logger.error(
                f"Error checking slots for monitoring {monitoring.id}: {e}")

    def _filter_suitable_coefficients(self, coefficients: List[Dict[str, Any]], monitoring: SlotMonitoring) -> List[Dict[str, Any]]:
        """Фильтровать коэффициенты приемки по критериям мониторинга"""
        suitable_slots = []

        # Рассчитываем минимальную дату с учетом логистического плеча
        from datetime import datetime, timedelta
        
        # Если пользователь выбрал период вручную, используем date_from как есть (уже с плечом)
        if monitoring.date_from:
            min_slot_date = monitoring.date_from.date()
            logger.debug(f"Using monitoring date_from (already with logistics shoulder): {min_slot_date}")
        else:
            # Иначе считаем от даты создания мониторинга + логистическое плечо
            base_date = monitoring.created_at.date()
            min_slot_date = base_date + timedelta(days=monitoring.logistics_shoulder)
            logger.debug(f"Using monitoring created date + logistics shoulder: {base_date} + {monitoring.logistics_shoulder} days = {min_slot_date}")

        # Получаем максимальную дату из настроек мониторинга
        max_slot_date = None
        if monitoring.date_to:
            max_slot_date = monitoring.date_to.date()

        logger.debug(
            f"Filtering slots for monitoring {monitoring.id}: logistics_shoulder={monitoring.logistics_shoulder} days, min_slot_date={min_slot_date}, max_slot_date={max_slot_date}, selected_warehouses={monitoring.warehouse_ids}")

        for coeff_data in coefficients:
            try:
                # Извлекаем коэффициент приемки
                coefficient = float(coeff_data.get('coefficient', -1))

                # Проверяем что коэффициент в допустимом диапазоне (0-20)
                if coefficient < 0 or coefficient > 20:
                    continue

                # Проверяем что разгрузка разрешена
                if not coeff_data.get('allowUnload', False):
                    continue

                # Проверяем диапазон коэффициентов мониторинга
                if not (monitoring.coefficient_min <= coefficient <= monitoring.coefficient_max):
                    continue

                # Проверяем, что склад входит в список выбранных складов мониторинга
                warehouse_id = coeff_data.get('warehouseID')
                if warehouse_id not in monitoring.warehouse_ids:
                    logger.debug(
                        f"Skipping slot for warehouse {warehouse_id}: not in selected warehouses {monitoring.warehouse_ids}")
                    continue

                # Проверяем логистическое плечо - дата слота должна быть не раньше минимальной даты
                slot_date_str = coeff_data.get('date', '')
                if slot_date_str:
                    try:
                        # Парсим дату из ISO формата
                        if 'T' in slot_date_str:
                            slot_date = datetime.fromisoformat(
                                slot_date_str.replace('Z', '+00:00')).date()
                        else:
                            slot_date = datetime.strptime(
                                slot_date_str, '%Y-%m-%d').date()

                        # Проверяем, что дата слота не раньше минимальной даты с учетом логистического плеча
                        if slot_date < min_slot_date:
                            logger.debug(
                                f"Skipping slot {slot_date} for monitoring {monitoring.id}: too early (logistics shoulder: {monitoring.logistics_shoulder} days)")
                            continue

                        # Проверяем, что дата слота не позже максимальной даты мониторинга
                        if max_slot_date and slot_date > max_slot_date:
                            logger.debug(
                                f"Skipping slot {slot_date} for monitoring {monitoring.id}: too late (max date: {max_slot_date})")
                            continue

                        # Проверяем, что дата не входит в список неудачных попыток бронирования
                        failed_dates = monitoring.failed_booking_dates or []
                        slot_date_str_check = slot_date.strftime('%Y-%m-%d')
                        if slot_date_str_check in failed_dates:
                            logger.debug(
                                f"Skipping slot {slot_date} for monitoring {monitoring.id}: date in failed booking dates")
                            continue

                    except (ValueError, TypeError) as e:
                        logger.warning(
                            f"Error parsing slot date: {e}, date: {slot_date_str}")
                        continue

                # Проверяем тип упаковки, если он указан в мониторинге
                if monitoring.box_type_id is not None:
                    slot_box_type_id = coeff_data.get('boxTypeID')
                    if slot_box_type_id != monitoring.box_type_id:
                        logger.debug(
                            f"Skipping slot for monitoring {monitoring.id}: box type mismatch (expected {monitoring.box_type_id}, got {slot_box_type_id})")
                        continue

                # Создаем объект слота из данных коэффициента
                slot_data = {
                    'warehouseID': coeff_data.get('warehouseID'),
                    'warehouseName': coeff_data.get('warehouseName'),
                    'date': coeff_data.get('date'),
                    'coefficient': coefficient,
                    'boxTypeName': coeff_data.get('boxTypeName'),
                    'boxTypeID': coeff_data.get('boxTypeID'),
                    'allowUnload': coeff_data.get('allowUnload'),
                    'available': True  # Если коэффициент 0 или 1 и allowUnload=true, то доступен
                }

                suitable_slots.append(slot_data)

            except (ValueError, TypeError) as e:
                logger.warning(
                    f"Error parsing coefficient data: {e}, data: {coeff_data}")
                continue

        return suitable_slots

    def _is_better_slot(self, new_slot: Dict[str, Any], current_best_slot: Dict[str, Any], monitoring: SlotMonitoring) -> bool:
        """Определить, является ли новый слот лучше текущего лучшего"""
        if not current_best_slot:
            return True

        new_coefficient = float(new_slot.get('coefficient', 999))
        current_coefficient = float(current_best_slot.get('coefficient', 999))

        # Сначала сравниваем по коэффициенту (чем меньше, тем лучше)
        if new_coefficient < current_coefficient:
            return True
        elif new_coefficient > current_coefficient:
            return False

        # Если коэффициенты равны, сравниваем по дате (чем ближе к дате создания мониторинга, тем лучше)
        try:
            new_date_str = new_slot.get('date', '')
            current_date_str = current_best_slot.get('date', '')

            if 'T' in new_date_str:
                new_date = datetime.fromisoformat(
                    new_date_str.replace('Z', '+00:00')).date()
            else:
                new_date = datetime.strptime(new_date_str, '%Y-%m-%d').date()

            if 'T' in current_date_str:
                current_date = datetime.fromisoformat(
                    current_date_str.replace('Z', '+00:00')).date()
            else:
                current_date = datetime.strptime(
                    current_date_str, '%Y-%m-%d').date()

            # Дата начала поиска слотов (используем ту же логику, что и в фильтрации)
            if monitoring.date_from:
                monitoring_start_date = monitoring.date_from.date()
            else:
                monitoring_start_date = monitoring.created_at.date() + timedelta(days=monitoring.logistics_shoulder)

            # Расстояние от даты создания мониторинга
            new_distance = abs((new_date - monitoring_start_date).days)
            current_distance = abs((current_date - monitoring_start_date).days)

            # Чем меньше расстояние, тем лучше
            return new_distance < current_distance

        except (ValueError, TypeError) as e:
            logger.warning(f"Error comparing slot dates: {e}")
            return False

    async def _process_slots_by_warehouse(self, monitoring: SlotMonitoring, slots: List[Dict[str, Any]]):
        """Обработать слоты, группируя их по складам"""
        if not slots:
            return

        try:
            # Группируем слоты по складам
            warehouse_slots = {}
            for slot in slots:
                warehouse_id = slot.get('warehouseID')
                if warehouse_id not in warehouse_slots:
                    warehouse_slots[warehouse_id] = []
                warehouse_slots[warehouse_id].append(slot)

            logger.debug(
                f"Processing slots for monitoring {monitoring.id}: found slots for {len(warehouse_slots)} warehouses")

            # Обрабатываем каждый склад отдельно
            for warehouse_id, warehouse_slot_list in warehouse_slots.items():
                # Находим лучший слот для этого склада
                best_slot_for_warehouse = None
                for slot in warehouse_slot_list:
                    if self._is_better_slot(slot, best_slot_for_warehouse, monitoring):
                        best_slot_for_warehouse = slot

                if best_slot_for_warehouse:
                    # Проверяем, не отправляли ли мы уже уведомление для этого склада
                    cache_key = f"{monitoring.id}_{warehouse_id}"
                    current_best = self.best_slots_cache.get(cache_key)

                    # Если нашли слот лучше текущего лучшего для этого склада
                    if self._is_better_slot(best_slot_for_warehouse, current_best, monitoring):
                        # Обновляем кэш для этого склада
                        self.best_slots_cache[cache_key] = best_slot_for_warehouse

                        # Отправляем уведомление
                        await self._send_slot_notification(
                            monitoring=monitoring,
                            warehouse_id=best_slot_for_warehouse.get(
                                'warehouseID'),
                            warehouse_name=best_slot_for_warehouse.get(
                                'warehouseName', f'Склад {best_slot_for_warehouse.get("warehouseID")}'),
                            slot_date=datetime.fromisoformat(best_slot_for_warehouse.get('date', '').replace(
                                'Z', '+00:00')) if 'T' in best_slot_for_warehouse.get('date', '') else datetime.strptime(best_slot_for_warehouse.get('date', ''), '%Y-%m-%d'),
                            coefficient=float(
                                best_slot_for_warehouse.get('coefficient', 0)),
                            slot_info=best_slot_for_warehouse
                        )

                        logger.info(
                            f"Sent notification for warehouse {warehouse_id} (monitoring {monitoring.id}): date {best_slot_for_warehouse.get('date')}, coefficient {best_slot_for_warehouse.get('coefficient')}")
                    else:
                        logger.debug(
                            f"No better slot found for warehouse {warehouse_id} (monitoring {monitoring.id})")

        except Exception as e:
            logger.error(
                f"Error processing slots by warehouse for monitoring {monitoring.id}: {e}")

    async def _process_best_slot(self, monitoring: SlotMonitoring, slots: List[Dict[str, Any]]):
        """Обработать найденные слоты и найти лучший"""
        if not slots:
            return

        try:
            # Находим лучший слот среди всех найденных
            best_slot = None
            for slot in slots:
                if self._is_better_slot(slot, best_slot, monitoring):
                    best_slot = slot

            if not best_slot:
                return

            # Получаем текущий лучший слот из кэша
            current_best = self.best_slots_cache.get(monitoring.id)

            logger.debug(
                f"Processing best slot for monitoring {monitoring.id}:")
            logger.debug(
                f"  Found slot: {best_slot.get('date')} | {best_slot.get('coefficient')} | {best_slot.get('warehouseName')}")
            logger.debug(
                f"  Current best: {current_best.get('date') if current_best else 'None'} | {current_best.get('coefficient') if current_best else 'None'} | {current_best.get('warehouseName') if current_best else 'None'}")

            # Проверяем, является ли найденный слот лучше текущего лучшего
            if self._is_better_slot(best_slot, current_best, monitoring):
                # Обновляем кэш лучшего слота
                self.best_slots_cache[monitoring.id] = best_slot

                # Отправляем уведомление о новом лучшем слоте
                await self._send_slot_notification(
                    monitoring=monitoring,
                    warehouse_id=best_slot.get('warehouseID'),
                    warehouse_name=best_slot.get(
                        'warehouseName', f'Склад {best_slot.get("warehouseID")}'),
                    slot_date=datetime.fromisoformat(best_slot.get('date', '').replace(
                        'Z', '+00:00')) if 'T' in best_slot.get('date', '') else datetime.strptime(best_slot.get('date', ''), '%Y-%m-%d'),
                    coefficient=float(best_slot.get('coefficient', 0)),
                    slot_info=best_slot
                )

                logger.info(
                    f"Sent notification for new best slot (monitoring {monitoring.id}): warehouse {best_slot.get('warehouseID')}, date {best_slot.get('date')}, coefficient {best_slot.get('coefficient')}")
            else:
                logger.debug(
                    f"No better slot found for monitoring {monitoring.id}")

        except Exception as e:
            logger.error(
                f"Error processing best slot for monitoring {monitoring.id}: {e}")

    async def _process_found_slots(self, monitoring: SlotMonitoring, warehouse_id: int, slots: List[Dict[str, Any]]):
        """Обработать найденные слоты и отправить уведомления"""
        if not slots:
            return

        try:
            # Инициализируем кеш для этого мониторинга, если его нет
            if monitoring.id not in self.notified_slots_cache:
                self.notified_slots_cache[monitoring.id] = set()

            notified_cache = self.notified_slots_cache[monitoring.id]

            for slot in slots:
                # Извлекаем данные из коэффициента приемки
                slot_date_str = slot.get('date', '')
                if not slot_date_str:
                    continue

                try:
                    # Парсим дату из ISO формата
                    if 'T' in slot_date_str:
                        slot_date = datetime.fromisoformat(
                            slot_date_str.replace('Z', '+00:00'))
                    else:
                        slot_date = datetime.strptime(
                            slot_date_str, '%Y-%m-%d')
                except (ValueError, TypeError):
                    logger.warning(f"Invalid date format: {slot_date_str}")
                    continue

                # Создаем уникальный ключ слота для кеша
                slot_coefficient = float(slot.get('coefficient', 0))
                slot_key = f"{warehouse_id}_{slot_date.date()}_{slot_coefficient}"

                # Проверяем, было ли уже отправлено уведомление об этом слоте
                if slot_key not in notified_cache:
                    # Отправляем уведомление пользователю
                    await self._send_slot_notification(
                        monitoring=monitoring,
                        warehouse_id=warehouse_id,
                        warehouse_name=slot.get(
                            'warehouseName', f'Склад {warehouse_id}'),
                        slot_date=slot_date,
                        coefficient=slot_coefficient,
                        slot_info=slot
                    )

                    # Добавляем в кеш, чтобы не отправлять повторно
                    notified_cache.add(slot_key)

                    logger.info(
                        f"Sent notification for new slot (monitoring {monitoring.id}): {slot_key}")
                else:
                    # Уведомление уже было отправлено, пропускаем
                    logger.debug(
                        f"Notification already sent for slot (monitoring {monitoring.id}): {slot_key}")

        except Exception as e:
            logger.error(
                f"Error processing found slots for monitoring {monitoring.id}: {e}")

    async def _send_slot_notification(
        self,
        monitoring: SlotMonitoring,
        warehouse_id: int,
        warehouse_name: str,
        slot_date: datetime,
        coefficient: float,
        slot_info: Dict[str, Any]
    ):
        """Отправить уведомление о найденном слоте и автоматически забронировать его"""
        try:
            # Формируем текст уведомления
            coeff_text = "🟢 Бесплатная приемка" if coefficient == 0 else "🟡 Платная приемка"

            # Получаем информацию о типе упаковки
            box_type_name = slot_info.get('boxTypeName', 'Неизвестно')
            box_type_id = slot_info.get('boxTypeID', 'N/A')

            # Сначала отправляем уведомление о начале бронирования
            initial_notification_text = f"""
🤖 <b>Найден подходящий слот! Начинаю автобронирование...</b>

<b>📊 Мониторинг #{monitoring.id}</b>
🏪 <b>Склад:</b> {warehouse_name} (ID: {warehouse_id})
📅 <b>Дата:</b> {slot_date.strftime('%d.%m.%Y')}
💰 <b>Коэффициент:</b> {coefficient} ({coeff_text})
📦 <b>Тип упаковки:</b> {box_type_name} (ID: {box_type_id})

⏳ <b>Автоматически бронирую слот...</b>
        """

            # Отправляем начальное уведомление
            initial_message = await self.bot.send_message(
                chat_id=monitoring.user.telegram_id,
                text=initial_notification_text,
                parse_mode="HTML"
            )

            logger.info(f"Found suitable slot for monitoring {monitoring.id}, starting auto-booking...")

            # Получаем данные сессии пользователя
            async with AsyncSessionLocal() as session:
                user_repo = UserRepository(session)
                session_data = await user_repo.get_phone_auth_session(monitoring.user)
            
            # Проверяем, есть ли у пользователя сохраненная сессия
            if not session_data:
                error_text = f"""
❌ <b>Ошибка автобронирования</b>

<b>📊 Мониторинг #{monitoring.id}</b>
🏪 <b>Склад:</b> {warehouse_name} (ID: {warehouse_id})
📅 <b>Дата:</b> {slot_date.strftime('%d.%m.%Y')}

<b>💬 Причина:</b> Сессия не найдена. Необходимо авторизоваться в кабинете Wildberries.

<a href="https://t.me/{self.bot.username}?start=auth">🔑 Авторизоваться</a>
                """
                
                await initial_message.edit_text(
                    text=error_text,
                    parse_mode="HTML",
                    reply_markup=create_slot_notification_keyboard(monitoring.id)
                )
                return

            # Проверяем наличие номера заказа
            if not monitoring.order_number:
                error_text = f"""
❌ <b>Ошибка автобронирования</b>

<b>📊 Мониторинг #{monitoring.id}</b>
🏪 <b>Склад:</b> {warehouse_name} (ID: {warehouse_id})
📅 <b>Дата:</b> {slot_date.strftime('%d.%m.%Y')}

<b>💬 Причина:</b> Номер заказа не найден в мониторинге.
                """
                
                await initial_message.edit_text(
                    text=error_text,
                    parse_mode="HTML",
                    reply_markup=create_slot_notification_keyboard(monitoring.id)
                )
                return

            # Запускаем автобронирование с повторными попытками
            await self._attempt_booking_with_retry(
                monitoring=monitoring,
                session_data=session_data,
                slot_date=slot_date,
                warehouse_id=warehouse_id,
                warehouse_name=warehouse_name,
                coefficient=coefficient,
                coeff_text=coeff_text,
                initial_message=initial_message
            )

        except Exception as e:
            logger.error(f"Error in auto-booking notification: {e}")
            # Отправляем уведомление об ошибке, если не удалось даже отправить сообщение
            try:
                await self.bot.send_message(
                    chat_id=monitoring.user.telegram_id,
                    text=f"❌ <b>Ошибка автобронирования</b>\n\n💬 {str(e).replace('<', '&lt;').replace('>', '&gt;')}",
                    parse_mode="HTML"
                )
            except:
                pass

    async def _attempt_booking_with_retry(
        self,
        monitoring: SlotMonitoring,
        session_data: Dict[str, Any],
        slot_date: datetime,
        warehouse_id: int,
        warehouse_name: str,
        coefficient: float,
        coeff_text: str,
        initial_message
    ):
        """Попытка бронирования с повторными попытками при ошибках"""
        max_attempts = 3
        attempt = self.booking_attempts_cache.get(monitoring.id, 0) + 1
        
        # Обновляем счетчик попыток
        self.booking_attempts_cache[monitoring.id] = attempt
        
        logger.info(f"🔄 Booking attempt {attempt}/{max_attempts} for monitoring {monitoring.id}")
        
        try:
            from app.services.wb_web_auth import get_wb_auth_service
            from app.services.booking_service import BookingService, BookingServiceError
            
            auth_service = get_wb_auth_service(user_id=monitoring.user.telegram_id)
            booking_service = BookingService(auth_service)
            
            success, message = await booking_service.book_slot(
                session_data=session_data,
                order_number=monitoring.order_number,
                target_date=slot_date,
                target_warehouse_id=warehouse_id
            )
            
            if success:
                # Успешное бронирование
                success_text = f"""
✅ <b>Автобронирование успешно!</b>

<b>📊 Мониторинг #{monitoring.id}</b>
🏪 <b>Склад:</b> {warehouse_name} (ID: {warehouse_id})
📅 <b>Дата:</b> {slot_date.strftime('%d.%m.%Y')}
📦 <b>Заказ:</b> {monitoring.order_number}
💰 <b>Коэффициент:</b> {coefficient} ({coeff_text})

<b>💬 {message}</b>

🎉 <b>Слот успешно забронирован!</b>
                """
                
                await initial_message.edit_text(
                    text=success_text,
                    parse_mode="HTML",
                    reply_markup=create_slot_notification_keyboard(monitoring.id)
                )
                
                logger.info(f"Successfully auto-booked slot for monitoring {monitoring.id} on attempt {attempt}")
                
                # Останавливаем мониторинг при успешном бронировании
                await self._stop_monitoring_for_user(monitoring.id)
                return
                
            else:
                # Неуспешное бронирование - не повторяем
                error_text = f"""
❌ <b>Ошибка автобронирования</b>

<b>📊 Мониторинг #{monitoring.id}</b>
🏪 <b>Склад:</b> {warehouse_name} (ID: {warehouse_id})
📅 <b>Дата:</b> {slot_date.strftime('%d.%m.%Y')}
📦 <b>Заказ:</b> {monitoring.order_number}

<b>💬 {message}</b>

🔄 <b>Перехожу к следующей дате и продолжаю поиск...</b>
                """
                
                await initial_message.edit_text(
                    text=error_text,
                    parse_mode="HTML",
                    reply_markup=create_slot_notification_keyboard(monitoring.id)
                )
                
                logger.info(f"Auto-booking failed for monitoring {monitoring.id}, continuing search...")
                
                # Добавляем дату в список неудачных попыток бронирования
                await self._add_failed_booking_date(monitoring.id, slot_date)
                
                # Очищаем кеш и сбрасываем счетчик попыток
                self._clear_slot_cache(monitoring.id, warehouse_id, slot_date, coefficient)
                return
                
        except BookingServiceError as e:
            error_message = str(e)
            
            # Проверяем, является ли это ошибкой stale element reference
            is_stale_error = "stale element reference" in error_message.lower()
            is_retryable_error = any(keyword in error_message.lower() for keyword in [
                "stale element reference",
                "timeout",
                "element not found",
                "element not clickable"
            ])
            
            if is_retryable_error and attempt < max_attempts:
                # Повторяем попытку
                logger.warning(f"🔄 Retryable error on attempt {attempt}: {error_message}")
                
                retry_text = f"""
🔄 <b>Повторная попытка бронирования</b>

<b>📊 Мониторинг #{monitoring.id}</b>
🏪 <b>Склад:</b> {warehouse_name} (ID: {warehouse_id})
📅 <b>Дата:</b> {slot_date.strftime('%d.%m.%Y')}
📦 <b>Заказ:</b> {monitoring.order_number}

<b>💬 Попытка {attempt + 1}/{max_attempts}</b>
⏳ <b>Повторяю бронирование через 3 секунды...</b>
                """
                
                await initial_message.edit_text(
                    text=retry_text,
                    parse_mode="HTML",
                    reply_markup=create_slot_notification_keyboard(monitoring.id)
                )
                
                # Ждем перед повторной попыткой
                await asyncio.sleep(3)
                
                # Рекурсивно вызываем метод снова
                await self._attempt_booking_with_retry(
                    monitoring=monitoring,
                    session_data=session_data,
                    slot_date=slot_date,
                    warehouse_id=warehouse_id,
                    warehouse_name=warehouse_name,
                    coefficient=coefficient,
                    coeff_text=coeff_text,
                    initial_message=initial_message
                )
                return
                
            else:
                # Не повторяем - либо не критическая ошибка, либо исчерпаны попытки
                if attempt >= max_attempts:
                    logger.error(f"❌ Max attempts ({max_attempts}) reached for monitoring {monitoring.id}")
                    error_text = f"""
❌ <b>Превышено количество попыток</b>

<b>📊 Мониторинг #{monitoring.id}</b>
🏪 <b>Склад:</b> {warehouse_name} (ID: {warehouse_id})
📅 <b>Дата:</b> {slot_date.strftime('%d.%m.%Y')}
📦 <b>Заказ:</b> {monitoring.order_number}

<b>💬 Попыток: {attempt}/{max_attempts}</b>
<b>💬 Ошибка: {error_message.replace('<', '&lt;').replace('>', '&gt;')}</b>

🔄 <b>Перехожу к следующей дате и продолжаю поиск...</b>
                    """
                else:
                    error_text = f"""
❌ <b>Ошибка автобронирования</b>

<b>📊 Мониторинг #{monitoring.id}</b>
🏪 <b>Склад:</b> {warehouse_name} (ID: {warehouse_id})
📅 <b>Дата:</b> {slot_date.strftime('%d.%m.%Y')}
📦 <b>Заказ:</b> {monitoring.order_number}

<b>💬 {error_message.replace('<', '&lt;').replace('>', '&gt;')}</b>

🔄 <b>Перехожу к следующей дате и продолжаю поиск...</b>
                    """
                
                await initial_message.edit_text(
                    text=error_text,
                    parse_mode="HTML",
                    reply_markup=create_slot_notification_keyboard(monitoring.id)
                )
                
                logger.info(f"BookingServiceError for monitoring {monitoring.id}, continuing search...")
                
                # Добавляем дату в список неудачных попыток бронирования
                await self._add_failed_booking_date(monitoring.id, slot_date)
                
                # Очищаем кеш и сбрасываем счетчик попыток
                self._clear_slot_cache(monitoring.id, warehouse_id, slot_date, coefficient)
                return
                
        except Exception as e:
            logger.error(f"Unexpected error during auto-booking for monitoring {monitoring.id}: {e}")
            error_text = f"""
❌ <b>Неожиданная ошибка автобронирования</b>

<b>📊 Мониторинг #{monitoring.id}</b>
🏪 <b>Склад:</b> {warehouse_name} (ID: {warehouse_id})
📅 <b>Дата:</b> {slot_date.strftime('%d.%m.%Y')}
📦 <b>Заказ:</b> {monitoring.order_number}

<b>💬 Попробуйте позже или обратитесь в поддержку.</b>

🔄 <b>Перехожу к следующей дате и продолжаю поиск...</b>
            """
            
            await initial_message.edit_text(
                text=error_text,
                parse_mode="HTML",
                reply_markup=create_slot_notification_keyboard(monitoring.id)
            )
            
            logger.info(f"Unexpected error for monitoring {monitoring.id}, continuing search...")
            
            # Добавляем дату в список неудачных попыток бронирования
            await self._add_failed_booking_date(monitoring.id, slot_date)
            
            # Очищаем кеш и сбрасываем счетчик попыток
            self._clear_slot_cache(monitoring.id, warehouse_id, slot_date, coefficient)
            return
    
    def _clear_slot_cache(self, monitoring_id: int, warehouse_id: int, slot_date: datetime, coefficient: float):
        """Очистить кеш для конкретного слота"""
        # Очищаем кеш уведомлений для этого слота
        slot_key = f"{warehouse_id}_{slot_date.strftime('%Y-%m-%d')}_{coefficient}"
        if monitoring_id in self.notified_slots_cache:
            self.notified_slots_cache[monitoring_id].discard(slot_key)
        
        # Очищаем кеш лучших слотов
        if monitoring_id in self.best_slots_cache:
            del self.best_slots_cache[monitoring_id]
        
        # Сбрасываем счетчик попыток
        if monitoring_id in self.booking_attempts_cache:
            del self.booking_attempts_cache[monitoring_id]
        
        logger.info(f"Cleared cache for monitoring {monitoring_id} to search for better slots")

    def clear_monitoring_cache(self, monitoring_id: int):
        """Очистить кэш для конкретного мониторинга"""
        # Очищаем кэш лучших слотов для всех складов мониторинга
        keys_to_remove = []
        for key in self.best_slots_cache.keys():
            if key.startswith(f"{monitoring_id}_") or key == str(monitoring_id):
                keys_to_remove.append(key)

        for key in keys_to_remove:
            del self.best_slots_cache[key]
            logger.info(
                f"Cleared best slots cache for monitoring {monitoring_id} (key: {key})")

        if monitoring_id in self.notified_slots_cache:
            del self.notified_slots_cache[monitoring_id]
            logger.info(
                f"Cleared notified slots cache for monitoring {monitoring_id}")
        
        if monitoring_id in self.booking_attempts_cache:
            del self.booking_attempts_cache[monitoring_id]
            logger.info(
                f"Cleared booking attempts cache for monitoring {monitoring_id}")

    async def _add_failed_booking_date(self, monitoring_id: int, failed_date: datetime):
        """Добавить дату в список неудачных попыток бронирования"""
        try:
            from app.database.database import AsyncSessionLocal
            from app.database.repositories.slot_monitoring_repo import SlotMonitoringRepository
            
            async with AsyncSessionLocal() as session:
                slot_repo = SlotMonitoringRepository(session)
                success = await slot_repo.add_failed_booking_date(monitoring_id, failed_date)
                
                if success:
                    logger.info(f"Added failed booking date {failed_date.date()} for monitoring {monitoring_id}")
                else:
                    logger.error(f"Failed to add failed booking date for monitoring {monitoring_id}")
                    
        except Exception as e:
            logger.error(f"Error adding failed booking date for monitoring {monitoring_id}: {e}")


# Глобальный экземпляр сервиса мониторинга
slot_monitor_service: Optional[SlotMonitorService] = None


def get_slot_monitor_service(bot: Bot) -> SlotMonitorService:
    """Получить экземпляр сервиса мониторинга"""
    global slot_monitor_service
    if slot_monitor_service is None:
        slot_monitor_service = SlotMonitorService(bot)
    return slot_monitor_service
