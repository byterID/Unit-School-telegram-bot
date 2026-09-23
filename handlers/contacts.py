"""Контакты преподавателей: постраничный список + карточка."""
from __future__ import annotations

import logging

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes

from database import db
from utils import formatting as fmt
from utils import keyboard as kb
from utils.decorators import is_admin, safe_handler

logger = logging.getLogger(__name__)


@safe_handler
async def cmd_contacts(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/contacts, con:list, con:list:<страница>."""
    page = 0
    if update.callback_query:
        parts = update.callback_query.data.split(":")
        page = int(parts[2]) if len(parts) > 2 else 0

    teachers = await db.get_teachers()
    if teachers:
        text = "📞 <b>Контакты преподавателей</b>\n\nВыберите преподавателя:"
        markup = kb.contacts_list(teachers, page)
    else:
        text = "Список преподавателей пока пуст."
        markup = kb.main_menu(is_admin(update.effective_user.id))

    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(
            text, parse_mode=ParseMode.HTML, reply_markup=markup
        )
    elif update.message:
        await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=markup)


@safe_handler
async def contact_show(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """con:t:<teacher_id>:<страница>"""
    query = update.callback_query
    _, _, raw_id, raw_page = query.data.split(":")
    teacher = await db.get_teacher(int(raw_id))
    if teacher is None:
        await query.answer("Преподаватель не найден", show_alert=True)
        return
    await query.answer()
    await query.edit_message_text(
        fmt.format_teacher_card(teacher),
        parse_mode=ParseMode.HTML,
        reply_markup=kb.contact_card(teacher["id"], int(raw_page)),
    )


def register(app: Application) -> None:
    app.add_handler(CommandHandler("contacts", cmd_contacts))
    app.add_handler(CallbackQueryHandler(cmd_contacts, pattern=r"^con:list(:\d+)?$"))
    app.add_handler(CallbackQueryHandler(contact_show, pattern=r"^con:t:\d+:\d+$"))
