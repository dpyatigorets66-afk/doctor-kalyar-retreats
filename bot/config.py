"""Настройки бота из .env. Значения по умолчанию — безопасные (см. .env.example)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Mapping


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class Settings:
    bot_token: str = ""
    admin_ids: frozenset[int] = field(default_factory=frozenset)
    db_path: str = "data/bot.sqlite3"
    timezone: str = "Asia/Bangkok"

    # C. Рассылки подписчикам
    broadcast_min_days: int = 3
    broadcast_max_block_rate: float = 0.03

    # D. Касания клиентской базы
    outreach_warmup_days: int = 7
    outreach_week1_limit: int = 5
    outreach_week2_limit: int = 10
    outreach_daily_limit: int = 20
    outreach_min_gap_min: int = 4
    outreach_max_gap_min: int = 12
    outreach_hour_start: int = 10
    outreach_hour_end: int = 19
    outreach_max_len: int = 300
    outreach_max_emoji: int = 2
    outreach_similarity_max: float = 0.70
    outreach_similarity_window: int = 50
    outreach_stop_pause_threshold: int = 2
    flood_pause_hours: int = 48

    # E. Ответы в группах
    scout_min_score: float = 8.0
    scout_daily_limit: int = 10
    scout_per_group_daily: int = 1
    scout_autopilot: bool = False
    group_join_daily_limit: int = 2

    # Ответ разрешён, только если человек писал сам за последние N часов
    reply_window_hours: int = 24
    summary_hour: int = 21

    def __post_init__(self) -> None:
        problems = []
        positive = (
            "broadcast_min_days", "outreach_week1_limit", "outreach_week2_limit",
            "outreach_daily_limit", "outreach_max_len", "outreach_similarity_window",
            "flood_pause_hours", "scout_daily_limit", "scout_per_group_daily",
            "reply_window_hours",
        )
        for name in positive:
            if getattr(self, name) <= 0:
                problems.append(f"{name.upper()} должно быть больше 0")
        non_negative = (
            "outreach_warmup_days", "group_join_daily_limit", "outreach_max_emoji", "outreach_stop_pause_threshold",
        )
        for name in non_negative:
            if getattr(self, name) < 0:
                problems.append(f"{name.upper()} не может быть отрицательным")
        if not 0 <= self.scout_min_score <= 10:
            problems.append("SCOUT_MIN_SCORE должно быть от 0 до 10")
        if not 0 < self.outreach_min_gap_min <= self.outreach_max_gap_min:
            problems.append("нужно 0 < OUTREACH_MIN_GAP_MIN <= OUTREACH_MAX_GAP_MIN")
        if not 0 <= self.outreach_hour_start < self.outreach_hour_end <= 24:
            problems.append("нужно 0 <= OUTREACH_HOUR_START < OUTREACH_HOUR_END <= 24")
        if not 0 < self.broadcast_max_block_rate < 1:
            problems.append("BROADCAST_MAX_BLOCK_RATE должно быть между 0 и 1")
        if not 0 < self.outreach_similarity_max < 1:
            problems.append("OUTREACH_SIMILARITY_MAX должно быть между 0 и 1")
        if not 0 <= self.summary_hour <= 23:
            problems.append("SUMMARY_HOUR должно быть от 0 до 23")
        if problems:
            raise ConfigError("; ".join(problems))


def _parse_bool(raw: str) -> bool:
    value = raw.strip().lower()
    if value in {"1", "true", "yes", "on", "да"}:
        return True
    if value in {"0", "false", "no", "off", "нет", ""}:
        return False
    raise ConfigError(f"не понимаю логическое значение {raw!r}")


def _parse_ids(raw: str) -> frozenset[int]:
    try:
        return frozenset(int(part) for part in raw.replace(";", ",").split(",") if part.strip())
    except ValueError as exc:
        raise ConfigError(f"ADMIN_IDS: ожидаются числовые id через запятую, получено {raw!r}") from exc


def load_settings(env: Mapping[str, str] | None = None, env_file: str | Path | None = ".env") -> Settings:
    """Читает настройки из переменных окружения (и .env, если он есть)."""
    if env is None:
        if env_file and Path(env_file).exists():
            from dotenv import load_dotenv

            load_dotenv(env_file)
        env = os.environ

    values: dict[str, object] = {}
    for f in fields(Settings):
        raw = env.get(f.name.upper())
        if raw is None or raw.strip() == "":
            continue
        raw = raw.strip()
        try:
            if f.name == "admin_ids":
                values[f.name] = _parse_ids(raw)
            elif f.type == "bool":
                values[f.name] = _parse_bool(raw)
            elif f.type == "int":
                values[f.name] = int(raw)
            elif f.type == "float":
                values[f.name] = float(raw)
            else:
                values[f.name] = raw
        except ValueError as exc:
            if isinstance(exc, ConfigError):
                raise
            raise ConfigError(f"{f.name.upper()}: неверное значение {raw!r}") from exc
    return Settings(**values)
