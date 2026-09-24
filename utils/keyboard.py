"""
Инлайн-клавиатуры.

Формат callback_data: 'раздел:действие:аргументы' (лимит Telegram — 64 байта).
Длинные списки режутся на страницы функцией paged().
"""
from __future__ import annotations

import math
from typing import Sequence

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

import config

PER_PAGE = 8
SHORT_DAYS = {1: "Пн", 2: "Вт", 3: "Ср", 4: "Чт", 5: "Пт", 6: "Сб", 7: "Вс"}

BACK_MAIN = InlineKeyboardButton("🏠 Главное меню", callback_data="nav:main")


def _rows(buttons: Sequence[InlineKeyboardButton], per_row: int = 2):
    return [list(buttons[i : i + per_row]) for i in range(0, len(buttons), per_row)]


def page_of(index: int) -> int:
    """На какой странице находится элемент с данным индексом."""
    return index // PER_PAGE


def paged(
    items: Sequence[tuple[str, str]],
    page: int,
    page_prefix: str,
    footer: list[list[InlineKeyboardButton]],
    per_row: int = 1,
) -> InlineKeyboardMarkup:
    """
    Список кнопок (текст, callback) с постраничной навигацией.
    Кнопки листания шлют '<page_prefix>:<номер страницы>'.
    """
    pages = max(1, math.ceil(len(items) / PER_PAGE))
    page = min(max(page, 0), pages - 1)  # защита от подделанного номера
    chunk = items[page * PER_PAGE : (page + 1) * PER_PAGE]
    keyboard = _rows(
        [InlineKeyboardButton(text, callback_data=cb) for text, cb in chunk], per_row
    )
    if pages > 1:
        prev_btn = (
            InlineKeyboardButton("◀️", callback_data=f"{page_prefix}:{page - 1}")
            if page > 0 else InlineKeyboardButton(" ", callback_data="noop")
        )
        next_btn = (
            InlineKeyboardButton("▶️", callback_data=f"{page_prefix}:{page + 1}")
            if page < pages - 1 else InlineKeyboardButton(" ", callback_data="noop")
        )
        keyboard.append(
            [prev_btn, InlineKeyboardButton(f"{page + 1}/{pages}", callback_data="noop"), next_btn]
        )
    return InlineKeyboardMarkup(keyboard + footer)


# --- Главное меню -----------------------------------------------------------
def main_menu(is_admin: bool = False) -> InlineKeyboardMarkup:
    keyboard = [
        [
            InlineKeyboardButton("📅 Расписание", callback_data="sch:root"),
            InlineKeyboardButton("🍽 Столовая", callback_data="menu:root"),
        ],
        [
            InlineKeyboardButton("📞 Контакты", callback_data="con:list"),
            InlineKeyboardButton("📢 Объявления", callback_data="news:list"),
        ],
        [
            InlineKeyboardButton("❓ FAQ", callback_data="faq:list"),
            InlineKeyboardButton("⚙️ Настройки", callback_data="set:root"),
        ],
    ]
    if is_admin:
        keyboard.append([InlineKeyboardButton("🛠 Админ-панель", callback_data="adm:root")])
    return InlineKeyboardMarkup(keyboard)


# --- Расписание -------------------------------------------------------------
def schedule_root(has_group: bool, has_teacher: bool) -> InlineKeyboardMarkup:
    keyboard = []
    if has_group:
        keyboard.append([InlineKeyboardButton("⭐️ Мой класс", callback_data="sch:my")])
    if has_teacher:
        keyboard.append([InlineKeyboardButton("⭐️ Мои занятия", callback_data="sch:mine")])
    keyboard += [
        [InlineKeyboardButton("👥 Выбрать класс", callback_data="sch:groups")],
        [InlineKeyboardButton("👨‍🏫 По преподавателю", callback_data="sch:teachers")],
        [BACK_MAIN],
    ]
    return InlineKeyboardMarkup(keyboard)


def group_list(groups: Sequence, prefix: str, back_cb: str) -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(g["name"], callback_data=f"{prefix}:{g['id']}") for g in groups
    ]
    return InlineKeyboardMarkup(
        _rows(buttons, 3)
        + [[InlineKeyboardButton("⬅️ Назад", callback_data=back_cb), BACK_MAIN]]
    )


def schedule_teachers(teachers: Sequence, page: int) -> InlineKeyboardMarkup:
    items = [(t["full_name"], f"sch:t:{t['id']}") for t in teachers]
    return paged(
        items, page, "sch:teachers",
        [[InlineKeyboardButton("⬅️ Назад", callback_data="sch:root"), BACK_MAIN]],
    )


def schedule_days(scope: str, obj_id: int) -> InlineKeyboardMarkup:
    base = f"sch:show:{scope}:{obj_id}"
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Сегодня", callback_data=f"{base}:today"),
                InlineKeyboardButton("Завтра", callback_data=f"{base}:tomorrow"),
            ],
            [
                InlineKeyboardButton(SHORT_DAYS[d], callback_data=f"{base}:{d}")
                for d in config.STUDY_DAYS
            ],
            [InlineKeyboardButton("🗓 Вся неделя", callback_data=f"{base}:week")],
            [InlineKeyboardButton("⬅️ Назад", callback_data="sch:root"), BACK_MAIN],
        ]
    )


