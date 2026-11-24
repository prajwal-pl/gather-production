from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

from .domain import Event, EventKind, Meeting, Message, TaskStatus

@dataclass(frozen=True, slots=True)
class Migration:
    version: int
    name: str
    sql: str


BASELINE_V1 = """
CREATE TABLE IF NOT EXISTS organizations (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    org_id TEXT NOT NULL,
    email TEXT NOT NULL,
    name TEXT NOT NULL,
    slack_id TEXT,
    UNIQUE (org_id, email)
);

CREATE TABLE IF NOT EXISTS connections (
    id TEXT PRIMARY KEY,
    org_id TEXT NOT NULL,
    provider TEXT NOT NULL,
    mode TEXT NOT NULL DEFAULT 'org',
    user_id TEXT,
    scopes TEXT NOT NULL DEFAULT '[]',
    secret_ref TEXT,
    cursor TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    expires_at TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS raw_events (
    id TEXT PRIMARY KEY,
    org_id TEXT NOT NULL,
    provider TEXT NOT NULL,
    provider_event_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    received_at TEXT NOT NULL,
    payload TEXT NOT NULL,
    UNIQUE (provider, provider_event_id)
);

CREATE TABLE IF NOT EXISTS meetings (
    id TEXT PRIMARY KEY,
    org_id TEXT NOT NULL,
    provider TEXT NOT NULL,
    external_id TEXT NOT NULL,
    title TEXT,
    started_at TEXT,
    duration_min INTEGER,
    participants TEXT NOT NULL DEFAULT '[]',
    transcript TEXT NOT NULL DEFAULT '',
    summary TEXT,
    processed_at TEXT,
    UNIQUE (org_id, provider, external_id)
);

CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    org_id TEXT NOT NULL,
    title TEXT NOT NULL,
    detail TEXT,
    owner_user_id TEXT,
    owner_email TEXT,
    due_date TEXT,
    status TEXT NOT NULL DEFAULT 'open',
    priority TEXT,
    source_meeting_id TEXT,
    source_quote TEXT,
    confidence REAL,
    fingerprint TEXT,
    next_check_at TEXT,
    last_nudged_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tasks_org_status ON tasks (org_id, status);
CREATE INDEX IF NOT EXISTS idx_tasks_org_owner ON tasks (org_id, owner_email);

CREATE TABLE IF NOT EXISTS task_events (
    id TEXT PRIMARY KEY,
    org_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    actor TEXT,
    payload TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_task_events_task ON task_events (task_id);

CREATE TABLE IF NOT EXISTS messages (
    id TEXT PRIMARY KEY,
    org_id TEXT NOT NULL,
    provider TEXT NOT NULL,
    external_id TEXT NOT NULL,
    thread_id TEXT NOT NULL,
    from_email TEXT NOT NULL,
    from_name TEXT NOT NULL DEFAULT '',
    to_emails TEXT NOT NULL DEFAULT '[]',
    subject TEXT NOT NULL DEFAULT '',
    body_text TEXT NOT NULL DEFAULT '',
    sent_at TEXT,
    UNIQUE (org_id, provider, external_id)
);

CREATE TABLE IF NOT EXISTS message_task_links (
    org_id TEXT NOT NULL,
    message_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    confidence REAL NOT NULL DEFAULT 0,
    method TEXT NOT NULL DEFAULT 'unknown',
    UNIQUE (org_id, message_id, task_id)
);

CREATE TABLE IF NOT EXISTS proposals (
    id TEXT PRIMARY KEY,
    org_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    thread_id TEXT,
    dedupe_key TEXT,
    payload TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL,
    decided_at TEXT,
    decided_by TEXT,
    UNIQUE (org_id, dedupe_key)
);
CREATE INDEX IF NOT EXISTS idx_proposals_org_status ON proposals (org_id, status);
"""

CONNECTION_SCHEDULING_V2 = """
ALTER TABLE connections ADD COLUMN last_sync_at TEXT;
ALTER TABLE connections ADD COLUMN next_sync_at TEXT;
ALTER TABLE connections ADD COLUMN backoff_until TEXT;
ALTER TABLE connections ADD COLUMN error_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE connections ADD COLUMN last_error TEXT;
CREATE INDEX IF NOT EXISTS idx_connections_due
    ON connections (org_id, status, next_sync_at);
"""

