"""Polling ingestion: connector windows, cursor repair, scheduling and backoff."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

import gather.connectors.fireflies as fireflies_module
import gather.connectors.gmail as gmail_module
import gather.poller.dispatch as dispatch_module
from gather.connectors.base import (
    AuthExpired,
    CapabilityHub,
    Connection,
    RateLimited,
    SyncError,
)
from gather.connectors.fireflies import FirefliesConnector
from gather.connectors.gmail import GmailConnector
from gather.api import create_app
from gather.config import load_settings
from gather.db import Store
from gather.domain import Event, EventKind, Message, ObjectRef, SyncBatch
from gather.poller import PollSource

NOW = datetime(2026, 11, 18, 12, 0, tzinfo=timezone.utc)


class FakeResponse:
    def __init__(self, payload: dict, status_code: int = 200, headers: dict | None = None):
        self._payload = payload
        self.status_code = status_code
        self.headers = headers or {}

    def json(self) -> dict:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def _event(provider: str = "fake", message_id: str = "m1") -> Event:
    return Event(
        id=f"{provider}:{message_id}",
        provider=provider,
        kind=EventKind.MESSAGE_RECEIVED,
        occurred_at=NOW,
        payload={"message_id": message_id},
    )


# --------------------------------------------------------------------------- #
# Fireflies
# --------------------------------------------------------------------------- #


def test_fireflies_sync_lists_a_window_and_builds_stable_ids(monkeypatch):
    calls: list[dict] = []
    pages = {
        0: [
            {"id": "ff_1", "title": "Kickoff", "date": 1763000000000, "participants": []},
            {"id": "ff_2", "title": "Review", "date": 1763100000000, "participants": []},
        ]
    }

    def fake_post(url, headers=None, json=None, timeout=None):
        calls.append(json)
        return FakeResponse({"data": {"transcripts": pages.get(json["variables"]["skip"], [])}})

    monkeypatch.setattr(fireflies_module, "httpx", SimpleNamespace(post=fake_post))
    connector = FirefliesConnector(api_key="key")
    conn = Connection(id="c1", org_id="o1", provider="fireflies")

    batch = connector.sync(conn)

    assert [event.id for event in batch.events] == [
        "fireflies:ff_1:1763000000000",
        "fireflies:ff_2:1763100000000",
    ]
    assert batch.events[0].subject.id == "ff_1"
    assert batch.cursor and batch.cursor.endswith("Z")
    variables = calls[0]["variables"]
    assert "fromDate" in calls[0]["query"]
    assert set(variables) == {"from", "to", "limit", "skip"}
    assert variables["skip"] == 0


def test_fireflies_sync_paginates_until_short_page(monkeypatch):
    pages = {
        0: [{"id": f"ff_{i}", "date": 1763000000000 + i} for i in range(50)],
        50: [{"id": "ff_last", "date": 1764000000000}],
    }
    mono = []

    def fake_post(url, headers=None, json=None, timeout=None):
        mono.append(json["variables"]["skip"])
        return FakeResponse({"data": {"transcripts": pages.get(json["variables"]["skip"], [])}})

    monkeypatch.setattr(fireflies_module, "httpx", SimpleNamespace(post=fake_post))
    batch = FirefliesConnector(api_key="key").sync(
        Connection(id="c1", org_id="o1", provider="fireflies")
    )

    assert len(batch.events) == 51
    assert mono == [0, 50]


def test_fireflies_rate_limit_carries_retry_after(monkeypatch):
    monkeypatch.setattr(
        fireflies_module,
        "httpx",
        SimpleNamespace(
            post=lambda *a, **k: FakeResponse({}, status_code=429, headers={"retry-after": "7"})
        ),
    )
    with pytest.raises(RateLimited) as caught:
        FirefliesConnector(api_key="key").sync(
            Connection(id="c1", org_id="o1", provider="fireflies")
        )
    assert caught.value.retry_after_s == 7


def test_fireflies_without_a_key_is_a_noop():
    batch = FirefliesConnector(api_key="").sync(
        Connection(id="c1", org_id="o1", provider="fireflies")
    )
    assert batch.events == []


# --------------------------------------------------------------------------- #
# Gmail
# --------------------------------------------------------------------------- #


def _gmail_get(routes):
    calls: list[tuple[str, dict]] = []

    def fake_get(url, headers=None, params=None, timeout=None):
        calls.append((url, params or {}))
        for suffix, response in routes.items():
            if url.endswith(suffix):
                return response(params or {}) if callable(response) else response
        raise AssertionError(f"unexpected url {url}")

    return fake_get, calls


def test_gmail_sync_without_a_cursor_requests_a_backfill(monkeypatch):
    fake_get, _ = _gmail_get({})
    monkeypatch.setattr(gmail_module, "httpx", SimpleNamespace(get=fake_get))

    batch = GmailConnector(access_token="tok").sync(
        Connection(id="c1", org_id="o1", provider="gmail")
    )
    assert batch.reset is True
    assert batch.cursor is None


def test_gmail_mints_a_token_from_the_refresh_token_without_a_static_one(monkeypatch):
    """OAuth-only setups start with no access token; ``_token`` must mint one."""
    posts: list[dict] = []

    def fake_post(url, data=None, timeout=None):
        posts.append(data or {})
        return FakeResponse({"access_token": "minted", "expires_in": 3600})

    fake_get, _ = _gmail_get({})
    monkeypatch.setattr(
        gmail_module, "httpx", SimpleNamespace(get=fake_get, post=fake_post)
    )

    connector = GmailConnector(
        client_id="cid", client_secret="secret", refresh_token="refresh"
    )
    conn = Connection(id="c1", org_id="o1", provider="gmail")
    assert connector._token(conn) == "minted"
    assert posts[0]["grant_type"] == "refresh_token"
    # The minted token is cached until it expires.
    assert connector._token(conn) == "minted"
    assert len(posts) == 1


def test_gmail_sync_dedupes_history_and_advances_the_cursor(monkeypatch):
    def history(params):
        assert params["startHistoryId"] == "100"
        return FakeResponse(
            {
                "historyId": "105",
                "history": [
                    {"messagesAdded": [{"message": {"id": "m1"}}]},
                    {"messagesAdded": [{"message": {"id": "m1"}}]},
                    {"messagesAdded": [{"message": {"id": "m2"}}]},
                ],
            }
        )

    fake_get, _ = _gmail_get({"/history": history})
    monkeypatch.setattr(gmail_module, "httpx", SimpleNamespace(get=fake_get))

    batch = GmailConnector(access_token="tok").sync(
        Connection(id="c1", org_id="o1", provider="gmail", cursor="100")
    )
    assert [event.id for event in batch.events] == ["gmail:m1", "gmail:m2"]
    assert batch.cursor == "105"
    assert batch.reset is False


def test_gmail_404_requests_a_full_resync(monkeypatch):
    fake_get, _ = _gmail_get({"/history": FakeResponse({}, status_code=404)})
    monkeypatch.setattr(gmail_module, "httpx", SimpleNamespace(get=fake_get))

    batch = GmailConnector(access_token="tok").sync(
        Connection(id="c1", org_id="o1", provider="gmail", cursor="old")
    )
    assert batch.reset is True
    assert batch.cursor is None


def test_gmail_429_surfaces_retry_after(monkeypatch):
    fake_get, _ = _gmail_get(
        {"/history": FakeResponse({}, status_code=429, headers={"retry-after": "9"})}
    )
    monkeypatch.setattr(gmail_module, "httpx", SimpleNamespace(get=fake_get))

    with pytest.raises(RateLimited) as caught:
        GmailConnector(access_token="tok").sync(
            Connection(id="c1", org_id="o1", provider="gmail", cursor="100")
        )
    assert caught.value.retry_after_s == 9


def _gmail_403(reason: str, status: str, message: str) -> dict:
    return {
        "error": {
            "code": 403,
            "message": message,
            "errors": [{"message": message, "domain": "global", "reason": reason}],
            "status": status,
        }
    }


def test_gmail_403_rate_limit_is_retryable_not_a_reauth(monkeypatch):
    body = _gmail_403(
        "userRateLimitExceeded",
        "RESOURCE_EXHAUSTED",
        "User-rate limit exceeded. Retry after 2026-11-24T20:05:00Z",
    )
    fake_get, _ = _gmail_get({"/history": FakeResponse(body, status_code=403)})
    monkeypatch.setattr(gmail_module, "httpx", SimpleNamespace(get=fake_get))

    with pytest.raises(RateLimited) as caught:
        GmailConnector(access_token="tok").sync(
            Connection(id="c1", org_id="o1", provider="gmail", cursor="100")
        )
    assert "rate limit" in str(caught.value).lower()


def test_gmail_403_missing_scope_asks_the_user_to_reconnect(monkeypatch):
    body = _gmail_403(
        "insufficientPermissions",
        "PERMISSION_DENIED",
        "Request had insufficient authentication scopes.",
    )
    fake_get, _ = _gmail_get({"/history": FakeResponse(body, status_code=403)})
    monkeypatch.setattr(gmail_module, "httpx", SimpleNamespace(get=fake_get))

    with pytest.raises(AuthExpired) as caught:
        GmailConnector(access_token="tok").sync(
            Connection(id="c1", org_id="o1", provider="gmail", cursor="100")
        )
    assert "Reconnect Gmail" in str(caught.value)


def test_gmail_403_disabled_api_is_an_actionable_sync_error(monkeypatch):
    body = _gmail_403(
        "accessNotConfigured",
        "PERMISSION_DENIED",
        "Gmail API has not been used in project 123 before or it is disabled.",
    )
    fake_get, _ = _gmail_get({"/history": FakeResponse(body, status_code=403)})
    monkeypatch.setattr(gmail_module, "httpx", SimpleNamespace(get=fake_get))

    with pytest.raises(SyncError) as caught:
        GmailConnector(access_token="tok").sync(
            Connection(id="c1", org_id="o1", provider="gmail", cursor="100")
        )
    assert "Enable it" in str(caught.value)


def test_gmail_update_credentials_drops_a_cached_access_token():
    connector = GmailConnector(
        access_token="old", client_id="cid", client_secret="secret", refresh_token="old-refresh"
    )
    connector._token_expires_at = 9_999_999_999
    connector.update_credentials(
        client_id="cid2", client_secret="secret2", refresh_token="new-refresh"
    )
    assert connector.access_token is None
    assert connector._token_expires_at == 0
    assert (connector.client_id, connector.client_secret, connector.refresh_token) == (
        "cid2",
        "secret2",
        "new-refresh",
    )


def test_gmail_backfill_profiles_first_then_lists_recent_mail(monkeypatch):
    order: list[str] = []

    def profile(params):
        order.append("profile")
        return FakeResponse({"historyId": "200"})

    def messages(params):
        order.append("messages")
        assert params["q"].startswith("after:")
        assert "-in:sent" in params["q"]
        return FakeResponse({"messages": [{"id": "m1"}, {"id": "m2"}]})

    fake_get, _ = _gmail_get({"/profile": profile, "/messages": messages})
    monkeypatch.setattr(gmail_module, "httpx", SimpleNamespace(get=fake_get))

    batch = GmailConnector(access_token="tok").backfill(
        Connection(id="c1", org_id="o1", provider="gmail"),
        NOW - timedelta(days=14),
    )
    assert order == ["profile", "messages"]
    assert [event.id for event in batch.events] == ["gmail:m1", "gmail:m2"]
    assert batch.cursor == "200"


# --------------------------------------------------------------------------- #
# Poller scheduling
# --------------------------------------------------------------------------- #


class FakeConnector:
    provider = "fake"
    capabilities = frozenset()
    poll_interval_s = 120
    push_capable = False

    def __init__(self, batch=None, backfill=None, error=None):
        self.batch = batch or SyncBatch()
        self.backfill_batch = backfill
        self.error = error
        self.sync_calls = 0
        self.backfill_calls = 0
        self.since: datetime | None = None

    def sync(self, conn, limit=50):
        self.sync_calls += 1
        if self.error:
            raise self.error
        return self.batch

    def backfill(self, conn, since, limit=50):
        self.backfill_calls += 1
        self.since = since
        return self.backfill_batch or SyncBatch()


@pytest.fixture()
def poll_env(tmp_path):
    store = Store(tmp_path / "gather.db")
    org = store.ensure_org("Acme")
    hub = CapabilityHub(store)
    store.upsert_connection(org, "fake")
    yield store, org, hub
    store.close()


def _poller(store, hub, handled=None):
    return PollSource(
        store,
        hub,
        on_event=(handled.append if handled is not None else None),
        clock=lambda: NOW,
        jitter=lambda: 1.0,
    )


def test_poller_records_events_and_schedules_the_next_tick(poll_env):
    store, org, hub = poll_env
    connector = FakeConnector(batch=SyncBatch(events=[_event("fake", "1")], cursor="c1"))
    hub.register(connector)
    handled: list[Event] = []

    outcome = _poller(store, hub, handled).run_once(org, now=NOW)[0]

    assert outcome.events == 1
    assert outcome.cursor == "c1"
    assert [event.id for event in handled] == ["fake:1"]
    assert store.seen_event("fake", "fake:1")
    row = store.connections(org)[0]
    assert row["cursor"] == "c1"
    assert row["error_count"] == 0
    assert row["last_sync_at"] == "2026-11-18T12:00:00+00:00"
    assert outcome.next_sync_at == "2026-11-18T12:02:00+00:00"


def test_poller_skips_events_it_has_already_handled(poll_env):
    store, org, hub = poll_env
    event = _event("fake", "1")
    store.record_event(event, event.payload)
    connector = FakeConnector(batch=SyncBatch(events=[event, _event("fake", "2")], cursor="c1"))
    hub.register(connector)
    handled: list[Event] = []

    outcome = _poller(store, hub, handled).run_once(org, now=NOW)[0]

    assert outcome.events == 1
    assert outcome.duplicates == 1
    assert [item.id for item in handled] == ["fake:2"]


def test_poller_skips_duplicates_within_one_batch(poll_env):
    store, org, hub = poll_env
    event = _event("fake", "1")
    connector = FakeConnector(batch=SyncBatch(events=[event, event], cursor="c1"))
    hub.register(connector)
    handled: list[Event] = []

    outcome = _poller(store, hub, handled).run_once(org, now=NOW)[0]

    assert outcome.events == 1
    assert outcome.duplicates == 1
    assert len(handled) == 1


def test_failed_dispatch_keeps_the_cursor_and_backs_off(poll_env):
    store, org, hub = poll_env
    connector = FakeConnector(batch=SyncBatch(events=[_event("fake", "1")], cursor="c1"))
    hub.register(connector)
    poller = PollSource(
        store,
        hub,
        on_event=lambda event: (_ for _ in ()).throw(ValueError("graph exploded")),
        clock=lambda: NOW,
        jitter=lambda: 1.0,
    )

    outcome = poller.run_once(org, now=NOW)[0]

    assert "graph exploded" in (outcome.error or "")
    row = store.connections(org)[0]
    assert row["cursor"] is None
    assert row["error_count"] == 1
    assert row["last_error"] == "graph exploded"
    assert row["backoff_until"] == "2026-11-18T12:01:00+00:00"
    assert not store.seen_event("fake", "fake:1")

    # Still backing off 30 seconds later; due again after the minute.
    assert poller.run_once(org, now=NOW + timedelta(seconds=30)) == []
    assert len(poller.run_once(org, now=NOW + timedelta(seconds=61))) == 1


def test_backoff_doubles_on_repeated_failures(poll_env):
    store, org, hub = poll_env
    connector = FakeConnector(error=SyncError("nope"))
    hub.register(connector)
    poller = _poller(store, hub)

    poller.run_once(org, now=NOW)
    row = store.connections(org)[0]
    assert row["error_count"] == 1
    assert row["backoff_until"] == "2026-11-18T12:01:00+00:00"

    poller.run_once(org, now=NOW + timedelta(minutes=2))
    row = store.connections(org)[0]
    assert row["error_count"] == 2
    assert row["backoff_until"] == "2026-11-18T12:04:00+00:00"


def test_rate_limit_wins_over_the_default_backoff(poll_env):
    store, org, hub = poll_env
    hub.register(FakeConnector(error=RateLimited("slow down", retry_after_s=300)))

    outcome = _poller(store, hub).run_once(org, now=NOW)[0]
    assert outcome.next_sync_at == "2026-11-18T12:05:00+00:00"


def test_cursor_reset_triggers_a_backfill(poll_env):
    store, org, hub = poll_env
    connector = FakeConnector(
        batch=SyncBatch(events=[], cursor=None, reset=True),
        backfill=SyncBatch(events=[_event("fake", "old")], cursor="fresh"),
    )
    hub.register(connector)
    handled: list[Event] = []

    outcome = _poller(store, hub, handled).run_once(org, now=NOW)[0]

    assert connector.sync_calls == 1
    assert connector.backfill_calls == 1
    assert connector.since == NOW - timedelta(days=14)
    assert outcome.cursor == "fresh"
    assert [event.id for event in handled] == ["fake:old"]


def test_event_dispatch_routes_meetings_to_the_meeting_graph(monkeypatch):
    calls: list[dict] = []
    monkeypatch.setattr(
        dispatch_module, "run_meeting", lambda graph, **kwargs: calls.append(kwargs)
    )
    runtime = SimpleNamespace(meeting_graph=object(), org_id="o1")
    event = Event(
        id="fireflies:m1:1",
        provider="fireflies",
        org_id="o1",
        kind=EventKind.MEETING_COMPLETED,
        occurred_at=NOW,
        subject=ObjectRef(provider="fireflies", id="m1", kind="meeting"),
    )

    dispatch_module.process_event(runtime, event)

    assert calls[0]["org_id"] == "o1"
    assert calls[0]["external_id"] == "m1"
    assert calls[0]["thread_id"].startswith("meeting-m1-")


def test_event_dispatch_fetches_the_message_before_the_inbox_graph(monkeypatch):
    message = Message(
        id="msg1",
        org_id="o1",
        provider="fake",
        external_id="m1",
        thread_id="t1",
        from_email="a@example.com",
    )

    class FetchingConnector:
        def fetch_new(self, conn, payload):
            assert payload["message_id"] == "m1"
            return [message]

    monkeypatch.setattr(
        dispatch_module,
        "run_inbox",
        lambda graph, **kwargs: calls.append(kwargs),
    )
    calls: list[dict] = []
    conn_row = {
        "id": "c1",
        "org_id": "o1",
        "provider": "fake",
        "mode": "org",
        "scopes": [],
        "status": "active",
    }
    runtime = SimpleNamespace(
        inbox_graph=object(),
        org_id="o1",
        store=SimpleNamespace(connections=lambda org, provider=None: [conn_row]),
        hub=SimpleNamespace(connector=lambda provider: FetchingConnector()),
    )
    event = _event("fake", "m1")
    event.org_id = "o1"

    dispatch_module.process_event(runtime, event)

    assert calls[0]["org_id"] == "o1"
    assert calls[0]["message"].external_id == "m1"


def test_webhook_routes_are_gated_by_ingress(tmp_path, monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    def app_for(ingress: str):
        data = tmp_path / ingress
        settings = load_settings(
            data_dir=data,
            db_path=data / "gather.db",
            checkpoints_path=data / "checkpoints.db",
            ingress=ingress,
        )
        return create_app(settings)

    http_app = app_for("http")
    try:
        assert "/webhooks/fireflies/{org}" in http_app.openapi()["paths"]
    finally:
        http_app.state.rt.close()

    poll_app = app_for("poll")
    try:
        paths = set(poll_app.openapi()["paths"])
        assert "/webhooks/fireflies/{org}" not in paths
        assert "/sync" in paths
    finally:
        poll_app.state.rt.close()


def test_dead_credential_marks_the_connection_for_reauth(poll_env):
    store, org, hub = poll_env
    hub.register(FakeConnector(error=AuthExpired("token revoked")))

    outcome = _poller(store, hub).run_once(org, now=NOW)[0]

    assert outcome.error == "token revoked"
    row = store.connections(org, status=None)[0]
    assert row["status"] == "reauth_required"
    assert store.due_connections(org, "2026-11-19T12:00:00+00:00") == []
