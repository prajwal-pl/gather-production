"""One place that assembles the object graph.

The graphs are compiled once and kept, because ``interrupt()`` resumes against a
checkpoint held by that compiled graph instance.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

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


def build_runtime(
    settings: Settings | None = None,
    *,
    log: Callable[[str], None] = print,
    force_fixture: bool = False,
) -> Runtime:
    settings = settings or load_settings()
    store = Store(settings.db_path)
    llm = build_llm(settings, log=log, force_fixture=force_fixture)
    hub = CapabilityHub(store)

    fireflies = FirefliesConnector()
    gmail = GmailConnector()
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
        )
    )
    inbox_graph = build_inbox_graph(
        InboxDeps(store=store, llm=llm, hub=hub, notifier=notifier, settings=settings)
    )

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
        org_id=store.ensure_org(settings.org_name),
    )
