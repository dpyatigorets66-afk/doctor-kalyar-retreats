"""Запуск: python -m bot"""

import asyncio
import logging
import sys

from .app import run
from .config import ConfigError, load_settings


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        settings = load_settings()
    except ConfigError as exc:
        sys.exit(f"Ошибка в .env: {exc}")
    if not settings.bot_token:
        sys.exit("BOT_TOKEN не задан — скопируйте .env.example в .env и заполните.")
    if not settings.admin_ids:
        logging.warning("ADMIN_IDS пуст: админ-панель и уведомления недоступны")
    asyncio.run(run(settings))


if __name__ == "__main__":
    main()
