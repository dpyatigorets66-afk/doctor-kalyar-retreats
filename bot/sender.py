"""Отправка сообщений ботом. Только через этот модуль: он пропускает всё через safety."""

from __future__ import annotations

import logging
from dataclasses import replace
from typing import Any

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramForbiddenError, TelegramRetryAfter
from aiogram.types import Message

from .keyboards import has_unsubscribe_button
from .safety import Channel, Decision, Outgoing, Safety, SendResult

log = logging.getLogger(__name__)


async def send_text(bot: Bot, safety: Safety, out: Outgoing, reply_markup: Any = None) -> SendResult[Message]:
    if out.channel == Channel.BROADCAST:
        out = replace(out, has_unsubscribe_button=has_unsubscribe_button(reply_markup))
    try:
        return await safety.send_guarded(
            out, lambda: bot.send_message(out.recipient, out.text, reply_markup=reply_markup)
        )
    except TelegramForbiddenError:
        safety.record_blocked(out.recipient)
        return SendResult(Decision(False, "получатель заблокировал бота"))
    except TelegramRetryAfter as exc:
        log.warning("Telegram просит подождать %s с (%s → %s)", exc.retry_after, out.channel, out.recipient)
        return SendResult(Decision(False, f"Telegram просит подождать {exc.retry_after} с"))
    except TelegramAPIError as exc:
        log.warning("не отправлено %s → %s: %s", out.channel, out.recipient, exc)
        return SendResult(Decision(False, f"ошибка Telegram: {exc}"))
