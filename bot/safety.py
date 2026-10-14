"""Единый шлюз для всего исходящего: рассылки, касания, ответы в группах, ответы людям.

Каждая отправка сначала проходит Safety.check() (или Safety.send_guarded(), который
делает check → отправку → запись). Решение пишется в таблицу safety_log и в лог.
В обход этого модуля ничего не отправляется.
"""

from __future__ import annotations

import asyncio
import difflib
import json
import logging
import random
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from enum import StrEnum
from typing import Any, Awaitable, Callable, Generic, TypeVar
from zoneinfo import ZoneInfo

from .config import Settings
from .db import Database

log = logging.getLogger("safety")

T = TypeVar("T")


class Channel(StrEnum):
    BROADCAST = "broadcast"    # C: рассылка подписчикам бота
    OUTREACH = "outreach"      # D: первое сообщение клиенту с Telethon-аккаунта
    GROUP = "group"            # E: ответ в группе
    GROUP_DM = "group_dm"      # E: личное сообщение автору вопроса из группы
    JOIN = "join"              # E: вступление аккаунта в группу
    ADD_MEMBER = "add_member"  # добавление участников — запрещено всегда
    REPLY = "reply"            # ответ человеку, который сам написал
    ADMIN = "admin"            # служебное сообщение админу


# Что останавливает «⛔ Стоп всё»
STOPPABLE = frozenset({Channel.BROADCAST, Channel.OUTREACH, Channel.GROUP, Channel.GROUP_DM, Channel.JOIN})
# Действия Telethon-аккаунта: их останавливают флуд-пауза и ограничение от @SpamBot
ACCOUNT_CHANNELS = frozenset({Channel.OUTREACH, Channel.GROUP, Channel.GROUP_DM, Channel.JOIN})
# Личные сообщения, которые мы пишем первыми: на них считаем «ответили»
PROACTIVE = frozenset({Channel.BROADCAST, Channel.OUTREACH, Channel.GROUP_DM})
SCOUT = frozenset({Channel.GROUP, Channel.GROUP_DM})
# Одно касание на человека: если уже писали первыми, больше не пишем
FIRST_TOUCH = frozenset({Channel.OUTREACH, Channel.GROUP_DM})

OUTREACH_FOOTER = 'Если не актуально — просто ответьте "стоп", больше не напишу'
GROUP_FOOTER = "Подробнее — в личку"

# Ключи в таблице state
STOP_ALL = "stop_all"
ACCOUNT_PAUSE_UNTIL = "account_pause_until"  # флуд-пауза Telethon-аккаунта
ACCOUNT_HOLD = "account_hold"                # ограничение от @SpamBot, до ручного включения
OUTREACH_HOLD = "outreach_hold"              # много «стоп» за день, до ручного включения
BROADCAST_HOLD = "broadcast_hold"            # много блокировок после рассылки
ACCOUNT_STARTED_ON = "account_started_on"
OUTREACH_NEXT_AT = "outreach_next_at"
HOLDS = (ACCOUNT_HOLD, OUTREACH_HOLD, BROADCAST_HOLD)

REPLY_TRACK_DAYS = 7


@dataclass(frozen=True)
class Outgoing:
    channel: Channel
    recipient: int | str | None = None
    text: str = ""
    group_id: int | str | None = None
    campaign: str | None = None
    has_unsubscribe_button: bool = False
    score: float | None = None
    admin_approved: bool = False
    group_no_ads: bool = False


@dataclass(frozen=True)
class Decision:
    allowed: bool
    reason: str

    def __bool__(self) -> bool:
        return self.allowed


@dataclass(frozen=True)
class SendResult(Generic[T]):
    decision: Decision
    value: T | None = None

    @property
    def sent(self) -> bool:
        return self.decision.allowed


@dataclass(frozen=True)
class StopResult:
    new: bool        # человек только что попал в стоп-лист
    apologize: bool  # отправить одно извинение (только один раз за всё время)


# ---------------------------------------------------------------- тексты

_WORD_RE = re.compile(r"\w+")
_LINK_RE = re.compile(
    r"https?://|www\.|\b(?:t|telegram)\.me/|\btg://|@[a-z0-9_]{4,}"
    r"|\b[a-z0-9][a-z0-9-]*\.[a-z]{2,24}\b|\b[\w-]+\.(?:рф|рус)\b",
    re.IGNORECASE,
)
_EMOJI_RE = re.compile("[\U0001F000-\U0001FAFF☀-➿⬀-⯿⌀-⏿]")
_LETTERS_WORD_RE = re.compile(r"[^\W\d_]{4,}")
_DASHES = str.maketrans({"—": "-", "–": "-", "‑": "-", "−": "-"})
_QUOTES_RE = re.compile("[\"'«»“”„‘’]")

