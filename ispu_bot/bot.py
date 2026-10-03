"""Обработчики сообщений и кнопок."""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InlineQuery,
    InlineQueryResultArticle,
    InlineQueryResultsButton,
    InputTextMessageContent,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)

from . import config
from .formatting import WEEKDAYS_SHORT, format_day, format_now, format_week, monday_of, plural
from .scraper import Group, IspuClient, ScheduleError, Teacher
from .storage import JsonStore, UserStorage

log = logging.getLogger("ispu_bot")

client = IspuClient()
users = UserStorage(config.DATA_FILE)
state = JsonStore(config.STATE_FILE)
router = Router()

BTN_TODAY = "📅 Сегодня"
BTN_TOMORROW = "➡️ Завтра"
BTN_NOW = "⏰ Сейчас"
BTN_WEEK = "🗓 Неделя"
BTN_TEACHER = "🔎 Преподаватель"
BTN_SETTINGS = "⚙️ Настройки"

MAIN_KB = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text=BTN_TODAY), KeyboardButton(text=BTN_TOMORROW), KeyboardButton(text=BTN_NOW)],
        [KeyboardButton(text=BTN_WEEK), KeyboardButton(text=BTN_TEACHER), KeyboardButton(text=BTN_SETTINGS)],
    ],
    resize_keyboard=True,
    input_field_placeholder="Группа (1-12а) или фамилия преподавателя",
)

MAX_LEN = 4000


# --------------------------------------------------------------------------- #
# Настройки чата
# --------------------------------------------------------------------------- #


def now() -> datetime:
    return datetime.now(config.TZ)


def today() -> date:
    return now().date()


def group_of(chat_id: int) -> Group | None:
    raw = users.get(chat_id).get("group")
    return Group.from_dict(raw) if raw else None


def subgroup_of(chat_id: int) -> str | None:
    return users.get(chat_id).get("subgroup")


def morning_of(settings: dict) -> str | None:
    if "morning" in settings:
        return settings["morning"]
    # совместимость со старой настройкой «subscribed»
    return "07:00" if settings.get("subscribed") else None


def owner_label(chat_id: int) -> str:
    group = group_of(chat_id)
    if group is None:
        return ""
    sg = subgroup_of(chat_id)
    return f"{group.title} · подгр. {sg}" if sg else group.title


def describe(chat_id: int) -> str:
    group = group_of(chat_id)
    if group is None:
        return "не выбрана"
    text = f"<b>{group.title}</b> ({group.faculty})"
    sg = subgroup_of(chat_id)
    if sg:
        text += f", подгруппа «{sg}»"
    return text


# --------------------------------------------------------------------------- #
# Помощники
# --------------------------------------------------------------------------- #


def rows(buttons: list[InlineKeyboardButton], per_row: int) -> list[list[InlineKeyboardButton]]:
    return [buttons[i : i + per_row] for i in range(0, len(buttons), per_row)]


def clip(text: str) -> str:
    return text if len(text) <= MAX_LEN else text[: MAX_LEN - 20].rsplit("\n", 1)[0] + "\n…"


def ymd(d: date) -> str:
    return d.strftime("%Y%m%d")


def parse_ymd(s: str) -> date:
    return datetime.strptime(s, "%Y%m%d").date()


def shift_day(d: date, step: int) -> date:
    """Следующий/предыдущий день, пропуская воскресенье."""
    d += timedelta(days=step)
    if d.weekday() == 6:
        d += timedelta(days=step)
    return d


async def safe_edit(message: Message, text: str, reply_markup: InlineKeyboardMarkup | None = None) -> None:
    """edit_text, который не падает на повторное нажатие той же кнопки."""
    try:
        await message.edit_text(clip(text), reply_markup=reply_markup)
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e):
            raise


async def teacher_by_id(tid: str) -> Teacher | None:
    return next((t for t in await client.teachers() if t.teacher_id == tid), None)


