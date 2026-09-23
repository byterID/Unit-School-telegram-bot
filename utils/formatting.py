"""Функции сборки текстов сообщений (HTML parse_mode)."""
from __future__ import annotations

from datetime import date, datetime, timedelta
from html import escape
from itertools import groupby
from typing import Iterable, Mapping, Sequence

import config


def today() -> date:
    """Сегодняшняя дата в часовом поясе школы."""
    return datetime.now(config.TIMEZONE).date()


def resolve_day(kind: str) -> tuple[int, str]:
    """
    Превращает 'today' / 'tomorrow' в (номер дня недели, подпись).
    Возвращает isoweekday: 1 = понедельник.
    """
    d = today()
    if kind == "tomorrow":
        d += timedelta(days=1)
    weekday = d.isoweekday()
    label = f"{config.WEEKDAYS[weekday]}, {d.strftime('%d.%m.%Y')}"
    return weekday, label

TG_LIMIT = 4096


def fit(text: str, limit: int = TG_LIMIT) -> str:
    """Обрезает текст под лимит Telegram, не разрывая строки (и HTML-теги внутри них)."""
    if len(text) <= limit:
        return text
    tail = "\n\n<i>…сообщение слишком длинное, показана только часть</i>"
    cut = text.rfind("\n", 0, limit - len(tail))
    if cut <= 0:
        cut = limit - len(tail)
    return text[:cut] + tail


def esc(value: object) -> str:
    """Безопасное экранирование для HTML-разметки Telegram."""
    return escape(str(value if value is not None else ""), quote=False)


# --- Расписание -------------------------------------------------------------
# Функции ниже принимают список dict из utils/timetable.py
# (ключи урока + changed, cancelled, note).

CHANGED_MARK = "🔄"


def day_label(d: date) -> str:
    return f"{config.WEEKDAYS[d.isoweekday()]}, {d.strftime('%d.%m.%Y')}"


def _status(lesson: Mapping) -> str:
    """'' — обычный урок, 'changed' — замена, 'cancelled' — урока нет."""
    if lesson.get("cancelled"):
        return "cancelled"
    if lesson.get("changed"):
        return "changed"
    return ""


def _uniq(values: Iterable) -> list[str]:
    """Уникальные непустые значения с сохранением порядка."""
    return list(dict.fromkeys(str(v) for v in values if v))


def _slots(lessons: Sequence[Mapping]) -> list[list[Mapping]]:
    """Уроки с одним номером — один слот (подгруппы, совместные уроки, замены)."""
    rows = sorted(lessons, key=lambda l: l["number"])
    return [list(g) for _, g in groupby(rows, key=lambda l: l["number"])]


def format_day_schedule(
    lessons: Sequence[Mapping], title: str, show_group: bool = False
) -> str:
    """Расписание на один день. Изменения помечаются только значком 🔄."""
    header = f"📅 <b>Расписание</b>\n<i>{esc(title)}</i>\n"
    if not lessons:
        return header + "\nЗанятий нет — можно отдыхать 🎉"

    lines = [header]
    for slot in _slots(lessons):
        first = slot[0]
        active = [l for l in slot if not l.get("cancelled")]
        mark = f"{CHANGED_MARK} " if any(_status(l) for l in slot) else ""

        start, end = first.get("time_start"), first.get("time_end")
        time = f" {esc(start)}–{esc(end)}" if start and end else ""
        line = f"\n<b>{first['number']}.</b>{time} — {mark}"

        # Примечания берём только у тех уроков, которые реально идут
        notes = _uniq(l.get("note") for l in (active or slot))
        note = f" · <i>{', '.join(esc(n) for n in notes)}</i>" if notes else ""

        if not active:
            lines.append(f"{line}<i>урока нет</i>{note}")
            continue

        subject = " / ".join(esc(s) for s in _uniq(l["subject"] for l in active))
        if show_group:
            who_list = _uniq(l.get("group_name") for l in active)
            who = f"👥 {', '.join(esc(w) for w in who_list)}" if who_list else ""
        else:
            who_list = _uniq(l.get("teacher_name") for l in active)
            who = f"👨‍🏫 {', '.join(esc(w) for w in who_list)}" if who_list else ""
        rooms = _uniq(l.get("room") for l in active)
        room = f"📍 {', '.join(esc(r) for r in rooms)}" if rooms else ""

        details = "  ".join(p for p in (room, who) if p)
        lines.append(
            f"{line}<b>{subject}</b>" + (f"\n   {details}" if details else "") + note
        )
    return fit("".join(lines))


