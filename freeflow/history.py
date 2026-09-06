"""Dictation history stored as JSON lines in %APPDATA%/FreeFlow/history.jsonl."""
from __future__ import annotations

import json
import logging
import os
import threading
import time

from .config import HISTORY_PATH

log = logging.getLogger(__name__)


class History:
    def __init__(self, path: str = HISTORY_PATH, max_entries: int = 1000):
        self.path = path
        self.max_entries = max_entries
        self._lock = threading.Lock()
        self._count = None

    def add(self, text: str, raw: str = "", duration: float = 0.0, engine: str = "",
            app: str = "", elapsed: float = 0.0):
        entry = {"ts": time.time(), "text": text, "raw": raw, "duration": round(duration, 2),
                 "engine": engine, "app": app, "elapsed": round(elapsed, 2)}
        with self._lock:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            if self._count is None:
                self._count = self._line_count()
            self._count += 1
            if self._count > self.max_entries * 1.5:
                self._trim()

    def _line_count(self) -> int:
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                return sum(1 for _ in f)
        except FileNotFoundError:
            return 0

    def _trim(self):
        entries = self.entries(self.max_entries)
        entries.reverse()
        with open(self.path, "w", encoding="utf-8") as f:
            for e in entries:
                f.write(json.dumps(e, ensure_ascii=False) + "\n")
        self._count = len(entries)

    def entries(self, limit: int = 200) -> list[dict]:
        """Newest first."""
        out = []
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                lines = f.readlines()
        except FileNotFoundError:
            return out
        for line in reversed(lines):
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except Exception:
                continue
            if len(out) >= limit:
                break
        return out

    def clear(self):
        with self._lock:
            try:
                os.remove(self.path)
            except FileNotFoundError:
                pass
            self._count = 0
