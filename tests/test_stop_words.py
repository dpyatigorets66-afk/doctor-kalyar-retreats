import pytest

from bot.safety import OUTREACH_HOLD, Channel, Outgoing, find_stop_word
from tests.helpers import outreach, ready_for_outreach


@pytest.mark.parametrize("text", [
    "стоп", "Стоп!", "СТОП", "стоп, хватит", "stop", "STOP please", "unsubscribe",
    "Не пишите мне больше", "не пиши", "это спам", "Спамеры!", "Кто вы?", "а кто вы такие",
    "Откуда у вас мой номер?", "откуда номер", "Пожалуйста, стоп.",
])
def test_stop_words_detected(text):
    assert find_stop_word(text) is not None


@pytest.mark.parametrize("text", [
    "Делаете массаж стоп?", "крем для стоп", "Здравствуйте, хочу на ретрит", "Сколько стоит?",
    "стопы болят после практики", "Пишите в любое время", "Кто ведёт занятия?", "остановка автобуса",
    "",
])
def test_ordinary_messages_are_not_stop_words(text):
    assert find_stop_word(text) is None


def test_stop_word_puts_person_in_stop_list_forever_and_apologizes_once(safety, clock):
    safety.subscribe(42)
    first = safety.register_stop(42, source="account", text="стоп")
    assert first.new and first.apologize
    assert safety.is_stop_listed(42)
    assert not safety.is_subscribed(42)

    again = safety.register_stop(42, source="account", text="стоп!!!")
    assert not again.new and not again.apologize

    ready_for_outreach(safety, clock)
    assert safety.check(outreach(42)).reason == "в стоп-листе"
    gdm = Outgoing(Channel.GROUP_DM, 42, "ответ", score=9, admin_approved=True)
    assert safety.check(gdm).reason == "в стоп-листе"


def test_apology_still_goes_through_reply_channel(safety):
    safety.touch_inbound(42)
    safety.register_stop(42, source="bot", text="стоп")
    assert safety.check(Outgoing(Channel.REPLY, 42, "Извините")).allowed


def test_more_than_two_stops_a_day_pause_outreach(safety, clock):
    for person in (1, 2):
        safety.register_stop(person, source="account")
    assert safety.hold(OUTREACH_HOLD) is None

    safety.register_stop(3, source="account")
    assert safety.hold(OUTREACH_HOLD) is not None
    assert any("Касания на паузе" in text for _, text in safety.pending_alerts())

    ready_for_outreach(safety, clock)
    assert safety.check(outreach(99)).reason.startswith("касания на паузе")

    # Пауза до ручного включения: следующий день её не снимает
    clock.advance(days=1)
    assert safety.check(outreach(99)).reason.startswith("касания на паузе")
    safety.clear_hold(OUTREACH_HOLD, by="admin")
    assert safety.check(outreach(99)).allowed


def test_stops_on_different_days_do_not_add_up(safety, clock):
    safety.register_stop(1, source="account")
    safety.register_stop(2, source="account")
    clock.advance(days=1)
    safety.register_stop(3, source="account")
    assert safety.hold(OUTREACH_HOLD) is None


def test_stops_to_the_bot_do_not_pause_outreach(safety):
    for person in (1, 2, 3, 4):
        safety.register_stop(person, source="bot")
    assert safety.hold(OUTREACH_HOLD) is None
    assert safety.daily_stats()["stops"] == 4
