"""One place that assembles the object graph.

The graphs are compiled once and kept, because ``interrupt()`` resumes against a
checkpoint held by that compiled graph instance.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Mapping

from .checkpoints import Checkpoints
from .config import Settings, load_settings
from .connectors import CapabilityHub, FirefliesConnector, GmailConnector
from .db import Store
from .events import EventBus
from .graphs import Deps, InboxDeps, build_inbox_graph, build_meeting_graph
from .llm import BaseLLM, build_llm
from .notify import Notifier
from .runs import RunRegistry


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
    events: EventBus
    runs: RunRegistry
    keys: Mapping[str, str]
    fixture: bool = False
    log: Callable[[str], None] | None = None

    def close(self) -> None:
        self.store.close()
        self.checkpoints.close()

    def poll_once(self, *, now: datetime | None = None) -> list:
        """Run one polling tick for this workspace.

        The scheduler calls this on a timer; the CLI and the dev API call it
        once for testing. Each event is a cancellation boundary, so a human can
        stop a long backfill without killing the process.
        """
        from .poller import PollSource, process_event

        def handle(event) -> None:
            self.runs.checkpoint("event", {"provider": event.provider, "event_id": event.id})
            process_event(self, event)

        poller = PollSource(self.store, self.hub, on_event=handle, log=self.log)
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
    secrets: Mapping[str, str] | None = None,
) -> Runtime:
    """Assemble the runtime.

    ``secrets`` is the resolved credential mapping from the engine's secret
    store. ``None`` means "read the process environment" (CLI/dev/tests); an
    explicit mapping (even empty) is authoritative, which is how the bundled
    engine avoids falling back to a stray ``.env``.
    """
    settings = settings or load_settings()
    if secrets is not None:
        keys: Mapping[str, str] = dict(secrets)
    else:
        from .secrets import SECRET_KEYS

        keys = {name: os.environ[name] for name in SECRET_KEYS if os.environ.get(name)}

    store = Store(settings.db_path)
    checkpoints = Checkpoints(settings.checkpoints_path)
    events = EventBus()
    runs = RunRegistry(emit=events.emit)
    try:
        llm = build_llm(
            settings,
            log=log,
            force_fixture=force_fixture,
            api_keys=keys if secrets is not None else None,
        )
        hub = CapabilityHub(store)

        if force_fixture:
            fireflies = FirefliesConnector(api_key="")
            gmail = GmailConnector(access_token="")
        elif secrets is not None:
            fireflies = FirefliesConnector(api_key=keys.get("FIREFLIES_API_KEY", ""))
            gmail = GmailConnector(
                client_id=keys.get("GOOGLE_CLIENT_ID") or None,
                client_secret=keys.get("GOOGLE_CLIENT_SECRET") or None,
                refresh_token=keys.get("GMAIL_REFRESH_TOKEN") or None,
            )
        else:
            fireflies = FirefliesConnector()
            gmail = GmailConnector(
                client_id=settings.google_client_id or None,
                client_secret=settings.google_client_secret or None,
                refresh_token=settings.gmail_refresh_token or None,
            )
        hub.register(fireflies)
        hub.register(gmail)

        notifier = Notifier(
            settings,
            log=log,
            webhook_url=(
                "" if force_fixture else keys.get("SLACK_WEBHOOK_URL", "") if secrets is not None else None
            ),
        )

        def progress(stage: str, data: dict[str, Any]) -> None:
            runs.checkpoint(stage, data)

        meeting_graph = build_meeting_graph(
            Deps(
                store=store,
                llm=llm,
                hub=hub,
                notifier=notifier,
                fireflies=fireflies,
                settings=settings,
                progress=progress,
            ),
            checkpointer=checkpoints.saver,
        )
        inbox_graph = build_inbox_graph(
            InboxDeps(
                store=store,
                llm=llm,
                hub=hub,
                notifier=notifier,
                settings=settings,
                progress=progress,
            ),
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
        events=events,
        runs=runs,
        keys=keys,
        fixture=force_fixture,
        log=log,
    )
