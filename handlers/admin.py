"""
Админ-панель: редактирование меню столовой, объявления с рассылкой,
статистика. Доступ — только для user_id из ADMIN_IDS.
"""
from __future__ import annotations

import asyncio
import logging

from telegram import Update
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

# Состояния диалогов
ADD_DISH, ANN_TEXT = range(2)


# --- Корень админ-панели ----------------------------------------------------
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
        await update.message.reply_text(
            text, parse_mode=ParseMode.HTML, reply_markup=kb.admin_root()
        )


@admin_only
@safe_handler
async def admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    s = await db.get_stats()
    text = (
        "📊 <b>Статистика</b>\n\n"
        f"👥 Пользователей: {s.get('users_total', 0)}\n"
        f"🎒 Учеников/родителей: {s.get('students', 0)}\n"
        f"👨‍🏫 Преподавателей: {s.get('teachers_users', 0)}\n"
        f"🔔 Подписаны на объявления: {s.get('subscribed', 0)}\n"
        f"🚫 Заблокировали бота: {s.get('blocked', 0)}\n\n"
        f"📅 Уроков в расписании: {s.get('lessons', 0)}\n"
        f"🍽 Блюд в меню: {s.get('dishes', 0)}\n"
        f"📢 Объявлений отправлено: {s.get('announcements', 0)}"
    )
    await query.edit_message_text(
        text, parse_mode=ParseMode.HTML, reply_markup=kb.admin_root()
    )


# --- Редактирование меню столовой -------------------------------------------
@admin_only
@safe_handler
async def menu_choose_day(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "🍽 Выберите день недели:", reply_markup=kb.admin_menu_days()
    )


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


async def _render_dish_editor(query, weekday: int, meal_type: str) -> None:
    """Перерисовывает редактор блюд для выбранного приёма пищи."""
    dishes = await db.get_dishes(weekday, meal_type)
    listing = (
        "\n".join(f"• {fmt.esc(d['name'])}" for d in dishes)
        if dishes
        else "<i>пусто</i>"
    )
    text = (
        f"🍽 <b>{config.WEEKDAYS[weekday]}</b> — "
        f"{config.MEAL_TYPES.get(meal_type, meal_type)}\n\n"
        f"{listing}\n\nНажмите на блюдо, чтобы удалить его."
    )
    await query.edit_message_text(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=kb.admin_dish_editor(weekday, meal_type, dishes),
    )


