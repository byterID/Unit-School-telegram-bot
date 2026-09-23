"""Расписание: по классу, по преподавателю, на день/неделю (с учётом изменений)."""
from __future__ import annotations

import logging
from datetime import timedelta

from telegram import CallbackQuery, Update
from telegram.constants import ParseMode
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes

from database import db
from utils import formatting as fmt
from utils import keyboard as kb
from utils import timetable as tt
from utils.decorators import is_admin, safe_handler

logger = logging.getLogger(__name__)


def _main_menu(update: Update):
    """Главное меню с учётом прав — чтобы кнопка админки не пропадала."""
    return kb.main_menu(is_admin(update.effective_user.id))


async def _get_scope_title(scope: str, obj_id: int) -> str | None:
    if scope == "g":
        obj = await db.get_group(obj_id)
        return obj["name"] if obj else None
    obj = await db.get_teacher(obj_id)
    return obj["full_name"] if obj else None


async def _render_day_picker(update: Update, query: CallbackQuery, scope: str, obj_id: int) -> None:
    title = await _get_scope_title(scope, obj_id)
    if title is None:
        await query.edit_message_text(
            "Запись не найдена — возможно, её удалили.", reply_markup=_main_menu(update)
        )
        return
    await query.edit_message_text(
        f"📅 <b>{fmt.esc(title)}</b>\n\nНа какой день?",
        parse_mode=ParseMode.HTML,
        reply_markup=kb.schedule_days(scope, obj_id),
    )


@safe_handler
async def cmd_schedule(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    row = await db.get_user(update.effective_user.id)
    markup = kb.schedule_root(bool(row and row["group_id"]), bool(row and row["teacher_id"]))
    text = "📅 <b>Расписание</b>\n\nЧто показать?"
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(
            text, parse_mode=ParseMode.HTML, reply_markup=markup
        )
    elif update.message:
        await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=markup)


@safe_handler
async def choose_group(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    groups = await db.get_groups()
    if not groups:
        await query.edit_message_text("Классы ещё не заведены.", reply_markup=_main_menu(update))
        return
    await query.edit_message_text(
        "Выберите класс:", reply_markup=kb.group_list(groups, "sch:g", "sch:root")
    )


@safe_handler
async def choose_teacher(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """sch:teachers или sch:teachers:<страница>."""
    query = update.callback_query
    await query.answer()
    parts = query.data.split(":")
    page = int(parts[2]) if len(parts) > 2 else 0
    teachers = await db.get_teachers()
    if not teachers:
        await query.edit_message_text(
            "Преподаватели ещё не заведены.", reply_markup=_main_menu(update)
        )
        return
    await query.edit_message_text(
        "Выберите преподавателя:", reply_markup=kb.schedule_teachers(teachers, page)
    )


@safe_handler
async def choose_day(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    _, scope, raw_id = query.data.split(":")
    await _render_day_picker(update, query, scope, int(raw_id))


@safe_handler
async def my_schedule(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    row = await db.get_user(update.effective_user.id)
    if query.data == "sch:my" and row and row["group_id"]:
        scope, obj_id = "g", row["group_id"]
    elif query.data == "sch:mine" and row and row["teacher_id"]:
        scope, obj_id = "t", row["teacher_id"]
    else:
        await query.edit_message_text(
            "Класс или профиль пока не указан — загляните в /settings.",
            reply_markup=_main_menu(update),
        )
        return
    await _render_day_picker(update, query, scope, obj_id)


@safe_handler
async def show_schedule(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """sch:show:<g|t>:<id>:<today|tomorrow|week|1..7>"""
    query = update.callback_query
    await query.answer()
    _, _, scope, raw_id, when = query.data.split(":")
    obj_id = int(raw_id)
    show_group = scope == "t"

    name = await _get_scope_title(scope, obj_id)
    if name is None:
        await query.edit_message_text(
            "Запись не найдена — возможно, её удалили.", reply_markup=_main_menu(update)
        )
        return

    fetch_day = tt.group_day if scope == "g" else tt.teacher_day
    if when == "week":
        dates = tt.week_dates()
        days = [(d, await fetch_day(obj_id, d)) for d in dates]
        title = f"{name} • {dates[0].strftime('%d.%m')}–{dates[-1].strftime('%d.%m')}"
        text = fmt.format_week_schedule(days, title, show_group)
    else:
        if when == "today":
            d = fmt.today()
        elif when == "tomorrow":
            d = fmt.today() + timedelta(days=1)
        else:
            d = tt.upcoming(int(when))  # «Ср» = ближайшая среда
        text = fmt.format_day_schedule(
            await fetch_day(obj_id, d), f"{name} • {fmt.day_label(d)}", show_group
        )

    await query.edit_message_text(
        text, parse_mode=ParseMode.HTML, reply_markup=kb.schedule_days(scope, obj_id)
    )


def register(app: Application) -> None:
    app.add_handler(CommandHandler("schedule", cmd_schedule))
    app.add_handler(CallbackQueryHandler(cmd_schedule, pattern=r"^sch:root$"))
    app.add_handler(CallbackQueryHandler(choose_group, pattern=r"^sch:groups$"))
    app.add_handler(CallbackQueryHandler(choose_teacher, pattern=r"^sch:teachers(:\d+)?$"))
    app.add_handler(CallbackQueryHandler(my_schedule, pattern=r"^sch:(my|mine)$"))
    app.add_handler(
        CallbackQueryHandler(
            show_schedule, pattern=r"^sch:show:[gt]:\d+:(today|tomorrow|week|[1-7])$"
        )
    )
    app.add_handler(CallbackQueryHandler(choose_day, pattern=r"^sch:[gt]:\d+$"))
