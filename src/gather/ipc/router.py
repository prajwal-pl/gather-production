"""Method registry.

Two executors in the server: ``slow`` handlers (graph runs, provider tests,
model probes) must not starve ``tasks.list``. Marking a route is the only
scheduling decision a handler makes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

Handler = Callable[[dict[str, Any]], Any]


@dataclass(frozen=True)
class Route:
    handler: Handler
    slow: bool = False


class Router:
    def __init__(self) -> None:
        self._routes: dict[str, Route] = {}

    def register(self, method: str, handler: Handler, *, slow: bool = False) -> None:
        if not method or method in self._routes:
            raise ValueError(f"duplicate or empty method: {method!r}")
        self._routes[method] = Route(handler=handler, slow=slow)

    def route(self, method: str) -> Route | None:
        return self._routes.get(method)

    def methods(self) -> list[str]:
        return sorted(self._routes)