@admin_only
@safe_handler
async def menu_edit_meal(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """adm:meal:<weekday>:<meal_type>"""
    query = update.callback_query
    await query.answer()
    _, _, raw_day, meal_type = query.data.split(":")
    await _render_dish_editor(query, int(raw_day), meal_type)


@admin_only
@safe_handler
async def dish_delete(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """adm:ddel:<dish_id>"""
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
    """adm:dclr:<weekday>:<meal_type>"""
    query = update.callback_query
    _, _, raw_day, meal_type = query.data.split(":")
    weekday = int(raw_day)
    await db.clear_meal(weekday, meal_type)
    await query.answer("Приём пищи очищен")
    await _render_dish_editor(query, weekday, meal_type)


# --- Диалог: добавление блюда ----------------------------------------------
@admin_only
async def dish_add_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """adm:dadd:<weekday>:<meal_type> — запрашиваем название(я)."""
    query = update.callback_query
    await query.answer()
    _, _, raw_day, meal_type = query.data.split(":")
    context.user_data["dish_weekday"] = int(raw_day)
    context.user_data["dish_meal"] = meal_type
    await query.edit_message_text(
        f"✍️ Пришлите название блюда для "
        f"<b>{config.WEEKDAYS[int(raw_day)]}</b> — "
        f"{config.MEAL_TYPES.get(meal_type, meal_type)}.\n\n"
        "Можно отправить несколько блюд — каждое с новой строки.\n"
        "Отмена: /cancel",
        parse_mode=ParseMode.HTML,
    )
    return ADD_DISH


@admin_only
async def dish_add_save(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Сохраняет одно или несколько блюд из текста сообщения."""
    weekday = context.user_data.get("dish_weekday")
    meal_type = context.user_data.get("dish_meal")
    if weekday is None or meal_type is None:
        await update.message.reply_text("Сессия истекла, начните заново: /admin")
        return ConversationHandler.END

    names = [line.strip() for line in update.message.text.splitlines() if line.strip()]
    if not names:
        await update.message.reply_text("Пустое название. Попробуйте ещё раз.")
        return ADD_DISH

    for name in names:
        await db.add_dish(weekday, meal_type, name[:200])
    logger.info(
        "Админ %s добавил %d блюд(о) на день %s (%s)",
        update.effective_user.id, len(names), weekday, meal_type,
    )

    dishes = await db.get_dishes(weekday, meal_type)
    await update.message.reply_text(
        f"✅ Добавлено: {len(names)}\n\n"
        + "\n".join(f"• {fmt.esc(d['name'])}" for d in dishes),
        parse_mode=ParseMode.HTML,
        reply_markup=kb.admin_dish_editor(weekday, meal_type, dishes),
    )
    context.user_data.pop("dish_weekday", None)
    context.user_data.pop("dish_meal", None)
    return ConversationHandler.END


# --- Диалог: объявление + рассылка ------------------------------------------
@admin_only
async def ann_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(
            "📢 Пришлите текст объявления.\n\nОтмена: /cancel"
        )
    else:
        await update.message.reply_text(
            "📢 Пришлите текст объявления.\n\nОтмена: /cancel"
        )
    return ANN_TEXT


@admin_only
async def ann_preview(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Показывает предпросмотр и просит подтверждение."""
    msg = update.message
    plain = (msg.text or "").strip()
    if not plain:
        await msg.reply_text("Текст пустой, попробуйте снова.")
        return ANN_TEXT
    if len(plain) > 3500:
        await msg.reply_text(
            f"Слишком длинно: {len(plain)} символов (максимум 3500). "
            "Сократите текст и пришлите снова."
        )
        return ANN_TEXT

    # text_html собирает HTML из entities сообщения и сам экранирует
    # остальной текст — форматирование админа сохраняется безопасно.
    context.user_data["ann_text"] = msg.text_html
    targets = await db.get_broadcast_targets()
    await msg.reply_text(
        "<b>Предпросмотр объявления:</b>\n\n"
        f"📢 {context.user_data['ann_text']}\n\n"
        f"Получателей: <b>{len(targets)}</b>. Отправить?",
        parse_mode=ParseMode.HTML,
        reply_markup=kb.confirm_broadcast(),
    )
    return ConversationHandler.END  # дальше работают callback-кнопки



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
    """Запускает рассылку в фоне, чтобы не блокировать обработчик."""
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

    # create_task гарантирует, что задача корректно завершится при shutdown
    context.application.create_task(
        _broadcast(context, ann_id, text, query.message.chat_id)
    )


async def _broadcast(
    context: ContextTypes.DEFAULT_TYPE, ann_id: int, text: str, report_chat_id: int
) -> None:
    """Последовательная рассылка с учётом лимитов Telegram."""
    targets = await db.get_broadcast_targets()
    payload = f"📢 <b>Объявление</b>\n\n{text}"
    sent = failed = blocked = 0

    for user_id in targets:
        try:
            await context.bot.send_message(
                chat_id=user_id, text=payload, parse_mode=ParseMode.HTML
            )
            sent += 1
        except Forbidden:
            # Пользователь заблокировал бота — исключаем из будущих рассылок
            await db.mark_blocked(user_id)
            blocked += 1
        except RetryAfter as exc:
            logger.warning("Flood control: пауза %s c", exc.retry_after)
            await asyncio.sleep(exc.retry_after + 1)
            try:
                await context.bot.send_message(
                    chat_id=user_id, text=payload, parse_mode=ParseMode.HTML
                )
                sent += 1
            except TelegramError:
                failed += 1
        except TelegramError as exc:
            logger.error("Не удалось отправить %s: %s", user_id, exc)
            failed += 1
        await asyncio.sleep(config.BROADCAST_DELAY)

    await db.set_announcement_sent(ann_id, sent)
    logger.info("Рассылка #%s: доставлено %s, ошибок %s", ann_id, sent, failed + blocked)
    await context.bot.send_message(
        chat_id=report_chat_id,
        text=(
            f"✅ Рассылка завершена\n\n"
            f"Доставлено: {sent}\nЗаблокировали бота: {blocked}\nОшибок: {failed}"
        ),
        reply_markup=kb.admin_root(),
    )


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Универсальный выход из любого диалога."""
    context.user_data.clear()
    if update.message:
        await update.message.reply_text("Отменено.", reply_markup=kb.admin_root())
    return ConversationHandler.END


def register(app: Application) -> None:
    meal_re = "|".join(config.MEAL_TYPES)  # breakfast|lunch|snack

    # Любая команда внутри диалога завершает его, а не оставляет «висеть»
    fallbacks = [
        CommandHandler("cancel", cancel),
        MessageHandler(filters.COMMAND, cancel),
    ]

    # Диалоги: добавление блюда и создание объявления
    app.add_handler(
        ConversationHandler(
            entry_points=[
                CallbackQueryHandler(
                    dish_add_start, pattern=rf"^adm:dadd:[1-7]:({meal_re})$"
                )
            ],
            states={
                ADD_DISH: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, dish_add_save)
                ]
            },
            fallbacks=fallbacks,
            conversation_timeout=300,
            name="add_dish",
        )
    )
    app.add_handler(
        ConversationHandler(
            entry_points=[
                CommandHandler("announce", ann_start),
                CallbackQueryHandler(ann_start, pattern=r"^adm:ann$"),
            ],
            states={
                ANN_TEXT: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, ann_preview)
                ]
            },
            fallbacks=fallbacks,
            conversation_timeout=600,
            name="announcement",
        )
    )

    app.add_handler(CommandHandler("admin", cmd_admin))
    app.add_handler(CallbackQueryHandler(cmd_admin, pattern=r"^adm:root$"))
    app.add_handler(CallbackQueryHandler(admin_stats, pattern=r"^adm:stats$"))
    app.add_handler(CallbackQueryHandler(menu_choose_day, pattern=r"^adm:menu$"))
    app.add_handler(CallbackQueryHandler(menu_choose_meal, pattern=r"^adm:mday:[1-7]$"))
    app.add_handler(
        CallbackQueryHandler(menu_edit_meal, pattern=rf"^adm:meal:[1-7]:({meal_re})$")
    )
    app.add_handler(CallbackQueryHandler(dish_delete, pattern=r"^adm:ddel:\d+$"))
    app.add_handler(
        CallbackQueryHandler(meal_clear, pattern=rf"^adm:dclr:[1-7]:({meal_re})$")
    )
    app.add_handler(CallbackQueryHandler(ann_send, pattern=r"^adm:annsend$"))
    app.add_handler(CallbackQueryHandler(ann_cancel_cb, pattern=r"^adm:anncancel$"))