# --- Меню столовой ----------------------------------------------------------
def menu_days() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Сегодня", callback_data="menu:show:today"),
                InlineKeyboardButton("Завтра", callback_data="menu:show:tomorrow"),
            ],
            [
                InlineKeyboardButton(SHORT_DAYS[d], callback_data=f"menu:show:{d}")
                for d in config.STUDY_DAYS
            ],
            [InlineKeyboardButton("🗓 Вся неделя", callback_data="menu:show:week")],
            [BACK_MAIN],
        ]
    )


# --- Контакты ---------------------------------------------------------------
def contacts_list(teachers: Sequence, page: int) -> InlineKeyboardMarkup:
    items = [
        (t["full_name"], f"con:t:{t['id']}:{page_of(i)}") for i, t in enumerate(teachers)
    ]
    return paged(items, page, "con:list", [[BACK_MAIN]])


def contact_card(teacher_id: int, page: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📅 Расписание", callback_data=f"sch:t:{teacher_id}")],
            [InlineKeyboardButton("⬅️ К списку", callback_data=f"con:list:{page}"), BACK_MAIN],
        ]
    )


# --- FAQ --------------------------------------------------------------------
def faq_list(items: Sequence) -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(
            item["question"] if len(item["question"]) <= 60 else item["question"][:57] + "…",
            callback_data=f"faq:show:{item['id']}",
        )]
        for item in items
    ]
    return InlineKeyboardMarkup(buttons + [[BACK_MAIN]])


def faq_back() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("⬅️ К вопросам", callback_data="faq:list"), BACK_MAIN]]
    )


# --- Настройки --------------------------------------------------------------
def settings_menu(subscribed: bool, can_change_group: bool) -> InlineKeyboardMarkup:
    toggle = (
        InlineKeyboardButton("🔕 Отключить объявления", callback_data="set:sub:0")
        if subscribed
        else InlineKeyboardButton("🔔 Включить объявления", callback_data="set:sub:1")
    )
    keyboard = [[toggle]]
    if can_change_group:
        keyboard.append([InlineKeyboardButton("🎒 Сменить класс", callback_data="set:grp")])
    keyboard.append([BACK_MAIN])
    return InlineKeyboardMarkup(keyboard)


# --- Админ-панель -----------------------------------------------------------
def admin_root() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("📊 Статистика", callback_data="adm:stats"),
                InlineKeyboardButton("🍽 Меню столовой", callback_data="adm:menu"),
            ],
            [InlineKeyboardButton("✏️ Изменения в расписании", callback_data="chg:root")],
            [InlineKeyboardButton("📢 Создать объявление", callback_data="adm:ann")],

            [
                InlineKeyboardButton("🎟 Коды классов", callback_data="adm:inv"),
                InlineKeyboardButton("👨‍🏫 Преподаватели", callback_data="adm:tch:0"),
            ],
            [BACK_MAIN],
        ]
    )


def admin_back() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("⬅️ В админ-панель", callback_data="adm:root")]]
    )


def admin_menu_days() -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(config.WEEKDAYS[d], callback_data=f"adm:mday:{d}")
        for d in config.STUDY_DAYS
    ]
    return InlineKeyboardMarkup(
        _rows(buttons, 2) + [[InlineKeyboardButton("⬅️ Назад", callback_data="adm:root")]]
    )


def admin_meal_types(weekday: int) -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(title, callback_data=f"adm:meal:{weekday}:{key}")
        for key, title in config.MEAL_TYPES.items()
    ]
    return InlineKeyboardMarkup(
        _rows(buttons, 1) + [[InlineKeyboardButton("⬅️ Назад", callback_data="adm:menu")]]
    )


def admin_dish_editor(weekday: int, meal_type: str, dishes: Sequence) -> InlineKeyboardMarkup:
    keyboard = [
        [InlineKeyboardButton(f"❌ {dish['name']}", callback_data=f"adm:ddel:{dish['id']}")]
        for dish in dishes
    ]
    keyboard += [
        [InlineKeyboardButton("➕ Добавить блюдо", callback_data=f"adm:dadd:{weekday}:{meal_type}")],
        [InlineKeyboardButton("🗑 Очистить приём пищи", callback_data=f"adm:dclr:{weekday}:{meal_type}")],
        [InlineKeyboardButton("⬅️ Назад", callback_data=f"adm:mday:{weekday}")],
    ]
    return InlineKeyboardMarkup(keyboard)


def confirm_broadcast() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[
            InlineKeyboardButton("✅ Отправить всем", callback_data="adm:annsend"),
            InlineKeyboardButton("❌ Отмена", callback_data="adm:anncancel"),
        ]]
    )


