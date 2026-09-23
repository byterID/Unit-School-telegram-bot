"""Декораторы для обработчиков: контроль доступа и защита от падений."""
from __future__ import annotations

import functools
import logging
from typing import Awaitable, Callable

from telegram import Update
from telegram.error import BadRequest
from telegram.ext import ContextTypes

import config

logger = logging.getLogger(__name__)

Handler = Callable[[Update, ContextTypes.DEFAULT_TYPE], Awaitable[object]]


def is_admin(user_id: int | None) -> bool:
    """Проверка прав администратора по списку из .env."""
    return user_id is not None and user_id in config.ADMIN_IDS


def admin_only(func: Handler) -> Handler:
    """Пускает дальше только администраторов, остальным — вежливый отказ."""

    @functools.wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        if not is_admin(user.id if user else None):
            logger.warning(
                "Отказ в доступе к админ-функции: user_id=%s", user.id if user else "?"
            )
            if update.callback_query:
                await update.callback_query.answer(
                    "Недостаточно прав", show_alert=True
                )
            elif update.effective_message:
                await update.effective_message.reply_text(
                    "⛔️ Эта команда доступна только администраторам школы."
                )
            return None
        return await func(update, context)

    return wrapper


def safe_handler(func: Handler) -> Handler:
    """
    Локальный «предохранитель»: логирует исключение и сообщает пользователю,
    не давая обработчику уронить диалог. Глобальный error handler остаётся
    вторым уровнем защиты.
    """

    @functools.wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            return await func(update, context)
        except BadRequest as exc:
            # «Message is not modified» — не ошибка, а нормальная ситуация:
            # пользователь нажал ту же кнопку повторно, и содержимое
            # сообщения не изменилось. Telegram такие правки отклоняет.
            if "not modified" in str(exc).lower():
                logger.debug("Сообщение не изменилось (%s)", func.__name__)
                return None
            logger.exception("BadRequest в обработчике %s", func.__name__)
        except Exception:  # noqa: BLE001 — намеренно широкий перехват
            logger.exception("Ошибка в обработчике %s", func.__name__)

        # Общий путь уведомления пользователя для реальных сбоев
        if update.callback_query:
            try:
                await update.callback_query.answer(
                    "Произошла ошибка, попробуйте ещё раз", show_alert=True
                )
            except Exception:
                pass
        elif update.effective_message:
            try:
                await update.effective_message.reply_text(
                    "😔 Что-то пошло не так. Попробуйте ещё раз или напишите "
                    f"администратору {config.SUPPORT_CONTACT}."
                )
            except Exception:
                pass
        return None

    return wrapper
