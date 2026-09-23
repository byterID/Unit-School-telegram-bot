"""Массовая отправка сообщений с учётом блокировок и лимитов Telegram."""
from __future__ import annotations

import asyncio
import logging
from typing import Iterable

from telegram import Bot
from telegram.constants import ParseMode
from telegram.error import Forbidden, RetryAfter, TelegramError

import config
from database import db

logger = logging.getLogger(__name__)


def _seconds(value) -> float:
    """retry_after может быть int или timedelta — в зависимости от настроек PTB."""
    return value.total_seconds() if hasattr(value, "total_seconds") else float(value)


async def send_many(bot: Bot, user_ids: Iterable[int], text: str) -> tuple[int, int, int]:
    """Возвращает (доставлено, заблокировали бота, ошибок)."""
    sent = blocked = failed = 0
    for user_id in user_ids:
        for attempt in range(2):
            try:
                await bot.send_message(user_id, text, parse_mode=ParseMode.HTML)
                sent += 1
            except Forbidden:
                await db.mark_blocked(user_id)
                blocked += 1
            except RetryAfter as exc:
                if attempt == 0:
                    await asyncio.sleep(_seconds(exc.retry_after) + 1)
                    continue
                failed += 1
            except TelegramError as exc:
                logger.error("Не удалось отправить %s: %s", user_id, exc)
                failed += 1
            break
        await asyncio.sleep(config.BROADCAST_DELAY)
    return sent, blocked, failed
