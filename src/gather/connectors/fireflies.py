from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

import httpx

from ..domain import Event, EventKind, Meeting, ObjectRef, Person, SyncBatch
from .base import Capability, Connection

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


class FirefliesConnector:
    """Fireflies delivers a thin notification, then you fetch the transcript.

    That indirection is deliberate on their side and it is the same shape as
    Gmail, so the spine handles both the same way: store the notification, then
    fetch the heavy object.
    """

    provider = "fireflies"
    capabilities = frozenset({Capability.GET_TRANSCRIPT})

    def __init__(
        self,
        *,
        api_key: str | None = None,
        fixture_dir: Path | None = None,
        endpoint: str = "https://api.fireflies.ai/graphql",
    ) -> None:
        self.api_key = api_key or os.environ.get("FIREFLIES_API_KEY")
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
        return SyncBatch(events=[], cursor=conn.cursor, has_more=False)

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
        response = httpx.post(
            self.endpoint,
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            json={"query": _TRANSCRIPT_QUERY, "variables": {"id": external_id}},
            timeout=60,
        )
        response.raise_for_status()
        body = response.json()
        if body.get("errors"):
            raise RuntimeError(f"fireflies graphql error: {body['errors']}")
        return (body.get("data") or {}).get("transcript") or {}

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
