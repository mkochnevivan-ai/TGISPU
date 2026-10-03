"""Telegram-бот с расписанием ИГЭУ (schedule.ispu.ru) для любой группы.

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
from aiogram.exceptions import TelegramBadRequest
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
from .scraper import Group, IspuClient, ScheduleError
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
NOTIFY_TIME = os.getenv("NOTIFY_TIME", "07:00")
DATA_FILE = os.getenv("DATA_FILE", "data/users.json")

BTN_TODAY = "📅 Сегодня"
BTN_TOMORROW = "➡️ Завтра"
BTN_WEEK = "🗓 Неделя"
BTN_NEXT_WEEK = "⏭ След. неделя"
BTN_GROUP = "🎓 Группа"
BTN_SUBGROUP = "👥 Подгруппа"
BTN_NOTIFY = "🔔 Рассылка"

KEYBOARD = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text=BTN_TODAY), KeyboardButton(text=BTN_TOMORROW)],
        [KeyboardButton(text=BTN_WEEK), KeyboardButton(text=BTN_NEXT_WEEK)],
        [KeyboardButton(text=BTN_GROUP), KeyboardButton(text=BTN_SUBGROUP), KeyboardButton(text=BTN_NOTIFY)],
    ],
    resize_keyboard=True,
)

client = IspuClient()
storage = UserStorage(DATA_FILE)
router = Router()


def today() -> date:
    return datetime.now(TZ).date()


def group_of(chat_id: int) -> Group | None:
    raw = storage.get(chat_id).get("group")
    return Group.from_dict(raw) if raw else None


def subgroup_of(chat_id: int) -> str | None:
    return storage.get(chat_id).get("subgroup")


def describe(chat_id: int) -> str:
    group = group_of(chat_id)
    if group is None:
        return "группа не выбрана"
    text = f"<b>{group.title}</b> ({group.faculty})"
    sg = subgroup_of(chat_id)
    if sg:
        text += f", подгруппа «{sg}»"
    return text


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


async def safe_edit(message: Message, text: str, reply_markup: InlineKeyboardMarkup | None = None) -> None:
    """edit_text, который не падает на повторное нажатие той же кнопки."""
    try:
        await message.edit_text(text, reply_markup=reply_markup)
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e):
            raise


def rows(buttons: list[InlineKeyboardButton], per_row: int) -> list[list[InlineKeyboardButton]]:
    return [buttons[i : i + per_row] for i in range(0, len(buttons), per_row)]


# --------------------------------------------------------------------------- #
# Тексты расписания
# --------------------------------------------------------------------------- #


async def day_text(d: date, group: Group, subgroup: str | None, header: str = "") -> str:
    schedule = await client.schedule_for(d, group, subgroup)
    return format_day(d, schedule, header=header)


async def week_text(monday: date, group: Group, subgroup: str | None) -> str:
    title = f"🎓 <b>Группа {group.title}</b>"
    if subgroup:
        title += f", подгруппа «{subgroup}»"
    blocks = [title]
    for i in range(7):
        d = monday + timedelta(days=i)
        schedule = await client.schedule_for(d, group, subgroup)
        # воскресенье показываем только если в нём есть пары
        if i == 6 and (schedule is None or not schedule.lessons_on(d)):
            continue
        blocks.append(format_day(d, schedule))
    return "\n\n".join(blocks)


async def send_schedule(message: Message, make_text) -> None:
    """make_text(group, subgroup) -> корутина с текстом."""
    group = group_of(message.chat.id)
    if group is None:
        await message.answer("Сначала выберите группу 👇")
        await show_faculties(message)
        return
    try:
        text = await make_text(group, subgroup_of(message.chat.id))
    except ScheduleError as e:
        log.warning("schedule error: %s", e)
        await message.answer(f"⚠️ Не удалось получить расписание: {e}")
        return
    for part in split_message(text):
        await message.answer(part, reply_markup=KEYBOARD)


# --------------------------------------------------------------------------- #
# Выбор группы и подгруппы
# --------------------------------------------------------------------------- #


async def faculties_markup() -> InlineKeyboardMarkup:
    buttons = [InlineKeyboardButton(text=name, callback_data=f"f:{fid}") for fid, name in await client.faculties()]
    return InlineKeyboardMarkup(inline_keyboard=rows(buttons, 3))


FACULTY_PROMPT = "Выберите факультет\n<i>(или просто напишите группу, например <code>1-12а</code>)</i>:"


async def show_faculties(message: Message) -> None:
    try:
        await message.answer(FACULTY_PROMPT, reply_markup=await faculties_markup())
    except ScheduleError as e:
        await message.answer(f"⚠️ {e}")


async def set_group(chat_id: int, group: Group) -> tuple[str, InlineKeyboardMarkup | None]:
    """Сохраняет группу; возвращает текст и, если нужно, клавиатуру выбора подгруппы."""
    subgroups = await client.subgroups(group)
    storage.update(chat_id, group=group.to_dict(), subgroup=subgroups[0] if len(subgroups) == 1 else None)
    text = f"✅ Группа {group.title} ({group.faculty})."
    if len(subgroups) > 1:
        return text + "\n\nТеперь выберите подгруппу:", subgroups_markup(subgroups, None)
    return text + "\n\nГотово! Жмите «📅 Сегодня».", None


def subgroups_markup(subgroups: list[str], current: str | None) -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(text=("✅ " if s == current else "") + s, callback_data=f"s:{s}") for s in subgroups
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows(buttons, 4))


@router.message(Command("group"))
@router.message(F.text == BTN_GROUP)
async def cmd_group(message: Message) -> None:
    await message.answer(f"Сейчас: {describe(message.chat.id)}", reply_markup=KEYBOARD)
    await show_faculties(message)


@router.callback_query(F.data == "menu")
async def cb_menu(callback: CallbackQuery) -> None:
    try:
        await safe_edit(callback.message, FACULTY_PROMPT, reply_markup=await faculties_markup())
    except ScheduleError as e:
        await safe_edit(callback.message, f"⚠️ {e}")
    await callback.answer()


@router.callback_query(F.data.startswith("f:"))
async def cb_faculty(callback: CallbackQuery) -> None:
    fid = callback.data.split(":")[1]
    try:
        courses = await client.courses(fid)
        name = dict(await client.faculties()).get(fid, "")
    except ScheduleError as e:
        await callback.answer(str(e), show_alert=True)
        return
    buttons = [InlineKeyboardButton(text=f"{c} курс", callback_data=f"c:{fid}:{c}") for c in courses]
    kb = rows(buttons, 3) + [[InlineKeyboardButton(text="⬅️ Назад", callback_data="menu")]]
    await safe_edit(callback.message, f"{name}: выберите курс", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))
    await callback.answer()


@router.callback_query(F.data.startswith("c:"))
async def cb_course(callback: CallbackQuery) -> None:
    _, fid, course = callback.data.split(":")
    try:
        groups = await client.groups(fid, course)
    except ScheduleError as e:
        await callback.answer(str(e), show_alert=True)
        return
    buttons = [
        InlineKeyboardButton(text=g.name, callback_data=f"g:{fid}:{course}:{g.group_id}") for g in groups
    ]
    kb = rows(buttons, 4) + [[InlineKeyboardButton(text="⬅️ Назад", callback_data=f"f:{fid}")]]
    text = f"{course} курс: выберите группу" if groups else f"На {course} курсе групп не найдено."
    await safe_edit(callback.message, text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))
    await callback.answer()


@router.callback_query(F.data.startswith("g:"))
async def cb_group(callback: CallbackQuery) -> None:
    _, fid, course, gid = callback.data.split(":")
    try:
        group = next((g for g in await client.groups(fid, course) if g.group_id == gid), None)
        if group is None:
            await callback.answer("Группа не найдена, выберите заново", show_alert=True)
            return
        text, kb = await set_group(callback.message.chat.id, group)
    except ScheduleError as e:
        await callback.answer(str(e), show_alert=True)
        return
    await safe_edit(callback.message, text, reply_markup=kb)
    await callback.answer()


@router.message(Command("subgroup"))
@router.message(F.text == BTN_SUBGROUP)
async def cmd_subgroup(message: Message) -> None:
    group = group_of(message.chat.id)
    if group is None:
        await show_faculties(message)
        return
    try:
        options = await client.subgroups(group)
    except ScheduleError as e:
        await message.answer(f"⚠️ {e}")
        return
    if len(options) <= 1:
        await message.answer(f"У группы {group.title} нет деления на подгруппы.")
        return
    await message.answer("Выберите подгруппу:", reply_markup=subgroups_markup(options, subgroup_of(message.chat.id)))


@router.callback_query(F.data.startswith("s:"))
async def cb_subgroup(callback: CallbackQuery) -> None:
    sg = callback.data.split(":", 1)[1]
    storage.update(callback.message.chat.id, subgroup=sg)
    await safe_edit(callback.message, f"✅ {describe(callback.message.chat.id)}\n\nГотово! Жмите «📅 Сегодня».")
    await callback.answer()


# --------------------------------------------------------------------------- #
# Команды расписания
# --------------------------------------------------------------------------- #


@router.message(CommandStart())
@router.message(Command("help"))
async def cmd_start(message: Message) -> None:
    await message.answer(
        "Привет! Я показываю расписание ИГЭУ с сайта schedule.ispu.ru.\n\n"
        f"Ваша группа: {describe(message.chat.id)}\n\n"
        "Команды:\n"
        "/today — на сегодня\n"
        "/tomorrow — на завтра\n"
        "/week — на эту неделю\n"
        "/nextweek — на следующую неделю\n"
        "/day <code>дд.мм</code> — на конкретную дату\n"
        "/group — сменить группу (или просто напишите, например, <code>1-12а</code>)\n"
        "/subgroup — выбрать подгруппу\n"
        f"/subscribe — присылать расписание каждый день в {NOTIFY_TIME}\n"
        "/unsubscribe — отключить рассылку",
        reply_markup=KEYBOARD,
    )
    if group_of(message.chat.id) is None:
        await show_faculties(message)


@router.message(Command("today"))
@router.message(F.text == BTN_TODAY)
async def cmd_today(message: Message) -> None:
    await send_schedule(message, lambda g, sg: day_text(today(), g, sg, "Сегодня · "))


@router.message(Command("tomorrow"))
@router.message(F.text == BTN_TOMORROW)
async def cmd_tomorrow(message: Message) -> None:
    await send_schedule(message, lambda g, sg: day_text(today() + timedelta(days=1), g, sg, "Завтра · "))


@router.message(Command("week"))
@router.message(F.text == BTN_WEEK)
async def cmd_week(message: Message) -> None:
    await send_schedule(message, lambda g, sg: week_text(monday_of(today()), g, sg))


@router.message(Command("nextweek"))
@router.message(F.text == BTN_NEXT_WEEK)
async def cmd_next_week(message: Message) -> None:
    await send_schedule(message, lambda g, sg: week_text(monday_of(today()) + timedelta(days=7), g, sg))


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
    await send_schedule(message, lambda g, sg: day_text(d, g, sg))


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


@router.message(F.chat.type == "private", F.text, ~F.text.startswith("/"))
async def text_search(message: Message) -> None:
    """Любой другой текст в личке — поиск группы по названию («1-12а», «12а»)."""
    query = message.text.strip()
    if len(query) > 20:
        await message.answer("Не понял 🤔 Напишите название группы, например <code>1-12а</code>, или /help")
        return
    await message.bot.send_chat_action(message.chat.id, "typing")
    try:
        found = await client.find_groups(query)
        if len(found) == 1:
            text, kb = await set_group(message.chat.id, found[0])
            await message.answer(text, reply_markup=kb or KEYBOARD)
            return
    except ScheduleError as e:
        await message.answer(f"⚠️ {e}")
        return
    if not found:
        await message.answer(
            f"Группа «{query}» не найдена. Напишите как на сайте, например <code>1-12а</code>, "
            "или выберите из списка: /group"
        )
        return
    buttons = [
        InlineKeyboardButton(text=f"{g.faculty} {g.title}", callback_data=f"g:{g.faculty_id}:{g.course}:{g.group_id}")
        for g in found[:30]
    ]
    await message.answer("Нашлось несколько групп, выберите:", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows(buttons, 2)))


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
            if not settings.get("group"):
                continue
            group = Group.from_dict(settings["group"])
            try:
                schedule = await client.schedule_for(d, group, settings.get("subgroup"))
                if schedule is None or not schedule.lessons_on(d):
                    continue
                await bot.send_message(chat_id, format_day(d, schedule, header="Доброе утро! "))
            except Exception:  # noqa: BLE001 — один сбой не должен ломать рассылку остальным
                log.exception("failed to notify %s", chat_id)
            await asyncio.sleep(0.05)


async def warm_up() -> None:
    """Заранее загружаем список всех групп, чтобы поиск по названию отвечал сразу."""
    try:
        groups = await client.all_groups()
        log.info("loaded %d groups", len(groups))
    except Exception:  # noqa: BLE001
        log.exception("failed to preload groups")


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
            BotCommand(command="group", description="Выбрать группу"),
            BotCommand(command="subgroup", description="Выбрать подгруппу"),
            BotCommand(command="subscribe", description="Ежедневная рассылка"),
            BotCommand(command="unsubscribe", description="Отключить рассылку"),
        ]
    )
    tasks = [asyncio.create_task(notifier(bot)), asyncio.create_task(warm_up())]
    try:
        await dp.start_polling(bot)
    finally:
        for t in tasks:
            t.cancel()


if __name__ == "__main__":
    asyncio.run(main())
