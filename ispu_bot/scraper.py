"""Загрузка и разбор расписания с сайта schedule.ispu.ru.

Сайт — ASP.NET WebForms: выбор расписания, факультета, курса, группы и
подгруппы делается postback-запросами с __VIEWSTATE. Поэтому мы повторяем
действия пользователя: GET главной страницы, затем по одному POST на каждый
выпадающий список, и разбираем итоговую таблицу #sheduleTable.
"""

from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

import aiohttp
from bs4 import BeautifulSoup, Tag

# HTTPS на сайте поддерживает только устаревшие версии TLS, поэтому ходим по HTTP.
BASE_URL = "http://schedule.ispu.ru/"

_P = "ctl00$ContentPlaceHolder1$"
F_SCHEDULE = _P + "ddlSchedule"
F_FACULTY = _P + "ddlSubDivision"
F_COURSE = _P + "ddlCorse"
F_GROUP = _P + "ddlObjectValue"
F_SUBGROUP = _P + "rblSubGroup"

PAIR_TIMES = [
    "8:00–9:35",
    "9:50–11:25",
    "11:40–13:15",
    "14:00–15:35",
    "15:50–17:25",
    "17:40–19:15",
    "19:25–21:00",
]

# Латинские буквы, которые на сайте иногда стоят вместо кириллических ("31M", "32A").
_LAT_TO_CYR = str.maketrans("ABCEHKMOPTXaceopxy", "АВСЕНКМОРТХасеорху")


class ScheduleError(Exception):
    pass


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", s).translate(_LAT_TO_CYR).lower()


@dataclass
class Lesson:
    pair: int  # 1..7
    text: str

    @property
    def time(self) -> str:
        return PAIR_TIMES[self.pair - 1] if 1 <= self.pair <= len(PAIR_TIMES) else "?"


@dataclass
class Schedule:
    title: str
    start: date | None
    end: date | None
    start_week: int  # номер недели (1 или 2), к которой относится дата начала
    # weeks[номер недели][день недели 0..6] -> список занятий
    weeks: dict[int, dict[int, list[Lesson]]] = field(default_factory=dict)

    def covers(self, d: date) -> bool:
        return (self.start is None or self.start <= d) and (self.end is None or d <= self.end)

    def week_number(self, d: date) -> int:
        if self.start is None:
            return self.start_week
        monday0 = self.start - timedelta(days=self.start.weekday())
        monday = d - timedelta(days=d.weekday())
        elapsed = (monday - monday0).days // 7
        return self.start_week if elapsed % 2 == 0 else 3 - self.start_week

    def lessons_on(self, d: date) -> list[Lesson]:
        return self.weeks.get(self.week_number(d), {}).get(d.weekday(), [])


# --------------------------------------------------------------------------- #
# Разбор HTML
# --------------------------------------------------------------------------- #

_TITLE_RE = re.compile(
    r"начало:\s*(\d{2}\.\d{2}\.\d{4})\s+\S+\s+(\d)\s+недели\s*-\s*окончание:\s*(\d{2}\.\d{2}\.\d{4})"
)


def _parse_date(s: str) -> date:
    return datetime.strptime(s, "%d.%m.%Y").date()


def _form_state(soup: BeautifulSoup) -> dict[str, str]:
    data: dict[str, str] = {}
    for inp in soup.select("input[type=hidden]"):
        if inp.get("name"):
            data[inp["name"]] = inp.get("value", "")
    for sel in soup.select("select"):
        opt = sel.select_one("option[selected]") or sel.select_one("option")
        data[sel["name"]] = opt["value"] if opt else ""
    for radio in soup.select("input[type=radio][checked]"):
        data[radio["name"]] = radio["value"]
    return data


def _options(soup: BeautifulSoup, name: str) -> list[tuple[str, str]]:
    """[(value, текст)] для select или списка радиокнопок."""
    sel = soup.find("select", attrs={"name": name})
    if sel is not None:
        return [(o["value"], o.get_text(strip=True)) for o in sel.find_all("option")]
    result = []
    for radio in soup.find_all("input", attrs={"type": "radio", "name": name}):
        label = soup.find("label", attrs={"for": radio.get("id")})
        result.append((radio["value"], label.get_text(strip=True) if label else ""))
    return result


def _split_entries(text: str) -> list[str]:
    return [p.strip() for p in text.split(";") if p.strip()]


def parse_schedule_table(html: str) -> Schedule:
    soup = BeautifulSoup(html, "html.parser")
    return _parse_schedule(soup)


