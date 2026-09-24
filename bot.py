"""
Точка входа Telegram-бота школы.

Запуск: python bot.py
"""
from __future__ import annotations

import html
import logging
import warnings

from telegram.warnings import PTBUserWarning

# диалоги смешивают кнопки и текстовый ввод
warnings.filterwarnings("ignore", message=r".*per_message=False.*", category=PTBUserWarning)

import logging.handlers
import sys
import traceback

from telegram import BotCommandScopeChat, BotCommandScopeDefault, Update
from telegram.constants import ParseMode
from telegram.error import Forbidden, NetworkError, TelegramError
from telegram.ext import AIORateLimiter, Application, ApplicationBuilder, ContextTypes

import config
from data.import_faq import DEFAULT_FAQ
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

    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("apscheduler").setLevel(logging.WARNING)
    logging.getLogger("telegram.ext.Application").setLevel(logging.INFO)


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Пишет traceback в лог и уведомляет администраторов."""
    if isinstance(context.error, NetworkError):
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
            await context.bot.send_message(admin_id, message, parse_mode=ParseMode.HTML)
        except TelegramError:
            pass

    if isinstance(update, Update) and update.effective_message:
        try:
            await update.effective_message.reply_text(
                "😔 Произошла ошибка. Мы уже знаем о ней, попробуйте позже."
            )
        except (TelegramError, Forbidden):
            pass


async def post_init(app: Application) -> None:
    """БД, FAQ по умолчанию, меню команд."""
    init_db(config.DB_PATH)
    await db.connect()
    if not await db.get_faq():
        await db.replace_faq(DEFAULT_FAQ)
        logger.info("Загружен FAQ по умолчанию (%d вопросов)", len(DEFAULT_FAQ))

    # У всех убираем меню команд; админам ставим своё
    await app.bot.delete_my_commands(scope=BotCommandScopeDefault())
    for admin_id in config.ADMIN_IDS:
        try:
            await app.bot.set_my_commands(
                config.ADMIN_COMMANDS, scope=BotCommandScopeChat(admin_id)
            )
        except TelegramError as exc:
            logger.info("Меню админа %s поставится после его первого сообщения (%s)", admin_id, exc)

    me = await app.bot.get_me()
    logger.info("Бот @%s запущен", me.username)


async def post_shutdown(app: Application) -> None:
    await db.close()


def main() -> None:
    setup_logging()
    config.validate()

    application = (
        ApplicationBuilder()
        .token(config.BOT_TOKEN)
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
        drop_pending_updates=True,
    )


if __name__ == "__main__":
    try:
        main()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Остановлено пользователем")
    except Exception:
        logging.getLogger(__name__).critical("Критический сбой", exc_info=True)
        raise
