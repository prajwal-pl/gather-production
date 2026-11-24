"""Background jobs: the clock-driven half of the system.

In production these are cron entries (or LangGraph Platform cron). They never
send anything directly; they queue a proposal for approval.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

from ..agents.draft import draft_nudge
from ..db import Store
from ..llm import BaseLLM, LLMError
from ..notify import Notifier


@dataclass
class SweepResult:
    nudges: list[dict] = field(default_factory=list)
    renewals: list[dict] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def sweep(
    store: Store,
    llm: BaseLLM,
    notifier: Notifier,
    *,
    org_id: str,
    today: date | None = None,
    deliver: bool = False,
) -> SweepResult:
    today = today or date.today()
    result = SweepResult()
    llm.start_run()

    for task in store.stale_nudge_candidates(org_id, today):
        why = (
            f"it was due {task['due_date']}"
            if task.get("due_date")
            else "it has been open with no update"
        )
        try:
            body = draft_nudge(llm, task=task, why=why)
        except (LLMError, ValueError) as exc:
            result.errors.append(f"{task['title']}: {exc}")
            continue

        proposal = store.create_proposal(
            org_id,
            "nudge",
            {
                "task": {"id": task["id"], "title": task["title"]},
                "to": task.get("owner_email"),
                "subject": f"Check-in: {task['title']}",
                "body": body,
                "why": why,
            },
            dedupe_key=f"nudge:{task['id']}:{today.isoformat()}",
        )
        store.update_task(task["id"], last_nudged_at=today.isoformat())
        store.add_task_event(org_id, task["id"], "nudge_drafted", actor="scheduler", payload={"why": why})
        result.nudges.append(proposal)

        if deliver:
            notifier.send(
                org_id=org_id,
                to=task.get("owner_email"),
                subject=f"Check-in: {task['title']}",
                body=body,
                channel="nudge",
            )

    for conn in store.connections_expiring(org_id):
        result.renewals.append(
            {
                "provider": conn["provider"],
                "connection_id": conn["id"],
                "expires_at": conn["expires_at"],
                "action": "renew subscription / refresh token",
            }
        )

    return result