def _parse_schedule(soup: BeautifulSoup) -> Schedule:
    title_span = soup.select_one("div[style*='text-align: center'] span")
    title = title_span.get_text(" ", strip=True) if title_span else ""
    start = end = None
    start_week = 1
    m = _TITLE_RE.search(title)
    if m:
        start, start_week, end = _parse_date(m.group(1)), int(m.group(2)), _parse_date(m.group(3))

    table = soup.find("table", id="sheduleTable")
    if table is None:
        raise ScheduleError("на странице нет таблицы расписания")

    weeks: dict[int, dict[int, list[Lesson]]] = {}
    pending = [0] * 7  # сколько ещё строк занимает ячейка с rowspan в каждом столбце
    carried = [""] * 7  # текст этой ячейки
    week = 0
    pair = 0

    for tr in table.find_all("tr"):
        if "caption" in (tr.get("class") or []):
            continue
        cells: list[Tag] = tr.find_all("td", recursive=False)
        if not cells:
            continue
        if "caption" in (cells[0].get("class") or []) and cells[0].get("rowspan") and cells[0].get_text(strip=True).isdigit():
            week = int(cells[0].get_text(strip=True))
            pair = 0
            cells = cells[1:]
        # ячейка со временем пары
        cells = cells[1:]
        pair += 1
        days = weeks.setdefault(week, {d: [] for d in range(7)})

        it = iter(cells)
        for col in range(7):
            if pending[col] > 0:
                pending[col] -= 1
                text = carried[col]
            else:
                td = next(it, None)
                if td is None:
                    continue
                text = td.get_text(" ", strip=True)
                span = int(td.get("rowspan") or 1)
                pending[col] = span - 1
                carried[col] = text
            for entry in _split_entries(text):
                days[col].append(Lesson(pair=pair, text=entry))

    return Schedule(title=title, start=start, end=end, start_week=start_week, weeks=weeks)


