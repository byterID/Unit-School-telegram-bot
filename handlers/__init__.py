"""Централизованная регистрация всех обработчиков."""
from __future__ import annotations

import logging
import time

from telegram import Update
from telegram.constants import ChatType
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    ApplicationHandlerStop,
    ContextTypes,
    TypeHandler,
)

from handlers import admin, changes, common, contacts, menu, schedule

logger = logging.getLogger(__name__)

# Минимальный интервал между апдейтами от одного пользователя (сек)
FLOOD_INTERVAL = 0.5


async def _guard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Выполняется ДО всех обработчиков (group=-1):
    1) бот работает только в личных сообщениях — из групп/каналов выходит;
    2) простой антифлуд на пользователя.
    """
    chat = update.effective_chat
    if chat is not None and chat.type != ChatType.PRIVATE:
        try:
            await chat.leave()
            logger.info("Покинул чат %s (%s)", chat.id, chat.type)
        except TelegramError:
            pass
        raise ApplicationHandlerStop

    if context.user_data is None:
        return

    now = time.monotonic()
    last = context.user_data.get("_last_ts", 0.0)
    context.user_data["_last_ts"] = now
    if now - last < FLOOD_INTERVAL:
        if update.callback_query:
            try:
                await update.callback_query.answer("Не так быстро 🙂")
            except TelegramError:
                pass
        raise ApplicationHandlerStop


def register_all(app: Application) -> None:
    """
    Порядок важен: ConversationHandler'ы админки регистрируются первыми,
    чтобы перехватывать текстовые сообщения внутри диалогов.
    """
    app.add_handler(TypeHandler(Update, _guard), group=-1)
    admin.register(app)
    changes.register(app)  # тоже содержит диалог — до common с его обработчиком текста
    common.register(app)
    schedule.register(app)
    menu.register(app)
    contacts.register(app)