# --------------------------------------------------------------------------- #
# Отрисовка: день / неделя (для своей группы и для преподавателя)
# --------------------------------------------------------------------------- #


def day_nav(d: date, prefix: str) -> InlineKeyboardMarkup:
    prev_d, next_d, t = shift_day(d, -1), shift_day(d, 1), today()
    row = [InlineKeyboardButton(text=f"◀ {WEEKDAYS_SHORT[prev_d.weekday()]} {prev_d:%d.%m}", callback_data=f"{prefix}d:{ymd(prev_d)}")]
    if d != t:
        row.append(InlineKeyboardButton(text="• Сегодня", callback_data=f"{prefix}d:{ymd(t)}"))
    row.append(InlineKeyboardButton(text=f"{WEEKDAYS_SHORT[next_d.weekday()]} {next_d:%d.%m} ▶", callback_data=f"{prefix}d:{ymd(next_d)}"))
    return InlineKeyboardMarkup(
        inline_keyboard=[row, [InlineKeyboardButton(text="🗓 Вся неделя", callback_data=f"{prefix}w:{ymd(monday_of(d))}")]]
    )


def week_nav(monday: date, prefix: str) -> InlineKeyboardMarkup:
    this = monday_of(today())
    row = [InlineKeyboardButton(text="◀ Пред.", callback_data=f"{prefix}w:{ymd(monday - timedelta(days=7))}")]
    if monday != this:
        row.append(InlineKeyboardButton(text="• Эта неделя", callback_data=f"{prefix}w:{ymd(this)}"))
    row.append(InlineKeyboardButton(text="След. ▶", callback_data=f"{prefix}w:{ymd(monday + timedelta(days=7))}"))
    day = today() if monday == this and today().weekday() != 6 else monday
    return InlineKeyboardMarkup(
        inline_keyboard=[row, [InlineKeyboardButton(text="📅 По дням", callback_data=f"{prefix}d:{ymd(day)}")]]
    )


async def render_day(chat_id: int, d: date) -> tuple[str, InlineKeyboardMarkup]:
    group = group_of(chat_id)
    schedule = await client.schedule_for(d, group, subgroup_of(chat_id))
    return format_day(d, schedule, owner=owner_label(chat_id), now=now()), day_nav(d, "")


async def render_week(chat_id: int, monday: date) -> tuple[str, InlineKeyboardMarkup]:
    group, sg = group_of(chat_id), subgroup_of(chat_id)
    days = []
    for i in range(7):
        d = monday + timedelta(days=i)
        days.append((d, await client.schedule_for(d, group, sg)))
    return format_week(days, owner=owner_label(chat_id), today=today()), week_nav(monday, "")


async def render_teacher_day(teacher: Teacher, d: date) -> tuple[str, InlineKeyboardMarkup]:
    schedule = await client.teacher_schedule_for(d, teacher)
    return format_day(d, schedule, owner=f"👤 {teacher.name}", now=now()), day_nav(d, f"t{teacher.teacher_id}")


async def render_teacher_week(teacher: Teacher, monday: date) -> tuple[str, InlineKeyboardMarkup]:
    days = []
    for i in range(7):
        d = monday + timedelta(days=i)
        days.append((d, await client.teacher_schedule_for(d, teacher)))
    return format_week(days, owner=f"👤 {teacher.name}", today=today()), week_nav(monday, f"t{teacher.teacher_id}")


async def render_now(chat_id: int) -> str:
    group, sg = group_of(chat_id), subgroup_of(chat_id)
    n = now()
    schedule = await client.schedule_for(n.date(), group, sg)
    today_lessons = schedule.lessons_on(n.date()) if schedule else []
    next_day = None
    for i in range(1, 15):
        d = n.date() + timedelta(days=i)
        s = await client.schedule_for(d, group, sg)
        if s and s.lessons_on(d):
            next_day = (d, s.lessons_on(d))
            break
    return format_now(n, today_lessons, next_day, owner=owner_label(chat_id))


