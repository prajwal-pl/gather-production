"""Nudge drafting. Style matters more than structure here, so this is prose."""

from __future__ import annotations

from ..llm import BaseLLM

SYSTEM = """You write short, low-friction follow-up messages to a colleague.

Rules:
- 2 to 4 sentences. No greeting paragraph, no signature block.
- Name the specific deliverable.
- Say why you are asking now (deadline, blocker, or silence).
- Make it easy to reply: one line is enough, and a new date is a fine answer.
- Never guilt-trip, never escalate, never invent progress.
- Plain text only."""


def draft_nudge(llm: BaseLLM, *, task: dict, why: str) -> str:
    user = f"""TASK: {task['title']}
OWNER: {task.get('owner_email') or 'unassigned'}
DUE: {task.get('due_date') or 'none'}
WHY: {why}"""
    return llm.complete(SYSTEM, user, hint="nudge").strip()
