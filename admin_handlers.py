import asyncio
import io
import logging
from typing import Dict, Any
from datetime import datetime, timedelta

from aiogram import Router, types, F, Bot
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

from config import Config
from sheets import sheets_manager
from utils import parse_date, verify_qr_token, is_within_checkin_window, generate_qr_token
from keyboards import get_main_keyboard, get_admin_keyboard

# Попробуем импортировать библиотеки для распознавания QR-кодов
try:
    from PIL import Image
    import pyzbar.pyzbar as pyzbar

    QR_SUPPORT = True
except ImportError:
    QR_SUPPORT = False
    logging.warning("Библиотеки для распознавания QR-кодов не установлены. Установите: pip install pyzbar pillow")

logger = logging.getLogger(__name__)
router = Router()


class AdminStates(StatesGroup):
    waiting_for_event_post = State()


def is_admin(user_id):
    """Проверка прав администратора"""
    return str(user_id) in Config.ADMIN_IDS


# ==================== ОБРАБОТЧИКИ REPLY-КНОПОК АДМИНА ====================

@router.message(F.text == "📋 Список событий")
async def admin_events_reply(message: types.Message):
    """Обработка кнопки 'Список событий' в админ-режиме"""
    if not is_admin(message.from_user.id):
        await message.answer("Доступ запрещен", reply_markup=get_main_keyboard())
        return

    await admin_events_list_message(message)


async def admin_events_list_message(message: types.Message):
    """Показ списка событий для админа (все события)"""
    upcoming_events = await sheets_manager.get_upcoming_events()
    past_events = await sheets_manager.get_past_events()

    if not upcoming_events and not past_events:
        await message.answer("Нет событий", reply_markup=get_admin_keyboard())
        return

    keyboard = InlineKeyboardBuilder()

    # Будущие события
    for event_id, event in upcoming_events.items():
        # ДОБАВЛЕНО: Проверка что event - словарь
        if not isinstance(event, dict):
            logger.error(f"Событие {event_id} имеет неверный формат: {type(event)}")
            continue

        # ИЗМЕНЕНИЕ: Считаем ВСЕ регистрации (registered + attended)
        registered_count = await sheets_manager.get_registrations_count(event_id, 'registered')
        attended_count = await sheets_manager.get_registrations_count(event_id, 'attended')
        total_registrations = registered_count + attended_count

        button_text = f"🟢 {event['title']} ({total_registrations}/{event['capacity']})"
        keyboard.add(InlineKeyboardButton(
            text=button_text,
            callback_data=f"admin_event_{event_id}"
        ))

    # Прошедшие события
    for event_id, event in past_events.items():
        # ДОБАВЛЕНО: Проверка что event - словарь
        if not isinstance(event, dict):
            logger.error(f"Событие {event_id} имеет неверный формат: {type(event)}")
            continue

        # ИЗМЕНЕНИЕ: Для прошедших событий показываем attended/общее количество
        registered_count = await sheets_manager.get_registrations_count(event_id, 'registered')
        attended_count = await sheets_manager.get_registrations_count(event_id, 'attended')
        total_registrations = registered_count + attended_count

        button_text = f"🔴 {event['title']} ({attended_count}/{total_registrations})"
        keyboard.add(InlineKeyboardButton(
            text=button_text,
            callback_data=f"admin_event_{event_id}"
        ))

    keyboard.adjust(1)

    text = "Выберите событие для управления:\n"
    if upcoming_events:
        text += "🟢 - будущие события (все регистрации/вместимость)\n"
    if past_events:
        text += "🔴 - прошедшие события (пришли/всего зарегистрировалось)"

    await message.answer(text, reply_markup=keyboard.as_markup())


@router.message(F.text == "📱 Сканировать QR")
async def admin_scan_qr_reply(message: types.Message):
    """Обработка кнопки 'Сканировать QR' в админ-режиме"""
    if not is_admin(message.from_user.id):
        await message.answer("Доступ запрещен", reply_markup=get_main_keyboard())
        return

    await message.answer(
        "📱 **Режим сканирования QR-кодов**\n\n"
        "Отправьте мне:\n"
        "• Фото QR-кода пользователя\n"
        "• Или ссылку из QR-кода\n"
        "• Или используйте /checkin <ID_регистрации>\n\n"
        "Я автоматически отмечу посещение пользователя.\n\n"
        "Для выхода из режима сканирования используйте /cancel"
    )


@router.message(F.text == "⚫ Черный список")
async def admin_blacklist_reply(message: types.Message):
    """Обработка кнопки 'Черный список' в админ-режиме"""
    if not is_admin(message.from_user.id):
        await message.answer("Доступ запрещен", reply_markup=get_main_keyboard())
        return

    # Показываем меню черного списка с вертикальным расположением кнопок
    keyboard = InlineKeyboardBuilder()
    keyboard.add(InlineKeyboardButton(text="📋 Показать черный список", callback_data="admin_blacklist_show"))
    keyboard.add(InlineKeyboardButton(text="➕ Добавить в черный список", callback_data="admin_blacklist_add"))
    keyboard.add(InlineKeyboardButton(text="➖ Удалить из черного списка", callback_data="admin_blacklist_remove"))
    keyboard.add(InlineKeyboardButton(text="🗑 Очистить черный список", callback_data="admin_blacklist_clear"))
    keyboard.add(InlineKeyboardButton(text="🔙 Назад", callback_data="admin_back_to_main"))

    # ВЕРТИКАЛЬНОЕ РАСПОЛОЖЕНИЕ: по одной кнопке в ряду
    keyboard.adjust(1)

    await message.answer(
        "⚫ Управление черным списком:",
        reply_markup=keyboard.as_markup()
    )


