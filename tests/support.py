"""Shared test wiring. Mirrors ``build_runtime`` without live providers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from gather.checkpoints import Checkpoints
from gather.config import Settings
from gather.connectors import CapabilityHub, FirefliesConnector, GmailConnector
from gather.db import Store
from gather.domain import task_fingerprint
from gather.graphs import Deps, InboxDeps, build_inbox_graph, build_meeting_graph
from gather.llm import FixtureLLM
from gather.notify import Notifier

TODAY = date(2026, 11, 17)
SEED_TITLE = "Send updated pricing deck to Northwind"


@dataclass
class Workspace:
    settings: Settings
    store: Store
    checkpoints: Checkpoints
    org: str
    priya: str
    seeded: dict
    gmail: GmailConnector
    meeting_graph: Any
    inbox_graph: Any

    def close(self) -> None:
        self.store.close()
        self.checkpoints.close()

    def as_env(self) -> dict:
        return {
            "store": self.store,
            "org": self.org,
            "priya": self.priya,
            "seeded": self.seeded,
            "gmail": self.gmail,
            "meeting_graph": self.meeting_graph,
            "inbox_graph": self.inbox_graph,
        }


def build_workspace(settings: Settings, *, today: date = TODAY) -> Workspace:
    """The same wiring as ``build_runtime``, with fixture models and paths."""
    store = Store(settings.db_path)
    checkpoints = Checkpoints(settings.checkpoints_path)
    llm = FixtureLLM(today=today, log=lambda _: None)
    hub = CapabilityHub(store)
    fireflies = FirefliesConnector(api_key="")
    gmail = GmailConnector(access_token="")
    hub.register(fireflies)
    hub.register(gmail)
    notifier = Notifier(settings, log=lambda _: None)

    org = store.ensure_org("Acme")
    priya = store.add_user(org, "priya@acme.test", "Priya Raman")
    store.add_user(org, "marcus@acme.test", "Marcus Lee")
    store.upsert_connection(org, "fireflies", mode="org")
    store.upsert_connection(org, "gmail", mode="user", user_id=priya)

    fingerprint = task_fingerprint("priya@acme.test", SEED_TITLE)
    seeded = store.find_task_by_fingerprint(org, fingerprint)
    if seeded is None:
        seeded = store.create_task(
            org,
            title=SEED_TITLE,
            owner_user_id=priya,
            owner_email="priya@acme.test",
            due_date="2026-11-16",
            fingerprint=fingerprint,
        )

    meeting_graph = build_meeting_graph(
        Deps(
            store=store,
            llm=llm,
            hub=hub,
            notifier=notifier,
            fireflies=fireflies,
            settings=settings,
            today=today,
        ),
        checkpointer=checkpoints.saver,
    )
    inbox_graph = build_inbox_graph(
        InboxDeps(
            store=store, llm=llm, hub=hub, notifier=notifier, settings=settings, today=today
        ),
        checkpointer=checkpoints.saver,
    )

    return Workspace(
        settings=settings,
        store=store,
        checkpoints=checkpoints,
        org=org,
        priya=priya,
        seeded=seeded,
        gmail=gmail,
        meeting_graph=meeting_graph,
        inbox_graph=inbox_graph,
    )
