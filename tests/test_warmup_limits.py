from datetime import date, timedelta

import pytest

from bot.safety import Channel, Outgoing
from tests.helpers import TEXT_B, bkk, fake_outreach_sent, outreach, ready_for_outreach

TODAY = date(2026, 10, 14)


def start_days_ago(safety, day_number: int) -> None:
    """Сегодня — day_number-й день жизни аккаунта."""
    safety.set_account_start(TODAY - timedelta(days=day_number - 1))


# ---------------------------------------------------------------- прогрев


def test_no_outreach_without_account_start_date(safety, clock):
    clock.set(bkk(2026, 10, 14, 11))
    assert "дата начала аккаунта" in safety.check(outreach(5)).reason
    assert safety.outreach_daily_limit() == 0


@pytest.mark.parametrize("day_number", [1, 2, 7])
def test_first_week_only_reads(safety, clock, day_number):
    clock.set(bkk(2026, 10, 14, 11))
    start_days_ago(safety, day_number)
    assert safety.account_day_number() == day_number
    assert safety.outreach_daily_limit() == 0
    assert safety.check(outreach(5)).reason.startswith("прогрев аккаунта")


def test_first_messages_allowed_from_day_8(safety, clock):
    clock.set(bkk(2026, 10, 14, 11))
    start_days_ago(safety, 8)
    assert safety.check(outreach(5)).allowed


def test_replies_allowed_during_warmup(safety, clock):
    clock.set(bkk(2026, 10, 14, 11))
    start_days_ago(safety, 1)
    safety.touch_inbound(5)
    assert safety.check(Outgoing(Channel.REPLY, 5, "Здравствуйте!")).allowed


@pytest.mark.parametrize("day_number, limit", [
    (8, 5), (14, 5),      # неделя 1 после прогрева
    (15, 10), (21, 10),   # неделя 2
    (22, 20), (200, 20),  # дальше OUTREACH_DAILY_LIMIT
])
def test_daily_limit_grows_smoothly(safety, clock, day_number, limit):
    clock.set(bkk(2026, 10, 14, 11))
    start_days_ago(safety, day_number)
    assert safety.outreach_daily_limit() == limit


def test_daily_limit_never_exceeds_env_maximum(make_safety, clock):
    safety = make_safety(outreach_daily_limit=8)
    clock.set(bkk(2026, 10, 14, 11))
    start_days_ago(safety, 15)
    assert safety.outreach_daily_limit() == 8


def test_daily_limit_is_enforced_and_resets_next_day(safety, clock):
    clock.set(bkk(2026, 10, 14, 10))
    start_days_ago(safety, 8)
    fake_outreach_sent(safety, clock, 5)
    assert safety.check(outreach(5)).reason == "дневной лимит касаний исчерпан (5)"

    clock.set(bkk(2026, 10, 15, 11))
    assert safety.check(outreach(5)).allowed


# ---------------------------------------------------------------- паузы и одно касание


def test_random_pause_between_messages(safety, clock):
    ready_for_outreach(safety, clock)
    safety.record_sent(outreach(5))
    next_at = safety.next_outreach_at()
    gap = next_at - safety.now()
    assert timedelta(minutes=4) <= gap <= timedelta(minutes=12)

    assert safety.check(outreach(6, TEXT_B)).reason.startswith("пауза между сообщениями")
    clock.advance(minutes=3, seconds=59)
    assert not safety.check(outreach(6, TEXT_B)).allowed
    clock.set(next_at.astimezone(clock.t.tzinfo) + timedelta(seconds=1))
    assert safety.check(outreach(6, TEXT_B)).allowed


def test_pauses_are_random(make_safety, clock):
    ready = []
    for seed in range(20):
        safety = make_safety()
        safety._rng.seed(seed)
        ready_for_outreach(safety, clock)
        safety.record_sent(outreach(5))
        ready.append((safety.next_outreach_at() - safety.now()).total_seconds())
    assert min(ready) >= 4 * 60 and max(ready) <= 12 * 60
    assert len(set(ready)) > 10


def test_only_one_touch_per_person(safety, clock):
    ready_for_outreach(safety, clock)
    safety.record_sent(outreach(5))
    clock.advance(days=2)
    assert safety.check(outreach(5, TEXT_B)).reason == "уже было касание — повторно не пишем"


# ---------------------------------------------------------------- рассылки


def subscribed(safety, user_id=10):
    safety.subscribe(user_id, "anna", "Анна")
    return user_id


def broadcast(user_id=10, button=True, campaign="retreat-nov"):
    return Outgoing(Channel.BROADCAST, user_id, "Анонс", campaign=campaign, has_unsubscribe_button=button)


def test_broadcast_only_to_subscribers(safety):
    assert safety.check(broadcast()).reason == "человек не нажимал /start"
    subscribed(safety)
    assert safety.check(broadcast()).allowed
    safety.unsubscribe(10, via="button")
    assert safety.check(broadcast()).reason == "отписался от анонсов"
    safety.subscribe(10)
    assert safety.check(broadcast()).allowed


