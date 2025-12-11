import asyncio
import logging
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

from config import Config
from sheets import sheets_manager
from scheduler import SchedulerManager
from utils import validate_fullname, generate_qr_token, generate_qr_code_image
from keyboards import create_registration_keyboard
from keyboards import get_main_keyboard, create_registration_keyboard, create_cancel_keyboard
from user_manager import user_manager  # Импортируем менеджер пользователей
import admin_handlers
import checkin_handlers

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Инициализация бота
bot = Bot(token=Config.BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# Регистрация роутеров
dp.include_router(admin_handlers.router)
dp.include_router(checkin_handlers.router)


class RegistrationStates(StatesGroup):
    waiting_fullname = State()


@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext, command: CommandObject = None):
    """Обработка команды /start с поддержкой deep link для регистрации"""
    await state.clear()

    user_id = message.from_user.id
    username = message.from_user.username
    full_name = f"{message.from_user.first_name} {message.from_user.last_name or ''}".strip()

    # Добавляем пользователя в базу Google Sheets (через локальное хранилище)
    await sheets_manager.add_user(user_id, username, full_name)

    # Добавляем пользователя в JSON
    user_manager.add_user(user_id, username, full_name)

    # Проверяем черный список
    if await sheets_manager.is_blacklisted(user_id):
        await message.answer("Действие недоступно. Обратитесь к менеджеру, пожалуйста.")
        return

    # Обработка deep link для регистрации
    if command and command.args and command.args.startswith("register_"):
        event_id = command.args.replace("register_", "")
        await handle_direct_registration(message, state, event_id)
        return

    # Обычное приветствие
    await message.answer(
        "Привет! 👋\nЯ помогу записаться на наши события.\nВыберите действие ниже.",
        reply_markup=get_main_keyboard()
    )


async def handle_direct_registration(message: types.Message, state: FSMContext, event_id: str):
    """Обработка прямой регистрации через deep link"""
    user_id = message.from_user.id

    # Проверяем событие
    event = await sheets_manager.get_event(event_id)
    if not event:
        await message.answer("❌ Событие не найдено.")
        return

    # Проверяем, что событие еще не прошло
    start_at = datetime.fromisoformat(event['start_at'])
    if start_at < datetime.now(sheets_manager.timezone) - timedelta(hours=2):
        await message.answer("❌ Регистрация на это событие закрыта, так как оно уже прошло.")
        return

    # Проверяем существующую регистрацию
    existing_reg = await sheets_manager.get_user_registration(user_id, event_id)
    if existing_reg:
        if existing_reg['status'] == 'registered':
            await message.answer("✅ Вы уже зарегистрированы на это событие!")
            # Показываем QR-код если уже зарегистрирован
            await _generate_and_send_qr(user_id, message.bot, message.chat.id)
        elif existing_reg['status'] == 'waitlist':
            position = existing_reg.get('waitlist_position', '?')
            await message.answer(f"⏳ Вы в листе ожидания. Ваша позиция: {position}")
        elif existing_reg['status'] == 'attended':
            await message.answer("✅ Вы уже посетили это событие!")
        return

    # Начинаем регистрацию
    await state.set_state(RegistrationStates.waiting_fullname)
    await state.update_data(event_id=event_id)

    # Предлагаем использовать текущее имя или ввести новое
    current_name = message.from_user.first_name
    if message.from_user.last_name:
        current_name += f" {message.from_user.last_name}"

    await message.answer(
        f"🎫 **Регистрация на событие:** {event['title']}\n\n"
        f"📅 **Дата:** {start_at.strftime('%d.%m.%Y %H:%M')}\n\n"
        f"Для завершения регистрации введите ваше Имя и Фамилию.\n"
        f"Можно использовать: `{current_name}` или ввести новые данные.\n\n"
        f"*Пример:* Иван Петров"
    )