def format_week_schedule(
    days: Sequence[tuple[date, Sequence[Mapping]]], title: str, show_group: bool = False
) -> str:
    parts = [f"📅 <b>Расписание на неделю</b>\n<i>{esc(title)}</i>\n"]
    for d, lessons in days:
        changed = any(_status(l) for l in lessons)
        parts.append(
            f"\n<b>— {config.WEEKDAYS[d.isoweekday()]}, {d.strftime('%d.%m')} —</b>"
            + (f" {CHANGED_MARK}" if changed else "")
        )
        if not lessons:
            parts.append("\n<i>занятий нет</i>\n")
            continue
        for slot in _slots(lessons):
            first = slot[0]
            active = [l for l in slot if not l.get("cancelled")]
            mark = f"{CHANGED_MARK} " if any(_status(l) for l in slot) else ""
            if active:
                subj = " / ".join(esc(s) for s in _uniq(l["subject"] for l in active))
            else:
                subj = "<i>урока нет</i>"
            extra = ""
            if show_group and active:
                extra = " (" + ", ".join(esc(g) for g in _uniq(l.get("group_name") for l in active)) + ")"
            parts.append(f"\n{first['number']}. {esc(first['time_start'])} {mark}{subj}{extra}")
        parts.append("\n")
    return fit("".join(parts))


# --- Меню столовой ----------------------------------------------------------
def format_menu_day(dishes: Sequence[Mapping], title: str) -> str:
    """Меню на один день, сгруппированное по приёмам пищи."""
    header = f"🍽 <b>Меню столовой</b>\n<i>{esc(title)}</i>\n"
    if not dishes:
        return header + "\nМеню на этот день ещё не заполнено."

    grouped: dict[str, list[str]] = {}
    for dish in dishes:
        grouped.setdefault(dish["meal_type"], []).append(dish["name"])

    parts = [header]
    for meal_type, meal_title in config.MEAL_TYPES.items():
        items = grouped.get(meal_type)
        if not items:
            continue
        parts.append(f"\n<b>{meal_title}</b>")
        parts.extend(f"\n• {esc(name)}" for name in items)
        parts.append("\n")
    return "".join(parts)


def format_menu_week(menu_by_day: Mapping[int, Sequence[Mapping]]) -> str:
    """Меню на всю учебную неделю (кратко, чтобы уложиться в лимит 4096)."""
    parts = ["🍽 <b>Меню столовой на неделю</b>\n"]
    for weekday in config.STUDY_DAYS:
        parts.append(f"\n<b>— {config.WEEKDAYS[weekday]} —</b>")
        dishes = menu_by_day.get(weekday, [])
        if not dishes:
            parts.append("\n<i>не заполнено</i>\n")
            continue
        grouped: dict[str, list[str]] = {}
        for dish in dishes:
            grouped.setdefault(dish["meal_type"], []).append(dish["name"])
        for meal_type, meal_title in config.MEAL_TYPES.items():
            if meal_type in grouped:
                names = ", ".join(esc(n) for n in grouped[meal_type])
                parts.append(f"\n{meal_title}: {names}")
        parts.append("\n")
    return "".join(parts)


# --- Контакты ---------------------------------------------------------------
def format_teacher_card(teacher: Mapping) -> str:
    lines = [f"👨‍🏫 <b>{esc(teacher['full_name'])}</b>"]
    if teacher["subject"]:
        lines.append(f"📚 Предмет: {esc(teacher['subject'])}")
    if teacher["phone"]:
        lines.append(f"📞 Телефон: {esc(teacher['phone'])}")
    if teacher["room"]:
        lines.append(f"🚪 Кабинет: {esc(teacher['room'])}")
    if teacher["email"]:
        lines.append(f"✉️ Email: {esc(teacher['email'])}")
    return "\n".join(lines)


def format_contacts_list(teachers: Sequence[Mapping]) -> str:
    if not teachers:
        return "Список преподавателей пока пуст."
    parts = ["📞 <b>Контакты преподавателей</b>\n"]
    for teacher in teachers:
        parts.append("\n" + format_teacher_card(teacher) + "\n")
    return "".join(parts)


# --- Объявления -------------------------------------------------------------
def format_announcements(rows: Sequence[Mapping], limit: int = 3900) -> str:
    """
    Последние объявления. Текст уже хранится в безопасном HTML (text_html).
    Добавляем объявления, пока укладываемся в лимит сообщения Telegram.
    """
    if not rows:
        return "📢 Объявлений пока нет."
    header = "📢 <b>Последние объявления</b>\n"
    parts = [header]
    total = len(header)
    for row in rows:
        created = datetime.fromisoformat(row["created_at"]).strftime("%d.%m.%Y %H:%M")
        block = f"\n<i>{created}</i>\n{row['text']}\n"
        if total + len(block) > limit and len(parts) > 1:
            break
        parts.append(block)
        total += len(block)
    return "".join(parts)