@router.message(F.text == "📊 Статистика")
async def admin_stats_reply(message: types.Message):
    """Обработка кнопки 'Статистика' в админ-режиме"""
    if not is_admin(message.from_user.id):
        await message.answer("Доступ запрещен", reply_markup=get_main_keyboard())
        return

    # Получаем статистику
    try:
        events_count = len(await sheets_manager.get_active_events())
        users_count = len(await sheets_manager.get_all_records('users'))
        registrations_count = len(await sheets_manager.get_all_records('registrations'))
        blacklist_count = len(await sheets_manager.get_blacklist())
        reminders_count = len(await sheets_manager.get_pending_reminders())

        # Получаем статистику по последним событиям
        upcoming_events = await sheets_manager.get_upcoming_events()
        past_events = await sheets_manager.get_past_events()

        stats_text = "📊 **Статистика системы**\n\n"
        stats_text += f"**Общая статистика:**\n"
        stats_text += f"• Событий: {events_count}\n"
        stats_text += f"• Пользователей: {users_count}\n"
        stats_text += f"• Регистраций: {registrations_count}\n"
        stats_text += f"• В черном списке: {blacklist_count}\n"
        stats_text += f"• Ожидающих напоминаний: {reminders_count}\n\n"

        stats_text += f"**Активные события:** {len(upcoming_events)}\n"
        for event_id, event in list(upcoming_events.items())[:5]:  # Показываем первые 5
            reg_count = await sheets_manager.get_registrations_count(event_id)
            waitlist_count = await sheets_manager.get_waitlist_count(event_id)
            stats_text += f"• {event['title']}: {reg_count}/{event['capacity']} записей"
            if waitlist_count > 0:
                stats_text += f" (+{waitlist_count} в очереди)"
            stats_text += "\n"

        if len(upcoming_events) > 5:
            stats_text += f"• ... и еще {len(upcoming_events) - 5} событий\n"

        stats_text += f"\n**Прошедшие события:** {len(past_events)}\n"

        await message.answer(stats_text, parse_mode="Markdown")

    except Exception as e:
        logger.error(f"Ошибка получения статистики: {e}")
        await message.answer("❌ Ошибка при получении статистики")


@router.message(F.text == "🔗 Получить ссылку")
async def admin_get_link_reply(message: types.Message):
    """Обработка кнопки 'Получить ссылку' - показывает список событий"""
    if not is_admin(message.from_user.id):
        await message.answer("Доступ запрещен", reply_markup=get_main_keyboard())
        return

    await show_events_for_link(message)


async def show_events_for_link(message: types.Message):
    """Показ списка событий для выбора и получения ссылки"""
    upcoming_events = await sheets_manager.get_upcoming_events()
    past_events = await sheets_manager.get_past_events()

    if not upcoming_events and not past_events:
        await message.answer("❌ Нет событий для получения ссылки", reply_markup=get_admin_keyboard())
        return

    keyboard = InlineKeyboardBuilder()

    # Будущие события
    for event_id, event in upcoming_events.items():
        if not isinstance(event, dict):
            continue

        # Считаем общее количество регистраций
        registered_count = await sheets_manager.get_registrations_count(event_id, 'registered')
        attended_count = await sheets_manager.get_registrations_count(event_id, 'attended')
        total_registrations = registered_count + attended_count

        button_text = f"🟢 {event['title']} ({total_registrations}/{event['capacity']})"
        keyboard.add(InlineKeyboardButton(
            text=button_text,
            callback_data=f"getlink_{event_id}"
        ))

    # Прошедшие события (тоже можно получить ссылку, но с предупреждением)
    for event_id, event in past_events.items():
        if not isinstance(event, dict):
            continue

        registered_count = await sheets_manager.get_registrations_count(event_id, 'registered')
        attended_count = await sheets_manager.get_registrations_count(event_id, 'attended')
        total_registrations = registered_count + attended_count

        button_text = f"🔴 {event['title']} ({attended_count}/{total_registrations})"
        keyboard.add(InlineKeyboardButton(
            text=button_text,
            callback_data=f"getlink_{event_id}"
        ))

    keyboard.adjust(1)

    text = "📋 Выберите событие для получения ссылки:\n"
    if upcoming_events:
        text += "🟢 - будущие события\n"
    if past_events:
        text += "🔴 - прошедшие события (регистрация может быть закрыта)"

    await message.answer(text, reply_markup=keyboard.as_markup())


@router.message(F.text == "🔙 Главное меню")
async def admin_back_to_main_reply(message: types.Message):
    """Обработка кнопки 'Главное меню' в админ-режиме"""
    await message.answer(
        "Главное меню. Выберите действие:",
        reply_markup=get_main_keyboard()
    )


@router.message(Command("scan"))
async def cmd_scan(message: types.Message, state: FSMContext):
    """Команда для сканирования QR-кодов администратором"""
    if not is_admin(message.from_user.id):
        await message.answer("Доступ запрещен")
        return

    await message.answer(
        "📱 **Режим сканирования QR-кодов**\n\n"
        "Отправьте мне:\n"
        "• Фото QR-кода пользователя\n"
        "• Или ссылку из QR-кода\n"
        "• Или используйте /checkin <ID_регистрации>\n\n"
        "Я автоматически отмечу посещение пользователя.\n\n"
        "Для выхода из режима сканирования используйте /cancel"
    )


