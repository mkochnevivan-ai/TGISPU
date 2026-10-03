"""Telegram-бот с расписанием ИГЭУ (schedule.ispu.ru).

Запуск:  python -m ispu_bot   (токен — в переменной BOT_TOKEN или в файле .env)
"""

from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from . import config
from .bot import client, router
from .notifications import Notifier
from .profile import setup_profile

log = logging.getLogger("ispu_bot")


async def warm_up() -> None:
    """Заранее загружаем списки групп и преподавателей, чтобы поиск отвечал сразу."""
    try:
        groups = await client.all_groups()
        teachers = await client.teachers()
        log.info("loaded %d groups and %d teachers", len(groups), len(teachers))
    except Exception:  # noqa: BLE001
        log.exception("failed to preload groups")


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if not config.BOT_TOKEN:
        raise SystemExit("Не задан BOT_TOKEN: впишите его в файл .env (BOT_TOKEN=...)")
    bot = Bot(config.BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML, link_preview_is_disabled=True))
    dp = Dispatcher()
    dp.include_router(router)
    if config.SETUP_PROFILE:
        await setup_profile(bot)
    tasks = [asyncio.create_task(Notifier(bot).run()), asyncio.create_task(warm_up())]
    try:
        await dp.start_polling(bot)
    finally:
        for t in tasks:
            t.cancel()


if __name__ == "__main__":
    asyncio.run(main())
