import asyncio
from datetime import datetime, timedelta
import pytz
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from aiogram import Bot
from config import Config
from sheets import sheets_manager
from keyboards import create_reminder_keyboard, create_place_offer_keyboard, create_rating_keyboard
import logging

from utils import timezone

logger = logging.getLogger(__name__)


class SchedulerManager:
    def __init__(self, bot: Bot):
        self.bot = bot
        self.scheduler = AsyncIOScheduler(timezone=Config.TIMEZONE)
        self.setup_scheduler()

    def setup_scheduler(self):
        """Настройка планировщика"""
        # Запуск каждую минуту для проверки напоминаний
        self.scheduler.add_job(
            self.process_reminders,
            'cron',
            minute='*',
            id='reminders_check'
        )

        # Запуск каждые 5 минут для обработки очередей
        self.scheduler.add_job(
            self.process_waitlist,
            'cron',
            minute='*/5',
            id='waitlist_check'
        )

        # Запуск каждый час для обработки неявок и благодарностей
        self.scheduler.add_job(
            self.process_attendance_followup,
            'cron',
            hour='*',
            id='attendance_check'
        )

        # Синхронизация с Google Sheets каждую минуту
        self.scheduler.add_job(
            self.sync_with_sheets,
            'cron',
            minute='*',
            id='sheets_sync'
        )

        logger.info("Планировщик настроен")

    async def sync_with_sheets(self):
        """Задача синхронизации с Google Sheets"""
        try:
            await sheets_manager.sync_all_data()
            logger.info("Периодическая синхронизация с Google Sheets выполнена")
        except Exception as e:
            logger.error(f"Ошибка периодической синхронизации: {e}")

    async def process_reminders(self):
        """Обработка ожидающих напоминаний"""
        try:
            reminders = await sheets_manager.get_pending_reminders()
            logger.info(f"Найдено {len(reminders)} ожидающих напоминаний")

            for reminder in reminders:
                await self.send_reminder(reminder)
                await sheets_manager.mark_reminder_sent(reminder)

        except Exception as e:
            logger.error(f"Ошибка обработки напоминаний: {e}")

    async def send_reminder(self, reminder):
        """Отправка напоминания"""
        try:
            user_id = int(reminder['user_id'])
            event_id = reminder['event_id']
            reminder_type = reminder['type']

            event = await sheets_manager.get_event(event_id)
            if not event:
                logger.warning(f"Событие {event_id} для напоминания не найдено")
                return

            # Проверяем, что регистрация еще активна
            registration = await sheets_manager.get_user_registration(user_id, event_id)
            if not registration or registration['status'] != 'registered':
                logger.warning(f"Регистрация для напоминания не найдена или отменена")
                return

            # Проверяем, что событие еще не прошло
            start_at = datetime.fromisoformat(event['start_at'])
            now = datetime.now(timezone)

            # Если событие уже началось более 2 часов назад, не отправляем напоминания
            if start_at < now - timedelta(hours=2):
                logger.info(f"Событие {event_id} уже прошло, напоминание {reminder_type} не отправляется")
                await sheets_manager.mark_reminder_sent(reminder)
                return

            place = event.get('place', 'Место уточняется')

            # Форматируем сообщение в зависимости от типа напоминания
            if reminder_type == 'D1':
                message_text = "Напоминание за 1 день до события:\n"
            elif reminder_type == 'H6':
                message_text = "Напоминание за 6 часов до события:\n"
            elif reminder_type == 'H1':
                message_text = "Напоминание за 1 час до события:\n"
            else:
                message_text = "Напоминание:\n"

            message_text += f"**{event['title']}**\n"
            message_text += f"🗓 {start_at.strftime('%d.%m.%Y %H:%M')} | 📍 {place}\n"
            message_text += "Если планы изменились — вы можете отменить регистрацию."

            keyboard = create_reminder_keyboard(registration['registration_id'])

            await self.bot.send_message(user_id, message_text, reply_markup=keyboard)
            logger.info(f"Отправлено напоминание {reminder_type} пользователю {user_id}")

        except Exception as e:
            logger.error(f"Ошибка отправки напоминания пользователю {reminder.get('user_id', 'unknown')}: {e}")

    async def process_waitlist(self):
        """Обработка листа ожидания"""
        try:
            events = await sheets_manager.get_active_events()
            now = datetime.now(timezone)

            for event_id, event in events.items():
                # Проверяем отмены регистраций
                registrations = await sheets_manager.get_all_records('registrations')
                cancelled_registrations = [
                    reg for reg in registrations
                    if (reg['event_id'] == event_id and
                        reg['status'] == 'cancelled' and
                        reg.get('updated_at'))
                ]

                for cancelled_reg in cancelled_registrations:
                    updated_at = datetime.fromisoformat(cancelled_reg['updated_at'])
                    start_at = datetime.fromisoformat(event['start_at'])

                    # Проверяем, что отмена была за более чем 60 минут до начала
                    if (start_at - updated_at) > timedelta(minutes=60):
                        await self.offer_place_to_waitlist(event_id, event)

        except Exception as e:
            logger.error(f"Ошибка обработки листа ожидания: {e}")

    async def offer_place_to_waitlist(self, event_id, event):
        """Предложение места первому в очереди"""
        try:
            # Находим первого в очереди
            registrations = await sheets_manager.get_all_records('registrations')
            waitlist = [
                reg for reg in registrations
                if (reg['event_id'] == event_id and
                    reg['status'] == 'waitlist' and
                    reg.get('waitlist_position') == 1)
            ]

            if not waitlist:
                return

            next_in_line = waitlist[0]
            user_id = next_in_line['user_id']

            # Отправляем предложение
            message_text = f"Освободилось место на событие {event['title']}.\n"
            message_text += "Хотите занять его? Удержание — 15 минут."

            keyboard = create_place_offer_keyboard(next_in_line['registration_id'])

            await self.bot.send_message(user_id, message_text, reply_markup=keyboard)

            # Создаем задачу на отмену удержания через 15 минут
            self.scheduler.add_job(
                self.revoke_place_offer,
                'date',
                run_date=datetime.now(timezone) + timedelta(minutes=Config.PLACE_HOLD_TIME),
                args=[next_in_line['registration_id'], event_id],
                id=f"hold_{next_in_line['registration_id']}"
            )

        except Exception as e:
            logger.error(f"Ошибка предложения места: {e}")

    async def revoke_place_offer(self, registration_id, event_id):
        """Отзыв предложения места"""
        try:
            registration = await sheets_manager.get_registration(registration_id)
            if registration and registration['status'] == 'waitlist':
                # Предложение истекло, переходим к следующему
                event = await sheets_manager.get_event(event_id)
                if event:
                    await self.offer_place_to_waitlist(event_id, event)
        except Exception as e:
            logger.error(f"Ошибка отзыва предложения места: {e}")

    async def process_attendance_followup(self):
        """Обработка неявок и отправка благодарностей"""
        try:
            now = datetime.now(timezone)
            events = await sheets_manager.get_active_events()

            for event_id, event in events.items():
                start_at = datetime.fromisoformat(event['start_at'])

                # Проверяем неявки (через 2 часа после начала)
                if now >= start_at + timedelta(hours=2):
                    await self.process_no_shows(event_id, event)

                # Проверяем благодарности (через 2 часа после начала)
                if now >= start_at + timedelta(hours=2):
                    await self.process_thanks(event_id, event)

        except Exception as e:
            logger.error(f"Ошибка обработки посещений: {e}")

    async def process_no_shows(self, event_id, event):
        """Обработка неявок"""
        try:
            registrations = await sheets_manager.get_all_records('registrations')

            for reg in registrations:
                if (reg['event_id'] == event_id and
                        reg['status'] == 'registered' and
                        not reg.get('checkin_at')):

                    # Отправляем сообщение о неявке
                    message_text = (
                        "Вы были зарегистрированы на мероприятие, но не пришли. "
                        "Пожалуйста, отменяйте регистрацию заранее, если планы меняются."
                    )

                    try:
                        await self.bot.send_message(reg['user_id'], message_text)
                        # Обновляем статус на no_show
                        await sheets_manager.update_registration_status(
                            reg['registration_id'], 'no_show'
                        )
                    except Exception as e:
                        logger.error(f"Не удалось отправить сообщение о неявке: {e}")

        except Exception as e:
            logger.error(f"Ошибка обработки неявок: {e}")

    async def process_thanks(self, event_id, event):
        """Отправка благодарностей"""
        try:
            registrations = await sheets_manager.get_all_records('registrations')

            for reg in registrations:
                if (reg['event_id'] == event_id and
                        reg['status'] == 'attended'):

                    # Отправляем благодарность
                    message_text = f"Спасибо, что пришли на событие {event['title']}! 🙌\n"
                    message_text += "Оцените, пожалуйста, событие по шкале 1–5."

                    keyboard = create_rating_keyboard(event_id)

                    try:
                        await self.bot.send_message(
                            reg['user_id'],
                            message_text,
                            reply_markup=keyboard
                        )
                    except Exception as e:
                        logger.error(f"Не удалось отправить благодарность: {e}")

        except Exception as e:
            logger.error(f"Ошибка отправки благодарностей: {e}")

    def start(self):
        """Запуск планировщика"""
        self.scheduler.start()
        logger.info("Планировщик запущен")

    def shutdown(self):
        """Остановка планировщика"""
        self.scheduler.shutdown()
        logger.info("Планировщик остановлен")