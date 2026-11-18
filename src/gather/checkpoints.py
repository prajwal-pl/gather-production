"""Durable LangGraph checkpoints.

A paused approval is user-visible state: it must survive quit, restart and an
app update. That rules out ``InMemorySaver``. LangGraph's SQLite saver writes
the graph state to ``<data-dir>/checkpoints.db``.

The saver owns one connection, opened with ``check_same_thread=False`` because
graph runs happen on worker threads. ``SqliteSaver`` serializes its own access.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from langgraph.checkpoint.sqlite import SqliteSaver


class Checkpoints:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path), check_same_thread=False, timeout=5.0)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.execute("PRAGMA busy_timeout=5000")
        self.saver = SqliteSaver(self.conn)

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "Checkpoints":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
