# user_manager.py
import json
import os
import logging
from typing import List, Set

logger = logging.getLogger(__name__)

USERS_JSON_FILE = 'users.json'


class UserManager:
    def __init__(self):
        self.users_file = USERS_JSON_FILE
        self._ensure_users_file()

    def _ensure_users_file(self):
        """Создает файл users.json если он не существует"""
        if not os.path.exists(self.users_file):
            with open(self.users_file, 'w', encoding='utf-8') as f:
                json.dump([], f, ensure_ascii=False, indent=2)
            logger.info(f"Создан файл {self.users_file}")

    def add_user(self, user_id: int, username: str = "", full_name: str = ""):
        """Добавление пользователя в JSON"""
        try:
            users = self.get_all_users()

            # Нормализуем username (убираем @ если есть)
            normalized_username = username.lstrip('@') if username else ""

            # Проверяем, есть ли уже такой пользователь
            user_exists = any(user['user_id'] == user_id for user in users)

            if not user_exists:
                user_data = {
                    'user_id': user_id,
                    'username': normalized_username,
                    'full_name': full_name or '',
                    'added_at': self._get_current_timestamp()
                }
                users.append(user_data)

                with open(self.users_file, 'w', encoding='utf-8') as f:
                    json.dump(users, f, ensure_ascii=False, indent=2)

                logger.info(f"Пользователь {user_id} (@{normalized_username}) добавлен в JSON")
                return True
            return False

        except Exception as e:
            logger.error(f"Ошибка добавления пользователя в JSON: {e}")
            return False

    def get_all_users(self) -> List[dict]:
        """Получение всех пользователей из JSON"""
        try:
            with open(self.users_file, 'r', encoding='utf-8') as f:
                users = json.load(f)
            return users
        except Exception as e:
            logger.error(f"Ошибка чтения users.json: {e}")
            return []

    def get_user_ids(self) -> List[int]:
        """Получение только ID пользователей"""
        users = self.get_all_users()
        return [user['user_id'] for user in users]

    def remove_user(self, user_id: int) -> bool:
        """Удаление пользователя из JSON"""
        try:
            users = self.get_all_users()
            initial_count = len(users)

            users = [user for user in users if user['user_id'] != user_id]

            if len(users) < initial_count:
                with open(self.users_file, 'w', encoding='utf-8') as f:
                    json.dump(users, f, ensure_ascii=False, indent=2)

                logger.info(f"Пользователь {user_id} удален из JSON")
                return True
            return False

        except Exception as e:
            logger.error(f"Ошибка удаления пользователя из JSON: {e}")
            return False

    def get_user_count(self) -> int:
        """Получение количества пользователей"""
        users = self.get_all_users()
        return len(users)

    def _get_current_timestamp(self) -> str:
        """Получение текущего времени в формате строки"""
        from datetime import datetime
        return datetime.now().isoformat()

    def update_user_info(self, user_id: int, username: str = None, full_name: str = None):
        """Обновление информации о пользователе"""
        try:
            users = self.get_all_users()
            updated = False

            for user in users:
                if user['user_id'] == user_id:
                    if username is not None:
                        user['username'] = username
                    if full_name is not None:
                        user['full_name'] = full_name
                    updated = True
                    break

            if updated:
                with open(self.users_file, 'w', encoding='utf-8') as f:
                    json.dump(users, f, ensure_ascii=False, indent=2)
                logger.info(f"Информация о пользователе {user_id} обновлена")

            return updated

        except Exception as e:
            logger.error(f"Ошибка обновления информации о пользователе: {e}")
            return False


# Глобальный экземпляр менеджера пользователей
user_manager = UserManager()