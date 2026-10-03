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
    text = format_day(date(2026, 10, 5), s, owner="1-12А · подгр. х")
    assert "Понедельник, 5 октября" in text
    assert "<b>Информатика</b> · <i>лабораторная</i>" in text
    assert "📍 А322а" in text and "👤 Самышина О.В." in text
    assert "1-12А · подгр. х · 1-я неделя" in text
    assert "Итого: 2 пары · 11:40–15:35" in text
    assert "Пар нет" in format_day(date(2026, 10, 11), s)
    assert "расписания на сайте нет" in format_day(date(2026, 10, 11), None)


def test_current_pair_is_marked():
    from datetime import datetime

    from ispu_bot.config import TZ

    s = load("permanent_12a.html")
    text = format_day(date(2026, 10, 5), s, now=datetime(2026, 10, 5, 12, 0, tzinfo=TZ))
    assert "сегодня" in text
    assert "идёт, ещё 1 ч 16 мин" in text


def test_parse_lesson():
    from ispu_bot.formatting import parse_lesson

    info = parse_lesson("( с 28.09) Кураторский час сем. Куратор А325в")
    assert (info.subject, info.kind, info.who, info.room, info.note) == ("Кураторский час", "практика", "Куратор", "А325в", "с 28.09")
    info = parse_lesson("Высш.матем. лек. Торопова Е.К. А209")
    assert info.subject == "Высшая математика" and info.who_icon == "👤"
    info = parse_lesson("Материалы ядерной техники лек. 4-11, 12, 12А В422")
    assert info.who == "4-11, 12, 12А" and info.who_icon == "👥" and info.room == "В422"
    info = parse_lesson("Физическая культура и спорт сем. XX С324")
    assert info.who == "" and info.room == "С324"


def test_format_now():
    from datetime import datetime

    from ispu_bot.config import TZ
    from ispu_bot.formatting import format_now

    s = load("permanent_12a.html")
    d = date(2026, 10, 5)
    text = format_now(datetime(2026, 10, 5, 13, 30, tzinfo=TZ), s.lessons_on(d), None)
    assert "Следующая — 4 пара в 14:00" in text and "через 31 мин" in text
    text = format_now(datetime(2026, 10, 5, 18, 0, tzinfo=TZ), s.lessons_on(d), (date(2026, 10, 6), s.lessons_on(date(2026, 10, 6))))
    assert "пары закончились" in text and "Ближайшая пара — завтра, 2 пара в 9:50" in text


def test_split_entries_keeps_parentheses():
    from ispu_bot.scraper import _split_entries

    assert _split_entries("(16.09; 30.09) Волонтёрство сем. X А1;  Ин.яз. сем. Y Б2") == [
        "(16.09; 30.09) Волонтёрство сем. X А1",
        "Ин.яз. сем. Y Б2",
    ]


def test_changed_days():
    from ispu_bot.notifications import changed_days

    old = {"1:0": ["3|A"], "2:3": ["1|B"]}
    new = {"1:0": ["3|A"], "2:3": ["1|C"], "1:5": ["2|D"]}
    assert changed_days(old, new) == [(1, 5), (2, 3)]


def test_group_query_normalization():
    from ispu_bot.scraper import Group, normalize_group_query

    assert normalize_group_query("1-12а") == ("1", "12а")
    assert normalize_group_query("1 12А") == ("1", "12а")
    assert normalize_group_query("12a") == (None, "12а")  # латинская «a»
    assert normalize_group_query("1-ТЭ-1") == ("1", "тэ-1")
    g = Group("10000", "ИФФ", "1", "101030", "12А")
    assert g.title == "1-12А"
    assert Group.from_dict(g.to_dict()) == g