def admin_invite_groups(groups: Sequence) -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(g["name"], callback_data=f"adm:ig:{g['id']}") for g in groups
    ]
    return InlineKeyboardMarkup(
        _rows(buttons, 3) + [[InlineKeyboardButton("⬅️ Назад", callback_data="adm:root")]]
    )


def admin_group_invite(group_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🔄 Выпустить новый код", callback_data=f"adm:igr:{group_id}")],
            [InlineKeyboardButton("⬅️ К классам", callback_data="adm:inv")],
        ]
    )


def admin_teachers(teachers: Sequence, linked: set[int], page: int) -> InlineKeyboardMarkup:
    items = [
        (("✅ " if t["id"] in linked else "▫️ ") + t["full_name"],
         f"adm:t:{t['id']}:{page_of(i)}")
        for i, t in enumerate(teachers)
    ]
    return paged(
        items, page, "adm:tch",
        [[InlineKeyboardButton("⬅️ Назад", callback_data="adm:root")]],
    )


def admin_teacher_card(teacher_id: int, page: int, linked: bool) -> InlineKeyboardMarkup:
    keyboard = [[InlineKeyboardButton(
        "🔗 Выдать ссылку для входа", callback_data=f"adm:tl:{teacher_id}:{page}"
    )]]
    if linked:
        keyboard.append([InlineKeyboardButton(
            "✂️ Отвязать аккаунт", callback_data=f"adm:tu:{teacher_id}:{page}"
        )])
    keyboard.append([InlineKeyboardButton("⬅️ К списку", callback_data=f"adm:tch:{page}")])
    return InlineKeyboardMarkup(keyboard)


def admin_teacher_back(teacher_id: int, page: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("⬅️ К преподавателю", callback_data=f"adm:t:{teacher_id}:{page}")]]
    )



# --- Срочные изменения расписания -------------------------------------------
def changes_dates(days: Sequence, marked: set[str], today) -> InlineKeyboardMarkup:
    """Ближайшие учебные дни; ✏️ — на эту дату уже есть изменения."""
    buttons = []
    for d in days:
        if d == today:
            label = "Сегодня"
        elif (d - today).days == 1:
            label = "Завтра"
        else:
            label = SHORT_DAYS[d.isoweekday()]
        mark = "✏️ " if d.isoformat() in marked else ""
        buttons.append(InlineKeyboardButton(
            f"{mark}{label} {d.strftime('%d.%m')}", callback_data=f"chg:d:{d.strftime('%Y%m%d')}"
        ))
    return InlineKeyboardMarkup(
        _rows(buttons, 2) + [[InlineKeyboardButton("⬅️ Назад", callback_data="adm:root")]]
    )


def changes_groups(groups: Sequence, day_key: str, marked: set[int]) -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(
            ("✏️ " if g["id"] in marked else "") + g["name"],
            callback_data=f"chg:g:{day_key}:{g['id']}",
        )
        for g in groups
    ]
    return InlineKeyboardMarkup(
        _rows(buttons, 3) + [[InlineKeyboardButton("⬅️ К датам", callback_data="chg:root")]]
    )


def changes_editor(day_key: str, group_id: int, changes: Sequence) -> InlineKeyboardMarkup:
    keyboard = []
    for c in changes:
        what = "отмена" if c["subject"] is None else c["subject"]
        keyboard.append([InlineKeyboardButton(
            f"❌ {c['number']} урок: {what}"[:60], callback_data=f"chg:x:{c['id']}"
        )])
    keyboard.append([InlineKeyboardButton(
        "✍️ Внести изменения", callback_data=f"chg:in:{day_key}:{group_id}"
    )])
    if changes:
        keyboard.append([
            InlineKeyboardButton("📣 Уведомить класс", callback_data=f"chg:ntf:{day_key}:{group_id}"),
            InlineKeyboardButton("🗑 Сбросить всё", callback_data=f"chg:clr:{day_key}:{group_id}"),
        ])
    keyboard.append([
        InlineKeyboardButton("⬅️ К классам", callback_data=f"chg:d:{day_key}"),
        InlineKeyboardButton("🛠 Админ-панель", callback_data="adm:root"),
    ])
    return InlineKeyboardMarkup(keyboard)


def changes_notify(day_key: str, group_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Отправить", callback_data=f"chg:snd:{day_key}:{group_id}"),
        InlineKeyboardButton("❌ Не надо", callback_data=f"chg:g:{day_key}:{group_id}"),
    ]])


def changes_back(day_key: str, group_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("⬅️ К изменениям", callback_data=f"chg:g:{day_key}:{group_id}"),
        InlineKeyboardButton("🛠 Админ-панель", callback_data="adm:root"),
    ]])


# --- Гость и «Назад» --------------------------------------------------------
def guest_menu() -> InlineKeyboardMarkup:
    """Экран до ввода кода приглашения."""
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("❓ Частые вопросы", callback_data="faq:list")]]
    )


def back_to(callback: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("⬅️ Назад", callback_data=callback), BACK_MAIN]]
    )
