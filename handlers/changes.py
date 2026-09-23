"""
Срочные изменения расписания на конкретную дату (замены, отмены).

Путь: Админ-панель → ✏️ Изменения в расписании → дата → класс → ввод текстом.
Недельная сетка из Excel не меняется: изменения хранятся отдельно
и накладываются поверх неё только в выбранный день.
"""
from __future__ import annotations

import logging
import re
from datetime import date, datetime, timedelta
from itertools import groupby
from typing import Mapping, Sequence

from telegram import CallbackQuery, Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

import config
from database import db
from utils import formatting as fmt
from utils import keyboard as kb
from utils import timetable as tt
from utils.broadcast import send_many
from utils.decorators import admin_only, safe_handler

logger = logging.getLogger(__name__)

CHG_TEXT = 100      # состояние диалога (не пересекается с admin.py)
DAYS_AHEAD = 10     # сколько учебных дней показывать при выборе даты
MAX_LESSON = 12

# «3 Физика, Стяжкина» / «3. Физика» / «3 урок -» / «все -»
LINE_RE = re.compile(r"^(\d{1,2}(?!\d)|вс[её])\s*(?:урок\w*)?\s*[.:)]?\s*(.*)$", re.IGNORECASE)
# «-», «- экскурсия», «отмена», «отменён»
CANCEL_RE = re.compile(r"^(?:-|—|–|отмен\w*|нет)(?:\s+(.*))?$", re.IGNORECASE)
# Запятые вне скобок: «Психология (Понять себя, понять других)» не режется
SPLIT_RE = re.compile(r",(?![^()]*\))")
KEEP = "="
NO_TEACHER = {"-", "—", "–", "?"}


# --- Вспомогательное --------------------------------------------------------
def _day_key(d: date) -> str:
    return d.strftime("%Y%m%d")


def _parse_day(raw: str) -> date | None:
    """Дата из callback_data; прошедшие даты не принимаем."""
    try:
        d = datetime.strptime(raw, "%Y%m%d").date()
    except ValueError:
        return None
    return d if d >= fmt.today() else None


def _study_days(count: int) -> list[date]:
    d, result = fmt.today(), []
    while len(result) < count:
        if d.isoweekday() in config.STUDY_DAYS:
            result.append(d)
        d += timedelta(days=1)
    return result


def _clip(text: str, limit: int) -> str | None:
    text = text.strip()
    return text[:limit] if text else None


def _norm_name(s: str) -> list[str]:
    return re.sub(r"[.\s]+", " ", s.lower().replace("ё", "е")).split()


def match_teacher(query: str, teachers: Sequence[Mapping]) -> list[Mapping]:
    """«Стяжкина», «Стяжкина Л.П.», «Абрамян Ж» → подходящие преподаватели."""
    q = _norm_name(query)
    if not q:
        return []
    exact = [t for t in teachers if _norm_name(t["full_name"]) == q]
    if exact:
        return exact
    found = []
    for t in teachers:
        words = _norm_name(t["full_name"])
        if len(q) <= len(words) and all(w.startswith(p) for w, p in zip(words, q)):
            found.append(t)
    return found


def _help_text(title: str) -> str:
    return (
        f"✍️ <b>{fmt.esc(title)}</b>\n\n"
        "Пришлите изменения одним сообщением, каждое с новой строки:\n\n"
        "<code>3 Физика, Стяжкина</code>\n— вместо 3 урока физика, ведёт Стяжкина\n\n"
        "<code>3 =, Гарян</code>\n— предмет тот же, другой учитель\n\n"
        "<code>3 Математика, =</code>\n— другой предмет, учитель тот же\n\n"
        "<code>5 -</code>\n— урок отменён\n\n"
        "<code>5 -, экскурсия</code>\n— отменён, с пояснением\n\n"
        "<code>6 Химия, Гарян, в каб. 12</code>\n— третья часть — примечание\n\n"
        "<code>все -, карантин</code>\n— отменить весь день\n\n"
        "Учителя достаточно указать фамилией. Если урок уже меняли, "
        "новая строка заменит старую.\n\nОтмена: /cancel"
    )


