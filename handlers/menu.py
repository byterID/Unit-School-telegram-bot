"""Меню столовой (просмотр). Редактирование — в handlers/admin.py."""
from __future__ import annotations

import logging

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

import config
from database import db
from utils import formatting as fmt
from utils import keyboard as kb
from utils.decorators import safe_handler

logger = logging.getLogger(__name__)


@safe_handler
async def cmd_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/menu — сразу показываем меню на сегодня + кнопки выбора дня."""
    weekday, label = fmt.resolve_day("today")
    dishes = await db.get_dishes(weekday)
    text = fmt.format_menu_day(dishes, label)
    markup = kb.menu_days()

    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(
            text, parse_mode=ParseMode.HTML, reply_markup=markup
        )
    elif update.message:
        await update.message.reply_text(
            text, parse_mode=ParseMode.HTML, reply_markup=markup
        )


@safe_handler
async def show_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """callback: menu:show:<today|tomorrow|week|1..7>"""
    query = update.callback_query
    await query.answer()
    when = query.data.split(":")[2]

    if when == "week":
        menu_by_day = {d: await db.get_dishes(d) for d in config.STUDY_DAYS}
        text = fmt.format_menu_week(menu_by_day)
    else:
        if when in ("today", "tomorrow"):
            weekday, label = fmt.resolve_day(when)
        else:
            weekday = int(when)
            label = config.WEEKDAYS[weekday]
        dishes = await db.get_dishes(weekday)
        text = fmt.format_menu_day(dishes, label)

    # Повторное нажатие той же кнопки даёт «Message is not modified» —
    # это безопасно перехватывается в safe_handler.
    await query.edit_message_text(
        text, parse_mode=ParseMode.HTML, reply_markup=kb.menu_days()
    )
def register(app: Application) -> None:
    app.add_handler(CommandHandler("menu", cmd_menu))
    app.add_handler(CallbackQueryHandler(cmd_menu, pattern=r"^menu:root$"))
    app.add_handler(
        CallbackQueryHandler(
            show_menu, pattern=r"^menu:show:(today|tomorrow|week|[1-7])$"
        )
    )