"""Append-only лог событий с хэш-цепочкой (идея из MONOLITH Durable State).

Источник истины — лог; состояние — чистая свёртка (см. ledger.py). Без wall-clock:
порядок задаётся `seq`, поэтому воспроизводимость (S10) не зависит от времени.
Оборванный последний (torn) хвост допустим и обрезается; повреждение в середине —
LogCorruptError.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Optional

from .dto import canonical
from .errors import LogCorruptError

GENESIS = "0" * 64


def _hash(prev: str, seq: int, etype: str, data: Any) -> str:
    return hashlib.sha256((prev + canonical({"seq": seq, "type": etype, "data": data})).encode()).hexdigest()


class EventLog:
    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = Path(path) if path is not None else None
        self.events: list[dict[str, Any]] = []
        self.recovered_torn_tail = False
        if self.path is not None and self.path.exists():
            self._load()

    def _load(self) -> None:
        raw = self.path.read_bytes()
        lines = raw.split(b"\n")
        torn = lines[-1] != b""  # нет завершающего \n => хвост мог оборваться
        complete = lines[:-1] if not torn else lines[:-1]
        tail = lines[-1] if torn else b""
        prev = GENESIS
        good_bytes = 0
        for i, line in enumerate(complete):
            try:
                ev = json.loads(line)
                ok = (ev["seq"] == i and ev["prev"] == prev
                      and ev["hash"] == _hash(prev, ev["seq"], ev["type"], ev["data"]))
            except Exception:
                ok = False
            if not ok:
                raise LogCorruptError(f"hash chain broken at line {i}")
            prev = ev["hash"]
            self.events.append(ev)
            good_bytes += len(line) + 1
        if torn and tail:
            self.recovered_torn_tail = True
            with open(self.path, "r+b") as f:
                f.truncate(good_bytes)

    @property
    def head(self) -> str:
        return self.events[-1]["hash"] if self.events else GENESIS

    def append(self, etype: str, data: dict[str, Any]) -> dict[str, Any]:
        seq = len(self.events)
        prev = self.head
        ev = {"seq": seq, "type": etype, "data": data, "prev": prev, "hash": _hash(prev, seq, etype, data)}
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            size = self.path.stat().st_size if self.path.exists() else 0
            try:
                with open(self.path, "ab") as f:
                    f.write((canonical(ev) + "\n").encode())
                    f.flush()
                    os.fsync(f.fileno())
            except BaseException:
                if self.path.exists():
                    with open(self.path, "r+b") as f:
                        f.truncate(size)  # не оставляем частичную запись
                raise
        self.events.append(ev)
        return ev