@dp.callback_query(F.data == "events_list")
async def show_events_list(callback: types.CallbackQuery):
    """Показ списка событий для пользователей (только будущие)"""
    events = await sheets_manager.get_upcoming_events()

    if not events:
        await callback.message.answer("На данный момент нет активных событий.")
        return

    keyboard = InlineKeyboardBuilder()
    for event_id, event in events.items():
        # ИЗМЕНЕНИЕ: Считаем ВСЕ активные регистрации (registered + attended)
        registered_count = await sheets_manager.get_registrations_count(event_id, 'registered')
        attended_count = await sheets_manager.get_registrations_count(event_id, 'attended')
        total_registrations = registered_count + attended_count

        waitlist_count = await sheets_manager.get_waitlist_count(event_id)

        button_text = f"{event['title']} ({total_registrations}/{event['capacity']})"
        if waitlist_count > 0:
            button_text += f" (ожидание: {waitlist_count})"

        keyboard.add(InlineKeyboardButton(
            text=button_text,
            callback_data=f"event_{event_id}"
        ))

    keyboard.adjust(1)
    await callback.message.answer(
        "📋 Список активных событий:",
        reply_markup=keyboard.as_markup()
    )


@dp.callback_query(F.data.startswith("event_"))
async def show_event(callback: types.CallbackQuery):
    """Показ поста события С МЕДИА-ФАЙЛАМИ"""
    event_id = callback.data.split("_")[1]
    event = await sheets_manager.get_event(event_id)

    if not event:
        await callback.answer("Событие не найдено")
        return

    # Используем сохраненный пост администратора или создаем базовый
    if event.get('description'):
        event_post = event['description']
    else:
        event_post = f"**{event['title']}**\n\n"

    keyboard = create_registration_keyboard(event_id)

    # ПРОВЕРЯЕМ НАЛИЧИЕ МЕДИА-ФАЙЛА
    media_file_id = event.get('media_file_id')
    media_type = event.get('media_type')

    if media_file_id and media_type:
        try:
            # ОТПРАВЛЯЕМ МЕДИА С ТЕКСТОМ
            if media_type == 'photo':
                await callback.message.answer_photo(
                    photo=media_file_id,
                    caption=event_post,
                    reply_markup=keyboard,
                    parse_mode="Markdown"
                )
            elif media_type == 'video':
                await callback.message.answer_video(
                    video=media_file_id,
                    caption=event_post,
                    reply_markup=keyboard,
                    parse_mode="Markdown"
                )
            elif media_type == 'document':
                await callback.message.answer_document(
                    document=media_file_id,
                    caption=event_post,
                    reply_markup=keyboard,
                    parse_mode="Markdown"
                )
            else:
                # Если неизвестный тип медиа, отправляем только текст
                await callback.message.answer(event_post, reply_markup=keyboard, parse_mode="Markdown")
        except Exception as e:
            logger.error(f"Ошибка отправки медиа для события {event_id}: {e}")
            # В случае ошибки отправляем только текст
            await callback.message.answer(event_post, reply_markup=keyboard, parse_mode="Markdown")
    else:
        # Если медиа нет, отправляем только текст
        await callback.message.answer(event_post, reply_markup=keyboard, parse_mode="Markdown")


@dp.callback_query(F.data.startswith("register_"))
async def start_registration(callback: types.CallbackQuery, state: FSMContext):
    """Начало регистрации"""
    user_id = callback.from_user.id
    event_id = callback.data.split("_")[1]

    # Проверяем черный список
    if await sheets_manager.is_blacklisted(user_id):
        await callback.answer("Действие недоступно. Обратитесь к менеджеру.")
        return

    # Проверяем, что событие еще не прошло
    event = await sheets_manager.get_event(event_id)
    if event:
        start_at = datetime.fromisoformat(event['start_at'])
        if start_at < datetime.now(sheets_manager.timezone) - timedelta(hours=2):
            await callback.answer("Регистрация на это событие закрыта, так как оно уже прошло.")
            return

    # Проверяем существующую регистрацию на ЭТО событие
    existing_reg = await sheets_manager.get_user_registration(user_id, event_id)
    if existing_reg:
        if existing_reg['status'] == 'registered':
            await callback.answer("Вы уже зарегистрированы на это событие")
        elif existing_reg['status'] == 'waitlist':
            position = existing_reg.get('waitlist_position', '?')
            await callback.answer(f"Вы в листе ожидания. Ваша позиция: {position}")
        return

    await state.set_state(RegistrationStates.waiting_fullname)
    await state.update_data(event_id=event_id)

    await callback.message.answer(
        "Напишите ваше Имя и Фамилию одной строкой (например: «Иван Петров»)."
    )


