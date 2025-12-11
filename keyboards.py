from aiogram.types import InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder
from config import Config

def get_main_keyboard():
    """Создание основной клавиатуры"""
    keyboard = InlineKeyboardBuilder()
    keyboard.add(InlineKeyboardButton(text="📋 Список событий", callback_data="events_list"))
    keyboard.add(InlineKeyboardButton(text="🎫 Мой QR-код", callback_data="my_qr_code"))
    keyboard.add(InlineKeyboardButton(text="👨‍💼 Связаться с менеджером", url=Config.MANAGER_URL))
    return keyboard.as_markup()

def create_registration_keyboard(event_id):
    """Создание клавиатуры для регистрации"""
    keyboard = InlineKeyboardBuilder()
    keyboard.add(InlineKeyboardButton(
        text="🎫 Регистрация",
        callback_data=f"register_{event_id}"
    ))
    return keyboard.as_markup()

def create_reminder_keyboard(registration_id):
    """Создание клавиатуры для напоминания"""
    keyboard = InlineKeyboardBuilder()
    keyboard.add(InlineKeyboardButton(
        text="❌ Отменить регистрацию",
        callback_data=f"reminder_cancel_{registration_id}"
    ))
    return keyboard.as_markup()

def create_cancel_keyboard(registration_id):
    """Создание клавиатуры для отмены регистрации"""
    keyboard = InlineKeyboardBuilder()
    keyboard.add(InlineKeyboardButton(
        text="✅ Подтвердить отмену",
        callback_data=f"cancel_confirm_{registration_id}"
    ))
    keyboard.add(InlineKeyboardButton(
        text="❌ Отмена операции",
        callback_data="cancel_cancel"
    ))
    return keyboard.as_markup()

def create_place_offer_keyboard(registration_id):
    """Создание клавиатуры для предложения места"""
    keyboard = InlineKeyboardBuilder()
    keyboard.add(InlineKeyboardButton(
        text="✅ Занять место",
        callback_data=f"take_place_{registration_id}"
    ))
    return keyboard.as_markup()

def create_rating_keyboard(event_id):
    """Создание клавиатуры для оценки события"""
    keyboard = InlineKeyboardBuilder()
    for i in range(1, 6):
        keyboard.add(InlineKeyboardButton(
            text=str(i),
            callback_data=f"rate_{event_id}_{i}"
        ))
    return keyboard.as_markup()

def get_admin_keyboard():
    """Клавиатура для админа"""
    keyboard = ReplyKeyboardBuilder()
    keyboard.add(KeyboardButton(text="📋 Список событий"))
    keyboard.add(KeyboardButton(text="📱 Сканировать QR"))
    keyboard.add(KeyboardButton(text="⚫ Черный список"))
    keyboard.add(KeyboardButton(text="📊 Статистика"))
    keyboard.add(KeyboardButton(text="🔗 Получить ссылку"))
    keyboard.add(KeyboardButton(text="🔙 Главное меню"))
    keyboard.adjust(2)
    return keyboard.as_markup(resize_keyboard=True)

def get_user_keyboard():
    """Клавиатура для пользователя"""
    keyboard = ReplyKeyboardBuilder()
    keyboard.add(KeyboardButton(text="📋 Список событий"))
    keyboard.add(KeyboardButton(text="🎫 Мой QR-код"))
    keyboard.add(KeyboardButton(text="👨‍💼 Связаться с менеджером"))
    return keyboard.as_markup(resize_keyboard=True)