"""
Расписание на конкретную дату: недельная сетка + срочные изменения.

Все функции возвращают список dict (не sqlite Row) с ключами урока и
дополнительными полями: changed, cancelled, note, was, cancel_text.
"""
from __future__ import annotations

from datetime import date, timedelta
from itertools import groupby
from typing import Mapping

import config
from database import db
from utils import formatting as fmt


def upcoming(weekday: int) -> date:
    """Ближайшая дата с этим днём недели (сегодня или позже)."""
    d = fmt.today()
    return d + timedelta(days=(weekday - d.isoweekday()) % 7)


def week_dates() -> list[date]:
    """Учебные дни текущей недели; в выходные — следующей."""
    d = fmt.today()
    monday = d - timedelta(days=d.isoweekday() - 1)
    if d.isoweekday() > max(config.STUDY_DAYS):
        monday += timedelta(days=7)
    return [monday + timedelta(days=wd - 1) for wd in config.STUDY_DAYS]


def _plain(row: Mapping) -> dict:
    item = dict(row)
    item.update(changed=False, cancelled=False, note=None, was=None)
    return item


def _from_change(ch: Mapping, subject: str, cancelled: bool, was: str | None) -> dict:
    return {
        "number": ch["number"],
        "time_start": ch["time_start"],
        "time_end": ch["time_end"],
        "subject": subject,
        "teacher_id": ch["teacher_id"],
        "teacher_name": ch["teacher_name"],
        "group_id": ch["group_id"],
        "group_name": ch["group_name"],
        "group_sort": ch["group_sort"],
        "room": None,
        "changed": True,
        "cancelled": cancelled,
        "note": ch["note"],
        "was": was,
    }


def _subjects(rows) -> str | None:
    names = list(dict.fromkeys(r["subject"] for r in rows))
    return " / ".join(names) if names else None


async def group_day(group_id: int, d: date) -> list[dict]:
    """Уроки класса в конкретный день с учётом изменений."""
    base = await db.get_lessons_by_group(group_id, d.isoweekday())
    changes = {c["number"]: c for c in await db.get_changes(d.isoformat(), group_id)}
    by_num = {n: list(rows) for n, rows in groupby(base, key=lambda r: r["number"])}

    result: list[dict] = []
    for n in sorted(set(by_num) | set(changes)):
        orig = by_num.get(n, [])
        ch = changes.get(n)
        if ch is None:
            result.extend(_plain(r) for r in orig)
            continue
        was = _subjects(orig)
        if ch["subject"] is None:
            result.append(_from_change(ch, was or "Урок", True, None))
        else:
            result.append(_from_change(ch, ch["subject"], False, was))
    return result


async def teacher_day(teacher_id: int, d: date) -> list[dict]:
    """Уроки преподавателя в конкретный день с учётом изменений во всех классах."""
    base = await db.get_lessons_by_teacher(teacher_id, d.isoweekday())
    changes = await db.get_changes(d.isoformat())
    slots = {(c["group_id"], c["number"]): c for c in changes}

    result: list[dict] = []
    for row in base:
        ch = slots.get((row["group_id"], row["number"]))
        if ch is None:
            result.append(_plain(row))
        elif ch["subject"] is None or ch["teacher_id"] != teacher_id:
            item = _plain(row)
            if ch["subject"] is not None:
                who = ch["teacher_name"] or "другой преподаватель"
                item.update(cancel_text="замена", note=f"урок ведёт: {who}")
            else:
                item["note"] = ch["note"]
            item.update(changed=True, cancelled=True)
            result.append(item)
        # иначе урок по-прежнему за этим преподавателем — добавится из изменений ниже

    for ch in changes:
        if ch["teacher_id"] == teacher_id and ch["subject"] is not None:
            result.append(_from_change(ch, ch["subject"], False, None))

    result.sort(key=lambda l: (l["number"], l["cancelled"], l["changed"], l.get("group_sort", 0)))
    return result
