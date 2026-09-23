"""Контакты преподавателей."""
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

from database import db
from utils import formatting as fmt
from utils import keyboard as kb
from utils.decorators import is_admin, safe_handler

logger = logging.getLogger(__name__)


@safe_handler
async def cmd_contacts(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Полный список: ФИО, предмет, телефон, кабинет."""
    teachers = await db.get_teachers()
    text = fmt.format_contacts_list(teachers)
    markup = kb.main_menu(is_admin(update.effective_user.id))

    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=markup,
            disable_web_page_preview=True,
        )
    elif update.message:
        await update.message.reply_text(
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=markup,
            disable_web_page_preview=True,
        )


def register(app: Application) -> None:
    app.add_handler(CommandHandler("contacts", cmd_contacts))
    app.add_handler(CallbackQueryHandler(cmd_contacts, pattern=r"^con:list$"))
