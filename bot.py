"""
Точка входа Telegram-бота школы.

Запуск: python bot.py
"""
from __future__ import annotations

import html
import logging
import logging.handlers
import sys
import traceback

from telegram import BotCommandScopeChat, BotCommandScopeDefault, Update
from telegram.constants import ParseMode
from telegram.error import Forbidden, NetworkError, TelegramError
from telegram.ext import AIORateLimiter, Application, ApplicationBuilder, ContextTypes

import config
from data import sample_data
from database import db, init_db
from handlers import register_all

logger = logging.getLogger(__name__)


def setup_logging() -> None:
    """Логи в консоль + ротация файла (5 файлов по 2 МБ)."""
    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
    )
    root = logging.getLogger()
    root.setLevel(getattr(logging, config.LOG_LEVEL, logging.INFO))

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(formatter)
    root.addHandler(console)

    file_handler = logging.handlers.RotatingFileHandler(
        config.LOG_FILE, maxBytes=2_000_000, backupCount=5, encoding="utf-8"
    )
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)

    # httpx слишком болтлив на уровне INFO
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("apscheduler").setLevel(logging.WARNING)
    logging.getLogger("telegram.ext.Application").setLevel(logging.INFO)


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Глобальный обработчик ошибок: пишет traceback в лог и уведомляет
    администраторов, чтобы проблемы не оставались незамеченными.
    """
    if isinstance(context.error, NetworkError):
        # Сетевые сбои PTB переживает сам — не спамим админов
        logger.warning("Сетевая ошибка: %s", context.error)
        return

    logger.error("Необработанное исключение", exc_info=context.error)

    tb = "".join(
        traceback.format_exception(
            type(context.error), context.error, context.error.__traceback__
        )
    )[-2500:]
    chat_id = (
        update.effective_chat.id
        if isinstance(update, Update) and update.effective_chat
        else "—"
    )
    message = (
        "⚠️ <b>Ошибка в боте</b>\n"
        f"chat_id: <code>{chat_id}</code>\n"
        f"<pre>{html.escape(tb)}</pre>"
    )
    for admin_id in config.ADMIN_IDS:
        try:
            await context.bot.send_message(
                admin_id, message, parse_mode=ParseMode.HTML
            )
        except TelegramError:
            pass

    # Сообщаем пользователю, что запрос не прошёл
    if isinstance(update, Update) and update.effective_message:
        try:
            await update.effective_message.reply_text(
                "😔 Произошла ошибка. Мы уже знаем о ней, попробуйте позже."
            )
        except (TelegramError, Forbidden):
            pass


async def post_init(app: Application) -> None:
    """Выполняется после инициализации: БД, seed-данные, меню команд."""
    init_db(config.DB_PATH)
    await db.connect()
    if config.SEED_DEMO:
        await sample_data.seed(db)

    # Общее меню команд для всех пользователей
    await app.bot.set_my_commands(config.USER_COMMANDS, scope=BotCommandScopeDefault())

    # Расширенное меню для админов. Сработает только если чат с ботом уже
    # существует (админ хоть раз нажимал /start); иначе Telegram вернёт
    # "Chat not found" — это не ошибка, меню поставится при первом /start.
    for admin_id in config.ADMIN_IDS:
        try:
            await app.bot.set_my_commands(
                config.ADMIN_COMMANDS, scope=BotCommandScopeChat(admin_id)
            )
        except TelegramError as exc:
            logger.info(
                "Меню админа %s будет установлено после его первого /start (%s)",
                admin_id,
                exc,
            )

    me = await app.bot.get_me()
    logger.info("Бот @%s запущен", me.username)


async def post_shutdown(app: Application) -> None:
    """Аккуратно закрываем соединение с БД."""
    await db.close()


def main() -> None:
    setup_logging()
    config.validate()

    application = (
        ApplicationBuilder()
        .token(config.BOT_TOKEN)
        # AIORateLimiter соблюдает лимиты Telegram автоматически
        .rate_limiter(AIORateLimiter(max_retries=3))
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )

    register_all(application)
    application.add_error_handler(error_handler)

    logger.info("Запуск polling…")
    application.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True,  # не обрабатывать накопившееся за время простоя
    )


if __name__ == "__main__":
    try:
        main()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Остановлено пользователем")
    except Exception:
        logging.getLogger(__name__).critical("Критический сбой", exc_info=True)
        raise
