from __future__ import annotations

import random

import pytest

from bot.config import Settings
from bot.db import Database
from bot.safety import Safety
from tests.helpers import ADMIN, Clock, bkk


@pytest.fixture
def clock() -> Clock:
    return Clock(bkk(2026, 10, 14, 12))  # среда, полдень по Бангкоку


@pytest.fixture
def make_safety(clock):
    dbs = []

    def make(**overrides) -> Safety:
        overrides.setdefault("admin_ids", frozenset({ADMIN}))
        db = Database()
        dbs.append(db)
        return Safety(db, Settings(**overrides), now=clock, rng=random.Random(7))

    yield make
    for db in dbs:
        db.close()


@pytest.fixture
def safety(make_safety) -> Safety:
    return make_safety()

