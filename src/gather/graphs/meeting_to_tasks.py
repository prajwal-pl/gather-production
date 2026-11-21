"""Meeting -> tasks.

    load -> extract -> resolve -> (fan out) enrich -> review [interrupt] -> persist -> notify

The shape matters more than the prompts: extraction is one narrow call per
chunk, enrichment fans out per candidate, and nothing is written to the task
table until a human (or an explicit auto-approve policy) says yes.
"""

from __future__ import annotations

import operator
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Annotated, Any, Callable, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, Send, interrupt

from ..agents.extract import (
    extract_commitments,
    is_near_duplicate,
    resolve_owner,
)
from ..connectors.base import Capability, CapabilityHub
from ..connectors.fireflies import FirefliesConnector
from ..db import Store
from ..domain import Commitment, Meeting, Person, task_fingerprint
from ..llm import BaseLLM
from ..notify import Notifier


@dataclass
class Deps:
    store: Store
    llm: BaseLLM
    hub: CapabilityHub
    notifier: Notifier
    fireflies: FirefliesConnector
    settings: Any
    today: date | None = None
    # Optional progress hook: ``(stage, data)``. The engine binds it to the
    # active run; CLI/tests leave it unset.
    progress: Callable[[str, dict[str, Any]], None] | None = None

    def day(self) -> date:
        return self.today or date.today()

    def report(self, stage: str, data: dict[str, Any] | None = None) -> None:
        if self.progress is not None:
            self.progress(stage, data or {})


class MeetingState(TypedDict, total=False):
    org_id: str
    thread_id: str
    external_id: str
    meeting_row_id: str
    title: str
    started_at: str | None
    participants: list[dict]
    transcript: str
    chunks: int
    stats: dict
    warning: str
    candidates: list[dict]
    duplicates: list[dict]
    enriched: Annotated[list[dict], operator.add]
    decision: dict
    created: list[dict]
    candidate: dict
    manual: bool


