"""
Конфигурация бота.

Все секреты берутся из переменных окружения (файл .env, который добавлен
в .gitignore). Токен НИКОГДА не хранится в коде.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dotenv import load_dotenv
from telegram import BotCommand

logger = logging.getLogger(__name__)

# Базовый каталог проекта
BASE_DIR = Path(__file__).resolve().parent

# Загружаем переменные из .env (если файл существует)
load_dotenv(BASE_DIR / ".env")


def _get_admin_ids(raw: str | None) -> set[int]:
    """Парсит строку вида '12345,67890' в множество Telegram user_id."""
    if not raw:
        return set()
    ids: set[int] = set()
    for chunk in raw.replace(";", ",").split(","):
        chunk = chunk.strip()
        if chunk.isdigit():
            ids.add(int(chunk))
    return ids


def _load_timezone(name: str):
    """
    Загружает часовой пояс IANA.

    На Windows и в slim-образах Linux системной базы tz нет — она приходит
    из пакета tzdata. Если пояс не найден, не падаем, а откатываемся
    на локальное время системы: работающий бот важнее точности TZ.
    """
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        logger.warning(
            "Часовой пояс %r не найден. Установите пакет tzdata "
            "(pip install tzdata). Использую локальное время системы.",
            name,
        )
    except Exception:  # некорректное значение в .env
        logger.warning("Некорректный TIMEZONE=%r, использую локальное время.", name)
    return datetime.now().astimezone().tzinfo


# --- Основные настройки -----------------------------------------------------
BOT_TOKEN: str = os.getenv("BOT_TOKEN", "").strip()
ADMIN_IDS: set[int] = _get_admin_ids(os.getenv("ADMIN_IDS"))
DB_PATH: Path = Path(os.getenv("DB_PATH", BASE_DIR / "school_bot.db"))

SCHOOL_NAME: str = os.getenv("SCHOOL_NAME", "Частная школа «Пример»")
SUPPORT_CONTACT: str = os.getenv("SUPPORT_CONTACT", "@school_admin")

# Часовой пояс: нужен, чтобы «сегодня» считалось по времени школы,
# а не по времени сервера (часто UTC).
TIMEZONE_NAME: str = os.getenv("TIMEZONE", "Asia/Yerevan")
TIMEZONE = _load_timezone(TIMEZONE_NAME)

LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO").upper()

# Заливать демонстрационные данные в пустую БД (только для разработки)
SEED_DEMO: bool = os.getenv("SEED_DEMO", "0") == "1"
LOG_FILE: Path = Path(os.getenv("LOG_FILE", BASE_DIR / "bot.log"))

# Пауза между сообщениями при рассылке (Telegram: ~30 сообщений/сек).
BROADCAST_DELAY: float = float(os.getenv("BROADCAST_DELAY", "0.05"))

# --- Справочники ------------------------------------------------------------
# Дни недели: 1 = понедельник (совпадает с datetime.isoweekday()).
WEEKDAYS: dict[int, str] = {
    1: "Понедельник",
    2: "Вторник",
    3: "Среда",
    4: "Четверг",
    5: "Пятница",
    6: "Суббота",
    7: "Воскресенье",
}
STUDY_DAYS: tuple[int, ...] = (1, 2, 3, 4, 5)  # учебная неделя

MEAL_TYPES: dict[str, str] = {
    "breakfast": "🥣 Завтрак",
    "lunch": "🍲 Обед",
    "snack": "🍎 Полдник",
}

ROLES: dict[str, str] = {
    "student": "Ученик / родитель",
    "teacher": "Преподаватель",
    "admin": "Администратор",
}

# --- Меню команд бота -------------------------------------------------------
# Держим здесь, а не в bot.py, чтобы обработчики могли импортировать
# списки без циклического импорта.
USER_COMMANDS: list[BotCommand] = [
    BotCommand("start", "Запустить бота / главное меню"),
    BotCommand("schedule", "📅 Расписание занятий"),
    BotCommand("menu", "🍽 Меню столовой"),
    BotCommand("contacts", "📞 Контакты преподавателей"),
    BotCommand("news", "📢 Объявления"),
    BotCommand("faq", "❓ Частые вопросы"),
    BotCommand("settings", "⚙️ Настройки"),
    BotCommand("help", "ℹ️ Справка"),
]

ADMIN_COMMANDS: list[BotCommand] = USER_COMMANDS + [
    BotCommand("admin", "🛠 Админ-панель"),
    BotCommand("announce", "📢 Создать объявление"),
    BotCommand("cancel", "Отменить текущее действие"),
]


def validate() -> None:
    """Проверка конфигурации при старте — падаем сразу, а не в рантайме."""
    if not BOT_TOKEN:
        raise RuntimeError(
            "Не задан BOT_TOKEN. Скопируйте .env.example в .env "
            "и укажите токен, полученный у @BotFather."
        )
    if not ADMIN_IDS:
        raise RuntimeError(
            "Не задан ADMIN_IDS. Узнайте свой user_id у @userinfobot "
            "и добавьте его в .env."
        )