@router.message(Command("checkin"))
async def cmd_checkin(message: types.Message):
    """Быстрый чекин по ID регистрации"""
    if not is_admin(message.from_user.id):
        await message.answer("Доступ запрещен")
        return

    try:
        parts = message.text.split()
        if len(parts) < 2:
            await message.answer(
                "Формат: /checkin <ID_регистрации>\n"
                "Пример: /checkin 123"
            )
            return

        registration_id = parts[1]

        # Получаем информацию о регистрации
        registration = await sheets_manager.get_registration(registration_id)
        if not registration:
            await message.answer("❌ Регистрация не найдена")
            return

        # Проверяем статус
        if registration['status'] == 'attended':
            await message.answer("⚠️ Пользователь уже отмечен")
            return

        if registration['status'] != 'registered':
            await message.answer(f"❌ Неверный статус регистрации: {registration['status']}")
            return

        # Проверяем окно чекина
        event = await sheets_manager.get_event(registration['event_id'])
        if not event:
            await message.answer("❌ Событие не найдено")
            return

        # ДОБАВЛЕНО: Проверка что event - словарь
        if not isinstance(event, dict):
            await message.answer("❌ Ошибка: данные события имеют неверный формат")
            return

        if not is_within_checkin_window(event):
            await message.answer("❌ Чекин невозможен: вне временного окна")
            return

        # Выполняем чек-ин
        from datetime import datetime
        await sheets_manager.update_registration_status(
            registration_id,
            'attended',
            datetime.now(sheets_manager.timezone)
        )

        # Получаем информацию о пользователе
        user = await sheets_manager.get_user(registration['user_id'])
        user_name = user.get('full_name', 'Неизвестно') if user else 'Неизвестно'

        await message.answer(
            f"✅ **Чекин выполнен!**\n\n"
            f"👤 {user_name}\n"
            f"📅 {event['title']}\n"
            f"🆔 ID: {registration_id}"
        )

        logger.info(f"Админ {message.from_user.id} выполнил чекин по ID {registration_id}")

    except Exception as e:
        logger.error(f"Ошибка ручного чекина: {e}")
        await message.answer("❌ Ошибка при выполнении чекина")


@router.message(Command("post"))
async def cmd_post(message: types.Message, state: FSMContext):
    """Создание нового события БЕЗ РАССЫЛКИ - первый шаг: ввод основных данных"""
    if not is_admin(message.from_user.id):
        await message.answer("Доступ запрещен", reply_markup=get_main_keyboard())
        return

    try:
        # Разбираем команду с учетом пробелов в названии
        text = message.text
        parts = text.split()

        if len(parts) < 4:
            await message.answer(
                "Формат: /post [Название] [количество_мест] [ДД-ММ-ГГГГ-ЧЧ:ММ]\n"
                "Пример: /post Мое Событие С Пробелами 100 25-11-2025-19:30\n\n"
                "ℹ️ Событие будет создано и доступно в списке событий, но рассылка не выполняется.",
                reply_markup=get_admin_keyboard()
            )
            return

        # Извлекаем дату (последний элемент) и capacity (предпоследний элемент)
        date_str = parts[-1]
        capacity_str = parts[-2]

        # Все что между "/post" и capacity - это название события
        title_parts = parts[1:-2]  # Пропускаем "/post" и два последних параметра
        title = " ".join(title_parts)

        if not title:
            await message.answer("Название события не может быть пустым", reply_markup=get_admin_keyboard())
            return

        # Проверяем capacity
        try:
            capacity = int(capacity_str)
        except ValueError:
            await message.answer("Количество мест должно быть числом", reply_markup=get_admin_keyboard())
            return

        # Парсим дату
        start_at = parse_date(date_str)
        if not start_at:
            await message.answer(
                "Неверный формат даты. Используйте: ДД-ММ-ГГГГ-ЧЧ:ММ\n"
                "Пример: 25-11-2025-19:30",
                reply_markup=get_admin_keyboard()
            )
            return

        # Создаем событие
        event_id = await sheets_manager.create_event(title, capacity, start_at)
        if not event_id:
            await message.answer("Ошибка при создании события", reply_markup=get_admin_keyboard())
            return

        # Сохраняем event_id в состояние и переходим к ожиданию поста
        await state.set_state(AdminStates.waiting_for_event_post)
        await state.update_data(event_id=event_id)

        await message.answer(
            f"✅ Событие '{title}' создано успешно! (ID: {event_id})\n\n"
            "Теперь отправьте пост для события:\n"
            "- Можно отправить текст\n"
            "- Или текст с фото/видео\n"
            "- Этот пост будет отображаться при регистрации\n\n"
            "ℹ️ Рассылка пользователям выполняться не будет.\n\n"
            "Для отмены используйте команду /cancel"
        )

    except Exception as e:
        logger.error(f"Ошибка создания события: {e}")
        await message.answer("Ошибка при создании события", reply_markup=get_admin_keyboard())


