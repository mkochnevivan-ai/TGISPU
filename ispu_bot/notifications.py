"""Фоновые задачи: утренняя/вечерняя рассылка, напоминания перед парой, отслеживание изменений."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time as _time
from datetime import date, datetime, timedelta

from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError, TelegramNotFound
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from . import config
from .bot import client, group_of, morning_of, owner_label, state, subgroup_of, users, ymd
from .formatting import PAIR_TIMES, WEEKDAYS_SHORT, by_pair, format_day, lesson_lines, parse_lesson
from .scraper import Group, Schedule, ScheduleError

log = logging.getLogger("ispu_bot")


def day_button(d: date) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="◀ ▶ Листать", callback_data=f"d:{ymd(d)}"),
        InlineKeyboardButton(text="🗓 Неделя", callback_data=f"w:{ymd(d - timedelta(days=d.weekday()))}"),
    ]])


class Notifier:
    def __init__(self, bot: Bot) -> None:
        self.bot = bot
        self._sent: set[str] = set()  # защита от повторной отправки в ту же минуту
        self._last_check = 0.0

    async def run(self) -> None:
        while True:
            n = datetime.now(config.TZ)
            await asyncio.sleep(60 - n.second - n.microsecond / 1e6 + 0.5)
            try:
                await self.tick(datetime.now(config.TZ).replace(second=0, microsecond=0))
            except Exception:  # noqa: BLE001
                log.exception("notifier tick failed")
            if _time.monotonic() - self._last_check >= config.CHECK_INTERVAL_MIN * 60:
                self._last_check = _time.monotonic()
                try:
                    await self.check_changes()
                except Exception:  # noqa: BLE001
                    log.exception("change check failed")

    # -- отправка ------------------------------------------------------------ #

    async def send(self, chat_id: int, key: str, text: str, kb: InlineKeyboardMarkup | None = None) -> None:
        if key in self._sent:
            return
        self._sent.add(key)
        if len(self._sent) > 50_000:
            self._sent.clear()
        try:
            await self.bot.send_message(chat_id, text, reply_markup=kb)
        except (TelegramForbiddenError, TelegramNotFound):
            # пользователь заблокировал бота или чат удалён — выключаем рассылки
            users.update(chat_id, morning=None, evening=False, remind=False, changes=False, subscribed=False)
        except Exception:  # noqa: BLE001
            log.exception("failed to send to %s", chat_id)
        await asyncio.sleep(0.05)

    async def tick(self, now: datetime) -> None:
        hm = now.strftime("%H:%M")
        today = now.date()
        for chat_id, settings in users.chats():
            group = group_of(chat_id)
            if group is None:
                continue
            sg = subgroup_of(chat_id)
            try:
                if morning_of(settings) == hm and today.weekday() != 6:
                    await self.send_day(chat_id, group, sg, today, f"m{chat_id}{today}", "☀️ <b>Доброе утро!</b>")
                if settings.get("evening") and hm == config.EVENING_TIME:
                    tomorrow = today + timedelta(days=1)
                    await self.send_day(chat_id, group, sg, tomorrow, f"e{chat_id}{today}", "🌙 <b>Расписание на завтра</b>")
                if settings.get("remind"):
                    await self.remind(chat_id, group, sg, now)
            except ScheduleError as e:
                log.warning("notify %s: %s", chat_id, e)

    async def send_day(self, chat_id: int, group: Group, sg: str | None, d: date, key: str, greeting: str) -> None:
        schedule = await client.schedule_for(d, group, sg)
        if schedule is None or not schedule.lessons_on(d):
            return
        text = format_day(d, schedule, owner=owner_label(chat_id), greeting=greeting)
        await self.send(chat_id, key, text, day_button(d))

    async def remind(self, chat_id: int, group: Group, sg: str | None, now: datetime) -> None:
        target = now + timedelta(minutes=config.REMIND_MINUTES)
        if target.date() != now.date():
            return
        start = f"{target.hour}:{target.minute:02d}"
        pair = next((i + 1 for i, p in enumerate(PAIR_TIMES) if p.split("–")[0] == start), None)
        if pair is None:
            return
        schedule = await client.schedule_for(now.date(), group, sg)
        if schedule is None:
            return
        items = by_pair(schedule.lessons_on(now.date())).get(pair)
        if not items:
            return
        lines = [f"⏰ <b>Через {config.REMIND_MINUTES} минут — {pair} пара</b> <code>{PAIR_TIMES[pair - 1]}</code>", ""]
        for i, lesson in enumerate(items):
            if i:
                lines.append("   <i>или</i>")
            lines += lesson_lines(parse_lesson(lesson.text))
        await self.send(chat_id, f"r{chat_id}{now:%Y%m%d}{pair}", "\n".join(lines))

    # -- изменения расписания ---------------------------------------------- #

    async def check_changes(self) -> None:
        targets: dict[str, tuple[Group, str | None, list[int]]] = {}
        for chat_id, settings in users.chats():
            group = group_of(chat_id)
            if group is None or not settings.get("changes", True):
                continue
            sg = subgroup_of(chat_id)
            key = f"{group.key}|{sg or ''}"
            targets.setdefault(key, (group, sg, []))[2].append(chat_id)

        today = datetime.now(config.TZ).date()
        for key, (group, sg, chats) in targets.items():
            try:
                schedules = await client.get_schedules(group, sg, fresh=True)
            except ScheduleError as e:
                log.warning("change check %s: %s", key, e)
                continue
            # уже закончившиеся расписания (например, лекционное в начале семестра) не отслеживаем
            actual = [s for s in schedules if s.end is None or s.end >= today]
            new = {s.short_title: snapshot(s) for s in actual}
            old = state.get("snapshots").get(key)
            state.update("snapshots", **{key: new})
            if old is None:
                continue  # первая проверка — только запоминаем
            messages = []
            for title, snap in new.items():
                if title not in old:
                    messages.append(f"🆕 На сайте появилось: <i>{title}</i>")
                    continue
                changed = changed_days(old[title], snap)
                if changed:
                    days = ", ".join(f"{WEEKDAYS_SHORT[d]} ({w}-я нед.)" for w, d in changed)
                    messages.append(f"✏️ <i>{title}</i>\nИзменились: <b>{days}</b>")
            if not messages:
                continue
            header = f"🔄 <b>Расписание {group.title}{' · подгр. ' + sg if sg else ''} изменилось</b>"
            text = "\n\n".join([header, *messages])
            stamp = digest(new)
            for chat_id in chats:
                await self.send(chat_id, f"c{chat_id}{key}{stamp}", text, day_button(today))


def snapshot(schedule: Schedule) -> dict[str, list[str]]:
    return {
        f"{week}:{day}": [f"{l.pair}|{l.text}" for l in lessons]
        for week, days in schedule.weeks.items()
        for day, lessons in days.items()
    }


def digest(snap: dict) -> str:
    return hashlib.sha1(json.dumps(snap, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:16]


def changed_days(old: dict, new: dict) -> list[tuple[int, int]]:
    result = []
    for k in sorted(set(old) | set(new)):
        if old.get(k) != new.get(k):
            w, d = k.split(":")
            result.append((int(w), int(d)))
    return result
