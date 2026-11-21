"""Inbound message -> task update.

    load -> match -> classify -> apply

Matching is deterministic first (sender + title overlap, later thread id), then
the model only answers one question: does this message change the task?
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Any, Callable, TypedDict

from langgraph.graph import END, START, StateGraph

from ..agents.classify import classify_reply
from ..connectors.base import CapabilityHub
from ..db import Store
from ..domain import Message
from ..llm import BaseLLM, LLMError
from ..notify import Notifier

_MATCH_THRESHOLD = 0.5
_STOP = {"the", "and", "for", "with", "from", "that", "this", "re", "you", "are", "our"}


@dataclass
class InboxDeps:
    store: Store
    llm: BaseLLM
    hub: CapabilityHub
    notifier: Notifier
    settings: Any
    today: date | None = None
    progress: Callable[[str, dict[str, Any]], None] | None = None

    def day(self) -> date:
        return self.today or date.today()

    def report(self, stage: str, data: dict[str, Any] | None = None) -> None:
        if self.progress is not None:
            self.progress(stage, data or {})


class InboxState(TypedDict, total=False):
    org_id: str
    message: dict
    message_id: str
    task: dict | None
    match_score: float
    match_method: str
    intent: dict
    applied: str
    proposal_id: str


def _tokens(text: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9]{3,}", text.lower())
        if token not in _STOP
    }


def build_inbox_graph(deps: InboxDeps, *, checkpointer: Any):
    store = deps.store

    def load(state: InboxState) -> dict:
        deps.report("load", {"provider": state["message"].get("provider")})
        message = Message(**state["message"])
        message_id = store.save_message(message)
        return {"message_id": message_id}

    def match(state: InboxState) -> dict:
        deps.report("match", {})
        message = Message(**state["message"])
        body_tokens = _tokens(f"{message.subject} {message.body_text[:800]}")
        best: dict | None = None
        best_score = 0.0
        best_method = ""

        for task in store.open_tasks(state["org_id"]):
            score = 0.0
            method = []
            if (task.get("owner_email") or "").lower() == message.from_email.lower():
                score += 0.45
                method.append("sender=owner")

            title_tokens = _tokens(task["title"])
            if title_tokens:
                overlap = len(title_tokens & body_tokens) / len(title_tokens)
                score += 0.5 * overlap
                if overlap:
                    method.append(f"title-overlap={overlap:.2f}")

            linked_threads = {
                row.get("thread_id") for row in store.messages_for_task(task["id"])
            }
            if message.thread_id in linked_threads:
                score += 0.4
                method.append("thread-match")

            if score > best_score:
                best, best_score, best_method = task, score, "+".join(method)

        if best_score < _MATCH_THRESHOLD:
            return {"task": None, "match_score": best_score, "match_method": "no-match"}
        return {"task": best, "match_score": best_score, "match_method": best_method}

    def classify(state: InboxState) -> dict:
        task = state.get("task")
        if not task:
            return {
                "intent": {
                    "intent": "unrelated",
                    "summary": "No open task matched this message.",
                    "confidence": 0.0,
                }
            }
        message = Message(**state["message"])
        deps.report("classify", {"task": task["id"]})
        try:
            intent = classify_reply(deps.llm, task=task, message=message, today=deps.day())
        except (LLMError, ValueError) as exc:
            return {
                "intent": {
                    "intent": "needs_review",
                    "summary": f"classifier unavailable: {exc}",
                    "confidence": 0.0,
                }
            }

        store.link_message(
            state["org_id"],
            state["message_id"],
            task["id"],
            confidence=state.get("match_score", 0.0),
            method=state.get("match_method", ""),
        )
        return {"intent": intent.model_dump()}

    def apply(state: InboxState) -> dict:
        deps.report("apply", {})
        task = state.get("task")
        intent = state.get("intent") or {}
        if not task:
            return {"applied": "ignored: no matching task"}

        kind = intent.get("intent")
        confidence = float(intent.get("confidence") or 0)
        org_id = state["org_id"]

        if kind == "done" and confidence >= 0.7:
            store.update_task(task["id"], status="done")
            store.add_task_event(
                org_id, task["id"], "completed", actor="email",
                payload={"evidence": intent.get("evidence"), "confidence": confidence},
            )
            return {"applied": "status -> done"}

        if kind == "date_change" and confidence >= 0.6 and intent.get("new_due_date"):
            store.update_task(task["id"], due_date=intent["new_due_date"])
            store.add_task_event(
                org_id, task["id"], "due_date_changed", actor="email",
                payload={
                    "from": task.get("due_date"),
                    "to": intent["new_due_date"],
                    "evidence": intent.get("evidence"),
                },
            )
            return {"applied": f"due_date -> {intent['new_due_date']}"}

        if kind == "blocked" and confidence >= 0.65:
            store.update_task(task["id"], status="blocked")
            store.add_task_event(
                org_id, task["id"], "blocked", actor="email",
                payload={"evidence": intent.get("evidence")},
            )
            return {"applied": "status -> blocked"}

        if kind == "in_progress" and confidence >= 0.65:
            store.update_task(task["id"], status="in_progress")
            store.add_task_event(org_id, task["id"], "in_progress", actor="email", payload=intent)
            return {"applied": "status -> in_progress"}

        if kind == "unrelated":
            return {"applied": "ignored: unrelated"}

        proposal = store.create_proposal(
            org_id,
            "reply_review",
            {
                "task": {"id": task["id"], "title": task["title"]},
                "intent": intent,
                "message": {
                    "from": state["message"].get("from_email"),
                    "subject": state["message"].get("subject"),
                    "snippet": (state["message"].get("body_text") or "")[:400],
                },
            },
            dedupe_key=f"reply_review:{state['message_id']}",
        )
        return {"proposal_id": proposal["id"], "applied": "queued for review"}

    graph = StateGraph(InboxState)
    graph.add_node("load", load)
    graph.add_node("match", match)
    graph.add_node("classify", classify)
    graph.add_node("apply", apply)
    graph.add_edge(START, "load")
    graph.add_edge("load", "match")
    graph.add_edge("match", "classify")
    graph.add_edge("classify", "apply")
    graph.add_edge("apply", END)
    return graph.compile(checkpointer=checkpointer)


def run_inbox(graph, *, org_id: str, message: Message, thread_id: str | None = None) -> dict:
    import uuid

    thread_id = thread_id or f"inbox-{message.external_id}-{uuid.uuid4().hex[:6]}"
    return graph.invoke(
        {"org_id": org_id, "message": message.model_dump(mode="json")},
        {"configurable": {"thread_id": thread_id}},
    )
