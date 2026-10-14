"""Общие помощники тестов."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from bot.safety import Channel, Outgoing, Safety

BKK = ZoneInfo("Asia/Bangkok")
ADMIN = 1

# Тексты первого сообщения: проходят все правила и не похожи друг на друга
TEXT_A = (
    "Здравствуйте, Мария! Я ассистент Real Yoga, центра йоги и аюрведы на Пхукете. "
    "Вы были у нас на ретрите весной — в ноябре снова собираем группу на утреннюю практику у моря. "
    'Если не актуально — просто ответьте "стоп", больше не напишу'
)
TEXT_A_TEMPLATE = TEXT_A.replace("Мария", "Ольга").replace("весной", "осенью")
TEXT_B = (
    "Добрый день, Олег. Пишет ассистент Real Yoga. Вы спрашивали про пранаяму для сна — "
    "с 3 ноября доктор ведёт вечерний курс дыхания онлайн, три раза в неделю. "
    "Если не актуально — просто ответьте «стоп», больше не напишу."
)
TEXT_C = (
    "Игорь, привет от ассистента Real Yoga! В заметке у нас записано, что вам понравилась "
    "аюрведическая кухня. 20 ноября будет мастер-класс по кичари в студии на Раваи. "
    'Если не актуально — просто ответьте "стоп", больше не напишу'
)


def bkk(y: int, m: int, d: int, h: int = 12, mi: int = 0) -> datetime:
    return datetime(y, m, d, h, mi, tzinfo=BKK)


class Clock:
    def __init__(self, start: datetime) -> None:
        self.t = start

    def __call__(self) -> datetime:
        return self.t

    def advance(self, **kw: float) -> None:
        self.t += timedelta(**kw)

    def set(self, t: datetime) -> None:
        self.t = t


def ready_for_outreach(safety: Safety, clock: Clock, account_age_days: int = 30) -> None:
    """Аккаунт прогрет, рабочий день, 11:00 по Бангкоку."""
    clock.set(bkk(2026, 10, 14, 11))
    safety.set_account_start(date(2026, 10, 14) - timedelta(days=account_age_days - 1))


def outreach(recipient: int | str, text: str = TEXT_A) -> Outgoing:
    return Outgoing(Channel.OUTREACH, recipient, text)


def fake_outreach_sent(safety: Safety, clock: Clock, n: int, start_id: int = 1000) -> None:
    """Имитирует n уже отправленных касаний с паузами между ними."""
    for i in range(n):
        safety.record_sent(Outgoing(Channel.OUTREACH, start_id + i, f"старое сообщение {i}"))
        clock.advance(minutes=13)