# «стоп» после этих слов — это стопы (ступни), а не просьба не писать: «массаж стоп»
_FOOT_CONTEXT = frozenset({
    "массаж", "массажа", "массажем", "для", "кожи", "кожа", "кожей", "мазь", "крем",
    "уход", "отек", "отеки", "отеков", "свод", "свода", "сводов", "подошвы",
})


def _tokens(text: str) -> list[str]:
    return _WORD_RE.findall(text.lower().replace("ё", "е"))


def find_stop_word(text: str) -> str | None:
    """Стоп-слово в сообщении человека или None.

    «стоп», «не пишите», «спам», «stop», «unsubscribe», «кто вы», «откуда (у вас мой) номер».
    """
    tokens = _tokens(text)
    for i, tok in enumerate(tokens):
        nxt = tokens[i + 1] if i + 1 < len(tokens) else ""
        if tok in ("stop", "unsubscribe"):
            return tok
        if tok == "стоп" and (i == 0 or tokens[i - 1] not in _FOOT_CONTEXT):
            return "стоп"
        if tok.startswith("спам") or tok.startswith("spam"):
            return "спам"
        if tok == "не" and nxt in ("пишите", "пиши"):
            return "не пишите"
        if tok == "кто" and nxt == "вы":
            return "кто вы"
        if tok == "откуда" and any(t.startswith("номер") for t in tokens[i + 1:]):
            return "откуда номер"
    return None


def has_link(text: str) -> bool:
    return bool(_LINK_RE.search(text))


def has_caps(text: str) -> bool:
    if any(w.isupper() for w in _LETTERS_WORD_RE.findall(text)):
        return True
    letters = [c for c in text if c.isalpha()]
    return len(letters) >= 20 and sum(c.isupper() for c in letters) / len(letters) > 0.3


def count_emoji(text: str) -> int:
    return len(_EMOJI_RE.findall(text))


def _loose(text: str) -> str:
    """Для сравнения обязательных фраз: без кавычек, с одним видом тире, без регистра."""
    text = _QUOTES_RE.sub("", text.translate(_DASHES)).lower().replace("ё", "е")
    return " ".join(text.split()).rstrip(" .!")


def ends_with_footer(text: str, footer: str) -> bool:
    return _loose(text).endswith(_loose(footer))


def last_line_is(text: str, line: str) -> bool:
    lines = [ln for ln in text.strip().splitlines() if ln.strip()]
    return bool(lines) and _loose(lines[-1]) == _loose(line)


_FOOTER_TOKENS = " ".join(_tokens(OUTREACH_FOOTER))


def similarity_key(text: str) -> str:
    """Текст для сравнения похожести: слова без регистра, без обязательной фразы в конце."""
    key = " ".join(_tokens(text))
    if key.endswith(_FOOTER_TOKENS):
        key = key[: -len(_FOOTER_TOKENS)].strip()
    return key


def similarity(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, similarity_key(a), similarity_key(b), autojunk=False).ratio()


def outreach_text_problems(text: str, settings: Settings) -> list[str]:
    """Что не так с текстом первого сообщения клиенту. Пустой список — всё в порядке."""
    problems = []
    tokens = _tokens(text)
    lowered = " ".join(tokens)
    if not text.strip():
        return ["пустой текст"]
    if len(text) > settings.outreach_max_len:
        problems.append(f"длиннее {settings.outreach_max_len} символов ({len(text)})")
    if has_link(text):
        problems.append("в первом сообщении не должно быть ссылок и @упоминаний")
    if has_caps(text):
        problems.append("капс")
    if count_emoji(text) > settings.outreach_max_emoji:
        problems.append(f"больше {settings.outreach_max_emoji} эмодзи")
    if not any(t.startswith("ассистент") for t in tokens) or "real yoga" not in lowered:
        problems.append("нужно представиться ассистентом Real Yoga")
    if re.search(r"\bя (?:доктор|врач|кальяр)\b|\bменя зовут\b", lowered):
        problems.append("нельзя представляться доктором или живым человеком")
    if not ends_with_footer(text, OUTREACH_FOOTER):
        problems.append(f"в конце должна быть фраза «{OUTREACH_FOOTER}»")
    return problems


# ---------------------------------------------------------------- @SpamBot