# --- Разбор текста ----------------------------------------------------------
def parse_changes(
    text: str,
    base: dict[int, list[Mapping]],
    bells: dict[int, tuple[str, str]],
    teachers: Sequence[Mapping],
) -> tuple[list[tuple], list[str]]:
    """
    Возвращает (изменения, ошибки). Если есть ошибки — ничего не сохраняем.
    Изменение: (number, subject | None, teacher_id | None, time_start, time_end, note)
    """
    items: dict[int, tuple] = {}
    errors: list[str] = []

    def err(idx: int, msg: str) -> None:
        errors.append(f"{idx}) {msg}")

    for idx, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line:
            continue
        if len(errors) >= 10:
            errors.append("…и другие ошибки")
            break

        m = LINE_RE.match(line)
        if not m:
            err(idx, f"«{fmt.esc(line[:40])}» — строка должна начинаться с номера урока")
            continue
        head = m.group(1).lower()
        is_all = head.startswith("вс")
        parts = [p.strip() for p in SPLIT_RE.split(m.group(2))]
        what = parts[0]
        if not what:
            err(idx, "после номера урока ничего не написано")
            continue

        # --- Отмена ---
        cancel = CANCEL_RE.match(what)
        if cancel:
            note = _clip(", ".join(p for p in [cancel.group(1) or "", *parts[1:]] if p), 200)
            if is_all:
                numbers = sorted(base)
                if not numbers:
                    err(idx, "в этот день и так нет уроков")
                    continue
            else:
                n = int(head)
                if n not in base:
                    err(idx, f"{n} урока в этот день нет — отменять нечего")
                    continue
                numbers = [n]
            for n in numbers:
                first = base[n][0]
                items[n] = (n, None, None, first["time_start"], first["time_end"], note)
            continue

        if is_all:
            err(idx, "«все» можно только отменить: <code>все -</code>")
            continue

        # --- Замена ---
        n = int(head)
        orig = base.get(n, [])
        times = (orig[0]["time_start"], orig[0]["time_end"]) if orig else bells.get(n)
        if not 1 <= n <= MAX_LESSON or times is None:
            err(idx, f"не знаю, во сколько идёт {n} урок")
            continue

        subject = what
        if subject == KEEP:
            if not orig:
                err(idx, f"в {n} урок сейчас пусто — «=» не с чем взять")
                continue
            subject = " / ".join(dict.fromkeys(r["subject"] for r in orig))

        who = parts[1] if len(parts) > 1 else ""
        teacher_id = None
        if who == KEEP:
            ids = {r["teacher_id"] for r in orig if r["teacher_id"]}
            if len(ids) != 1:
                reason = "нет учителя" if not ids else "несколько учителей"
                err(idx, f"в {n} урок {reason} — напишите фамилию вместо «=»")
                continue
            teacher_id = ids.pop()
        elif who and who not in NO_TEACHER:
            found = match_teacher(who, teachers)
            if not found:
                err(idx, f"преподаватель «{fmt.esc(who)}» не найден")
                continue
            if len(found) > 1:
                names = ", ".join(t["full_name"] for t in found[:4])
                err(idx, f"«{fmt.esc(who)}» подходит нескольким: {fmt.esc(names)}. Уточните")
                continue
            teacher_id = found[0]["id"]

        note = _clip(", ".join(p for p in parts[2:] if p), 200)
        items[n] = (n, subject[:100], teacher_id, times[0], times[1], note)

    if not items and not errors:
        errors.append("не нашёл ни одного изменения")
    return [items[n] for n in sorted(items)], errors


# --- Экраны -----------------------------------------------------------------
async def _editor_view(d: date, group_id: int):
    group = await db.get_group(group_id)
    if group is None:
        return None
    lessons = await tt.group_day(group_id, d)
    changes = await db.get_changes(d.isoformat(), group_id)
    text = "✏️ <b>Изменения в расписании</b>\n\n" + fmt.format_day_schedule(
        lessons, f"{group['name']} • {fmt.day_label(d)}"
    )
    if changes:
        text += (
            f"\n\nИзменений: <b>{len(changes)}</b>. "
            "Чтобы убрать изменение, нажмите на него ниже."
        )
    else:
        text += "\n\nИзменений нет, расписание обычное."
    return fmt.fit(text), kb.changes_editor(_day_key(d), group_id, changes)


async def _show_editor(query: CallbackQuery, d: date, group_id: int) -> None:
    view = await _editor_view(d, group_id)
    if view is None:
        await query.edit_message_text("Класс не найден.", reply_markup=kb.admin_back())
        return
    await query.edit_message_text(view[0], parse_mode=ParseMode.HTML, reply_markup=view[1])


async def _day_group(query: CallbackQuery) -> tuple[date, int] | None:
    """Разбирает '<prefix>:<action>:<YYYYMMDD>:<group_id>'."""
    parts = query.data.split(":")
    d = _parse_day(parts[2])
    if d is None:
        await query.answer("Эта дата уже прошла, выберите другую", show_alert=True)
        return None
    return d, int(parts[3])