async def need_group(message: Message) -> bool:
    if group_of(message.chat.id) is not None:
        return False
    await message.answer("Сначала выберите группу 👇", reply_markup=MAIN_KB)
    await show_faculties(message)
    return True


async def answer_rendered(message: Message, render) -> None:
    try:
        text, kb = await render
    except ScheduleError as e:
        log.warning("schedule error: %s", e)
        await message.answer(f"⚠️ Не удалось получить расписание: {e}")
        return
    await message.answer(clip(text), reply_markup=kb)


# --------------------------------------------------------------------------- #
# Команды расписания
# --------------------------------------------------------------------------- #


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    name = message.from_user.first_name if message.from_user else ""
    me = await message.bot.me()
    await message.answer(
        f"👋 <b>Привет{', ' + name if name else ''}!</b>\n\n"
        "Я — бот расписания ИГЭУ. Показываю пары любой группы и любого преподавателя "
        "прямо с сайта schedule.ispu.ru.\n\n"
        "<b>Что я умею:</b>\n"
        "📅 расписание на сегодня, завтра и любой день — листайте кнопками ◀ ▶\n"
        "⏰ «Сейчас» — какая пара идёт и сколько до конца\n"
        "🗓 вся неделя одним сообщением\n"
        "🔎 расписание преподавателя — просто напишите фамилию\n"
        "🔔 утренняя рассылка, напоминания перед парой и сообщения об изменениях\n"
        f"💬 в любом чате: наберите <code>@{me.username} 1-12а</code>\n\n"
        f"Ваша группа: {describe(message.chat.id)}",
        reply_markup=MAIN_KB,
    )
    if group_of(message.chat.id) is None:
        await show_faculties(message)


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(
        "<b>📖 Команды</b>\n\n"
        "/today — на сегодня\n"
        "/tomorrow — на завтра\n"
        "/now — какая пара сейчас и какая следующая\n"
        "/week — эта неделя\n"
        "/nextweek — следующая неделя\n"
        "/day <code>15.10</code> — на конкретную дату\n"
        "/teacher <code>Фамилия</code> — расписание преподавателя\n"
        "/group — сменить группу\n"
        "/subgroup — выбрать подгруппу\n"
        "/settings — рассылки и напоминания\n\n"
        "<b>💡 Подсказки</b>\n"
        "• Напишите название группы (<code>1-12а</code>) — я переключусь на неё.\n"
        "• Напишите фамилию (<code>Габитов</code>) — покажу расписание преподавателя.\n"
        "• Под расписанием есть кнопки ◀ ▶ для перехода по дням и неделям.\n"
        "• Добавьте меня в чат группы — расписание будет доступно всем.",
        reply_markup=MAIN_KB,
    )


@router.message(Command("today"))
@router.message(F.text == BTN_TODAY)
async def cmd_today(message: Message) -> None:
    if not await need_group(message):
        await answer_rendered(message, render_day(message.chat.id, today()))


@router.message(Command("tomorrow"))
@router.message(F.text == BTN_TOMORROW)
async def cmd_tomorrow(message: Message) -> None:
    if not await need_group(message):
        await answer_rendered(message, render_day(message.chat.id, today() + timedelta(days=1)))


@router.message(Command("week"))
@router.message(F.text == BTN_WEEK)
async def cmd_week(message: Message) -> None:
    if not await need_group(message):
        await answer_rendered(message, render_week(message.chat.id, monday_of(today())))


@router.message(Command("nextweek"))
async def cmd_next_week(message: Message) -> None:
    if not await need_group(message):
        await answer_rendered(message, render_week(message.chat.id, monday_of(today()) + timedelta(days=7)))