@router.message(AdminStates.waiting_for_event_post)
async def process_event_post(message: types.Message, state: FSMContext):
    """Обработка поста для события от администратора БЕЗ РАССЫЛКИ"""
    try:
        user_data = await state.get_data()
        event_id = user_data['event_id']

        # ДОБАВЛЕНО: Проверка что событие существует и является словарем
        event = await sheets_manager.get_event(event_id)
        if not event or not isinstance(event, dict):
            await message.answer(
                "❌ Ошибка: событие не найдено или имеет неверный формат",
                reply_markup=get_admin_keyboard()
            )
            await state.clear()
            return

        # Небольшая задержка для стабилизации
        await asyncio.sleep(1)

        # Сохраняем медиа-файл если есть
        media_file_id = None
        media_type = None

        if message.photo:
            media_file_id = message.photo[-1].file_id
            media_type = 'photo'
        elif message.video:
            media_file_id = message.video.file_id
            media_type = 'video'
        elif message.document:
            media_file_id = message.document.file_id
            media_type = 'document'

        # Обновляем событие с медиа-файлом
        if media_file_id:
            await sheets_manager.update_event_media(event_id, media_file_id, media_type)
            await asyncio.sleep(1)  # Задержка после обновления медиа

        # Сохраняем текст поста если есть
        if message.caption:
            post_text = message.caption
        elif message.text:
            post_text = message.text
        else:
            post_text = ""

        if post_text:
            await sheets_manager.update_event_description(event_id, post_text)
            await asyncio.sleep(1)  # Задержка после обновления описания

        # УБРАНА РАССЫЛКА - только сохранение события
        await message.answer(
            f"✅ Событие {event_id} создано и сохранено!\n\n"
            "Теперь оно будет отображаться в списке событий для пользователей.\n"
            "Рассылка не выполнялась.",
            reply_markup=get_admin_keyboard()
        )

        await state.clear()

    except Exception as e:
        logger.error(f"Ошибка обработки поста: {e}")
        await message.answer("Ошибка при обработке поста", reply_markup=get_admin_keyboard())
        await state.clear()


@router.message(Command("cancel"))
async def cmd_cancel(message: types.Message, state: FSMContext):
    """Отмена текущей операции"""
    current_state = await state.get_state()
    if current_state is None:
        return

    await state.clear()
    await message.answer("Операция отменена", reply_markup=get_admin_keyboard())


@router.message(Command("blacklist"))
async def cmd_blacklist(message: types.Message):
    """Управление черным списком"""
    if not is_admin(message.from_user.id):
        await message.answer("Доступ запрещен", reply_markup=get_main_keyboard())
        return

    parts = message.text.split()
    if len(parts) < 2:
        await show_blacklist(message)
        return

    action = parts[1].lower()

    if action == "add" and len(parts) >= 3:
        user_ref = parts[2]
        await blacklist_add(message, user_ref)
    elif action == "remove" and len(parts) >= 3:
        user_ref = parts[2]
        await blacklist_remove(message, user_ref)
    elif action == "list":
        await show_blacklist(message)
    elif action == "clear":
        await blacklist_clear(message)
    else:
        await message.answer(
            "Доступные команды:\n"
            "/blacklist add @username_or_id\n"
            "/blacklist remove @username_or_id\n"
            "/blacklist list\n"
            "/blacklist clear"
        )


@router.message(Command("admin"))
async def cmd_admin(message: types.Message):
    """Админ-панель"""
    if not is_admin(message.from_user.id):
        await message.answer("Доступ запрещен", reply_markup=get_main_keyboard())
        return

    admin_help_text = (
        "👨‍💼 **Админ-панель**\n\n"
        "**Основные команды:**\n"
        "• /post - Создать событие\n"
        "• /scan - Сканировать QR-код\n"
        "• /checkin - Чекин по ID\n"
        "• /blacklist - Управление ЧС\n"
        "• /status - Статистика\n\n"
        "**Или используйте кнопки ниже:** ⬇️"
    )

    await message.answer(admin_help_text, reply_markup=get_admin_keyboard(), parse_mode="Markdown")


# ==================== ОБРАБОТЧИКИ QR-КОДОВ ====================

