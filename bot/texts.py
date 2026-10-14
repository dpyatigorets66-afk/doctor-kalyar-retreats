"""Тексты бота и форматирование сводки/статуса для админа."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from aiogram.types import User

from .safety import ACCOUNT_HOLD, BROADCAST_HOLD, OUTREACH_HOLD, Channel

UNSUBSCRIBE_BUTTON = "Не присылать анонсы"

UNSUBSCRIBED = "Готово, анонсы больше не придут. Если передумаете — /start."
ALREADY_UNSUBSCRIBED = "Анонсы уже отключены. Включить снова — /start."
APOLOGY = "Извините за беспокойство. Больше писать не будем. Если захотите вернуться — /start."
ACK = "Спасибо! Сообщение передано команде Real Yoga, с вами свяжутся."
ADMIN_HINT = "Админ-панель: /admin"

HOLD_LABELS = {
    ACCOUNT_HOLD: "ограничение от @SpamBot",
    OUTREACH_HOLD: "пауза касаний",
    BROADCAST_HOLD: "блок рассылок",
}


def plural_days(n: int) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return f"{n} день"
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return f"{n} дня"
    return f"{n} дней"


def welcome(broadcast_min_days: int) -> str:
    every = "раз в день" if broadcast_min_days == 1 else f"раза в {plural_days(broadcast_min_days)}"
    return (
        "Здравствуйте! Это бот Real Yoga — центра йоги и аюрведы доктора Кальяра на Пхукете.\n\n"
        f"Здесь будут анонсы практик, ретритов и онлайн-занятий — не чаще {every}.\n\n"
        "Отписаться можно в любой момент: кнопка «Не присылать анонсы» под анонсом или команда /stop.\n\n"
        "Вопрос можно написать прямо сюда — он попадёт команде."
    )


def who(user: User) -> str:
    name = user.full_name or "без имени"
    handle = f"@{user.username}, " if user.username else ""
    return f"{name} ({handle}id {user.id})"


def forwarded(user: User, text: str) -> str:
    if len(text) > 3500:
        text = text[:3500] + "…"
    return f"💬 Сообщение боту от {who(user)}:\n\n{text}"


def _when(raw: str | None, tz) -> str:
    if not raw:
        return ""
    return datetime.fromisoformat(raw).astimezone(tz).strftime("%d.%m %H:%M")


def _day(d: date | str) -> str:
    d = date.fromisoformat(d) if isinstance(d, str) else d
    return d.strftime("%d.%m.%Y")


def status_text(status: dict[str, Any], tz) -> str:
    lines = ["🛡 Статус защиты", ""]
    stop = status["stop_all"]
    lines.append(f"⛔ Стоп всё: включён ({stop.get('by')}, {_when(stop.get('at'), tz)})" if stop else "Стоп всё: нет")
    pause = status["account_pause_until"]
    if pause:
        lines.append(f"🌊 Аккаунт на паузе после флуда до {pause.astimezone(tz):%d.%m %H:%M}")
    for key, hold in status["holds"].items():
        if hold:
            lines.append(f"⏸ {HOLD_LABELS[key].capitalize()}: {hold.get('reason')}")
    lines.append("")
    if status["account_start"] is None:
        lines.append("Аккаунт для касаний: дата старта не задана — /warmup_start ГГГГ-ММ-ДД")
    else:
        lines.append(f"Аккаунт для касаний: день {status['account_day']} (старт {_day(status['account_start'])})")
    lines.append(f"Касаний сегодня: {status['outreach_today']} из {status['outreach_limit']}")
    lines.append(f"Ответов в группах сегодня: {status['scout_today']}")
    lines.append(f"Вступлений в группы сегодня: {status['joins_today']}")
    lines.append(f"Подписчиков бота: {status['subscribers']}")
    return "\n".join(lines)


def summary_text(stats: dict[str, Any]) -> str:
    sent = stats["sent"]
    return "\n".join([
        f"📊 Сводка за {_day(stats['day'])}",
        "",
        f"Отправлено первыми: {stats['sent_total']}",
        f"  · рассылки: {sent[Channel.BROADCAST]}",
        f"  · касания клиентов: {sent[Channel.OUTREACH]}",
        f"  · ответы в группах: {sent[Channel.GROUP]}",
        f"  · личные авторам из групп: {sent[Channel.GROUP_DM]}",
        f"Ответили: {stats['replied']}",
        f"Написали «стоп»: {stats['stops']}",
        f"Отписались от анонсов: {stats['unsubscribed']}",
        f"Заблокировали бота: {stats['blocked']}",
        "",
        f"Ответы на входящие: {stats['replies_to_inbound']}",
        f"Отклонено защитой: {stats['denied']}",
        f"Подписчиков сейчас: {stats['subscribers']}",
    ])
