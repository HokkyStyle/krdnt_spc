import asyncio
import logging
from datetime import datetime, timedelta
from aiogram import Bot
from aiogram.types import InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

from config import Config
from sheets import sheets_manager
from keyboards import create_registration_keyboard
from user_manager import user_manager

logger = logging.getLogger(__name__)


async def broadcast_event(event_id: str, bot: Bot, max_retries: int = 3):
    """Рассылка события всем пользователям из JSON с повторными попытками"""
    for attempt in range(max_retries):
        try:
            logger.info(f"Попытка {attempt + 1} рассылки для события {event_id}")

            # Небольшая задержка между попытками
            if attempt > 0:
                await asyncio.sleep(2)

            event = await sheets_manager.get_event(event_id)

            # РАСШИРЕННАЯ ДИАГНОСТИКА: логируем тип и содержимое события
            logger.info(f"Тип события: {type(event)}, содержимое: {event}")

            if not event:
                logger.warning(f"Событие {event_id} не найдено (попытка {attempt + 1})")
                if attempt == max_retries - 1:
                    logger.error(f"Событие {event_id} не найдено после {max_retries} попыток")
                    return
                continue

            # ВАЖНОЕ ИСПРАВЛЕНИЕ: Проверяем что event - словарь, а не строка
            if not isinstance(event, dict):
                logger.error(f"Событие {event_id} имеет неверный тип: {type(event)}. Ожидался dict.")
                if attempt == max_retries - 1:
                    logger.error(f"Событие {event_id} имеет неверный формат после {max_retries} попыток")
                    return
                continue

            # Проверяем, что событие еще не прошло
            try:
                start_at = datetime.fromisoformat(event['start_at'])
                if start_at < datetime.now(sheets_manager.timezone) - timedelta(hours=2):
                    logger.info(f"Событие {event_id} уже прошло, рассылка отменена")
                    return
            except (ValueError, KeyError) as e:
                logger.error(f"Ошибка парсинга даты события {event_id}: {e}")
                return

            # Получаем текст поста и медиа
            post_text = event.get('description', f"**{event['title']}**")
            media_file_id = event.get('media_file_id')
            media_type = event.get('media_type')

            # ИСПРАВЛЕНИЕ: Получаем пользователей и правильно обрабатываем структуру данных
            users_data = user_manager.get_all_users()

            # Обрабатываем разные форматы данных пользователей
            if isinstance(users_data, dict):
                # Если это словарь {user_id: user_data}
                users_to_process = list(users_data.values())
                logger.info(f"Получено {len(users_to_process)} пользователей из словаря")
            elif isinstance(users_data, list):
                # Если это список [user_data, user_data, ...]
                users_to_process = users_data
                logger.info(f"Получено {len(users_to_process)} пользователей из списка")
            else:
                logger.error(f"Неизвестный формат данных пользователей: {type(users_data)}")
                users_to_process = []

            keyboard = create_registration_keyboard(event_id)

            success_count = 0
            failed_count = 0

            for user_data in users_to_process:
                try:
                    # ИСПРАВЛЕНИЕ: Правильно извлекаем user_id из структуры данных
                    if isinstance(user_data, dict):
                        user_id = user_data.get('user_id')
                        if not user_id:
                            logger.warning("Пропуск пользователя без user_id")
                            continue
                    else:
                        logger.warning(f"Пропуск невалидного пользователя: {type(user_data)}")
                        continue

                    # Проверяем черный список (из Google Sheets)
                    if await sheets_manager.is_blacklisted(user_id):
                        logger.debug(f"Пользователь {user_id} в черном списке, пропускаем")
                        continue

                    # Отправляем медиа или текст в зависимости от типа контента
                    if media_file_id and media_type:
                        if media_type == 'photo':
                            await bot.send_photo(
                                user_id,
                                photo=media_file_id,
                                caption=post_text,
                                reply_markup=keyboard,
                                parse_mode="Markdown"
                            )
                        elif media_type == 'video':
                            await bot.send_video(
                                user_id,
                                video=media_file_id,
                                caption=post_text,
                                reply_markup=keyboard,
                                parse_mode="Markdown"
                            )
                        elif media_type == 'document':
                            await bot.send_document(
                                user_id,
                                document=media_file_id,
                                caption=post_text,
                                reply_markup=keyboard,
                                parse_mode="Markdown"
                            )
                        else:
                            # Неизвестный тип медиа, отправляем текст
                            await bot.send_message(
                                user_id,
                                post_text,
                                reply_markup=keyboard,
                                parse_mode="Markdown"
                            )
                    else:
                        # Отправляем только текст
                        await bot.send_message(
                            user_id,
                            post_text,
                            reply_markup=keyboard,
                            parse_mode="Markdown"
                        )

                    success_count += 1
                    await asyncio.sleep(0.05)  # Rate limiting

                except Exception as e:
                    failed_count += 1
                    logger.error(f"Ошибка отправки пользователю {user_id}: {e}")

            logger.info(f"Рассылка события {event_id} завершена: {success_count} успешно, {failed_count} ошибок")
            return  # Успешно завершили рассылку

        except Exception as e:
            logger.error(f"Критическая ошибка рассылки события {event_id} (попытка {attempt + 1}): {e}")
            if attempt == max_retries - 1:
                logger.error(f"Не удалось выполнить рассылку события {event_id} после {max_retries} попыток")


async def broadcast_message(message_text: str, bot: Bot, include_keyboard: bool = False, event_id: str = None):
    """Общая функция рассылки сообщения всем пользователям из JSON"""
    try:
        users_data = user_manager.get_all_users()

        # Обрабатываем разные форматы данных пользователей
        if isinstance(users_data, dict):
            users_to_process = list(users_data.values())
        elif isinstance(users_data, list):
            users_to_process = users_data
        else:
            logger.error(f"Неизвестный формат данных пользователей: {type(users_data)}")
            users_to_process = []

        logger.info(f"Начинаю рассылку сообщения для {len(users_to_process)} пользователей")

        keyboard = None
        if include_keyboard and event_id:
            keyboard = create_registration_keyboard(event_id)

        success_count = 0
        failed_count = 0

        for user_data in users_to_process:
            try:
                if isinstance(user_data, dict):
                    user_id = user_data.get('user_id')
                    if not user_id:
                        continue
                else:
                    continue

                # Проверяем черный список
                if await sheets_manager.is_blacklisted(user_id):
                    continue

                if keyboard:
                    await bot.send_message(
                        user_id,
                        message_text,
                        reply_markup=keyboard,
                        parse_mode="Markdown"
                    )
                else:
                    await bot.send_message(
                        user_id,
                        message_text,
                        parse_mode="Markdown"
                    )

                success_count += 1
                await asyncio.sleep(0.05)  # Rate limiting

            except Exception as e:
                failed_count += 1
                logger.error(f"Ошибка отправки сообщения пользователю {user_id}: {e}")

        logger.info(f"Рассылка сообщения завершена: {success_count} успешно, {failed_count} ошибок")
        return success_count, failed_count

    except Exception as e:
        logger.error(f"Критическая ошибка рассылки сообщения: {e}")
        return 0, 0