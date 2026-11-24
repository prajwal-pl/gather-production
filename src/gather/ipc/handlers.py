"""IPC method surface.

Thin adapter over :class:`Runtime`. No new domain logic lives here: if a
method needs a decision, it is already a store/graph/connector operation. That
keeps HTTP (dev), CLI and stdio behaving the same way.
"""

from __future__ import annotations

import os
import threading
import time
import uuid
from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable

from pydantic import BaseModel

from .. import __version__
from ..agents.assistant import Assistant
from ..check import SYSTEM as PING_SYSTEM
from ..check import USER as PING_USER
from ..check import Ping
from ..config import PROVIDER_KEY_ENV
from ..connectors.base import Connection
from ..domain import Meeting, Person, TaskStatus
from ..events import EventBus
from ..graphs import resume_meeting, run_meeting
from ..llm import ProviderLLM
from ..runs import RunCancelled
from ..runtime import Runtime
from ..secrets import (
    SECRET_KEYS,
    STORED_SETTINGS,
    SecretStore,
    SecretStoreError,
    secret_presence,
)
from .protocol import RpcError
from .router import Router

TASK_UPDATE_FIELDS = {
    "title",
    "detail",
    "owner_user_id",
    "owner_email",
    "due_date",
    "status",
    "priority",
    "next_check_at",
}


def jsonable(value: Any) -> Any:
    """Convert graph/store values into JSON-safe primitives."""
    if isinstance(value, dict):
        return {
            str(key): jsonable(item)
            for key, item in value.items()
            if not str(key).startswith("__")
        }
    if isinstance(value, (list, tuple, set)):
        return [jsonable(item) for item in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, BaseModel):
        return jsonable(value.model_dump())
    if is_dataclass(value) and not isinstance(value, type):
        return jsonable(asdict(value))
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _timestamp() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


