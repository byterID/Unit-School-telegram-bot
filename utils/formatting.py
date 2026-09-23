"""Функции сборки текстов сообщений (HTML parse_mode)."""
from __future__ import annotations

from datetime import date, datetime, timedelta
from html import escape
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


def esc(value: object) -> str:
    """Безопасное экранирование для HTML-разметки Telegram."""
    return escape(str(value if value is not None else ""), quote=False)


# --- Расписание -------------------------------------------------------------
def format_day_schedule(
    lessons: Sequence[Mapping], title: str, show_group: bool = False
) -> str:
    """Расписание на один день."""
    header = f"📅 <b>Расписание</b>\n<i>{esc(title)}</i>\n"
    if not lessons:
        return header + "\nЗанятий нет — можно отдыхать 🎉"

    lines = [header]
    for lesson in lessons:
        who = (
            f"👥 {esc(lesson['group_name'])}"
            if show_group
            else (f"👨‍🏫 {esc(lesson['teacher_name'])}" if lesson["teacher_name"] else "")
        )
        room = f"📍 {esc(lesson['room'])}" if lesson["room"] else ""
        details = "   " + "  ".join(part for part in (room, who) if part)
        lines.append(
            f"\n<b>{lesson['number']}.</b> {esc(lesson['time_start'])}"
            f"–{esc(lesson['time_end'])} — <b>{esc(lesson['subject'])}</b>"
            + (f"\n{details}" if details.strip() else "")
        )
    return "".join(lines)


def format_week_schedule(
    lessons: Iterable[Mapping], title: str, show_group: bool = False
) -> str:
    """Расписание на всю неделю, сгруппированное по дням."""
    by_day: dict[int, list[Mapping]] = {}
    for lesson in lessons:
        by_day.setdefault(lesson["weekday"], []).append(lesson)

    parts = [f"📅 <b>Расписание на неделю</b>\n<i>{esc(title)}</i>\n"]
    for weekday in config.STUDY_DAYS:
        day_lessons = by_day.get(weekday, [])
        parts.append(f"\n<b>— {config.WEEKDAYS[weekday]} —</b>")
        if not day_lessons:
            parts.append("\n<i>занятий нет</i>")
            continue
        for lesson in day_lessons:
            extra = (
                esc(lesson["group_name"]) if show_group else esc(lesson["room"] or "")
            )
            parts.append(
                f"\n{esc(lesson['time_start'])} — {esc(lesson['subject'])}"
                + (f" ({extra})" if extra else "")
            )
        parts.append("\n")
    return "".join(parts)


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
        phone = esc(teacher["phone"])
        # tel: делает номер кликабельным в мобильном клиенте
        lines.append(f'📞 Телефон: <a href="tel:{phone}">{phone}</a>')
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
def format_announcements(rows: Sequence[Mapping]) -> str:
    if not rows:
        return "📢 Объявлений пока нет."
    parts = ["📢 <b>Последние объявления</b>\n"]
    for row in rows:
        created = datetime.fromisoformat(row["created_at"]).strftime("%d.%m.%Y %H:%M")
        parts.append(f"\n<i>{created}</i>\n{esc(row['text'])}\n")
    return "".join(parts)
