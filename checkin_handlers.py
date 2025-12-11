from aiogram import Router, types, F
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
import logging

from config import Config
from sheets import sheets_manager
from utils import verify_qr_token, is_within_checkin_window

logger = logging.getLogger(__name__)
router = Router()


@router.message(Command("start"))
async def cmd_start_deeplink(message: types.Message, command: CommandObject, state: FSMContext):
    """Обработка deeplink для чекина"""
    if not command.args or not command.args.startswith("chk_"):
        return

    await state.clear()

    try:
        # Парсим deeplink: chk_<registration_id>_<signature>
        parts = command.args.split("_")
        if len(parts) != 3:
            await message.answer("Неверная ссылка для чекина")
            return

        registration_id = parts[1]
        signature = parts[2]

        registration = await sheets_manager.get_registration(registration_id)
        if not registration:
            await message.answer("Регистрация не найдена")
            return

        # Проверяем подпись
        if not verify_qr_token(signature, registration_id, registration['event_id'], registration['user_id']):
            await message.answer("Недействительная ссылка для чекина")
            return

        # Проверяем черный список
        if await sheets_manager.is_blacklisted(registration['user_id']):
            await message.answer("Действие недоступно. Обратитесь к менеджеру.")
            return

        # Проверяем, что пользователь совпадает
        if str(registration['user_id']) != str(message.from_user.id):
            await message.answer("Этот QR-код не для вас")
            return

        # Проверяем статус регистрации
        if registration['status'] == 'cancelled':
            await message.answer("Регистрация отменена")
            return

        if registration['status'] == 'attended':
            await message.answer("Вы уже отмечены на мероприятии")
            return

        if registration['status'] == 'waitlist':
            await message.answer("Вы в списке ожидания и не можете отметить посещение")
            return

        # Проверяем окно чекина
        event = await sheets_manager.get_event(registration['event_id'])
        if not event:
            await message.answer("Событие не найдено")
            return

        if not is_within_checkin_window(event):
            await message.answer("Отметка сейчас недоступна. Чек-ин доступен за 1 час до начала и в течение 2 часов после.")
            return

        # Выполняем чек-ин
        from datetime import datetime
        await sheets_manager.update_registration_status(
            registration_id,
            'attended',
            datetime.now(sheets_manager.timezone)
        )

        await message.answer("✅ Отметка выполнена. Хорошего мероприятия!")

        logger.info(f"Пользователь {message.from_user.id} отмечен на событии {event['event_id']}")

    except Exception as e:
        logger.error(f"Ошибка обработки чекина: {e}")
        await message.answer("Ошибка при выполнении отметки")