class Handlers:
    def __init__(
        self,
        runtime: Runtime,
        *,
        events: EventBus | None = None,
        secrets: SecretStore | None = None,
        keys: dict[str, str] | None = None,
        scheduler: Any | None = None,
        shutdown: Callable[[], None] | None = None,
        log: Callable[[str], None] | None = None,
    ) -> None:
        self.runtime = runtime
        self.events = events or runtime.events
        self.secret_store = secrets
        self.keys = dict(keys or {})
        self.scheduler = scheduler
        self._shutdown = shutdown or (lambda: None)
        self.log = log or (lambda _message: None)
        self._decision_lock = threading.Lock()
        # In-process override used when the secret backend is read-only.
        self._onboarded_override: bool | None = None

    # -- registration ------------------------------------------------------ #

    def register(self, router: Router) -> None:
        router.register("health", self.health)
        router.register("engine.shutdown", self.engine_shutdown)
        router.register("settings.get", self.settings_get)
        router.register("settings.update", self.settings_update)

        router.register("connections.list", self.connections_list)
        router.register("connections.add", self.connections_add)
        router.register("connections.remove", self.connections_remove)
        router.register("connections.test", self.connections_test, slow=True)
        router.register("sync.now", self.sync_now, slow=True)

        router.register("meetings.list", self.meetings_list)
        router.register("meetings.get", self.meetings_get)
        router.register("meetings.ingest", self.meetings_ingest, slow=True)
        router.register("runs.list", self.runs_list)
        router.register("runs.get", self.runs_get)
        router.register("runs.cancel", self.runs_cancel)

        router.register("tasks.list", self.tasks_list)
        router.register("tasks.get", self.tasks_get)
        router.register("tasks.update", self.tasks_update)

        router.register("proposals.list", self.proposals_list)
        router.register("proposals.get", self.proposals_get)
        router.register("proposals.approve", self.proposals_approve, slow=True)
        router.register("proposals.reject", self.proposals_reject)

        router.register("models.check", self.models_check, slow=True)
        router.register("models.usage", self.models_usage)
        router.register("scheduler.status", self.scheduler_status)

        # ─── onboarding ───────────────────────────────────────────────── #
        router.register("onboarding.status", self.onboarding_status)
        router.register("onboarding.complete", self.onboarding_complete)
        router.register("onboarding.reset", self.onboarding_reset)

        # ─── assistant (tool loop: several model calls) ───────────────── #
        router.register("agent.chat", self.agent_chat, slow=True)
        router.register("agent.tools", self.agent_tools)

        # ─── connections.connect (slow: OAuth flow, API key exchange) ─── #
        router.register("connections.connect", self.connections_connect, slow=True)

    # -- helpers ----------------------------------------------------------- #

    @property
    def org(self) -> str:
        return self.runtime.org_id

    def _emit(self, name: str, data: dict[str, Any] | None = None) -> None:
        self.events.emit(name, data or {})

    def _model_labels(self) -> list[str]:
        specs = getattr(self.runtime.llm, "specs", None) or ()
        return [spec.label for spec in specs]

    def _task(self, task_id: str) -> dict:
        task = self.runtime.store.get_task(task_id)
        if not task or task["org_id"] != self.org:
            raise RpcError("not_found", f"task {task_id} not found")
        return task

    def _proposal(self, proposal_id: str) -> dict:
        proposal = self.runtime.store.get_proposal(proposal_id)
        if not proposal or proposal["org_id"] != self.org:
            raise RpcError("not_found", f"proposal {proposal_id} not found")
        return proposal

    def _connection(self, params: dict[str, Any]) -> dict:
        connection_id = params.get("connection_id")
        provider = params.get("provider")
        if connection_id:
            row = self.runtime.store.get_connection(str(connection_id))
        elif provider:
            rows = self.runtime.store.connections(self.org, provider=str(provider), status=None)
            row = rows[0] if rows else None
        else:
            raise RpcError("invalid_params", "connection_id or provider is required")
        if not row or row["org_id"] != self.org:
            raise RpcError("not_found", "connection not found")
        return row

    def _refresh_connector_credentials(self) -> None:
        """Apply secret changes to already-constructed connectors.

        Connectors are built once at engine start. Without this, saving a new
        key/token only takes effect after a restart.
        """
        gmail = getattr(self.runtime, "gmail", None)
        if gmail is not None and hasattr(gmail, "update_credentials"):
            gmail.update_credentials(
                client_id=self.keys.get("GOOGLE_CLIENT_ID") or None,
                client_secret=self.keys.get("GOOGLE_CLIENT_SECRET") or None,
                refresh_token=self.keys.get("GMAIL_REFRESH_TOKEN") or None,
            )
        fireflies = getattr(self.runtime, "fireflies", None)
        if fireflies is not None:
            fireflies.api_key = self.keys.get("FIREFLIES_API_KEY", "")

    def _announce_thread(self, thread_id: str) -> list[str]:
        ids: list[str] = []
        for proposal in self.runtime.store.proposals(self.org, status="pending"):
            if proposal.get("thread_id") != thread_id:
                continue
            ids.append(proposal["id"])
            self._emit(
                "proposal.created",
                {
                    "proposal_id": proposal["id"],
                    "kind": proposal.get("kind"),
                    "thread_id": thread_id,
                    "title": (proposal.get("payload") or {}).get("meeting"),
                    "at": _timestamp(),
                },
            )
        return ids

    # -- system ------------------------------------------------------------ #

    def health(self, params: dict[str, Any]) -> dict[str, Any]:
        labels = self._model_labels()
        active = self.runtime.runs.list(active_only=True, limit=20)
        scheduler_status = (
            self.scheduler.status() if self.scheduler is not None else {"running": False}
        )
        if scheduler_status.get("last_error"):
            state = "needs_attention"
        elif any(run["kind"] == "sync" for run in active):
            state = "syncing"
        elif active:
            state = "running"
        elif labels or self.runtime.fixture:
            state = "ready"
        else:
            state = "needs_attention"
        return {
            "ok": True,
            "state": state,
            "version": __version__,
            "org_id": self.org,
            "workspace": self.runtime.settings.org_name,
            "data_dir": str(self.runtime.settings.data_dir),
            "schema_version": self.runtime.store.schema_version,
            "providers": self.runtime.hub.providers(),
            "models": labels,
            "fixture_mode": not labels,
            "usage": self.runtime.llm.usage.as_dict(),
            "scheduler": scheduler_status,
            "secrets_backend": (
                self.secret_store.backend if self.secret_store is not None else "env"
            ),
            "ingress": self.runtime.settings.ingress,
        }

    def engine_shutdown(self, params: dict[str, Any]) -> dict[str, Any]:
        self._emit("engine.stopping", {"at": _timestamp()})
        self._shutdown()
        return {"ok": True, "at": _timestamp()}

    def settings_get(self, params: dict[str, Any]) -> dict[str, Any]:
        settings = self.runtime.settings
        return {
            "org_name": settings.org_name,
            "data_dir": str(settings.data_dir),
            "chain": [spec.label for spec in settings.chain],
            "chunk_chars": settings.chunk_chars,
            "max_chunks": settings.max_chunks,
            "min_interval_s": settings.min_interval_s,
            "max_calls_per_run": settings.max_calls_per_run,
            "auto_approve": settings.auto_approve,
            "poll_interval_s": settings.poll_interval_s,
            "sweep_interval_s": settings.sweep_interval_s,
            "secrets": secret_presence(self.secret_store),
            "secrets_backend": (
                self.secret_store.backend if self.secret_store is not None else "env"
            ),
        }

    def settings_update(self, params: dict[str, Any]) -> dict[str, Any]:
        store = self.secret_store
        if store is None or not store.writable:
            raise RpcError("unauthorized", "the active secret store is read-only")

        secrets = params.get("secrets") or {}
        settings = params.get("settings") or {}
        if not isinstance(secrets, dict) or not isinstance(settings, dict):
            raise RpcError("invalid_params", "secrets and settings must be objects")

        # Validate the whole request before writing anything: a typo in the
        # last key must not leave a half-applied configuration.
        secret_writes: list[tuple[str, str | None]] = []
        for key, value in secrets.items():
            key = str(key)
            if key not in SECRET_KEYS:
                raise RpcError("invalid_params", f"unknown secret {key!r}")
            if value is None or value == "":
                secret_writes.append((key, None))
            elif isinstance(value, str):
                secret_writes.append((key, value))
            else:
                raise RpcError("invalid_params", f"secret {key!r} must be a string")

        setting_writes: list[tuple[str, str | None]] = []
        for key, raw in settings.items():
            key = str(key)
            if key not in STORED_SETTINGS:
                raise RpcError("invalid_params", f"unknown setting {key!r}")
            if raw is None:
                setting_writes.append((key, None))
                continue
            _, cast = STORED_SETTINGS[key]
            try:
                value = cast(str(raw))
            except (TypeError, ValueError) as exc:
                raise RpcError("invalid_params", f"bad value for {key}: {exc}") from exc
            if key == "GATHER_MODEL_CHAIN":
                if not value:
                    raise RpcError("invalid_params", "model chain cannot be empty")
                normalized = ",".join(spec.label for spec in value)
            elif isinstance(value, bool):
                normalized = "true" if value else "false"
            else:
                normalized = str(value)
            setting_writes.append((key, normalized))

        updated: list[str] = []
        deleted: list[str] = []
        for key, value in secret_writes:
            if value is None:
                store.delete(key)
                self.keys.pop(key, None)
                deleted.append(key)
            else:
                store.set(key, value)
                self.keys[key] = value
                updated.append(key)

        changed_settings: list[str] = []
        for key, value in setting_writes:
            if value is None:
                store.delete(key)
            else:
                store.set(key, value)
            changed_settings.append(key)

        self._refresh_connector_credentials()

        self._emit(
            "settings.changed",
            {
                "updated": updated,
                "deleted": deleted,
                "settings": changed_settings,
                "at": _timestamp(),
            },
        )
        return {
            "updated": updated,
            "deleted": deleted,
            "settings": changed_settings,
            # Model clients and connector credentials are built at startup.
            "restart_required": bool(updated or deleted or changed_settings),
        }

    # -- connections ------------------------------------------------------- #

    def connections_list(self, params: dict[str, Any]) -> dict[str, Any]:
        rows = self.runtime.store.connections(self.org, status=None)
        return {
            "connections": rows,
            "providers": self.runtime.hub.providers(),
            "credentials": {
                "fireflies": bool(self.keys.get("FIREFLIES_API_KEY")),
                "gmail": bool(
                    self.keys.get("GMAIL_REFRESH_TOKEN") or self.keys.get("GMAIL_ACCESS_TOKEN")
                ),
            },
        }

    def connections_add(self, params: dict[str, Any]) -> dict[str, Any]:
        provider = params.get("provider")
        if provider not in self.runtime.hub.providers():
            raise RpcError("invalid_params", f"unknown provider {provider!r}")
        mode = params.get("mode", "org")
        if mode not in {"org", "user"}:
            raise RpcError("invalid_params", "mode must be 'org' or 'user'")

        scopes = params.get("scopes") or []
        if not isinstance(scopes, list) or not all(isinstance(scope, str) for scope in scopes):
            raise RpcError("invalid_params", "scopes must be a list of strings")

        user_id = params.get("user_id")
        if mode == "user":
            if not user_id:
                raise RpcError("invalid_params", "user_id is required for user mode")
            users = self.runtime.store.users(self.org)
            match = next(
                (
                    user
                    for user in users
                    if user_id in {user["id"], user["email"]}
                ),
                None,
            )
            if match is None:
                raise RpcError("invalid_params", f"unknown user {user_id!r}")
            user_id = match["id"]

        connection_id = self.runtime.store.upsert_connection(
            self.org,
            str(provider),
            mode=str(mode),
            user_id=str(user_id) if user_id else None,
            scopes=scopes,
        )
        # Re-adding a disabled connection must make it active again.
        self.runtime.store.set_connection_status(connection_id, "active")
        connection = self.runtime.store.get_connection(connection_id)
        self._emit(
            "connections.changed",
            {"connection_id": connection_id, "provider": provider, "action": "added"},
        )
        return {"connection": connection}

    def connections_remove(self, params: dict[str, Any]) -> dict[str, Any]:
        row = self._connection(params)
        self.runtime.store.set_connection_status(row["id"], "disabled")
        self._emit(
            "connections.changed",
            {"connection_id": row["id"], "provider": row["provider"], "action": "removed"},
        )
        return {"ok": True, "connection_id": row["id"]}

    def connections_test(self, params: dict[str, Any]) -> dict[str, Any]:
        row = self._connection(params)
        connector = self.runtime.hub.connector(row["provider"])
        test = getattr(connector, "test", None)
        if test is None:
            raise RpcError(
                "invalid_params", f"{row['provider']} does not support connection tests"
            )
        result = test(Connection(**row))
        payload = {"provider": row["provider"], "connection_id": row["id"], **result}
        self._emit("connections.tested", payload)
        return payload

    def onboarding_status(self, params: dict[str, Any]) -> dict[str, Any]:
        """Report the persisted first-run state.

        The secret store is authoritative; ``settings.onboarded`` mirrors a
        stored ``GATHER_ONBOARDED`` value (useful when the secret backend is
        temporarily unreadable), and the environment is the CLI escape hatch.
        """
        store = self.secret_store
        try:
            onboarded_raw = store.get("GATHER_ONBOARDED") if store is not None else None
        except SecretStoreError:
            # A locked keychain must not make an existing install look new.
            onboarded_raw = None
        if onboarded_raw is None:
            onboarded_raw = os.environ.get("GATHER_ONBOARDED")
        onboarded = (
            onboarded_raw is not None
            and onboarded_raw.strip().lower() in {"1", "true", "yes", "on"}
        )
        onboarded = onboarded or bool(getattr(self.runtime.settings, "onboarded", False))
        if self._onboarded_override is not None:
            onboarded = self._onboarded_override

        # The tenant actually in use, not a UI label. The shell keeps the
        # user-facing workspace name in its own flag store.
        org_name = self.runtime.settings.org_name or os.environ.get("GATHER_ORG_NAME")

        conn_rows = self.runtime.store.connections(self.org, status=None)
        connections = len(conn_rows) if conn_rows else 0
        live_models = tuple(
            spec for spec in self.runtime.settings.chain
            if self.keys.get(PROVIDER_KEY_ENV.get(spec.provider, ""))
        )
        return {
            "ok": True,
            "onboarded": onboarded,
            "needs_onboarding": not onboarded,
            "workspace": org_name,
            "connections": connections,
            "model_configured": len(live_models) > 0,
        }

    def onboarding_complete(self, params: dict[str, Any]) -> dict[str, Any]:
        store = self.secret_store
        writable = store is not None and store.writable
        if writable:
            store.set("GATHER_ONBOARDED", "1")
            # Older builds also stored the typed workspace name as
            # GATHER_ORG_NAME. That value feeds `ensure_org` on the next start
            # and would fork the tenant, so repair it on the way through.
            store.delete("GATHER_ORG_NAME")
        # Keep status coherent for the rest of this process even when the
        # backend is read-only (e.g. ``--secrets env`` in dev).
        self._onboarded_override = True
        self._emit("onboarding.completed", {"at": _timestamp()})
        return {"ok": True, "onboarded": True, "persisted": writable}

    def onboarding_reset(self, params: dict[str, Any]) -> dict[str, Any]:
        """Clear the first-run marker so the shell shows onboarding again."""
        store = self.secret_store
        writable = store is not None and store.writable
        if writable:
            store.delete("GATHER_ONBOARDED")
            store.delete("GATHER_ORG_NAME")
        self._onboarded_override = False
        self._emit("onboarding.reset", {"at": _timestamp()})
        return {"ok": True, "onboarded": False, "persisted": writable}

    # -- assistant --------------------------------------------------------- #

    def _assistant(self) -> Assistant:
        return Assistant(self.runtime, scheduler=self.scheduler, log=self.log)

    def agent_tools(self, params: dict[str, Any]) -> dict[str, Any]:
        """Introspection for the UI and tests; the model receives the same list."""
        return {"tools": self._assistant().tool_catalog()}

    def agent_chat(self, params: dict[str, Any]) -> dict[str, Any]:
        message = params.get("message")
        if not isinstance(message, str) or not message.strip():
            raise RpcError("invalid_params", "message is required")

        history = params.get("history") or []
        if not isinstance(history, list):
            raise RpcError("invalid_params", "history must be a list of messages")

        assistant = self._assistant()
        try:
            result = assistant.chat(message, history=history)
        except Exception as exc:  # noqa: BLE001 - surface, do not kill the worker
            self.log(f"[assistant] chat failed: {type(exc).__name__}: {exc}")
            raise RpcError("agent_failed", f"assistant failed: {exc}") from exc

        self._emit(
            "agent.replied",
            {
                "tool_calls": len(result.get("tool_calls") or []),
                "fixture": bool(result.get("fixture")),
                "degraded": bool(result.get("degraded")),
                "at": _timestamp(),
            },
        )
        return result

    def connections_connect(self, params: dict[str, Any]) -> dict[str, Any]:
        """Connect a provider.

        Two supported providers:
        - "gmail": runs the interactive OAuth 2.0 flow (slow, opens browser)
        - "fireflies": validates and stores an API key
        """
        provider = params.get("provider")
        if not provider:
            raise RpcError("invalid_params", "provider is required")

        if provider == "gmail":
            return self._connect_gmail_oauth(params)

        if provider == "fireflies":
            return self._connect_fireflies(params)

        raise RpcError("invalid_params", f"unknown provider: {provider}")

    def _connect_gmail_oauth(self, params: dict[str, Any]) -> dict[str, Any]:
        """Run the Gmail OAuth flow and upsert the connection.

        This is a slow operation because it involves a browser redirect.
        """
        from .. import __version__
        from ..config import PROVIDER_KEY_ENV, default_data_dir
        from ..secrets import SECRET_KEYS
        import os

        org = self.org

        # Read Google client credentials from the engine's secret keys.
        # Fall back to settings if not in keys (e.g. first-run dev).
        keys = dict(self.keys)  # copy
        client_id = keys.get("GOOGLE_CLIENT_ID") or os.environ.get("GOOGLE_CLIENT_ID", "")
        client_secret = keys.get("GOOGLE_CLIENT_SECRET") or os.environ.get("GOOGLE_CLIENT_SECRET", "")

        if not client_id or not client_secret:
            raise RpcError(
                "unauthorized",
                "Google sign-in is not configured in this build. "
                "Add GOOGLE_CLIENT_ID/SECRET to the environment or secret store.",
            )

        # Run the OAuth flow using the oauth module
        from ..oauth import connect_gmail_oauth as _oauth_connect

        try:
            tokens = _oauth_connect(
                client_id=client_id,
                client_secret=client_secret,
                timeout=240,
                open_browser=True,
            )
        except Exception as exc:  # noqa: BLE001 - flow cancelled / timed out
            raise RpcError("oauth_failed", f"OAuth flow failed: {exc}") from exc

        # Store the refresh token in the secret store so the connector picks it up.
        # Google occasionally omits it on re-consent; keep the working one then.
        refresh_token = tokens.get("refresh_token") or ""
        if not refresh_token:
            try:
                stored = (
                    self.secret_store.get("GMAIL_REFRESH_TOKEN")
                    if self.secret_store is not None
                    else None
                )
            except SecretStoreError:
                stored = None
            refresh_token = stored or self.keys.get("GMAIL_REFRESH_TOKEN") or ""
        if self.secret_store is not None and self.secret_store.writable:
            self.secret_store.set("GMAIL_REFRESH_TOKEN", refresh_token)
        self.keys["GMAIL_REFRESH_TOKEN"] = refresh_token

        # A running engine built its connector at startup. Apply the new grant
        # immediately so the next sync does not keep using a revoked token.
        connector = getattr(self.runtime, "gmail", None)
        if connector is not None and hasattr(connector, "update_credentials"):
            connector.update_credentials(
                client_id=client_id,
                client_secret=client_secret,
                refresh_token=refresh_token,
            )

        # Upsert the connection as a user-mode Gmail connection with the email as user_id
        user_email = tokens.get("user_email") or ""
        # Ensure the user exists in the store. Older builds used the Google
        # `sub` claim as the identity; prefer a real email address when present.
        users = self.runtime.store.users(org)
        user_match = next(
            (u for u in users if user_email and (u.get("id") == user_email or u.get("email") == user_email)),
            None,
        )
        user_id = (
            user_match["id"]
            if user_match
            else self.runtime.store.add_user(org, user_email or "gmail-user", user_email or "gmail-user")
        )

        scopes = [scope for scope in str(tokens.get("scope") or "").split() if scope]
        if not scopes:
            scopes = [
                "https://www.googleapis.com/auth/gmail.readonly",
                "https://www.googleapis.com/auth/gmail.send",
                "https://www.googleapis.com/auth/gmail.modify",
            ]

        # Reuse the existing Gmail row even if the account identity changed
        # (e.g. `sub` -> email), so a reconnect cannot create a duplicate
        # connection that ingests the same mailbox twice.
        existing = self.runtime.store.connections(org, provider="gmail", status=None)
        if existing:
            connection_id = existing[0]["id"]
            self.runtime.store.update_connection_identity(
                connection_id, user_id=str(user_id), scopes=scopes
            )
            self.runtime.store.set_connection_status(connection_id, "active")
        else:
            connection_id = self.runtime.store.upsert_connection(
                org,
                "gmail",
                mode="user",
                user_id=str(user_id),
                scopes=scopes,
            )
            self.runtime.store.set_connection_status(connection_id, "active")

        # The old 403/backoff belonged to the previous grant; a reconnect is a
        # clean slate, and the next poll should run immediately.
        self.runtime.store.clear_connection_error(connection_id, next_sync_at=_timestamp())

        connection = self.runtime.store.get_connection(connection_id)

        self._emit(
            "connections.changed",
            {"connection_id": connection_id, "provider": "gmail", "action": "added"},
        )

        return {
            "provider": "gmail",
            "mode": "user",
            "user_email": user_email,
            "connection": connection,
            "ok": True,
        }

    def _connect_fireflies(self, params: dict[str, Any]) -> dict[str, Any]:
        """Validate and store a Fireflies API key, upsert the connection."""
        api_key = params.get("api_key") or ""
        if not api_key:
            raise RpcError("invalid_params", "Fireflies API key is required")

        org = self.org

        # Upsert a Fireflies connection (org mode)
        connection_id = self.runtime.store.upsert_connection(
            org,
            "fireflies",
            mode="org",
            user_id=None,
            scopes=[],
        )
        # Make sure it's active
        self.runtime.store.set_connection_status(connection_id, "active")

        connection = self.runtime.store.get_connection(connection_id)

        # Store the API key in the secret store
        if self.secret_store is not None and self.secret_store.writable:
            self.secret_store.set("FIREFLIES_API_KEY", api_key)
        self.keys["FIREFLIES_API_KEY"] = api_key
        connector = getattr(self.runtime, "fireflies", None)
        if connector is not None:
            connector.api_key = api_key

        # A new key invalidates the old failure/backoff state.
        self.runtime.store.clear_connection_error(connection_id, next_sync_at=_timestamp())

        self._emit(
            "connections.changed",
            {"connection_id": connection_id, "provider": "fireflies", "action": "added"},
        )

        return {
            "provider": "fireflies",
            "mode": "org",
            "ok": True,
        }

    def sync_now(self, params: dict[str, Any]) -> dict[str, Any]:
        if self.scheduler is not None:
            result = self.scheduler.run_now(block=False)
            return {"accepted": not result.get("busy", False), **result}
        outcomes = self.runtime.poll_once()
        return {
            "accepted": True,
            "events": sum(outcome.events for outcome in outcomes),
            "outcomes": [jsonable(outcome) for outcome in outcomes],
        }

    # -- capture ----------------------------------------------------------- #

    def meetings_list(self, params: dict[str, Any]) -> dict[str, Any]:
        return {
            "meetings": self.runtime.store.meetings(
                self.org,
                provider=params.get("provider"),
                limit=int(params.get("limit", 100)),
            )
        }

    def meetings_get(self, params: dict[str, Any]) -> dict[str, Any]:
        meeting_id = params.get("meeting_id") or params.get("id")
        if not meeting_id:
            raise RpcError("invalid_params", "meeting_id is required")
        meeting = self.runtime.store.get_meeting(str(meeting_id))
        if not meeting or meeting["org_id"] != self.org:
            raise RpcError("not_found", f"meeting {meeting_id} not found")
        return {
            "meeting": meeting,
            "tasks": self.runtime.store.tasks_for_meeting(str(meeting_id)),
        }

    def meetings_ingest(self, params: dict[str, Any]) -> dict[str, Any]:
        external_id = params.get("external_id") or params.get("meeting_id")
        if not external_id:
            raise RpcError("invalid_params", "external_id is required")
        external_id = str(external_id)
        provider = str(params.get("provider") or "fireflies")
        if provider not in self.runtime.hub.providers():
            raise RpcError("invalid_params", f"unknown provider {provider!r}")

        transcript = params.get("transcript")
        manual = transcript is not None
        if not manual and provider != "fireflies":
            raise RpcError(
                "invalid_params",
                "only Fireflies meetings can be fetched by id; supply a transcript "
                "for other providers",
            )
        row_id: str | None = None
        if manual:
            if not isinstance(transcript, str):
                raise RpcError("invalid_params", "transcript must be a string")
            started_at = params.get("started_at")
            if isinstance(started_at, str):
                try:
                    started_at = datetime.fromisoformat(started_at)
                except ValueError as exc:
                    raise RpcError("invalid_params", f"bad started_at: {exc}") from exc
            elif started_at is not None and not isinstance(started_at, datetime):
                raise RpcError("invalid_params", "started_at must be an ISO string")

            raw_participants = params.get("participants") or []
            if not isinstance(raw_participants, list):
                raise RpcError("invalid_params", "participants must be a list")
            try:
                participants = [
                    Person(**person) if isinstance(person, dict) else Person(name=str(person))
                    for person in raw_participants
                ]
                meeting = Meeting(
                    id="pending",
                    org_id=self.org,
                    provider=provider,
                    external_id=external_id,
                    title=str(params.get("title") or "Pasted meeting"),
                    started_at=started_at,
                    duration_min=params.get("duration_min"),
                    participants=participants,
                    transcript=transcript,
                    summary=params.get("summary"),
                )
            except Exception as exc:  # noqa: BLE001 - pydantic validation
                raise RpcError("invalid_params", f"bad meeting: {exc}") from exc
            row_id = self.runtime.store.upsert_meeting(meeting)

        thread_id = str(
            params.get("thread_id") or f"meeting-{external_id}-{uuid.uuid4().hex[:6]}"
        )

        def work() -> tuple[dict, str]:
            self.runtime.llm.start_run()
            return run_meeting(
                self.runtime.meeting_graph,
                org_id=self.org,
                external_id=external_id,
                thread_id=thread_id,
                meeting_row_id=row_id,
                manual=manual,
            )

        try:
            (state, returned_thread), handle = self.runtime.runs.run(
                "meeting", thread_id, work
            )
        except RunCancelled as exc:
            raise RpcError("cancelled", str(exc)) from exc

        proposal_ids = self._announce_thread(returned_thread)
        self._emit(
            "data.changed", {"scopes": ["meetings", "proposals", "tasks"], "at": _timestamp()}
        )
        return {
            "thread_id": returned_thread,
            "run_id": handle.run_id,
            "state": jsonable(state),
            "proposal_ids": proposal_ids,
        }

    # -- runs -------------------------------------------------------------- #

    def runs_list(self, params: dict[str, Any]) -> dict[str, Any]:
        return {
            "runs": self.runtime.runs.list(
                active_only=bool(params.get("active_only", False)),
                limit=int(params.get("limit", 50)),
            )
        }

    def runs_get(self, params: dict[str, Any]) -> dict[str, Any]:
        run_id = params.get("run_id")
        if run_id:
            run = self.runtime.runs.get(str(run_id))
            if run is not None:
                return {"run": run.as_dict(), "thread": None}
        thread_id = params.get("thread_id")
        if not thread_id:
            raise RpcError("not_found", "run not found and no thread_id supplied")
        snapshot = self.runtime.meeting_graph.get_state(
            {"configurable": {"thread_id": str(thread_id)}}
        )
        return {
            "run": None,
            "thread": {
                "thread_id": str(thread_id),
                "values": jsonable(snapshot.values),
                "next": [str(node) for node in snapshot.next],
            },
        }

    def runs_cancel(self, params: dict[str, Any]) -> dict[str, Any]:
        run_id = params.get("run_id")
        if not run_id:
            raise RpcError("invalid_params", "run_id is required")
        cancelled = self.runtime.runs.cancel(str(run_id))
        run = self.runtime.runs.get(str(run_id))
        return {
            "cancelled": cancelled,
            "run": run.as_dict() if run is not None else None,
        }

    # -- tasks ------------------------------------------------------------- #

    def tasks_list(self, params: dict[str, Any]) -> dict[str, Any]:
        return {
            "tasks": self.runtime.store.tasks(
                self.org,
                status=params.get("status"),
                owner_email=params.get("owner_email"),
                limit=int(params.get("limit", 200)),
            )
        }

    def tasks_get(self, params: dict[str, Any]) -> dict[str, Any]:
        task_id = params.get("task_id") or params.get("id")
        if not task_id:
            raise RpcError("invalid_params", "task_id is required")
        task = self._task(str(task_id))
        return {
            "task": task,
            "events": self.runtime.store.task_events(task["id"]),
            "messages": self.runtime.store.messages_for_task(task["id"]),
        }

    def tasks_update(self, params: dict[str, Any]) -> dict[str, Any]:
        task_id = params.get("task_id") or params.get("id")
        if not task_id:
            raise RpcError("invalid_params", "task_id is required")
        task = self._task(str(task_id))
        fields = {key: value for key, value in params.items() if key in TASK_UPDATE_FIELDS}
        if not fields:
            raise RpcError("invalid_params", "no updatable fields supplied")
        if "status" in fields and fields["status"] not in {state.value for state in TaskStatus}:
            raise RpcError("invalid_params", f"unknown status {fields['status']!r}")
        if "title" in fields and not str(fields["title"]).strip():
            raise RpcError("invalid_params", "title cannot be empty")
        if "owner_email" in fields and fields["owner_email"]:
            fields["owner_email"] = str(fields["owner_email"]).lower()
        if "due_date" in fields and fields["due_date"]:
            try:
                date.fromisoformat(str(fields["due_date"]))
            except ValueError as exc:
                raise RpcError("invalid_params", f"bad due_date: {exc}") from exc
        if "owner_user_id" in fields and fields["owner_user_id"]:
            known = {
                user["id"] for user in self.runtime.store.users(self.org)
            }
            if str(fields["owner_user_id"]) not in known:
                raise RpcError("invalid_params", "unknown owner_user_id")

        updated = self.runtime.store.update_task(task["id"], **fields)
        self.runtime.store.add_task_event(
            self.org,
            task["id"],
            "updated",
            actor="human",
            payload={"fields": sorted(fields)},
        )
        self._emit(
            "tasks.changed",
            {"task_id": task["id"], "fields": sorted(fields), "at": _timestamp()},
        )
        return {
            "task": updated,
            "events": self.runtime.store.task_events(task["id"]),
        }

    # -- approvals --------------------------------------------------------- #

    def proposals_list(self, params: dict[str, Any]) -> dict[str, Any]:
        return {
            "proposals": self.runtime.store.proposals(
                self.org, status=params.get("status", "pending")
            )
        }

    def proposals_get(self, params: dict[str, Any]) -> dict[str, Any]:
        proposal_id = params.get("proposal_id") or params.get("id")
        if not proposal_id:
            raise RpcError("invalid_params", "proposal_id is required")
        return {"proposal": self._proposal(str(proposal_id))}

    def proposals_approve(self, params: dict[str, Any]) -> dict[str, Any]:
        proposal_id = params.get("proposal_id") or params.get("id")
        if not proposal_id:
            raise RpcError("invalid_params", "proposal_id is required")

        with self._decision_lock:
            proposal = self._proposal(str(proposal_id))
            if proposal["status"] != "pending":
                return {
                    "already": True,
                    "status": proposal["status"],
                    "proposal": proposal,
                }

            self.runtime.llm.start_run()
            kind = proposal["kind"]
            result: dict[str, Any] = {}
            if kind == "task_approval":
                tasks = (proposal.get("payload") or {}).get("tasks", [])
                approved = params.get("approved")
                if approved is None and params.get("approved_indices") is not None:
                    # The desktop UI selects rows; the graph speaks fingerprints.
                    indices = params["approved_indices"]
                    if not isinstance(indices, list) or not all(
                        isinstance(index, int) and not isinstance(index, bool)
                        for index in indices
                    ):
                        raise RpcError(
                            "invalid_params", "approved_indices must be a list of integers"
                        )
                    approved = [
                        tasks[index]["fingerprint"]
                        for index in indices
                        if 0 <= index < len(tasks) and tasks[index].get("fingerprint")
                    ]
                if approved is None:
                    approved = [task["fingerprint"] for task in tasks]
                thread_id = proposal.get("thread_id")
                if thread_id:
                    snapshot = self.runtime.meeting_graph.get_state(
                        {"configurable": {"thread_id": thread_id}}
                    )
                    if not snapshot.next and snapshot.values:
                        # Crash window: the graph finished but the decision was
                        # not recorded. Record it instead of re-running.
                        self.runtime.store.decide_proposal(
                            proposal["id"], "approved", "human"
                        )
                        self._emit(
                            "proposal.decided",
                            {
                                "proposal_id": proposal["id"],
                                "status": "approved",
                                "at": _timestamp(),
                            },
                        )
                        self._emit(
                            "data.changed",
                            {"scopes": ["proposals", "tasks"], "at": _timestamp()},
                        )
                        return {
                            "already": False,
                            "status": "approved",
                            "recovered": True,
                            "created": jsonable(snapshot.values.get("created", [])),
                        }
                    try:
                        state = resume_meeting(
                            self.runtime.meeting_graph,
                            thread_id,
                            approved=list(approved),
                            edits=params.get("edits"),
                        )
                    except Exception as exc:  # noqa: BLE001 - keep the proposal pending
                        raise RpcError(
                            "conflict",
                            f"could not resume the approval: {exc}",
                        ) from exc
                    result = {"created": jsonable(state.get("created", []))}
            elif kind == "nudge":
                payload = proposal.get("payload") or {}
                result = {
                    "delivered": jsonable(
                        self.runtime.notifier.send(
                            org_id=self.org,
                            to=payload.get("to"),
                            subject=payload.get("subject", "Follow-up"),
                            body=payload.get("body", ""),
                            channel="nudge",
                        )
                    )
                }

            self.runtime.store.decide_proposal(proposal["id"], "approved", "human")
            self._emit(
                "proposal.decided",
                {"proposal_id": proposal["id"], "status": "approved", "at": _timestamp()},
            )
            self._emit("data.changed", {"scopes": ["proposals", "tasks"], "at": _timestamp()})
            return {"already": False, "status": "approved", **result}

    def proposals_reject(self, params: dict[str, Any]) -> dict[str, Any]:
        proposal_id = params.get("proposal_id") or params.get("id")
        if not proposal_id:
            raise RpcError("invalid_params", "proposal_id is required")

        with self._decision_lock:
            proposal = self._proposal(str(proposal_id))
            if proposal["status"] != "pending":
                return {
                    "already": True,
                    "status": proposal["status"],
                    "proposal": proposal,
                }
            if proposal["kind"] == "task_approval" and proposal.get("thread_id"):
                snapshot = self.runtime.meeting_graph.get_state(
                    {"configurable": {"thread_id": proposal["thread_id"]}}
                )
                if not snapshot.next and snapshot.values:
                    # Already completed (or lost its interrupt): the decision is
                    # still valid, there is nothing to resume.
                    pass
                else:
                    try:
                        resume_meeting(
                            self.runtime.meeting_graph,
                            proposal["thread_id"],
                            approved=[],
                        )
                    except Exception as exc:  # noqa: BLE001
                        self.log(f"[approvals] could not resume rejection: {exc}")
            self.runtime.store.decide_proposal(proposal["id"], "rejected", "human")
            self._emit(
                "proposal.decided",
                {"proposal_id": proposal["id"], "status": "rejected", "at": _timestamp()},
            )
            self._emit("data.changed", {"scopes": ["proposals"], "at": _timestamp()})
            return {"already": False, "status": "rejected"}

    # -- models ------------------------------------------------------------ #

    def models_check(self, params: dict[str, Any]) -> dict[str, Any]:
        timeout = int(params.get("timeout", 45))
        specs = [
            spec
            for spec in self.runtime.settings.chain
            if self.keys.get(PROVIDER_KEY_ENV[spec.provider])
        ]
        if not specs:
            return {"usable": 0, "results": [], "note": "no model API keys configured"}

        results: list[dict[str, Any]] = []
        for spec in specs:
            llm = ProviderLLM(
                (spec,),
                max_calls=1,
                min_interval=0,
                timeout=timeout,
                log=self.log,
                api_keys=self.keys,
            )
            started = time.monotonic()
            try:
                ping = llm.json(Ping, PING_SYSTEM, PING_USER, hint="ping")
                results.append(
                    {
                        "model": spec.label,
                        "ok": bool(ping.ok),
                        "latency_s": round(time.monotonic() - started, 2),
                        "error": None,
                    }
                )
            except Exception as exc:  # noqa: BLE001 - a probe reports failures
                results.append(
                    {
                        "model": spec.label,
                        "ok": False,
                        "latency_s": round(time.monotonic() - started, 2),
                        "error": f"{type(exc).__name__}: {exc}"[:300],
                    }
                )
        usable = sum(1 for result in results if result["ok"])
        self._emit(
            "models.checked",
            {"usable": usable, "total": len(results), "at": _timestamp()},
        )
        return {"usable": usable, "results": results}

    def models_usage(self, params: dict[str, Any]) -> dict[str, Any]:
        return {
            "usage": self.runtime.llm.usage.as_dict(),
            "models": self._model_labels(),
            "fixture_mode": not self._model_labels(),
        }

    def scheduler_status(self, params: dict[str, Any]) -> dict[str, Any]:
        if self.scheduler is None:
            return {"enabled": False, "running": False}
        return {"enabled": True, **self.scheduler.status()}