@router.message(Command("now"))
@router.message(F.text == BTN_NOW)
async def cmd_now(message: Message) -> None:
    if await need_group(message):
        return
    try:
        text = await render_now(message.chat.id)
    except ScheduleError as e:
        await message.answer(f"⚠️ Не удалось получить расписание: {e}")
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🔄 Обновить", callback_data="now"),
        InlineKeyboardButton(text="📅 Весь день", callback_data=f"d:{ymd(today())}"),
    ]])
    await message.answer(text, reply_markup=kb)


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
    if not await need_group(message):
        await answer_rendered(message, render_day(message.chat.id, d))


# --------------------------------------------------------------------------- #
# Кнопки навигации: d:/w: — своя группа, t<id>d:/t<id>w: — преподаватель
# --------------------------------------------------------------------------- #


@router.callback_query(F.data.regexp(r"^(t\d+)?[dw]:\d{8}$"))
async def cb_navigate(callback: CallbackQuery) -> None:
    prefix, _, value = callback.data.partition(":")
    kind, d = prefix[-1], parse_ymd(value)
    chat_id = callback.message.chat.id
    try:
        if prefix.startswith("t"):
            teacher = await teacher_by_id(prefix[1:-1])
            if teacher is None:
                await callback.answer("Преподаватель не найден", show_alert=True)
                return
            render = render_teacher_day(teacher, d) if kind == "d" else render_teacher_week(teacher, d)
        else:
            if group_of(chat_id) is None:
                await callback.answer("Сначала выберите группу: /group", show_alert=True)
                return
            render = render_day(chat_id, d) if kind == "d" else render_week(chat_id, d)
        text, kb = await render
    except ScheduleError as e:
        await callback.answer(f"⚠️ {e}", show_alert=True)
        return
    await safe_edit(callback.message, text, kb)
    await callback.answer()


@router.callback_query(F.data == "now")
async def cb_now(callback: CallbackQuery) -> None:
    try:
        text = await render_now(callback.message.chat.id)
    except ScheduleError as e:
        await callback.answer(f"⚠️ {e}", show_alert=True)
        return
    await safe_edit(callback.message, text, callback.message.reply_markup)
    await callback.answer("Обновлено")


# --------------------------------------------------------------------------- #
# Выбор группы и подгруппы
# --------------------------------------------------------------------------- #

FACULTY_PROMPT = (
    "🎓 <b>Выберите факультет</b>\n"
    "<i>или просто напишите группу, например <code>1-12а</code></i>"
)


async def faculties_markup() -> InlineKeyboardMarkup:
    buttons = [InlineKeyboardButton(text=name, callback_data=f"f:{fid}") for fid, name in await client.faculties()]
    return InlineKeyboardMarkup(inline_keyboard=rows(buttons, 3))


async def show_faculties(message: Message) -> None:
    try:
        await message.answer(FACULTY_PROMPT, reply_markup=await faculties_markup())
    except ScheduleError as e:
        await message.answer(f"⚠️ {e}")


def subgroups_markup(subgroups: list[str], current: str | None) -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(text=("✅ " if s == current else "") + s, callback_data=f"s:{s}") for s in subgroups
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows(buttons, 4))


async def set_group(chat_id: int, group: Group) -> tuple[str, InlineKeyboardMarkup | None]:
    """Сохраняет группу; возвращает текст и, если нужно, клавиатуру выбора подгруппы."""
    subgroups = await client.subgroups(group)
    users.update(chat_id, group=group.to_dict(), subgroup=subgroups[0] if len(subgroups) == 1 else None)
    text = f"✅ Группа <b>{group.title}</b> ({group.faculty})"
    if len(subgroups) > 1:
        return text + "\n\n👥 Теперь выберите подгруппу:", subgroups_markup(subgroups, None)
    return text + "\n\nГотово! Жмите «📅 Сегодня».", None


@router.message(Command("group"))
async def cmd_group(message: Message) -> None:
    await message.answer(f"Сейчас: {describe(message.chat.id)}", reply_markup=MAIN_KB)
    await show_faculties(message)