# Forward-only. Never edit a shipped migration; append a new one.
# v1 is idempotent on purpose: databases created before versioning existed have
# all of these tables but report user_version = 0.
MIGRATIONS: tuple[Migration, ...] = (
    Migration(1, "baseline", BASELINE_V1),
    Migration(2, "connection_scheduling", CONNECTION_SCHEDULING_V2),
)

LATEST_SCHEMA_VERSION = MIGRATIONS[-1].version


class MigrationError(RuntimeError):
    pass


def schema_version(con: sqlite3.Connection) -> int:
    return int(con.execute("PRAGMA user_version").fetchone()[0])


def apply_migrations(
    con: sqlite3.Connection, migrations: tuple[Migration, ...] = MIGRATIONS
) -> int:
    """Run pending migrations in one transaction each.

    ``executescript`` commits before it runs, so the transaction is written
    explicitly inside the script. DDL and the version bump therefore land
    together or not at all.
    """
    version = schema_version(con)
    latest = migrations[-1].version if migrations else 0
    if version > latest:
        raise MigrationError(
            f"database schema v{version} is newer than this build ({latest}); "
            "update gather instead of downgrading"
        )
    for migration in migrations:
        if migration.version <= version:
            continue
        script = (
            "BEGIN IMMEDIATE;\n"
            f"{migration.sql}\n"
            f"PRAGMA user_version = {migration.version};\n"
            "COMMIT;"
        )
        try:
            con.executescript(script)
        except Exception as exc:  # noqa: BLE001 - re-raised with context
            try:
                con.execute("ROLLBACK")
            except sqlite3.OperationalError:
                pass
            raise MigrationError(
                f"migration {migration.version} ({migration.name}) failed: {exc}"
            ) from exc
        version = migration.version
    return version


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _j(value: Any) -> str:
    return json.dumps(value, default=str)


def _uid() -> str:
    return uuid.uuid4().hex


