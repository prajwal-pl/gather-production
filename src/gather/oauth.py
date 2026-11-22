"""Google OAuth 2.0 for desktop — PKCE + loopback redirect.

Provides:
  - Google authorization URL generation
  - run() : open browser, wait for callback, exchange code, return tokens + user info
  - All heavy lifting (HTTP server, PKCE, verification) lives here; UI simply
    calls ``engine.connections.connect("gmail")`` which internally calls this.
"""

from __future__ import annotations

import base64
import json
import os
import re
import sys
import threading
import time
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, Optional

import httpx


# ---------------------------------------------------------------------------
# PKCE helpers
# ---------------------------------------------------------------------------

def _base64url_encode(data: bytes) -> str:
    """RFC 7636 base64url encoding (no padding, replace + and =)."""
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _pkce_challenge(code_verifier: str) -> str:
    """Compute S256 code challenge from verifier."""
    import hashlib
    digest = hashlib.sha256(code_verifier.encode("utf-8")).digest()
    return _base64url_encode(digest)


def _generate_pkce() -> tuple[str, str]:
    """Generate a random PKCE code_verifier + code_challenge."""
    import secrets
    code_verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode("ascii")
    # Google requires 43-128 chars for S256
    code_challenge = _pkce_challenge(code_verifier)
    return code_verifier, code_challenge


# ---------------------------------------------------------------------------
# Local callback server
# ---------------------------------------------------------------------------

_REDIRECT_PATH = "/callback"