@dp.message(RegistrationStates.waiting_fullname)
async def process_fullname(message: types.Message, state: FSMContext):
    """Обработка ввода ФИО"""
    fullname = message.text.strip()

    # Валидация ФИО
    if not validate_fullname(fullname):
        await message.answer("Пожалуйста, введите корректные Имя и Фамилию (минимум 2 слова, только текст)")
        return

    user_data = await state.get_data()
    event_id = user_data['event_id']
    user_id = message.from_user.id

    # Обновляем ФИО пользователя в Google Sheets (через локальное хранилище)
    await sheets_manager.update_user_fullname(user_id, fullname)

    # Обновляем ФИО пользователя в JSON
    user_manager.update_user_info(user_id, full_name=fullname)

    # Генерируем QR-токен
    qr_token = generate_qr_token(f"reg_{user_id}_{event_id}", event_id, user_id)

    # Создаем регистрацию
    registration_id, status, waitlist_position = await sheets_manager.create_registration(
        user_id, event_id, fullname, qr_token
    )

    if registration_id:
        if status == 'registered':
            # Получаем информацию о событии для QR-кода
            event = await sheets_manager.get_event(event_id)

            # Формируем deeplink для чекина
            bot_username = (await message.bot.get_me()).username
            deeplink = f"https://t.me/{bot_username}?start=chk_{registration_id}_{qr_token}"

            # Генерируем QR-код
            qr_image = generate_qr_code_image(deeplink)

            if qr_image:
                # Отправляем сообщение об успешной регистрации
                await message.answer(
                    f"✅ Вы успешно зарегистрировались на событие!\n"
                    f"📅 {event['title']}\n"
                    f"🗓 {datetime.fromisoformat(event['start_at']).strftime('%d.%m.%Y %H:%M')}\n\n"
                    f"Сохраните QR-код ниже для входа на мероприятие:"
                )

                # Отправляем QR-код
                await message.answer_photo(
                    types.BufferedInputFile(
                        qr_image.getvalue(),
                        filename="qr_code.png"
                    ),
                    caption="Ваш QR-код для входа на мероприятие"
                )
            else:
                await message.answer(
                    f"✅ Вы успешно зарегистрировались на событие {event_id}!\n"
                    f"QR-код будет отправлен позже."
                )

            # Создаем напоминания только если время их отправки еще не прошло
            if event:
                start_at = datetime.fromisoformat(event['start_at'])
                now = datetime.now(sheets_manager.timezone)

                # Напоминание за 1 день - создаем только если до события больше 1 дня
                reminder_time_day = start_at - timedelta(days=1)
                if reminder_time_day > now:
                    await sheets_manager.create_reminder(
                        event_id, user_id, reminder_time_day, "D1"
                    )
                    logger.info(f"Создано напоминание D1 для пользователя {user_id}")

                # Напоминание за 6 часов - создаем только если до события больше 6 часов
                reminder_time_six_hours = start_at - timedelta(hours=6)
                if reminder_time_six_hours > now:
                    await sheets_manager.create_reminder(
                        event_id, user_id, reminder_time_six_hours, "H6"
                    )
                    logger.info(f"Создано напоминание H6 для пользователя {user_id}")

                # Напоминание за 1 час - создаем только если до события больше 1 часа
                reminder_time_one_hour = start_at - timedelta(hours=1)
                if reminder_time_one_hour > now:
                    await sheets_manager.create_reminder(
                        event_id, user_id, reminder_time_one_hour, "H1"
                    )
                    logger.info(f"Создано напоминание H1 для пользователя {user_id}")

        else:  # waitlist
            await message.answer(
                "Мест нет 😕 Вы добавлены в список ожидания. Сообщим, если место освободится."
            )
    else:
        await message.answer("Произошла ошибка при регистрации. Попробуйте позже.")

    await state.clear()