async def process_qr_deeplink(bot: Bot, deeplink_text: str, admin_user_id: int, chat_id: int):
    """Общая функция обработки QR-кода с расширенной проверкой"""
    try:
        logger.info(f"Обработка QR-кода администратором {admin_user_id}: {deeplink_text}")

        # Извлекаем параметры из deeplink
        text = deeplink_text
        if "start=" in text:
            parts = text.split("start=")
            if len(parts) > 1:
                deeplink_params = parts[1].split(' ')[0]
            else:
                await bot.send_message(chat_id, "❌ Неверный формат ссылки")
                return
        else:
            deeplink_params = text

        if not deeplink_params.startswith("chk_"):
            await bot.send_message(chat_id, "❌ Это не ссылка для чекина")
            return

        params_parts = deeplink_params.split("_")
        if len(params_parts) != 3:
            await bot.send_message(chat_id, "❌ Неверный формат QR-кода")
            return

        registration_id = params_parts[1]
        signature = params_parts[2]

        logger.info(f"Распарсенные параметры: registration_id={registration_id}, signature={signature}")

        # Получаем информацию о регистрации
        registration = await sheets_manager.get_registration(registration_id)
        if not registration:
            logger.error(f"Регистрация {registration_id} не найдена")
            await bot.send_message(chat_id, "❌ Регистрация не найдена")
            return

        logger.info(f"Найдена регистрация: {registration}")

        # УЛУЧШЕННАЯ ПРОВЕРКА ТОКЕНА
        token_valid = False

        # 1. Проверяем совпадение с сохраненным токеном (основная проверка)
        if signature == registration.get('qr_token', ''):
            logger.info("✅ Токен совпадает с сохраненным в регистрации")
            token_valid = True
        else:
            # 2. Если не совпадает, проверяем через verify_qr_token
            logger.info("❌ Токен не совпадает с сохраненным, проверяем через verify_qr_token")
            token_valid = verify_qr_token(signature, registration_id, registration['event_id'], registration['user_id'])

            if token_valid:
                logger.info("✅ Токен прошел проверку через verify_qr_token")
                # Обновляем токен в базе для будущих проверок
                await sheets_manager.update_registration(registration_id, {'qr_token': signature})
            else:
                logger.error("❌ Все проверки токена провалились")

        if not token_valid:
            logger.error(f"Недействительный QR-код для регистрации {registration_id}")
            await bot.send_message(chat_id, "❌ Недействительный QR-код")
            return

        # Проверяем статус регистрации
        if registration['status'] == 'cancelled':
            await bot.send_message(chat_id, "❌ Регистрация отменена")
            return

        if registration['status'] == 'attended':
            await bot.send_message(chat_id, "⚠️ Пользователь уже отмечен на мероприятии")
            return

        if registration['status'] == 'waitlist':
            await bot.send_message(chat_id, "❌ Пользователь в списке ожидания")
            return

        # Проверяем окно чекина
        event = await sheets_manager.get_event(registration['event_id'])
        if not event:
            await bot.send_message(chat_id, "❌ Событие не найдено")
            return

        if not isinstance(event, dict):
            await bot.send_message(chat_id, "❌ Ошибка: данные события имеют неверный формат")
            return

        # УЛУЧШЕННАЯ ПРОВЕРКА ВРЕМЕННОГО ОКНА С ЛОГИРОВАНИЕМ
        if not is_within_checkin_window(event):
            # Логируем детали для отладки
            start_at = datetime.fromisoformat(event['start_at'])
            now = datetime.now(sheets_manager.timezone)
            window_start = start_at + timedelta(minutes=event.get('checkin_window_start_minutes', -60))
            window_end = start_at + timedelta(minutes=event.get('checkin_window_end_minutes', 120))

            logger.info(f"Временное окно чекина: {window_start} - {window_end}")
            logger.info(f"Текущее время: {now}")
            logger.info(f"Начало события: {start_at}")

            await bot.send_message(
                chat_id,
                f"❌ Чекин невозможен: вне временного окна\n\n"
                f"Событие: {start_at.strftime('%d.%m.%Y %H:%M')}\n"
                f"Текущее время: {now.strftime('%d.%m.%Y %H:%M')}\n"
                f"Окно чекина: за 60 мин до и 120 мин после начала"
            )
            return

        # Выполняем чек-ин
        checkin_time = datetime.now(sheets_manager.timezone)
        await sheets_manager.update_registration_status(
            registration_id,
            'attended',
            checkin_time
        )

        # Получаем информацию о пользователе
        user = await sheets_manager.get_user(registration['user_id'])
        user_name = user.get('full_name', 'Неизвестно') if user else 'Неизвестно'

        await bot.send_message(
            chat_id,
            f"✅ **Чекин выполнен успешно!**\n\n"
            f"👤 **Пользователь:** {user_name}\n"
            f"📅 **Событие:** {event['title']}\n"
            f"🆔 **ID регистрации:** {registration_id}\n"
            f"⏰ **Время:** {checkin_time.strftime('%H:%M')}"
        )

        logger.info(
            f"Администратор {admin_user_id} отметил пользователя {registration['user_id']} на событии {event['event_id']}")

    except Exception as e:
        logger.error(f"Ошибка обработки QR-кода администратором: {e}")
        await bot.send_message(chat_id, "❌ Ошибка при обработке QR-кода")


@router.message(F.text.contains("chk_"))
async def handle_qr_deeplink(message: types.Message):
    """Обработка deeplink из QR-кода от администратора"""
    if not is_admin(message.from_user.id):
        return

    await process_qr_deeplink(message.bot, message.text, message.from_user.id, message.chat.id)


@router.message(F.photo)
async def handle_qr_photo(message: types.Message):
    """Обработка фотографий с QR-кодами от администратора"""
    if not is_admin(message.from_user.id):
        return

    if not QR_SUPPORT:
        await message.answer(
            "❌ Распознавание QR-кодов из фото недоступно.\n\n"
            "Для включения этой функции установите:\n"
            "`pip install pyzbar pillow`\n\n"
            "Или отправьте текстовую ссылку из QR-кода."
        )
        return

    try:
        # Скачиваем фото
        photo = message.photo[-1]
        file_info = await message.bot.get_file(photo.file_id)
        downloaded_file = await message.bot.download_file(file_info.file_path)

        # Открываем изображение
        image = Image.open(io.BytesIO(downloaded_file.getvalue()))

        # Распознаем QR-код
        decoded_objects = pyzbar.decode(image)

        if not decoded_objects:
            await message.answer("❌ QR-код не распознан. Попробуйте сделать фото лучше.")
            return

        # Извлекаем данные из QR-кода
        qr_data = decoded_objects[0].data.decode('utf-8')

        # Используем общую функцию обработки
        await process_qr_deeplink(message.bot, qr_data, message.from_user.id, message.chat.id)

    except Exception as e:
        logger.error(f"Ошибка обработки фото QR-кода: {e}")
        await message.answer("❌ Ошибка при распознавании QR-кода")


# ==================== CALLBACK-ОБРАБОТЧИКИ (INLINE КНОПКИ) ====================

@router.callback_query(F.data == "admin_events")
async def admin_events_list(callback: types.CallbackQuery):
    """Список событий для админа (callback)"""
    await admin_events_list_message(callback.message)
    await callback.answer()


