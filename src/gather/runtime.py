"""One place that assembles the object graph.

The graphs are compiled once and kept, because ``interrupt()`` resumes against a
checkpoint held by that compiled graph instance.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable

from .checkpoints import Checkpoints
from .config import Settings, load_settings
from .connectors import CapabilityHub, FirefliesConnector, GmailConnector
from .db import Store
from .graphs import Deps, InboxDeps, build_inbox_graph, build_meeting_graph
from .llm import BaseLLM, build_llm
from .notify import Notifier


@dataclass
class Runtime:
    settings: Settings
    store: Store
    llm: BaseLLM
    hub: CapabilityHub
    notifier: Notifier
    fireflies: FirefliesConnector
    gmail: GmailConnector
    meeting_graph: Any
    inbox_graph: Any
    org_id: str
    checkpoints: Checkpoints

    def close(self) -> None:
        self.store.close()
        self.checkpoints.close()

    def poll_once(self, *, now: datetime | None = None) -> list:
        """Run one polling tick for this workspace.

        The scheduler thread (Phase 3) will call this on a timer; the CLI and
        the dev API call it once for testing.
        """
        from .poller import PollSource, process_event

        poller = PollSource(
            self.store,
            self.hub,
            on_event=lambda event: process_event(self, event),
        )
        return poller.run_once(self.org_id, now=now)

    def __enter__(self) -> "Runtime":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def build_runtime(
    settings: Settings | None = None,
    *,
    log: Callable[[str], None] = print,
    force_fixture: bool = False,
) -> Runtime:
    settings = settings or load_settings()
    store = Store(settings.db_path)
    checkpoints = Checkpoints(settings.checkpoints_path)
    try:
        llm = build_llm(settings, log=log, force_fixture=force_fixture)
        hub = CapabilityHub(store)

        if force_fixture:
            fireflies = FirefliesConnector(api_key="")
            gmail = GmailConnector(access_token="")
        else:
            fireflies = FirefliesConnector()
            gmail = GmailConnector(
                client_id=settings.google_client_id or None,
                client_secret=settings.google_client_secret or None,
                refresh_token=settings.gmail_refresh_token or None,
            )
        hub.register(fireflies)
        hub.register(gmail)

        notifier = Notifier(settings, log=log)
        meeting_graph = build_meeting_graph(
            Deps(
                store=store,
                llm=llm,
                hub=hub,
                notifier=notifier,
                fireflies=fireflies,
                settings=settings,
            ),
            checkpointer=checkpoints.saver,
        )
        inbox_graph = build_inbox_graph(
            InboxDeps(store=store, llm=llm, hub=hub, notifier=notifier, settings=settings),
            checkpointer=checkpoints.saver,
        )
        org_id = store.ensure_org(settings.org_name)
    except Exception:
        checkpoints.close()
        store.close()
        raise

    return Runtime(
        settings=settings,
        store=store,
        llm=llm,
        hub=hub,
        notifier=notifier,
        fireflies=fireflies,
        gmail=gmail,
        meeting_graph=meeting_graph,
        inbox_graph=inbox_graph,
        org_id=org_id,
        checkpoints=checkpoints,
    )
