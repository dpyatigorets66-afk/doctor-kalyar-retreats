"""Админ-панель: «⛔ Стоп всё», статус защиты, сводка, дата старта аккаунта."""

from __future__ import annotations

from contextlib import suppress
from datetime import date

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, Message

from .. import texts
from ..config import Settings
from ..keyboards import admin_kb
from ..safety import HOLDS, Channel, Outgoing, Safety
from ..sender import send_text


def build_router(settings: Settings) -> Router:
    router = Router(name="admin")
    is_admin = F.from_user.id.in_(settings.admin_ids)
    router.message.filter(F.chat.type == "private", is_admin)
    router.callback_query.filter(is_admin)

    async def say(bot: Bot, safety: Safety, admin_id: int, text: str, reply_markup=None) -> None:
        await send_text(bot, safety, Outgoing(Channel.ADMIN, admin_id, text), reply_markup=reply_markup)

    def panel(safety: Safety) -> tuple[str, object]:
        status = safety.status()
        return texts.status_text(status, safety.tz), admin_kb(status)

    @router.message(Command("admin"))
    async def show_panel(message: Message, bot: Bot, safety: Safety) -> None:
        text, kb = panel(safety)
        await say(bot, safety, message.from_user.id, text, kb)

    @router.message(Command("summary"))
    async def summary(message: Message, bot: Bot, safety: Safety) -> None:
        await say(bot, safety, message.from_user.id, texts.summary_text(safety.daily_stats()))

    @router.message(Command("warmup_start"))
    async def warmup_start(message: Message, command: CommandObject, bot: Bot, safety: Safety) -> None:
        if command.args:
            try:
                start = date.fromisoformat(command.args.strip())
            except ValueError:
                await say(bot, safety, message.from_user.id, "Формат даты: /warmup_start 2026-10-14")
                return
            if start > safety.today():
                await say(bot, safety, message.from_user.id, "Дата старта не может быть в будущем.")
                return
            safety.set_account_start(start)
        start = safety.account_start()
        if start is None:
            text = "Дата старта аккаунта не задана. Пример: /warmup_start 2026-10-14"
        else:
            s = settings
            text = (
                f"Аккаунт начал работу {start:%d.%m.%Y}, сегодня день {safety.account_day_number()}.\n"
                f"Первые сообщения клиентам — с {s.outreach_warmup_days + 1}-го дня. "
                f"Лимит сегодня: {safety.outreach_daily_limit()}."
            )
        await say(bot, safety, message.from_user.id, text)

    async def refresh(callback: CallbackQuery, safety: Safety) -> None:
        if callback.message is None:
            return
        text, kb = panel(safety)
        with suppress(TelegramBadRequest):  # «message is not modified»
            await callback.message.edit_text(text, reply_markup=kb)

    @router.callback_query(F.data == "adm:stop")
    async def stop_all(callback: CallbackQuery, safety: Safety) -> None:
        safety.stop_all(by=texts.who(callback.from_user))
        safety.alert(
            f"⛔ «Стоп всё» включён: {texts.who(callback.from_user)}. "
            "Рассылки, касания и ответы в группах остановлены."
        )
        await callback.answer("⛔ Всё остановлено")
        await refresh(callback, safety)

    @router.callback_query(F.data == "adm:resume")
    async def resume_all(callback: CallbackQuery, safety: Safety) -> None:
        safety.resume_all(by=texts.who(callback.from_user))
        safety.alert(f"▶️ «Стоп всё» снят: {texts.who(callback.from_user)}.")
        await callback.answer("▶️ Включено")
        await refresh(callback, safety)

    @router.callback_query(F.data.startswith("adm:clear:"))
    async def clear_hold(callback: CallbackQuery, safety: Safety) -> None:
        key = callback.data.removeprefix("adm:clear:")
        if key in HOLDS:
            safety.clear_hold(key, by=texts.who(callback.from_user))
            safety.alert(f"▶️ Снято «{texts.HOLD_LABELS[key]}»: {texts.who(callback.from_user)}.")
        await callback.answer("Готово")
        await refresh(callback, safety)

    @router.callback_query(F.data == "adm:status")
    async def status(callback: CallbackQuery, safety: Safety) -> None:
        await callback.answer()
        await refresh(callback, safety)

    @router.callback_query(F.data == "adm:summary")
    async def summary_button(callback: CallbackQuery, bot: Bot, safety: Safety) -> None:
        await callback.answer()
        await say(bot, safety, callback.from_user.id, texts.summary_text(safety.daily_stats()))

    return router
