import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    BOT_TOKEN = os.getenv('BOT_TOKEN')
    SECRET_KEY = os.getenv('SECRET_KEY', 'default_secret_key')
    SERVICE_ACCOUNT_JSON = "creds.json"
    SPREADSHEET_NAME = "EventBot_Registry"
    SPREADSHEET_URL = os.getenv('SPREADSHEET_URL', '')

    # Администраторы (заменить на реальные user_id)
    ADMIN_IDS = ['1458704301', '1070944210']

    # Менеджер для связи
    MANAGER_USERNAME = os.getenv('MANAGER_USERNAME', 'manager')  # ← ДОБАВЛЕНО
    MANAGER_URL = f"https://t.me/{MANAGER_USERNAME}"   # ← ДОБАВЛЕНО

    # Временные настройки
    TIMEZONE = 'Europe/Moscow'
    REMINDER_TIMES = {
        'day_before': 24 * 60,  # за 1 день (в минутах)
        'six_hours': 6 * 60,  # за 6 часов
        'one_hour': 60,  # за 1 час
    }
    GRACE_PERIOD = 2 * 60  # 2 часа в минутах для неявки и благодарностей
    PLACE_HOLD_TIME = 15  # 15 минут удержания места

    # Окно чекина (в минутах относительно начала события)
    CHECKIN_WINDOW = {
        'start': -60,  # за 60 минут до начала
        'end': 120  # через 120 минут после начала
    }