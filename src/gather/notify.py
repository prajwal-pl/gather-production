"""Outbound notifications.

Slack is the effective channel for internal nudges; email is for external
parties. Both sit behind one interface so graphs never care which is configured.
"""

from __future__ import annotations

from typing import Any, Callable

import httpx

from .config import Settings


class Notifier:
    def __init__(
        self,
        settings: Settings,
        log: Callable[[str], None] = print,
        *,
        webhook_url: str | None = None,
    ) -> None:
        self.settings = settings
        self.log = log
        # The engine resolves secrets from the keychain; ``None`` keeps the
        # environment/.env behaviour used by the CLI and tests.
        self.webhook_url = webhook_url if webhook_url is not None else settings.slack_webhook_url
        self.outbox: list[dict[str, Any]] = []

    def send(
        self,
        *,
        org_id: str,
        to: str | None,
        subject: str,
        body: str,
        channel: str = "console",
    ) -> dict[str, Any]:
        record = {
            "org_id": org_id,
            "to": to,
            "subject": subject,
            "body": body,
            "channel": channel,
        }
        self.outbox.append(record)

        if self.webhook_url:
            try:
                httpx.post(
                    self.webhook_url,
                    json={"text": f"*{subject}*\n{body}"},
                    timeout=15,
                )
                record["delivered"] = "slack"
            except Exception as exc:  # noqa: BLE001
                record["delivered"] = f"slack_failed: {exc}"

        self.log(f"[notify:{channel}] to={to or '-'} | {subject}")
        return record
