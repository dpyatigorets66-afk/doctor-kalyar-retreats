from __future__ import annotations

from typing import Any

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from . import texts
from .safety import HOLDS

UNSUBSCRIBE_CB = "unsub"


def unsubscribe_kb() -> InlineKeyboardMarkup:
    """Обязательная кнопка под каждой рассылкой."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=texts.UNSUBSCRIBE_BUTTON, callback_data=UNSUBSCRIBE_CB)],
    ])


def has_unsubscribe_button(markup: Any) -> bool:
    if not isinstance(markup, InlineKeyboardMarkup):
        return False
    return any(b.callback_data == UNSUBSCRIBE_CB for row in markup.inline_keyboard for b in row)


def admin_kb(status: dict[str, Any]) -> InlineKeyboardMarkup:
    if status["stop_all"]:
        main = InlineKeyboardButton(text="▶️ Включить всё", callback_data="adm:resume")
    else:
        main = InlineKeyboardButton(text="⛔ Стоп всё", callback_data="adm:stop")
    rows = [
        [main],
        [
            InlineKeyboardButton(text="📊 Сводка за сегодня", callback_data="adm:summary"),
            InlineKeyboardButton(text="🔄 Обновить", callback_data="adm:status"),
        ],
    ]
    for key in HOLDS:
        if status["holds"].get(key):
            rows.append([InlineKeyboardButton(
                text=f"▶️ Снять: {texts.HOLD_LABELS[key]}", callback_data=f"adm:clear:{key}",
            )])
    return InlineKeyboardMarkup(inline_keyboard=rows)
