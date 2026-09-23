"""Общие обработчики: /start, /help, /faq, /news, настройки, навигация."""
from __future__ import annotations

import logging

from telegram import BotCommandScopeChat, Update
from telegram.constants import ParseMode
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

import config
from database import db
from utils import formatting as fmt
from utils import keyboard as kb
from utils.decorators import is_admin, safe_handler

logger = logging.getLogger(__name__)

HELP_TEXT = (
    "ℹ️ <b>Что я умею</b>\n\n"
    "/schedule — расписание занятий (день/неделя, по группе или преподавателю)\n"
    "/menu — меню столовой\n"
    "/contacts — контакты преподавателей\n"
    "/news — последние объявления школы\n"
    "/faq — частые вопросы\n"
    "/settings — группа и уведомления\n"
    "/help — эта справка\n\n"
    "Навигация — кнопками под сообщениями. "
    f"Вопросы и правки: {config.SUPPORT_CONTACT}"
)


async def _greeting(user_row, first_name: str) -> str:
    """Формирует приветствие с учётом сохранённой группы/роли."""
    text = (
        f"👋 Здравствуйте, {fmt.esc(first_name)}!\n\n"
        f"Я бот <b>{fmt.esc(config.SCHOOL_NAME)}</b>. "
        "Помогу узнать расписание, меню столовой, контакты преподавателей "
        "и не пропустить объявления."
    )
    if user_row and user_row["group_id"]:
        group = await db.get_group(user_row["group_id"])
        if group:
            text += f"\n\n⭐️ Ваша группа: <b>{fmt.esc(group['name'])}</b>"
    elif user_row and user_row["teacher_id"]:
        teacher = await db.get_teacher(user_row["teacher_id"])
        if teacher:
            text += f"\n\n⭐️ Ваш профиль: <b>{fmt.esc(teacher['full_name'])}</b>"
    return text


