"""Бот целиком: настройки, отправка через safety, обработчики на настоящем диспетчере aiogram."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest
from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError
from aiogram.methods import EditMessageText, SendMessage
from aiogram.types import Chat, Message, Update

from bot import texts
from bot.app import build_dispatcher, deliver_alerts, queue_summary_if_due
from bot.config import ConfigError, load_settings
from bot.keyboards import unsubscribe_kb
from bot.safety import Channel, Outgoing
from bot.sender import send_text
from tests.helpers import ADMIN, bkk

USER = 500


class FakeBot(Bot):
    """Bot, который ничего не шлёт в Telegram, а запоминает вызовы."""

    def __init__(self, fail_with: Exception | None = None) -> None:
        super().__init__("42:TEST")
        self.calls: list = []
        self.fail_with = fail_with

    async def __call__(self, method, request_timeout=None):
        if self.fail_with is not None:
            raise self.fail_with
        self.calls.append(method)
        if isinstance(method, SendMessage):
            return Message(
                message_id=len(self.calls), date=datetime.now(timezone.utc),
                chat=Chat(id=method.chat_id, type="private"), text=method.text,
            )
        return True

    def messages(self, chat_id: int | None = None) -> list[str]:
        return [
            m.text for m in self.calls
            if isinstance(m, SendMessage) and (chat_id is None or m.chat_id == chat_id)
        ]


def run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------- настройки


def test_safe_defaults():
    s = load_settings(env={})
    assert (s.broadcast_min_days, s.outreach_daily_limit, s.scout_daily_limit) == (3, 20, 10)
    assert (s.outreach_hour_start, s.outreach_hour_end, s.timezone) == (10, 19, "Asia/Bangkok")
    assert s.scout_autopilot is False and s.flood_pause_hours == 48 and s.group_join_daily_limit == 2


def test_env_example_matches_defaults():
    from pathlib import Path

    from dotenv import dotenv_values

    example = Path(__file__).resolve().parent.parent / ".env.example"
    env = {k: v for k, v in dotenv_values(example).items() if v is not None}
    assert load_settings(env=env) == load_settings(env={})


def test_settings_from_env():
    s = load_settings(env={
        "BOT_TOKEN": "1:abc", "ADMIN_IDS": "1, 2;3", "BROADCAST_MIN_DAYS": "5",
        "OUTREACH_SIMILARITY_MAX": "0.6", "SCOUT_AUTOPILOT": "false",
    })
    assert s.admin_ids == {1, 2, 3}
    assert s.broadcast_min_days == 5 and s.outreach_similarity_max == 0.6


@pytest.mark.parametrize("env", [
    {"ADMIN_IDS": "@me"},
    {"OUTREACH_DAILY_LIMIT": "много"},
    {"OUTREACH_DAILY_LIMIT": "0"},
    {"OUTREACH_MIN_GAP_MIN": "15"},
    {"OUTREACH_HOUR_START": "20"},
    {"SCOUT_AUTOPILOT": "может быть"},
])
def test_bad_settings_are_rejected(env):
    with pytest.raises(ConfigError):
        load_settings(env=env)


# ---------------------------------------------------------------- отправка


def test_denied_message_is_not_sent(safety):
    bot = FakeBot()
    result = run(send_text(bot, safety, Outgoing(Channel.REPLY, USER, "Привет")))
    assert not result.sent and bot.calls == []


def test_broadcast_button_is_detected_from_keyboard(safety):
    bot = FakeBot()
    safety.subscribe(USER)
    out = Outgoing(Channel.BROADCAST, USER, "Анонс", campaign="c1")
    assert not run(send_text(bot, safety, out)).sent
    assert run(send_text(bot, safety, out, reply_markup=unsubscribe_kb())).sent
    assert bot.messages(USER) == ["Анонс"]
    assert safety.daily_stats()["sent"][Channel.BROADCAST] == 1


def test_blocked_bot_is_recorded(safety):
    safety.subscribe(USER)
    error = TelegramForbiddenError(method=SendMessage(chat_id=USER, text="x"), message="bot was blocked by the user")
    bot = FakeBot(fail_with=error)
    out = Outgoing(Channel.BROADCAST, USER, "Анонс", campaign="c1")
    result = run(send_text(bot, safety, out, reply_markup=unsubscribe_kb()))
    assert not result.sent
    assert not safety.is_subscribed(USER)
    assert safety.daily_stats()["blocked"] == 1


def test_alerts_are_delivered_to_admins(safety):
    bot = FakeBot()
    safety.alert("первое")
    safety.alert("второе")
    assert run(deliver_alerts(bot, safety, safety.settings)) == 2
    assert bot.messages(ADMIN) == ["первое", "второе"]
    assert safety.pending_alerts() == []


# ---------------------------------------------------------------- сводка


def test_daily_summary_counts(safety, clock):
    for user in (10, 11, 12):
        safety.subscribe(user)
        safety.record_sent(Outgoing(Channel.BROADCAST, user, "Анонс", campaign="c1", has_unsubscribe_button=True))
    safety.record_reply(10)
    safety.record_reply(10)                 # повторный ответ на то же сообщение не считается
    assert not safety.record_reply(999)     # ему мы не писали
    safety.register_stop(11, source="bot")
    safety.unsubscribe(12, via="button")
    safety.check(Outgoing(Channel.ADMIN, 2, "не админ"))

    stats = safety.daily_stats()
    assert stats["sent_total"] == 3 and stats["sent"][Channel.BROADCAST] == 3
    assert (stats["replied"], stats["stops"], stats["unsubscribed"], stats["denied"]) == (1, 1, 1, 1)

    text = texts.summary_text(stats)
    assert "Отправлено первыми: 3" in text and "Ответили: 1" in text
    assert "Написали «стоп»: 1" in text and "Заблокировали бота: 0" in text


def test_summary_is_queued_once_a_day_after_summary_hour(safety, clock):
    clock.set(bkk(2026, 10, 14, 20, 59))
    assert not queue_summary_if_due(safety, safety.settings)
    clock.set(bkk(2026, 10, 14, 21, 0))
    assert queue_summary_if_due(safety, safety.settings)
    assert not queue_summary_if_due(safety, safety.settings)
    assert len(safety.pending_alerts()) == 1
    clock.set(bkk(2026, 10, 15, 21, 5))
    assert queue_summary_if_due(safety, safety.settings)


# ---------------------------------------------------------------- обработчики


def user_dict(user_id: int) -> dict:
    return {"id": user_id, "is_bot": False, "first_name": "Анна", "username": f"u{user_id}"}


def message_update(bot: Bot, user_id: int, text: str) -> Update:
    return Update.model_validate({
        "update_id": 1,
        "message": {
            "message_id": 1, "date": 0, "text": text,
            "chat": {"id": user_id, "type": "private"}, "from": user_dict(user_id),
        },
    }, context={"bot": bot})


def callback_update(bot: Bot, user_id: int, data: str) -> Update:
    return Update.model_validate({
        "update_id": 2,
        "callback_query": {
            "id": "cb", "chat_instance": "ci", "data": data, "from": user_dict(user_id),
            "message": {
                "message_id": 7, "date": 0, "text": "панель",
                "chat": {"id": user_id, "type": "private"}, "from": {"id": 42, "is_bot": True, "first_name": "bot"},
            },
        },
    }, context={"bot": bot})


@pytest.fixture
def harness(safety):
    bot = FakeBot()
    dp = build_dispatcher(safety.settings, safety)

    def feed(update_factory, *args):
        run(dp.feed_update(bot, update_factory(bot, *args)))

    return bot, feed


def test_start_subscribes_and_welcomes(safety, harness):
    bot, feed = harness
    feed(message_update, USER, "/start")
    assert safety.is_subscribed(USER)
    assert "Real Yoga" in bot.messages(USER)[0]
    assert "не чаще раза в 3 дня" in bot.messages(USER)[0]


def test_unsubscribe_button(safety, harness):
    bot, feed = harness
    feed(message_update, USER, "/start")
    feed(callback_update, USER, "unsub")
    assert not safety.is_subscribed(USER)
    assert bot.messages(USER)[-1] == texts.UNSUBSCRIBED


def test_stop_word_in_bot_apologizes_once(safety, harness):
    bot, feed = harness
    feed(message_update, USER, "/start")
    feed(message_update, USER, "Стоп")
    feed(message_update, USER, "стоп!!!")
    assert safety.is_stop_listed(USER) and not safety.is_subscribed(USER)
    assert bot.messages(USER).count(texts.APOLOGY) == 1


def test_question_is_forwarded_to_admin(safety, harness):
    bot, feed = harness
    feed(message_update, USER, "Сколько стоит ретрит в декабре?")
    assert bot.messages(USER) == [texts.ACK]
    assert "Сколько стоит ретрит" in safety.pending_alerts()[0][1]


def test_admin_stop_all_button(safety, harness):
    bot, feed = harness
    safety.subscribe(USER)
    broadcast = Outgoing(Channel.BROADCAST, USER, "Анонс", campaign="c1", has_unsubscribe_button=True)

    feed(message_update, ADMIN, "/admin")
    assert bot.messages(ADMIN)[0].startswith("🛡 Статус защиты")

    feed(callback_update, ADMIN, "adm:stop")
    assert safety.is_stopped()
    assert safety.check(broadcast).reason == "включён «⛔ Стоп всё»"
    panel = [m for m in bot.calls if isinstance(m, EditMessageText)][-1]
    assert "⛔ Стоп всё: включён (Анна (@u1, id 1), 14.10 12:00)" in panel.text
    assert any("«Стоп всё» включён" in text for _, text in safety.pending_alerts())

    feed(callback_update, ADMIN, "adm:resume")
    assert not safety.is_stopped()
    assert safety.check(broadcast).allowed


def test_non_admin_cannot_press_admin_buttons(safety, harness):
    bot, feed = harness
    feed(callback_update, USER, "adm:stop")
    assert not safety.is_stopped()


def test_warmup_start_command(safety, harness, clock):
    bot, feed = harness
    feed(message_update, ADMIN, "/warmup_start 2026-10-01")
    assert safety.account_day_number() == 14
    assert "день 14" in bot.messages(ADMIN)[-1]
    feed(message_update, ADMIN, "/warmup_start 2030-01-01")
    assert "в будущем" in bot.messages(ADMIN)[-1]
