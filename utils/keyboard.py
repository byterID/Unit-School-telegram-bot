"""
Инлайн-клавиатуры.

Формат callback_data: 'раздел:действие:аргументы' — короткие строки,
чтобы гарантированно уложиться в лимит Telegram (64 байта).
"""
from __future__ import annotations

from typing import Sequence

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

import config


def _rows(buttons: Sequence[InlineKeyboardButton], per_row: int = 2):
    """Разбивает список кнопок на строки по per_row штук."""
    return [list(buttons[i : i + per_row]) for i in range(0, len(buttons), per_row)]


BACK_MAIN = InlineKeyboardButton("🏠 Главное меню", callback_data="nav:main")


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
        keyboard.append(
            [InlineKeyboardButton("🛠 Админ-панель", callback_data="adm:root")]
        )
    return InlineKeyboardMarkup(keyboard)


# --- Роли при регистрации ---------------------------------------------------
def role_choice() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🎒 Ученик / родитель", callback_data="reg:student")],
            [InlineKeyboardButton("👨‍🏫 Преподаватель", callback_data="reg:teacher")],
            [InlineKeyboardButton("⏭ Пропустить", callback_data="reg:skip")],
        ]
    )


# --- Расписание -------------------------------------------------------------
def schedule_root(has_group: bool, has_teacher: bool) -> InlineKeyboardMarkup:
    keyboard = []
    if has_group:
        keyboard.append(
            [InlineKeyboardButton("⭐️ Моя группа", callback_data="sch:my")]
        )
    if has_teacher:
        keyboard.append(
            [InlineKeyboardButton("⭐️ Мои занятия", callback_data="sch:mine")]
        )
    keyboard += [
        [InlineKeyboardButton("👥 Выбрать группу", callback_data="sch:groups")],
        [InlineKeyboardButton("👨‍🏫 По преподавателю", callback_data="sch:teachers")],
        [BACK_MAIN],
    ]
    return InlineKeyboardMarkup(keyboard)


def group_list(groups: Sequence, prefix: str = "sch:g") -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(g["name"], callback_data=f"{prefix}:{g['id']}")
        for g in groups
    ]
    return InlineKeyboardMarkup(
        _rows(buttons, 2)
        + [[InlineKeyboardButton("⬅️ Назад", callback_data="sch:root"), BACK_MAIN]]
    )


def teacher_list(teachers: Sequence, prefix: str) -> InlineKeyboardMarkup:
    """Универсальный список преподавателей (для расписания и контактов)."""
    buttons = [
        InlineKeyboardButton(t["full_name"], callback_data=f"{prefix}:{t['id']}")
        for t in teachers
    ]
    back = "sch:root" if prefix.startswith("sch") else "nav:main"
    return InlineKeyboardMarkup(
        _rows(buttons, 1)
        + [[InlineKeyboardButton("⬅️ Назад", callback_data=back), BACK_MAIN]]
    )


def schedule_days(scope: str, obj_id: int) -> InlineKeyboardMarkup:
    """
    Выбор дня для расписания.
    scope: 'g' (группа) или 't' (преподаватель).
    """
    base = f"sch:show:{scope}:{obj_id}"
    keyboard = [
        [
            InlineKeyboardButton("Сегодня", callback_data=f"{base}:today"),
            InlineKeyboardButton("Завтра", callback_data=f"{base}:tomorrow"),
        ],
        [InlineKeyboardButton("🗓 Вся неделя", callback_data=f"{base}:week")],
    ]
    day_buttons = [
        InlineKeyboardButton(
            config.WEEKDAYS[d][:3], callback_data=f"{base}:{d}"
        )
        for d in config.STUDY_DAYS
    ]
    keyboard += _rows(day_buttons, 5)
    back = "sch:groups" if scope == "g" else "sch:teachers"
    keyboard.append(
        [InlineKeyboardButton("⬅️ Назад", callback_data=back), BACK_MAIN]
    )
    return InlineKeyboardMarkup(keyboard)