@router.callback_query(F.data == "menu")
async def cb_menu(callback: CallbackQuery) -> None:
    try:
        await safe_edit(callback.message, FACULTY_PROMPT, await faculties_markup())
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
    await safe_edit(callback.message, f"🎓 <b>{name}</b>: выберите курс", InlineKeyboardMarkup(inline_keyboard=kb))
    await callback.answer()


@router.callback_query(F.data.startswith("c:"))
async def cb_course(callback: CallbackQuery) -> None:
    _, fid, course = callback.data.split(":")
    try:
        groups = await client.groups(fid, course)
    except ScheduleError as e:
        await callback.answer(str(e), show_alert=True)
        return
    buttons = [InlineKeyboardButton(text=g.name, callback_data=f"g:{fid}:{course}:{g.group_id}") for g in groups]
    kb = rows(buttons, 4) + [[InlineKeyboardButton(text="⬅️ Назад", callback_data=f"f:{fid}")]]
    text = f"🎓 <b>{course} курс</b>: выберите группу" if groups else f"На {course} курсе групп не найдено."
    await safe_edit(callback.message, text, InlineKeyboardMarkup(inline_keyboard=kb))
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
    await safe_edit(callback.message, text, kb)
    await callback.answer()


@router.message(Command("subgroup"))
async def cmd_subgroup(message: Message) -> None:
    if not await need_group(message):
        await show_subgroups(message, edit=False)


async def show_subgroups(message: Message, edit: bool) -> None:
    group = group_of(message.chat.id)
    try:
        options = await client.subgroups(group)
    except ScheduleError as e:
        await message.answer(f"⚠️ {e}")
        return
    if len(options) <= 1:
        text, kb = f"У группы {group.title} нет деления на подгруппы.", None
    else:
        text, kb = "👥 Выберите подгруппу:", subgroups_markup(options, subgroup_of(message.chat.id))
    if edit:
        await safe_edit(message, text, kb)
    else:
        await message.answer(text, reply_markup=kb)


@router.callback_query(F.data == "sgmenu")
async def cb_subgroup_menu(callback: CallbackQuery) -> None:
    if group_of(callback.message.chat.id) is None:
        await callback.answer("Сначала выберите группу", show_alert=True)
        return
    await show_subgroups(callback.message, edit=True)
    await callback.answer()


@router.callback_query(F.data.startswith("s:"))
async def cb_subgroup(callback: CallbackQuery) -> None:
    sg = callback.data.split(":", 1)[1]
    users.update(callback.message.chat.id, subgroup=sg)
    await safe_edit(callback.message, f"✅ {describe(callback.message.chat.id)}\n\nГотово! Жмите «📅 Сегодня».")
    await callback.answer()


# --------------------------------------------------------------------------- #
# Настройки
# --------------------------------------------------------------------------- #


def onoff(v: bool) -> str:
    return "вкл ✅" if v else "выкл"


def settings_view(chat_id: int) -> tuple[str, InlineKeyboardMarkup]:
    s = users.get(chat_id)
    morning = morning_of(s)
    evening, remind, changes = s.get("evening", False), s.get("remind", False), s.get("changes", True)
    group = group_of(chat_id)
    text = (
        "⚙️ <b>Настройки</b>\n\n"
        f"🎓 Группа: {f'<b>{group.title}</b> ({group.faculty})' if group else 'не выбрана'}\n"
        f"👥 Подгруппа: <b>{subgroup_of(chat_id) or '—'}</b>\n\n"
        f"☀️ Утром — расписание на день: <b>{morning or 'выкл'}</b>\n"
        f"🌙 Вечером ({config.EVENING_TIME}) — расписание на завтра: <b>{onoff(evening)}</b>\n"
        f"⏰ Напоминание за {config.REMIND_MINUTES} мин до пары: <b>{onoff(remind)}</b>\n"
        f"🔄 Сообщать об изменениях в расписании: <b>{onoff(changes)}</b>\n\n"
        "<i>Нажимайте кнопки, чтобы переключать.</i>"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎓 Группа", callback_data="menu"),
         InlineKeyboardButton(text="👥 Подгруппа", callback_data="sgmenu")],
        [InlineKeyboardButton(text=f"☀️ Утро: {morning or 'выкл'}", callback_data="set:morning")],
        [InlineKeyboardButton(text=f"🌙 Вечер: {'вкл' if evening else 'выкл'}", callback_data="set:evening"),
         InlineKeyboardButton(text=f"⏰ Напоминания: {'вкл' if remind else 'выкл'}", callback_data="set:remind")],
        [InlineKeyboardButton(text=f"🔄 Изменения: {'вкл' if changes else 'выкл'}", callback_data="set:changes")],
    ])
    return text, kb


