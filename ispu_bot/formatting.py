from __future__ import annotations

import re
from datetime import date, timedelta
from html import escape

from .scraper import Lesson, Schedule

WEEKDAYS = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]
PAIR_EMOJI = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣"]

_KIND_RE = re.compile(r"^(?P<subject>.*?)\s+(?P<kind>лек|сем|лаб|пр|конс|экз|зач)\.\s*(?P<rest>.*)$")
_KIND_ICON = {"лек": "📘", "сем": "✏️", "пр": "✏️", "лаб": "🔬", "конс": "💬", "экз": "📝", "зач": "📝"}


def format_lesson(lesson: Lesson) -> str:
    m = _KIND_RE.match(lesson.text)
    if m:
        icon = _KIND_ICON.get(m["kind"], "•")
        body = f"{icon} <b>{escape(m['subject'])}</b> ({m['kind']}.)"
        if m["rest"]:
            body += f"\n      {escape(m['rest'])}"
    else:
        body = f"• {escape(lesson.text)}"
    num = PAIR_EMOJI[lesson.pair - 1] if 1 <= lesson.pair <= len(PAIR_EMOJI) else f"{lesson.pair}."
    return f"{num} <code>{lesson.time}</code>\n      {body}"


def format_day(d: date, schedule: Schedule | None, *, header: str = "") -> str:
    title = f"📅 <b>{header}{WEEKDAYS[d.weekday()]}, {d:%d.%m.%Y}</b>"
    if schedule is None:
        return f"{title}\n\nНа эту дату расписания на сайте нет (каникулы или сессия)."
    week = schedule.week_number(d)
    lessons = schedule.lessons_on(d)
    lines = [f"{title}\n<i>{week}-я неделя</i>", ""]
    if not lessons:
        lines.append("Пар нет 🎉")
    else:
        lines.extend(format_lesson(l) for l in lessons)
    return "\n".join(lines)


def monday_of(d: date) -> date:
    return d - timedelta(days=d.weekday())