# Обработчики отмены регистрации
@dp.callback_query(F.data.startswith("reminder_cancel_"))
async def reminder_cancel_registration(callback: types.CallbackQuery):
    """Запрос отмены регистрации из напоминания"""
    registration_id = callback.data.split("_")[2]

    keyboard = create_cancel_keyboard(registration_id)
    await callback.message.answer(
        "Подтвердить отмену регистрации?",
        reply_markup=keyboard
    )


@dp.callback_query(F.data.startswith("cancel_confirm_"))
async def confirm_cancel_registration(callback: types.CallbackQuery):
    """Подтверждение отмена регистрации"""
    registration_id = callback.data.split("_")[2]

    await sheets_manager.cancel_registration(registration_id)
    await callback.message.answer("✅ Ваша регистрация отменена.")
    await callback.answer()


@dp.callback_query(F.data == "cancel_cancel")
async def cancel_cancel_operation(callback: types.CallbackQuery):
    """Отмена операции отмены регистрации"""
    await callback.message.answer("Операция отменена.")
    await callback.answer()


# Обработчики для очереди
@dp.callback_query(F.data.startswith("take_place_"))
async def take_place_from_waitlist(callback: types.CallbackQuery):
    """Занимание освободившегося места"""
    registration_id = callback.data.split("_")[2]
    registration = await sheets_manager.get_registration(registration_id)

    if not registration or registration['status'] != 'waitlist':
        await callback.message.answer("К сожалению, место уже занято. Вы остаетесь в листе ожидания.")
        return

    # Переводим в зарегистрированные
    await sheets_manager.update_registration_status(registration_id, 'registered')

    # Отправляем QR-код для нового участника
    try:
        event = await sheets_manager.get_event(registration['event_id'])
        user_id = registration['user_id']

        # Генерируем новый QR-токен
        qr_token = generate_qr_token(f"reg_{user_id}_{registration['event_id']}", registration['event_id'], user_id)

        # Обновляем регистрацию с новым QR-токеном
        await sheets_manager.update_registration(registration_id, {'qr_token': qr_token})

        # Формируем deeplink для чекина
        bot_username = (await callback.message.bot.get_me()).username
        deeplink = f"https://t.me/{bot_username}?start=chk_{registration_id}_{qr_token}"

        # Генерируем QR-код
        qr_image = generate_qr_code_image(deeplink)

        if qr_image and event:
            # Отправляем сообщение об успешной регистрации
            await callback.message.answer(
                f"✅ Вы успешно зарегистрировались на событие!\n"
                f"📅 {event['title']}\n"
                f"🗓 {datetime.fromisoformat(event['start_at']).strftime('%d.%m.%Y %H:%M')}\n\n"
                f"Сохраните QR-код ниже для входа на мероприятие:"
            )

            # Отправляем QR-код
            await callback.message.answer_photo(
                types.BufferedInputFile(
                    qr_image.getvalue(),
                    filename="qr_code.png"
                ),
                caption="Ваш QR-код для входа на мероприятие"
            )
        else:
            await callback.message.answer(
                f"✅ Вы успешно зарегистрировались на событие {registration['event_id']}!"
            )
    except Exception as e:
        logger.error(f"Ошибка отправки QR-кода при занятии места: {e}")
        await callback.message.answer(
            f"✅ Вы успешно зарегистрировались на событие {registration['event_id']}!"
        )

    await callback.answer()


# Обработчики оценки событий
@dp.callback_query(F.data.startswith("rate_"))
async def process_event_rating(callback: types.CallbackQuery):
    """Обработка оценки события"""
    try:
        parts = callback.data.split("_")
        event_id = parts[1]
        rating = int(parts[2])

        await callback.message.answer(f"Спасибо за оценку {rating}! Ваш отзыв очень важен для нас.")
        await callback.answer()

    except Exception as e:
        logger.error(f"Ошибка обработки оценки: {e}")
        await callback.answer("Ошибка при обработке оценки")