@router.message(Command("settings"))
@router.message(F.text == BTN_SETTINGS)
async def cmd_settings(message: Message) -> None:
    text, kb = settings_view(message.chat.id)
    await message.answer(text, reply_markup=kb)


@router.callback_query(F.data.startswith("set:"))
async def cb_settings(callback: CallbackQuery) -> None:
    chat_id = callback.message.chat.id
    what = callback.data.split(":", 1)[1]
    s = users.get(chat_id)
    if what == "morning":
        options = [None, *config.MORNING_TIMES]
        current = morning_of(s)
        nxt = options[(options.index(current) + 1) % len(options)] if current in options else options[1]
        users.update(chat_id, morning=nxt, subscribed=False)
        note = f"Утренняя рассылка: {nxt}" if nxt else "Утренняя рассылка выключена"
    elif what in ("evening", "remind", "changes"):
        default = what == "changes"
        users.update(chat_id, **{what: not s.get(what, default)})
        note = "Сохранено"
    else:
        note = ""
    text, kb = settings_view(chat_id)
    await safe_edit(callback.message, text, kb)
    await callback.answer(note)


@router.message(Command("subscribe"))
async def cmd_subscribe(message: Message) -> None:
    users.update(message.chat.id, morning=morning_of(users.get(message.chat.id)) or "07:00")
    await cmd_settings(message)


@router.message(Command("unsubscribe"))
async def cmd_unsubscribe(message: Message) -> None:
    users.update(message.chat.id, morning=None, evening=False, remind=False, subscribed=False)
    await message.answer("🔕 Все рассылки и напоминания отключены. Включить снова: /settings")


# --------------------------------------------------------------------------- #
# Преподаватели и поиск текстом
# --------------------------------------------------------------------------- #


async def show_teachers(message: Message, query: str) -> None:
    try:
        found = await client.find_teachers(query)
    except ScheduleError as e:
        await message.answer(f"⚠️ {e}")
        return
    if not found:
        await message.answer(
            f"🤷 Не нашёл «{query}».\n\nНапишите фамилию преподавателя (например, <code>Габитов</code>) "
            "или группу (например, <code>1-12а</code>)."
        )
        return
    if len(found) == 1:
        await answer_rendered(message, render_teacher_day(found[0], today()))
        return
    buttons = [InlineKeyboardButton(text=t.name, callback_data=f"t{t.teacher_id}d:{ymd(today())}") for t in found[:30]]
    await message.answer("🔎 Нашлось несколько преподавателей:", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows(buttons, 2)))


async def search_group(message: Message, query: str) -> None:
    try:
        found = await client.find_groups(query)
        if len(found) == 1:
            text, kb = await set_group(message.chat.id, found[0])
            await message.answer(text, reply_markup=kb or MAIN_KB)
            return
    except ScheduleError as e:
        await message.answer(f"⚠️ {e}")
        return
    if not found:
        await message.answer(
            f"🤷 Группа «{query}» не найдена. Напишите как на сайте, например <code>1-12а</code>, "
            "или выберите из списка: /group"
        )
        return
    buttons = [
        InlineKeyboardButton(text=f"{g.faculty} {g.title}", callback_data=f"g:{g.faculty_id}:{g.course}:{g.group_id}")
        for g in found[:30]
    ]
    await message.answer("Нашлось несколько групп, выберите:", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows(buttons, 2)))


