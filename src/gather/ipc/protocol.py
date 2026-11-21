"""IPC wire format.

One UTF-8 JSON object per line. stdout carries protocol only; logs go to
stderr. The shapes here are deliberately tiny so the Tauri side can implement
them without a schema compiler.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

ErrorCode = str


class ProtocolError(ValueError):
    """A line could not be understood as a request."""


class RpcError(RuntimeError):
    """A handler failed in a way the client should see verbatim."""

    def __init__(
        self,
        code: ErrorCode,
        message: str,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


@dataclass(frozen=True)
class Request:
    id: str | int | None
    method: str
    params: dict[str, Any]


def _valid_id(value: Any) -> bool:
    return value is None or isinstance(value, (str, int)) and not isinstance(value, bool)


def parse_request(line: str) -> Request:
    text = (line or "").strip()
    if not text:
        raise ProtocolError("empty request")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ProtocolError(f"invalid JSON: {exc.msg}") from exc
    if not isinstance(payload, dict):
        raise ProtocolError("request must be a JSON object")

    request_id = payload.get("id")
    if not _valid_id(request_id):
        raise ProtocolError("id must be a string, integer or null")

    method = payload.get("method")
    if not isinstance(method, str) or not method.strip():
        raise ProtocolError("method must be a non-empty string")

    params = payload.get("params", {})
    if params is None:
        params = {}
    if not isinstance(params, dict):
        raise ProtocolError("params must be a JSON object")

    return Request(id=request_id, method=method.strip(), params=params)


def encode(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str)


def ok(request_id: str | int | None, result: Any) -> dict[str, Any]:
    return {"id": request_id, "ok": True, "result": result}


def fail(
    request_id: str | int | None,
    code: ErrorCode,
    message: str,
    *,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    error: dict[str, Any] = {"code": code, "message": message}
    if details:
        error["details"] = details
    return {"id": request_id, "ok": False, "error": error}


def event(name: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"event": name, "data": data or {}}
