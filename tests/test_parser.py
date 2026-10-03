from datetime import date
from pathlib import Path

from ispu_bot.formatting import format_day
from ispu_bot.scraper import parse_schedule_table

FIXTURES = Path(__file__).parent / "fixtures"


def load(name):
    return parse_schedule_table((FIXTURES / name).read_text(encoding="utf-8"))


def test_header_dates_and_week():
    s = load("permanent_12a.html")
    assert s.start == date(2026, 9, 15)
    assert s.end == date(2026, 12, 21)
    assert s.covers(date(2026, 10, 5))
    assert not s.covers(date(2026, 9, 14))
    # сайт показывает 3 октября 2026 как «2 учебная неделя»
    assert s.week_number(date(2026, 10, 3)) == 2
    assert s.week_number(date(2026, 10, 5)) == 1


def test_lessons_for_day():
    s = load("permanent_12a.html")
    lessons = s.lessons_on(date(2026, 10, 5))  # понедельник, 1-я неделя
    assert [(l.pair, l.text) for l in lessons] == [
        (3, "Информат. лаб. Самышина О.В. А322а"),
        (4, "История России лек. Котлова Т.Б. Б316"),
    ]


def test_rowspan_cells_are_repeated():
    s = load("lecture_12a.html")
    # во всех слотах сетки 2 недели по 7 пар
    assert set(s.weeks) == {1, 2}
    for week in s.weeks.values():
        for lessons in week.values():
            assert all(1 <= l.pair <= 7 for l in lessons)


def test_format_day():
    s = load("permanent_12a.html")
    text = format_day(date(2026, 10, 5), s)
    assert "Понедельник, 05.10.2026" in text
    assert "<b>Информат.</b> (лаб.)" in text
    assert "1-я неделя" in text
    assert "Пар нет" in format_day(date(2026, 10, 11), s)
    assert "расписания на сайте нет" in format_day(date(2026, 10, 11), None)
