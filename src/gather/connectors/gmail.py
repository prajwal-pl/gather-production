from __future__ import annotations

import base64
import json
import os
import re
import time
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path
from typing import Any, Mapping

import httpx

from ..domain import Event, EventKind, Message, ObjectRef, SyncBatch
from .base import (
    AuthExpired,
    Capability,
    Connection,
    RateLimited,
    SyncError,
    retry_after_seconds,
)

FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures"
API = "https://gmail.googleapis.com/gmail/v1/users/me"
TOKEN_URL = "https://oauth2.googleapis.com/token"
_MAX_HISTORY_PAGES = 20
_MAX_BACKFILL_PAGES = 20
# Gmail allows 250 quota units/user/second and a message.get costs 5 units.
# Ten calls per second keeps a backfill comfortably under the limit.
_DEFAULT_MIN_INTERVAL_S = 0.1

_QUOTE_MARKERS = (
    re.compile(r"^On .+ wrote:$", re.IGNORECASE),
    re.compile(r"^-{2,}\s*Original Message\s*-{2,}$", re.IGNORECASE),
    re.compile(r"^_{10,}$"),
)


class GmailConnector:
    """Gmail is a *two-hop* notification: Pub/Sub hits us with only a historyId,
    then we ask Gmail what actually changed since our stored cursor.

    That is why the cursor matters more than the payload here.
    """

    provider = "gmail"
    capabilities = frozenset({Capability.SEARCH_MESSAGES, Capability.SEND_MESSAGE})
    poll_interval_s = 120
    push_capable = True

    def __init__(
        self,
        *,
        access_token: str | None = None,
        client_id: str | None = None,
        client_secret: str | None = None,
        refresh_token: str | None = None,
        fixture_dir: Path | None = None,
        min_interval_s: float | None = None,
    ) -> None:
        self.access_token = access_token
        self.client_id = client_id
        self.client_secret = client_secret
        self.refresh_token = refresh_token
        self.fixture_dir = fixture_dir or FIXTURE_DIR
        self._token_expires_at: float = 0
        self.min_interval_s = (
            float(os.environ.get("GATHER_GMAIL_MIN_INTERVAL", _DEFAULT_MIN_INTERVAL_S))
            if min_interval_s is None
            else max(0.0, float(min_interval_s))
        )
        self._next_call_at: float = 0.0

    def update_credentials(
        self,
        *,
        client_id: str | None,
        client_secret: str | None,
        refresh_token: str | None,
    ) -> None:
        """Apply a fresh OAuth grant without restarting the engine.

        ``connections.connect("gmail")`` stores a new refresh token, but the
        connector was built at engine start. Without this, syncs keep using
        the old (possibly revoked) grant until the app is restarted.
        """
        self.client_id = client_id
        self.client_secret = client_secret
        self.refresh_token = refresh_token
        self.access_token = None
        self._token_expires_at = 0

    def _pace(self) -> None:
        """Stay under Gmail's per-user quota during a backfill burst."""
        if self.min_interval_s <= 0:
            return
        now = time.monotonic()
        if self._next_call_at > now:
            time.sleep(self._next_call_at - now)
            now = time.monotonic()
        self._next_call_at = now + self.min_interval_s

    def _refresh_access_token(self) -> str | None:
        """Refresh the access token using the refresh token."""
        if not (self.client_id and self.client_secret and self.refresh_token):
            return None
        data = {
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "refresh_token": self.refresh_token,
            "grant_type": "refresh_token",
        }
        response = httpx.post(TOKEN_URL, data=data, timeout=30)
        if response.status_code == 429:
            raise RateLimited(
                "google oauth rate limit",
                retry_after_s=retry_after_seconds((response.headers or {}).get("retry-after")),
            )
        if response.status_code != 200:
            raise AuthExpired(f"failed to refresh token: {response.text[:300]}")
        body = response.json()
        self.access_token = body["access_token"]
        expires_in = body.get("expires_in", 3600)
        self._token_expires_at = time.time() + expires_in - 60  # 60s buffer
        return self.access_token

    def _ensure_valid_token(self) -> str | None:
        """Get a valid access token, refreshing if necessary."""
        # If we have a static access token (no OAuth refresh configured), use it directly
        if self.access_token and not (self.client_id and self.client_secret and self.refresh_token):
            return self.access_token
        # If we have OAuth config, check expiry and refresh if needed
        if self.access_token and time.time() < self._token_expires_at:
            return self.access_token
        return self._refresh_access_token()

    # -- webhook ----------------------------------------------------------- #

    def parse_webhook(self, headers: Mapping[str, str], body: bytes) -> list[Event]:
        envelope: dict[str, Any] = json.loads(body or b"{}")
        message = envelope.get("message") or envelope
        data_b64 = message.get("data") or ""
        try:
            decoded = json.loads(_b64decode(data_b64)) if data_b64 else {}
        except (ValueError, json.JSONDecodeError):
            decoded = {}

        email_address = decoded.get("emailAddress") or ""
        history_id = str(decoded.get("historyId") or "")
        provider_event_id = message.get("messageId") or f"{email_address}:{history_id}"
        if not provider_event_id:
            return []
        return [
            Event(
                id=f"gmail:{provider_event_id}",
                provider=self.provider,
                kind=EventKind.MESSAGE_RECEIVED,
                occurred_at=datetime.now(timezone.utc),
                subject=ObjectRef(provider=self.provider, id=email_address, kind="mailbox"),
                payload={
                    "email_address": email_address,
                    "history_id": history_id,
                    "pubsub_message_id": provider_event_id,
                },
            )
        ]

    # -- sync -------------------------------------------------------------- #

    def sync(self, conn: Connection, limit: int = 50) -> SyncBatch:
        """Walk ``users.history.list`` from the stored historyId.

        Gmail's history window is finite. A 404 means the cursor is too old to
        continue, so the poller is told to backfill rather than silently skip.
        """
        token = self._token(conn)
        if not token:
            return SyncBatch(events=[], cursor=conn.cursor)
        if not conn.cursor:
            return SyncBatch(events=[], cursor=None, reset=True)

        cursor = conn.cursor
        page_token: str | None = None
        events: list[Event] = []
        seen: set[str] = set()

        for _ in range(_MAX_HISTORY_PAGES):
            params: dict[str, Any] = {
                "startHistoryId": cursor,
                "historyTypes": "messageAdded",
                "maxResults": max(1, min(limit, 500)),
            }
            if page_token:
                params["pageToken"] = page_token
            self._pace()
            response = httpx.get(
                f"{API}/history",
                headers={"Authorization": f"Bearer {token}"},
                params=params,
                timeout=30,
            )
            if response.status_code == 404:
                return SyncBatch(events=[], cursor=None, reset=True)
            _check(response)

            body = response.json()
            cursor = str(body.get("historyId") or cursor)
            for record in body.get("history", []):
                for item in record.get("messagesAdded", []):
                    message_id = (item.get("message") or {}).get("id")
                    if not message_id or message_id in seen:
                        continue
                    seen.add(message_id)
                    events.append(
                        Event(
                            id=f"gmail:{message_id}",
                            provider=self.provider,
                            kind=EventKind.MESSAGE_RECEIVED,
                            occurred_at=datetime.now(timezone.utc),
                            payload={"message_id": message_id},
                        )
                    )
            page_token = body.get("nextPageToken")
            if not page_token:
                break
        else:
            raise SyncError("gmail history pagination did not finish; try again later")

        return SyncBatch(events=events, cursor=cursor)

    def backfill(self, conn: Connection, since: datetime, limit: int = 100) -> SyncBatch:
        """List recent messages, then set the cursor to the current historyId.

        The profile is read *before* the listing so messages that arrive during
        the backfill are picked up by the next history sync (overlap is deduped).
        """
        token = self._token(conn)
        if not token:
            return SyncBatch(events=[], cursor=conn.cursor)

        cursor = self._profile(token)
        query = f"after:{int(since.timestamp())} -in:sent"
        page_token: str | None = None
        events: list[Event] = []
        seen: set[str] = set()

        for _ in range(_MAX_BACKFILL_PAGES):
            params: dict[str, Any] = {
                "q": query,
                "maxResults": max(1, min(limit, 500)),
            }
            if page_token:
                params["pageToken"] = page_token
            self._pace()
            response = httpx.get(
                f"{API}/messages",
                headers={"Authorization": f"Bearer {token}"},
                params=params,
                timeout=30,
            )
            _check(response)
            body = response.json()
            for item in body.get("messages", []):
                message_id = item.get("id")
                if not message_id or message_id in seen:
                    continue
                seen.add(message_id)
                events.append(
                    Event(
                        id=f"gmail:{message_id}",
                        provider=self.provider,
                        kind=EventKind.MESSAGE_RECEIVED,
                        occurred_at=datetime.now(timezone.utc),
                        payload={"message_id": message_id},
                    )
                )
            page_token = body.get("nextPageToken")
            if not page_token:
                break
        else:
            raise SyncError("gmail backfill pagination did not finish; try again later")

        return SyncBatch(events=events, cursor=cursor)

    def test(self, conn: Connection) -> dict[str, Any]:
        """Cheap connectivity check used by ``connections.test``."""
        token = self._token(conn)
        if not token:
            return {"ok": False, "error": "no Gmail credentials configured"}
        try:
            self._pace()
            response = httpx.get(
                f"{API}/profile",
                headers={"Authorization": f"Bearer {token}"},
                timeout=30,
            )
            _check(response)
            body = response.json()
            return {
                "ok": True,
                "detail": body.get("emailAddress"),
                "history_id": body.get("historyId"),
            }
        except Exception as exc:  # noqa: BLE001 - report, do not crash the engine
            return {"ok": False, "error": str(exc)[:300]}

    def _profile(self, token: str) -> str:
        self._pace()
        response = httpx.get(
            f"{API}/profile",
            headers={"Authorization": f"Bearer {token}"},
            timeout=30,
        )
        _check(response)
        history_id = response.json().get("historyId")
        if not history_id:
            raise SyncError("gmail profile did not return a historyId")
        return str(history_id)

    # -- capability -------------------------------------------------------- #

    def execute(self, conn: Connection, capability: Capability, args: dict[str, Any]) -> Any:
        if capability is Capability.SEARCH_MESSAGES:
            return self.search(conn, args.get("query", ""), limit=int(args.get("limit", 5)))
        if capability is Capability.SEND_MESSAGE:
            return self.send(
                conn,
                to=args["to"],
                subject=args["subject"],
                body=args["body"],
                thread_id=args.get("thread_id"),
            )
        raise ValueError(f"gmail cannot {capability}")

    def fetch_new(self, conn: Connection, payload: dict[str, Any]) -> list[Message]:
        """Turn one notification into zero or more canonical messages."""
        token = self._token(conn)
        if not token:
            return [self.fixture_message(conn.org_id)]

        history_id = payload.get("history_id")
        if history_id:
            batch = self.sync(conn, limit=20)
            if batch.cursor:
                conn.cursor = batch.cursor

        if payload.get("message_id"):
            return [self._fetch_message(conn, payload["message_id"])]

        messages: list[Message] = []
        if history_id:
            self._pace()
            response = httpx.get(
                f"{API}/history",
                headers={"Authorization": f"Bearer {token}"},
                params={"startHistoryId": history_id, "historyTypes": "messageAdded"},
                timeout=30,
            )
            if response.status_code == 200:
                for record in response.json().get("history", []):
                    for item in record.get("messagesAdded", []):
                        messages.append(self._fetch_message(conn, item["message"]["id"]))
        return messages

    def search(self, conn: Connection, query: str, limit: int = 5) -> list[dict[str, Any]]:
        token = self._token(conn)
        if not token:
            fixture = self.fixture_message(conn.org_id)
            text = f"{fixture.subject} {fixture.body_text}".lower()
            stop = {"with", "this", "that", "from", "have", "your", "will", "about", "they", "when"}
            terms = [
                term
                for term in re.findall(r"[a-z0-9]{4,}", query.lower())
                if term not in stop
            ]
            if not terms or any(term in text for term in terms):
                return [
                    {
                        "provider": self.provider,
                        "external_id": fixture.external_id,
                        "thread_id": fixture.thread_id,
                        "subject": fixture.subject,
                        "snippet": fixture.body_text[:240],
                        "sent_at": fixture.sent_at.isoformat() if fixture.sent_at else None,
                        "score": 0.72,
                    }
                ]
            return []

        self._pace()
        response = httpx.get(
            f"{API}/messages",
            headers={"Authorization": f"Bearer {token}"},
            params={"q": query, "maxResults": limit},
            timeout=30,
        )
        _check(response)
        hits: list[dict[str, Any]] = []
        for item in response.json().get("messages", [])[:limit]:
            self._pace()
            meta = httpx.get(
                f"{API}/messages/{item['id']}",
                headers={"Authorization": f"Bearer {token}"},
                params={"format": "metadata", "metadataHeaders": ["Subject", "From", "Date"]},
                timeout=30,
            )
            body = meta.json() if meta.status_code == 200 else {}
            headers = {
                header["name"].lower(): header["value"]
                for header in body.get("payload", {}).get("headers", [])
            }
            hits.append(
                {
                    "provider": self.provider,
                    "external_id": item["id"],
                    "thread_id": item.get("threadId", item["id"]),
                    "subject": headers.get("subject", ""),
                    "snippet": body.get("snippet", ""),
                    "sent_at": headers.get("date"),
                    "score": 0.7,
                }
            )
        return hits

    def send(
        self,
        conn: Connection,
        *,
        to: str,
        subject: str,
        body: str,
        thread_id: str | None = None,
    ) -> dict[str, Any]:
        token = self._token(conn)
        if not token:
            return {"sent": False, "dry_run": True, "to": to, "subject": subject, "body": body}

        message = EmailMessage()
        message["To"] = to
        message["Subject"] = subject
        message["From"] = "me"
        message.set_content(body)
        raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
        payload: dict[str, Any] = {"raw": raw}
        if thread_id:
            payload["threadId"] = thread_id

        self._pace()
        response = httpx.post(
            f"{API}/messages/send",
            headers={"Authorization": f"Bearer {token}"},
            json=payload,
            timeout=30,
        )
        _check(response)
        return {"sent": True, "id": response.json().get("id"), "dry_run": False}

    # -- internals --------------------------------------------------------- #

    def _token(self, conn: Connection) -> str | None:
        # First check for per-connection secret_ref (env var name)
        if conn.secret_ref and os.environ.get(conn.secret_ref):
            return os.environ[conn.secret_ref]
        # Then check connector-level credentials. With OAuth config we can mint
        # an access token from the refresh token even when we start with none.
        if self.access_token or (self.client_id and self.client_secret and self.refresh_token):
            return self._ensure_valid_token()
        # Then check env for raw access token
        env_token = os.environ.get("GMAIL_ACCESS_TOKEN")
        if env_token:
            self.access_token = env_token
            return self._ensure_valid_token()
        return None

    def _fetch_message(self, conn: Connection, message_id: str) -> Message:
        token = self._token(conn)
        assert token
        self._pace()
        response = httpx.get(
            f"{API}/messages/{message_id}",
            headers={"Authorization": f"Bearer {token}"},
            params={"format": "full"},
            timeout=30,
        )
        _check(response)
        body = response.json()
        headers = {
            header["name"].lower(): header["value"]
            for header in body.get("payload", {}).get("headers", [])
        }
        return Message(
            id=message_id,
            org_id=conn.org_id,
            provider=self.provider,
            external_id=message_id,
            thread_id=body.get("threadId", message_id),
            from_email=_email_only(headers.get("from", "")),
            from_name=_display_name(headers.get("from", "")),
            to_emails=[_email_only(headers.get("to", ""))],
            subject=headers.get("subject", ""),
            body_text=_message_text(body.get("payload", {})),
            sent_at=_parse_date(headers.get("date")),
        )

    def fixture_message(self, org_id: str) -> Message:
        path = self.fixture_dir / "gmail_reply.json"
        raw = json.loads(path.read_text(encoding="utf-8"))
        return Message(
            id=raw["external_id"],
            org_id=org_id,
            provider=self.provider,
            external_id=raw["external_id"],
            thread_id=raw.get("thread_id", raw["external_id"]),
            from_email=raw["from_email"],
            from_name=raw.get("from_name", ""),
            to_emails=raw.get("to_emails", []),
            subject=raw.get("subject", ""),
            body_text=strip_quoted(raw.get("body_text", "")),
            sent_at=_parse_date(raw.get("sent_at")),
        )


