"""Оформление профиля бота: аватарка, описание, список команд.

Telegram ограничивает частоту этих вызовов, поэтому повторяем их,
только если что-то поменялось (отпечаток хранится в state.json).
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

from aiogram import Bot
from aiogram.types import BotCommand, FSInputFile, InputProfilePhotoStatic

from .bot import state

log = logging.getLogger("ispu_bot")

AVATAR = Path(__file__).parent / "assets" / "avatar.jpg"

SHORT_DESCRIPTION = "📅 Расписание ИГЭУ: любая группа и преподаватель, «что сейчас», рассылки и напоминания."

DESCRIPTION = (
    "📅 Расписание ИГЭУ прямо в Telegram — данные с schedule.ispu.ru.\n\n"
    "• Сегодня, завтра, неделя — листайте кнопками ◀ ▶\n"
    "• «Сейчас»: какая пара идёт и сколько до конца\n"
    "• Расписание любого преподавателя — напишите фамилию\n"
    "• Утренняя рассылка и напоминания перед парой\n"
    "• Уведомления, когда расписание меняется\n\n"
    "Нажмите «Старт» и выберите свою группу 👇"
)

COMMANDS = [
    BotCommand(command="today", description="📅 Расписание на сегодня"),
    BotCommand(command="tomorrow", description="➡️ На завтра"),
    BotCommand(command="now", description="⏰ Какая пара сейчас"),
    BotCommand(command="week", description="🗓 Эта неделя"),
    BotCommand(command="nextweek", description="⏭ Следующая неделя"),
    BotCommand(command="day", description="📆 На дату: /day 15.10"),
    BotCommand(command="teacher", description="🔎 Преподаватель: /teacher Фамилия"),
    BotCommand(command="group", description="🎓 Сменить группу"),
    BotCommand(command="subgroup", description="👥 Выбрать подгруппу"),
    BotCommand(command="settings", description="⚙️ Рассылки и напоминания"),
    BotCommand(command="help", description="📖 Справка"),
]


def _fingerprint() -> str:
    h = hashlib.sha1()
    h.update(SHORT_DESCRIPTION.encode())
    h.update(DESCRIPTION.encode())
    h.update(repr([(c.command, c.description) for c in COMMANDS]).encode())
    if AVATAR.exists():
        h.update(AVATAR.read_bytes())
    return h.hexdigest()


async def setup_profile(bot: Bot) -> None:
    fp = _fingerprint()
    if state.get("profile").get("fingerprint") == fp:
        return
    try:
        await bot.set_my_commands(COMMANDS)
        await bot.set_my_short_description(short_description=SHORT_DESCRIPTION)
        await bot.set_my_description(description=DESCRIPTION)
        if AVATAR.exists():
            await bot.set_my_profile_photo(photo=InputProfilePhotoStatic(photo=FSInputFile(AVATAR)))
    except Exception:  # noqa: BLE001 — оформление не должно мешать работе бота
        log.exception("failed to set up bot profile (it will be retried on next start)")
        return
    state.update("profile", fingerprint=fp)
    log.info("bot profile updated: avatar, description, commands")
