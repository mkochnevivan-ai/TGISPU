from __future__ import annotations

import json
import os
from pathlib import Path


class UserStorage:
    """Настройки пользователей (подгруппа, подписка) в JSON-файле."""

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

    def get(self, chat_id: int) -> dict:
        return self._data.get(str(chat_id), {})

    def update(self, chat_id: int, **values) -> None:
        self._data.setdefault(str(chat_id), {}).update(values)
        self._save()

    def subscribers(self) -> list[tuple[int, dict]]:
        return [(int(k), v) for k, v in self._data.items() if v.get("subscribed")]
