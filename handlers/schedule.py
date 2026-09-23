"""Расписание занятий: по группе, по преподавателю, на день/неделю."""
from __future__ import annotations

import logging

from telegram import CallbackQuery, Update
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


async def _get_scope_title(scope: str, obj_id: int) -> str | None:
    """Название группы или ФИО преподавателя; None, если объект не найден."""
    if scope == "g":
        obj = await db.get_group(obj_id)
        return obj["name"] if obj else None
    obj = await db.get_teacher(obj_id)
    return obj["full_name"] if obj else None


async def _render_day_picker(query: CallbackQuery, scope: str, obj_id: int) -> None:
    """
    Показывает экран выбора дня.

    Вынесено в отдельную функцию, потому что объекты Telegram иммутабельны
    и подменить query.data для переиспользования обработчика нельзя.
    """
    title = await _get_scope_title(scope, obj_id)
    if title is None:
        await query.edit_message_text(
            "Запись не найдена — возможно, она была удалена.",
            reply_markup=kb.main_menu(),
        )
        return
    await query.edit_message_text(
        f"📅 <b>{fmt.esc(title)}</b>\n\nНа какой день?",
        parse_mode=ParseMode.HTML,
        reply_markup=kb.schedule_days(scope, obj_id),
    )


@safe_handler
async def cmd_schedule(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Точка входа: /schedule и кнопка «Расписание»."""
    user_row = await db.get_user(update.effective_user.id)
    has_group = bool(user_row and user_row["group_id"])
    has_teacher = bool(user_row and user_row["teacher_id"])
    text = "📅 <b>Расписание</b>\n\nЧто показать?"
    markup = kb.schedule_root(has_group, has_teacher)

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
async def choose_group(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Список групп."""
    query = update.callback_query
    await query.answer()
    groups = await db.get_groups()
    if not groups:
        await query.edit_message_text(
            "Группы ещё не заведены.", reply_markup=kb.main_menu()
        )
        return
    await query.edit_message_text(
        "Выберите группу:", reply_markup=kb.group_list(groups, "sch:g")
    )


@safe_handler
async def choose_teacher(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Список преподавателей."""
    query = update.callback_query
    await query.answer()
    teachers = await db.get_teachers()
    if not teachers:
        await query.edit_message_text(
            "Преподаватели ещё не заведены.", reply_markup=kb.main_menu()
        )
        return
    await query.edit_message_text(
        "Выберите преподавателя:", reply_markup=kb.teacher_list(teachers, "sch:t")
    )


@safe_handler
async def choose_day(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Выбор дня после выбора группы/преподавателя: sch:g:<id> / sch:t:<id>."""
    query = update.callback_query
    await query.answer()
    _, scope, raw_id = query.data.split(":")
    await _render_day_picker(query, scope, int(raw_id))


@safe_handler
async def my_schedule(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Быстрый доступ к своему расписанию: sch:my (группа) / sch:mine (учитель)."""
    query = update.callback_query
    await query.answer()
    user_row = await db.get_user(update.effective_user.id)

    if query.data == "sch:my" and user_row and user_row["group_id"]:
        scope, obj_id = "g", user_row["group_id"]
    elif query.data == "sch:mine" and user_row and user_row["teacher_id"]:
        scope, obj_id = "t", user_row["teacher_id"]
    else:
        await query.edit_message_text(
            "Сначала укажите группу или профиль преподавателя в /settings.",
            reply_markup=kb.main_menu(),
        )
        return

    await _render_day_picker(query, scope, obj_id)


@safe_handler
async def show_schedule(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Показ расписания.
    callback: sch:show:<g|t>:<id>:<today|tomorrow|week|1..7>
    """
    query = update.callback_query
    await query.answer()
    _, _, scope, raw_id, when = query.data.split(":")
    obj_id = int(raw_id)
    show_group = scope == "t"  # для преподавателя важнее класс, чем его ФИО

    name = await _get_scope_title(scope, obj_id)
    if name is None:
        await query.edit_message_text(
            "Запись не найдена — возможно, она была удалена.",
            reply_markup=kb.main_menu(),
        )
        return

    fetch = db.get_lessons_by_group if scope == "g" else db.get_lessons_by_teacher

    if when == "week":
        lessons = await fetch(obj_id)
        text = fmt.format_week_schedule(lessons, name, show_group)
    else:
        if when in ("today", "tomorrow"):
            weekday, label = fmt.resolve_day(when)
        else:
            weekday = int(when)
            label = config.WEEKDAYS[weekday]
        lessons = await fetch(obj_id, weekday)
        text = fmt.format_day_schedule(lessons, f"{name} • {label}", show_group)

    # Если пользователь нажал ту же кнопку повторно, Telegram вернёт
    # «Message is not modified» — это гасится в safe_handler.
    await query.edit_message_text(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=kb.schedule_days(scope, obj_id),
    )


def register(app: Application) -> None:
    app.add_handler(CommandHandler("schedule", cmd_schedule))
    app.add_handler(CallbackQueryHandler(cmd_schedule, pattern=r"^sch:root$"))
    app.add_handler(CallbackQueryHandler(choose_group, pattern=r"^sch:groups$"))
    app.add_handler(CallbackQueryHandler(choose_teacher, pattern=r"^sch:teachers$"))
    app.add_handler(CallbackQueryHandler(my_schedule, pattern=r"^sch:(my|mine)$"))
    app.add_handler(
        CallbackQueryHandler(
            show_schedule, pattern=r"^sch:show:[gt]:\d+:(today|tomorrow|week|[1-7])$"
        )
    )
    # Важно: этот паттерн регистрируем последним, чтобы не перехватывать sch:show
    app.add_handler(CallbackQueryHandler(choose_day, pattern=r"^sch:[gt]:\d+$"))
