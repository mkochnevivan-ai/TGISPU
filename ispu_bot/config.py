"""Настройки бота из переменных окружения (и файла .env)."""

from __future__ import annotations

import logging
import os
from datetime import timedelta, timezone, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

log = logging.getLogger("ispu_bot")


def _load_dotenv(path: str = ".env") -> None:
    """Подхватывает переменные из .env, не перезаписывая уже заданные."""
    try:
        with open(path, encoding="utf-8-sig") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, _, value = line.partition("=")
                    os.environ.setdefault(key.strip(), value.strip().strip("'\""))
    except FileNotFoundError:
        pass


def _load_tz(name: str) -> tzinfo:
    # В Windows нет системной базы часовых поясов: нужен пакет tzdata.
    # Если его нет — считаем время по МСК (UTC+3).
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        log.warning("time zone %s not found (pip install tzdata), using UTC+3", name)
        return timezone(timedelta(hours=3), "MSK")


_load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
TZ = _load_tz(os.getenv("TZ_NAME", "Europe/Moscow"))
DATA_FILE = os.getenv("DATA_FILE", "data/users.json")
STATE_FILE = os.getenv("STATE_FILE", os.path.join(os.path.dirname(DATA_FILE) or ".", "state.json"))

# Варианты времени утренней рассылки (кнопка в настройках перебирает их по кругу)
MORNING_TIMES = [t.strip() for t in os.getenv("MORNING_TIMES", "06:30,07:00,07:30,08:00,09:00").split(",") if t.strip()]
EVENING_TIME = os.getenv("EVENING_TIME", "20:00")
REMIND_MINUTES = int(os.getenv("REMIND_MINUTES", "15"))
# Как часто проверять сайт на изменения расписания (минуты)
CHECK_INTERVAL_MIN = int(os.getenv("CHECK_INTERVAL_MIN", "120"))
# Обновлять ли аватарку и описание бота при запуске
SETUP_PROFILE = os.getenv("SETUP_PROFILE", "1") not in ("0", "false", "no")