class Store:
    """Thin repository over SQLite.

    Every method takes ``org_id`` explicitly; there is no ambient tenant. In
    Postgres the same shape is enforced by row-level security, so an app bug
    cannot cross tenants.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.con = sqlite3.connect(str(self.path), check_same_thread=False, timeout=5.0)
        self.con.row_factory = sqlite3.Row
        # PRAGMAs that cannot run inside a migration transaction.
        self.con.execute("PRAGMA journal_mode=WAL")
        self.con.execute("PRAGMA synchronous=NORMAL")
        self.con.execute("PRAGMA foreign_keys=ON")
        self.con.execute("PRAGMA busy_timeout=5000")
        apply_migrations(self.con)
        self.con.commit()

    @property
    def schema_version(self) -> int:
        return schema_version(self.con)

    # -- low level --------------------------------------------------------- #

    def _rows(self, sql: str, params: Iterable[Any] = ()) -> list[dict]:
        with self._lock:
            cur = self.con.execute(sql, tuple(params))
            return [dict(row) for row in cur.fetchall()]

    def _row(self, sql: str, params: Iterable[Any] = ()) -> dict | None:
        rows = self._rows(sql, params)
        return rows[0] if rows else None

    def _write(self, sql: str, params: Iterable[Any] = ()) -> None:
        with self._lock:
            self.con.execute(sql, tuple(params))
            self.con.commit()

    def close(self) -> None:
        with self._lock:
            self.con.close()

    # -- tenant / people --------------------------------------------------- #

    def ensure_org(self, name: str, org_id: str | None = None) -> str:
        existing = self._row("SELECT id FROM organizations WHERE name = ?", (name,))
        if existing:
            return existing["id"]
        org_id = org_id or _uid()
        self._write(
            "INSERT INTO organizations (id, name, created_at) VALUES (?, ?, ?)",
            (org_id, name, _now()),
        )
        return org_id

    def get_org(self, org_id: str) -> dict | None:
        return self._row("SELECT * FROM organizations WHERE id = ?", (org_id,))

    def add_user(self, org_id: str, email: str, name: str, slack_id: str | None = None) -> str:
        existing = self.user_by_email(org_id, email)
        if existing:
            return existing["id"]
        user_id = _uid()
        self._write(
            "INSERT INTO users (id, org_id, email, name, slack_id) VALUES (?, ?, ?, ?, ?)",
            (user_id, org_id, email.lower(), name, slack_id),
        )
        return user_id

    def users(self, org_id: str) -> list[dict]:
        return self._rows("SELECT * FROM users WHERE org_id = ? ORDER BY name", (org_id,))

    def user_by_email(self, org_id: str, email: str) -> dict | None:
        return self._row(
            "SELECT * FROM users WHERE org_id = ? AND lower(email) = ?",
            (org_id, email.strip().lower()),
        )

    # -- connections ------------------------------------------------------- #

    def upsert_connection(
        self,
        org_id: str,
        provider: str,
        *,
        mode: str = "org",
        user_id: str | None = None,
        scopes: Iterable[str] = (),
        secret_ref: str | None = None,
        expires_at: str | None = None,
    ) -> str:
        existing = self._row(
            "SELECT id FROM connections WHERE org_id = ? AND provider = ? "
            "AND COALESCE(user_id, '') = COALESCE(?, '')",
            (org_id, provider, user_id),
        )
        if existing:
            return existing["id"]
        conn_id = _uid()
        self._write(
            "INSERT INTO connections "
            "(id, org_id, provider, mode, user_id, scopes, secret_ref, status, expires_at, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)",
            (conn_id, org_id, provider, mode, user_id, _j(list(scopes)), secret_ref, expires_at, _now()),
        )
        return conn_id

    def connections(
        self,
        org_id: str,
        provider: str | None = None,
        status: str | None = "active",
    ) -> list[dict]:
        sql = "SELECT * FROM connections WHERE org_id = ?"
        params: list[Any] = [org_id]
        if provider:
            sql += " AND provider = ?"
            params.append(provider)
        if status:
            sql += " AND status = ?"
            params.append(status)
        rows = self._rows(sql, params)
        for row in rows:
            row["scopes"] = json.loads(row.get("scopes") or "[]")
        return rows

    def set_cursor(self, connection_id: str, cursor: str) -> None:
        self._write("UPDATE connections SET cursor = ? WHERE id = ?", (cursor, connection_id))

    def get_connection(self, connection_id: str) -> dict | None:
        row = self._row("SELECT * FROM connections WHERE id = ?", (connection_id,))
        if row:
            row["scopes"] = json.loads(row.get("scopes") or "[]")
        return row

    def due_connections(self, org_id: str, now: str, limit: int = 10) -> list[dict]:
        """Active connections whose next poll is due and which are not backing off."""
        rows = self._rows(
            "SELECT * FROM connections WHERE org_id = ? AND status = 'active' "
            "AND (next_sync_at IS NULL OR next_sync_at <= ?) "
            "AND (backoff_until IS NULL OR backoff_until <= ?) "
            "ORDER BY COALESCE(next_sync_at, '') LIMIT ?",
            (org_id, now, now, limit),
        )
        for row in rows:
            row["scopes"] = json.loads(row.get("scopes") or "[]")
        return rows

    def update_connection_identity(
        self,
        connection_id: str,
        *,
        user_id: str | None,
        scopes: Iterable[str],
    ) -> None:
        """Refresh the account/grant metadata after a reconnect."""
        self._write(
            "UPDATE connections SET user_id = ?, scopes = ? WHERE id = ?",
            (user_id, _j(list(scopes)), connection_id),
        )

    def mark_sync_success(
        self,
        connection_id: str,
        *,
        cursor: str | None,
        next_sync_at: str,
        synced_at: str,
    ) -> None:
        self._write(
            "UPDATE connections SET cursor = COALESCE(?, cursor), last_sync_at = ?, "
            "next_sync_at = ?, backoff_until = NULL, error_count = 0, last_error = NULL "
            "WHERE id = ?",
            (cursor, synced_at, next_sync_at, connection_id),
        )

    def clear_connection_error(
        self, connection_id: str, *, next_sync_at: str, status: str = "active"
    ) -> None:
        """Forget a stale failure after a credential was repaired.

        Reconnecting Gmail (or rotating a Fireflies key) should not leave the
        old error and exponential backoff on screen until the next poll.
        """
        self._write(
            "UPDATE connections SET last_error = NULL, error_count = 0, "
            "backoff_until = NULL, next_sync_at = ?, status = ? WHERE id = ?",
            (next_sync_at, status, connection_id),
        )

    def mark_sync_failure(
        self,
        connection_id: str,
        *,
        error: str,
        next_sync_at: str,
        backoff_until: str,
        status: str | None = None,
    ) -> None:
        self._write(
            "UPDATE connections SET error_count = error_count + 1, last_error = ?, "
            "next_sync_at = ?, backoff_until = ?, status = COALESCE(?, status) "
            "WHERE id = ?",
            (error[:500], next_sync_at, backoff_until, status, connection_id),
        )

    def set_connection_status(self, connection_id: str, status: str) -> None:
        self._write("UPDATE connections SET status = ? WHERE id = ?", (status, connection_id))

    # -- events ------------------------------------------------------------ #

    def record_event(self, event: Event, raw: dict | None = None) -> bool:
        """Returns False when the provider retried a delivery we already saw."""
        try:
            self._write(
                "INSERT INTO raw_events "
                "(id, org_id, provider, provider_event_id, kind, received_at, payload) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    _uid(),
                    event.org_id,
                    event.provider,
                    event.id,
                    str(event.kind),
                    _now(),
                    _j(raw or event.payload),
                ),
            )
            return True
        except sqlite3.IntegrityError:
            return False

    def seen_event(self, provider: str, provider_event_id: str) -> bool:
        return bool(
            self._row(
                "SELECT 1 FROM raw_events WHERE provider = ? AND provider_event_id = ?",
                (provider, provider_event_id),
            )
        )

    # -- meetings ---------------------------------------------------------- #

    def upsert_meeting(self, meeting: Meeting) -> str:
        existing = self._row(
            "SELECT id FROM meetings WHERE org_id = ? AND provider = ? AND external_id = ?",
            (meeting.org_id, meeting.provider, meeting.external_id),
        )
        if existing:
            self._write(
                "UPDATE meetings SET title = ?, transcript = ?, participants = ?, summary = ? WHERE id = ?",
                (
                    meeting.title,
                    meeting.transcript,
                    _j([p.model_dump() for p in meeting.participants]),
                    meeting.summary,
                    existing["id"],
                ),
            )
            return existing["id"]

        meeting_id = _uid()
        self._write(
            "INSERT INTO meetings "
            "(id, org_id, provider, external_id, title, started_at, duration_min, participants, transcript, summary) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                meeting_id,
                meeting.org_id,
                meeting.provider,
                meeting.external_id,
                meeting.title,
                meeting.started_at.isoformat() if meeting.started_at else None,
                meeting.duration_min,
                _j([p.model_dump() for p in meeting.participants]),
                meeting.transcript,
                meeting.summary,
            ),
        )
        return meeting_id

    def mark_meeting_processed(self, meeting_id: str) -> None:
        self._write("UPDATE meetings SET processed_at = ? WHERE id = ?", (_now(), meeting_id))

    def meetings(
        self,
        org_id: str,
        *,
        provider: str | None = None,
        limit: int = 100,
    ) -> list[dict]:
        """List meetings without shipping whole transcripts to the UI."""
        sql = (
            "SELECT id, org_id, provider, external_id, title, started_at, "
            "duration_min, participants, summary, processed_at, "
            "length(transcript) AS transcript_chars "
            "FROM meetings WHERE org_id = ?"
        )
        params: list[Any] = [org_id]
        if provider:
            sql += " AND provider = ?"
            params.append(provider)
        sql += " ORDER BY COALESCE(started_at, '') DESC LIMIT ?"
        params.append(max(1, int(limit)))
        rows = self._rows(sql, params)
        for row in rows:
            row["participants"] = json.loads(row.get("participants") or "[]")
        return rows

    def get_meeting(self, meeting_id: str) -> dict | None:
        row = self._row("SELECT * FROM meetings WHERE id = ?", (meeting_id,))
        if row:
            row["participants"] = json.loads(row.get("participants") or "[]")
        return row

    def tasks_for_meeting(self, meeting_id: str) -> list[dict]:
        return self._rows(
            "SELECT * FROM tasks WHERE source_meeting_id = ? ORDER BY created_at",
            (meeting_id,),
        )

    # -- tasks ------------------------------------------------------------- #

    def create_task(
        self,
        org_id: str,
        *,
        title: str,
        detail: str | None = None,
        owner_user_id: str | None = None,
        owner_email: str | None = None,
        due_date: str | None = None,
        status: str = TaskStatus.OPEN.value,
        priority: str | None = None,
        source_meeting_id: str | None = None,
        source_quote: str | None = None,
        confidence: float | None = None,
        fingerprint: str | None = None,
    ) -> dict:
        task_id = _uid()
        now = _now()
        self._write(
            "INSERT INTO tasks "
            "(id, org_id, title, detail, owner_user_id, owner_email, due_date, status, priority, "
            " source_meeting_id, source_quote, confidence, fingerprint, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                task_id,
                org_id,
                title,
                detail,
                owner_user_id,
                owner_email,
                due_date,
                status,
                priority,
                source_meeting_id,
                source_quote,
                confidence,
                fingerprint,
                now,
                now,
            ),
        )
        return self.get_task(task_id)  # type: ignore[return-value]

    def get_task(self, task_id: str) -> dict | None:
        return self._row("SELECT * FROM tasks WHERE id = ?", (task_id,))

    def tasks(
        self,
        org_id: str,
        *,
        status: str | None = None,
        owner_email: str | None = None,
        limit: int = 200,
    ) -> list[dict]:
        sql = "SELECT * FROM tasks WHERE org_id = ?"
        params: list[Any] = [org_id]
        if status:
            sql += " AND status = ?"
            params.append(status)
        if owner_email:
            sql += " AND lower(owner_email) = ?"
            params.append(owner_email.lower())
        sql += " ORDER BY COALESCE(due_date, '9999-12-31'), created_at DESC LIMIT ?"
        params.append(limit)
        return self._rows(sql, params)

    def open_tasks(self, org_id: str) -> list[dict]:
        return self._rows(
            "SELECT * FROM tasks WHERE org_id = ? AND status NOT IN ('done', 'dismissed', 'proposed') "
            "ORDER BY COALESCE(due_date, '9999-12-31')",
            (org_id,),
        )

    def find_task_by_fingerprint(self, org_id: str, fingerprint: str) -> dict | None:
        return self._row(
            "SELECT * FROM tasks WHERE org_id = ? AND fingerprint = ? "
            "AND status NOT IN ('done', 'dismissed')",
            (org_id, fingerprint),
        )

    def update_task(self, task_id: str, **fields: Any) -> dict | None:
        allowed = {
            "title",
            "detail",
            "owner_user_id",
            "owner_email",
            "due_date",
            "status",
            "priority",
            "next_check_at",
            "last_nudged_at",
        }
        updates = {k: v for k, v in fields.items() if k in allowed}
        if not updates:
            return self.get_task(task_id)
        assignments = ", ".join(f"{key} = ?" for key in updates)
        params = list(updates.values()) + [_now(), task_id]
        self._write(f"UPDATE tasks SET {assignments}, updated_at = ? WHERE id = ?", params)
        return self.get_task(task_id)

    def add_task_event(
        self,
        org_id: str,
        task_id: str,
        kind: str,
        *,
        actor: str = "system",
        payload: dict | None = None,
    ) -> None:
        self._write(
            "INSERT INTO task_events (id, org_id, task_id, kind, actor, payload, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (_uid(), org_id, task_id, kind, actor, _j(payload or {}), _now()),
        )

    def task_events(self, task_id: str) -> list[dict]:
        rows = self._rows(
            "SELECT * FROM task_events WHERE task_id = ? ORDER BY created_at", (task_id,)
        )
        for row in rows:
            row["payload"] = json.loads(row.get("payload") or "{}")
        return rows

    def due_tasks(self, org_id: str, on_or_before: date) -> list[dict]:
        return self._rows(
            "SELECT * FROM tasks WHERE org_id = ? "
            "AND status IN ('open', 'in_progress', 'blocked') "
            "AND due_date IS NOT NULL AND due_date <= ? "
            "ORDER BY due_date",
            (org_id, on_or_before.isoformat()),
        )

    # -- messages ---------------------------------------------------------- #

    def save_message(self, message: Message) -> str:
        existing = self._row(
            "SELECT id FROM messages WHERE org_id = ? AND provider = ? AND external_id = ?",
            (message.org_id, message.provider, message.external_id),
        )
        if existing:
            return existing["id"]
        message_id = _uid()
        self._write(
            "INSERT INTO messages "
            "(id, org_id, provider, external_id, thread_id, from_email, from_name, "
            " to_emails, subject, body_text, sent_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                message_id,
                message.org_id,
                message.provider,
                message.external_id,
                message.thread_id,
                message.from_email.lower(),
                message.from_name,
                _j(message.to_emails),
                message.subject,
                message.body_text,
                message.sent_at.isoformat() if message.sent_at else None,
            ),
        )
        return message_id

    def messages_for_task(self, task_id: str) -> list[dict]:
        return self._rows(
            "SELECT m.* FROM messages m "
            "JOIN message_task_links l ON l.message_id = m.id "
            "WHERE l.task_id = ? ORDER BY m.sent_at",
            (task_id,),
        )

    def link_message(
        self,
        org_id: str,
        message_id: str,
        task_id: str,
        *,
        confidence: float,
        method: str,
    ) -> None:
        try:
            self._write(
                "INSERT INTO message_task_links "
                "(org_id, message_id, task_id, confidence, method) VALUES (?, ?, ?, ?, ?)",
                (org_id, message_id, task_id, confidence, method),
            )
        except sqlite3.IntegrityError:
            pass

    # -- proposals --------------------------------------------------------- #

    def create_proposal(
        self,
        org_id: str,
        kind: str,
        payload: dict,
        *,
        thread_id: str | None = None,
        dedupe_key: str | None = None,
    ) -> dict:
        dedupe_key = dedupe_key or (f"{kind}:{thread_id}" if thread_id else None)
        if dedupe_key:
            existing = self._row(
                "SELECT * FROM proposals WHERE org_id = ? AND dedupe_key = ?",
                (org_id, dedupe_key),
            )
            if existing:
                self._write(
                    "UPDATE proposals SET payload = ? WHERE id = ?",
                    (_j(payload), existing["id"]),
                )
                return self.get_proposal(existing["id"])  # type: ignore[return-value]

        proposal_id = _uid()
        self._write(
            "INSERT INTO proposals (id, org_id, kind, thread_id, dedupe_key, payload, status, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, 'pending', ?)",
            (proposal_id, org_id, kind, thread_id, dedupe_key, _j(payload), _now()),
        )
        return self.get_proposal(proposal_id)  # type: ignore[return-value]

    def get_proposal(self, proposal_id: str) -> dict | None:
        row = self._row("SELECT * FROM proposals WHERE id = ?", (proposal_id,))
        if row:
            row["payload"] = json.loads(row.get("payload") or "{}")
        return row

    def proposals(self, org_id: str, status: str | None = "pending") -> list[dict]:
        sql = "SELECT * FROM proposals WHERE org_id = ?"
        params: list[Any] = [org_id]
        if status:
            sql += " AND status = ?"
            params.append(status)
        sql += " ORDER BY created_at DESC"
        rows = self._rows(sql, params)
        for row in rows:
            row["payload"] = json.loads(row.get("payload") or "{}")
        return rows

    def decide_proposal(self, proposal_id: str, status: str, decided_by: str = "human") -> None:
        self._write(
            "UPDATE proposals SET status = ?, decided_at = ?, decided_by = ? WHERE id = ?",
            (status, _now(), decided_by, proposal_id),
        )

    # -- scheduler helpers ------------------------------------------------- #

    def stale_nudge_candidates(self, org_id: str, today: date, cooldown_days: int = 3) -> list[dict]:
        cutoff = (today - timedelta(days=cooldown_days)).isoformat()
        return self._rows(
            "SELECT * FROM tasks WHERE org_id = ? "
            "AND status IN ('open', 'in_progress', 'blocked') "
            "AND due_date IS NOT NULL AND due_date < ? "
            "AND (last_nudged_at IS NULL OR substr(last_nudged_at, 1, 10) <= ?) "
            "ORDER BY due_date",
            (org_id, today.isoformat(), cutoff),
        )

    def connections_expiring(self, org_id: str, within_days: int = 2) -> list[dict]:
        cutoff = (datetime.now().astimezone() + timedelta(days=within_days)).isoformat()
        return self._rows(
            "SELECT * FROM connections WHERE org_id = ? AND status = 'active' "
            "AND expires_at IS NOT NULL AND expires_at <= ?",
            (org_id, cutoff),
        )
