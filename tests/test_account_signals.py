"""Флуд, @SpamBot, санкции групп, блокировки бота после рассылки."""

from datetime import timedelta

import pytest

from bot.safety import ACCOUNT_HOLD, BROADCAST_HOLD, Channel, Outgoing, spambot_says_free
from tests.helpers import outreach, ready_for_outreach

GROUP_OUT = dict(score=9, admin_approved=True)


def group_reply(group=-1):
    return Outgoing(Channel.GROUP, group, "Хатха по утрам.\nПодробнее — в личку", group_id=group, **GROUP_OUT)


def test_flood_pauses_account_for_48_hours(safety, clock):
    ready_for_outreach(safety, clock)
    safety.on_flood("PeerFloodError")
    assert "на паузе после флуда" in safety.check(outreach(5)).reason
    assert "на паузе после флуда" in safety.check(group_reply()).reason
    assert "на паузе после флуда" in safety.check(Outgoing(Channel.JOIN, -3)).reason
    assert any("PeerFloodError" in text for _, text in safety.pending_alerts())

    clock.advance(hours=47, minutes=59)
    assert not safety.check(outreach(5)).allowed
    clock.advance(minutes=2)
    assert safety.check(outreach(5)).allowed


def test_long_flood_wait_is_respected(safety, clock):
    until = safety.on_flood("FloodWaitError", seconds=72 * 3600)
    assert until - safety.now() == timedelta(hours=72)


def test_shorter_flood_does_not_shorten_existing_pause(safety):
    first = safety.on_flood("FloodWaitError", seconds=72 * 3600)
    second = safety.on_flood("PeerFloodError")
    assert second == first == safety.account_pause_until()


def test_flood_does_not_stop_bot_broadcasts(safety):
    safety.subscribe(10)
    safety.on_flood("PeerFloodError")
    out = Outgoing(Channel.BROADCAST, 10, "Анонс", campaign="c1", has_unsubscribe_button=True)
    assert safety.check(out).allowed


@pytest.mark.parametrize("reply", [
    "Good news, no limits are currently applied to your account. You’re free as a bird!",
    "Отличные новости: на Ваш аккаунт не наложено никаких ограничений. Вы свободны, как птица в полёте!",
    "Ваш аккаунт свободен от каких-либо ограничений.",
])
def test_spambot_free(safety, reply):
    assert spambot_says_free(reply)
    assert safety.on_spambot_reply(reply)
    assert safety.hold(ACCOUNT_HOLD) is None


@pytest.mark.parametrize("reply", [
    "I'm afraid some Telegram users found your messages annoying and forwarded them to our team "
    "of moderators for inspection. Your account is now limited until 21 Oct 2026.",
    "К сожалению, ваш аккаунт ограничен: вы не можете писать тем, у кого нет вашего номера.",
    "Что-то непонятное",
])
def test_spambot_restriction_stops_all_touches(safety, clock, reply):
    ready_for_outreach(safety, clock)
    assert not safety.on_spambot_reply(reply)
    assert safety.hold(ACCOUNT_HOLD) is not None
    assert safety.check(outreach(5)).reason.startswith("аккаунт остановлен")
    assert safety.check(group_reply()).reason.startswith("аккаунт остановлен")
    assert any("@SpamBot" in text for _, text in safety.pending_alerts())

    clock.advance(days=3)  # до ручного включения
    assert not safety.check(outreach(5)).allowed
    safety.clear_hold(ACCOUNT_HOLD, by="admin")
    assert safety.check(outreach(5)).allowed


def test_group_penalty_blacklists_group(safety):
    safety.on_group_penalty(-1, "админ удалил наше сообщение")
    assert safety.check(group_reply(-1)).reason == "группа в чёрном списке"
    assert safety.check(Outgoing(Channel.JOIN, -1)).reason == "группа в чёрном списке"
    assert safety.check(group_reply(-2)).allowed
    assert any("чёрном списке" in text for _, text in safety.pending_alerts())


def send_broadcast(safety, users, campaign="c1"):
    for user in users:
        safety.subscribe(user)
        safety.record_sent(Outgoing(Channel.BROADCAST, user, "Анонс", campaign=campaign, has_unsubscribe_button=True))


def test_blocks_up_to_3_percent_are_ok(safety):
    send_broadcast(safety, range(100, 140))  # 40 получателей
    safety.record_blocked(100)                # 2.5%
    assert safety.hold(BROADCAST_HOLD) is None


def test_more_than_3_percent_blocks_next_broadcast(safety, clock):
    send_broadcast(safety, range(100, 140))
    safety.record_blocked(100)
    safety.record_blocked(101)                # 5%
    assert safety.hold(BROADCAST_HOLD) is not None
    assert any("рассылка заблокирована" in text for _, text in safety.pending_alerts())

    clock.advance(days=4)
    safety.subscribe(500)
    out = Outgoing(Channel.BROADCAST, 500, "Новый анонс", campaign="c2", has_unsubscribe_button=True)
    assert safety.check(out).reason.startswith("рассылки заблокированы")
    safety.clear_hold(BROADCAST_HOLD, by="admin")
    assert safety.check(out).allowed


def test_blocks_long_after_broadcast_do_not_count(safety, clock):
    send_broadcast(safety, range(100, 110))
    clock.advance(days=10)
    safety.record_blocked(100)
    assert safety.hold(BROADCAST_HOLD) is None


def test_blocked_user_gets_no_broadcasts_until_start(safety, clock):
    send_broadcast(safety, [10])
    safety.record_blocked(10)
    clock.advance(days=5)
    safety.clear_hold(BROADCAST_HOLD, by="admin")
    out = Outgoing(Channel.BROADCAST, 10, "Анонс", campaign="c2", has_unsubscribe_button=True)
    assert safety.check(out).reason == "заблокировал бота"
    safety.subscribe(10)
    assert safety.check(out).allowed
