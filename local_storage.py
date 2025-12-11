import json
import os
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional
import asyncio
import pytz

from config import Config

logger = logging.getLogger(__name__)


class LocalStorage:
    def __init__(self):
        self.data = {
            'events': {},
            'users': {},
            'registrations': {},
            'blacklist': {},
            'reminders': {},
            'posts': {}
        }
        self.lock = asyncio.Lock()
        self.timezone = pytz.timezone(Config.TIMEZONE)
        self.load_all()

    def load_all(self):
        """Загрузка всех данных из локальных файлов с улучшенной обработкой ошибок"""
        for data_type in self.data.keys():
            try:
                if os.path.exists(f'{data_type}.json'):
                    with open(f'{data_type}.json', 'r', encoding='utf-8') as f:
                        loaded_data = json.load(f)

                    # ДОБАВЛЕНО: Проверка и исправление формата данных
                    if isinstance(loaded_data, dict):
                        # Это правильный формат - словарь
                        self.data[data_type] = loaded_data
                    elif isinstance(loaded_data, list):
                        # Преобразуем список в словарь если нужно
                        self.data[data_type] = {}
                        for item in loaded_data:
                            if isinstance(item, dict) and 'event_id' in item:
                                self.data[data_type][item['event_id']] = item
                            elif isinstance(item, dict) and 'user_id' in item:
                                self.data[data_type][item['user_id']] = item
                            elif isinstance(item, dict) and 'registration_id' in item:
                                self.data[data_type][item['registration_id']] = item
                            else:
                                logger.warning(f"Пропуск элемента в {data_type}.json: неподдерживаемый формат")
                    else:
                        # Неизвестный формат - создаем пустой словарь
                        logger.error(f"Неверный формат данных в {data_type}.json: {type(loaded_data)}")
                        self.data[data_type] = {}

                    logger.info(f"Загружены {len(self.data[data_type])} записей {data_type}")

                    # ДОБАВЛЕНО: Проверка качества данных
                    self._validate_data(data_type)

                else:
                    logger.info(f"Файл {data_type}.json не найден, создаем пустой")
                    self.data[data_type] = {}
            except Exception as e:
                logger.error(f"Ошибка загрузки {data_type}: {e}")
                self.data[data_type] = {}

    def _validate_data(self, data_type: str):
        """Проверка качества данных"""
        invalid_count = 0
        for key, value in list(self.data[data_type].items()):
            if not isinstance(value, dict):
                logger.warning(f"Удаление неверного элемента {key} из {data_type}: {type(value)}")
                del self.data[data_type][key]
                invalid_count += 1

        if invalid_count > 0:
            logger.warning(f"Удалено {invalid_count} неверных элементов из {data_type}")
            self.save_locally(data_type)

    def save_locally(self, data_type: str):
        """Сохранение конкретного типа данных в файл"""
        try:
            # ДОБАВЛЕНО: Проверка что сохраняем словарь
            if not isinstance(self.data[data_type], dict):
                logger.error(f"Попытка сохранить не словарь в {data_type}.json: {type(self.data[data_type])}")
                return

            with open(f'{data_type}.json', 'w', encoding='utf-8') as f:
                json.dump(self.data[data_type], f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"Ошибка сохранения {data_type}: {e}")

    def save_all(self):
        """Сохранение всех данных"""
        for data_type in self.data.keys():
            self.save_locally(data_type)

    # Events methods
    async def get_event(self, event_id: str) -> Optional[Dict[str, Any]]:
        async with self.lock:
            event = self.data['events'].get(str(event_id))
            # ДОБАВЛЕНО: Проверка типа
            if event and not isinstance(event, dict):
                logger.error(f"Событие {event_id} имеет неверный тип: {type(event)}")
                return None
            return event

    async def get_all_events(self) -> Dict[str, Dict]:
        async with self.lock:
            # Фильтруем только словари
            return {k: v for k, v in self.data['events'].items() if isinstance(v, dict)}

    async def get_active_events(self) -> Dict[str, Dict]:
        """Получение активных событий"""
        async with self.lock:
            now = datetime.now(self.timezone)
            active_events = {}

            for event_id, event in self.data['events'].items():
                # ДОБАВЛЕНО: Проверка типа события
                if not isinstance(event, dict):
                    continue

                if event.get('status') == 'active':
                    try:
                        start_at = datetime.fromisoformat(event['start_at'])
                        # Считаем активными события, которые еще не прошли более 2 часов
                        if start_at > now - timedelta(hours=2):
                            active_events[event_id] = event
                    except (ValueError, KeyError):
                        continue
            return active_events

    async def get_upcoming_events(self) -> Dict[str, Dict]:
        """Получение будущих событий (для пользователей)"""
        async with self.lock:
            now = datetime.now(self.timezone)
            upcoming_events = {}

            for event_id, event in self.data['events'].items():
                # ДОБАВЛЕНО: Проверка типа события
                if not isinstance(event, dict):
                    continue

                if event.get('status') == 'active':
                    try:
                        start_at = datetime.fromisoformat(event['start_at'])
                        if start_at > now:
                            upcoming_events[event_id] = event
                    except (ValueError, KeyError):
                        continue
            return upcoming_events

    async def get_past_events(self) -> Dict[str, Dict]:
        """Получение прошедших событий"""
        async with self.lock:
            now = datetime.now(self.timezone)
            past_events = {}

            for event_id, event in self.data['events'].items():
                # ДОБАВЛЕНО: Проверка типа события
                if not isinstance(event, dict):
                    continue

                if event.get('status') == 'active':
                    try:
                        start_at = datetime.fromisoformat(event['start_at'])
                        if start_at <= now - timedelta(hours=2):
                            past_events[event_id] = event
                    except (ValueError, KeyError):
                        continue
            return past_events

    async def create_event(self, event_data: Dict[str, Any]) -> str:
        async with self.lock:
            event_id = event_data['event_id']
            event_data['created_at'] = datetime.now(self.timezone).isoformat()
            event_data['updated_at'] = datetime.now(self.timezone).isoformat()
            self.data['events'][event_id] = event_data
            self.save_locally('events')
            logger.info(f"Создано событие {event_id} в локальном хранилище")
            return event_id

    async def update_event(self, event_id: str, updates: Dict[str, Any]):
        async with self.lock:
            if event_id in self.data['events']:
                # ДОБАВЛЕНО: Проверка что обновляем словарь
                if isinstance(self.data['events'][event_id], dict):
                    self.data['events'][event_id].update(updates)
                    self.data['events'][event_id]['updated_at'] = datetime.now(self.timezone).isoformat()
                    self.save_locally('events')
                else:
                    logger.error(
                        f"Попытка обновить не словарь события {event_id}: {type(self.data['events'][event_id])}")

    async def get_next_event_id(self) -> str:
        """Генерация следующего ID события"""
        async with self.lock:
            if not self.data['events']:
                return "001"

            max_id = 0
            for event_id in self.data['events'].keys():
                try:
                    if event_id.isdigit():
                        max_id = max(max_id, int(event_id))
                except ValueError:
                    continue

            return f"{max_id + 1:03d}"

    # Users methods
    async def get_user(self, user_id: str) -> Optional[Dict[str, Any]]:
        async with self.lock:
            user = self.data['users'].get(str(user_id))
            # ДОБАВЛЕНО: Проверка типа
            if user and not isinstance(user, dict):
                logger.error(f"Пользователь {user_id} имеет неверный тип: {type(user)}")
                return None
            return user

    async def get_all_users(self) -> Dict[str, Dict]:
        async with self.lock:
            # Фильтруем только словари
            return {k: v for k, v in self.data['users'].items() if isinstance(v, dict)}

    async def add_user(self, user_data: Dict[str, Any]):
        async with self.lock:
            user_id = str(user_data['user_id'])
            if user_id not in self.data['users']:
                user_data['created_at'] = datetime.now(self.timezone).isoformat()
                user_data['is_blacklisted'] = False
                self.data['users'][user_id] = user_data
                self.save_locally('users')
                logger.info(f"Добавлен пользователь {user_id} в локальное хранилище")

    async def update_user(self, user_id: str, updates: Dict[str, Any]):
        async with self.lock:
            user_id = str(user_id)
            if user_id in self.data['users']:
                # ДОБАВЛЕНО: Проверка что обновляем словарь
                if isinstance(self.data['users'][user_id], dict):
                    self.data['users'][user_id].update(updates)
                    self.save_locally('users')
                else:
                    logger.error(
                        f"Попытка обновить не словарь пользователя {user_id}: {type(self.data['users'][user_id])}")

    # Registrations methods
    async def get_registration(self, registration_id: str) -> Optional[Dict[str, Any]]:
        async with self.lock:
            registration = self.data['registrations'].get(str(registration_id))
            # ДОБАВЛЕНО: Проверка типа
            if registration and not isinstance(registration, dict):
                logger.error(f"Регистрация {registration_id} имеет неверный тип: {type(registration)}")
                return None
            return registration

    async def get_all_registrations(self) -> Dict[str, Dict]:
        async with self.lock:
            # Фильтруем только словари
            return {k: v for k, v in self.data['registrations'].items() if isinstance(v, dict)}

    async def get_user_registration(self, user_id: str, event_id: str) -> Optional[Dict[str, Any]]:
        async with self.lock:
            user_id = str(user_id)
            event_id = str(event_id)

            for reg in self.data['registrations'].values():
                # ДОБАВЛЕНО: Проверка типа регистрации
                if not isinstance(reg, dict):
                    continue

                if (str(reg.get('user_id')) == user_id and
                        str(reg.get('event_id')) == event_id and
                        reg.get('status') in ['registered', 'waitlist', 'attended']):
                    return reg
            return None

    async def get_registrations_count(self, event_id: str, status: str = 'registered') -> int:
        async with self.lock:
            event_id = str(event_id)
            count = 0
            for reg in self.data['registrations'].values():
                # ДОБАВЛЕНО: Проверка типа регистрации
                if not isinstance(reg, dict):
                    continue

                if str(reg.get('event_id')) == event_id and reg.get('status') == status:
                    count += 1
            return count

    async def get_waitlist_count(self, event_id: str) -> int:
        return await self.get_registrations_count(event_id, 'waitlist')

    async def create_registration(self, registration_data: Dict[str, Any]) -> str:
        async with self.lock:
            registration_id = str(registration_data['registration_id'])
            registration_data['created_at'] = datetime.now(self.timezone).isoformat()
            registration_data['updated_at'] = datetime.now(self.timezone).isoformat()
            self.data['registrations'][registration_id] = registration_data
            self.save_locally('registrations')
            logger.info(f"Создана регистрация {registration_id} в локальном хранилище")
            return registration_id

    async def update_registration(self, registration_id: str, updates: Dict[str, Any]):
        async with self.lock:
            registration_id = str(registration_id)
            if registration_id in self.data['registrations']:
                # ДОБАВЛЕНО: Проверка что обновляем словарь
                if isinstance(self.data['registrations'][registration_id], dict):
                    self.data['registrations'][registration_id].update(updates)
                    self.data['registrations'][registration_id]['updated_at'] = datetime.now(self.timezone).isoformat()
                    self.save_locally('registrations')
                else:
                    logger.error(
                        f"Попытка обновить не словарь регистрации {registration_id}: {type(self.data['registrations'][registration_id])}")

    async def get_next_registration_id(self) -> int:
        """Генерация следующего ID регистрации"""
        async with self.lock:
            if not self.data['registrations']:
                return 1

            max_id = 0
            for reg_id in self.data['registrations'].keys():
                try:
                    max_id = max(max_id, int(reg_id))
                except ValueError:
                    continue

            return max_id + 1

    # Blacklist methods
    async def is_blacklisted(self, user_id: str) -> bool:
        async with self.lock:
            return str(user_id) in self.data['blacklist']

    async def add_to_blacklist(self, user_id: str, reason: str, added_by: str):
        async with self.lock:
            try:
                user_id = str(user_id)
                self.data['blacklist'][user_id] = {
                    'user_id': user_id,
                    'reason': reason,
                    'added_by': added_by,
                    'added_at': datetime.now(self.timezone).isoformat()
                }
                self.save_locally('blacklist')
                logger.info(f"Пользователь {user_id} добавлен в черный список")
                return True
            except Exception as e:
                logger.error(f"Ошибка добавления в черный список в local_storage: {e}")
                raise

    async def remove_from_blacklist(self, user_id: str):
        async with self.lock:
            user_id = str(user_id)
            if user_id in self.data['blacklist']:
                del self.data['blacklist'][user_id]
                self.save_locally('blacklist')
                logger.info(f"Пользователь {user_id} удален из черного списка")

    async def get_blacklist(self) -> Dict[str, Dict]:
        async with self.lock:
            # Фильтруем только словари
            return {k: v for k, v in self.data['blacklist'].items() if isinstance(v, dict)}

    # Reminders methods
    async def create_reminder(self, reminder_data: Dict[str, Any]):
        async with self.lock:
            reminder_id = f"{reminder_data['event_id']}_{reminder_data['user_id']}_{reminder_data['type']}"
            self.data['reminders'][reminder_id] = reminder_data
            self.save_locally('reminders')

    async def get_user_active_registrations(self, user_id: str) -> List[Dict[str, Any]]:
        """Получение активных регистраций пользователя"""
        async with self.lock:
            user_id = str(user_id)
            active_registrations = []

            for reg_id, reg_data in self.data['registrations'].items():
                # ДОБАВЛЕНО: Проверка типа регистрации
                if not isinstance(reg_data, dict):
                    continue

                if (str(reg_data.get('user_id')) == user_id and
                        reg_data.get('status') in ['registered', 'attended']):
                    active_registrations.append(reg_data)

            return active_registrations

    async def find_user_registrations(self, user_id: str) -> List[Dict[str, Any]]:
        """Поиск всех регистраций пользователя с детальным логированием"""
        async with self.lock:
            user_id_str = str(user_id)
            found_registrations = []

            logger.info(f"Поиск регистраций для user_id: {user_id_str}")
            logger.info(f"Всего регистраций в системе: {len(self.data['registrations'])}")

            for reg_id, reg_data in self.data['registrations'].items():
                # ДОБАВЛЕНО: Проверка типа регистрации
                if not isinstance(reg_data, dict):
                    logger.warning(f"Регистрация {reg_id} имеет неверный тип: {type(reg_data)}")
                    continue

                reg_user_id = reg_data.get('user_id')
                reg_status = reg_data.get('status')

                # Логируем все регистрации для отладки
                logger.info(
                    f"Регистрация {reg_id}: user_id={reg_user_id} (тип: {type(reg_user_id)}), status={reg_status}")

                # Сравниваем как строки для надежности
                if str(reg_user_id) == user_id_str:
                    logger.info(f"Найдена регистрация пользователя: {reg_id}")
                    found_registrations.append(reg_data)

            logger.info(f"Итог поиска: найдено {len(found_registrations)} регистраций")
            return found_registrations

    async def get_pending_reminders(self) -> List[Dict[str, Any]]:
        async with self.lock:
            now = datetime.now(self.timezone)
            pending = []

            for reminder in self.data['reminders'].values():
                # ДОБАВЛЕНО: Проверка типа напоминания
                if not isinstance(reminder, dict):
                    continue

                if not reminder.get('sent_at'):
                    try:
                        scheduled_for = datetime.fromisoformat(reminder['scheduled_for'])
                        if scheduled_for <= now:
                            pending.append(reminder)
                    except (ValueError, KeyError):
                        continue
            return pending

    async def mark_reminder_sent(self, reminder_id: str):
        async with self.lock:
            if reminder_id in self.data['reminders']:
                # ДОБАВЛЕНО: Проверка типа напоминания
                if isinstance(self.data['reminders'][reminder_id], dict):
                    self.data['reminders'][reminder_id]['sent_at'] = datetime.now(self.timezone).isoformat()
                    self.save_locally('reminders')
                else:
                    logger.error(
                        f"Попытка обновить не словарь напоминания {reminder_id}: {type(self.data['reminders'][reminder_id])}")

    async def get_reminder_by_data(self, event_id: str, user_id: str, reminder_type: str) -> Optional[str]:
        """Получение ID напоминания по данным"""
        async with self.lock:
            target_id = f"{event_id}_{user_id}_{reminder_type}"
            for reminder_id, reminder in self.data['reminders'].items():
                # ДОБАВЛЕНО: Проверка типа напоминания
                if not isinstance(reminder, dict):
                    continue

                if (reminder.get('event_id') == event_id and
                        str(reminder.get('user_id')) == str(user_id) and
                        reminder.get('type') == reminder_type):
                    return reminder_id
            return None


# Глобальный экземпляр
local_storage = LocalStorage()