"""
Админ-панель: меню столовой, объявления, коды классов, привязка
преподавателей, статистика. Доступ — только для ADMIN_IDS.
"""
from __future__ import annotations

import asyncio
import logging

from telegram import CallbackQuery, Update
from telegram.constants import ParseMode
from telegram.error import Forbidden, RetryAfter, TelegramError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

import config
from database import db
from utils import formatting as fmt
from utils import keyboard as kb
from utils.decorators import admin_only, safe_handler

logger = logging.getLogger(__name__)

ADD_DISH, ANN_TEXT = range(2)


def _invite_link(context: ContextTypes.DEFAULT_TYPE, code: str) -> str:
    return f"https://t.me/{context.bot.username}?start={code}"


# --- Корень -----------------------------------------------------------------
@admin_only
@safe_handler
async def cmd_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = "🛠 <b>Админ-панель</b>\n\nВыберите действие:"
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(
            text, parse_mode=ParseMode.HTML, reply_markup=kb.admin_root()
        )
    elif update.message:
        await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=kb.admin_root())


@admin_only
@safe_handler
async def admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    s = await db.get_stats()
    text = (
        "📊 <b>Статистика</b>\n\n"
        f"👥 Всего нажимали /start: {s.get('users_total', 0)}\n"
        f"🔑 Вошли по коду: {s.get('verified', 0)}\n"
        f"🎒 Учеников/родителей: {s.get('students', 0)}\n"
        f"👨‍🏫 Преподавателей: {s.get('teachers_users', 0)}\n"
        f"🔔 Получают объявления: {s.get('subscribed', 0)}\n"
        f"🚫 Заблокировали бота: {s.get('blocked', 0)}\n\n"
        f"📅 Уроков в расписании: {s.get('lessons', 0)}\n"
        f"🍽 Блюд в меню: {s.get('dishes', 0)}\n"
        f"📢 Объявлений: {s.get('announcements', 0)}"
    )
    await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=kb.admin_root())