@dp.message(Command("my_qr"))
async def cmd_my_qr(message: types.Message):
    """Команда для получения QR-кода"""
    # Используем ID пользователя из сообщения
    user_id = message.from_user.id
    await _generate_and_send_qr(user_id, message.bot, message.chat.id)


async def _generate_and_send_qr(user_id: int, bot: Bot, chat_id: int):
    """Внутренняя функция для генерации и отправки QR-кода"""
    try:
        logger.info(f"Генерация QR-кода для пользователя {user_id}")

        # Получаем все регистрации через локальное хранилище
        registrations_data = await sheets_manager.local_storage.get_all_registrations()
        logger.info(f"Всего регистраций в локальном хранилище: {len(registrations_data)}")

        # Ищем активные регистрации пользователя
        active_registrations = []
        for reg_id, reg_data in registrations_data.items():
            reg_user_id = reg_data.get('user_id')
            reg_status = reg_data.get('status')

            # Сравниваем как строки, так как типы могут различаться
            if (str(reg_user_id) == str(user_id) and
                    reg_status in ['registered', 'attended']):
                active_registrations.append(reg_data)

        logger.info(f"Найдено активных регистраций для пользователя {user_id}: {len(active_registrations)}")

        if not active_registrations:
            await bot.send_message(
                chat_id,
                "У вас нет активных регистраций на события.\n\n"
                "Чтобы получить QR-код:\n"
                "1. Нажмите '📋 Список событий'\n"
                "2. Выберите интересующее событие\n"
                "3. Зарегистрируйтесь на него"
            )
            return

        # Берем последнюю регистрацию
        registration = active_registrations[-1]
        event_id = registration['event_id']

        logger.info(f"Используем регистрацию: {registration['registration_id']} для события {event_id}")

        # Получаем информацию о событии
        event = await sheets_manager.get_event(event_id)
        if not event:
            await bot.send_message(chat_id, "❌ Событие не найдено в базе данных.")
            return

        # Проверяем актуальность события
        start_at = datetime.fromisoformat(event['start_at'])
        now = datetime.now(sheets_manager.timezone)

        if start_at < now - timedelta(hours=2):
            await bot.send_message(chat_id, "⚠️ Это событие уже прошло. QR-код больше не действителен.")
            return

        # Генерируем или обновляем QR-токен
        qr_token = registration.get('qr_token', '')
        if not qr_token or qr_token == 'temp_token':
            qr_token = generate_qr_token(registration['registration_id'], event_id, user_id)
            # Обновляем в хранилище
            await sheets_manager.update_registration(registration['registration_id'], {'qr_token': qr_token})
            logger.info(f"Сгенерирован новый QR-токен для регистрации {registration['registration_id']}")

        # Создаем deeplink
        bot_username = (await bot.get_me()).username
        deeplink = f"https://t.me/{bot_username}?start=chk_{registration['registration_id']}_{qr_token}"

        # Генерируем QR-код
        qr_image = generate_qr_code_image(deeplink)

        if qr_image:
            event_date = datetime.fromisoformat(event['start_at']).strftime('%d.%m.%Y в %H:%M')

            await bot.send_photo(
                chat_id,
                photo=types.BufferedInputFile(
                    qr_image.getvalue(),
                    filename="qr_code.png"
                ),
                caption=(
                    f"🎫 **Ваш QR-код**\n\n"
                    f"**Событие:** {event['title']}\n"
                    f"**Дата:** {event_date}\n"
                    f"**Место:** {event.get('place', 'уточняется')}\n\n"
                    f"Покажите этот код на входе для отметки посещения."
                ),
                parse_mode="Markdown"
            )

            logger.info(f"QR-код успешно отправлен пользователю {user_id}")
        else:
            await bot.send_message(chat_id, "❌ Не удалось сгенерировать QR-код. Попробуйте позже.")

    except Exception as e:
        logger.error(f"Критическая ошибка при генерации QR-кода для пользователя {user_id}: {e}")
        await bot.send_message(chat_id, "❌ Произошла ошибка. Обратитесь к администратору.")


