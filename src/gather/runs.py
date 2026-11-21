"""Active-run registry and cooperative cancellation.

LangGraph runs are synchronous and can take minutes (chunked extraction). We
cannot kill a Python thread safely, so cancellation is cooperative: the engine
binds a run handle to the executing thread, graph progress callbacks call
:meth:`RunRegistry.checkpoint`, and that raises :class:`RunCancelled` at the
next chunk or candidate boundary.

The registry is intentionally in-memory. After a restart there are no active
runs; the UI rebuilds from ``runs.list`` / ``tasks.list`` / ``proposals.list``.
"""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable


class RunCancelled(RuntimeError):
    """Raised inside a run after a human asked to cancel it."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class RunHandle:
    run_id: str
    kind: str
    thread_id: str
    started_at: str
    cancel: threading.Event = field(default_factory=threading.Event)
    status: str = "running"
    stage: str = "starting"
    progress: dict[str, Any] = field(default_factory=dict)
    finished_at: str | None = None
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "kind": self.kind,
            "thread_id": self.thread_id,
            "status": self.status,
            "stage": self.stage,
            "progress": self.progress,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "error": self.error,
            "cancellable": self.status == "running",
        }


class RunRegistry:
    def __init__(self, emit: Callable[[str, dict[str, Any]], Any] | None = None) -> None:
        self._emit = emit
        self._lock = threading.RLock()
        self._runs: dict[str, RunHandle] = {}
        self._local = threading.local()

    # -- lifecycle --------------------------------------------------------- #

    def create(self, kind: str, thread_id: str | None = None) -> RunHandle:
        handle = RunHandle(
            run_id=f"run_{uuid.uuid4().hex[:12]}",
            kind=kind,
            thread_id=thread_id or "",
            started_at=_now(),
        )
        with self._lock:
            self._runs[handle.run_id] = handle
        self._local.handle = handle
        self._notify("run.started", handle)
        return handle

    def finish(
        self, handle: RunHandle, status: str = "done", *, error: str | None = None
    ) -> None:
        handle.status = status
        handle.error = error
        handle.finished_at = _now()
        if getattr(self._local, "handle", None) is handle:
            self._local.handle = None
        self._notify("run.finished", handle)

    def bind(self, handle: RunHandle):
        """Context manager binding ``handle`` to the current thread."""
        return _BoundRun(self, handle)

    # -- lookup / control -------------------------------------------------- #

    def current(self) -> RunHandle | None:
        return getattr(self._local, "handle", None)

    def get(self, run_id: str) -> RunHandle | None:
        with self._lock:
            return self._runs.get(run_id)

    def list(self, *, active_only: bool = False, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            handles = list(self._runs.values())
        if active_only:
            handles = [handle for handle in handles if handle.status == "running"]
        handles.sort(key=lambda handle: handle.started_at, reverse=True)
        return [handle.as_dict() for handle in handles[:limit]]

    def cancel(self, run_id: str) -> bool:
        handle = self.get(run_id)
        if handle is None or handle.status != "running":
            return False
        handle.cancel.set()
        self._notify("run.cancelling", handle)
        return True

    def should_cancel(self) -> bool:
        handle = self.current()
        return bool(handle and handle.cancel.is_set())

    # -- progress / cancellation point ------------------------------------- #

    def checkpoint(self, stage: str, data: dict[str, Any] | None = None) -> None:
        """Called by graphs between units of work.

        No active handle (CLI, tests, HTTP dev edge) means this is a no-op, so
        the same compiled graphs work in every entry point.
        """
        handle = self.current()
        if handle is None:
            return
        if handle.cancel.is_set():
            raise RunCancelled(f"run {handle.run_id} cancelled during {stage}")
        handle.stage = stage
        handle.progress = dict(data or {})
        self._notify("run.progress", handle)

    # -- helpers ----------------------------------------------------------- #

    def run(self, kind: str, thread_id: str | None, fn: Callable[[], Any]) -> tuple[Any, RunHandle]:
        """Create, bind, execute and finish a run in one call."""
        handle = self.create(kind, thread_id)
        try:
            result = fn()
        except RunCancelled:
            self.finish(handle, "cancelled")
            raise
        except Exception as exc:
            self.finish(handle, "failed", error=str(exc)[:500])
            raise
        else:
            self.finish(handle, "done")
            return result, handle

    def _notify(self, event: str, handle: RunHandle) -> None:
        if self._emit is not None:
            self._emit(event, handle.as_dict())


class _BoundRun:
    def __init__(self, registry: RunRegistry, handle: RunHandle) -> None:
        self.registry = registry
        self.handle = handle
        self.previous: RunHandle | None = None

    def __enter__(self) -> RunHandle:
        self.previous = getattr(self.registry._local, "handle", None)
        self.registry._local.handle = self.handle
        return self.handle

    def __exit__(self, *exc: object) -> None:
        self.registry._local.handle = self.previous
