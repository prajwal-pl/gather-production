from __future__ import annotations

from datetime import date

import pytest

from gather.agents.extract import chunk_transcript, merge_commitments
from gather.domain import Commitment, parse_due_date
from gather.graphs import resume_meeting, run_inbox, run_meeting
from gather.llm import extract_json

TODAY = date(2026, 11, 17)


def test_extract_json_recovers_from_fences_and_prose():
    assert extract_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert extract_json('Sure! Here you go: {"a": 2} Hope that helps.') == {"a": 2}
    with pytest.raises(ValueError):
        extract_json("no json here")


def test_chunking_covers_every_line():
    transcript = "\n".join(f"Speaker: line number {i} with some padding text" for i in range(200))
    chunks, truncated = chunk_transcript(transcript, max_chars=2000, max_chunks=50)
    assert not truncated
    joined = "\n".join(chunks)
    assert "line number 0" in joined
    assert "line number 199" in joined
    assert all(len(chunk) <= 2200 for chunk in chunks)


def test_merge_collapses_restatements_only_for_same_owner():
    base = Commitment(title="Send the recap to Dana", owner_email="marcus@acme.test", confidence=0.7)
    restated = Commitment(
        title="Send recap to Dana", owner_email="marcus@acme.test", confidence=0.9,
        quote="So I'll send the recap to Dana by Friday.",
    )
    other_owner = Commitment(title="Send the recap to Dana", owner_email="priya@acme.test")
    merged = merge_commitments([base, restated, other_owner])
    assert len(merged) == 2
    marcus = next(item for item in merged if item.owner_email == "marcus@acme.test")
    assert marcus.confidence == 0.9


def test_due_date_parsing():
    assert parse_due_date("2026-11-20", TODAY) == date(2026, 11, 20)
    assert parse_due_date("Friday", TODAY) == date(2026, 11, 20)
    assert parse_due_date("next Tuesday", TODAY) == date(2026, 11, 24)
    assert parse_due_date("next Tuesday", date(2026, 11, 18)) == date(2026, 11, 24)
    assert parse_due_date("tomorrow", TODAY) == date(2026, 11, 18)
    assert parse_due_date("sometime soon", TODAY) is None


def test_meeting_graph_pauses_then_persists(env):
    store, org = env["store"], env["org"]

    state, thread_id = run_meeting(
        env["meeting_graph"], org_id=org, external_id="ff_9f3a2c"
    )

    # 1. The graph is paused at the approval gate, and no tasks were written.
    assert "__interrupt__" in state
    assert state.get("created") in (None, [])
    assert len(store.tasks(org)) == 1  # only the seeded task

    # 2. Duplicates are detected rather than silently created.
    duplicates = state.get("duplicates", [])
    assert any("pricing deck" in item["title"].lower() for item in duplicates)

    # 3. The four real commitments survive merge/dedupe.
    cards = state["__interrupt__"][0].value["tasks"]
    assert len(cards) == 4
    titles = " | ".join(card["title"].lower() for card in cards)
    assert "security review" in titles
    assert "crm" in titles
    assert "recap" in titles
    assert "procurement" in titles

    # 4. External owner stays unassigned instead of being invented into a member.
    procurement = next(card for card in cards if "procurement" in card["title"].lower())
    assert procurement["owner_email"] is None
    assert procurement["external_owner"] == "dana@northwind.test"

    # 5. Approving resumes the checkpoint and writes idempotently.
    running = resume_meeting(
        env["meeting_graph"], thread_id, [card["fingerprint"] for card in cards]
    )
    assert len(running["created"]) == 4
    assert len(store.tasks(org)) == 5

    again = resume_meeting(
        env["meeting_graph"], thread_id, [card["fingerprint"] for card in cards]
    )
    assert len(again.get("created", [])) == 4  # replay, no new rows
    assert len(store.tasks(org)) == 5


def test_reply_updates_due_date(env):
    store, org = env["store"], env["org"]
    message = env["gmail"].fixture_message(org)

    state = run_inbox(env["inbox_graph"], org_id=org, message=message)

    assert state["task"]["id"] == env["seeded"]["id"]
    assert state["intent"]["intent"] == "date_change"
    assert state["applied"].startswith("due_date ->")

    updated = store.get_task(env["seeded"]["id"])
    assert updated["due_date"] is not None
    assert updated["due_date"] != "2026-11-16"
    events = [event["kind"] for event in store.task_events(env["seeded"]["id"])]
    assert "due_date_changed" in events


def test_event_delivery_is_idempotent(env):
    store, org = env["store"], env["org"]
    body = b'{"event": "meeting.transcribed", "meetingId": "ff_9f3a2c"}'
    from gather.connectors.fireflies import FirefliesConnector

    event = FirefliesConnector().parse_webhook({}, body)[0]
    event.org_id = org
    assert store.record_event(event, {}) is True
    assert store.record_event(event, {}) is False
