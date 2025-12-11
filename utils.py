from datetime import datetime, timedelta
import pytz
from config import Config
import logging
import re
import hashlib
import hmac
import base64
import qrcode
from io import BytesIO

logger = logging.getLogger(__name__)
timezone = pytz.timezone(Config.TIMEZONE)


def format_event_post(event_data):
    """Форматирование поста события"""
    title = event_data.get('title', f"Событие {event_data['event_id']}")
    start_at = datetime.fromisoformat(event_data['start_at'])

    # Форматирование даты
    months = {
        1: 'января', 2: 'февраля', 3: 'марта', 4: 'апреля',
        5: 'мая', 6: 'июня', 7: 'июля', 8: 'августа',
        9: 'сентября', 10: 'октября', 11: 'ноября', 12: 'декабря'
    }
    formatted_date = f"{start_at.day} {months[start_at.month]} {start_at.year}, {start_at.hour:02d}:{start_at.minute:02d} (MSK)"

    post = f"**{title}**\n\n"
    post += f"🗓 {formatted_date}\n"

    if event_data.get('place'):
        post += f"📍 {event_data['place']}\n"

    if event_data.get('description'):
        post += f"\n{event_data['description']}\n"

    return post


def validate_fullname(fullname):
    """Валидация ФИО"""
    if not fullname or len(fullname) < 3 or len(fullname) > 100:
        return False

    words = fullname.split()
    if len(words) < 2:
        return False

    # Проверяем, что это текст (без специальных символов, кроме дефиса и апострофа)
    if not re.match(r'^[a-zA-Zа-яА-ЯёЁ\s\-\']+$', fullname):
        return False

    return True


def parse_date(date_str):
    """Парсинг даты из формата ДД-ММ-ГГГГ-ЧЧ:ММ"""
    try:
        dt = datetime.strptime(date_str, '%d-%m-%Y-%H:%M')
        return timezone.localize(dt)
    except ValueError as e:
        logger.error(f"Ошибка парсинга даты {date_str}: {e}")
        return None


def generate_qr_token(registration_id, event_id, user_id):
    """Генерация подписи для QR-кода с детальным логированием"""
    try:
        logger.info(f"Генерация токена для: reg_id={registration_id}, event_id={event_id}, user_id={user_id}")
        logger.info(f"Типы данных: reg_id={type(registration_id)}, event_id={type(event_id)}, user_id={type(user_id)}")

        # ИСПРАВЛЕНИЕ: Приводим все к строке и убираем лишние символы
        data = f"{str(registration_id).strip()}_{str(event_id).strip()}_{str(user_id).strip()}".encode()
        logger.info(f"Данные для подписи: {data}")

        signature = hmac.new(
            Config.SECRET_KEY.encode(),
            data,
            hashlib.sha256
        ).digest()

        # ИСПРАВЛЕНИЕ: Используем другой способ кодирования
        token = base64.urlsafe_b64encode(signature).decode('utf-8')[:16].replace('=', '').replace('_', '').replace('-', '')
        logger.info(f"Сгенерированный токен: {token}")
        return token
    except Exception as e:
        logger.error(f"Ошибка генерации QR-токена: {e}")
        return "default_token"


def verify_qr_token(token, registration_id, event_id, user_id):
    """Проверка валидности QR-токена с детальным логированием"""
    try:
        logger.info(f"Проверка токена: {token}")
        logger.info(f"Параметры проверки: reg_id={registration_id}, event_id={event_id}, user_id={user_id}")

        expected_token = generate_qr_token(registration_id, event_id, user_id)
        logger.info(f"Ожидаемый токен: {expected_token}")

        result = hmac.compare_digest(token, expected_token)
        logger.info(f"Результат проверки: {result}")

        return result
    except Exception as e:
        logger.error(f"Ошибка проверки QR-токена: {e}")
        return False


def generate_qr_code_image(qr_data):
    """Генерация изображения QR-кода"""
    try:
        qr = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_L,
            box_size=12,  # Увеличиваем размер для лучшей читаемости
            border=4,
        )
        qr.add_data(qr_data)
        qr.make(fit=True)

        img = qr.make_image(fill_color="black", back_color="white")
        bio = BytesIO()
        img.save(bio, 'PNG', quality=100)
        bio.seek(0)
        return bio
    except Exception as e:
        logger.error(f"Ошибка генерации QR-кода: {e}")
        return None


def is_within_checkin_window(event_data):
    """Проверка, находится ли текущее время в окне чекина с логированием"""
    try:
        now = datetime.now(timezone)
        start_at = datetime.fromisoformat(event_data['start_at'])

        checkin_start = start_at + timedelta(minutes=event_data.get('checkin_window_start_minutes', -60))
        checkin_end = start_at + timedelta(minutes=event_data.get('checkin_window_end_minutes', 120))

        result = checkin_start <= now <= checkin_end

        logger.info(f"Проверка временного окна для события {event_data.get('event_id', 'unknown')}:")
        logger.info(f"  Начало события: {start_at}")
        logger.info(f"  Окно чекина: {checkin_start} - {checkin_end}")
        logger.info(f"  Текущее время: {now}")
        logger.info(f"  Результат: {'В окне' if result else 'Вне окна'}")

        return result

    except Exception as e:
        logger.error(f"Ошибка проверки временного окна: {e}")
        return False


def calculate_reminder_times(start_at):
    """Расчет времени напоминаний"""
    start_dt = datetime.fromisoformat(start_at) if isinstance(start_at, str) else start_at

    return {
        'day_before': start_dt - timedelta(days=1),
        'six_hours': start_dt - timedelta(hours=6),
        'one_hour': start_dt - timedelta(hours=1),
        'no_show': start_dt + timedelta(hours=2),
        'thanks': start_dt + timedelta(hours=2)
    }