def test_broadcast_needs_unsubscribe_button_and_campaign(safety):
    subscribed(safety)
    assert safety.check(broadcast(button=False)).reason == "нет кнопки «Не присылать анонсы»"
    assert safety.check(broadcast(campaign=None)).reason == "у рассылки не указан campaign"


def test_one_broadcast_per_three_days(safety, clock):
    subscribed(safety)
    safety.record_sent(broadcast())
    clock.advance(days=2, hours=23)
    assert "меньше 3 дн." in safety.check(broadcast(campaign="next")).reason
    clock.advance(hours=1)
    assert safety.check(broadcast(campaign="next")).allowed


def test_broadcast_min_days_from_env(make_safety, clock):
    safety = make_safety(broadcast_min_days=7)
    subscribed(safety)
    safety.record_sent(broadcast())
    clock.advance(days=5)
    assert not safety.check(broadcast(campaign="next")).allowed


# ---------------------------------------------------------------- группы


GROUP_TEXT = "Утренняя хатха в Раваи по будням в 8:00.\nПодробнее — в личку"


def group_reply(group=-100, score=9.0, approved=True, text=GROUP_TEXT, **kw):
    return Outgoing(Channel.GROUP, group, text, group_id=group, score=score, admin_approved=approved, **kw)


def test_group_reply_rules(safety):
    assert safety.check(group_reply()).allowed
    assert "ниже 8" in safety.check(group_reply(score=7.5)).reason
    assert "ниже 8" in safety.check(group_reply(score=None)).reason
    assert safety.check(group_reply(approved=False)).reason == "нужно подтверждение админа"
    assert "ссылок" in safety.check(group_reply(text="Пишите: t.me/realyoga\nПодробнее — в личку")).reason
    assert "последняя строка" in safety.check(group_reply(text="Подробнее — в личку\nУтренняя хатха")).reason
    assert "только в личку" in safety.check(group_reply(group_no_ads=True)).reason


def test_autopilot_off_by_default_and_from_env(make_safety):
    assert make_safety().settings.scout_autopilot is False
    assert make_safety(scout_autopilot=True).check(group_reply(approved=False)).allowed


def test_one_reply_per_group_per_day(safety, clock):
    safety.record_sent(group_reply(-100))
    assert safety.check(group_reply(-100)).reason == "в этой группе сегодня уже был ответ"
    assert safety.check(group_reply(-200)).allowed
    clock.advance(days=1)
    assert safety.check(group_reply(-100)).allowed


def test_scout_daily_limit_across_groups(make_safety):
    safety = make_safety(scout_daily_limit=3)
    for group in (-1, -2):
        safety.record_sent(group_reply(group))
    safety.record_sent(Outgoing(Channel.GROUP_DM, 77, "ответ", group_id=-9, score=9, admin_approved=True))
    assert safety.check(group_reply(-4)).reason == "дневной лимит ответов в группах исчерпан (3)"


def test_group_dm_needs_admin_button_even_with_autopilot(make_safety):
    safety = make_safety(scout_autopilot=True)
    dm = Outgoing(Channel.GROUP_DM, 77, "Здравствуйте! Увидели ваш вопрос про йогу.", group_id=-1, score=9)
    assert safety.check(dm).reason == "личное сообщение автору — только после кнопки админа"


def test_join_at_most_two_groups_a_day(safety, clock):
    for group in (-1, -2):
        assert safety.check(Outgoing(Channel.JOIN, group)).allowed
        safety.record_sent(Outgoing(Channel.JOIN, group))
    assert safety.check(Outgoing(Channel.JOIN, -3)).reason == "лимит вступлений в группы на сегодня исчерпан (2)"
    clock.advance(days=1)
    assert safety.check(Outgoing(Channel.JOIN, -3)).allowed


def test_adding_members_is_never_allowed(safety):
    assert not safety.check(Outgoing(Channel.ADD_MEMBER, -1)).allowed


def test_admin_channel_only_to_admins(safety):
    assert safety.check(Outgoing(Channel.ADMIN, 1, "сводка")).allowed
    assert safety.check(Outgoing(Channel.ADMIN, 2, "сводка")).reason == "получатель не админ"


def test_reply_only_after_person_wrote(safety, clock):
    assert not safety.check(Outgoing(Channel.REPLY, 5, "Да")).allowed
    safety.touch_inbound(5)
    assert safety.check(Outgoing(Channel.REPLY, 5, "Да")).allowed
    clock.advance(hours=25)
    assert not safety.check(Outgoing(Channel.REPLY, 5, "Да")).allowed


def test_every_decision_is_logged(safety):
    safety.check(Outgoing(Channel.ADMIN, 1, "ok"))
    safety.check(Outgoing(Channel.ADMIN, 2, "no"))
    rows = safety.db.all("SELECT channel, recipient, allowed, reason FROM safety_log ORDER BY id")
    assert [tuple(r) for r in rows] == [("admin", "1", 1, "ok"), ("admin", "2", 0, "получатель не админ")]
