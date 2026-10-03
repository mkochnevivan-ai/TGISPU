"""Оформление расписания для Telegram (HTML)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from html import escape

from .scraper import PAIR_TIMES, Lesson, Schedule

WEEKDAYS = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]
WEEKDAYS_SHORT = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня",
          "июля", "августа", "сентября", "октября", "ноября", "декабря"]


def _t(s: str) -> time:
    h, m = s.split(":")
    return time(int(h), int(m))


# (начало, конец) каждой пары
PAIRS: list[tuple[time, time]] = [tuple(_t(x) for x in p.split("–")) for p in PAIR_TIMES]  # type: ignore[misc]

KINDS = {
    "лек": ("📘", "лекция"),
    "сем": ("✏️", "практика"),
    "пр": ("✏️", "практика"),
    "лаб": ("🔬", "лабораторная"),
    "к.раб": ("📐", "курсовая работа"),
    "к.пр": ("📐", "курсовой проект"),
    "конс": ("💬", "консультация"),
    "экз": ("📝", "экзамен"),
    "зач": ("✅", "зачёт"),
    "диф.зач": ("✅", "дифф. зачёт"),
}
_KIND_RE = re.compile(r"^(?P<subject>.*?)\s+(?P<kind>диф\.зач|к\.раб|к\.пр|лек|сем|лаб|пр|конс|экз|зач)\.(?:\s+(?P<rest>.*))?$")
_NOTE_RE = re.compile(r"^\(\s*([^)]*?)\s*\)\s*")
_ROOM_RE = re.compile(r"^[А-ЯA-Z]-?\d{2,4}[а-яa-z]?(\(\d+\))?$")
_GROUPS_RE = re.compile(r"^\d-")

# Расшифровка частых сокращений с сайта
ABBREVIATIONS = {
    "Высш.матем.": "Высшая математика",
    "Дискр.матем.": "Дискретная математика",
    "Ин.яз.": "Иностранный язык",
    'Ин.яз.с "0"(англ.)': "Английский язык с нуля",
    "Ин. яз (нем.)": "Немецкий язык",
    "Иностр.язык (нем)": "Немецкий язык",
    "Инж.графика": "Инженерная графика",
    "Информат.": "Информатика",
    "Выч.сист.": "Вычислительные системы",
    "Эл.машины": "Электрические машины",
    "Эл.техника": "Электротехника",
    "Прикл.мех.": "Прикладная механика",
    "Правовед.": "Правоведение",
    "Теория вероятн.и мат.статистика": "Теория вероятностей и мат. статистика",
    "Осн. яд. энергет.": "Основы ядерной энергетики",
    "Бух. учет": "Бухгалтерский учёт",
    "Орг.поведение": "Организационное поведение",
    "Теор.менеджм.": "Теория менеджмента",
    "Экон. анализ": "Экономический анализ",
    "Фин.предпр.": "Финансы предприятия",
    "Иннов.менед.": "Инновационный менеджмент",
}


@dataclass
class LessonInfo:
    subject: str
    icon: str
    kind: str
    who: str  # преподаватель (или группы — в расписании преподавателя)
    room: str
    note: str

    @property
    def who_icon(self) -> str:
        return "👥" if _GROUPS_RE.match(self.who) else "👤"


def parse_lesson(text: str) -> LessonInfo:
    note = ""
    m = _NOTE_RE.match(text)
    if m:
        note, text = m.group(1), text[m.end():]
    m = _KIND_RE.match(text)
    if not m:
        return LessonInfo(text, "•", "", "", "", note)
    subject = m["subject"].strip()
    subject = ABBREVIATIONS.get(subject, subject)
    icon, kind = KINDS[m["kind"]]
    tokens = (m["rest"] or "").split()
    room = ""
    if tokens and _ROOM_RE.match(tokens[-1]):
        room = tokens.pop()
    who = " ".join(tokens)
    if who.upper() == "XX":
        who = ""
    return LessonInfo(subject, icon, kind, who, room, note)


# --------------------------------------------------------------------------- #
# Мелкие помощники
# --------------------------------------------------------------------------- #


def plural(n: int, one: str, few: str, many: str) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def pairs_word(n: int) -> str:
    return f"{n} {plural(n, 'пара', 'пары', 'пар')}"


def duration(minutes: int) -> str:
    h, m = divmod(max(minutes, 0), 60)
    if h and m:
        return f"{h} ч {m} мин"
    if h:
        return f"{h} ч"
    return f"{m} мин"


def human_date(d: date) -> str:
    return f"{d.day} {MONTHS[d.month - 1]}"


def relative_day(d: date, today: date) -> str:
    return {0: "сегодня", 1: "завтра", -1: "вчера"}.get((d - today).days, "")


def monday_of(d: date) -> date:
    return d - timedelta(days=d.weekday())


def pair_bounds(d: date, pair: int, tz) -> tuple[datetime, datetime]:
    start, end = PAIRS[pair - 1]
    return datetime.combine(d, start, tz), datetime.combine(d, end, tz)


def by_pair(lessons: list[Lesson]) -> dict[int, list[Lesson]]:
    result: dict[int, list[Lesson]] = {}
    for lesson in lessons:
        result.setdefault(lesson.pair, []).append(lesson)
    return result


def span_text(lessons: list[Lesson]) -> str:
    pairs = sorted({l.pair for l in lessons})
    return f"{pairs_word(len(pairs))} · {PAIR_TIMES[pairs[0] - 1].split('–')[0]}–{PAIR_TIMES[pairs[-1] - 1].split('–')[1]}"


def lesson_lines(info: LessonInfo) -> list[str]:
    head = f"{info.icon} <b>{escape(info.subject)}</b>"
    if info.kind:
        head += f" · <i>{info.kind}</i>"
    lines = [head]
    details = []
    if info.room:
        details.append(f"📍 {escape(info.room)}")
    if info.who:
        details.append(f"{info.who_icon} {escape(info.who)}")
    if details:
        lines.append("    ".join(details))
    if info.note:
        lines.append(f"📌 <i>{escape(info.note)}</i>")
    return lines


def short_lesson(info: LessonInfo) -> str:
    text = f"{info.icon} {escape(info.subject)}"
    if info.room:
        text += f" · {escape(info.room)}"
    if info.who and info.who_icon == "👥":  # в расписании преподавателя показываем группы
        text += f" · {escape(info.who)}"
    return text


# --------------------------------------------------------------------------- #
# День
# --------------------------------------------------------------------------- #


def format_day(
    d: date,
    schedule: Schedule | None,
    *,
    owner: str = "",
    now: datetime | None = None,
    greeting: str = "",
) -> str:
    """owner — подпись вида «1-12А · подгр. х»; now — текущее время для отметки идущей пары."""
    today = now.date() if now else None
    rel = relative_day(d, today) if today else ""
    title = f"📅 <b>{WEEKDAYS[d.weekday()]}, {human_date(d)}</b>" + (f" · {rel}" if rel else "")
    lines = [greeting, title] if greeting else [title]

    sub = [owner] if owner else []
    if schedule is not None:
        sub.append(f"{schedule.week_number(d)}-я неделя")
    if sub:
        lines.append(f"<i>{' · '.join(sub)}</i>")

    if schedule is None:
        lines += ["", "🏖 На эту дату расписания на сайте нет — каникулы или сессия."]
        return "\n".join(lines)

    lessons = schedule.lessons_on(d)
    if not lessons:
        lines += ["", "🎉 Пар нет — можно отдыхать!"]
        return "\n".join(lines)

    for pair, items in sorted(by_pair(lessons).items()):
        status = ""
        if now and d == today:
            start, end = pair_bounds(d, pair, now.tzinfo)
            if start <= now < end:
                status = f" · ⏳ <b>идёт, ещё {duration(int((end - now).total_seconds() // 60) + 1)}</b>"
            elif now >= end:
                status = " · ✔️"
        lines += ["", f"<b>{pair} пара</b> │ <code>{PAIR_TIMES[pair - 1]}</code>{status}"]
        for i, lesson in enumerate(items):
            if i:
                lines.append("   <i>или</i>")
            lines += lesson_lines(parse_lesson(lesson.text))

    lines += ["", f"<i>Итого: {span_text(lessons)}</i>"]
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Неделя
# --------------------------------------------------------------------------- #


def format_week(days: list[tuple[date, Schedule | None]], *, owner: str = "", today: date | None = None) -> str:
    monday = days[0][0]
    sunday = monday + timedelta(days=6)
    if monday.month == sunday.month:
        period = f"{monday.day}–{sunday.day} {MONTHS[monday.month - 1]}"
    else:
        period = f"{human_date(monday)} – {human_date(sunday)}"
    week_no = next((s.week_number(d) for d, s in days if s is not None), None)

    lines = [f"🗓 <b>Неделя {period}</b>"]
    sub = [owner] if owner else []
    if week_no:
        sub.append(f"{week_no}-я неделя")
    if sub:
        lines.append(f"<i>{' · '.join(sub)}</i>")

    total = 0
    for d, schedule in days:
        lessons = schedule.lessons_on(d) if schedule else []
        if d.weekday() == 6 and not lessons:
            continue
        mark = " 👈" if today and d == today else ""
        head = f"<b>{WEEKDAYS_SHORT[d.weekday()]}, {d.day:02d}.{d.month:02d}</b>{mark}"
        if schedule is None:
            lines += ["", f"{head} — 🏖 нет расписания"]
            continue
        if not lessons:
            lines += ["", f"{head} — 🎉 пар нет"]
            continue
        lines += ["", head]
        for pair, items in sorted(by_pair(lessons).items()):
            start = PAIR_TIMES[pair - 1].split("–")[0]
            text = " <i>/</i> ".join(short_lesson(parse_lesson(l.text)) for l in items)
            lines.append(f"<code>{pair}│{start:>5}</code> {text}")
        total += len({l.pair for l in lessons})

    lines += ["", f"<i>Всего за неделю: {pairs_word(total)}</i>"]
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# «Сейчас»
# --------------------------------------------------------------------------- #


def format_now(
    now: datetime,
    today_lessons: list[Lesson],
    next_day: tuple[date, list[Lesson]] | None,
    *,
    owner: str = "",
) -> str:
    lines = [f"⏰ <b>Сейчас {now:%H:%M}</b>, {WEEKDAYS[now.weekday()].lower()}"]
    if owner:
        lines.append(f"<i>{owner}</i>")
    lines.append("")

    current = upcoming = None
    for pair, items in sorted(by_pair(today_lessons).items()):
        start, end = pair_bounds(now.date(), pair, now.tzinfo)
        if start <= now < end:
            current = (pair, items, end)
        elif now < start and upcoming is None:
            upcoming = (pair, items, start)

    def block(pair: int, items: list[Lesson]) -> list[str]:
        out = []
        for i, lesson in enumerate(items):
            if i:
                out.append("   <i>или</i>")
            out += lesson_lines(parse_lesson(lesson.text))
        return out

    if current:
        pair, items, end = current
        left = int((end - now).total_seconds() // 60) + 1
        lines.append(f"🟢 <b>Идёт {pair} пара</b> — до {end:%H:%M}, ещё {duration(left)}")
        lines += block(pair, items)
        lines.append("")
    if upcoming:
        pair, items, start = upcoming
        wait = int((start - now).total_seconds() // 60) + 1
        label = "Следующая" if current or any(l.pair < pair for l in today_lessons) else "Первая"
        lines.append(f"⏭ <b>{label} — {pair} пара в {start:%H:%M}</b> (через {duration(wait)})")
        lines += block(pair, items)
    elif not current:
        if today_lessons:
            lines.append("✅ На сегодня пары закончились. Отдыхайте!")
        else:
            lines.append("🎉 Сегодня пар нет.")
        if next_day:
            d, lessons = next_day
            first = min(l.pair for l in lessons)
            rel = relative_day(d, now.date()) or f"{WEEKDAYS[d.weekday()].lower()}, {human_date(d)}"
            lines += ["", f"⏭ <b>Ближайшая пара — {rel}, {first} пара в {PAIR_TIMES[first - 1].split('–')[0]}</b>"]
            lines += block(first, by_pair(lessons)[first])
    return "\n".join(lines).rstrip()
