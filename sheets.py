import gspread
from google.oauth2.service_account import Credentials
from datetime import datetime, timedelta
import pytz
from config import Config
import logging
import time
import asyncio
from typing import Dict, Any

from local_storage import local_storage

logger = logging.getLogger(__name__)


class SheetsManager:
    def __init__(self):
        self.sheets = None
        self.timezone = pytz.timezone(Config.TIMEZONE)
        self.local_storage = local_storage
        self.init_sheets()

    def init_sheets(self):
        """Инициализация подключения к Google Sheets"""
        try:
            scopes = [
                'https://www.googleapis.com/auth/spreadsheets',
                'https://www.googleapis.com/auth/drive'
            ]

            creds = Credentials.from_service_account_file(
                Config.SERVICE_ACCOUNT_JSON,
                scopes=scopes
            )
            client = gspread.authorize(creds)

            try:
                if hasattr(Config, 'SPREADSHEET_URL') and Config.SPREADSHEET_URL:
                    spreadsheet = client.open_by_url(Config.SPREADSHEET_URL)
                else:
                    spreadsheet = client.open(Config.SPREADSHEET_NAME)
            except gspread.SpreadsheetNotFound:
                logger.error(f"Таблица '{Config.SPREADSHEET_NAME}' не найдена")
                return

            self.sheets = {}
            sheet_titles = ['Events', 'Users', 'Registrations', 'Posts', 'Reminders', 'Blacklist']

            for title in sheet_titles:
                try:
                    self.sheets[title.lower()] = spreadsheet.worksheet(title)
                except gspread.WorksheetNotFound:
                    logger.warning(f"Лист '{title}' не найден")

            logger.info("Google Sheets initialized successfully")

        except Exception as e:
            logger.error(f"Ошибка инициализации Google Sheets: {e}")
            self.sheets = None

    async def sync_all_data(self):
        """Синхронизация всех данных с Google Sheets"""
        if not self.sheets:
            logger.warning("Google Sheets не доступен, пропускаем синхронизацию")
            return

        try:
            logger.info("Начало синхронизации с Google Sheets...")

            # Синхронизация событий
            events = await self.local_storage.get_all_events()
            await self._sync_to_sheet('events', events, [
                'event_id', 'title', 'description', 'start_at', 'place', 'capacity',
                'media_file_id', 'media_type', 'status', 'checkin_window_start_minutes',
                'checkin_window_end_minutes', 'created_at', 'updated_at'
            ])

            # Синхронизация пользователей
            users = await self.local_storage.get_all_users()
            await self._sync_to_sheet('users', users, [
                'user_id', 'username', 'full_name', 'created_at', 'is_blacklisted'
            ])

            # Синхронизация регистраций
            registrations = await self.local_storage.get_all_registrations()
            await self._sync_to_sheet('registrations', registrations, [
                'registration_id', 'event_id', 'user_id', 'full_name', 'status',
                'waitlist_position', 'qr_token', 'checkin_at', 'created_at', 'updated_at'
            ])

            # Синхронизация черного списка
            blacklist = await self.local_storage.get_blacklist()
            await self._sync_to_sheet('blacklist', blacklist, [
                'user_id', 'reason', 'added_by', 'added_at'
            ])

            # Синхронизация напоминаний
            reminders = await self.get_all_reminders()
            await self._sync_to_sheet('reminders', reminders, [
                'event_id', 'user_id', 'scheduled_for', 'type', 'sent_at'
            ])

            logger.info("Синхронизация с Google Sheets завершена")

        except Exception as e:
            logger.error(f"Ошибка синхронизации с Google Sheets: {e}")

    async def _sync_to_sheet(self, sheet_name: str, data: dict, headers: list):
        """Синхронизация данных в конкретный лист"""
        if sheet_name not in self.sheets:
            logger.warning(f"Лист {sheet_name} не доступен")
            return

        try:
            sheet = self.sheets[sheet_name]

            # Получаем текущие данные из Google Sheets
            current_records = sheet.get_all_records()

            # Создаем словарь для быстрого поиска
            current_dict = {}
            key_field = headers[0]  # Первое поле - ключевое

            for record in current_records:
                if key_field in record:
                    current_dict[str(record[key_field])] = record

            # Подготавливаем данные для обновления
            rows_to_update = []
            for item_id, item_data in data.items():
                # ДОБАВЛЕНО: Проверяем что item_data - словарь
                if not isinstance(item_data, dict):
                    logger.warning(f"Пропуск элемента {item_id} в {sheet_name}: неверный формат данных")
                    continue

                row_data = []
                for header in headers:
                    row_data.append(item_data.get(header, ''))
                rows_to_update.append(row_data)

            # Очищаем лист и записываем новые данные
            if rows_to_update:
                sheet.clear()
                sheet.append_row(headers)  # Заголовки
                sheet.append_rows(rows_to_update)  # Данные

            logger.info(f"Синхронизировано {len(rows_to_update)} записей в {sheet_name}")

        except Exception as e:
            logger.error(f"Ошибка синхронизации листа {sheet_name}: {e}")

    async def get_all_reminders(self) -> dict:
        """Получение всех напоминаний для синхронизации"""
        async with self.local_storage.lock:
            return self.local_storage.data['reminders'].copy()

    # Методы для обратной совместимости - теперь работают с локальным хранилищем

    async def get_event(self, event_id: str, use_cache=True):
        """ДОБАВЛЕНО: Проверка формата возвращаемого события"""
        event = await self.local_storage.get_event(event_id)
        if event and not isinstance(event, dict):
            logger.error(f"Событие {event_id} имеет неверный формат: {type(event)}")
            return None
        return event

    async def get_active_events(self):
        events = await self.local_storage.get_active_events()
        # Фильтруем только словари
        return {k: v for k, v in events.items() if isinstance(v, dict)}

    async def get_upcoming_events(self):
        events = await self.local_storage.get_upcoming_events()
        # Фильтруем только словари
        return {k: v for k, v in events.items() if isinstance(v, dict)}

    async def get_past_events(self):
        events = await self.local_storage.get_past_events()
        # Фильтруем только словари
        return {k: v for k, v in events.items() if isinstance(v, dict)}

    async def create_event(self, title: str, capacity: int, start_at, description: str = "", place: str = ""):
        event_id = await self.local_storage.get_next_event_id()

        event_data = {
            'event_id': event_id,
            'title': title,
            'description': description,
            'start_at': start_at.isoformat(),
            'place': place,
            'capacity': capacity,
            'media_file_id': '',
            'media_type': '',
            'status': 'active',
            'checkin_window_start_minutes': Config.CHECKIN_WINDOW['start'],
            'checkin_window_end_minutes': Config.CHECKIN_WINDOW['end'],
            'created_at': datetime.now(self.timezone).isoformat(),
            'updated_at': datetime.now(self.timezone).isoformat()
        }

        return await self.local_storage.create_event(event_data)

    async def update_event_media(self, event_id: str, media_file_id: str, media_type: str):
        await self.local_storage.update_event(event_id, {
            'media_file_id': media_file_id,
            'media_type': media_type
        })

    async def update_event_description(self, event_id: str, description: str):
        await self.local_storage.update_event(event_id, {
            'description': description
        })

    async def get_user(self, user_id: str):
        user_data = await self.local_storage.get_user(user_id)
        if user_data and not isinstance(user_data, dict):
            logger.error(f"Пользователь {user_id} имеет неверный формат: {type(user_data)}")
            return None
        return user_data

    async def add_user(self, user_id: str, username: str, full_name: str):
        user_data = {
            'user_id': user_id,
            'username': username or '',
            'full_name': full_name,
            'created_at': datetime.now(self.timezone).isoformat(),
            'is_blacklisted': False
        }
        await self.local_storage.add_user(user_data)

    async def update_user_fullname(self, user_id: str, full_name: str):
        await self.local_storage.update_user(user_id, {'full_name': full_name})

    async def get_registration(self, registration_id: str):
        registration = await self.local_storage.get_registration(registration_id)
        if registration and not isinstance(registration, dict):
            logger.error(f"Регистрация {registration_id} имеет неверный формат: {type(registration)}")
            return None
        return registration

    async def get_user_registration(self, user_id: str, event_id: str):
        registration = await self.local_storage.get_user_registration(user_id, event_id)
        if registration and not isinstance(registration, dict):
            logger.error(
                f"Регистрация пользователя {user_id} на событие {event_id} имеет неверный формат: {type(registration)}")
            return None
        return registration

    async def get_registrations_count(self, event_id: str, status: str = 'registered'):
        return await self.local_storage.get_registrations_count(event_id, status)

    async def get_waitlist_count(self, event_id: str):
        return await self.local_storage.get_waitlist_count(event_id)

    async def create_registration(self, user_id: str, event_id: str, full_name: str, qr_token: str):
        registration_id = await self.local_storage.get_next_registration_id()

        # Проверяем доступность мест
        event = await self.local_storage.get_event(event_id)
        if not event:
            return None, None, None

        registered_count = await self.local_storage.get_registrations_count(event_id)
        capacity = event['capacity']

        status = 'registered'
        waitlist_position = None

        if registered_count >= capacity:
            status = 'waitlist'
            waitlist_count = await self.local_storage.get_waitlist_count(event_id)
            waitlist_position = waitlist_count + 1

        registration_data = {
            'registration_id': registration_id,
            'event_id': event_id,
            'user_id': user_id,
            'full_name': full_name,
            'status': status,
            'waitlist_position': waitlist_position,
            'qr_token': qr_token,
            'checkin_at': '',
            'created_at': datetime.now(self.timezone).isoformat(),
            'updated_at': datetime.now(self.timezone).isoformat()
        }

        await self.local_storage.create_registration(registration_data)
        return registration_id, status, waitlist_position

    async def update_registration_status(self, registration_id: str, status: str, checkin_at=None):
        updates = {'status': status}
        if checkin_at:
            updates['checkin_at'] = checkin_at.isoformat()

        await self.local_storage.update_registration(registration_id, updates)

    async def cancel_registration(self, registration_id: str):
        await self.update_registration_status(registration_id, 'cancelled')

    # ДОБАВЛЕННЫЙ МЕТОД для обновления любых полей регистрации
    async def update_registration(self, registration_id: str, updates: Dict[str, Any]):
        """Обновление любых полей регистрации"""
        await self.local_storage.update_registration(registration_id, updates)

    async def is_blacklisted(self, user_id: str):
        return await self.local_storage.is_blacklisted(user_id)

    async def add_to_blacklist(self, user_id: str, reason: str, added_by: str):
        """Добавление в черный список с возвратом результата"""
        try:
            await self.local_storage.add_to_blacklist(user_id, reason, added_by)
            return True  # Явно возвращаем True при успехе
        except Exception as e:
            logger.error(f"Ошибка добавления в черный список: {e}")
            return False  # Возвращаем False при ошибке

    async def remove_from_blacklist(self, user_id: str):
        """Удаление из черного списка с возвратом результата"""
        try:
            await self.local_storage.remove_from_blacklist(user_id)
            return True  # Явно возвращаем True при успехе
        except Exception as e:
            logger.error(f"Ошибка удаления из черного списка: {e}")
            return False  # Возвращаем False при ошибке

    async def get_blacklist(self):
        return await self.local_storage.get_blacklist()

    async def get_pending_reminders(self):
        return await self.local_storage.get_pending_reminders()

    async def create_reminder(self, event_id: str, user_id: str, scheduled_for, reminder_type: str):
        reminder_data = {
            'event_id': event_id,
            'user_id': user_id,
            'scheduled_for': scheduled_for.isoformat(),
            'type': reminder_type,
            'sent_at': ''
        }
        await self.local_storage.create_reminder(reminder_data)

    async def mark_reminder_sent(self, reminder_data: dict):
        reminder_id = await self.local_storage.get_reminder_by_data(
            reminder_data['event_id'],
            reminder_data['user_id'],
            reminder_data['type']
        )
        if reminder_id:
            await self.local_storage.mark_reminder_sent(reminder_id)

    async def get_all_records(self, sheet_name: str):
        """Получение всех записей (для обратной совместимости)"""
        if sheet_name == 'events':
            events = await self.local_storage.get_all_events()
            # Фильтруем только словари
            return [v for v in events.values() if isinstance(v, dict)]
        elif sheet_name == 'users':
            users = await self.local_storage.get_all_users()
            # Фильтруем только словари
            return [v for v in users.values() if isinstance(v, dict)]
        elif sheet_name == 'registrations':
            registrations = await self.local_storage.get_all_registrations()
            # Фильтруем только словари
            return [v for v in registrations.values() if isinstance(v, dict)]
        elif sheet_name == 'blacklist':
            blacklist = await self.local_storage.get_blacklist()
            # Фильтруем только словари
            return [v for v in blacklist.values() if isinstance(v, dict)]
        elif sheet_name == 'reminders':
            reminders = await self.get_all_reminders()
            # Фильтруем только словари
            return [v for v in reminders.values() if isinstance(v, dict)]
        return []

    async def get_all_records_dict(self, sheet_name: str, key_column: str = None):
        records = await self.get_all_records(sheet_name)
        if key_column:
            return {str(record[key_column]): record for record in records if isinstance(record, dict)}
        return records


# Глобальный экземпляр менеджера
sheets_manager = SheetsManager()