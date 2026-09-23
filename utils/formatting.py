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
# Функции ниже принимают список dict из utils/timetable.py.

def day_label(d: date) -> str:
    return f"{config.WEEKDAYS[d.isoweekday()]}, {d.strftime('%d.%m.%Y')}"


def _status(lesson: Mapping) -> str:
    if lesson.get("cancelled"):
        return "cancelled"
    if lesson.get("changed"):
        return "changed"
    return ""


def _slots(lessons: Sequence[Mapping]) -> list[list[Mapping]]:
    """Группирует уроки одного времени (подгруппы, совместные уроки)."""
    return [list(g) for _, g in groupby(lessons, key=lambda l: (l["number"], _status(l)))]


def _subjects(slot: Sequence[Mapping]) -> list[str]:
    return list(dict.fromkeys(l["subject"] for l in slot))


def _slot_title(slot: Sequence[Mapping]) -> str:
    first = slot[0]
    status = _status(first)
    if status == "cancelled":
        return f"❌ <s>{esc(first['subject'])}</s> — {esc(first.get('cancel_text') or 'отменён')}"
    if status == "changed":
        title = f"🔄 <b>{esc(first['subject'])}</b>"
        was = first.get("was")
        if was and was != first["subject"]:
            title += f" <i>(вместо: {esc(was)})</i>"
        return title
    return " / ".join(f"<b>{esc(s)}</b>" for s in _subjects(slot))


def _slot_details(slot: Sequence[Mapping], show_group: bool) -> list[str]:
    first = slot[0]
    lines: list[str] = []
    if show_group:
        lines.append("👥 " + ", ".join(esc(l["group_name"]) for l in slot))
    elif not first.get("cancelled"):
        many = len(slot) > 1
        for lesson in slot:
            parts = []
            if lesson.get("teacher_name"):
                parts.append(f"👨‍🏫 {esc(lesson['teacher_name'])}")
            if lesson.get("room"):
                parts.append(f"📍 {esc(lesson['room'])}")
            if parts:
                prefix = f"{esc(lesson['subject'])}: " if many else ""
                lines.append(prefix + "  ".join(parts))
    if first.get("note"):
        lines.append(f"💬 {esc(first['note'])}")
    return lines


_REGULAR = ("", "regular", "normal", "base", "weekly", "none")
_CANCELLED = ("cancel", "cancelled", "canceled", "removed", "deleted")


def _status(row: Mapping) -> str:
    """'' — обычный урок, 'changed' — замена, 'cancelled' — урока нет."""
    raw = str(row.get("status") or row.get("change") or "").lower()
    if (
        raw in _CANCELLED
        or any(row.get(k) for k in ("cancelled", "canceled", "is_cancelled"))
        or str(row.get("subject") or "").strip() in ("", "-", "—")
    ):
        return "cancelled"
    if raw not in _REGULAR or any(
        row.get(k) for k in ("changed", "is_changed", "replaced", "added")
    ):
        return "changed"
    return ""


def _uniq(values: Iterable) -> list[str]:
    """Уникальные непустые значения с сохранением порядка."""
    return list(dict.fromkeys(str(v) for v in values if v))


def format_day_schedule(
    lessons: Sequence[Mapping], title: str, show_group: bool = False
) -> str:
    """Расписание на один день. Изменения помечаются только значком 🔄."""
    header = f"📅 <b>Расписание</b>\n<i>{esc(title)}</i>\n"
    rows = [dict(lesson) for lesson in lessons]
    if not rows:
        return header + "\nЗанятий нет — можно отдыхать 🎉"

    rows.sort(key=lambda r: r.get("number") or 0)
    lines = [header]
    for number, grp in groupby(rows, key=lambda r: r.get("number")):
        grp = list(grp)
        first = grp[0]
        statuses = [_status(r) for r in grp]

        start, end = first.get("time_start"), first.get("time_end")
        time = f" {esc(start)}–{esc(end)}" if start and end else ""
        line = f"\n<b>{esc(number)}.</b>{time} — "
        notes = ", ".join(esc(n) for n in _uniq(r.get("note") for r in grp))
        note = f" · <i>{notes}</i>" if notes else ""

        if all(s == "cancelled" for s in statuses):
            lines.append(line + "🔄 <i>урока нет</i>" + note)
            continue

        active = [r for r, s in zip(grp, statuses) if s != "cancelled"]
        mark = "🔄 " if any(statuses) else ""
        subject = " / ".join(esc(s) for s in _uniq(r.get("subject") for r in active))

        if show_group:
            who_list = _uniq(r.get("group_name") for r in active)
            who = f"👥 {', '.join(esc(w) for w in who_list)}" if who_list else ""
        else:
            who_list = _uniq(r.get("teacher_name") for r in active)
            who = f"👨‍🏫 {', '.join(esc(w) for w in who_list)}" if who_list else ""
        rooms = _uniq(r.get("room") for r in active)
        room = f"📍 {', '.join(esc(x) for x in rooms)}" if rooms else ""

        details = "  ".join(p for p in (room, who) if p)
        lines.append(
            f"{line}{mark}<b>{subject}</b>"
            + (f"\n   {details}" if details else "")
            + note
        )
    return fit("".join(lines))


def format_week_schedule(
    days: Sequence[tuple[date, Sequence[Mapping]]], title: str, show_group: bool = False
) -> str:
    parts = [f"📅 <b>Расписание на неделю</b>\n<i>{esc(title)}</i>\n"]
    has_changes = False
    for d, lessons in days:
        changed = any(l.get("changed") for l in lessons)
        has_changes = has_changes or changed
        parts.append(
            f"\n<b>— {config.WEEKDAYS[d.isoweekday()]}, {d.strftime('%d.%m')} —</b>"
            + (" ⚠️" if changed else "")
        )
        if not lessons:
            parts.append("\n<i>занятий нет</i>\n")
            continue
        for slot in _slots(lessons):
            first = slot[0]
            status = _status(first)
            if status == "cancelled":
                subj = f"❌ <s>{esc(first['subject'])}</s>"
            elif status == "changed":
                subj = f"🔄 {esc(first['subject'])}"
            else:
                subj = " / ".join(esc(s) for s in _subjects(slot))
            extra = (
                " (" + ", ".join(esc(l["group_name"]) for l in slot) + ")" if show_group else ""
            )
            parts.append(f"\n{first['number']}. {esc(first['time_start'])} {subj}{extra}")
        parts.append("\n")
    if has_changes:
        parts.append("\n🔄 — замена   ❌ — отменён")
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

