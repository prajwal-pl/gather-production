"""The engine's clock.

One thread, one tick at a time. A tick polls due connections, runs the nudge
sweep when it is due, and republishes changed state as events. The scheduler
never sends anything: the sweep only creates proposals.

Design choices that matter for a desktop app:

* **One thread, non-overlapping ticks.** SQLite has one writer and double
  polling would duplicate provider work.
* **Catch-up is free.** Tickets are driven by ``connections.next_sync_at``, so
  after sleep or a laptop lid close the next tick simply processes what is due.
* **A failure never kills the loop.** The thread logs, emits ``scheduler.error``
  and waits for the next interval.
* **Cancellation is cooperative.** A tick runs as a tracked run; cancelling it
  stops at the next event boundary and leaves the cursor untouched.
"""

from __future__ import annotations

import threading
import time
from dataclasses import asdict
from datetime import date, datetime, timezone
from typing import Any, Callable

from .events import EventBus
from .runs import RunCancelled
from .runtime import Runtime
from .workers.scheduler import SweepResult, sweep


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Scheduler:
    def __init__(
        self,
        runtime: Runtime,
        events: EventBus | None = None,
        *,
        poll_interval_s: float | None = None,
        sweep_interval_s: float | None = None,
        log: Callable[[str], None] | None = None,
    ) -> None:
        self.runtime = runtime
        self.events = events or runtime.events
        self.poll_interval_s = max(
            0.05,
            float(poll_interval_s if poll_interval_s is not None else runtime.settings.poll_interval_s),
        )
        self.sweep_interval_s = max(
            0.05,
            float(sweep_interval_s if sweep_interval_s is not None else runtime.settings.sweep_interval_s),
        )
        self.log = log or (lambda _message: None)

        self._stop = threading.Event()
        self._tick_lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._started_at: str | None = None
        self._last_tick_at: str | None = None
        self._last_sweep_at: str | None = None
        self._last_poll: dict[str, Any] | None = None
        self._last_error: str | None = None
        self._ticks = 0
        self._last_sweep_monotonic = 0.0
        # Proposals seen once; prevents re-announcing the whole queue each tick.
        self._announced_proposals: set[str] = set()

    # -- lifecycle --------------------------------------------------------- #

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._started_at = _iso()
        self._thread = threading.Thread(
            target=self._loop, name="gather-scheduler", daemon=True
        )
        self._thread.start()
        self.events.emit(
            "scheduler.started",
            {"poll_interval_s": self.poll_interval_s, "sweep_interval_s": self.sweep_interval_s},
        )

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=timeout)
        self._thread = None
        self.events.emit("scheduler.stopped", {"at": _iso()})

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def status(self) -> dict[str, Any]:
        return {
            "running": self.running,
            "started_at": self._started_at,
            "last_tick_at": self._last_tick_at,
            "last_sweep_at": self._last_sweep_at,
            "poll_interval_s": self.poll_interval_s,
            "sweep_interval_s": self.sweep_interval_s,
            "ticks": self._ticks,
            "last_poll": self._last_poll,
            "last_error": self._last_error,
        }

    # -- ticks ------------------------------------------------------------- #

    def run_now(self, *, block: bool = False, force_sweep: bool = False) -> dict[str, Any]:
        """Run one tick.

        ``block=False`` is the IPC default: a UI-triggered sync should not pile
        up behind a long backfill, it should say "busy" and refresh.
        """
        if not self._tick_lock.acquire(blocking=block):
            return {"busy": True, "running": self.running}
        try:
            return self._tick(force_sweep=force_sweep)
        finally:
            self._tick_lock.release()

    def _loop(self) -> None:
        # Catch up immediately on start; otherwise a restart would wait a full
        # interval before polling anything.
        while not self._stop.is_set():
            try:
                self.run_now(block=False)
            except Exception as exc:  # noqa: BLE001 - the loop must survive
                self._last_error = str(exc)[:500]
                self.log(f"[scheduler] tick failed: {exc}")
                self.events.emit("scheduler.error", {"error": self._last_error})
            self._stop.wait(self.poll_interval_s)

    def _tick(self, *, force_sweep: bool = False) -> dict[str, Any]:
        started = time.monotonic()
        self._ticks += 1
        self._last_tick_at = _iso()

        self.events.emit("scheduler.tick", {"tick": self._ticks, "at": self._last_tick_at})
        poll_result = self._poll()
        self._last_poll = poll_result

        sweep_result: SweepResult | None = None
        if force_sweep or (started - self._last_sweep_monotonic) >= self.sweep_interval_s:
            sweep_result = self._sweep()
            self._last_sweep_monotonic = started
            self._last_sweep_at = _iso()

        self._announce_proposals()
        changed = bool(poll_result.get("events")) or bool(sweep_result and sweep_result.nudges)
        if changed:
            self.events.emit("data.changed", {"scopes": ["tasks", "proposals"]})

        return {
            "at": self._last_tick_at,
            "poll": poll_result,
            "sweep": (
                {
                    "nudges": len(sweep_result.nudges),
                    "renewals": sweep_result.renewals,
                    "errors": sweep_result.errors,
                }
                if sweep_result
                else None
            ),
        }

    def _poll(self) -> dict[str, Any]:
        runtime = self.runtime
        handle = runtime.runs.create("sync", "scheduler")
        outcomes = []
        try:
            with runtime.runs.bind(handle):
                outcomes = runtime.poll_once()
        except RunCancelled:
            runtime.runs.finish(handle, "cancelled")
            self.events.emit("sync.cancelled", {"run_id": handle.run_id, "at": _iso()})
            return {"cancelled": True, "events": 0, "outcomes": []}
        except Exception as exc:
            runtime.runs.finish(handle, "failed", error=str(exc)[:500])
            raise
        else:
            runtime.runs.finish(handle, "done")

        total = 0
        for outcome in outcomes:
            total += outcome.events
            self.events.emit(
                "sync.status",
                {
                    "provider": outcome.provider,
                    "connection_id": outcome.connection_id,
                    "state": "error" if outcome.error else "ok",
                    "events": outcome.events,
                    "duplicates": outcome.duplicates,
                    "next_sync_at": outcome.next_sync_at,
                    "error": outcome.error,
                    "at": _iso(),
                },
            )
        return {
            "events": total,
            "outcomes": [asdict(outcome) for outcome in outcomes],
        }

    def _sweep(self) -> SweepResult:
        result = sweep(
            self.runtime.store,
            self.runtime.llm,
            self.runtime.notifier,
            org_id=self.runtime.org_id,
            today=date.today(),
            deliver=False,
        )
        self.events.emit(
            "sweep.finished",
            {
                "nudges": len(result.nudges),
                "renewals": len(result.renewals),
                "errors": result.errors,
                "at": _iso(),
            },
        )
        return result

    def _announce_proposals(self) -> None:
        pending = self.runtime.store.proposals(self.runtime.org_id, status="pending")
        current = {proposal["id"] for proposal in pending}
        for proposal in pending:
            if proposal["id"] in self._announced_proposals:
                continue
            self.events.emit(
                "proposal.created",
                {
                    "proposal_id": proposal["id"],
                    "kind": proposal.get("kind"),
                    "thread_id": proposal.get("thread_id"),
                    "title": (proposal.get("payload") or {}).get("meeting"),
                    "at": _iso(),
                },
            )
        self._announced_proposals = current
