"""End-to-end demo, no accounts required.

    python -m gather.demo            # offline, deterministic model
    python -m gather.demo --live     # real Groq / OpenRouter keys from .env

It walks the whole spine: webhook -> dedupe -> extract -> resolve -> fan out ->
human approval -> persist -> notify -> reply classification -> nudge sweep.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, timedelta

from .config import ROOT, Settings, load_settings
from .domain import task_fingerprint
from .graphs import resume_meeting, run_inbox, run_meeting
from .runtime import Runtime, build_runtime
from .workers import sweep

DEMO_DB = ROOT / ".gather" / "demo.db"


def rule(label: str) -> None:
    print(f"\n\033[1m{label}\033[0m")
    print("─" * 74)


def _interrupt_value(state: dict):
    items = state.get("__interrupt__") or ()
    if not items:
        return None
    first = items[0]
    return getattr(first, "value", first)


def _show_card(card: dict, index: int) -> None:
    owner = card.get("owner_email") or (
        f"{card['external_owner']} (external)" if card.get("external_owner") else "unassigned"
    )
    due = card.get("due_date") or "no date"
    print(f"  {index}. {card['title']}")
    print(f"     owner={owner}  due={due}  priority={card.get('priority')}  "
          f"confidence={card.get('confidence')}")
    if card.get("quote"):
        print(f"     quote: “{card['quote'][:110]}”")
    for hit in card.get("context") or []:
        print(f"     context[{hit['provider']}]: {hit['subject'][:70]}")


def _seed(rt: Runtime) -> tuple[str, str, dict]:
    store, org = rt.store, rt.org_id
    priya = store.add_user(org, "priya@acme.test", "Priya Raman")
    store.add_user(org, "marcus@acme.test", "Marcus Lee")
    store.upsert_connection(org, "fireflies", mode="org")
    store.upsert_connection(org, "gmail", mode="user", user_id=priya)

    overdue = (date.today() - timedelta(days=1)).isoformat()
    title = "Send updated pricing deck to Northwind"
    existing = store.create_task(
        org,
        title=title,
        owner_user_id=priya,
        owner_email="priya@acme.test",
        due_date=overdue,
        fingerprint=task_fingerprint("priya@acme.test", title),
    )
    store.add_task_event(org, existing["id"], "created", actor="seed")
    return org, priya, existing


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="gather demo")
    parser.add_argument("--live", action="store_true", help="use Groq/OpenRouter from .env")
    parser.add_argument("--keep", action="store_true", help="keep the demo database")
    parser.add_argument("--auto-approve", action="store_true", help="skip the human gate")
    args = parser.parse_args(argv)

    if DEMO_DB.exists() and not args.keep:
        DEMO_DB.unlink()

    settings: Settings = load_settings(db_path=DEMO_DB, auto_approve=args.auto_approve)
    if args.live and settings.fixture_mode:
        print("No provider keys found. Set GROQ_API_KEY and/or OPENROUTER_API_KEY in .env.")
        return 1

    rt = build_runtime(settings, force_fixture=not args.live)
    live = bool(rt.settings.live_models) and args.live
    org, priya, seeded = _seed(rt)

    print("gather — end to end")
    print(f"mode: {'live models' if live else 'offline fixture model'}")
    print(f"db:   {settings.db_path}")

    # ------------------------------------------------------------------ #
    rule("1. Inbound Fireflies webhook")
    body = json.dumps({"event": "meeting.transcribed", "meetingId": "ff_9f3a2c"}).encode()
    event = rt.fireflies.parse_webhook({}, body)[0]
    event.org_id = org
    first_delivery = rt.store.record_event(event, json.loads(body))
    retry_delivery = rt.store.record_event(event, json.loads(body))
    print(f"  event: {event.id}")
    print(f"  first delivery recorded: {first_delivery}")
    print(f"  provider retry suppressed: {not retry_delivery}")

    state, thread_id = run_meeting(
        rt.meeting_graph, org_id=org, external_id=event.subject.id
    )
    stats = state.get("stats", {})
    print(f"  chunks: {stats.get('chunks')}  raw: {stats.get('raw_commitments')}  "
          f"after merge: {stats.get('after_merge')}")
    print(f"  duplicates vs open tasks: {len(state.get('duplicates', []))}")
    for dup in state.get("duplicates", []):
        print(f"    ~ {dup['title']}  (already open: {dup['duplicate_title']})")
    if state.get("warning"):
        print(f"  warning: {state['warning']}")

    # ------------------------------------------------------------------ #
    rule("2. Human approval (graph is paused at interrupt)")
    payload = _interrupt_value(state)
    if payload is None:
        created = state.get("created", [])
        print("  auto-approve enabled; tasks written without a gate")
    else:
        print(f"  proposal: {payload['proposal_id']}")
        print(f"  meeting:  {payload['meeting']}")
        print(f"  {len(payload['tasks'])} candidate task(s):")
        for index, card in enumerate(payload["tasks"], start=1):
            _show_card(card, index)
        approved = [card["fingerprint"] for card in payload["tasks"]]
        state = resume_meeting(rt.meeting_graph, thread_id, approved)
        rt.store.decide_proposal(payload["proposal_id"], "approved")
        created = state.get("created", [])
        print(f"\n  approved {len(approved)} -> {len(created)} task(s) created")

    for task in created:
        print(f"    + [{task['status']}] {task['title']}  "
              f"owner={task['owner_email'] or 'unassigned'}  due={task['due_date'] or '-'}")

    # ------------------------------------------------------------------ #
    rule("3. Scheduler sweep (overdue task -> drafted nudge, not sent)")
    result = sweep(rt.store, rt.llm, rt.notifier, org_id=org, today=date.today())
    if not result.nudges:
        print("  nothing overdue")
    for proposal in result.nudges:
        print(f"  proposal {proposal['id']}")
        print(f"  to: {proposal['payload'].get('to')}")
        print(f"  “{proposal['payload']['body']}”")
    for renewal in result.renewals:
        print(f"  renew {renewal['provider']} ({renewal['expires_at']})")
    for error in result.errors:
        print(f"  error: {error}")

    # ------------------------------------------------------------------ #
    rule("4. Inbound Gmail reply -> task update")
    message = rt.gmail.fixture_message(org)
    print(f"  from: {message.from_email}")
    print(f"  subject: {message.subject}")
    inbox = run_inbox(rt.inbox_graph, org_id=org, message=message)
    matched = inbox.get("task")
    if matched:
        print(f"  matched: {matched['title']}  (score={inbox.get('match_score'):.2f}, "
              f"{inbox.get('match_method')})")
    intent = inbox.get("intent", {})
    print(f"  intent: {intent.get('intent')}  confidence={intent.get('confidence')}")
    print(f"  applied: {inbox.get('applied')}")
    if matched:
        updated = rt.store.get_task(matched["id"])
        print(f"  task now: status={updated['status']}  due={updated['due_date']}")

    # ------------------------------------------------------------------ #
    rule("5. State of the world")
    print(f"  {'STATUS':12} {'DUE':12} {'OWNER':24} TITLE")
    for task in rt.store.tasks(org):
        print(
            f"  {task['status']:12} {(task['due_date'] or '-'):12} "
            f"{(task['owner_email'] or 'unassigned'):24} {task['title'][:60]}"
        )

    pending = rt.store.proposals(org)
    print(f"\n  pending proposals: {len(pending)}")
    print(f"  model usage: {rt.llm.usage.as_dict()}")
    print(f"\n  inspect: GATHER_DB={DEMO_DB} python -m gather.serve")
    return 0


if __name__ == "__main__":
    sys.exit(main())