# --- Меню столовой ----------------------------------------------------------
def menu_days() -> InlineKeyboardMarkup:
    keyboard = [
        [
            InlineKeyboardButton("Сегодня", callback_data="menu:show:today"),
            InlineKeyboardButton("Завтра", callback_data="menu:show:tomorrow"),
        ],
        [InlineKeyboardButton("🗓 Вся неделя", callback_data="menu:show:week")],
    ]
    day_buttons = [
        InlineKeyboardButton(config.WEEKDAYS[d][:3], callback_data=f"menu:show:{d}")
        for d in config.STUDY_DAYS
    ]
    keyboard += _rows(day_buttons, 5)
    keyboard.append([BACK_MAIN])
    return InlineKeyboardMarkup(keyboard)


# --- FAQ --------------------------------------------------------------------
def faq_list(items: Sequence) -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(item["question"], callback_data=f"faq:show:{item['id']}")
        for item in items
    ]
    return InlineKeyboardMarkup(_rows(buttons, 1) + [[BACK_MAIN]])


def faq_back() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("⬅️ К вопросам", callback_data="faq:list"), BACK_MAIN]]
    )


# --- Настройки --------------------------------------------------------------
def settings_menu(subscribed: bool) -> InlineKeyboardMarkup:
    toggle = (
        InlineKeyboardButton("🔕 Отключить объявления", callback_data="set:sub:0")
        if subscribed
        else InlineKeyboardButton("🔔 Включить объявления", callback_data="set:sub:1")
    )
    return InlineKeyboardMarkup(
        [
            [toggle],
            [InlineKeyboardButton("👥 Сменить группу/роль", callback_data="set:role")],
            [BACK_MAIN],
        ]
    )


# --- Админ-панель -----------------------------------------------------------
def admin_root() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🍽 Редактировать меню", callback_data="adm:menu")],
            [InlineKeyboardButton("📢 Новое объявление", callback_data="adm:ann")],
            [InlineKeyboardButton("📊 Статистика", callback_data="adm:stats")],
            [BACK_MAIN],
        ]
    )


def admin_menu_days() -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(
            config.WEEKDAYS[d], callback_data=f"adm:mday:{d}"
        )
        for d in config.STUDY_DAYS
    ]
    return InlineKeyboardMarkup(
        _rows(buttons, 2)
        + [[InlineKeyboardButton("⬅️ Назад", callback_data="adm:root")]]
    )


def admin_meal_types(weekday: int) -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(title, callback_data=f"adm:meal:{weekday}:{key}")
        for key, title in config.MEAL_TYPES.items()
    ]
    return InlineKeyboardMarkup(
        _rows(buttons, 1)
        + [[InlineKeyboardButton("⬅️ Назад", callback_data="adm:menu")]]
    )


def admin_dish_editor(
    weekday: int, meal_type: str, dishes: Sequence
) -> InlineKeyboardMarkup:
    """Список блюд с кнопками удаления + добавление."""
    keyboard = [
        [
            InlineKeyboardButton(
                f"❌ {dish['name']}", callback_data=f"adm:ddel:{dish['id']}"
            )
        ]
        for dish in dishes
    ]
    keyboard += [
        [
            InlineKeyboardButton(
                "➕ Добавить блюдо", callback_data=f"adm:dadd:{weekday}:{meal_type}"
            )
        ],
        [
            InlineKeyboardButton(
                "🗑 Очистить приём пищи",
                callback_data=f"adm:dclr:{weekday}:{meal_type}",
            )
        ],
        [InlineKeyboardButton("⬅️ Назад", callback_data=f"adm:mday:{weekday}")],
    ]
    return InlineKeyboardMarkup(keyboard)


def confirm_broadcast() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ Отправить всем", callback_data="adm:annsend"),
                InlineKeyboardButton("❌ Отмена", callback_data="adm:anncancel"),
            ]
        ]
    )
