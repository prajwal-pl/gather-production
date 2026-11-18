"""Connector contract + capability resolution.

A connector is transport only: verify, fetch, normalize, execute one capability.
No prompts, no orchestration, no tenancy decisions.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Mapping, Protocol

from pydantic import BaseModel, Field

from ..db import Store
from ..domain import Event, SearchHit, SyncBatch


class Capability(StrEnum):
    SEARCH_MESSAGES = "search_messages"
    READ_THREAD = "read_thread"
    GET_TRANSCRIPT = "get_transcript"
    LIST_EVENTS = "list_events"
    SEARCH_FILES = "search_files"
    SEND_MESSAGE = "send_message"


class Connection(BaseModel):
    id: str
    org_id: str
    provider: str
    mode: str = "org"
    user_id: str | None = None
    scopes: list[str] = Field(default_factory=list)
    secret_ref: str | None = None
    cursor: str | None = None
    status: str = "active"
    expires_at: str | None = None
    # Scheduling state, owned by the poller.
    last_sync_at: str | None = None
    next_sync_at: str | None = None
    backoff_until: str | None = None
    error_count: int = 0
    last_error: str | None = None


class SyncError(RuntimeError):
    """A sync failed in a way the poller should back off from."""

    def __init__(self, message: str, *, retry_after_s: float | None = None) -> None:
        super().__init__(message)
        self.retry_after_s = retry_after_s


class AuthExpired(SyncError):
    """The credential is dead; retrying will not help until the user reauths."""


class RateLimited(SyncError):
    """The provider asked us to slow down. ``retry_after_s`` may be set."""


def retry_after_seconds(raw: str | None, default: float = 60.0) -> float:
    try:
        return max(1.0, float(raw or ""))
    except (TypeError, ValueError):
        return default


class Connector(Protocol):
    provider: str
    capabilities: frozenset[Capability]
    #: How often the poller should call ``sync`` for this provider.
    poll_interval_s: int
    #: Whether the provider can push (webhook/Socket Mode). Polling works regardless.
    push_capable: bool

    def parse_webhook(self, headers: Mapping[str, str], body: bytes) -> list[Event]:
        """Normalize a provider delivery. ``org_id`` is filled in by the edge."""
        ...

    def sync(self, conn: Connection, limit: int = 50) -> SyncBatch:
        """Cursor-based pull. This is the desktop ingestion path."""
        ...

    def backfill(self, conn: Connection, since: datetime, limit: int = 50) -> SyncBatch:
        """First connect and cursor-expiry repair. Bounded by the caller."""
        ...

    def execute(self, conn: Connection, capability: Capability, args: dict[str, Any]) -> Any:
        """Implement one capability. Called by tools, never directly by a prompt."""
        ...


class CapabilityHub:
    """Answers "which credentials may perform this verb for this org/user?"."""

    def __init__(self, store: Store) -> None:
        self.store = store
        self._connectors: dict[str, Connector] = {}

    def register(self, connector: Connector) -> None:
        self._connectors[connector.provider] = connector

    def connector(self, provider: str) -> Connector | None:
        return self._connectors.get(provider)

    def providers(self) -> list[str]:
        return sorted(self._connectors)

    def active(
        self,
        org_id: str,
        capability: Capability,
        *,
        viewer: str | None = None,
        provider: str | None = None,
    ) -> list[tuple[Connection, Connector]]:
        found: list[tuple[Connection, Connector]] = []
        for row in self.store.connections(org_id, provider=provider):
            connector = self._connectors.get(row["provider"])
            if not connector or capability not in connector.capabilities:
                continue
            conn = Connection(**row)
            # A user-delegated connection only ever acts for that user.
            if conn.mode == "user" and viewer is not None and conn.user_id != viewer:
                continue
            found.append((conn, connector))
        return found

    def search_messages(
        self,
        org_id: str,
        query: str,
        *,
        viewer: str | None = None,
        provider: str | None = None,
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        hits: list[dict[str, Any]] = []
        for conn, connector in self.active(
            org_id, Capability.SEARCH_MESSAGES, viewer=viewer, provider=provider
        ):
            try:
                result = connector.execute(
                    conn, Capability.SEARCH_MESSAGES, {"query": query, "limit": limit}
                )
            except Exception:  # noqa: BLE001 - a broken provider must not kill the run
                continue
            for hit in result or []:
                hits.append(SearchHit(**hit).model_dump())
        hits.sort(key=lambda item: item.get("score", 0), reverse=True)
        return hits[:limit]

    def send_message(self, org_id: str, args: dict[str, Any], *, viewer: str | None = None) -> dict:
        for conn, connector in self.active(
            org_id, Capability.SEND_MESSAGE, viewer=viewer
        ):
            try:
                return connector.execute(conn, Capability.SEND_MESSAGE, args)
            except Exception as exc:  # noqa: BLE001
                last = exc
                continue
        return {"sent": False, "reason": "no send-capable connection"}

    def has(self, org_id: str, capability: Capability, *, viewer: str | None = None) -> bool:
        return bool(self.active(org_id, capability, viewer=viewer))
