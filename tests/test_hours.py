from datetime import datetime, timezone

import pytest

from tests.helpers import bkk, outreach, ready_for_outreach


@pytest.mark.parametrize("moment, allowed", [
    (bkk(2026, 10, 14, 9, 59), False),   # среда, до 10:00
    (bkk(2026, 10, 14, 10, 0), True),
    (bkk(2026, 10, 14, 18, 59), True),
    (bkk(2026, 10, 14, 19, 0), False),   # 19:00 уже нельзя
    (bkk(2026, 10, 14, 23, 30), False),
    (bkk(2026, 10, 17, 12, 0), True),    # суббота
    (bkk(2026, 10, 18, 12, 0), False),   # воскресенье
    (bkk(2026, 10, 19, 10, 30), True),   # понедельник
])
def test_outreach_hours_bangkok(safety, clock, moment, allowed):
    ready_for_outreach(safety, clock)
    clock.set(moment)
    assert (safety.outside_outreach_hours() is None) is allowed
    assert safety.check(outreach(5)).allowed is allowed


def test_hours_are_counted_in_bangkok_not_utc(safety, clock):
    ready_for_outreach(safety, clock)
    clock.set(datetime(2026, 10, 14, 2, 59, tzinfo=timezone.utc))  # 09:59 в Бангкоке
    assert "вне рабочих часов" in safety.check(outreach(5)).reason
    clock.set(datetime(2026, 10, 14, 3, 0, tzinfo=timezone.utc))   # 10:00 в Бангкоке
    assert safety.check(outreach(5)).allowed


def test_sunday_in_bangkok_while_still_saturday_in_utc(safety, clock):
    ready_for_outreach(safety, clock)
    clock.set(datetime(2026, 10, 17, 23, 30, tzinfo=timezone.utc))  # вс 06:30 в Бангкоке
    assert safety.check(outreach(5)).reason == "воскресенье — не пишем"


def test_custom_hours_from_env(make_safety, clock):
    safety = make_safety(outreach_hour_start=12, outreach_hour_end=14)
    ready_for_outreach(safety, clock)
    clock.set(bkk(2026, 10, 14, 11, 0))
    assert not safety.check(outreach(5)).allowed
    clock.set(bkk(2026, 10, 14, 13, 0))
    assert safety.check(outreach(5)).allowed
