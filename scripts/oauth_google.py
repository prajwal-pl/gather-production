#!/usr/bin/env python3
"""Google OAuth helper for gather.

Run this once to authorize gather and get a refresh token.

Usage:
    # Interactive: starts a local callback server
    python scripts/oauth_google.py --client-id <id> --client-secret <secret>

    # Manual: print the URL, then exchange a pasted code
    python scripts/oauth_google.py --client-id <id> --client-secret <secret> --url-only
    python scripts/oauth_google.py --client-id <id> --client-secret <secret> --code "<code>"
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from threading import Thread
from typing import Any

import httpx

# Scopes needed for Gmail read + send, plus Calendar/Drive read (for later).
SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
]

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
# Read from env so we stop hard-coding a rotating ngrok URL.
REDIRECT_URI = os.environ.get(
    "GATHER_OAUTH_REDIRECT_URI",
    "http://localhost:8080/callback",
)

_done = threading.Event()
_result: dict[str, Any] = {}


class OAuthHandler(BaseHTTPRequestHandler):
    """Handle the OAuth callback."""

    def do_GET(self) -> None:
        query = urllib.parse.urlparse(self.path).query
        params = urllib.parse.parse_qs(query)
        if "code" in params:
            _result["code"] = params["code"][0]
            self._respond(200, "<h1>Authorization successful!</h1><p>You can close this window.</p>")
        elif "error" in params:
            _result["error"] = params["error"][0]
            self._respond(400, f"<h1>Error: {params['error'][0]}</h1>")
        else:
            self._respond(400, "<h1>Missing code or error in callback</h1>")
        _done.set()

    def _respond(self, status: int, html: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(html.encode())

    def log_message(self, format: str, *args) -> None:
        pass


def run_local_server(port: int, timeout_s: int = 300) -> dict[str, Any]:
    """Run a temporary HTTP server to catch the OAuth callback."""
    server = HTTPServer(("localhost", port), OAuthHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        if not _done.wait(timeout=timeout_s):
            return {"error": "timeout waiting for callback"}
    finally:
        server.shutdown()
    return _result


def get_authorization_url(client_id: str, redirect_uri: str = REDIRECT_URI) -> str:
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",
        "prompt": "consent",
    }
    return f"{AUTH_URL}?{urllib.parse.urlencode(params)}"


def exchange_code_for_tokens(
    client_id: str, client_secret: str, code: str, redirect_uri: str = REDIRECT_URI
) -> dict[str, Any]:
    data = {
        "client_id": client_id,
        "client_secret": client_secret,
        "code": code,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
    }
    response = httpx.post(TOKEN_URL, data=data, timeout=30)
    if response.status_code != 200:
        print(f"Token exchange failed ({response.status_code}): {response.text}", file=sys.stderr)
        sys.exit(1)
    return response.json()


def update_env(refresh_token: str) -> None:
    """Add or update GMAIL_REFRESH_TOKEN in .env."""
    env_path = Path(__file__).resolve().parent.parent / ".env"
    lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
    key = "GMAIL_REFRESH_TOKEN"
    updated = False
    out: list[str] = []
    for line in lines:
        if line.strip().startswith(f"{key}"):
            out.append(f"{key} = {json.dumps(refresh_token)}")
            updated = True
        else:
            out.append(line)
    if not updated:
        out.append(f"{key} = {json.dumps(refresh_token)}")
    env_path.write_text("\n".join(out) + "\n", encoding="utf-8")
    print(f"Updated {env_path}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Google OAuth helper for gather")
    parser.add_argument("--client-id", required=True, help="Google OAuth client ID")
    parser.add_argument("--client-secret", required=True, help="Google OAuth client secret")
    parser.add_argument("--redirect-uri", default=REDIRECT_URI, help="OAuth redirect URI")
    parser.add_argument("--port", type=int, default=8080, help="Local callback port")
    parser.add_argument("--url-only", action="store_true", help="Print the auth URL and exit")
    parser.add_argument("--code", help="Exchange an authorization code manually")
    parser.add_argument("--timeout", type=int, default=300, help="Seconds to wait for callback")
    args = parser.parse_args()

    if args.code:
        tokens = exchange_code_for_tokens(
            args.client_id, args.client_secret, args.code, args.redirect_uri
        )
    elif args.url_only:
        print(get_authorization_url(args.client_id, args.redirect_uri))
        return 0
    else:
        auth_url = get_authorization_url(args.client_id, args.redirect_uri)
        print("Opening browser for authorization...")
        print(f"If browser doesn't open, go to:\n{auth_url}\n")
        webbrowser.open(auth_url)
        result = run_local_server(args.port, args.timeout)
        if "error" in result:
            print(f"Authorization failed: {result['error']}", file=sys.stderr)
            return 1
        tokens = exchange_code_for_tokens(
            args.client_id, args.client_secret, result["code"], args.redirect_uri
        )

    print("\n=== TOKENS (store these securely) ===")
    print(f"Access Token:  {tokens['access_token']}")
    refresh = tokens.get("refresh_token")
    print(f"Refresh Token: {refresh or 'NOT RETURNED - re-run with prompt=consent'}")
    print(f"Expires In:    {tokens.get('expires_in', 'unknown')} seconds")
    print(f"Scope:         {tokens.get('scope', 'unknown')}")

    if refresh:
        update_env(refresh)
        print("\nYou can now run: uv run --no-sync gather sync")
    return 0


if __name__ == "__main__":
    sys.exit(main())