@dp.callback_query(F.data == "my_qr_code")
async def my_qr_code_handler(callback: types.CallbackQuery):
    """Обработка кнопки 'Мой QR-код' - ИСПРАВЛЕННАЯ ВЕРСИЯ"""
    # ИСПРАВЛЕНИЕ: передаем правильный user_id из callback, а не из message
    user_id = callback.from_user.id
    logger.info(f"Обработка кнопки QR-кода для пользователя {user_id}")

    # Вызываем внутреннюю функцию с правильными параметрами
    await _generate_and_send_qr(user_id, callback.bot, callback.message.chat.id)
    await callback.answer()


@dp.message(Command("my_qr_direct"))
async def cmd_my_qr_direct(message: types.Message):
    """Альтернативная команда для получения QR-кода с прямым доступом к данным"""
    try:
        user_id = message.from_user.id
        logger.info(f"Прямой поиск QR-кода для пользователя {user_id}")

        # Используем прямой метод поиска
        user_registrations = await sheets_manager.local_storage.find_user_registrations(user_id)

        # Фильтруем только активные регистрации
        active_registrations = [
            reg for reg in user_registrations
            if reg.get('status') in ['registered', 'attended']
        ]

        if not active_registrations:
            # Покажем все регистрации пользователя для отладки
            all_user_regs = [
                reg for reg in user_registrations
                if reg.get('status') in ['registered', 'attended', 'waitlist', 'cancelled']
            ]

            if all_user_regs:
                status_info = "\n".join([f"- {reg['registration_id']}: {reg['status']}" for reg in all_user_regs])
                await message.answer(
                    f"У вас есть регистрации, но нет активных:\n{status_info}\n\n"
                    f"Статус 'registered' или 'attended' требуется для получения QR-кода."
                )
            else:
                await message.answer("У вас нет ни одной регистрации на события.")
            return

        # Продолжаем с генерацией QR-кода...
        registration = active_registrations[-1]
        # ... остальной код такой же как в cmd_my_qr

    except Exception as e:
        logger.error(f"Ошибка в cmd_my_qr_direct: {e}")
        await message.answer("❌ Ошибка при получении QR-кода.")


@dp.message(Command("status"))
async def cmd_status(message: types.Message):
    """Проверка статуса системы"""
    try:
        events_count = len(await sheets_manager.get_active_events())
        users_count = len(await sheets_manager.get_all_records('users'))
        json_users_count = user_manager.get_user_count()
        registrations_count = len(await sheets_manager.get_all_records('registrations'))
        reminders_count = len(await sheets_manager.get_pending_reminders())

        status_text = f"""📊 Статус системы:

Событий: {events_count}
Пользователей (Google Sheets): {users_count}
Пользователей (JSON): {json_users_count}
Регистраций: {registrations_count}
Ожидающих напоминаний: {reminders_count}

✅ Бот работает нормально"""

        await message.answer(status_text)
    except Exception as e:
        await message.answer(f"❌ Ошибка получения статуса: {str(e)}")


@dp.message(Command("users"))
async def cmd_users(message: types.Message):
    """Показать статистику по пользователям"""
    try:
        json_users = user_manager.get_all_users()
        json_count = user_manager.get_user_count()

        users_text = f"📊 Пользователи в JSON: {json_count}\n\n"

        if json_users:
            for i, user in enumerate(json_users[:10], 1):  # Показываем первых 10
                users_text += f"{i}. ID: {user['user_id']}\n"
                if user.get('username'):
                    users_text += f"   @{user['username']}\n"
                if user.get('full_name'):
                    users_text += f"   {user['full_name']}\n"
                users_text += "\n"

            if json_count > 10:
                users_text += f"... и еще {json_count - 10} пользователей"
        else:
            users_text += "Нет пользователей в JSON"

        await message.answer(users_text)

    except Exception as e:
        await message.answer(f"❌ Ошибка получения списка пользователей: {str(e)}")


