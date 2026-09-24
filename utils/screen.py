"""
«Один экран» на пользователя: в чате висит одно актуальное сообщение бота.

• Нажатие кнопки — сообщение редактируется на месте.
• Текст от пользователя — новый экран внизу, а сообщение пользователя,
  старый экран и все прошлые неудачные попытки удаляются.
• keep_input=True (ошибка ввода) — попытки остаются видны, чтобы текст
  можно было скопировать и исправить, и стираются после успешного ввода.

Telegram позволяет боту удалять сообщения не старше 48 часов —
ошибки удаления молча игнорируются.
"""
from __future__ import annotations

import logging

from telegram import InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.error import BadRequest, TelegramError
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)

KEY = "screen_id"         # id текущего экрана
TRASH = "screen_trash"    # сообщения, которые сотрём после успешного ввода


async def delete(bot, chat_id: int, message_id: int | None) -> None:
    if not message_id:
        return
    try:
        await bot.delete_message(chat_id, message_id)
    except TelegramError:
        pass


async def purge(bot, chat_id: int, user_data: dict | None) -> None:
    if user_data is None:
        return
    for mid in user_data.pop(TRASH, []):
        await delete(bot, chat_id, mid)


async def show(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    text: str,
    markup: InlineKeyboardMarkup | None = None,
    *,
    keep_input: bool = False,
    parse_mode: str = ParseMode.HTML,
) -> None:
    chat = update.effective_chat
    if chat is None:
        return
    ud = context.user_data if context.user_data is not None else {}
    bot = context.bot
    query = update.callback_query
    old = ud.get(KEY)

    # --- Нажатие кнопки: правим сообщение на месте --------------------------
    if query is not None and query.message is not None:
        await purge(bot, chat.id, ud)
        try:
            await query.edit_message_text(text, parse_mode=parse_mode, reply_markup=markup)
            ud[KEY] = query.message.message_id
            return
        except BadRequest as exc:
            if "not modified" in str(exc).lower():
                ud[KEY] = query.message.message_id
                return
            logger.debug("Не удалось отредактировать экран: %s", exc)
            old = query.message.message_id

    # --- Текст от пользователя: новый экран внизу ---------------------------
    msg = await bot.send_message(chat.id, text, parse_mode=parse_mode, reply_markup=markup)
    ud[KEY] = msg.message_id
    user_msg = update.message.message_id if update.message else None

    if keep_input:
        trash = ud.setdefault(TRASH, [])
        trash.extend(m for m in (old, user_msg) if m and m != msg.message_id)
        del trash[:-100]
        return

    await delete(bot, chat.id, user_msg)
    await purge(bot, chat.id, ud)
    if old and old != msg.message_id:
        await delete(bot, chat.id, old)