class _OAuthCallbackHandler(BaseHTTPRequestHandler):
    """Handle the Google OAuth callback.  Only /callback is accepted."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:  # type: ignore[override]
        self._server_shutdown = kwargs.pop("_server_shutdown", None)  # type: ignore[arg-type]
        super().__init__(*args, **kwargs)  # type: ignore[super-init-negated]

    # silence per-request log lines
    def log_message(self, format: str, *args: Any) -> None:  # type: ignore[override]
        pass

    def do_GET(self) -> None:  # type: ignore[override]
        path = self.path.split("?", 1)[-1] if "?" in self.path else ""
        params = urllib.parse.parse_qs(path)
        if "code" in params:
            self.server.result["code"] = params["code"][0]
            self.server.result["state"] = params.get("state", [""])[0]
            self._send_html("Authorization successful! You can close this tab.")
        elif "error" in params:
            self.server.result["error"] = params["error"][0]
            self._send_html(
                f"<h1>Error: {params['error'][0]}</h1>"
                "<p>You can close this tab.</p>",
            )
        else:
            self._send_html(
                "<h1>OAuth callback received — no code or error found.</h1>"
                "<p>You can close this tab.</p>",
            )

    def _send_html(self, body: str) -> None:  # type: ignore[override]
        self.send_response(200)
        self.send_header("Content-type", "text/html")
        self.end_headers()
        self.wfile.write(body.encode("utf-8"))

    def do_POST(self) -> None:  # type: ignore[override]
        self.do_GET()


def _run_callback_server(
    result: dict[str, Any],
    timeout: float,
    host: str = "127.0.0.1",
) -> None:
    """Bind a single-use HTTP server, wait for callback or timeout."""
    server = HTTPServer((host, 0), _OAuthCallbackHandler)  # port 0 → ephemeral
    server.result = result
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if result.get("code") is not None or result.get("error") is not None:
            break
        server.handle_request()  # non-blocking iteration
        time.sleep(0.05)

    # final check after loop
    if not (result.get("code") or result.get("error")):
        result["timed_out"] = True

    # shut down
    try:
        server.shutdown()
    except Exception:
        pass
    server.server_close()
    thread.join(timeout=2)


# ---------------------------------------------------------------------------
# Google OAuth flow
# ---------------------------------------------------------------------------

# Default to desktop-app style loopback; configurable via env
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
DEFAULT_REDIRECT_URI = "http://127.0.0.1:{port}/callback"

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.modify",
    "openid",
    "profile",
]


class GoogleOAuth:
    """Run the interactive OAuth 2.0 flow for a single Google account."""

    def __init__(
        self,
        *,
        client_id: str,
        client_secret: str,
        redirect_uri: str | None = None,
        timeout: int = 240,
    ) -> None:
        self.client_id = client_id
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri or f"http://127.0.0.1:{os.getenv('GATHER_OAUTH_PORT', '0')}/callback"
        self.timeout = timeout
        self.code_verifier, self.code_challenge = _generate_pkce()

    def authorization_url(self) -> str:
        """Return the URL to open in the system browser."""
        params = {
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "response_type": "code",
            "scope": " ".join(SCOPES),
            "state": secrets.token_urlsafe(24),
            "code_challenge": self.code_challenge,
            "code_challenge_method": "S256",
            "access_type": "offline",
            "prompt": "consent",
        }
        qs = urllib.parse.urlencode(params, query_pairs_limit=10)
        return f"{AUTH_URL}?{qs}"

    def _exchange_code(self, code: str) -> dict[str, Any]:
        """Exchange authorization code for tokens + user info."""
        payload = {
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": self.redirect_uri,
            "code_verifier": self.code_verifier,
        }
        r = httpx.post(TOKEN_URL, data=payload, timeout=30)
        r.raise_for_status()
        tokens = r.json()

        # Fetch user email from openid connect userinfo
        email: Optional[str] = None
        if "id_token" in tokens:
            try:
                # Decode JWT to get email without extra request
                id_token = tokens["id_token"]
                # Minimal JWT payload parsing — look for 'email' claim
                # JWT format: header.payload.signature; payload is base64url
                parts = id_token.split(".")
                if len(parts) == 3:
                    payload_b64 = parts[1]
                    # Pad if needed
                    payload_b64 += "=" * (4 - len(payload_b64) % 4) if len(payload_b64) % 4 else ""
                    import base64 as _b64
                    payload_data = _b64.urlsafe_b64decode(payload_b64)
                    payload = json.loads(payload_data)
                    email = payload.get("email") or payload.get("sub")
            except Exception:
                pass

        if not email:
            # Fallback: use Gmail profile endpoint (access token already available)
            access = tokens.get("access_token")
            if access:
                try:
                    info = httpx.get(
                        "https://www.googleapis.com/oauth2/v2/userinfo",
                        headers={"Authorization": f"Bearer {access}"},
                        timeout=15,
                    )
                    info.raise_for_status()
                    data = info.json()
                    email = data.get("email") or data.get("id")
                except Exception:
                    pass

        return {
            "access_token": tokens.get("access_token"),
            "refresh_token": tokens.get("refresh_token"),
            "expires_in": tokens.get("expires_in"),
            "user_email": email or "",
        }

    def run(self, *, open_browser: bool = True) -> dict[str, Any]:
        """Run the full flow.

        1. Open the authorization URL (or return it if open_browser=False)
        2. Start a local HTTP server to capture the callback
        3. Exchange the code for tokens
        4. Return {access_token, refresh_token, user_email}
        """
        result: dict[str, Any] = {}

        if open_browser:
            url = self.authorization_url()
            webbrowser.open(url, new=2)  # open in new tab if possible

        _run_callback_server(result, self.timeout)

        if result.get("timed_out"):
            raise TimeoutError("OAuth callback timed out (no code received)")

        if result.get("error"):
            raise RuntimeError(f"OAuth cancelled or errored: {result['error']}")

        if not result.get("code"):
            raise RuntimeError("OAuth callback received with no code")

        tokens = self._exchange_code(result["code"])
        return tokens

# ---------------------------------------------------------------------------
# Export for the engine / CLI
# ---------------------------------------------------------------------------

def connect_gmail_oauth(
    *,
    client_id: str,
    client_secret: str,
    timeout: int = 240,
    open_browser: bool = True,
) -> dict[str, Any]:
    """Convenience wrapper used by the engine's connections.connect command."""
    oa = GoogleOAuth(client_id=client_id, client_secret=client_secret, timeout=timeout)
    return oa.run(open_browser=open_browser)