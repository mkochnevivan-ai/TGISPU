from __future__ import annotations

import json
import os
from pathlib import Path


class JsonStore:
    """Простое хранилище «ключ → словарь» в JSON-файле."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._data: dict[str, dict] = {}
        if self.path.exists():
            try:
                self._data = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                self._data = {}

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)

    def get(self, key) -> dict:
        return self._data.get(str(key), {})

    def update(self, key, **values) -> None:
        self._data.setdefault(str(key), {}).update(values)
        self._save()

    def items(self) -> list[tuple[str, dict]]:
        return list(self._data.items())


class UserStorage(JsonStore):
    """Настройки чатов: группа, подгруппа, рассылки."""

    def chats(self) -> list[tuple[int, dict]]:
        return [(int(k), v) for k, v in self._data.items()]
