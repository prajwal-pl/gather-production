"""In-process event bus for the desktop engine.

Events are *optimizations*, not truth. A UI must be able to rebuild its view
from the ``*.list`` RPCs alone, so a dropped or missed event is never data
loss. That rule keeps this module small: no persistence, no retries.

The sink is injected by the transport (stdio today). It must be thread-safe;
``emit`` serializes calls through a re-entrant lock so a sink can safely emit
another event.
"""

from __future__ import annotations

import threading
from collections import deque
from typing import Any, Callable

EventSink = Callable[[dict[str, Any]], None]


class EventBus:
    def __init__(self, sink: EventSink | None = None, *, history: int = 200) -> None:
        self._sink = sink
        self._lock = threading.RLock()
        self._history: deque[dict[str, Any]] = deque(maxlen=history)

    def set_sink(self, sink: EventSink | None) -> None:
        with self._lock:
            self._sink = sink

    def emit(self, event: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
        record = {"event": event, "data": dict(data or {})}
        with self._lock:
            self._history.append(record)
            sink = self._sink
            if sink is not None:
                sink(record)
        return record

    def recent(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._history)[-max(0, limit) :]