_SPAMBOT_FREE = (
    "no limits are currently applied", "free as a bird", "good news",
    "никаких ограничений", "не наложено", "свободен от", "свободны как птица",
)


def spambot_says_free(text: str) -> bool:
    """True, только если @SpamBot явно пишет, что ограничений нет. Непонятный ответ = ограничение."""
    t = _loose(text).replace(",", "")
    return any(marker in t for marker in _SPAMBOT_FREE)


# ---------------------------------------------------------------- Safety


def _key(value: int | str | None) -> str | None:
    return None if value is None else str(value)


class Safety:
    def __init__(
        self,
        db: Database,
        settings: Settings,
        now: Callable[[], datetime] | None = None,
        rng: random.Random | None = None,
    ) -> None:
        self.db = db
        self.settings = settings
        self.tz = ZoneInfo(settings.timezone)
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._rng = rng or random.Random()
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------ время

    def now(self) -> datetime:
        return self._now().astimezone(timezone.utc)

    def local_now(self) -> datetime:
        return self.now().astimezone(self.tz)

    def today(self) -> date:
        return self.local_now().date()

    def _ts(self) -> str:
        return self.now().isoformat(timespec="seconds")

    def _day(self) -> str:
        return self.today().isoformat()

    # ------------------------------------------------------------ флаги

    def _flag(self, key: str) -> dict[str, Any] | None:
        raw = self.db.get_state(key)
        return None if raw is None else json.loads(raw)

    def _set_flag(self, key: str, **data: Any) -> None:
        data.setdefault("at", self._ts())
        self.db.set_state(key, json.dumps(data, ensure_ascii=False), self._ts())

    def is_stopped(self) -> bool:
        return self.db.get_state(STOP_ALL) is not None

    def stop_all(self, by: str) -> None:
        """«⛔ Стоп всё»: рассылки, касания, ответы в группах — до ручного включения."""
        if not self.is_stopped():
            self._set_flag(STOP_ALL, by=by)
            log.warning("STOP ALL by %s", by)

    def resume_all(self, by: str) -> None:
        if self.is_stopped():
            self.db.delete_state(STOP_ALL)
            log.warning("RESUME ALL by %s", by)

    def hold(self, key: str) -> dict[str, Any] | None:
        return self._flag(key)

    def clear_hold(self, key: str, by: str) -> None:
        if key not in HOLDS:
            raise ValueError(key)
        if self.db.get_state(key) is not None:
            self.db.delete_state(key)
            log.warning("hold %s cleared by %s", key, by)

    def account_pause_until(self) -> datetime | None:
        raw = self.db.get_state(ACCOUNT_PAUSE_UNTIL)
        if raw is None:
            return None
        until = datetime.fromisoformat(raw)
        return until if until > self.now() else None

    # ------------------------------------------------------------ уведомления админу

    def alert(self, text: str) -> None:
        self.db.execute("INSERT INTO admin_alerts (ts, text) VALUES (?, ?)", (self._ts(), text))
        log.warning("ALERT: %s", text)

    def pending_alerts(self) -> list[tuple[int, str]]:
        rows = self.db.all("SELECT id, text FROM admin_alerts WHERE sent_at IS NULL ORDER BY id")
        return [(r["id"], r["text"]) for r in rows]

    def mark_alert_sent(self, alert_id: int) -> None:
        self.db.execute("UPDATE admin_alerts SET sent_at = ? WHERE id = ?", (self._ts(), alert_id))

    # ------------------------------------------------------------ главный шлюз

    def check(self, out: Outgoing) -> Decision:
        reason = self._evaluate(out)
        decision = Decision(reason is None, reason or "ok")
        self.db.execute(
            "INSERT INTO safety_log (ts, day, channel, recipient, allowed, reason) VALUES (?, ?, ?, ?, ?, ?)",
            (self._ts(), self._day(), out.channel, _key(out.recipient), int(decision.allowed), decision.reason),
        )
        log.log(
            logging.INFO if decision.allowed else logging.WARNING,
            "%s %s → %s: %s", out.channel, out.recipient, "ALLOW" if decision else "DENY", decision.reason,
        )
        return decision

    def record_sent(self, out: Outgoing) -> None:
        """Вызывать после успешной отправки (send_guarded делает это сам)."""
        self.db.execute(
            "INSERT INTO outbox (ts, day, channel, recipient, group_id, campaign, text) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (self._ts(), self._day(), out.channel, _key(out.recipient), _key(out.group_id), out.campaign, out.text),
        )
        if out.channel == Channel.OUTREACH:
            gap = self._rng.uniform(self.settings.outreach_min_gap_min, self.settings.outreach_max_gap_min)
            next_at = self.now() + timedelta(minutes=gap)
            self.db.set_state(OUTREACH_NEXT_AT, next_at.isoformat(timespec="seconds"), self._ts())

    async def send_guarded(self, out: Outgoing, do_send: Callable[[], Awaitable[T]]) -> SendResult[T]:
        """check → отправка → запись. Исключения отправки пробрасываются, запись тогда не делается.

        Проактивные отправки идут строго по одной, чтобы два параллельных вызова
        не проскочили один и тот же лимит.
        """
        if out.channel in STOPPABLE:
            async with self._lock:
                return await self._send(out, do_send)
        return await self._send(out, do_send)

    async def _send(self, out: Outgoing, do_send: Callable[[], Awaitable[T]]) -> SendResult[T]:
        decision = self.check(out)
        if not decision:
            return SendResult(decision)
        value = await do_send()
        self.record_sent(out)
        return SendResult(decision, value)

    def _evaluate(self, out: Outgoing) -> str | None:
        ch = out.channel
        rec = _key(out.recipient)
        if ch == Channel.ADD_MEMBER:
            return "добавлять участников куда-либо запрещено"
        if ch == Channel.ADMIN:
            return None if rec in {str(i) for i in self.settings.admin_ids} else "получатель не админ"
        if ch == Channel.REPLY:
            return self._check_reply(rec)
        if ch in STOPPABLE and self.is_stopped():
            return "включён «⛔ Стоп всё»"
        if ch in ACCOUNT_CHANNELS:
            until = self.account_pause_until()
            if until is not None:
                return f"аккаунт на паузе после флуда до {until.astimezone(self.tz):%d.%m %H:%M}"
            hold = self.hold(ACCOUNT_HOLD)
            if hold:
                return f"аккаунт остановлен: {hold.get('reason', '')}"
        checks = {
            Channel.BROADCAST: self._check_broadcast,
            Channel.OUTREACH: self._check_outreach,
            Channel.GROUP: self._check_group,
            Channel.GROUP_DM: self._check_group_dm,
            Channel.JOIN: self._check_join,
        }
        return checks[ch](out, rec)

    # ------------------------------------------------------------ проверки по каналам

    def _check_reply(self, rec: str | None) -> str | None:
        if rec is None:
            return "нет получателя"
        last = self.db.scalar("SELECT ts FROM inbound WHERE recipient = ?", (rec,))
        window = timedelta(hours=self.settings.reply_window_hours)
        if last is None or datetime.fromisoformat(last) < self.now() - window:
            return f"человек не писал сам за последние {self.settings.reply_window_hours} ч"
        return None

    def _check_broadcast(self, out: Outgoing, rec: str | None) -> str | None:
        hold = self.hold(BROADCAST_HOLD)
        if hold:
            return f"рассылки заблокированы: {hold.get('reason', '')}"
        if rec is None:
            return "нет получателя"
        if not out.campaign:
            return "у рассылки не указан campaign"
        if not out.has_unsubscribe_button:
            return "нет кнопки «Не присылать анонсы»"
        sub = self.db.one("SELECT unsubscribed_at, blocked_at FROM subscribers WHERE user_id = ?", (rec,))
        if sub is None:
            return "человек не нажимал /start"
        if sub["unsubscribed_at"]:
            return "отписался от анонсов"
        if sub["blocked_at"]:
            return "заблокировал бота"
        last = self.db.scalar(
            "SELECT MAX(ts) FROM outbox WHERE channel = ? AND recipient = ?", (Channel.BROADCAST, rec)
        )
        min_gap = timedelta(days=self.settings.broadcast_min_days)
        if last is not None and self.now() - datetime.fromisoformat(last) < min_gap:
            return f"рассылка этому человеку была меньше {self.settings.broadcast_min_days} дн. назад"
        return None

    def _check_outreach(self, out: Outgoing, rec: str | None) -> str | None:
        s = self.settings
        if rec is None:
            return "нет получателя"
        hold = self.hold(OUTREACH_HOLD)
        if hold:
            return f"касания на паузе: {hold.get('reason', '')}"
        if self.is_stop_listed(rec):
            return "в стоп-листе"
        if self._touched(rec):
            return "уже было касание — повторно не пишем"
        day_n = self.account_day_number()
        if day_n is None:
            return "не задана дата начала аккаунта (/warmup_start)"
        if day_n <= s.outreach_warmup_days:
            return (
                f"прогрев аккаунта: день {day_n} из {s.outreach_warmup_days}, "
                f"первые сообщения с {s.outreach_warmup_days + 1}-го дня"
            )
        hours = self.outside_outreach_hours()
        if hours:
            return hours
        limit = self.outreach_daily_limit()
        if self.count_today(Channel.OUTREACH) >= limit:
            return f"дневной лимит касаний исчерпан ({limit})"
        next_at = self.next_outreach_at()
        if next_at is not None:
            return f"пауза между сообщениями до {next_at.astimezone(self.tz):%H:%M}"
        problems = self.outreach_text_issues(out.text)
        if problems:
            return "текст: " + "; ".join(problems)
        return None

    def outreach_text_issues(self, text: str) -> list[str]:
        """Проблемы текста первого сообщения, включая похожесть на последние отправленные.

        Этап D вызывает это до check(), чтобы попросить Claude переписать текст.
        """
        s = self.settings
        problems = outreach_text_problems(text, s)
        recent = self.db.all(
            "SELECT text FROM outbox WHERE channel = ? AND text IS NOT NULL ORDER BY id DESC LIMIT ?",
            (Channel.OUTREACH, s.outreach_similarity_window),
        )
        worst = max((similarity(text, r["text"]) for r in recent), default=0.0)
        if worst > s.outreach_similarity_max:
            problems.append(f"похож на отправленный ранее на {worst:.0%} — переписать")
        return problems

    def _check_group(self, out: Outgoing, rec: str | None) -> str | None:
        s = self.settings
        group = _key(out.group_id) or rec
        if group is None:
            return "не указана группа"
        if self.is_group_blacklisted(group):
            return "группа в чёрном списке"
        if out.group_no_ads:
            return "в группе запрещена реклама — только в личку автору, после кнопки админа"
        score_problem = self._score_problem(out.score)
        if score_problem:
            return score_problem
        if not (out.admin_approved or s.scout_autopilot):
            return "нужно подтверждение админа"
        in_group = self.db.scalar(
            "SELECT COUNT(*) FROM outbox WHERE channel = ? AND group_id = ? AND day = ?",
            (Channel.GROUP, group, self._day()),
        )
        if in_group >= s.scout_per_group_daily:
            return "в этой группе сегодня уже был ответ"
        if self.count_today(*SCOUT) >= s.scout_daily_limit:
            return f"дневной лимит ответов в группах исчерпан ({s.scout_daily_limit})"
        if has_link(out.text):
            return "в ответе в группе не должно быть ссылок и @упоминаний"
        if not last_line_is(out.text, GROUP_FOOTER):
            return f"последняя строка должна быть «{GROUP_FOOTER}»"
        return None

    def _check_group_dm(self, out: Outgoing, rec: str | None) -> str | None:
        s = self.settings
        if rec is None:
            return "нет получателя"
        if self.is_stop_listed(rec):
            return "в стоп-листе"
        if self._touched(rec):
            return "уже было касание — повторно не пишем"
        if out.group_id is not None and self.is_group_blacklisted(_key(out.group_id)):
            return "группа в чёрном списке"
        score_problem = self._score_problem(out.score)
        if score_problem:
            return score_problem
        if not out.admin_approved:
            return "личное сообщение автору — только после кнопки админа"
        if self.count_today(*SCOUT) >= s.scout_daily_limit:
            return f"дневной лимит ответов в группах исчерпан ({s.scout_daily_limit})"
        if has_link(out.text):
            return "в первом сообщении не должно быть ссылок и @упоминаний"
        return None

    def _check_join(self, out: Outgoing, rec: str | None) -> str | None:
        if rec is None:
            return "не указана группа"
        if self.is_group_blacklisted(rec):
            return "группа в чёрном списке"
        limit = self.settings.group_join_daily_limit
        if self.count_today(Channel.JOIN) >= limit:
            return f"лимит вступлений в группы на сегодня исчерпан ({limit})"
        return None

    def _score_problem(self, score: float | None) -> str | None:
        min_score = self.settings.scout_min_score
        if score is None or score < min_score:
            return f"оценка вопроса {score} ниже {min_score:g} из 10"
        return None

    # ------------------------------------------------------------ лимиты и прогрев

    def count_today(self, *channels: Channel) -> int:
        marks = ",".join("?" for _ in channels)
        return self.db.scalar(
            f"SELECT COUNT(*) FROM outbox WHERE day = ? AND channel IN ({marks})",
            (self._day(), *channels),
        )

    def _touched(self, rec: str) -> bool:
        marks = ",".join("?" for _ in FIRST_TOUCH)
        return bool(self.db.scalar(
            f"SELECT 1 FROM outbox WHERE recipient = ? AND channel IN ({marks}) LIMIT 1", (rec, *FIRST_TOUCH)
        ))

    def set_account_start(self, day: date) -> None:
        self.db.set_state(ACCOUNT_STARTED_ON, day.isoformat(), self._ts())

    def account_start(self) -> date | None:
        raw = self.db.get_state(ACCOUNT_STARTED_ON)
        return None if raw is None else date.fromisoformat(raw)

    def account_day_number(self) -> int | None:
        """День жизни аккаунта: день начала — 1-й."""
        start = self.account_start()
        return None if start is None else (self.today() - start).days + 1

    def outreach_daily_limit(self) -> int:
        """0 во время прогрева, потом 5 → 10 → OUTREACH_DAILY_LIMIT."""
        s = self.settings
        day_n = self.account_day_number()
        if day_n is None or day_n <= s.outreach_warmup_days:
            return 0
        sending_day = day_n - s.outreach_warmup_days
        if sending_day <= 7:
            limit = s.outreach_week1_limit
        elif sending_day <= 14:
            limit = s.outreach_week2_limit
        else:
            limit = s.outreach_daily_limit
        return min(limit, s.outreach_daily_limit)

    def outside_outreach_hours(self) -> str | None:
        s = self.settings
        local = self.local_now()
        if local.weekday() == 6:
            return "воскресенье — не пишем"
        if not s.outreach_hour_start <= local.hour < s.outreach_hour_end:
            return f"вне рабочих часов {s.outreach_hour_start:02d}:00–{s.outreach_hour_end:02d}:00 по Бангкоку"
        return None

    def next_outreach_at(self) -> datetime | None:
        raw = self.db.get_state(OUTREACH_NEXT_AT)
        if raw is None:
            return None
        next_at = datetime.fromisoformat(raw)
        return next_at if next_at > self.now() else None

    # ------------------------------------------------------------ подписчики

    def subscribe(self, user_id: int, username: str | None = None, first_name: str | None = None) -> None:
        now = self._ts()
        self.db.execute(
            "INSERT INTO subscribers (user_id, username, first_name, started_at, last_start_at) "
            "VALUES (?, ?, ?, ?, ?) ON CONFLICT(user_id) DO UPDATE SET username = excluded.username, "
            "first_name = excluded.first_name, last_start_at = excluded.last_start_at, "
            "unsubscribed_at = NULL, blocked_at = NULL",
            (user_id, username, first_name, now, now),
        )

    def unsubscribe(self, user_id: int | str, via: str) -> bool:
        cur = self.db.execute(
            "UPDATE subscribers SET unsubscribed_at = ? WHERE user_id = ? AND unsubscribed_at IS NULL",
            (self._ts(), str(user_id)),
        )
        if cur.rowcount:
            self._event("unsubscribe", user_id, via)
        return bool(cur.rowcount)

    def is_subscribed(self, user_id: int | str) -> bool:
        return bool(self.db.scalar(
            "SELECT 1 FROM subscribers WHERE user_id = ? AND unsubscribed_at IS NULL AND blocked_at IS NULL",
            (str(user_id),),
        ))

    def subscriber_count(self) -> int:
        return self.db.scalar(
            "SELECT COUNT(*) FROM subscribers WHERE unsubscribed_at IS NULL AND blocked_at IS NULL"
        )

    # ------------------------------------------------------------ входящие и стоп-слова

    def touch_inbound(self, recipient: int | str) -> None:
        """Человек сам написал: после этого ему можно отвечать (канал REPLY)."""
        self.db.execute(
            "INSERT INTO inbound (recipient, ts) VALUES (?, ?) ON CONFLICT(recipient) DO UPDATE SET ts = excluded.ts",
            (str(recipient), self._ts()),
        )

    def record_reply(self, recipient: int | str) -> bool:
        """Для сводки «ответили»: человек ответил на наше последнее сообщение, написанное первыми."""
        since = (self.now() - timedelta(days=REPLY_TRACK_DAYS)).isoformat(timespec="seconds")
        marks = ",".join("?" for _ in PROACTIVE)
        row = self.db.one(
            f"SELECT id, channel, replied_at FROM outbox WHERE recipient = ? AND channel IN ({marks}) "
            "AND ts >= ? ORDER BY id DESC LIMIT 1",
            (str(recipient), *PROACTIVE, since),
        )
        if row is None or row["replied_at"]:
            return False
        self.db.execute("UPDATE outbox SET replied_at = ? WHERE id = ?", (self._ts(), row["id"]))
        self._event("reply", recipient, row["channel"])
        return True

    def is_stop_listed(self, recipient: int | str) -> bool:
        return bool(self.db.scalar("SELECT 1 FROM stop_list WHERE recipient = ?", (str(recipient),)))

    def register_stop(self, recipient: int | str, source: str, text: str = "") -> StopResult:
        """Стоп-слово: навсегда в стоп-лист, отписка от анонсов, одно извинение.

        source: "bot" — написали боту, "account" — написали Telethon-аккаунту.
        Если за день больше OUTREACH_STOP_PAUSE_THRESHOLD человек написали «стоп» аккаунту,
        касания встают на паузу до ручного включения.
        """
        rec = str(recipient)
        row = self.db.one("SELECT apologized FROM stop_list WHERE recipient = ?", (rec,))
        new = row is None
        if new:
            self.db.execute(
                "INSERT INTO stop_list (recipient, source, reason, ts, day, apologized) VALUES (?, ?, ?, ?, ?, 1)",
                (rec, source, text[:200], self._ts(), self._day()),
            )
            self._event("stop", rec, source)
        else:
            self.db.execute("UPDATE stop_list SET apologized = 1 WHERE recipient = ?", (rec,))
        self.db.execute(
            "UPDATE subscribers SET unsubscribed_at = ? WHERE user_id = ? AND unsubscribed_at IS NULL",
            (self._ts(), rec),
        )
        if new and source == "account":
            self._check_stop_threshold()
        return StopResult(new=new, apologize=new or not row["apologized"])

    def _check_stop_threshold(self) -> None:
        threshold = self.settings.outreach_stop_pause_threshold
        today = self.db.scalar(
            "SELECT COUNT(*) FROM events WHERE kind = 'stop' AND detail = 'account' AND day = ?", (self._day(),)
        )
        if today > threshold and self.hold(OUTREACH_HOLD) is None:
            reason = f"за день {today} человек написали «стоп» (порог {threshold})"
            self._set_flag(OUTREACH_HOLD, reason=reason)
            self.alert(f"⏸ Касания на паузе: {reason}. Включить — в /admin.")

    # ------------------------------------------------------------ сигналы о бане

    def record_blocked(self, user_id: int | str) -> None:
        """Человек заблокировал бота."""
        cur = self.db.execute(
            "UPDATE subscribers SET blocked_at = ? WHERE user_id = ? AND blocked_at IS NULL",
            (self._ts(), str(user_id)),
        )
        if not cur.rowcount:
            return
        self._event("blocked", user_id, None)
        campaign = self.db.scalar(
            "SELECT campaign FROM outbox WHERE channel = ? AND recipient = ? ORDER BY id DESC LIMIT 1",
            (Channel.BROADCAST, str(user_id)),
        )
        if campaign:
            self.broadcast_health(campaign)

    def broadcast_health(self, campaign: str) -> float:
        """Доля получателей рассылки, которые заблокировали бота в течение BROADCAST_MIN_DAYS после неё.

        Больше BROADCAST_MAX_BLOCK_RATE → следующие рассылки блокируются, админу уведомление.
        """
        sent = {
            r["recipient"]: datetime.fromisoformat(r["ts"])
            for r in self.db.all(
                "SELECT recipient, MIN(ts) AS ts FROM outbox WHERE channel = ? AND campaign = ? GROUP BY recipient",
                (Channel.BROADCAST, campaign),
            )
        }
        if not sent:
            return 0.0
        window = timedelta(days=self.settings.broadcast_min_days)
        blocked = set()
        marks = ",".join("?" for _ in sent)
        for r in self.db.all(
            f"SELECT recipient, ts FROM events WHERE kind = 'blocked' AND recipient IN ({marks})", tuple(sent)
        ):
            got = sent[r["recipient"]]
            if got <= datetime.fromisoformat(r["ts"]) <= got + window:
                blocked.add(r["recipient"])
        rate = len(blocked) / len(sent)
        if rate > self.settings.broadcast_max_block_rate and self.hold(BROADCAST_HOLD) is None:
            reason = (
                f"после рассылки «{campaign}» бота заблокировали {len(blocked)} из {len(sent)} "
                f"({rate:.1%}, порог {self.settings.broadcast_max_block_rate:.0%})"
            )
            self._set_flag(BROADCAST_HOLD, reason=reason, campaign=campaign)
            self.alert(f"🚫 Следующая рассылка заблокирована: {reason}. Разблокировать — в /admin.")
        return rate

    def on_flood(self, error: str, seconds: int | None = None) -> datetime:
        """PeerFloodError / FloodWaitError: пауза аккаунта на FLOOD_PAUSE_HOURS (или дольше, если просит Telegram)."""
        pause = timedelta(hours=self.settings.flood_pause_hours)
        if seconds:
            pause = max(pause, timedelta(seconds=seconds))
        until = self.now() + pause
        current = self.account_pause_until()
        if current is not None and current > until:
            until = current
        else:
            self.db.set_state(ACCOUNT_PAUSE_UNTIL, until.isoformat(timespec="seconds"), self._ts())
        self._event("flood", None, error)
        self.alert(f"🌊 {error}: аккаунт на паузе до {until.astimezone(self.tz):%d.%m %H:%M} (Бангкок).")
        return until

    def on_spambot_reply(self, text: str) -> bool:
        """Ежедневная самопроверка через @SpamBot. True — ограничений нет."""
        if spambot_says_free(text):
            return True
        self._event("spambot", None, text[:500])
        if self.hold(ACCOUNT_HOLD) is None:
            self._set_flag(ACCOUNT_HOLD, reason="@SpamBot сообщает об ограничении")
        self.alert(f"🚨 @SpamBot: на аккаунте ограничение, все касания остановлены.\n\n{text[:1000]}")
        return False

    def on_group_penalty(self, group_id: int | str, reason: str) -> None:
        """Наше сообщение удалил админ группы или аккаунт исключили — группа в чёрный список."""
        gid = str(group_id)
        self.db.execute(
            "INSERT INTO group_blacklist (group_id, reason, ts) VALUES (?, ?, ?) ON CONFLICT(group_id) DO NOTHING",
            (gid, reason, self._ts()),
        )
        self._event("group_penalty", gid, reason)
        self.alert(f"⚫ Группа {gid} в чёрном списке: {reason}.")

    def is_group_blacklisted(self, group_id: str | None) -> bool:
        if group_id is None:
            return False
        return bool(self.db.scalar("SELECT 1 FROM group_blacklist WHERE group_id = ?", (group_id,)))

    # ------------------------------------------------------------ сводка и статус

    def _event(self, kind: str, recipient: int | str | None, detail: str | None) -> None:
        self.db.execute(
            "INSERT INTO events (ts, day, kind, recipient, detail) VALUES (?, ?, ?, ?, ?)",
            (self._ts(), self._day(), kind, _key(recipient), detail),
        )

    def daily_stats(self, day: date | None = None) -> dict[str, Any]:
        d = (day or self.today()).isoformat()
        sent = {
            r["channel"]: r["n"]
            for r in self.db.all("SELECT channel, COUNT(*) AS n FROM outbox WHERE day = ? GROUP BY channel", (d,))
        }
        events = {
            r["kind"]: r["n"]
            for r in self.db.all("SELECT kind, COUNT(*) AS n FROM events WHERE day = ? GROUP BY kind", (d,))
        }
        first = (Channel.BROADCAST, Channel.OUTREACH, Channel.GROUP, Channel.GROUP_DM)
        return {
            "day": d,
            "sent": {ch: sent.get(ch, 0) for ch in first},
            "sent_total": sum(sent.get(ch, 0) for ch in (*PROACTIVE, Channel.GROUP)),
            "replies_to_inbound": sent.get(Channel.REPLY, 0),
            "joins": sent.get(Channel.JOIN, 0),
            "replied": events.get("reply", 0),
            "stops": events.get("stop", 0),
            "unsubscribed": events.get("unsubscribe", 0),
            "blocked": events.get("blocked", 0),
            "denied": self.db.scalar("SELECT COUNT(*) FROM safety_log WHERE day = ? AND allowed = 0", (d,)),
            "subscribers": self.subscriber_count(),
        }

    def status(self) -> dict[str, Any]:
        return {
            "stop_all": self._flag(STOP_ALL),
            "account_pause_until": self.account_pause_until(),
            "holds": {key: self.hold(key) for key in HOLDS},
            "account_start": self.account_start(),
            "account_day": self.account_day_number(),
            "outreach_limit": self.outreach_daily_limit(),
            "outreach_today": self.count_today(Channel.OUTREACH),
            "scout_today": self.count_today(*SCOUT),
            "joins_today": self.count_today(Channel.JOIN),
            "subscribers": self.subscriber_count(),
        }
