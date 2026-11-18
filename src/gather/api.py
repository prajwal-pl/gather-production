"""HTTP edge.

Two responsibilities only:

1. Verify a provider delivery, dedupe it, ack fast, and hand work to a
   background task. No LLM call happens inside the request.
2. Expose the approval queue. Nothing external is sent on the strength of a
   model's opinion alone.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import Any

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request

from . import __version__
from .config import Settings
from .connectors.base import Connection
from .graphs import resume_meeting, run_inbox, run_meeting
from .runtime import Runtime, build_runtime


def _process_meeting(rt: Runtime, org_id: str, external_id: str, thread_id: str) -> None:
    try:
        run_meeting(
            rt.meeting_graph, org_id=org_id, external_id=external_id, thread_id=thread_id
        )
    except Exception as exc:  # noqa: BLE001 - background work must not kill the worker
        print(f"[webhook] meeting processing failed: {exc}")


def _process_inbox(rt: Runtime, org_id: str, payload: dict[str, Any]) -> None:
    rows = rt.store.connections(org_id, provider="gmail")
    if not rows:
        print("[webhook] gmail event with no active connection; ignored")
        return
    conn = Connection(**rows[0])
    try:
        messages = rt.gmail.fetch_new(conn, payload)
        if conn.cursor:
            rt.store.set_cursor(conn.id, conn.cursor)
        for message in messages:
            message.org_id = org_id
            run_inbox(rt.inbox_graph, org_id=org_id, message=message)
    except Exception as exc:  # noqa: BLE001
        print(f"[webhook] inbox processing failed: {exc}")


async def _json_body(request: Request) -> dict[str, Any]:
    raw = await request.body()
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


def create_app(settings: Settings | None = None) -> FastAPI:
    rt = build_runtime(settings)
    app = FastAPI(title="gather", version=__version__)
    app.state.rt = rt
    org_id = rt.org_id

    def require_org(org: str) -> str:
        # The tenant comes from the registered webhook URL. If it is not a real
        # org, refuse — never create a tenant implicitly from a URL segment.
        if not rt.store.get_org(org):
            raise HTTPException(status_code=404, detail="unknown organization")
        return org

    # -- health ------------------------------------------------------------ #

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {
            "ok": True,
            "version": __version__,
            "org_id": org_id,
            "providers": rt.hub.providers(),
            "models": [spec.label for spec in rt.settings.live_models] or ["fixture"],
            "usage": rt.llm.usage.as_dict(),
        }

    # -- inbound webhooks -------------------------------------------------- #

    @app.post("/webhooks/fireflies/{org}", status_code=202)
    async def fireflies_webhook(
        org: str, request: Request, background: BackgroundTasks
    ) -> dict[str, Any]:
        body = await request.body()
        secret = rt.settings.fireflies_secret
        if secret and not hmac.compare_digest(
            request.headers.get("x-gather-secret", ""), secret
        ):
            raise HTTPException(status_code=401, detail="bad signature")

        org = require_org(org)
        events = rt.fireflies.parse_webhook(dict(request.headers), body)
        raw = json.loads(body or b"{}")
        threads: list[str] = []
        for event in events:
            event.org_id = org
            if not rt.store.record_event(event, raw):
                continue  # provider retry
            thread_id = f"meeting-{event.subject.id}-{int(time.time())}"
            threads.append(thread_id)
            background.add_task(_process_meeting, rt, org, event.subject.id, thread_id)

        return {"accepted": len(threads), "threads": threads}

    @app.post("/webhooks/gmail/{org}", status_code=202)
    async def gmail_webhook(
        org: str, request: Request, background: BackgroundTasks
    ) -> dict[str, Any]:
        body = await request.body()
        if rt.settings.gmail_token and request.query_params.get("token") != rt.settings.gmail_token:
            raise HTTPException(status_code=401, detail="bad token")

        org = require_org(org)
        events = rt.gmail.parse_webhook(dict(request.headers), body)
        accepted = 0
        for event in events:
            event.org_id = org
            if not rt.store.record_event(event, json.loads(body or b"{}")):
                continue
            accepted += 1
            background.add_task(_process_inbox, rt, org, event.payload)
        return {"accepted": accepted}

    @app.post("/webhooks/slack")
    async def slack_webhook(request: Request) -> dict[str, Any]:
        body = await request.body()
        secret = rt.settings.slack_signing_secret
        if secret:
            timestamp = request.headers.get("x-slack-request-timestamp", "0")
            signature = request.headers.get("x-slack-signature", "")
            if abs(time.time() - float(timestamp or 0)) > 300:
                raise HTTPException(status_code=401, detail="stale request")
            base = f"v0:{timestamp}:{body.decode()}".encode()
            expected = "v0=" + hmac.new(secret.encode(), base, hashlib.sha256).hexdigest()
            if not hmac.compare_digest(expected, signature):
                raise HTTPException(status_code=401, detail="bad signature")

        payload = json.loads(body or b"{}")

        if payload.get("type") == "url_verification":
            return {"challenge": payload.get("challenge")}

        if payload.get("type") == "block_actions":
            action = (payload.get("actions") or [{}])[0]
            if action.get("action_id") == "task_done" and action.get("value"):
                task_id = action["value"]
                rt.store.update_task(task_id, status="done")
                rt.store.add_task_event(org_id, task_id, "completed", actor="slack")
                return {"ok": True, "task_id": task_id}

        return {"ok": True}

    # -- approvals --------------------------------------------------------- #

    @app.get("/proposals")
    def list_proposals(status: str = "pending") -> dict[str, Any]:
        return {"proposals": rt.store.proposals(org_id, status=status)}

    @app.get("/proposals/{proposal_id}")
    def get_proposal(proposal_id: str) -> dict[str, Any]:
        proposal = rt.store.get_proposal(proposal_id)
        if not proposal or proposal["org_id"] != org_id:
            raise HTTPException(status_code=404, detail="not found")
        return proposal

    @app.post("/proposals/{proposal_id}/approve")
    async def approve_proposal(proposal_id: str, request: Request) -> dict[str, Any]:
        proposal = rt.store.get_proposal(proposal_id)
        if not proposal or proposal["org_id"] != org_id:
            raise HTTPException(status_code=404, detail="not found")

        body = await _json_body(request)

        if proposal["kind"] == "task_approval":
            tasks = proposal["payload"].get("tasks", [])
            approved = body.get("approved")
            if approved is None:
                approved = [task["fingerprint"] for task in tasks]
            state = resume_meeting(
                rt.meeting_graph,
                proposal["thread_id"],
                approved=approved,
                edits=body.get("edits"),
            )
            rt.store.decide_proposal(proposal_id, "approved")
            return {"created": state.get("created", [])}

        if proposal["kind"] == "nudge":
            payload = proposal["payload"]
            delivered = rt.notifier.send(
                org_id=org_id,
                to=payload.get("to"),
                subject=payload.get("subject", "Follow-up"),
                body=payload.get("body", ""),
                channel="nudge",
            )
            rt.store.decide_proposal(proposal_id, "approved")
            return {"delivered": delivered}

        rt.store.decide_proposal(proposal_id, "approved")
        return {"ok": True}

    @app.post("/proposals/{proposal_id}/reject")
    async def reject_proposal(proposal_id: str) -> dict[str, Any]:
        proposal = rt.store.get_proposal(proposal_id)
        if not proposal or proposal["org_id"] != org_id:
            raise HTTPException(status_code=404, detail="not found")

        if proposal["kind"] == "task_approval" and proposal.get("thread_id"):
            resume_meeting(rt.meeting_graph, proposal["thread_id"], approved=[])
        rt.store.decide_proposal(proposal_id, "rejected")
        return {"ok": True}

    # -- reads ------------------------------------------------------------- #

    @app.get("/tasks")
    def list_tasks(status: str | None = None) -> dict[str, Any]:
        return {"tasks": rt.store.tasks(org_id, status=status)}

    @app.get("/tasks/{task_id}")
    def get_task(task_id: str) -> dict[str, Any]:
        task = rt.store.get_task(task_id)
        if not task or task["org_id"] != org_id:
            raise HTTPException(status_code=404, detail="not found")
        return {"task": task, "events": rt.store.task_events(task_id)}

    @app.get("/runs/{thread_id}")
    def get_run(thread_id: str) -> dict[str, Any]:
        snapshot = rt.meeting_graph.get_state({"configurable": {"thread_id": thread_id}})
        return {"values": snapshot.values, "next": list(snapshot.next)}

    return app


def __getattr__(name: str):
    """Lazy ASGI app for ``uvicorn gather.api:app``.

    Building the runtime opens the database and compiles the graphs, so it
    should happen on first use, not at import time.
    """
    if name == "app":
        return create_app()
    raise AttributeError(name)