# --- Меню столовой ----------------------------------------------------------
@admin_only
@safe_handler
async def menu_choose_day(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("🍽 Выберите день недели:", reply_markup=kb.admin_menu_days())


@admin_only
@safe_handler
async def menu_choose_meal(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    weekday = int(query.data.split(":")[2])
    await query.edit_message_text(
        f"🍽 <b>{config.WEEKDAYS[weekday]}</b>\n\nКакой приём пищи редактируем?",
        parse_mode=ParseMode.HTML,
        reply_markup=kb.admin_meal_types(weekday),
    )


async def _render_dish_editor(query: CallbackQuery, weekday: int, meal_type: str) -> None:
    dishes = await db.get_dishes(weekday, meal_type)
    listing = "\n".join(f"• {fmt.esc(d['name'])}" for d in dishes) if dishes else "<i>пусто</i>"
    await query.edit_message_text(
        f"🍽 <b>{config.WEEKDAYS[weekday]}</b> — {config.MEAL_TYPES.get(meal_type, meal_type)}\n\n"
        f"{listing}\n\nНажмите на блюдо, чтобы удалить его.",
        parse_mode=ParseMode.HTML,
        reply_markup=kb.admin_dish_editor(weekday, meal_type, dishes),
    )


@admin_only
@safe_handler
async def menu_edit_meal(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    _, _, raw_day, meal_type = query.data.split(":")
    await _render_dish_editor(query, int(raw_day), meal_type)


@admin_only
@safe_handler
async def dish_delete(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    dish_id = int(query.data.split(":")[2])
    dish = await db.get_dish(dish_id)
    if dish is None:
        await query.answer("Блюдо уже удалено", show_alert=True)
        return
    await db.delete_dish(dish_id)
    await query.answer(f"Удалено: {dish['name']}")
    logger.info("Админ %s удалил блюдо id=%s", update.effective_user.id, dish_id)
    await _render_dish_editor(query, dish["weekday"], dish["meal_type"])


@admin_only
@safe_handler
async def meal_clear(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    _, _, raw_day, meal_type = query.data.split(":")
    await db.clear_meal(int(raw_day), meal_type)
    await query.answer("Приём пищи очищен")
    await _render_dish_editor(query, int(raw_day), meal_type)


@admin_only
async def dish_add_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    _, _, raw_day, meal_type = query.data.split(":")
    context.user_data["dish_weekday"] = int(raw_day)
    context.user_data["dish_meal"] = meal_type
    await query.edit_message_text(
        f"✍️ Пришлите название блюда для <b>{config.WEEKDAYS[int(raw_day)]}</b> — "
        f"{config.MEAL_TYPES.get(meal_type, meal_type)}.\n\n"
        "Можно несколько — каждое с новой строки.\nОтмена: /cancel",
        parse_mode=ParseMode.HTML,
    )
    return ADD_DISH


@admin_only
async def dish_add_save(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    weekday = context.user_data.get("dish_weekday")
    meal_type = context.user_data.get("dish_meal")
    if weekday is None or meal_type is None:
        await update.message.reply_text("Сессия истекла, начните заново: /admin")
        return ConversationHandler.END

    names = [line.strip() for line in update.message.text.splitlines() if line.strip()]
    if not names:
        await update.message.reply_text("Пустое название. Попробуйте ещё раз.")
        return ADD_DISH
    for name in names[:30]:
        await db.add_dish(weekday, meal_type, name[:200])

    dishes = await db.get_dishes(weekday, meal_type)
    await update.message.reply_text(
        fmt.fit(f"✅ Добавлено: {min(len(names), 30)}\n\n"
                + "\n".join(f"• {fmt.esc(d['name'])}" for d in dishes)),
        parse_mode=ParseMode.HTML,
        reply_markup=kb.admin_dish_editor(weekday, meal_type, dishes),
    )
    context.user_data.pop("dish_weekday", None)
    context.user_data.pop("dish_meal", None)
    return ConversationHandler.END


# --- Объявления -------------------------------------------------------------
@admin_only
async def ann_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = "📢 Пришлите текст объявления (можно с форматированием).\n\nОтмена: /cancel"
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(text)
    else:
        await update.message.reply_text(text)
    return ANN_TEXT


@admin_only
async def ann_preview(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    msg = update.message
    plain = (msg.text or "").strip()
    if not plain:
        await msg.reply_text("Текст пустой, попробуйте снова.")
        return ANN_TEXT
    if len(plain) > 3500:
        await msg.reply_text(
            f"Слишком длинно: {len(plain)} символов (максимум 3500). Сократите и пришлите снова."
        )
        return ANN_TEXT

    context.user_data["ann_text"] = msg.text_html  # безопасный HTML из entities
    targets = await db.get_broadcast_targets()
    await msg.reply_text(
        "<b>Предпросмотр объявления:</b>\n\n"
        f"📢 {context.user_data['ann_text']}\n\n"
        f"Получателей: <b>{len(targets)}</b>. Отправить?",
        parse_mode=ParseMode.HTML,
        reply_markup=kb.confirm_broadcast(),
    )
    return ConversationHandler.END


@admin_only
@safe_handler
async def ann_cancel_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer("Отменено")
    context.user_data.pop("ann_text", None)
    await query.edit_message_text("❌ Объявление не отправлено.", reply_markup=kb.admin_root())


@admin_only
@safe_handler
async def ann_send(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    text = context.user_data.pop("ann_text", None)
    if not text:
        await query.edit_message_text(
            "Текст потерян, создайте объявление заново.", reply_markup=kb.admin_root()
        )
        return
    ann_id = await db.add_announcement(update.effective_user.id, text)
    await query.edit_message_text("📤 Рассылка запущена…")
    context.application.create_task(_broadcast(context, ann_id, text, query.message.chat_id))


async def _broadcast(
    context: ContextTypes.DEFAULT_TYPE, ann_id: int, text: str, report_chat_id: int
) -> None:
    targets = await db.get_broadcast_targets()
    payload = f"📢 <b>Объявление</b>\n\n{text}"
    sent = failed = blocked = 0
    for user_id in targets:
        try:
            await context.bot.send_message(user_id, payload, parse_mode=ParseMode.HTML)
            sent += 1
        except Forbidden:
            await db.mark_blocked(user_id)
            blocked += 1
        except RetryAfter as exc:
            await asyncio.sleep(float(exc.retry_after) + 1)
            try:
                await context.bot.send_message(user_id, payload, parse_mode=ParseMode.HTML)
                sent += 1
            except TelegramError:
                failed += 1
        except TelegramError as exc:
            logger.error("Не удалось отправить %s: %s", user_id, exc)
            failed += 1
        await asyncio.sleep(config.BROADCAST_DELAY)

    await db.set_announcement_sent(ann_id, sent)
    await context.bot.send_message(
        report_chat_id,
        f"✅ Рассылка завершена\n\nДоставлено: {sent}\n"
        f"Заблокировали бота: {blocked}\nОшибок: {failed}",
        reply_markup=kb.admin_root(),
    )


# --- Коды классов -----------------------------------------------------------
@admin_only
@safe_handler
async def inv_groups(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    groups = await db.get_groups()
    if not groups:
        await query.edit_message_text(
            "Классов пока нет — сначала импортируйте расписание.", reply_markup=kb.admin_back()
        )
        return
    await query.edit_message_text(
        "🎟 <b>Коды приглашения</b>\n\nУ каждого класса свой код. Выберите класс:",
        parse_mode=ParseMode.HTML,
        reply_markup=kb.admin_invite_groups(groups),
    )


@admin_only
@safe_handler
async def inv_show(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """adm:ig:<id> — показать код; adm:igr:<id> — выпустить новый."""
    query = update.callback_query
    action, raw_id = query.data.split(":")[1:]
    group_id = int(raw_id)
    group = await db.get_group(group_id)
    if group is None:
        await query.answer("Класс не найден", show_alert=True)
        return

    invite = None if action == "igr" else await db.get_active_invite("group", group_id)
    if invite is None:
        code, uses = await db.create_invite("group", group_id, update.effective_user.id), 0
        logger.info("Админ %s выпустил код для класса %s", update.effective_user.id, group_id)
    else:
        code, uses = invite["code"], invite["uses"]
    await query.answer("Новый код выпущен ✅" if action == "igr" else None)

    await query.edit_message_text(
        f"🎟 <b>{fmt.esc(group['name'])}</b>\n\n"
        f"Код: <code>{code}</code>\n"
        f"Ссылка: {_invite_link(context, code)}\n"
        f"Воспользовались: {uses}\n\n"
        "Отправьте ссылку в родительский чат класса.\n"
        "Если код попал к посторонним, выпустите новый. Старый сразу перестанет "
        "работать, а те, кто уже вошёл, останутся.",
        parse_mode=ParseMode.HTML,
        reply_markup=kb.admin_group_invite(group_id),
    )


# --- Преподаватели ----------------------------------------------------------
@admin_only
@safe_handler
async def tch_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """adm:tch:<страница>"""
    query = update.callback_query
    await query.answer()
    page = int(query.data.split(":")[2])
    teachers = await db.get_teachers()
    if not teachers:
        await query.edit_message_text(
            "Преподавателей нет — сначала импортируйте расписание.", reply_markup=kb.admin_back()
        )
        return
    linked = await db.get_linked_teacher_ids()
    await query.edit_message_text(
        "👨‍🏫 <b>Преподаватели</b>\n\n"
        "✅ аккаунт привязан, ▫️ ещё нет.\n"
        "Выберите преподавателя, чтобы выдать ему ссылку для входа.",
        parse_mode=ParseMode.HTML,
        reply_markup=kb.admin_teachers(teachers, linked, page),
    )


async def _render_teacher(query: CallbackQuery, teacher_id: int, page: int, extra: str = "") -> None:
    teacher = await db.get_teacher(teacher_id)
    if teacher is None:
        await query.edit_message_text("Преподаватель не найден.", reply_markup=kb.admin_back())
        return
    user = await db.get_teacher_user(teacher_id)
    if user:
        uname = f" (@{fmt.esc(user['username'])})" if user["username"] else ""
        status = f"✅ Привязан: {fmt.esc(user['full_name'])}{uname}"
    else:
        status = "▫️ Аккаунт не привязан"
    await query.edit_message_text(
        f"👨‍🏫 <b>{fmt.esc(teacher['full_name'])}</b>\n"
        f"📚 {fmt.esc(teacher['subject'] or '—')}\n\n{status}{extra}",
        parse_mode=ParseMode.HTML,
        reply_markup=kb.admin_teacher_card(teacher_id, page, user is not None),
    )


@admin_only
@safe_handler
async def tch_card(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """adm:t:<id>:<страница>"""
    query = update.callback_query
    await query.answer()
    _, _, raw_id, raw_page = query.data.split(":")
    await _render_teacher(query, int(raw_id), int(raw_page))


@admin_only
@safe_handler
async def tch_link(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """adm:tl:<id>:<страница> — одноразовая ссылка для входа преподавателя."""
    query = update.callback_query
    _, _, raw_id, raw_page = query.data.split(":")
    teacher_id = int(raw_id)
    if await db.get_teacher(teacher_id) is None:
        await query.answer("Преподаватель не найден", show_alert=True)
        return
    code = await db.create_invite("teacher", teacher_id, update.effective_user.id, max_uses=1)
    await query.answer("Ссылка создана")
    logger.info("Админ %s выдал ссылку преподавателю %s", update.effective_user.id, teacher_id)
    extra = (
        "\n\n🔗 <b>Ссылка для входа</b> (одноразовая):\n"
        f"{_invite_link(context, code)}\n\n"
        "Отправьте её лично преподавателю. Прежние ссылки больше не действуют."
    )
    await _render_teacher(query, teacher_id, int(raw_page), extra)


@admin_only
@safe_handler
async def tch_unlink(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """adm:tu:<id>:<страница>"""
    query = update.callback_query
    _, _, raw_id, raw_page = query.data.split(":")
    await db.unlink_teacher(int(raw_id))
    await query.answer("Аккаунт отвязан")
    logger.info("Админ %s отвязал преподавателя %s", update.effective_user.id, raw_id)
    await _render_teacher(query, int(raw_id), int(raw_page))


# --- Выход из диалогов ------------------------------------------------------
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    for key in ("dish_weekday", "dish_meal", "ann_text"):
        context.user_data.pop(key, None)
    if update.message:
        text = "Отменено." if update.message.text.startswith("/cancel") else \
            "Действие отменено. Повторите команду, если она нужна."
        await update.message.reply_text(text, reply_markup=kb.admin_root())
    return ConversationHandler.END


def register(app: Application) -> None:
    meal_re = "|".join(config.MEAL_TYPES)
    fallbacks = [CommandHandler("cancel", cancel), MessageHandler(filters.COMMAND, cancel)]

    app.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(dish_add_start, pattern=rf"^adm:dadd:[1-7]:({meal_re})$")],
        states={ADD_DISH: [MessageHandler(filters.TEXT & ~filters.COMMAND, dish_add_save)]},
        fallbacks=fallbacks,
        conversation_timeout=300,
        name="add_dish",
    ))
    app.add_handler(ConversationHandler(
        entry_points=[
            CommandHandler("announce", ann_start),
            CallbackQueryHandler(ann_start, pattern=r"^adm:ann$"),
        ],
        states={ANN_TEXT: [MessageHandler(filters.TEXT & ~filters.COMMAND, ann_preview)]},
        fallbacks=fallbacks,
        conversation_timeout=600,
        name="announcement",
    ))

    app.add_handler(CommandHandler("admin", cmd_admin))
    handlers = [
        (cmd_admin, r"^adm:root$"),
        (admin_stats, r"^adm:stats$"),
        (menu_choose_day, r"^adm:menu$"),
        (menu_choose_meal, r"^adm:mday:[1-7]$"),
        (menu_edit_meal, rf"^adm:meal:[1-7]:({meal_re})$"),
        (dish_delete, r"^adm:ddel:\d+$"),
        (meal_clear, rf"^adm:dclr:[1-7]:({meal_re})$"),
        (ann_send, r"^adm:annsend$"),
        (ann_cancel_cb, r"^adm:anncancel$"),
        (inv_groups, r"^adm:inv$"),
        (inv_show, r"^adm:igr?:\d+$"),
        (tch_list, r"^adm:tch:\d+$"),
        (tch_card, r"^adm:t:\d+:\d+$"),
        (tch_link, r"^adm:tl:\d+:\d+$"),
        (tch_unlink, r"^adm:tu:\d+:\d+$"),
    ]
    for callback, pattern in handlers:
        app.add_handler(CallbackQueryHandler(callback, pattern=pattern))