def build_meeting_graph(deps: Deps, *, checkpointer: Any):
    """Compile the graph against a durable checkpointer.

    There is deliberately no in-memory default: a pending approval that dies
    with the process is a data-loss bug, not a testing convenience.
    """
    store = deps.store

    # -- nodes ------------------------------------------------------------- #

    def load(state: MeetingState) -> dict:
        deps.report("load", {"external_id": state.get("external_id")})
        if state.get("manual") and state.get("meeting_row_id"):
            # Manual/paste ingestion already wrote the row; do not ask the
            # provider for a transcript we were given directly.
            row = store.get_meeting(state["meeting_row_id"])
            if row is None:
                raise ValueError(f"meeting {state['meeting_row_id']} not found")
            meeting = Meeting(
                id=row["id"],
                org_id=row["org_id"],
                provider=row["provider"],
                external_id=row["external_id"],
                title=row.get("title") or "Untitled meeting",
                started_at=(
                    datetime.fromisoformat(row["started_at"]) if row.get("started_at") else None
                ),
                duration_min=row.get("duration_min"),
                participants=[Person(**person) for person in row.get("participants", [])],
                transcript=row.get("transcript") or "",
                summary=row.get("summary"),
            )
            return {
                "meeting_row_id": row["id"],
                "title": meeting.title,
                "started_at": meeting.started_at.isoformat() if meeting.started_at else None,
                "participants": [person.model_dump() for person in meeting.participants],
                "transcript": meeting.transcript,
            }

        org_id, external_id = state["org_id"], state["external_id"]
        matches = deps.hub.active(org_id, Capability.GET_TRANSCRIPT, provider="fireflies")
        if matches:
            conn, connector = matches[0]
            meeting = connector.execute(
                conn, Capability.GET_TRANSCRIPT, {"external_id": external_id}
            )
        else:
            # No connection row yet (tests, first run) — go straight to transport.
            meeting = deps.fireflies.fetch_meeting(org_id, external_id)

        row_id = store.upsert_meeting(meeting)
        return {
            "meeting_row_id": row_id,
            "title": meeting.title,
            "started_at": meeting.started_at.isoformat() if meeting.started_at else None,
            "participants": [person.model_dump() for person in meeting.participants],
            "transcript": meeting.transcript,
        }

    def extract(state: MeetingState) -> dict:
        meeting = Meeting(
            id=state["meeting_row_id"],
            org_id=state["org_id"],
            provider="fireflies",
            external_id=state["external_id"],
            title=state.get("title") or "Untitled meeting",
            started_at=(
                datetime.fromisoformat(state["started_at"]) if state.get("started_at") else None
            ),
            participants=[Person(**person) for person in state.get("participants", [])],
            transcript=state.get("transcript", ""),
        )
        settings = deps.settings
        candidates, stats = extract_commitments(
            deps.llm,
            meeting,
            max_chars=settings.chunk_chars,
            max_chunks=settings.max_chunks,
            today=deps.day(),
            progress=lambda index, total: deps.report(
                "extract", {"chunk": index + 1, "total": total}
            ),
        )
        warning = ""
        if stats["truncated"]:
            warning = (
                f"transcript split into {stats['chunks']} chunks and capped; "
                "older sections were not extracted"
            )
        if stats.get("note"):
            warning = stats["note"]
        if stats["errors"]:
            warning = "; ".join(stats["errors"][:2])
        return {
            "candidates": [item.model_dump() for item in candidates],
            "chunks": stats["chunks"],
            "stats": stats,
            "warning": warning,
        }

    def resolve(state: MeetingState) -> dict:
        deps.report("resolve", {"candidates": len(state.get("candidates", []))})
        users = store.users(state["org_id"])
        open_tasks = store.open_tasks(state["org_id"])
        fresh: list[dict] = []
        duplicates: list[dict] = []

        for raw in state.get("candidates", []):
            commitment = Commitment(**raw)
            owner = resolve_owner(commitment, users)
            # External owners are real, but they are not org members: never
            # assign an internal task to someone outside the tenant.
            external_owner = None
            if owner is None:
                external_owner = (commitment.owner_email or commitment.owner_name or "").strip() or None
            owner_email = owner["email"] if owner else None
            fingerprint = task_fingerprint(owner_email or external_owner, commitment.title)

            existing = store.find_task_by_fingerprint(state["org_id"], fingerprint)
            if existing is None:
                existing = is_near_duplicate(commitment.title, owner_email, open_tasks)

            entry = {
                **commitment.model_dump(),
                "owner_user_id": owner["id"] if owner else None,
                "owner_email": owner_email,
                "external_owner": external_owner,
                "fingerprint": fingerprint,
                "duplicate_of": existing["id"] if existing else None,
            }
            if existing:
                duplicates.append({**entry, "duplicate_title": existing["title"]})
            else:
                fresh.append(entry)

        return {"candidates": fresh, "duplicates": duplicates}

    def fan_out(state: MeetingState) -> Any:
        if not state.get("candidates"):
            return END
        return [
            Send("enrich", {"org_id": state["org_id"], "candidate": candidate})
            for candidate in state["candidates"]
        ]

    def enrich(state: MeetingState) -> dict:
        candidate = dict(state["candidate"])
        deps.report("enrich", {"candidate": candidate.get("title", "")})
        query = f"{candidate['title']} {candidate.get('owner_email') or ''}".strip()
        hits = deps.hub.search_messages(state["org_id"], query, limit=3)
        candidate["context"] = [
            {
                "provider": hit["provider"],
                "subject": hit["subject"],
                "snippet": (hit.get("snippet") or "")[:200],
                "external_id": hit["external_id"],
            }
            for hit in hits
        ]

        due = candidate.get("due_date")
        priority = "normal"
        if due:
            parsed = date.fromisoformat(due)
            if parsed <= deps.day() + timedelta(days=2):
                priority = "high"
        candidate["priority"] = priority
        return {"enriched": [candidate]}

    def review(state: MeetingState) -> dict:
        deps.report("review", {"tasks": len(state.get("enriched") or [])})
        cards = state.get("enriched") or []
        proposal = store.create_proposal(
            state["org_id"],
            "task_approval",
            {
                "meeting": state.get("title"),
                "tasks": cards,
                "duplicates": state.get("duplicates", []),
                "warning": state.get("warning", ""),
            },
            thread_id=state.get("thread_id"),
        )
        if deps.settings.auto_approve:
            decision = {"approved": [card["fingerprint"] for card in cards], "edits": {}}
            store.decide_proposal(proposal["id"], "approved", "policy")
        else:
            decision = interrupt(
                {
                    "proposal_id": proposal["id"],
                    "meeting": state.get("title"),
                    "tasks": cards,
                    "duplicates": state.get("duplicates", []),
                }
            )
        return {"decision": decision if isinstance(decision, dict) else {}}

    def persist(state: MeetingState) -> dict:
        deps.report("persist", {})
        decision = state.get("decision") or {}
        approved = set(decision.get("approved") or [])
        edits: dict = decision.get("edits") or {}
        created: list[dict] = []

        for card in state.get("enriched", []):
            if card["fingerprint"] not in approved:
                continue
            if store.find_task_by_fingerprint(state["org_id"], card["fingerprint"]):
                continue  # idempotent: a retry must not create twins
            override = edits.get(card["fingerprint"], {})
            task = store.create_task(
                state["org_id"],
                title=override.get("title") or card["title"],
                detail=override.get("detail") or card.get("detail"),
                owner_user_id=card.get("owner_user_id"),
                owner_email=card.get("owner_email"),
                due_date=override.get("due_date", card.get("due_date")),
                priority=card.get("priority"),
                source_meeting_id=state.get("meeting_row_id"),
                source_quote=card.get("quote"),
                confidence=card.get("confidence"),
                fingerprint=card["fingerprint"],
            )
            store.add_task_event(
                state["org_id"],
                task["id"],
                "created",
                actor="agent",
                payload={
                    "meeting": state.get("title"),
                    "quote": card.get("quote"),
                    "context": card.get("context", []),
                    "confidence": card.get("confidence"),
                },
            )
            created.append(task)

        if state.get("meeting_row_id"):
            store.mark_meeting_processed(state["meeting_row_id"])
        return {"created": created}

    def notify(state: MeetingState) -> dict:
        for task in state.get("created", []):
            due = f" (due {task['due_date']})" if task.get("due_date") else ""
            deps.notifier.send(
                org_id=state["org_id"],
                to=task.get("owner_email"),
                subject=f"New follow-up: {task['title']}",
                body=(
                    f"You picked this up in “{state.get('title')}”{due}.\n\n"
                    f"“{task.get('source_quote') or ''}”\n\n"
                    "Reply here if it has moved or if you are blocked."
                ),
                channel="task_assigned",
            )
        return {}

    def after_extract(state: MeetingState) -> str:
        return "resolve" if state.get("candidates") else END

    # -- wiring ------------------------------------------------------------ #

    graph = StateGraph(MeetingState)
    graph.add_node("load", load)
    graph.add_node("extract", extract)
    graph.add_node("resolve", resolve)
    graph.add_node("enrich", enrich)
    graph.add_node("review", review)
    graph.add_node("persist", persist)
    graph.add_node("notify", notify)

    graph.add_edge(START, "load")
    graph.add_edge("load", "extract")
    graph.add_conditional_edges("extract", after_extract, {"resolve": "resolve", END: END})
    graph.add_conditional_edges("resolve", fan_out, {"enrich": "enrich", END: END})
    graph.add_edge("enrich", "review")
    graph.add_edge("review", "persist")
    graph.add_edge("persist", "notify")
    graph.add_edge("notify", END)

    return graph.compile(checkpointer=checkpointer)


def run_meeting(
    graph,
    *,
    org_id: str,
    external_id: str,
    thread_id: str | None = None,
    meeting_row_id: str | None = None,
    manual: bool = False,
) -> tuple[dict, str]:
    thread_id = thread_id or f"meeting-{external_id}-{uuid.uuid4().hex[:6]}"
    config = {"configurable": {"thread_id": thread_id}}
    initial: dict[str, Any] = {
        "org_id": org_id,
        "thread_id": thread_id,
        "external_id": external_id,
    }
    if meeting_row_id:
        initial["meeting_row_id"] = meeting_row_id
    if manual:
        initial["manual"] = True
    state = graph.invoke(initial, config)
    return state, thread_id


def resume_meeting(graph, thread_id: str, approved: list[str], edits: dict | None = None) -> dict:
    return graph.invoke(
        Command(resume={"approved": approved, "edits": edits or {}}),
        {"configurable": {"thread_id": thread_id}},
    )
