"""Turn a canonical event into graph work.

The poller knows nothing about meetings or mail; this is the one place that
maps event kinds to graphs. A connector that wants to join later only needs to
emit the same canonical events.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from ..connectors.base import Connection
from ..domain import Event, EventKind
from ..graphs import run_inbox, run_meeting

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ..runtime import Runtime


def process_event(runtime: "Runtime", event: Event) -> None:
    if not event.org_id:
        raise ValueError("event has no workspace id")

    if event.kind == EventKind.MEETING_COMPLETED:
        external_id = event.subject.id if event.subject else event.payload.get("meeting_id")
        if not external_id:
            raise ValueError("meeting event has no meeting id")
        external_id = str(external_id)
        run_meeting(
            runtime.meeting_graph,
            org_id=event.org_id,
            external_id=external_id,
            thread_id=f"meeting-{external_id}-{int(time.time() * 1000)}",
        )
        return

    if event.kind == EventKind.MESSAGE_RECEIVED:
        rows = runtime.store.connections(event.org_id, provider=event.provider)
        if not rows:
            raise RuntimeError(f"no active {event.provider} connection")
        conn = Connection(**rows[0])
        connector = runtime.hub.connector(event.provider)
        fetch = getattr(connector, "fetch_new", None)
        if fetch is None:
            raise NotImplementedError(f"{event.provider} cannot fetch a message")
        for message in fetch(conn, event.payload):
            message.org_id = event.org_id
            run_inbox(runtime.inbox_graph, org_id=event.org_id, message=message)
        return

    raise NotImplementedError(f"no graph for event kind {event.kind}")