async def _ensure_admin_commands(
    user_id: int, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """
    Ставит админское меню команд.

    Персональный scope можно применить только к существующему чату,
    поэтому делаем это при /start, а не на старте приложения.
    """
    if not is_admin(user_id):
        return
    try:
        await context.bot.set_my_commands(
            config.ADMIN_COMMANDS, scope=BotCommandScopeChat(user_id)
        )
    except TelegramError as exc:  # не критично для работы бота
        logger.warning("Не удалось обновить меню админа %s: %s", user_id, exc)


@safe_handler
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Регистрирует пользователя и показывает главное меню."""
    user = update.effective_user
    if user is None or update.message is None:
        return

    await db.upsert_user(user.id, user.username, user.full_name)
    user_row = await db.get_user(user.id)

    # Администраторам сразу проставляем роль admin и расширенное меню команд
    if is_admin(user.id):
        if user_row and user_row["role"] != "admin":
            await db.set_user_role(
                user.id, "admin", user_row["group_id"], user_row["teacher_id"]
            )
            user_row = await db.get_user(user.id)
        await _ensure_admin_commands(user.id, context)

    text = await _greeting(user_row, user.first_name or "друг")

    # Если роль ещё не выбрана — предлагаем определиться
    if user_row and not user_row["group_id"] and not user_row["teacher_id"]:
        text += "\n\nКто вы? Это нужно, чтобы показывать нужное расписание."
        await update.message.reply_text(
            text, parse_mode=ParseMode.HTML, reply_markup=kb.role_choice()
        )
        return

    await update.message.reply_text(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=kb.main_menu(is_admin(user.id)),
    )


@safe_handler
async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message:
        await update.message.reply_text(
            HELP_TEXT,
            parse_mode=ParseMode.HTML,
            reply_markup=kb.main_menu(is_admin(update.effective_user.id)),
        )


@safe_handler
async def nav_main(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Возврат в главное меню по кнопке."""
    query = update.callback_query
    await query.answer()
    user = update.effective_user
    user_row = await db.get_user(user.id)
    text = await _greeting(user_row, user.first_name or "друг")
    await query.edit_message_text(
        text, parse_mode=ParseMode.HTML, reply_markup=kb.main_menu(is_admin(user.id))
    )


# --- Регистрация роли -------------------------------------------------------
@safe_handler
async def reg_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработка выбора роли: reg:student / reg:teacher / reg:skip."""
    query = update.callback_query
    await query.answer()
    action = query.data.split(":")[1]
    user_id = update.effective_user.id

    if action == "skip":
        await query.edit_message_text(
            "Хорошо, можно указать позже в /settings.",
            reply_markup=kb.main_menu(is_admin(user_id)),
        )
        return

    if action == "student":
        groups = await db.get_groups()
        await query.edit_message_text(
            "Выберите свой класс:", reply_markup=kb.group_list(groups, "reg:g")
        )
        return

    teachers = await db.get_teachers()
    await query.edit_message_text(
        "Найдите себя в списке:", reply_markup=kb.teacher_list(teachers, "reg:t")
    )


@safe_handler
async def reg_save(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Сохраняет привязку: reg:g:<group_id> или reg:t:<teacher_id>."""
    query = update.callback_query
    _, scope, raw_id = query.data.split(":")
    obj_id = int(raw_id)
    user = update.effective_user

    # Сначала проверяем, что объект существует — callback_data нельзя доверять
    if scope == "g":
        obj = await db.get_group(obj_id)
        label = obj["name"] if obj else None
    else:
        obj = await db.get_teacher(obj_id)
        label = obj["full_name"] if obj else None

    if label is None:
        await query.answer("Этой записи больше нет, выберите заново", show_alert=True)
        return
    await query.answer()

    # На случай, если пользователь не нажимал /start (например, после сброса БД)
    await db.upsert_user(user.id, user.username, user.full_name)

    role = "admin" if is_admin(user.id) else ("student" if scope == "g" else "teacher")
    if scope == "g":
        await db.set_user_role(user.id, role, group_id=obj_id)
    else:
        await db.set_user_role(user.id, role, teacher_id=obj_id)

    await query.edit_message_text(
        f"✅ Готово! Профиль: <b>{fmt.esc(label)}</b>\n\nЧем помочь?",
        parse_mode=ParseMode.HTML,
        reply_markup=kb.main_menu(is_admin(user.id)),
    )


# --- Объявления (просмотр) --------------------------------------------------
@safe_handler
async def cmd_news(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    rows = await db.get_announcements(limit=5)
    text = fmt.format_announcements(rows)
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=kb.main_menu(is_admin(update.effective_user.id)),
        )
    elif update.message:
        await update.message.reply_text(text, parse_mode=ParseMode.HTML)


# --- FAQ --------------------------------------------------------------------
@safe_handler
async def cmd_faq(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    items = await db.get_faq()
    text = "❓ <b>Частые вопросы</b>\n\nВыберите вопрос:"
    if not items:
        text = "Список вопросов пока пуст."
    markup = kb.faq_list(items)
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(
            text, parse_mode=ParseMode.HTML, reply_markup=markup
        )
    elif update.message:
        await update.message.reply_text(
            text, parse_mode=ParseMode.HTML, reply_markup=markup
        )


@safe_handler
async def faq_show(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    item_id = int(query.data.split(":")[2])
    item = await db.get_faq_item(item_id)
    if item is None:
        await query.edit_message_text("Вопрос не найден.", reply_markup=kb.faq_back())
        return
    await query.edit_message_text(
        f"❓ <b>{fmt.esc(item['question'])}</b>\n\n{fmt.esc(item['answer'])}",
        parse_mode=ParseMode.HTML,
        reply_markup=kb.faq_back(),
    )


# --- Настройки --------------------------------------------------------------
def _settings_view(subscribed: bool):
    """Текст и клавиатура экрана настроек (без отправки)."""
    text = (
        "⚙️ <b>Настройки</b>\n\n"
        f"Объявления: {'включены 🔔' if subscribed else 'отключены 🔕'}"
    )
    return text, kb.settings_menu(subscribed)


@safe_handler
async def cmd_settings(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_row = await db.get_user(update.effective_user.id)
    subscribed = bool(user_row["subscribed"]) if user_row else True
    text, markup = _settings_view(subscribed)
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(
            text, parse_mode=ParseMode.HTML, reply_markup=markup
        )
    elif update.message:
        await update.message.reply_text(
            text, parse_mode=ParseMode.HTML, reply_markup=markup
        )


@safe_handler
async def settings_action(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """set:sub:<0|1> и set:role."""
    query = update.callback_query
    parts = query.data.split(":")

    if parts[1] == "sub":
        subscribed = parts[2] == "1"
        await db.set_subscription(update.effective_user.id, subscribed)
        await query.answer(
            "Объявления включены 🔔" if subscribed else "Объявления отключены 🔕"
        )
        text, markup = _settings_view(subscribed)
        await query.edit_message_text(
            text, parse_mode=ParseMode.HTML, reply_markup=markup
        )
        return

    await query.answer()
    await query.edit_message_text("Кто вы?", reply_markup=kb.role_choice())


def register(app: Application) -> None:
    """Регистрирует обработчики этого модуля."""
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("news", cmd_news))
    app.add_handler(CommandHandler("faq", cmd_faq))
    app.add_handler(CommandHandler("settings", cmd_settings))

    app.add_handler(CallbackQueryHandler(nav_main, pattern=r"^nav:main$"))
    app.add_handler(
        CallbackQueryHandler(reg_choice, pattern=r"^reg:(student|teacher|skip)$")
    )
    app.add_handler(CallbackQueryHandler(reg_save, pattern=r"^reg:[gt]:\d+$"))
    app.add_handler(CallbackQueryHandler(cmd_news, pattern=r"^news:list$"))
    app.add_handler(CallbackQueryHandler(cmd_faq, pattern=r"^faq:list$"))
    app.add_handler(CallbackQueryHandler(faq_show, pattern=r"^faq:show:\d+$"))
    app.add_handler(CallbackQueryHandler(cmd_settings, pattern=r"^set:root$"))
    app.add_handler(
        CallbackQueryHandler(settings_action, pattern=r"^set:(sub:[01]|role)$")
    )