# --------------------------------------------------------------------------- #
# Загрузка
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Group:
    faculty_id: str
    faculty: str
    course: str
    group_id: str
    name: str

    @property
    def title(self) -> str:
        return f"{self.course}-{self.name}"

    @property
    def key(self) -> str:
        return f"{self.faculty_id}:{self.course}:{self.group_id}"

    def to_dict(self) -> dict:
        return {
            "faculty_id": self.faculty_id,
            "faculty": self.faculty,
            "course": self.course,
            "group_id": self.group_id,
            "name": self.name,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Group":
        return cls(d["faculty_id"], d["faculty"], d["course"], d["group_id"], d["name"])


def normalize_group_query(text: str) -> tuple[str | None, str]:
    """«1-12а», «1 12А», «12а» -> (курс или None, нормализованное имя группы)."""
    t = text.strip().replace("—", "-").replace("–", "-")
    m = re.fullmatch(r"(\d)\s*[-\s]\s*(\S+)", t)
    if m:
        return m.group(1), _norm(m.group(2))
    return None, _norm(t)


class IspuClient:
    def __init__(self, cache_ttl: int = 1800, list_ttl: int = 6 * 3600, timeout: int = 30) -> None:
        self.cache_ttl = cache_ttl
        self.list_ttl = list_ttl
        self.timeout = aiohttp.ClientTimeout(total=timeout)
        self._cache: dict[str, tuple[float, object]] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    # -- публичное API ------------------------------------------------------ #

    async def faculties(self) -> list[tuple[str, str]]:
        async def load():
            async with self._session() as http:
                return _options(await self._get(http), F_FACULTY)

        return await self._cached("faculties", self.list_ttl, load)

    async def courses(self, faculty_id: str) -> list[str]:
        async def load():
            async with self._session() as http:
                soup = await self._choose(http, await self._get(http), F_FACULTY, faculty_id, "Факультет")
                return [v for v, _ in _options(soup, F_COURSE)]

        return await self._cached(f"courses:{faculty_id}", self.list_ttl, load)

    async def groups(self, faculty_id: str, course: str) -> list[Group]:
        async def load():
            async with self._session() as http:
                soup = await self._get(http)
                faculty = dict(_options(soup, F_FACULTY)).get(faculty_id, faculty_id)
                soup = await self._choose(http, soup, F_FACULTY, faculty_id, "Факультет")
                soup = await self._choose(http, soup, F_COURSE, course, "Курс")
                return [Group(faculty_id, faculty, course, v, t) for v, t in _options(soup, F_GROUP)]

        return await self._cached(f"groups:{faculty_id}:{course}", self.list_ttl, load)

    async def all_groups(self) -> list[Group]:
        """Все группы всех факультетов (для поиска по названию)."""

        async def load_faculty(faculty_id: str, faculty: str) -> list[Group]:
            # одна сессия на факультет: GET + выбор факультета + по запросу на курс
            async with self._session() as http:
                fsoup = await self._choose(http, await self._get(http), F_FACULTY, faculty_id, "Факультет")
                found: list[Group] = []
                for course, _ in _options(fsoup, F_COURSE):
                    soup = await self._choose(http, fsoup, F_COURSE, course, "Курс")
                    found += [Group(faculty_id, faculty, course, v, t) for v, t in _options(soup, F_GROUP)]
                return found

        async def load():
            parts = await asyncio.gather(*(load_faculty(fid, name) for fid, name in await self.faculties()))
            return [g for part in parts for g in part]

        return await self._cached("all_groups", self.list_ttl, load)

    async def find_groups(self, query: str) -> list[Group]:
        course, name = normalize_group_query(query)
        return [
            g for g in await self.all_groups()
            if _norm(g.name) == name and (course is None or g.course == course)
        ]

    async def subgroups(self, group: Group) -> list[str]:
        async def load():
            async with self._session() as http:
                soup = await self._select_group(http, await self._get(http), group, None)
                return [label for _, label in _options(soup, F_SUBGROUP)]

        return await self._cached(f"subgroups:{group.key}", self.list_ttl, load)

    async def get_schedules(self, group: Group, subgroup: str | None = None) -> list[Schedule]:
        return await self._cached(
            f"schedule:{group.key}:{_norm(subgroup or '')}",
            self.cache_ttl,
            lambda: self._fetch_all(group, subgroup),
        )

    async def schedule_for(self, d: date, group: Group, subgroup: str | None = None) -> Schedule | None:
        for s in await self.get_schedules(group, subgroup):
            if s.covers(d):
                return s
        return None

    # -- внутреннее --------------------------------------------------------- #

    def _session(self) -> aiohttp.ClientSession:
        return aiohttp.ClientSession(timeout=self.timeout)

    async def _cached(self, key: str, ttl: int, loader):
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            cached = self._cache.get(key)
            if cached and time.monotonic() - cached[0] < ttl:
                return cached[1]
            try:
                value = await loader()
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                if cached:  # сайт недоступен — отдаём устаревшие данные
                    return cached[1]
                raise ScheduleError(f"сайт расписания недоступен ({e.__class__.__name__})") from e
            self._cache[key] = (time.monotonic(), value)
            return value

    async def _get(self, http: aiohttp.ClientSession) -> BeautifulSoup:
        async with http.get(BASE_URL) as r:
            r.raise_for_status()
            return BeautifulSoup(await r.text(), "html.parser")

    async def _postback(
        self, http: aiohttp.ClientSession, soup: BeautifulSoup, name: str, value: str, target: str | None = None
    ) -> BeautifulSoup:
        data = _form_state(soup)
        data[name] = value
        data["__EVENTTARGET"] = target or name
        data["__EVENTARGUMENT"] = ""
        async with http.post(BASE_URL, data=data) as r:
            r.raise_for_status()
            return BeautifulSoup(await r.text(), "html.parser")

    async def _choose(self, http, soup, name: str, value: str, what: str) -> BeautifulSoup:
        if value not in {v for v, _ in _options(soup, name)}:
            raise ScheduleError(f"{what} не найден(а) на сайте — возможно, список изменился. Выберите группу заново: /group")
        if _form_state(soup).get(name) == value:
            return soup
        return await self._postback(http, soup, name, value)

    async def _select_group(self, http, soup: BeautifulSoup, group: Group, subgroup: str | None) -> BeautifulSoup:
        soup = await self._choose(http, soup, F_FACULTY, group.faculty_id, "Факультет")
        soup = await self._choose(http, soup, F_COURSE, group.course, "Курс")
        soup = await self._choose(http, soup, F_GROUP, group.group_id, "Группа")
        if subgroup:
            opts = _options(soup, F_SUBGROUP)
            for idx, (value, text) in enumerate(opts):
                if _norm(text) == _norm(subgroup):
                    if _form_state(soup).get(F_SUBGROUP) != value:
                        soup = await self._postback(http, soup, F_SUBGROUP, value, f"{F_SUBGROUP}${idx}")
                    break
            else:
                raise ScheduleError(
                    f"подгруппа «{subgroup}» не найдена. Доступно: {', '.join(t for _, t in opts)}. "
                    "Выберите заново: /subgroup"
                )
        return soup

    async def _fetch_all(self, group: Group, subgroup: str | None) -> list[Schedule]:
        result = []
        async with self._session() as http:
            first = await self._get(http)
            for value, _ in _options(first, F_SCHEDULE):
                soup = first
                if _form_state(soup).get(F_SCHEDULE) != value:
                    soup = await self._postback(http, soup, F_SCHEDULE, value)
                soup = await self._select_group(http, soup, group, subgroup)
                try:
                    result.append(_parse_schedule(soup))
                except ScheduleError:
                    continue
        if not result:
            raise ScheduleError("на сайте нет расписания для этой группы")
        return result
