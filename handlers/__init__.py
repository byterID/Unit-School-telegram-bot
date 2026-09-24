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
from utils import screen

logger = logging.getLogger(__name__)

# Минимальный интервал между апдейтами от одного пользователя (сек)
FLOOD_INTERVAL = 0.5


async def _guard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Выполняется ДО всех обработчиков (group=-1):
    1) бот работает только в личных сообщениях — из групп/каналов выходит;
    2) простой антифлуд на пользователя;
    3) учёт «экрана»: сообщение, на кнопку которого нажали, становится текущим.
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
        elif update.message:
            await screen.delete(context.bot, update.message.chat_id, update.message.message_id)
        raise ApplicationHandlerStop

    query = update.callback_query
    if query is not None and query.message is not None:
        # Любая кнопка, кроме «Сменить класс», отменяет ожидание кода
        if query.data != "set:grp":
            context.user_data.pop("await", None)
        chat_id = query.message.chat.id
        await screen.purge(context.bot, chat_id, context.user_data)
        old = context.user_data.get(screen.KEY)
        if old and old != query.message.message_id:
            await screen.delete(context.bot, chat_id, old)
        context.user_data[screen.KEY] = query.message.message_id


def register_all(app: Application) -> None:
    """
    Порядок важен: ConversationHandler'ы админки регистрируются первыми,
    чтобы перехватывать текстовые сообщения внутри диалогов.
    """
    app.add_handler(TypeHandler(Update, _guard), group=-1)
    admin.register(app)
    changes.register(app)
    schedule.register(app)
    menu.register(app)
    contacts.register(app)
    common.register(app)  # последним: в нём обработчик любого текста
