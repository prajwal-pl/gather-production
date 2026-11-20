from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

import httpx

from ..domain import Event, EventKind, Meeting, ObjectRef, Person, SyncBatch
from .base import (
    AuthExpired,
    Capability,
    Connection,
    RateLimited,
    SyncError,
    retry_after_seconds,
)

FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures"

_TRANSCRIPT_QUERY = """
query Transcript($id: String!) {
  transcript(id: $id) {
    id
    title
    date
    duration
    participants
    sentences { speaker_name text }
    summary { overview action_items }
  }
}
""".strip()

_LIST_TRANSCRIPTS_QUERY = """
query Transcripts($from: DateTime, $to: DateTime, $limit: Int, $skip: Int) {
  transcripts(fromDate: $from, toDate: $to, limit: $limit, skip: $skip) {
    id
    title
    date
    duration
    participants
  }
}
""".strip()

_MAX_PAGES = 40
_BACKFILL_DAYS = 14
# Transcripts can land hours after the meeting; rescan a day and let dedupe
# absorb the repeats.
_OVERLAP = timedelta(hours=24)


class FirefliesConnector:
    """Fireflies delivers a thin notification, then you fetch the transcript.

    That indirection is deliberate on their side and it is the same shape as
    Gmail, so the spine handles both the same way: store the notification, then
    fetch the heavy object.
    """

    provider = "fireflies"
    capabilities = frozenset({Capability.GET_TRANSCRIPT})
    poll_interval_s = 600
    push_capable = True

    def __init__(
        self,
        *,
        api_key: str | None = None,
        fixture_dir: Path | None = None,
        endpoint: str = "https://api.fireflies.ai/graphql",
    ) -> None:
        self.api_key = api_key if api_key is not None else os.environ.get("FIREFLIES_API_KEY")
        self.fixture_dir = fixture_dir or FIXTURE_DIR
        self.endpoint = endpoint

    # -- webhook ----------------------------------------------------------- #

    def parse_webhook(self, headers: Mapping[str, str], body: bytes) -> list[Event]:
        data: dict[str, Any] = json.loads(body or b"{}")
        event_name = data.get("event") or data.get("eventType") or "meeting.transcribed"
        payload = data.get("data") if isinstance(data.get("data"), dict) else {}
        meeting_id = (
            data.get("meetingId")
            or data.get("meeting_id")
            or payload.get("meetingId")
            or payload.get("meeting_id")
        )
        if not meeting_id:
            return []
        return [
            Event(
                id=f"fireflies:{meeting_id}:{event_name}",
                provider=self.provider,
                kind=EventKind.MEETING_COMPLETED,
                occurred_at=datetime.now(timezone.utc),
                subject=ObjectRef(provider=self.provider, id=str(meeting_id), kind="meeting"),
                payload={"event": event_name, "meeting_id": str(meeting_id)},
            )
        ]

    # -- sync -------------------------------------------------------------- #

    def sync(self, conn: Connection, limit: int = 50) -> SyncBatch:
        """List transcripts in the window since the cursor.

        A transcript can arrive hours after the meeting ends, so the window
        overlaps by a day and dedupe does the rest.
        """
        if not self.api_key:
            return SyncBatch(events=[], cursor=conn.cursor)

        now = datetime.now(timezone.utc)
        start = _cursor_time(conn.cursor)
        if start is None:
            start = now - timedelta(days=_BACKFILL_DAYS)
        else:
            start = max(start - _OVERLAP, now - timedelta(days=_BACKFILL_DAYS))
        if start >= now:
            return SyncBatch(events=[], cursor=_iso(now))
        return self._window(conn, start, now, limit)

    def backfill(self, conn: Connection, since: datetime, limit: int = 50) -> SyncBatch:
        if not self.api_key:
            return SyncBatch(events=[], cursor=conn.cursor)
        return self._window(conn, since, datetime.now(timezone.utc), limit)

    def _window(
        self, conn: Connection, start: datetime, end: datetime, limit: int
    ) -> SyncBatch:
        page_size = max(1, min(limit, 50))
        skip = 0
        events: list[Event] = []

        for _ in range(_MAX_PAGES):
            data = self._query(
                _LIST_TRANSCRIPTS_QUERY,
                {"from": _iso(start), "to": _iso(end), "limit": page_size, "skip": skip},
            )
            if "transcripts" not in data:
                # Never advance the cursor on a malformed/empty GraphQL envelope.
                raise SyncError("fireflies transcript list was missing from the response")
            rows = data.get("transcripts") or []
            for row in rows:
                meeting_id = str(row.get("id") or "")
                if not meeting_id:
                    continue
                stamp = row.get("date")
                revision = str(int(stamp)) if isinstance(stamp, (int, float)) else "v1"
                events.append(
                    Event(
                        id=f"fireflies:{meeting_id}:{revision}",
                        provider=self.provider,
                        kind=EventKind.MEETING_COMPLETED,
                        occurred_at=_parse_dt(stamp) or end,
                        subject=ObjectRef(
                            provider=self.provider, id=meeting_id, kind="meeting"
                        ),
                        payload={
                            "meeting_id": meeting_id,
                            "date": stamp,
                            "title": row.get("title"),
                        },
                    )
                )
            if len(rows) < page_size:
                break
            skip += len(rows)
        else:
            # Never silently drop a window: surface it and keep the old cursor.
            raise SyncError("fireflies transcript window is too large; run a narrower backfill")

        return SyncBatch(events=events, cursor=_iso(end))

    # -- capability -------------------------------------------------------- #

    def execute(self, conn: Connection, capability: Capability, args: dict[str, Any]) -> Any:
        if capability is Capability.GET_TRANSCRIPT:
            return self.fetch_meeting(conn.org_id, str(args["external_id"]))
        raise ValueError(f"fireflies cannot {capability}")

    def fetch_meeting(self, org_id: str, external_id: str) -> Meeting:
        if self.api_key:
            raw = self._graphql(external_id)
        else:
            raw = self._fixture()
        return self._to_meeting(org_id, external_id, raw)

    def _graphql(self, external_id: str) -> dict[str, Any]:
        data = self._query(_TRANSCRIPT_QUERY, {"id": external_id})
        return data.get("transcript") or {}

    def _query(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        response = httpx.post(
            self.endpoint,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json={"query": query, "variables": variables},
            timeout=60,
        )
        if response.status_code == 429:
            raise RateLimited(
                "fireflies rate limit",
                retry_after_s=retry_after_seconds(
                    (response.headers or {}).get("retry-after")
                ),
            )
        if response.status_code in (401, 403):
            raise AuthExpired(f"fireflies auth failed ({response.status_code})")
        response.raise_for_status()
        body = response.json()
        if body.get("errors"):
            raise SyncError(f"fireflies graphql error: {body['errors']}")
        return body.get("data") or {}

    def _fixture(self) -> dict[str, Any]:
        path = self.fixture_dir / "fireflies_meeting.json"
        return json.loads(path.read_text(encoding="utf-8"))

    # -- normalization ----------------------------------------------------- #

    def _to_meeting(self, org_id: str, external_id: str, raw: dict[str, Any]) -> Meeting:
        sentences = raw.get("sentences") or []
        lines: list[str] = []
        speakers: dict[str, Person] = {}
        for sentence in sentences:
            speaker = (sentence.get("speaker_name") or sentence.get("speaker") or "Unknown").strip()
            text = (sentence.get("text") or sentence.get("raw_text") or "").strip()
            if not text:
                continue
            lines.append(f"{speaker}: {text}")
            speakers.setdefault(speaker, Person(name=speaker))

        participants: list[Person] = []
        for entry in raw.get("participants") or []:
            if isinstance(entry, dict):
                person = Person(name=entry.get("name") or entry.get("email") or "", email=entry.get("email"))
            else:
                person = Person(name=str(entry).split("@")[0], email=str(entry) if "@" in str(entry) else None)
            if person.name:
                participants.append(person)
                speakers.setdefault(person.name, person)
                if person.email:
                    speakers[person.name] = person

        summary = None
        if isinstance(raw.get("summary"), dict):
            summary = raw["summary"].get("overview") or None

        started = _parse_dt(raw.get("date"))
        return Meeting(
            id=str(raw.get("id") or external_id),
            org_id=org_id,
            provider=self.provider,
            external_id=str(raw.get("id") or external_id),
            title=raw.get("title") or "Untitled meeting",
            started_at=started,
            duration_min=int(raw.get("duration")) if raw.get("duration") else None,
            participants=participants or list(speakers.values()),
            transcript="\n".join(lines),
            summary=summary,
        )


def _parse_dt(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        # Fireflies returns epoch milliseconds.
        return datetime.fromtimestamp(value / 1000, tz=timezone.utc)
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _cursor_time(cursor: str | None) -> datetime | None:
    if not cursor:
        return None
    try:
        parsed = datetime.fromisoformat(cursor.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