@dp.message(Command("check_secret"))
async def cmd_check_secret(message: types.Message):
    """Проверка секретного ключа"""
    from config import Config
    key = Config.SECRET_KEY
    # Покажем первые 10 и последние 5 символов ключа для проверки
    if len(key) > 15:
        masked_key = key[:10] + "..." + key[-5:]
    else:
        masked_key = key
    await message.answer(f"SECRET_KEY (маскированный): {masked_key}")


@dp.message(Command("test_token"))
async def cmd_test_token(message: types.Message):
    """Тестирование генерации токена"""
    from admin_handlers import is_admin
    if not is_admin(message.from_user.id):
        await message.answer("Доступ запрещен")
        return

    try:
        parts = message.text.split()
        if len(parts) < 2:
            await message.answer("Формат: /test_token <registration_id>")
            return

        registration_id = parts[1]
        registration = await sheets_manager.get_registration(registration_id)

        if not registration:
            await message.answer("❌ Регистрация не найдена")
            return

        # Генерируем токен разными способами
        token1 = generate_qr_token(registration_id, registration['event_id'], registration['user_id'])
        token2 = generate_qr_token(str(registration_id), str(registration['event_id']), str(registration['user_id']))

        response = (
            f"🔍 **Диагностика токена для регистрации {registration_id}**\n\n"
            f"**Параметры:**\n"
            f"• reg_id: {registration_id} (тип: {type(registration_id)})\n"
            f"• event_id: {registration['event_id']} (тип: {type(registration['event_id'])})\n"
            f"• user_id: {registration['user_id']} (тип: {type(registration['user_id'])})\n\n"
            f"**Результаты:**\n"
            f"• Токен в БД: {registration.get('qr_token', 'нет')}\n"
            f"• Новый токен (как есть): {token1}\n"
            f"• Новый токен (строки): {token2}\n"
            f"• Совпадают: {token1 == token2}"
        )

        await message.answer(response)

    except Exception as e:
        logger.error(f"Ошибка тестирования токена: {e}")
        await message.answer("❌ Ошибка при тестировании токена")


@dp.message(Command("fix_tokens"))
async def cmd_fix_tokens(message: types.Message):
    """Перегенерировать QR-токены для всех регистраций"""
    from admin_handlers import is_admin
    if not is_admin(message.from_user.id):
        await message.answer("Доступ запрещен")
        return

    try:
        registrations = await sheets_manager.get_all_records('registrations')
        fixed_count = 0

        for reg in registrations:
            if reg['status'] in ['registered', 'attended']:
                new_token = generate_qr_token(
                    reg['registration_id'],
                    reg['event_id'],
                    reg['user_id']
                )

                # Обновляем регистрацию
                await sheets_manager.update_registration(
                    reg['registration_id'],
                    {'qr_token': new_token}
                )
                fixed_count += 1

        await message.answer(f"✅ Обновлено {fixed_count} QR-токенов")

    except Exception as e:
        logger.error(f"Ошибка обновления токенов: {e}")
        await message.answer("❌ Ошибка при обновлении токенов")


@dp.message(F.text &
            ~F.text.startswith('/') &
            ~F.text.in_([
                "📋 Список событий",
                "⚫ Черный список",
                "📊 Статистика",
                "🔙 Главное меню",
                "🔗 Получить ссылку",
                "📱 Сканировать QR"
            ]))
async def handle_other_messages(message: types.Message, state: FSMContext):
    """Обработка всех остальных сообщений"""
    current_state = await state.get_state()
    if current_state is None:
        await message.answer("Пожалуйста, пользуйтесь кнопками ниже ⬇️", reply_markup=get_main_keyboard())


async def main():
    logger.info("Запуск бота...")

    try:
        scheduler = SchedulerManager(bot)
        scheduler.start()

        await dp.start_polling(bot)
    except Exception as e:
        logger.error(f"Ошибка запуска бота: {e}")
    finally:
        scheduler.shutdown()


if __name__ == "__main__":
    asyncio.run(main())