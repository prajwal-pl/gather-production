"""Stdio JSON-lines adapter."""

from __future__ import annotations

from .handlers import Handlers, jsonable
from .protocol import ProtocolError, Request, RpcError
from .router import Router
from .stdio import StdioServer


def build_router(handlers: Handlers) -> Router:
    router = Router()
    handlers.register(router)
    return router


__all__ = [
    "Handlers",
    "ProtocolError",
    "Request",
    "Router",
    "RpcError",
    "StdioServer",
    "build_router",
    "jsonable",
]
