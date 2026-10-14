"""Кнопка «⛔ Стоп всё» на уровне модуля safety."""

import asyncio

import pytest

from bot.keyboards import admin_kb
from bot.safety import Channel, Outgoing, Safety
from tests.helpers import outreach, ready_for_outreach

STOPPED = "включён «⛔ Стоп всё»"


@pytest.fixture
def ready(safety, clock):
    """Всё, что обычно разрешено: подписчик, прогретый аккаунт, одобренный ответ в группе."""
    safety.subscribe(10)
    ready_for_outreach(safety, clock)
    return {
        "broadcast": Outgoing(Channel.BROADCAST, 10, "Анонс", campaign="c1", has_unsubscribe_button=True),
        "outreach": outreach(20),
        "group": Outgoing(
            Channel.GROUP, -1, "Хатха по утрам.\nПодробнее — в личку", group_id=-1, score=9, admin_approved=True,
        ),
        "group_dm": Outgoing(Channel.GROUP_DM, 30, "Здравствуйте!", group_id=-1, score=9, admin_approved=True),
        "join": Outgoing(Channel.JOIN, -5),
    }


def test_everything_allowed_before_stop(safety, ready):
    for name, out in ready.items():
        assert safety.check(out).allowed, name


def test_stop_all_blocks_broadcasts_outreach_and_groups(safety, ready):
    safety.stop_all(by="admin")
    for name, out in ready.items():
        assert safety.check(out).reason == STOPPED, name


def test_stop_all_keeps_replies_and_admin_messages(safety, ready):
    safety.stop_all(by="admin")
    safety.touch_inbound(10)
    assert safety.check(Outgoing(Channel.REPLY, 10, "Здравствуйте")).allowed
    assert safety.check(Outgoing(Channel.ADMIN, 1, "Остановлено")).allowed


def test_stop_all_survives_restart(safety, ready, clock):
    safety.stop_all(by="admin")
    restarted = Safety(safety.db, safety.settings, now=clock)
    assert restarted.is_stopped()
    assert restarted.check(ready["broadcast"]).reason == STOPPED


def test_resume_only_by_hand(safety, ready, clock):
    safety.stop_all(by="admin")
    clock.advance(days=30)
    assert safety.is_stopped()
    safety.resume_all(by="admin")
    assert safety.check(ready["broadcast"]).allowed


def test_stop_all_stops_a_running_send_loop(safety, ready):
    sent = []

    async def loop():
        for user in range(100, 110):
            safety.subscribe(user)
            if user == 103:
                safety.stop_all(by="admin")  # нажали посреди рассылки
            out = Outgoing(Channel.BROADCAST, user, "Анонс", campaign="c1", has_unsubscribe_button=True)

            async def do_send(user=user):
                sent.append(user)

            await safety.send_guarded(out, do_send)

    asyncio.run(loop())
    assert sent == [100, 101, 102]


def test_admin_panel_button_switches(safety):
    def labels():
        kb = admin_kb(safety.status())
        return [b.text for row in kb.inline_keyboard for b in row]

    assert "⛔ Стоп всё" in labels()
    safety.stop_all(by="admin")
    assert "▶️ Включить всё" in labels() and "⛔ Стоп всё" not in labels()