_RATE_LIMIT_REASONS = (
    "ratelimitexceeded",
    "userratelimitexceeded",
    "quotaexceeded",
    "dailylimitexceeded",
    "resource_exhausted",
)
_REAUTH_REASONS = (
    "insufficientpermissions",
    "access_token_scope_insufficient",
    "permission_denied",
)
_API_DISABLED_REASONS = ("accessnotconfigured", "service_disabled")


def _google_error(response: Any) -> tuple[str, str]:
    """Extract ``(message, reasons)`` from a Google API error body."""
    try:
        body = response.json()
    except Exception:  # noqa: BLE001 - HTML/proxy error pages are valid too
        return "", ""
    error = body.get("error") if isinstance(body, dict) else None
    if not isinstance(error, dict):
        return "", ""
    message = str(error.get("message") or "")
    reasons: list[str] = []
    for item in error.get("errors") or []:
        if isinstance(item, dict) and item.get("reason"):
            reasons.append(str(item["reason"]).lower())
    if error.get("status"):
        reasons.append(str(error["status"]).lower())
    return message, ",".join(reasons)


def _check(response: Any) -> None:
    """Turn provider status codes into poller-level failures.

    Gmail uses **403** for several unrelated conditions: quota/rate limits,
    missing scopes, and a disabled API all arrive this way. Classifying the
    body gives the user an actionable message and lets the poller choose
    between "slow down" and "reconnect".
    """
    status = getattr(response, "status_code", 200)
    if status < 400:
        return

    message, reasons = _google_error(response)
    detail = message or getattr(response, "reason_phrase", "") or f"HTTP {status}"
    headers = getattr(response, "headers", {}) or {}

    if status == 401:
        raise AuthExpired(f"gmail credential expired or was revoked: {detail}")
    if status == 429 or (status == 403 and any(reason in reasons for reason in _RATE_LIMIT_REASONS)):
        raise RateLimited(
            f"gmail rate limit: {detail}",
            retry_after_s=retry_after_seconds(headers.get("retry-after")),
        )
    if status == 403 and any(reason in reasons for reason in _API_DISABLED_REASONS):
        raise SyncError(
            "Gmail API is disabled for this Google Cloud project. "
            "Enable it in the API Library and retry."
        )
    if status == 403 and any(reason in reasons for reason in _REAUTH_REASONS):
        raise AuthExpired(
            f"gmail permission denied: {detail}. "
            "Reconnect Gmail from Connections and grant read access."
        )
    if status == 403:
        raise SyncError(f"gmail refused the request: {detail}")
    if message and status >= 400:
        raise SyncError(f"gmail {status}: {detail}")
    response.raise_for_status()


