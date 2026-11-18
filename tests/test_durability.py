"""Durability: storage location, migrations, and approvals across a restart."""

from __future__ import annotations

import sqlite3

import pytest

from gather.config import load_settings
from gather.db import (
    BASELINE_V1,
    LATEST_SCHEMA_VERSION,
    MIGRATIONS,
    Migration,
    MigrationError,
    Store,
    apply_migrations,
    schema_version,
)
from gather.graphs import resume_meeting, run_meeting
from gather.runtime import build_runtime

from support import build_workspace


def test_store_creates_a_versioned_database(tmp_path):
    store = Store(tmp_path / "gather.db")
    try:
        assert store.schema_version == LATEST_SCHEMA_VERSION
    finally:
        store.close()


def test_unversioned_database_from_the_prototype_is_adopted(tmp_path):
    """Databases created before versioning report user_version = 0."""
    path = tmp_path / "legacy.db"
    con = sqlite3.connect(path)
    con.executescript(BASELINE_V1)
    con.execute(
        "INSERT INTO organizations (id, name, created_at) VALUES (?, ?, ?)",
        ("org_1", "Acme", "2026-01-01T00:00:00+00:00"),
    )
    con.commit()
    con.close()

    store = Store(path)
    try:
        assert store.schema_version == LATEST_SCHEMA_VERSION
        assert store.get_org("org_1")["name"] == "Acme"
    finally:
        store.close()

    # Reopening is a no-op, not a re-run of the baseline.
    again = Store(path)
    try:
        assert again.schema_version == LATEST_SCHEMA_VERSION
        assert again.get_org("org_1")["name"] == "Acme"
    finally:
        again.close()


def test_v1_database_gains_polling_state_preserving_rows(tmp_path):
    path = tmp_path / "v1.db"
    con = sqlite3.connect(path)
    apply_migrations(con, MIGRATIONS[:1])
    con.execute(
        "INSERT INTO organizations (id, name, created_at) VALUES ('o1', 'Acme', 'now')"
    )
    con.execute(
        "INSERT INTO connections "
        "(id, org_id, provider, mode, scopes, cursor, status, created_at) "
        "VALUES ('c1', 'o1', 'gmail', 'org', '[]', '100', 'active', 'now')"
    )
    con.commit()
    con.close()

    store = Store(path)
    try:
        assert store.schema_version == LATEST_SCHEMA_VERSION
        row = store.connections("o1", status=None)[0]
        assert row["cursor"] == "100"
        assert row["error_count"] == 0
        assert row["next_sync_at"] is None
    finally:
        store.close()


def test_migrations_apply_in_order_and_only_once(tmp_path):
    one = Migration(1, "alpha", "CREATE TABLE alpha (id TEXT PRIMARY KEY, name TEXT);")
    two = Migration(2, "note", "ALTER TABLE alpha ADD COLUMN note TEXT;")
    con = sqlite3.connect(tmp_path / "steps.db")

    assert apply_migrations(con, (one,)) == 1
    con.execute("INSERT INTO alpha (id, name) VALUES ('a', 'kept')")
    con.commit()

    assert apply_migrations(con, (one, two)) == 2
    assert apply_migrations(con, (one, two)) == 2
    assert schema_version(con) == 2
    assert con.execute("SELECT name, note FROM alpha").fetchone() == ("kept", None)
    con.close()


def test_failed_migration_rolls_back_the_whole_step(tmp_path):
    one = Migration(1, "alpha", "CREATE TABLE alpha (id TEXT PRIMARY KEY);")
    bad = Migration(
        2,
        "broken",
        "ALTER TABLE alpha ADD COLUMN note TEXT;\nSELECT * FROM does_not_exist;",
    )
    con = sqlite3.connect(tmp_path / "broken.db")
    apply_migrations(con, (one,))

    with pytest.raises(MigrationError):
        apply_migrations(con, (one, bad))

    assert schema_version(con) == 1
    columns = [row[1] for row in con.execute("PRAGMA table_info(alpha)")]
    assert "note" not in columns
    con.close()


def test_newer_database_is_refused_instead_of_corrupted(tmp_path):
    path = tmp_path / "future.db"
    con = sqlite3.connect(path)
    con.execute("PRAGMA user_version = 99")
    con.close()

    with pytest.raises(MigrationError, match="newer than this build"):
        Store(path)


def test_data_dir_is_the_single_storage_location(tmp_path, monkeypatch):
    monkeypatch.delenv("GATHER_DB", raising=False)
    monkeypatch.delenv("GATHER_DATA_DIR", raising=False)
    monkeypatch.delenv("GATHER_CHECKPOINTS", raising=False)

    settings = load_settings(data_dir=tmp_path / "workspace")
    assert settings.data_dir == tmp_path / "workspace"
    assert settings.db_path == tmp_path / "workspace" / "gather.db"
    assert settings.checkpoints_path == tmp_path / "workspace" / "checkpoints.db"


def test_unknown_ingress_mode_is_rejected(tmp_path, monkeypatch):
    monkeypatch.delenv("GATHER_INGRESS", raising=False)
    with pytest.raises(ValueError, match="GATHER_INGRESS"):
        load_settings(data_dir=tmp_path / "ws", ingress="webhook")


def test_gather_db_remains_a_development_override(tmp_path, monkeypatch):
    custom = tmp_path / "custom" / "dev.db"
    monkeypatch.setenv("GATHER_DB", str(custom))
    monkeypatch.delenv("GATHER_CHECKPOINTS", raising=False)

    settings = load_settings(data_dir=tmp_path / "ignored")
    assert settings.db_path == custom
    assert settings.checkpoints_path == custom.parent / "checkpoints.db"


def test_pending_approval_survives_a_restart(settings):
    first = build_workspace(settings)
    try:
        state, thread_id = run_meeting(
            first.meeting_graph, org_id=first.org, external_id="ff_9f3a2c"
        )
        assert "__interrupt__" in state
        cards = state["__interrupt__"][0].value["tasks"]
        assert len(cards) == 4
    finally:
        first.close()

    second = build_workspace(settings)
    try:
        snapshot = second.meeting_graph.get_state(
            {"configurable": {"thread_id": thread_id}}
        )
        assert snapshot.next  # still paused at the approval gate

        # The proposal the human saw is still there, not just the graph state.
        pending = second.store.proposals(second.org)
        assert len(pending) == 1
        assert pending[0]["thread_id"] == thread_id

        resumed = resume_meeting(
            second.meeting_graph, thread_id, [card["fingerprint"] for card in cards]
        )
        assert len(resumed["created"]) == 4
        assert len(second.store.tasks(second.org)) == 5
    finally:
        second.close()


def test_runtime_wires_the_durable_checkpointer(settings):
    first = build_runtime(settings, log=lambda _: None, force_fixture=True)
    try:
        assert settings.checkpoints_path.exists()
        state, thread_id = run_meeting(
            first.meeting_graph, org_id=first.org_id, external_id="ff_9f3a2c"
        )
        cards = state["__interrupt__"][0].value["tasks"]
    finally:
        first.close()

    second = build_runtime(settings, log=lambda _: None, force_fixture=True)
    try:
        resumed = resume_meeting(
            second.meeting_graph, thread_id, [card["fingerprint"] for card in cards]
        )
        # No seeded workspace here, so all five commitments are fresh.
        assert len(resumed["created"]) == 5
    finally:
        second.close()
