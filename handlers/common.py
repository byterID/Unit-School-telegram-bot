"""
Общие обработчики: приветствие, вход по коду, главное меню,
объявления, FAQ и настройки.

Команд для учеников нет: диалог начинается с любого сообщения («привет»),
дальше всё кнопками. /start остаётся только технически — его шлёт кнопка
«Запустить» в Telegram и ссылки-приглашения t.me/<бот>?start=<код>.
"""
from __future__ import annotations

import logging
import re
import time

from telegram import BotCommandScopeChat, InlineKeyboardMarkup, Update
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

import config
from database import db
from utils import formatting as fmt
from utils import keyboard as kb
from utils import screen
from utils.decorators import admin_only, is_admin, safe_handler

logger = logging.getLogger(__name__)

GREETING_RE = re.compile(
    r"^\W*(привет\w*|здравствуй\w*|здрасьте|добр\w*|салют|хай|hello|hi|hey|начать|старт|меню)\b",
    re.IGNORECASE,
)
CODE_RE = re.compile(r"^[A-Z0-9]{6,12}$")
LINK_RE = re.compile(r"start=([A-Za-z0-9]{6,12})")
# Кириллица, похожая на латиницу: код часто набирают на русской раскладке
LOOKALIKE = str.maketrans("АВЕКМНОРСТХУ", "ABEKMHOPCTXY")
MAX_FAILS = 5
LOCK_SECONDS = 10 * 60
AWAIT = "await"  # чего ждём текстом: "grp_code" — код нового класса


def _extract_code(text: str) -> str | None:
    m = LINK_RE.search(text)
    raw = m.group(1) if m else re.sub(r"[\s-]", "", text)
    raw = raw.upper().translate(LOOKALIKE)
    return raw if CODE_RE.match(raw) else None


def _has_access(row, user_id: int) -> bool:
    return is_admin(user_id) or bool(row and row["verified"])


