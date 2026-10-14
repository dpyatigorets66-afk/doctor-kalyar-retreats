"""Сборка бота: диспетчер, middleware, фоновые задачи (уведомления админу, ежедневная сводка)."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware, Bot, Dispatcher

from . import texts
from .config import Settings
from .db import Database
from .handlers import admin, user
from .safety import Channel, Outgoing, Safety
from .sender import send_text

log = logging.getLogger(__name__)

SUMMARY_SENT_DAY = "summary_sent_day"
ALERTS_EVERY = 5


class InboundMiddleware(BaseMiddleware):
    """Запоминает, что человек написал сам: только после этого ему можно отвечать."""

    def __init__(self, safety: Safety) -> None:
        self.safety = safety

    async def __call__(
        self,
        handler: Callable[[Any, dict[str, Any]], Awaitable[Any]],
        event: Any,
        data: dict[str, Any],
    ) -> Any:
        from_user = data.get("event_from_user")
        chat = data.get("event_chat")
        if from_user is not None and not from_user.is_bot and (chat is None or chat.type == "private"):
            self.safety.touch_inbound(from_user.id)
        return await handler(event, data)


def build_dispatcher(settings: Settings, safety: Safety) -> Dispatcher:
    dp = Dispatcher(settings=settings, safety=safety)
    inbound = InboundMiddleware(safety)
    dp.message.outer_middleware(inbound)
    dp.callback_query.outer_middleware(inbound)
    dp.include_router(admin.build_router(settings))
    dp.include_router(user.build_router())
    return dp


async def deliver_alerts(bot: Bot, safety: Safety, settings: Settings) -> int:
    """Отправляет накопившиеся уведомления всем админам. Возвращает число доставленных."""
    if not settings.admin_ids:
        return 0
    delivered = 0
    for alert_id, text in safety.pending_alerts():
        ok = False
        for admin_id in sorted(settings.admin_ids):
            result = await send_text(bot, safety, Outgoing(Channel.ADMIN, admin_id, text))
            ok = ok or result.sent
        if not ok:
            break  # попробуем позже, порядок уведомлений сохраняется
        safety.mark_alert_sent(alert_id)
        delivered += 1
    return delivered


def queue_summary_if_due(safety: Safety, settings: Settings) -> bool:
    """Раз в день после SUMMARY_HOUR по Бангкоку ставит сводку в очередь уведомлений."""
    today = safety.today().isoformat()
    if safety.local_now().hour < settings.summary_hour or safety.db.get_state(SUMMARY_SENT_DAY) == today:
        return False
    safety.alert(texts.summary_text(safety.daily_stats()))
    safety.db.set_state(SUMMARY_SENT_DAY, today, safety.now().isoformat(timespec="seconds"))
    return True


async def _every(seconds: float, job: Callable[[], Awaitable[Any]]) -> None:
    while True:
        try:
            await job()
        except Exception:
            log.exception("фоновая задача упала, повтор через %s с", seconds)
        await asyncio.sleep(seconds)


async def _alerts_loop(bot: Bot, safety: Safety, settings: Settings) -> None:
    """Каждые 5 с; если админам не доставить (не нажали /start у бота) — реже, до 10 минут."""
    delay = ALERTS_EVERY
    while True:
        try:
            delivered = await deliver_alerts(bot, safety, settings)
            stuck = not delivered and bool(safety.pending_alerts())
            delay = min(delay * 2, 600) if stuck else ALERTS_EVERY
        except Exception:
            log.exception("доставка уведомлений упала")
        await asyncio.sleep(delay)


async def run(settings: Settings) -> None:
    db = Database(settings.db_path)
    safety = Safety(db, settings)
    bot = Bot(settings.bot_token)
    dp = build_dispatcher(settings, safety)

    async def summary_job() -> None:
        queue_summary_if_due(safety, settings)

    tasks = [
        asyncio.create_task(_alerts_loop(bot, safety, settings)),
        asyncio.create_task(_every(30, summary_job)),
    ]
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        db.close()