@router.callback_query(F.data == "admin_blacklist")
async def admin_blacklist_menu(callback: types.CallbackQuery):
    """Меню черного списка (callback)"""
    keyboard = InlineKeyboardBuilder()
    keyboard.add(InlineKeyboardButton(text="📋 Показать черный список", callback_data="admin_blacklist_show"))
    keyboard.add(InlineKeyboardButton(text="➕ Добавить в черный список", callback_data="admin_blacklist_add"))
    keyboard.add(InlineKeyboardButton(text="➖ Удалить из черного списка", callback_data="admin_blacklist_remove"))
    keyboard.add(InlineKeyboardButton(text="🗑 Очистить черный список", callback_data="admin_blacklist_clear"))
    keyboard.add(InlineKeyboardButton(text="🔙 Назад", callback_data="admin_back_to_main"))

    # ВЕРТИКАЛЬНОЕ РАСПОЛОЖЕНИЕ: по одной кнопке в ряду
    keyboard.adjust(1)

    await callback.message.edit_text(
        "Управление черным списком:",
        reply_markup=keyboard.as_markup()
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin_event_"))
async def admin_event_management(callback: types.CallbackQuery):
    """Управление конкретным событием"""
    event_id = callback.data.split("_")[2]
    event = await sheets_manager.get_event(event_id)

    if not event:
        await callback.answer("Событие не найдено")
        return

    # ДОБАВЛЕНО: Проверка что event - словарь
    if not isinstance(event, dict):
        await callback.answer("Ошибка: данные события имеют неверный формат")
        return

    # Получаем статистику по регистрациям
    registered_count = await sheets_manager.get_registrations_count(event_id, 'registered')
    waitlist_count = await sheets_manager.get_registrations_count(event_id, 'waitlist')
    attended_count = await sheets_manager.get_registrations_count(event_id, 'attended')

    text = f"**Управление событием {event_id}**\n\n"
    text += f"📊 Статистика:\n"
    text += f"✅ Зарегистрировано: {registered_count}/{event['capacity']}\n"
    text += f"⏳ В очереди: {waitlist_count}\n"
    text += f"🎫 Отмечено: {attended_count}\n\n"
    text += "Выберите действие:"

    keyboard = InlineKeyboardBuilder()
    keyboard.add(InlineKeyboardButton(
        text="👥 Список регистраций",
        callback_data=f"admin_registrations_{event_id}"
    ))
    keyboard.add(InlineKeyboardButton(
        text="🔙 Назад",
        callback_data="admin_events"
    ))

    keyboard.adjust(1)
    await callback.message.edit_text(text, reply_markup=keyboard.as_markup())
    await callback.answer()


@router.callback_query(F.data.startswith("admin_registrations_"))
async def admin_event_registrations(callback: types.CallbackQuery):
    """Список регистраций на событие"""
    event_id = callback.data.split("_")[2]

    registrations = await sheets_manager.get_all_records('registrations')
    event_registrations = [
        reg for reg in registrations
        if reg['event_id'] == event_id and reg['status'] in ['registered', 'attended', 'waitlist']
    ]

    if not event_registrations:
        await callback.message.edit_text("Нет регистраций на это событие")
        await callback.answer()
        return

    text = f"📋 Регистрации на событие {event_id}:\n\n"

    keyboard = InlineKeyboardBuilder()
    for reg in event_registrations[:50]:  # Ограничиваем показ
        status_icon = "✅" if reg['status'] == 'attended' else "⏳" if reg['status'] == 'waitlist' else "👤"
        btn_text = f"{status_icon} {reg['full_name']}"

        if reg['status'] == 'registered':
            keyboard.add(InlineKeyboardButton(
                text=btn_text,
                callback_data=f"admin_checkin_{reg['registration_id']}"
            ))
        else:
            keyboard.add(InlineKeyboardButton(
                text=btn_text,
                callback_data=f"admin_view_{reg['registration_id']}"
            ))

    keyboard.add(InlineKeyboardButton(
        text="🔙 Назад",
        callback_data=f"admin_event_{event_id}"
    ))

    keyboard.adjust(1)
    await callback.message.edit_text(text, reply_markup=keyboard.as_markup())
    await callback.answer()


@router.callback_query(F.data.startswith("admin_checkin_"))
async def admin_manual_checkin(callback: types.CallbackQuery):
    """Ручной чек-ин администратором"""
    registration_id = callback.data.split("_")[2]
    registration = await sheets_manager.get_registration(registration_id)

    if not registration:
        await callback.answer("Регистрация не найдена")
        return

    if registration['status'] == 'attended':
        # Снять отметку
        await sheets_manager.update_registration_status(registration_id, 'registered')
        await callback.answer("Отметка посещения снята")
    else:
        # Поставить отметку
        from datetime import datetime
        await sheets_manager.update_registration_status(
            registration_id,
            'attended',
            datetime.now(sheets_manager.timezone)
        )
        await callback.answer("Посещение отмечено")

    # Обновляем список регистраций
    event_id = registration['event_id']
    await admin_event_registrations(callback)


@router.callback_query(F.data.startswith("getlink_"))
async def handle_get_link_selection(callback: types.CallbackQuery):
    """Обработка выбора события для получения ссылки"""
    event_id = callback.data.split("_")[1]
    event = await sheets_manager.get_event(event_id)

    if not event:
        await callback.answer("❌ Событие не найдено")
        return

    # Получаем username бота
    bot_username = (await callback.bot.get_me()).username

    # Создаем ссылку для регистрации
    registration_link = f"https://t.me/{bot_username}?start=register_{event_id}"

    # Создаем кнопку для удобства
    keyboard = InlineKeyboardBuilder()
    keyboard.add(InlineKeyboardButton(
        text="🎫 Зарегистрироваться",
        url=registration_link
    ))

    # Проверяем, не прошло ли событие
    start_at = datetime.fromisoformat(event['start_at'])
    now = datetime.now(sheets_manager.timezone)
    event_passed = start_at < now - timedelta(hours=2)

    # Формируем сообщение
    response = (
        f"🔗 **Ссылка для регистрации**\n\n"
        f"**Событие:** {event['title']}\n"
        f"**ID:** {event_id}\n"
        f"**Дата:** {start_at.strftime('%d.%m.%Y %H:%M')}\n"
    )

    if event_passed:
        response += f"⚠️ *Событие уже прошло*\n\n"

    response += f"\n**Ссылка для регистрации:**\n`{registration_link}`\n\n"

    if not event_passed:
        response += "Используйте эту ссылку в посте канала для прямой регистрации."
    else:
        response += "Регистрация на это событие закрыта."
    await callback.message.edit_text(
        response,
        parse_mode="Markdown"
    )
    await callback.answer()


@router.callback_query(F.data == "getlink_back_to_list")
async def handle_back_to_link_list(callback: types.CallbackQuery):
    """Возврат к списку событий для получения ссылки"""
    await show_events_for_link(callback.message)
    await callback.answer()


@router.callback_query(F.data.startswith("create_post_"))
async def handle_create_post(callback: types.CallbackQuery):
    """Создание готового поста для канала"""
    event_id = callback.data.split("_")[2]
    event = await sheets_manager.get_event(event_id)

    if not event:
        await callback.answer("❌ Событие не найдено")
        return

    # Получаем username бота
    bot_username = (await callback.bot.get_me()).username
    registration_link = f"https://t.me/{bot_username}?start=register_{event_id}"

    start_at = datetime.fromisoformat(event['start_at'])

    # Проверяем, не прошло ли событие
    now = datetime.now(sheets_manager.timezone)
    event_passed = start_at < now - timedelta(hours=2)

    post_text = (
        f"🎉 **{event['title']}**\n\n"
        f"📅 **Дата:** {start_at.strftime('%d.%m.%Y')}\n"
        f"⏰ **Время:** {start_at.strftime('%H:%M')}\n"
        f"📍 **Место:** {event.get('place', 'Уточняется')}\n\n"
    )

    if event.get('description'):
        post_text += f"{event['description']}\n\n"

    if event_passed:
        post_text += "❌ *Регистрация на это событие закрыта*"
    else:
        post_text += "Для регистрации нажмите кнопку ниже 👇"

    keyboard = InlineKeyboardBuilder()
    if not event_passed:
        keyboard.add(InlineKeyboardButton(
            text="🎫 Зарегистрироваться",
            url=registration_link
        ))

    # Кнопки для администратора
    admin_keyboard = InlineKeyboardBuilder()
    admin_keyboard.add(InlineKeyboardButton(
        text="📋 Вернуться к списку",
        callback_data="getlink_back_to_list"
    ))
    admin_keyboard.adjust(2)

    await callback.message.answer(
        "📝 **Готовый пост для канала:**\n\n"
        "Скопируйте текст ниже и разместите в канале:"
    )

    await callback.message.answer(post_text, reply_markup=keyboard.as_markup(), parse_mode="Markdown")

    await callback.message.answer(
        f"🔗 **Ссылка для кнопки:**\n`{registration_link}`\n\n"
        f"*Разместите эту ссылку как кнопку в посте канала*",
        parse_mode="Markdown",
        reply_markup=admin_keyboard.as_markup()
    )

    await callback.answer()


@router.callback_query(F.data == "admin_back_to_main")
async def admin_back_to_main_callback(callback: types.CallbackQuery):
    """Возврат в главное меню админа из инлайн-кнопки"""
    await callback.message.edit_text(
        "Админ-панель. Используйте кнопки ниже или команды:",
        reply_markup=get_admin_keyboard()
    )
    await callback.answer()


# ==================== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ====================

async def blacklist_add(message: types.Message, user_ref):
    """Добавление в черный список с улучшенным логированием"""
    try:
        logger.info(f"Попытка добавления в черный список: {user_ref}")

        # Пытаемся найти user_id по ссылке
        user_id = await resolve_user_ref(user_ref)
        if not user_id:
            await message.answer(f"Пользователь {user_ref} не найден")
            return

        # Проверяем, не в черном списке ли уже пользователь
        if await sheets_manager.is_blacklisted(user_id):
            await message.answer(f"Пользователь {user_ref} уже в черном списке")
            return

        # Добавляем в черный список
        success = await sheets_manager.add_to_blacklist(
            user_id,
            f"Добавлен администратором {message.from_user.id}",
            str(message.from_user.id)
        )

        if success:
            await message.answer(f"✅ Пользователь {user_ref} добавлен в черный список")
            logger.info(f"Пользователь {user_id} ({user_ref}) добавлен в черный список")
        else:
            await message.answer("❌ Ошибка при добавлении в черный список")
            logger.error(f"Метод add_to_blacklist вернул False для пользователя {user_id}")

    except Exception as e:
        logger.error(f"Ошибка добавления в черный список: {e}")
        await message.answer("❌ Ошибка при добавлении в черный список")


async def blacklist_remove(message: types.Message, user_ref):
    """Удаление из черного списка"""
    try:
        user_id = await resolve_user_ref(user_ref)
        if not user_id:
            await message.answer("Пользователь не найден")
            return

        success = await sheets_manager.remove_from_blacklist(user_id)
        if success:
            await message.answer(f"✅ Пользователь {user_ref} удален из черного списка")
        else:
            await message.answer("❌ Пользователь не найден в черном списке")

    except Exception as e:
        logger.error(f"Ошибка удаления из черного списка: {e}")
        await message.answer("❌ Ошибка при удалении из черного списка")


async def blacklist_clear(message: types.Message):
    """Очистка черного списка"""
    try:
        blacklist = await sheets_manager.get_blacklist()
        for entry in blacklist.values():
            await sheets_manager.remove_from_blacklist(entry['user_id'])

        await message.answer("✅ Черный список очищен")
    except Exception as e:
        logger.error(f"Ошибка очистки черного списка: {e}")
        await message.answer("❌ Ошибка при очистки черного списка")


async def show_blacklist(message: types.Message):
    """Показать черный список"""
    try:
        blacklist = await sheets_manager.get_blacklist()
        if not blacklist:
            await message.answer("Черный список пуст")
            return

        text = "📋 Черный список:\n\n"
        for entry in blacklist.values():
            text += f"👤 {entry['user_id']}\n"
            text += f"📝 {entry.get('reason', '')}\n"
            text += f"⏰ {entry.get('added_at', '')}\n\n"

        await message.answer(text)
    except Exception as e:
        logger.error(f"Ошибка показа черного списка: {e}")
        await message.answer("❌ Ошибка при получении черного списка")


async def resolve_user_ref(user_ref):
    """Разрешение user_ref в user_id с поиском в Google Sheets и JSON"""
    try:
        # Если это числовой ID
        if user_ref.isdigit():
            return int(user_ref)

        # Если это @username
        if user_ref.startswith('@'):
            username_to_find = user_ref[1:].lower()

            # 1. Ищем в Google Sheets
            users_from_sheets = await sheets_manager.get_all_records('users')
            for user in users_from_sheets:
                if user.get('username', '').lower() == username_to_find:
                    logger.info(f"Найден пользователь {username_to_find} в Google Sheets: {user['user_id']}")
                    return user['user_id']

            # 2. Ищем в JSON (локальное хранилище)
            from user_manager import user_manager
            users_from_json = user_manager.get_all_users()
            for user in users_from_json:
                if user.get('username', '').lower() == username_to_find:
                    logger.info(f"Найден пользователь {username_to_find} в JSON: {user['user_id']}")
                    return user['user_id']

            # 3. Если не нашли, логируем для отладки
            logger.warning(f"Пользователь @{username_to_find} не найден ни в Google Sheets, ни в JSON")

        return None
    except Exception as e:
        logger.error(f"Ошибка разрешения user_ref: {e}")
        return None


# ==================== CALLBACK-ОБРАБОТЧИКИ ЧЕРНОГО СПИСКА ====================

@router.callback_query(F.data == "admin_blacklist_show")
async def admin_blacklist_show(callback: types.CallbackQuery):
    """Показать черный список (callback)"""
    try:
        blacklist = await sheets_manager.get_blacklist()
        if not blacklist:
            await callback.message.edit_text("Черный список пуст")
            await callback.answer()
            return

        text = "📋 Черный список:\n\n"
        for entry in blacklist.values():
            text += f"👤 {entry['user_id']}\n"
            text += f"📝 {entry.get('reason', '')}\n"
            text += f"⏰ {entry.get('added_at', '')}\n\n"

        # Добавляем кнопку "Назад"
        keyboard = InlineKeyboardBuilder()
        keyboard.add(InlineKeyboardButton(text="🔙 Назад", callback_data="admin_blacklist"))
        keyboard.adjust(1)  # Вертикальное расположение

        await callback.message.edit_text(text, reply_markup=keyboard.as_markup())
        await callback.answer()
    except Exception as e:
        logger.error(f"Ошибка показа черного списка: {e}")
        await callback.message.edit_text("Ошибка при получении черного списка")
        await callback.answer()


@router.callback_query(F.data == "admin_blacklist_add")
async def admin_blacklist_add_callback(callback: types.CallbackQuery):
    """Добавление в черный список (callback)"""
    keyboard = InlineKeyboardBuilder()
    keyboard.add(InlineKeyboardButton(text="🔙 Назад", callback_data="admin_blacklist"))
    keyboard.adjust(1)  # Вертикальное расположение

    await callback.message.edit_text(
        "Для добавления в черный списка используйте команду:\n"
        "/blacklist add @username_or_id\n\n"
        "Или вернитесь в меню:",
        reply_markup=keyboard.as_markup()
    )
    await callback.answer()


@router.callback_query(F.data == "admin_blacklist_remove")
async def admin_blacklist_remove_callback(callback: types.CallbackQuery):
    """Удаление из черного списка (callback)"""
    keyboard = InlineKeyboardBuilder()
    keyboard.add(InlineKeyboardButton(text="🔙 Назад", callback_data="admin_blacklist"))
    keyboard.adjust(1)  # Вертикальное расположение

    await callback.message.edit_text(
        "Для удаления из черного списка используйте команду:\n"
        "/blacklist remove @username_or_id\n\n"
        "Или вернитесь в меню:",
        reply_markup=keyboard.as_markup()
    )
    await callback.answer()


@router.callback_query(F.data == "admin_blacklist_clear")
async def admin_blacklist_clear_callback(callback: types.CallbackQuery):
    """Очистка черного списка (callback)"""
    try:
        blacklist = await sheets_manager.get_blacklist()
        for entry in blacklist.values():
            await sheets_manager.remove_from_blacklist(entry['user_id'])

        # Добавляем кнопку "Назад"
        keyboard = InlineKeyboardBuilder()
        keyboard.add(InlineKeyboardButton(text="🔙 Назад", callback_data="admin_blacklist"))
        keyboard.adjust(1)  # Вертикальное расположение

        await callback.message.edit_text("✅ Черный список очищен", reply_markup=keyboard.as_markup())
        await callback.answer()
    except Exception as e:
        logger.error(f"Ошибка очистки черного списка: {e}")
        await callback.message.edit_text("❌ Ошибка при очистки черного списка")
        await callback.answer()