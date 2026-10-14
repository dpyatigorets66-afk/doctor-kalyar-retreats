"""Подписчики: /start, отписка, стоп-слова, входящие сообщения."""

from __future__ import annotations

from contextlib import suppress

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import KICKED, ChatMemberUpdatedFilter, Command, CommandStart
from aiogram.types import CallbackQuery, ChatMemberUpdated, Message

from .. import texts
from ..config import Settings
from ..keyboards import UNSUBSCRIBE_CB
from ..safety import Channel, Outgoing, Safety, find_stop_word
from ..sender import send_text


def build_router() -> Router:
    router = Router(name="user")
    router.message.filter(F.chat.type == "private")

    async def reply(bot: Bot, safety: Safety, user_id: int, text: str) -> None:
        await send_text(bot, safety, Outgoing(Channel.REPLY, user_id, text))

    @router.message(CommandStart())
    async def start(message: Message, bot: Bot, safety: Safety, settings: Settings) -> None:
        user = message.from_user
        safety.subscribe(user.id, user.username, user.first_name)
        await reply(bot, safety, user.id, texts.welcome(settings.broadcast_min_days))

    @router.message(Command("stop", "unsubscribe"))
    async def stop_command(message: Message, bot: Bot, safety: Safety) -> None:
        changed = safety.unsubscribe(message.from_user.id, via="command")
        await reply(bot, safety, message.from_user.id, texts.UNSUBSCRIBED if changed else texts.ALREADY_UNSUBSCRIBED)

    @router.callback_query(F.data == UNSUBSCRIBE_CB)
    async def unsubscribe_button(callback: CallbackQuery, bot: Bot, safety: Safety) -> None:
        changed = safety.unsubscribe(callback.from_user.id, via="button")
        await callback.answer()
        if callback.message is not None:
            with suppress(TelegramBadRequest):
                await callback.message.edit_reply_markup(reply_markup=None)
        await reply(bot, safety, callback.from_user.id, texts.UNSUBSCRIBED if changed else texts.ALREADY_UNSUBSCRIBED)

    @router.my_chat_member(F.chat.type == "private", ChatMemberUpdatedFilter(member_status_changed=KICKED))
    async def blocked_bot(event: ChatMemberUpdated, safety: Safety) -> None:
        safety.record_blocked(event.from_user.id)

    @router.message()
    async def incoming(message: Message, bot: Bot, safety: Safety, settings: Settings) -> None:
        user = message.from_user
        text = message.text or message.caption or ""
        if find_stop_word(text):
            result = safety.register_stop(user.id, source="bot", text=text)
            if result.apologize:
                await reply(bot, safety, user.id, texts.APOLOGY)
            return
        if user.id in settings.admin_ids:
            await reply(bot, safety, user.id, texts.ADMIN_HINT)
            return
        safety.record_reply(user.id)
        safety.alert(texts.forwarded(user, text or f"[{message.content_type}]"))
        await reply(bot, safety, user.id, texts.ACK)

    return router
