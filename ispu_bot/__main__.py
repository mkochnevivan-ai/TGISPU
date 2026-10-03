"""Telegram-бот с расписанием группы ИГЭУ (по умолчанию 1-12А, ИФФ).

Запуск:  BOT_TOKEN=... python -m ispu_bot
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import date, datetime, time as dtime, timedelta
from zoneinfo import ZoneInfo

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    BotCommand,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)

from .formatting import format_day, monday_of
from .scraper import IspuClient, ScheduleError
from .storage import UserStorage

log = logging.getLogger("ispu_bot")


def _load_dotenv(path: str = ".env") -> None:
    """Подхватывает переменные из .env, не перезаписывая уже заданные."""
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, _, value = line.partition("=")
                    os.environ.setdefault(key.strip(), value.strip().strip("'\""))
    except FileNotFoundError:
        pass


_load_dotenv()

TZ = ZoneInfo(os.getenv("TZ_NAME", "Europe/Moscow"))
FACULTY = os.getenv("ISPU_FACULTY", "ИФФ")
COURSE = os.getenv("ISPU_COURSE", "1")
GROUP = os.getenv("ISPU_GROUP", "12А")
DEFAULT_SUBGROUP = os.getenv("ISPU_SUBGROUP", "х")
NOTIFY_TIME = os.getenv("NOTIFY_TIME", "07:00")
DATA_FILE = os.getenv("DATA_FILE", "data/users.json")

BTN_TODAY = "📅 Сегодня"
BTN_TOMORROW = "➡️ Завтра"
BTN_WEEK = "🗓 Неделя"
BTN_NEXT_WEEK = "⏭ След. неделя"
BTN_SUBGROUP = "👥 Подгруппа"
BTN_NOTIFY = "🔔 Рассылка"

KEYBOARD = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text=BTN_TODAY), KeyboardButton(text=BTN_TOMORROW)],
        [KeyboardButton(text=BTN_WEEK), KeyboardButton(text=BTN_NEXT_WEEK)],
        [KeyboardButton(text=BTN_SUBGROUP), KeyboardButton(text=BTN_NOTIFY)],
    ],
    resize_keyboard=True,
)

client = IspuClient(FACULTY, COURSE, GROUP)
storage = UserStorage(DATA_FILE)
router = Router()


def today() -> date:
    return datetime.now(TZ).date()


def subgroup_of(chat_id: int) -> str:
    return storage.get(chat_id).get("subgroup", DEFAULT_SUBGROUP)


def split_message(text: str, limit: int = 4000) -> list[str]:
    parts, current = [], ""
    for block in text.split("\n\n"):
        candidate = f"{current}\n\n{block}" if current else block
        if len(candidate) > limit and current:
            parts.append(current)
            current = block
        else:
            current = candidate
    if current:
        parts.append(current)
    return parts


async def day_text(d: date, subgroup: str, header: str = "") -> str:
    schedule = await client.schedule_for(d, subgroup)
    return format_day(d, schedule, header=header)


async def week_text(monday: date, subgroup: str) -> str:
    blocks = [f"🎓 <b>Группа {client.group_title}</b>, подгруппа «{subgroup}»"]
    for i in range(7):
        d = monday + timedelta(days=i)
        schedule = await client.schedule_for(d, subgroup)
        # воскресенье показываем только если в нём есть пары
        if i == 6 and (schedule is None or not schedule.lessons_on(d)):
            continue
        blocks.append(format_day(d, schedule))
    return "\n\n".join(blocks)


async def reply(message: Message, coro) -> None:
    try:
        text = await coro
    except ScheduleError as e:
        log.warning("schedule error: %s", e)
        await message.answer(f"⚠️ Не удалось получить расписание: {e}")
        return
    for part in split_message(text):
        await message.answer(part, reply_markup=KEYBOARD)


# --------------------------------------------------------------------------- #
# Обработчики
# --------------------------------------------------------------------------- #


@router.message(CommandStart())
@router.message(Command("help"))
async def cmd_start(message: Message) -> None:
    sg = subgroup_of(message.chat.id)
    await message.answer(
        f"Привет! Я показываю расписание группы <b>{client.group_title}</b> ({FACULTY}) "
        f"с сайта schedule.ispu.ru.\n\n"
        f"Текущая подгруппа: <b>{sg}</b>\n\n"
        "Команды:\n"
        "/today — на сегодня\n"
        "/tomorrow — на завтра\n"
        "/week — на эту неделю\n"
        "/nextweek — на следующую неделю\n"
        "/day <code>дд.мм</code> — на конкретную дату\n"
        "/subgroup — выбрать подгруппу\n"
        f"/subscribe — присылать расписание каждый день в {NOTIFY_TIME}\n"
        "/unsubscribe — отключить рассылку",
        reply_markup=KEYBOARD,
    )


@router.message(Command("today"))
@router.message(F.text == BTN_TODAY)
async def cmd_today(message: Message) -> None:
    await reply(message, day_text(today(), subgroup_of(message.chat.id), "Сегодня · "))


@router.message(Command("tomorrow"))
@router.message(F.text == BTN_TOMORROW)
async def cmd_tomorrow(message: Message) -> None:
    await reply(message, day_text(today() + timedelta(days=1), subgroup_of(message.chat.id), "Завтра · "))


@router.message(Command("week"))
@router.message(F.text == BTN_WEEK)
async def cmd_week(message: Message) -> None:
    await reply(message, week_text(monday_of(today()), subgroup_of(message.chat.id)))


@router.message(Command("nextweek"))
@router.message(F.text == BTN_NEXT_WEEK)
async def cmd_next_week(message: Message) -> None:
    await reply(message, week_text(monday_of(today()) + timedelta(days=7), subgroup_of(message.chat.id)))


@router.message(Command("day"))
async def cmd_day(message: Message) -> None:
    arg = (message.text or "").partition(" ")[2].strip()
    t = today()
    d = None
    for fmt in ("%d.%m.%Y", "%d.%m"):
        try:
            parsed = datetime.strptime(arg, fmt).date()
        except ValueError:
            continue
        d = parsed if fmt == "%d.%m.%Y" else parsed.replace(year=t.year)
        break
    if d is None:
        await message.answer("Укажите дату: /day <code>15.10</code> или /day <code>15.10.2026</code>")
        return
    await reply(message, day_text(d, subgroup_of(message.chat.id)))


@router.message(Command("subgroup"))
@router.message(F.text == BTN_SUBGROUP)
async def cmd_subgroup(message: Message) -> None:
    try:
        options = await client.subgroups()
    except ScheduleError as e:
        await message.answer(f"⚠️ {e}")
        return
    current = subgroup_of(message.chat.id)
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=("✅ " if o == current else "") + o, callback_data=f"sg:{o}")
                for o in options
            ]
        ]
    )
    await message.answer("Выберите подгруппу:", reply_markup=kb)


@router.callback_query(F.data.startswith("sg:"))
async def on_subgroup(callback: CallbackQuery) -> None:
    sg = callback.data.split(":", 1)[1]
    storage.update(callback.message.chat.id, subgroup=sg)
    await callback.message.edit_text(f"Подгруппа: <b>{sg}</b> ✅")
    await callback.answer()


@router.message(Command("subscribe"))
async def cmd_subscribe(message: Message) -> None:
    storage.update(message.chat.id, subscribed=True)
    await message.answer(f"🔔 Буду присылать расписание на день каждое утро в {NOTIFY_TIME}.")


@router.message(Command("unsubscribe"))
async def cmd_unsubscribe(message: Message) -> None:
    storage.update(message.chat.id, subscribed=False)
    await message.answer("🔕 Рассылка отключена.")


@router.message(F.text == BTN_NOTIFY)
async def btn_notify(message: Message) -> None:
    if storage.get(message.chat.id).get("subscribed"):
        await cmd_unsubscribe(message)
    else:
        await cmd_subscribe(message)


# --------------------------------------------------------------------------- #
# Ежедневная рассылка
# --------------------------------------------------------------------------- #


async def notifier(bot: Bot) -> None:
    hh, mm = map(int, NOTIFY_TIME.split(":"))
    while True:
        now = datetime.now(TZ)
        target = datetime.combine(now.date(), dtime(hh, mm), TZ)
        if target <= now:
            target += timedelta(days=1)
        await asyncio.sleep((target - now).total_seconds())
        d = today()
        if d.weekday() == 6:  # по воскресеньям не беспокоим
            continue
        for chat_id, settings in storage.subscribers():
            sg = settings.get("subgroup", DEFAULT_SUBGROUP)
            try:
                schedule = await client.schedule_for(d, sg)
                if schedule is None or not schedule.lessons_on(d):
                    continue
                await bot.send_message(chat_id, format_day(d, schedule, header="Доброе утро! "))
            except Exception:  # noqa: BLE001 — один сбой не должен ломать рассылку остальным
                log.exception("failed to notify %s", chat_id)
            await asyncio.sleep(0.05)


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    token = os.getenv("BOT_TOKEN")
    if not token:
        raise SystemExit("Не задана переменная окружения BOT_TOKEN")
    bot = Bot(token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    dp.include_router(router)
    await bot.set_my_commands(
        [
            BotCommand(command="today", description="Расписание на сегодня"),
            BotCommand(command="tomorrow", description="Расписание на завтра"),
            BotCommand(command="week", description="Эта неделя"),
            BotCommand(command="nextweek", description="Следующая неделя"),
            BotCommand(command="day", description="На дату: /day 15.10"),
            BotCommand(command="subgroup", description="Выбрать подгруппу"),
            BotCommand(command="subscribe", description="Ежедневная рассылка"),
            BotCommand(command="unsubscribe", description="Отключить рассылку"),
        ]
    )
    task = asyncio.create_task(notifier(bot))
    try:
        await dp.start_polling(bot)
    finally:
        task.cancel()


if __name__ == "__main__":
    asyncio.run(main())
