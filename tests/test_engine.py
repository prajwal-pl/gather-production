"""Engine IPC, scheduler, secret store and cancellation.

The last test is the real contract: it spawns ``python -m gather.engine`` and
speaks JSON lines over its stdin/stdout, exactly as the Tauri shell will.
"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import time
from datetime import date, timedelta
from pathlib import Path

import pytest

from gather.config import load_settings
from gather.engine import main as engine_main
from gather.events import EventBus
from gather.ipc import Handlers, ProtocolError, RpcError, StdioServer, build_router
from gather.ipc.protocol import fail, ok, parse_request
from gather.lock import EngineLock
from gather.runs import RunCancelled, RunRegistry
from gather.runtime import build_runtime
from gather.scheduler import Scheduler
from gather.secrets import (
    SECRET_KEYS,
    FileSecretStore,
    apply_stored_settings,
    build_secret_store,
    resolve_secrets,
    secret_presence,
)

ROOT = Path(__file__).resolve().parents[1]


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture
def runtime(tmp_path):
    settings = load_settings(data_dir=tmp_path)
    rt = build_runtime(settings, log=lambda _message: None, force_fixture=True, secrets={})
    yield rt
    rt.close()


@pytest.fixture
def handlers(runtime, tmp_path):
    store = FileSecretStore(tmp_path / "secrets.json")
    return Handlers(
        runtime,
        events=runtime.events,
        secrets=store,
        keys={},
        log=lambda _message: None,
    )


def call(handlers: Handlers, method: str, params: dict | None = None):
    router = build_router(handlers)
    route = router.route(method)
    assert route is not None, f"missing method {method}"
    return route.handler(params or {})


# --------------------------------------------------------------------------- #
# Protocol
# --------------------------------------------------------------------------- #


def test_protocol_parses_requests_and_rejects_garbage():
    request = parse_request('{"id":"r1","method":"tasks.list","params":{"status":"open"}}')
    assert request.id == "r1"
    assert request.method == "tasks.list"
    assert request.params == {"status": "open"}

    for bad in ("", "not json", "[]", '{"id":{},"method":"x"}', '{"id":"1"}', '{"id":"1","method":"x","params":[]}'):
        with pytest.raises(ProtocolError):
            parse_request(bad)

    assert ok("1", {"a": 1}) == {"id": "1", "ok": True, "result": {"a": 1}}
    assert fail("1", "not_found", "gone")["error"]["code"] == "not_found"


# --------------------------------------------------------------------------- #
# Secrets
# --------------------------------------------------------------------------- #


def test_file_secret_store_roundtrip_and_mode(tmp_path):
    path = tmp_path / "secrets.json"
    store = FileSecretStore(path)
    store.set("GROQ_API_KEY", "secret-value")
    store.set("FIREFLIES_API_KEY", "ff")
    assert store.get("GROQ_API_KEY") == "secret-value"
    store.delete("FIREFLIES_API_KEY")
    assert store.get("FIREFLIES_API_KEY") is None
    assert path.exists()
    assert oct(path.stat().st_mode & 0o777) == "0o600"


def test_resolve_secrets_prefers_store_then_environment(tmp_path, monkeypatch):
    store = FileSecretStore(tmp_path / "secrets.json")
    store.set("GROQ_API_KEY", "from-store")
    monkeypatch.setenv("GROQ_API_KEY", "from-env")
    monkeypatch.setenv("OPENROUTER_API_KEY", "env-only")

    resolved = resolve_secrets(store)
    assert resolved["GROQ_API_KEY"] == "from-store"
    assert resolved["OPENROUTER_API_KEY"] == "env-only"
    assert set(resolved) <= set(SECRET_KEYS)
    assert secret_presence(store)["GROQ_API_KEY"] is True
    assert secret_presence(store)["SLACK_SIGNING_SECRET"] is False


def test_build_secret_store_modes(tmp_path):
    assert build_secret_store(tmp_path, mode="env").backend == "env"
    assert build_secret_store(tmp_path, mode="file").backend == "file"
    with pytest.raises(Exception):
        build_secret_store(tmp_path, mode="nonsense")


def test_apply_stored_settings_overlays_non_secret_values(tmp_path):
    settings = load_settings(data_dir=tmp_path)
    store = FileSecretStore(tmp_path / "secrets.json")
    store.set("GATHER_MODEL_CHAIN", "groq:openai/gpt-oss-120b")
    store.set("GATHER_MAX_CALLS", "7")
    store.set("GATHER_AUTO_APPROVE", "true")
    updated = apply_stored_settings(settings, store)
    assert [spec.label for spec in updated.chain] == ["groq:openai/gpt-oss-120b"]
    assert updated.max_calls_per_run == 7
    assert updated.auto_approve is True


def test_onboarding_state_is_read_from_the_secret_store(tmp_path):
    settings = load_settings(data_dir=tmp_path)
    store = FileSecretStore(tmp_path / "secrets.json")
    store.set("GATHER_ONBOARDED", "1")
    updated = apply_stored_settings(settings, store)
    assert updated.onboarded is True
    # The workspace name must never override the tenant key (org_name).
    store.set("GATHER_ORG_NAME", "Workstation")
    assert apply_stored_settings(settings, store).org_name == settings.org_name


# --------------------------------------------------------------------------- #
# Runs / cancellation / lock
# --------------------------------------------------------------------------- #


def test_run_registry_cancels_at_a_checkpoint():
    events: list[tuple[str, dict]] = []
    registry = RunRegistry(emit=lambda name, data: events.append((name, data)))

    def work():
        registry.checkpoint("extract", {"chunk": 1})
        registry.checkpoint("extract", {"chunk": 2})

    handle = registry.create("meeting", "thread-1")
    assert registry.cancel(handle.run_id) is True
    with pytest.raises(RunCancelled):
        with registry.bind(handle):
            work()
    registry.finish(handle, "cancelled")
    assert handle.status == "cancelled"
    assert [name for name, _ in events] == ["run.started", "run.cancelling", "run.finished"]
    assert registry.cancel(handle.run_id) is False


def test_engine_lock_is_exclusive(tmp_path):
    first = EngineLock(tmp_path / "engine.lock")
    second = EngineLock(tmp_path / "engine.lock")
    assert first.acquire() is True
    assert second.acquire() is False
    first.release()
    assert second.acquire() is True
    second.release()


# --------------------------------------------------------------------------- #
# Handlers
# --------------------------------------------------------------------------- #


def test_settings_update_refreshes_live_connector_credentials(handlers, runtime):
    assert runtime.gmail.refresh_token is None
    call(
        handlers,
        "settings.update",
        {
            "secrets": {
                "GOOGLE_CLIENT_ID": "client-id",
                "GOOGLE_CLIENT_SECRET": "client-secret",
                "GMAIL_REFRESH_TOKEN": "refresh-token",
            }
        },
    )
    assert runtime.gmail.client_id == "client-id"
    assert runtime.gmail.client_secret == "client-secret"
    assert runtime.gmail.refresh_token == "refresh-token"


def test_reconnect_can_clear_a_stale_sync_error(runtime):
    connection_id = runtime.store.upsert_connection(
        runtime.org_id, "gmail", mode="user", user_id="u1", scopes=[]
    )
    runtime.store.mark_sync_failure(
        connection_id,
        error="Client error '403 Forbidden'",
        next_sync_at="2026-11-23T16:00:25+00:00",
        backoff_until="2026-11-23T16:00:25+00:00",
    )
    assert runtime.store.get_connection(connection_id)["last_error"]

    runtime.store.clear_connection_error(
        connection_id, next_sync_at="2026-11-24T20:00:00+00:00"
    )
    row = runtime.store.get_connection(connection_id)
    assert row["last_error"] is None
    assert row["error_count"] == 0
    assert row["backoff_until"] is None
    assert row["status"] == "active"
    assert row["next_sync_at"] == "2026-11-24T20:00:00+00:00"


def test_onboarding_complete_persists_and_reset_clears(handlers, tmp_path, runtime):
    status = call(handlers, "onboarding.status")
    assert status["onboarded"] is False
    assert status["needs_onboarding"] is True

    # Existing data must stay attached to the same tenant after a workspace
    # name is typed into onboarding.
    connection_id = runtime.store.upsert_connection(runtime.org_id, "fireflies", mode="org")

    done = call(handlers, "onboarding.complete", {"org_name": "Workstation"})
    assert done == {"ok": True, "onboarded": True, "persisted": True}
    assert handlers.secret_store.get("GATHER_ONBOARDED") == "1"
    # The typed workspace label must not become the tenant key.
    assert handlers.secret_store.get("GATHER_ORG_NAME") is None
    assert runtime.store.get_org(runtime.org_id)["name"] != "Workstation"

    # Simulate a restart: reload settings from the same data dir.
    reloaded = apply_stored_settings(load_settings(data_dir=tmp_path), handlers.secret_store)
    assert reloaded.onboarded is True
    assert reloaded.org_name != "Workstation"
    assert runtime.store.ensure_org(reloaded.org_name) == runtime.org_id
    assert any(row["id"] == connection_id for row in runtime.store.connections(runtime.org_id, status=None))

    status = call(handlers, "onboarding.status")
    assert status["onboarded"] is True
    assert status["needs_onboarding"] is False

    reset = call(handlers, "onboarding.reset")
    assert reset["ok"] is True
    assert reset["onboarded"] is False
    assert handlers.secret_store.get("GATHER_ONBOARDED") is None
    assert handlers.secret_store.get("GATHER_ORG_NAME") is None
    assert call(handlers, "onboarding.status")["onboarded"] is False


def test_onboarding_status_survives_a_read_only_secret_store(runtime):
    """A completed install must not look new just because the backend is read-only."""
    from gather.secrets import EnvSecretStore

    readonly = Handlers(runtime, events=runtime.events, secrets=EnvSecretStore(), keys={})
    assert call(readonly, "onboarding.complete")["persisted"] is False
    assert call(readonly, "onboarding.status")["onboarded"] is True


def test_health_and_settings_never_expose_secret_values(handlers):
    health = call(handlers, "health")
    assert health["ok"] is True
    assert health["fixture_mode"] is True
    assert health["state"] == "ready"
    assert health["providers"] == ["fireflies", "gmail"]
    assert health["ingress"] in {"http", "stdio", "poll"}

    updated = call(
        handlers,
        "settings.update",
        {"secrets": {"GROQ_API_KEY": "super-secret-value"}},
    )
    assert updated["updated"] == ["GROQ_API_KEY"]
    assert updated["restart_required"] is True

    settings = call(handlers, "settings.get")
    assert settings["secrets"]["GROQ_API_KEY"] is True
    assert "super-secret-value" not in json.dumps(settings)
    assert handlers.secret_store.get("GROQ_API_KEY") == "super-secret-value"


def test_settings_update_validates_keys_atomically(handlers):
    with pytest.raises(RpcError):
        call(handlers, "settings.update", {"secrets": {"NOT_A_KEY": "x"}})
    with pytest.raises(RpcError):
        call(handlers, "settings.update", {"settings": {"GATHER_MODEL_CHAIN": ""}})
    with pytest.raises(RpcError):
        call(handlers, "settings.update", {"settings": {"GATHER_MODEL_CHAIN": "nonsense"}})
    accepted = call(
        handlers,
        "settings.update",
        {"settings": {"GATHER_MODEL_CHAIN": "groq:openai/gpt-oss-120b"}},
    )
    assert accepted["settings"] == ["GATHER_MODEL_CHAIN"]
    # A bad trailing key must not leave an earlier write behind.
    with pytest.raises(RpcError):
        call(
            handlers,
            "settings.update",
            {
                "secrets": {"GROQ_API_KEY": "half-written"},
                "settings": {"NOT_A_SETTING": "1"},
            },
        )
    assert handlers.secret_store.get("GROQ_API_KEY") is None


def test_connections_roundtrip_and_reactivation(handlers, runtime):
    added = call(handlers, "connections.add", {"provider": "fireflies", "mode": "org"})
    connection_id = added["connection"]["id"]
    assert added["connection"]["status"] == "active"

    listed = call(handlers, "connections.list")
    assert any(row["id"] == connection_id for row in listed["connections"])
    assert listed["providers"] == ["fireflies", "gmail"]

    call(handlers, "connections.remove", {"connection_id": connection_id})
    assert runtime.store.get_connection(connection_id)["status"] == "disabled"

    again = call(handlers, "connections.add", {"provider": "fireflies", "mode": "org"})
    assert again["connection"]["id"] == connection_id
    assert runtime.store.get_connection(connection_id)["status"] == "active"

    with pytest.raises(RpcError):
        call(handlers, "connections.add", {"provider": "yahoo"})
    with pytest.raises(RpcError):
        call(handlers, "connections.add", {"provider": "gmail", "mode": "user"})


def test_explicit_secret_mapping_never_falls_back_to_environment(tmp_path, monkeypatch):
    from gather.llm import FixtureLLM, build_llm

    monkeypatch.setenv("GROQ_API_KEY", "from-the-environment")
    settings = load_settings(data_dir=tmp_path)
    llm = build_llm(settings, log=lambda _m: None, api_keys={})
    assert isinstance(llm, FixtureLLM)


def test_connection_test_and_sync_now_are_safe_without_credentials(handlers, runtime):
    added = call(handlers, "connections.add", {"provider": "fireflies", "mode": "org"})
    connection_id = added["connection"]["id"]

    tested = call(handlers, "connections.test", {"connection_id": connection_id})
    assert tested["ok"] is False
    assert "API key" in tested["error"]

    synced = call(handlers, "sync.now")
    assert synced["accepted"] is True
    assert synced["events"] == 0

    usage = call(handlers, "models.usage")
    assert usage["fixture_mode"] is True
    assert usage["usage"]["calls"] == 0


def test_task_list_get_and_update(handlers, runtime):
    task = runtime.store.create_task(
        runtime.org_id,
        title="Send the deck",
        owner_email="priya@acme.test",
        due_date=date.today().isoformat(),
    )

    assert call(handlers, "tasks.list")["tasks"][0]["id"] == task["id"]
    got = call(handlers, "tasks.get", {"task_id": task["id"]})
    assert got["task"]["title"] == "Send the deck"
    assert got["events"] == []

    updated = call(
        handlers,
        "tasks.update",
        {"task_id": task["id"], "status": "done", "due_date": "2026-12-01"},
    )
    assert updated["task"]["status"] == "done"
    assert updated["task"]["due_date"] == "2026-12-01"
    assert updated["events"][-1]["kind"] == "updated"
    assert updated["events"][-1]["actor"] == "human"

    with pytest.raises(RpcError):
        call(handlers, "tasks.update", {"task_id": task["id"], "status": "nonsense"})
    with pytest.raises(RpcError):
        call(handlers, "tasks.update", {"task_id": task["id"], "due_date": "not-a-date"})
    with pytest.raises(RpcError):
        call(handlers, "tasks.get", {"task_id": "missing"})


def test_meeting_ingest_approve_is_end_to_end_and_idempotent(handlers, runtime):
    ingested = call(handlers, "meetings.ingest", {"external_id": "ff_fixture"})
    assert ingested["thread_id"].startswith("meeting-")
    assert ingested["proposal_ids"], "ingest should create an approval proposal"

    proposals = call(handlers, "proposals.list")["proposals"]
    assert len(proposals) == 1
    proposal = proposals[0]
    assert proposal["kind"] == "task_approval"

    approved = call(handlers, "proposals.approve", {"proposal_id": proposal["id"]})
    assert approved["already"] is False
    assert len(approved["created"]) >= 1
    assert call(handlers, "tasks.list")["tasks"]

    again = call(handlers, "proposals.approve", {"proposal_id": proposal["id"]})
    assert again["already"] is True
    assert again["status"] == "approved"

    # The meeting is listed and resolvable from the tasks it produced.
    meetings = call(handlers, "meetings.list")["meetings"]
    assert len(meetings) == 1
    detail = call(handlers, "meetings.get", {"meeting_id": meetings[0]["id"]})
    assert detail["meeting"]["transcript"]
    assert detail["tasks"]


def test_approve_with_selected_indices_creates_only_those_tasks(handlers, runtime):
    call(handlers, "meetings.ingest", {"external_id": "ff_fixture"})
    proposal = call(handlers, "proposals.list")["proposals"][0]
    cards = proposal["payload"]["tasks"]
    assert len(cards) >= 2, "fixture meeting should propose several commitments"

    approved = call(
        handlers,
        "proposals.approve",
        {"proposal_id": proposal["id"], "approved_indices": [0]},
    )
    assert approved["already"] is False
    assert len(approved["created"]) == 1
    assert approved["created"][0]["title"] == cards[0]["title"]


def test_approve_rejects_non_integer_indices(handlers, runtime):
    call(handlers, "meetings.ingest", {"external_id": "ff_fixture"})
    proposal = call(handlers, "proposals.list")["proposals"][0]
    with pytest.raises(RpcError):
        call(
            handlers,
            "proposals.approve",
            {"proposal_id": proposal["id"], "approved_indices": ["nope"]},
        )
    assert call(handlers, "proposals.list")["proposals"][0]["status"] == "pending"


def test_approve_recovers_when_the_graph_finished_before_the_decision(handlers, runtime):
    from gather.graphs import resume_meeting

    call(handlers, "meetings.ingest", {"external_id": "ff_fixture"})
    proposal = call(handlers, "proposals.list")["proposals"][0]

    # Simulate a crash between "graph resumed" and "proposal marked decided".
    resume_meeting(
        runtime.meeting_graph,
        proposal["thread_id"],
        approved=[
            task["fingerprint"]
            for task in (proposal.get("payload") or {}).get("tasks", [])
        ],
    )

    recovered = call(handlers, "proposals.approve", {"proposal_id": proposal["id"]})
    assert recovered["status"] == "approved"
    assert recovered.get("recovered") is True
    assert recovered["created"]
    assert runtime.store.get_proposal(proposal["id"])["status"] == "approved"
    # And a third call is simply idempotent.
    assert call(handlers, "proposals.approve", {"proposal_id": proposal["id"]})["already"] is True


def test_meeting_ingest_reject_leaves_no_tasks(handlers, runtime):
    call(handlers, "meetings.ingest", {"external_id": "ff_fixture"})
    proposal = call(handlers, "proposals.list")["proposals"][0]
    rejected = call(handlers, "proposals.reject", {"proposal_id": proposal["id"]})
    assert rejected["status"] == "rejected"
    assert call(handlers, "tasks.list")["tasks"] == []
    again = call(handlers, "proposals.reject", {"proposal_id": proposal["id"]})
    assert again["already"] is True


def test_manual_transcript_ingest_does_not_need_a_provider(handlers, runtime):
    ingested = call(
        handlers,
        "meetings.ingest",
        {
            "external_id": "manual-1",
            "provider": "fireflies",
            "title": "Pasted standup",
            "transcript": (
                "Priya: I'll send the updated pricing deck to Northwind by Friday.\n"
                "Marcus: I will book the security review with Dana tomorrow."
            ),
            "participants": [{"name": "Priya", "email": "priya@acme.test"}],
        },
    )
    assert ingested["proposal_ids"]
    meeting_id = call(handlers, "meetings.list")["meetings"][0]["id"]
    detail = call(handlers, "meetings.get", {"meeting_id": meeting_id})
    assert detail["meeting"]["title"] == "Pasted standup"
    assert detail["meeting"]["transcript"].startswith("Priya:")


def test_runs_are_listed_and_cancellable(handlers, runtime):
    registry = runtime.runs
    handle = registry.create("meeting", "thread-x")
    listed = call(handlers, "runs.list")
    assert any(run["run_id"] == handle.run_id for run in listed["runs"])
    assert call(handlers, "runs.get", {"run_id": handle.run_id})["run"]["status"] == "running"

    cancelled = call(handlers, "runs.cancel", {"run_id": handle.run_id})
    assert cancelled["cancelled"] is True
    assert cancelled["run"]["status"] == "running"  # cooperative: marked, not killed
    registry.finish(handle, "cancelled")

    assert call(handlers, "runs.cancel", {"run_id": "missing"})["cancelled"] is False


# --------------------------------------------------------------------------- #
# Scheduler
# --------------------------------------------------------------------------- #


def test_scheduler_tick_emits_status_and_sweep(runtime):
    today = date.today()
    runtime.store.create_task(
        runtime.org_id,
        title="Overdue item",
        owner_email="priya@acme.test",
        due_date=(today - timedelta(days=2)).isoformat(),
    )
    scheduler = Scheduler(
        runtime,
        runtime.events,
        poll_interval_s=0.05,
        sweep_interval_s=3600,
        log=lambda _message: None,
    )

    result = scheduler.run_now(force_sweep=True)
    assert result["sweep"] is not None
    assert result["sweep"]["nudges"] == 1
    names = [event["event"] for event in runtime.events.recent()]
    assert "scheduler.tick" in names
    assert "sweep.finished" in names
    assert "proposal.created" in names

    handlers = Handlers(
        runtime,
        events=runtime.events,
        secrets=None,
        keys={},
        scheduler=scheduler,
    )
    assert call(handlers, "scheduler.status")["enabled"] is True
    assert call(handlers, "scheduler.status")["ticks"] == 1


def test_scheduler_thread_starts_and_stops(runtime):
    scheduler = Scheduler(runtime, runtime.events, poll_interval_s=0.05, log=lambda _m: None)
    scheduler.start()
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline:
        if any(event["event"] == "scheduler.tick" for event in runtime.events.recent()):
            break
        time.sleep(0.02)
    scheduler.stop(timeout=2.0)
    assert scheduler.running is False
    assert any(event["event"] == "scheduler.stopped" for event in runtime.events.recent())


# --------------------------------------------------------------------------- #
# The real contract: a subprocess speaking JSON lines
# --------------------------------------------------------------------------- #


class EngineProcess:
    def __init__(self, data_dir: Path) -> None:
        env = dict(os.environ)
        env["PYTHONUNBUFFERED"] = "1"
        self.proc = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "gather.engine",
                "--data-dir",
                str(data_dir),
                "--fixture",
                "--no-scheduler",
                "--secrets",
                "file",
            ],
            cwd=ROOT,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            env=env,
        )
        self.events: list[dict] = []
        self._next_id = 0
        # A reader thread avoids a classic select()/TextIOWrapper trap: extra
        # lines can sit in the Python buffer while the raw fd reports "not
        # ready", which looks like a hung engine even though it replied.
        self._lines: queue.Queue[str] = queue.Queue()
        threading.Thread(target=self._pump, name="engine-test-reader", daemon=True).start()

    def _pump(self) -> None:
        assert self.proc.stdout is not None
        for line in self.proc.stdout:
            self._lines.put(line)

    def read(self, timeout: float = 10.0) -> dict:
        try:
            line = self._lines.get(timeout=timeout)
        except queue.Empty:
            raise AssertionError("timed out waiting for the engine") from None
        message = json.loads(line)  # every stdout line must be protocol JSON
        if "event" in message:
            self.events.append(message)
        return message

    def wait_for(self, predicate, timeout: float = 10.0) -> dict:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            message = self.read(timeout=max(0.1, deadline - time.monotonic()))
            if predicate(message):
                return message
        raise AssertionError("condition not met before timeout")

    def request(self, method: str, params: dict | None = None) -> dict:
        self._next_id += 1
        request_id = f"r{self._next_id}"
        assert self.proc.stdin is not None
        self.proc.stdin.write(
            json.dumps({"id": request_id, "method": method, "params": params or {}}) + "\n"
        )
        self.proc.stdin.flush()
        return self.wait_for(lambda message: message.get("id") == request_id)

    def write_raw(self, line: str) -> None:
        assert self.proc.stdin is not None
        self.proc.stdin.write(line)
        self.proc.stdin.flush()

    def close(self) -> None:
        if self.proc.poll() is None:
            try:
                self.request("engine.shutdown")
            except Exception:
                pass
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=5)


@pytest.fixture
def engine(tmp_path):
    process = EngineProcess(tmp_path)
    try:
        yield process
    finally:
        process.close()


def test_engine_subprocess_speaks_json_lines_end_to_end(engine):
    ready = engine.wait_for(lambda message: message.get("event") == "engine.ready")
    assert ready["data"]["fixture_mode"] is True
    assert ready["data"]["scheduler"] is False

    health = engine.request("health")
    assert health["ok"] is True
    assert health["result"]["ingress"] == "stdio"
    assert health["result"]["schema_version"] >= 2

    ingested = engine.request("meetings.ingest", {"external_id": "ff_fixture"})
    assert ingested["ok"] is True
    proposal_ids = ingested["result"]["proposal_ids"]
    assert proposal_ids

    proposals = engine.request("proposals.list")["result"]["proposals"]
    assert [proposal["id"] for proposal in proposals] == proposal_ids

    approved = engine.request("proposals.approve", {"proposal_id": proposal_ids[0]})
    assert approved["ok"] is True
    assert approved["result"]["already"] is False
    created = approved["result"]["created"]
    assert created

    tasks = engine.request("tasks.list")["result"]["tasks"]
    assert len(tasks) == len(created)

    # The assistant exposes read-only tools and answers from local data.
    tools = engine.request("agent.tools")
    assert tools["ok"] is True
    assert any(tool["name"] == "workspace_overview" for tool in tools["result"]["tools"])
    chat = engine.request("agent.chat", {"message": "what tasks are open?"})
    assert chat["ok"] is True
    assert chat["result"]["tool_calls"]
    assert "open" in chat["result"]["answer"].lower()

    # Unknown methods and malformed input get tidy errors, not a dead engine.
    unknown = engine.request("no.such.method")
    assert unknown["ok"] is False
    assert unknown["error"]["code"] == "method_not_found"

    engine.write_raw("this is not json\n")
    bad = engine.wait_for(lambda message: message.get("id") is None and "error" in message)
    assert bad["error"]["code"] == "bad_request"

    shutdown = engine.request("engine.shutdown")
    assert shutdown["ok"] is True
    engine.proc.wait(timeout=10)
    assert engine.proc.returncode == 0

    assert any(event["event"] == "engine.ready" for event in engine.events)
    assert any(event["event"] == "proposal.created" for event in engine.events)


def test_engine_refuses_a_second_instance(tmp_path):
    lock = EngineLock(tmp_path / "engine.lock")
    assert lock.acquire() is True
    before = dict(os.environ)
    try:
        assert engine_main(["--data-dir", str(tmp_path), "--fixture", "--no-scheduler"]) == 2
    finally:
        os.environ.clear()
        os.environ.update(before)
        lock.release()


def test_onboarding_survives_an_engine_restart(tmp_path):
    """The exact desktop contract: finish onboarding, restart, stay onboarded."""
    first = EngineProcess(tmp_path)
    try:
        first.wait_for(lambda message: message.get("event") == "engine.ready")
        status = first.request("onboarding.status")["result"]
        assert status["onboarded"] is False
        assert status["needs_onboarding"] is True

        done = first.request("onboarding.complete", {"org_name": "Workstation"})["result"]
        assert done["onboarded"] is True
        assert done["persisted"] is True
    finally:
        first.close()

    second = EngineProcess(tmp_path)
    try:
        second.wait_for(lambda message: message.get("event") == "engine.ready")
        status = second.request("onboarding.status")["result"]
        assert status["onboarded"] is True
        assert status["needs_onboarding"] is False

        reset = second.request("onboarding.reset")["result"]
        assert reset["onboarded"] is False
        assert second.request("onboarding.status")["result"]["onboarded"] is False
    finally:
        second.close()
