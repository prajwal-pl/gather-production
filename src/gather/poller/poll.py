"""Cursor-based ingestion: the desktop replacement for webhooks.

Each tick asks which connections are due, calls the connector's ``sync``, and
turns new events into work. The cursor only advances after the events have been
handled, so a failed dispatch is retried on the next tick instead of vanishing.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable

from ..connectors.base import (
    AuthExpired,
    CapabilityHub,
    Connection,
    Connector,
    SyncError,
)
from ..db import Store
from ..domain import Event
from ..runs import RunCancelled

DEFAULT_INTERVAL_S = 300
MAX_BACKOFF_S = 30 * 60
BACKFILL_DAYS = 14


@dataclass(slots=True)
class PollOutcome:
    connection_id: str
    provider: str
    events: int = 0
    duplicates: int = 0
    cursor: str | None = None
    error: str | None = None
    next_sync_at: str | None = None


def _iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat(timespec="seconds")


class PollSource:
    def __init__(
        self,
        store: Store,
        hub: CapabilityHub,
        on_event: Callable[[Event], None] | None = None,
        *,
        clock: Callable[[], datetime] | None = None,
        jitter: Callable[[], float] | None = None,
        backfill_days: int = BACKFILL_DAYS,
        limit: int = 50,
    ) -> None:
        self.store = store
        self.hub = hub
        self.on_event = on_event
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.jitter = jitter or (lambda: random.uniform(0.85, 1.15))
        self.backfill_days = backfill_days
        self.limit = limit

    def run_once(
        self, org_id: str, *, now: datetime | None = None
    ) -> list[PollOutcome]:
        current = now or self.clock()
        outcomes: list[PollOutcome] = []
        for row in self.store.due_connections(org_id, _iso(current)):
            conn = Connection(**row)
            connector = self.hub.connector(conn.provider)
            if connector is None:
                # Misconfiguration, but keep the rest of the tick moving and
                # avoid retrying it every few seconds.
                next_at = current + timedelta(hours=1)
                self.store.mark_sync_failure(
                    conn.id,
                    error="no connector registered",
                    next_sync_at=_iso(next_at),
                    backoff_until=_iso(next_at),
                )
                outcomes.append(
                    PollOutcome(
                        connection_id=conn.id,
                        provider=conn.provider,
                        error="no connector registered",
                        next_sync_at=_iso(next_at),
                    )
                )
                continue
            outcomes.append(self.poll_connection(conn, connector, current))
        return outcomes

    def poll_connection(
        self, conn: Connection, connector: Connector, now: datetime
    ) -> PollOutcome:
        try:
            batch = connector.sync(conn, limit=self.limit)
            if batch.reset:
                # Let the connector choose its own backfill page size; Gmail
                # allows far larger pages than the incremental limit.
                batch = connector.backfill(
                    conn,
                    now - timedelta(days=self.backfill_days),
                )

            handled = 0
            duplicates = 0
            seen_this_batch: set[tuple[str, str]] = set()
            for event in batch.events:
                event.org_id = conn.org_id
                key = (event.provider, event.id)
                if key in seen_this_batch or self.store.seen_event(*key):
                    duplicates += 1
                    continue
                seen_this_batch.add(key)
                # Dispatch before recording: if it fails, the cursor stays put
                # and the event is offered again next tick.
                if self.on_event is not None:
                    self.on_event(event)
                self.store.record_event(event, event.payload)
                handled += 1

            cursor = batch.cursor if batch.cursor is not None else conn.cursor
            interval = getattr(connector, "poll_interval_s", DEFAULT_INTERVAL_S)
            delay = 5 if batch.has_more else max(1, int(interval * self.jitter()))
            finished = self.clock()
            next_at = finished + timedelta(seconds=delay)
            self.store.mark_sync_success(
                conn.id,
                cursor=cursor,
                next_sync_at=_iso(next_at),
                synced_at=_iso(finished),
            )
            return PollOutcome(
                connection_id=conn.id,
                provider=conn.provider,
                events=handled,
                duplicates=duplicates,
                cursor=cursor,
                next_sync_at=_iso(next_at),
            )
        except RunCancelled:
            # A human cancelled the run; this is not a provider failure, so do
            # not back the connection off or mark it unhealthy.
            raise
        except AuthExpired as exc:
            return self._backoff(conn, exc, now, status="reauth_required")
        except SyncError as exc:
            return self._backoff(conn, exc, now)
        except Exception as exc:  # noqa: BLE001 - one bad connection must not stop the rest
            return self._backoff(conn, exc, now)

    def _backoff(
        self,
        conn: Connection,
        exc: Exception,
        now: datetime,
        *,
        status: str | None = None,
    ) -> PollOutcome:
        attempts = max(1, conn.error_count + 1)
        base = min(MAX_BACKOFF_S, 60 * (2 ** (attempts - 1)))
        retry_after = getattr(exc, "retry_after_s", None)
        delay = max(base, float(retry_after or 0))
        next_at = now + timedelta(seconds=delay)
        message = str(exc) or exc.__class__.__name__
        self.store.mark_sync_failure(
            conn.id,
            error=message,
            next_sync_at=_iso(next_at),
            backoff_until=_iso(next_at),
            status=status,
        )
        return PollOutcome(
            connection_id=conn.id,
            provider=conn.provider,
            error=message,
            next_sync_at=_iso(next_at),
        )