def _b64decode(value: str) -> str:
    padded = value + "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(padded.encode()).decode("utf-8", errors="replace")


def _email_only(raw: str) -> str:
    match = re.search(r"<([^>]+)>", raw)
    return (match.group(1) if match else raw).strip().lower()


def _display_name(raw: str) -> str:
    match = re.match(r"^\s*\"?([^\"<]+?)\"?\s*<", raw)
    return match.group(1).strip() if match else ""


def _parse_date(raw: str | None) -> datetime | None:
    if not raw:
        return None
    from email.utils import parsedate_to_datetime

    try:
        return parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None


def _message_text(payload: dict[str, Any]) -> str:
    plain: list[str] = []
    html: list[str] = []

    def walk(part: dict[str, Any]) -> None:
        mime = part.get("mimeType", "")
        data = (part.get("body") or {}).get("data")
        if data:
            decoded = _b64decode(data)
            if mime == "text/plain":
                plain.append(decoded)
            elif mime == "text/html":
                html.append(decoded)
        for child in part.get("parts") or []:
            walk(child)

    walk(payload)
    text = "\n".join(plain) if plain else re.sub(r"<[^>]+>", " ", "\n".join(html))
    return strip_quoted(text)


def strip_quoted(text: str) -> str:
    """Every reply carries the whole thread. Strip it before an LLM sees it."""
    kept: list[str] = []
    for line in text.splitlines():
        if any(marker.match(line.strip()) for marker in _QUOTE_MARKERS):
            break
        if line.lstrip().startswith(">"):
            continue
        kept.append(line)
    return "\n".join(kept).strip()