@router.message(Command("teacher"))
async def cmd_teacher(message: Message) -> None:
    query = (message.text or "").partition(" ")[2].strip()
    if query:
        await show_teachers(message, query)
    else:
        await message.answer("🔎 Напишите фамилию преподавателя, например <code>Габитов</code>")


@router.message(F.text == BTN_TEACHER)
async def btn_teacher(message: Message) -> None:
    await message.answer("🔎 Напишите фамилию преподавателя, например <code>Габитов</code>")


@router.message(F.chat.type == "private", F.text, ~F.text.startswith("/"))
async def text_search(message: Message) -> None:
    """Любой другой текст в личке: с цифрами — группа («1-12а»), без — фамилия преподавателя."""
    query = message.text.strip()
    if len(query) > 40:
        await message.answer("Не понял 🤔 Напишите группу (<code>1-12а</code>) или фамилию преподавателя. Справка: /help")
        return
    await message.bot.send_chat_action(message.chat.id, "typing")
    if any(ch.isdigit() for ch in query):
        await search_group(message, query)
    else:
        await show_teachers(message, query)


# --------------------------------------------------------------------------- #
# Inline-режим: @бот 1-12а в любом чате
# --------------------------------------------------------------------------- #


def _article(id_: str, title: str, description: str, text: str) -> InlineQueryResultArticle:
    return InlineQueryResultArticle(
        id=id_[:64],
        title=title,
        description=description,
        input_message_content=InputTextMessageContent(message_text=clip(text), parse_mode="HTML"),
    )


@router.inline_query()
async def inline_query(query: InlineQuery) -> None:
    text = query.query.strip()
    n = now()
    d0, d1 = n.date(), n.date() + timedelta(days=1)
    monday = monday_of(d0)
    results = []

    def count(s, d) -> str:
        n = len({l.pair for l in s.lessons_on(d)}) if s else 0
        return f"{n} {plural(n, 'пара', 'пары', 'пар')}" if n else "пар нет"

    async def for_target(key: str, label: str, fetch) -> None:
        s0, s1 = await fetch(d0), await fetch(d1)
        week = [(monday + timedelta(days=i), await fetch(monday + timedelta(days=i))) for i in range(7)]
        results.extend([
            _article(f"{key}:d0", f"📅 Сегодня — {label}", count(s0, d0), format_day(d0, s0, owner=label, now=n)),
            _article(f"{key}:d1", f"➡️ Завтра — {label}", count(s1, d1), format_day(d1, s1, owner=label, now=n)),
            _article(f"{key}:w", f"🗓 Неделя — {label}", "Расписание на эту неделю", format_week(week, owner=label, today=d0)),
        ])

    try:
        if not text:
            uid = query.from_user.id
            group = group_of(uid)
            if group:
                sg = subgroup_of(uid)
                await for_target(f"g{group.key}", owner_label(uid), lambda d: client.schedule_for(d, group, sg))
        elif any(ch.isdigit() for ch in text):
            for g in (await client.find_groups(text))[:3]:
                await for_target(f"g{g.key}", g.title, lambda d, g=g: client.schedule_for(d, g))
        else:
            for t in (await client.find_teachers(text))[:3]:
                await for_target(f"t{t.teacher_id}", f"👤 {t.name}", lambda d, t=t: client.teacher_schedule_for(d, t))
    except ScheduleError as e:
        log.warning("inline error: %s", e)

    if not results:
        await query.answer(
            [], cache_time=10, is_personal=True,
            button=InlineQueryResultsButton(text="🎓 Выбрать группу в боте", start_parameter="start"),
        )
        return
    await query.answer(results, cache_time=300, is_personal=not text)