async def _ensure_admin_commands(user_id: int, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        await context.bot.set_my_commands(
            config.ADMIN_COMMANDS, scope=BotCommandScopeChat(user_id)
        )
    except TelegramError as exc:
        logger.warning("Не удалось обновить меню админа %s: %s", user_id, exc)


async def _prepare_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Регистрирует/обновляет пользователя, админам — роль и меню команд."""
    user = update.effective_user
    await db.upsert_user(user.id, user.username, user.full_name)
    if is_admin(user.id):
        await db.ensure_admin(user.id)
        if not context.user_data.get("_admin_cmds"):
            await _ensure_admin_commands(user.id, context)
            context.user_data["_admin_cmds"] = True
    return await db.get_user(user.id)


async def _profile_line(row) -> str:
    if row and row["group_id"]:
        group = await db.get_group(row["group_id"])
        if group:
            return f"⭐️ Ваш класс: <b>{fmt.esc(group['name'])}</b>"
    if row and row["teacher_id"]:
        teacher = await db.get_teacher(row["teacher_id"])
        if teacher:
            return f"⭐️ Ваш профиль: <b>{fmt.esc(teacher['full_name'])}</b>"
    return ""


async def _show_home(update: Update, context: ContextTypes.DEFAULT_TYPE, row, notice: str = "") -> None:
    user = update.effective_user
    context.user_data.pop(AWAIT, None)
    text = (
        f"👋 Привет, {fmt.esc(user.first_name or 'друг')}!\n\n"
        f"Я бот <b>{fmt.esc(config.SCHOOL_NAME)}</b>: расписание, меню столовой, "
        "контакты преподавателей и объявления."
    )
    if _has_access(row, user.id):
        if line := await _profile_line(row):
            text += f"\n\n{line}"
        markup = kb.main_menu(is_admin(user.id))
    else:
        text += (
            "\n\n🔑 Чтобы начать, пришлите <b>код приглашения</b> вашего класса — "
            "его выдаёт классный руководитель. Или просто откройте ссылку-приглашение."
        )
        markup = kb.guest_menu()
    if notice:
        text = f"{notice}\n\n{text}"
    await screen.show(update, context, text, markup)


async def _redeem(update: Update, context: ContextTypes.DEFAULT_TYPE, code: str) -> None:
    user = update.effective_user
    ud = context.user_data
    back = kb.back_to("set:root") if ud.get(AWAIT) == "grp_code" else kb.guest_menu()

    now = time.monotonic()
    if ud.get("code_lock", 0) > now:
        mins = int((ud["code_lock"] - now) // 60) + 1
        await screen.show(
            update, context,
            f"⏳ Слишком много неверных попыток. Попробуйте через {mins} мин.",
            back, keep_input=True,
        )
        return

    invite = await db.redeem_invite(code, user.id)
    if invite is None:
        fails = ud.get("code_fails", 0) + 1
        ud["code_fails"] = fails
        left = MAX_FAILS - fails
        if left <= 0:
            ud["code_lock"], ud["code_fails"] = now + LOCK_SECONDS, 0
        logger.info("Неверный код от %s", user.id)
        await screen.show(
            update, context,
            f"❌ Код <code>{fmt.esc(code)}</code> не подошёл.\n\n"
            "Проверьте написание или попросите новый код у классного руководителя."
            + (f"\nОсталось попыток: {left}" if left > 0 else ""),
            back, keep_input=True,
        )
        return

    ud.pop("code_fails", None)
    logger.info("Пользователь %s вошёл по коду (%s)", user.id, invite["kind"])
    await _show_home(update, context, await db.get_user(user.id), notice="✅ Код принят!")


# --- Точки входа ------------------------------------------------------------
@safe_handler
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Кнопка «Запустить» и ссылки-приглашения. Само сообщение /start удаляется."""
    if update.message is None:
        return
    row = await _prepare_user(update, context)
    code = _extract_code(context.args[0]) if context.args else None
    if code:
        await _redeem(update, context, code)
    else:
        await _show_home(update, context, row)


@safe_handler
async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Любой текст вне админских диалогов: «привет», код приглашения и т.п."""
    msg = update.message
    if msg is None or not msg.text:
        return
    row = await _prepare_user(update, context)
    text = msg.text.strip()

    if GREETING_RE.match(text):
        await _show_home(update, context, row)
        return

    waiting_code = context.user_data.get(AWAIT) == "grp_code"
    if waiting_code or not _has_access(row, update.effective_user.id):
        if code := _extract_code(text):
            await _redeem(update, context, code)
        elif waiting_code:
            await screen.show(
                update, context,
                "🤔 Это не похоже на код. Код — 8 латинских букв и цифр, например <code>K7MP2QXA</code>.",
                kb.back_to("set:root"), keep_input=True,
            )
        else:
            await _show_home(update, context, row, notice="🤔 Это не похоже на код приглашения.")
        return

    await _show_home(update, context, row)


@safe_handler
async def on_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Неизвестные/старые команды (/schedule и т.п.) — просто главное меню."""
    row = await _prepare_user(update, context)
    await _show_home(update, context, row)


async def on_other(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Стикеры, фото, голосовые — не засоряем историю."""
    if update.message:
        await screen.delete(context.bot, update.message.chat_id, update.message.message_id)


@safe_handler
async def nav_main(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.callback_query.answer()
    row = await _prepare_user(update, context)
    await _show_home(update, context, row)


# --- Объявления и FAQ -------------------------------------------------------
@safe_handler
async def show_news(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.callback_query.answer()
    rows = await db.get_announcements(limit=5)
    await screen.show(
        update, context, fmt.format_announcements(rows), InlineKeyboardMarkup([[kb.BACK_MAIN]])
    )


@safe_handler
async def faq_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.callback_query.answer()
    items = await db.get_faq()
    text = "❓ <b>Частые вопросы</b>\n\nВыберите вопрос:" if items else "Список вопросов пока пуст."
    await screen.show(update, context, text, kb.faq_list(items))


@safe_handler
async def faq_show(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    item = await db.get_faq_item(int(query.data.split(":")[2]))
    if item is None:
        await screen.show(update, context, "Вопрос не найден.", kb.faq_back())
        return
    await screen.show(
        update, context,
        f"❓ <b>{fmt.esc(item['question'])}</b>\n\n{fmt.esc(item['answer'])}",
        kb.faq_back(),
    )


# --- Настройки --------------------------------------------------------------
async def _settings_view(user_id: int):
    row = await db.get_user(user_id)
    subscribed = bool(row["subscribed"]) if row else True
    # Преподавателю класс не нужен; ученикам и админам — можно менять
    can_change_group = bool(row) and not row["teacher_id"]
    lines = ["⚙️ <b>Настройки</b>", ""]
    if profile := await _profile_line(row):
        lines.append(profile)
    lines.append(f"Объявления: {'включены 🔔' if subscribed else 'отключены 🔕'}")
    return "\n".join(lines), kb.settings_menu(subscribed, can_change_group)


@safe_handler
async def show_settings(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.callback_query.answer()
    context.user_data.pop(AWAIT, None)
    text, markup = await _settings_view(update.effective_user.id)
    await screen.show(update, context, text, markup)


@safe_handler
async def settings_sub(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """set:sub:<0|1>"""
    query = update.callback_query
    subscribed = query.data.endswith(":1")
    await db.set_subscription(update.effective_user.id, subscribed)
    await query.answer("Объявления включены 🔔" if subscribed else "Объявления отключены 🔕")
    text, markup = await _settings_view(update.effective_user.id)
    await screen.show(update, context, text, markup)


@safe_handler
async def settings_group(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """set:grp — админ выбирает из списка, остальные вводят код нового класса."""
    query = update.callback_query
    await query.answer()
    if is_admin(update.effective_user.id):
        groups = await db.get_groups()
        await screen.show(
            update, context, "🎒 Выберите класс:", kb.group_list(groups, "set:g", "set:root")
        )
        return
    context.user_data[AWAIT] = "grp_code"
    await screen.show(
        update, context,
        "🎒 Пришлите <b>код нового класса</b> — его выдаёт классный руководитель.",
        kb.back_to("set:root"),
    )


@admin_only
@safe_handler
async def settings_group_save(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """set:g:<id> — только для админов."""
    query = update.callback_query
    group = await db.get_group(int(query.data.split(":")[2]))
    if group is None:
        await query.answer("Класс не найден", show_alert=True)
        return
    await db.set_group(update.effective_user.id, group["id"])
    await query.answer(f"Класс: {group['name']}")
    text, markup = await _settings_view(update.effective_user.id)
    await screen.show(update, context, text, markup)


def register(app: Application) -> None:
    app.add_handler(CommandHandler("start", cmd_start))

    app.add_handler(CallbackQueryHandler(nav_main, pattern=r"^nav:main$"))
    app.add_handler(CallbackQueryHandler(show_news, pattern=r"^news:list$"))
    app.add_handler(CallbackQueryHandler(faq_list, pattern=r"^faq:list$"))
    app.add_handler(CallbackQueryHandler(faq_show, pattern=r"^faq:show:\d+$"))
    app.add_handler(CallbackQueryHandler(show_settings, pattern=r"^set:root$"))
    app.add_handler(CallbackQueryHandler(settings_sub, pattern=r"^set:sub:[01]$"))
    app.add_handler(CallbackQueryHandler(settings_group, pattern=r"^set:grp$"))
    app.add_handler(CallbackQueryHandler(settings_group_save, pattern=r"^set:g:\d+$"))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    app.add_handler(MessageHandler(filters.COMMAND, on_command))
    app.add_handler(MessageHandler(~filters.TEXT & ~filters.COMMAND, on_other))