# --- Навигация --------------------------------------------------------------
@admin_only
@safe_handler
async def chg_root(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    today = fmt.today()
    marked = await db.get_change_days(today.isoformat())
    await query.edit_message_text(
        "✏️ <b>Изменения в расписании</b>\n\n"
        "На какой день? Значком ✏️ отмечены дни, где уже есть изменения.",
        parse_mode=ParseMode.HTML,
        reply_markup=kb.changes_dates(_study_days(DAYS_AHEAD), marked, today),
    )


@admin_only
@safe_handler
async def chg_day(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """chg:d:<YYYYMMDD>"""
    query = update.callback_query
    d = _parse_day(query.data.split(":")[2])
    if d is None:
        await query.answer("Эта дата уже прошла, выберите другую", show_alert=True)
        return
    await query.answer()
    groups = await db.get_groups()
    marked = {c["group_id"] for c in await db.get_changes(d.isoformat())}
    await query.edit_message_text(
        f"✏️ <b>{fmt.day_label(d)}</b>\n\nВыберите класс:",
        parse_mode=ParseMode.HTML,
        reply_markup=kb.changes_groups(groups, _day_key(d), marked),
    )


@admin_only
@safe_handler
async def chg_group(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """chg:g:<YYYYMMDD>:<group_id>"""
    query = update.callback_query
    parsed = await _day_group(query)
    if parsed is None:
        return
    await query.answer()
    await _show_editor(query, *parsed)


@admin_only
@safe_handler
async def chg_delete(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """chg:x:<change_id>"""
    query = update.callback_query
    change = await db.get_change(int(query.data.split(":")[2]))
    if change is None:
        await query.answer("Уже удалено")
        return
    await db.delete_change(change["id"])
    await query.answer("Изменение удалено")
    logger.info("Админ %s удалил изменение id=%s", update.effective_user.id, change["id"])
    await _show_editor(query, date.fromisoformat(change["day"]), change["group_id"])


@admin_only
@safe_handler
async def chg_clear(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """chg:clr:<YYYYMMDD>:<group_id>"""
    query = update.callback_query
    parsed = await _day_group(query)
    if parsed is None:
        return
    d, group_id = parsed
    await db.clear_changes(d.isoformat(), group_id)
    await query.answer("Все изменения на этот день сброшены")
    logger.info("Админ %s сбросил изменения %s, класс %s", update.effective_user.id, d, group_id)
    await _show_editor(query, d, group_id)


# --- Диалог ввода изменений -------------------------------------------------
@admin_only
async def chg_input_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """chg:in:<YYYYMMDD>:<group_id>"""
    query = update.callback_query
    parsed = await _day_group(query)
    if parsed is None:
        return ConversationHandler.END
    d, group_id = parsed
    group = await db.get_group(group_id)
    if group is None:
        await query.answer("Класс не найден", show_alert=True)
        return ConversationHandler.END
    await query.answer()
    context.user_data["chg"] = (_day_key(d), group_id)
    # Отдельным сообщением, чтобы расписание осталось видно выше
    await query.message.reply_text(
        _help_text(f"{group['name']} • {fmt.day_label(d)}"), parse_mode=ParseMode.HTML
    )
    return CHG_TEXT


@admin_only
async def chg_input_save(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    message = update.message
    saved = context.user_data.get("chg")
    if not saved:
        await message.reply_text("Сессия истекла, начните заново: /admin")
        return ConversationHandler.END
    raw_day, group_id = saved
    d = _parse_day(raw_day)
    if d is None:
        context.user_data.pop("chg", None)
        await message.reply_text("Эта дата уже прошла, выберите другую.", reply_markup=kb.admin_root())
        return ConversationHandler.END

    base_rows = await db.get_lessons_by_group(group_id, d.isoweekday())
    base = {n: list(rows) for n, rows in groupby(base_rows, key=lambda r: r["number"])}
    items, errors = parse_changes(
        message.text or "", base, await db.get_bells(), await db.get_teachers()
    )
    if errors:
        await message.reply_text(
            "⚠️ <b>Ничего не сохранено.</b> Исправьте ошибки и пришлите всё сообщение заново:\n\n"
            + "\n".join(errors)
            + "\n\nОтмена: /cancel",
            parse_mode=ParseMode.HTML,
        )
        return CHG_TEXT

    await db.save_changes(d.isoformat(), group_id, items, update.effective_user.id)
    context.user_data.pop("chg", None)
    logger.info(
        "Админ %s сохранил %d изменений: %s, класс %s",
        update.effective_user.id, len(items), d, group_id,
    )
    text, markup = await _editor_view(d, group_id)
    await message.reply_text(
        fmt.fit(
            f"✅ Сохранено изменений: {len(items)}\n"
            "Не забудьте нажать «📣 Уведомить класс».\n\n" + text
        ),
        parse_mode=ParseMode.HTML,
        reply_markup=markup,
    )
    return ConversationHandler.END


async def chg_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.pop("chg", None)
    if update.message:
        await update.message.reply_text(
            "Отменено, изменения не сохранены.", reply_markup=kb.admin_root()
        )
    return ConversationHandler.END


# --- Уведомление ------------------------------------------------------------
async def _audience(d: date, group_id: int) -> tuple[set[int], set[int]]:
    """(ученики/родители класса, затронутые преподаватели)."""
    parents = set(await db.get_group_audience(group_id))
    changes = await db.get_changes(d.isoformat(), group_id)
    numbers = {c["number"] for c in changes}
    teacher_ids = {c["teacher_id"] for c in changes if c["teacher_id"]}
    for row in await db.get_lessons_by_group(group_id, d.isoweekday()):
        if row["number"] in numbers and row["teacher_id"]:
            teacher_ids.add(row["teacher_id"])  # прежний учитель тоже должен узнать
    teachers = set(await db.get_teacher_audience(teacher_ids)) - parents
    return parents, teachers


async def _notify_text(d: date, group: Mapping) -> str:
    lessons = await tt.group_day(group["id"], d)
    return "🔔 <b>Изменения в расписании!</b>\n\n" + fmt.format_day_schedule(
        lessons, f"{group['name']} • {fmt.day_label(d)}"
    )


@admin_only
@safe_handler
async def chg_notify(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """chg:ntf:<YYYYMMDD>:<group_id> — предпросмотр и подтверждение."""
    query = update.callback_query
    parsed = await _day_group(query)
    if parsed is None:
        return
    d, group_id = parsed
    group = await db.get_group(group_id)
    if group is None:
        await query.answer("Класс не найден", show_alert=True)
        return
    parents, teachers = await _audience(d, group_id)
    if not parents and not teachers:
        await query.answer("Отправлять некому: из этого класса в боте пока никого нет", show_alert=True)
        return
    await query.answer()
    preview = await _notify_text(d, group)
    await query.edit_message_text(
        fmt.fit(
            "📣 <b>Отправить уведомление?</b>\n\n"
            f"Получат: <b>{len(parents) + len(teachers)}</b> "
            f"(класс: {len(parents)}, преподаватели: {len(teachers)})\n\n"
            "Вот как оно будет выглядеть:\n\n" + preview
        ),
        parse_mode=ParseMode.HTML,
        reply_markup=kb.changes_notify(_day_key(d), group_id),
    )


@admin_only
@safe_handler
async def chg_send(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """chg:snd:<YYYYMMDD>:<group_id>"""
    query = update.callback_query
    parsed = await _day_group(query)
    if parsed is None:
        return
    d, group_id = parsed
    group = await db.get_group(group_id)
    if group is None:
        await query.answer("Класс не найден", show_alert=True)
        return
    await query.answer()
    parents, teachers = await _audience(d, group_id)
    text = await _notify_text(d, group)
    await query.edit_message_text("📤 Отправляю уведомление…")
    logger.info("Админ %s уведомляет об изменениях %s, класс %s", update.effective_user.id, d, group_id)
    context.application.create_task(
        _send_task(context, sorted(parents | teachers), text, query.message.chat_id, _day_key(d), group_id)
    )


async def _send_task(
    context: ContextTypes.DEFAULT_TYPE,
    user_ids: list[int],
    text: str,
    report_chat_id: int,
    day_key: str,
    group_id: int,
) -> None:
    sent, blocked, failed = await send_many(context.bot, user_ids, text)
    await context.bot.send_message(
        report_chat_id,
        f"✅ Уведомление отправлено\n\nДоставлено: {sent}\n"
        f"Заблокировали бота: {blocked}\nОшибок: {failed}",
        reply_markup=kb.changes_back(day_key, group_id),
    )


def register(app: Application) -> None:
    app.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(chg_input_start, pattern=r"^chg:in:\d{8}:\d+$")],
        states={CHG_TEXT: [MessageHandler(filters.TEXT & ~filters.COMMAND, chg_input_save)]},
        fallbacks=[
            CommandHandler("cancel", chg_cancel),
            MessageHandler(filters.COMMAND, chg_cancel),
        ],
        conversation_timeout=600,
        name="schedule_changes",
    ))
    handlers = [
        (chg_root, r"^chg:root$"),
        (chg_day, r"^chg:d:\d{8}$"),
        (chg_group, r"^chg:g:\d{8}:\d+$"),
        (chg_delete, r"^chg:x:\d+$"),
        (chg_clear, r"^chg:clr:\d{8}:\d+$"),
        (chg_notify, r"^chg:ntf:\d{8}:\d+$"),
        (chg_send, r"^chg:snd:\d{8}:\d+$"),
    ]
    for callback, pattern in handlers:
        app.add_handler(CallbackQueryHandler(callback, pattern=pattern))
