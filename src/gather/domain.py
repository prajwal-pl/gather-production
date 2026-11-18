"""Canonical objects.

Nothing downstream of a connector should ever see provider JSON. If a provider
needs to expose something unusual, put it in ``extensions`` instead of growing
the core model — that is what keeps eight connectors from turning into eight
different domain models.
"""

from __future__ import annotations

import hashlib
import re
from datetime import date, datetime, timedelta
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field


class EventKind(StrEnum):
    MEETING_COMPLETED = "meeting.completed"
    MESSAGE_RECEIVED = "message.received"
    FILE_UPDATED = "file.updated"
    CALENDAR_CHANGED = "calendar.changed"


class TaskStatus(StrEnum):
    PROPOSED = "proposed"
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    BLOCKED = "blocked"
    DONE = "done"
    DISMISSED = "dismissed"


class Person(BaseModel):
    name: str = ""
    email: str | None = None


class ObjectRef(BaseModel):
    provider: str
    id: str
    kind: str


class Event(BaseModel):
    """The one shape every provider normalizes into."""

    id: str  # f"{provider}:{provider_event_id}" — also the dedupe key
    org_id: str = ""  # filled by the webhook edge, never by the provider
    provider: str
    kind: EventKind
    occurred_at: datetime
    actor: Person | None = None
    subject: ObjectRef | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class Meeting(BaseModel):
    id: str
    org_id: str
    provider: str
    external_id: str
    title: str
    started_at: datetime | None = None
    duration_min: int | None = None
    participants: list[Person] = Field(default_factory=list)
    transcript: str
    summary: str | None = None
    extensions: dict[str, Any] = Field(default_factory=dict)


class Commitment(BaseModel):
    """What the extraction model is allowed to return. Tight on purpose."""

    title: str = Field(description="Short imperative phrase, max 12 words")
    detail: str = ""
    owner_name: str = Field(default="", description="Exactly as written in the transcript")
    owner_email: str | None = None
    due_date: str | None = Field(default=None, description="ISO YYYY-MM-DD, or null")
    quote: str = Field(default="", description="Verbatim sentence that justifies it")
    confidence: float = 0.5


class CommitmentBatch(BaseModel):
    commitments: list[Commitment] = Field(default_factory=list)


class ReplyIntent(BaseModel):
    intent: Literal[
        "done",
        "in_progress",
        "blocked",
        "date_change",
        "unrelated",
        "needs_review",
    ] = "unrelated"
    new_due_date: str | None = None
    summary: str = ""
    evidence: str = ""
    confidence: float = 0.0


class Task(BaseModel):
    id: str
    org_id: str
    title: str
    detail: str | None = None
    owner_user_id: str | None = None
    owner_email: str | None = None
    due_date: str | None = None
    status: TaskStatus = TaskStatus.OPEN
    priority: str | None = None
    source_meeting_id: str | None = None
    source_quote: str | None = None
    confidence: float | None = None
    fingerprint: str | None = None
    next_check_at: str | None = None
    last_nudged_at: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


class Message(BaseModel):
    id: str
    org_id: str
    provider: str
    external_id: str
    thread_id: str
    from_email: str
    from_name: str = ""
    to_emails: list[str] = Field(default_factory=list)
    subject: str = ""
    body_text: str = ""
    sent_at: datetime | None = None


class SearchHit(BaseModel):
    provider: str
    external_id: str
    thread_id: str
    subject: str = ""
    snippet: str = ""
    sent_at: str | None = None
    score: float = 0.0


class SyncBatch(BaseModel):
    events: list[Event] = Field(default_factory=list)
    cursor: str | None = None
    has_more: bool = False
    # The provider rejected the cursor (Gmail 404). The poller must backfill.
    reset: bool = False


class Proposal(BaseModel):
    id: str
    org_id: str
    kind: str
    thread_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    status: str = "pending"
    created_at: str | None = None


# --------------------------------------------------------------------------- #
# Deterministic helpers. No LLM below this line — ever.
# --------------------------------------------------------------------------- #

_WEEKDAYS = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}

_STOPWORDS = {
    "a", "an", "the", "to", "for", "of", "and", "or", "on", "in", "with", "by",
    "please", "will", "we", "i", "it", "this", "that", "is", "be", "at", "from",
    "up", "out", "into", "then", "so", "ll", "re", "ve", "our", "your", "their",
}


def parse_due_date(raw: str | None, today: date | None = None) -> date | None:
    """Best-effort deterministic due-date parsing. The model is asked for ISO,
    but free models drift, so we recover the common phrasings ourselves."""
    if not raw:
        return None
    today = today or date.today()
    s = raw.strip().lower()

    iso = re.search(r"\d{4}-\d{2}-\d{2}", s)
    if iso:
        try:
            return date.fromisoformat(iso.group(0))
        except ValueError:
            pass

    # Earliest weekday mention wins, so "pushed to Monday" beats a stray
    # reference earlier in the same sentence.
    best: tuple[int, int] | None = None
    for name, idx in _WEEKDAYS.items():
        pos = s.find(name)
        if pos >= 0 and (best is None or pos < best[0]):
            best = (pos, idx)
    if best is not None:
        delta = (best[1] - today.weekday()) % 7
        if delta == 0:
            delta = 7
        return today + timedelta(days=delta)

    if "tomorrow" in s:
        return today + timedelta(days=1)
    if "today" in s or "eod" in s or "end of day" in s:
        return today
    if "next week" in s:
        return today + timedelta(days=7 - today.weekday())

    rel = re.search(r"in (\d+) (day|week|month)s?", s)
    if rel:
        n, unit = int(rel.group(1)), rel.group(2)
        if unit == "day":
            return today + timedelta(days=n)
        if unit == "week":
            return today + timedelta(weeks=n)
        return today + timedelta(days=30 * n)

    return None


def due_from_text(text: str | None, reference: date | None = None) -> date | None:
    """Pull the operative due date out of a sentence.

    Strong prepositions first ("by Friday"), then weak ones ("on Monday"),
    then the whole string. This is the deterministic half of date handling:
    the model may propose a date, but the transcript decides it.
    """
    text = text or ""
    for pattern in (
        r"\b(?:by|before|until|due)\s+([^.,;!?]{1,30})",
        r"\b(?:on|for|to)\s+([^.,;!?]{1,30})",
    ):
        for match in re.finditer(pattern, text, re.IGNORECASE):
            parsed = parse_due_date(match.group(1), reference)
            if parsed:
                return parsed
    return parse_due_date(text, reference)


def normalize_title(title: str) -> str:
    tokens = [t for t in re.findall(r"[a-z0-9]+", title.lower()) if t not in _STOPWORDS]
    return " ".join(sorted(tokens))


def task_fingerprint(owner_email: str | None, title: str) -> str:
    key = f"{(owner_email or '').strip().lower()}|{normalize_title(title)}"
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]


def utcnow() -> datetime:
    return datetime.now().astimezone()
