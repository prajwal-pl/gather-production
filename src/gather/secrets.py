"""Secret storage for the desktop engine.

Two rules:

1. **Never print secrets.** ``settings.get`` reports presence only; logs are
   redacted by the engine.
2. **Degrade, do not crash.** A headless Linux box often has no Secret Service.
   ``auto`` tries the OS keychain, then falls back to a ``0600`` JSON file and
   says so in the log. ``keychain`` (used by packaged builds) refuses to start
   instead of silently writing a weaker store.

The CLI and tests keep using environment variables; this module only changes
where a *bundled* engine reads its credentials from.
"""

from __future__ import annotations

import json
import os
import stat
import threading
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol, runtime_checkable

from .config import Settings, parse_chain

SERVICE_NAME = "gather"

# Credentials the engine may need. Values are never returned over IPC.
SECRET_KEYS: tuple[str, ...] = (
    "GROQ_API_KEY",
    "OPENROUTER_API_KEY",
    "FIREFLIES_API_KEY",
    "GOOGLE_CLIENT_ID",
    "GOOGLE_CLIENT_SECRET",
    "GMAIL_REFRESH_TOKEN",
    "GMAIL_ACCESS_TOKEN",
    "SLACK_WEBHOOK_URL",
    "SLACK_SIGNING_SECRET",
)

# Non-secret runtime settings that may be persisted beside the secrets. This
# keeps the desktop path free of a required ``.env`` file.
STORED_SETTINGS: dict[str, tuple[str, Callable[[str], Any]]] = {
    "GATHER_MODEL_CHAIN": ("chain", parse_chain),
    "GATHER_MIN_INTERVAL": ("min_interval_s", float),
    "GATHER_MAX_CALLS": ("max_calls_per_run", int),
    "GATHER_AUTO_APPROVE": (
        "auto_approve",
        lambda raw: raw.strip().lower() in {"1", "true", "yes", "on"},
    ),
    "GATHER_POLL_INTERVAL": ("poll_interval_s", float),
    "GATHER_SWEEP_INTERVAL": ("sweep_interval_s", float),
    "GATHER_ONBOARDED": ("onboarded", lambda raw: raw.strip().lower() in {"1", "true", "yes", "on"}),
    "GATHER_ORG_NAME": ("org_name", str),
}


class SecretStoreError(RuntimeError):
    """The secret backend is unavailable or refused an operation."""


@runtime_checkable
class SecretStore(Protocol):
    backend: str
    writable: bool

    def get(self, key: str) -> str | None: ...

    def set(self, key: str, value: str) -> None: ...

    def delete(self, key: str) -> None: ...


class EnvSecretStore:
    """Read-only view of the process environment (dev / tests)."""

    backend = "env"
    writable = False

    def __init__(self, env: Mapping[str, str] | None = None) -> None:
        self._env = env if env is not None else os.environ

    def get(self, key: str) -> str | None:
        return self._env.get(key) or None

    def set(self, key: str, value: str) -> None:
        raise SecretStoreError("the environment secret store is read-only")

    def delete(self, key: str) -> None:
        raise SecretStoreError("the environment secret store is read-only")


class FileSecretStore:
    """A ``0600`` JSON file. The fallback when no OS keychain is available.

    This is not encryption. It is file-permission protection, which is the
    honest best a local process can do without a user password. Packaged builds
    default to the keychain; this exists so the engine still works on machines
    that do not have one.
    """

    backend = "file"
    writable = True

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    def get(self, key: str) -> str | None:
        return self._read().get(key)

    def set(self, key: str, value: str) -> None:
        with self._lock:
            data = self._read()
            data[key] = value
            self._write(data)

    def delete(self, key: str) -> None:
        with self._lock:
            data = self._read()
            if data.pop(key, None) is not None:
                self._write(data)

    def _read(self) -> dict[str, str]:
        if not self.path.exists():
            return {}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8") or "{}")
        except (OSError, ValueError) as exc:
            raise SecretStoreError(f"cannot read {self.path}: {exc}") from exc
        if not isinstance(raw, dict):
            raise SecretStoreError(f"{self.path} is not a JSON object")
        return {str(key): str(value) for key, value in raw.items() if value is not None}

    def _write(self, data: dict[str, str]) -> None:
        tmp = self.path.with_name(self.path.name + ".tmp")
        payload = json.dumps(data, indent=2, sort_keys=True) + "\n"
        try:
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, self.path)
            os.chmod(self.path, 0o600)
        except OSError as exc:
            raise SecretStoreError(f"cannot write {self.path}: {exc}") from exc
        finally:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass

    def permissions_ok(self) -> bool:
        try:
            return stat.S_IMODE(self.path.stat().st_mode) == 0o600
        except OSError:
            return False


def _keyring_module():
    try:
        import keyring  # type: ignore[import-not-found]
    except ImportError:
        return None
    return keyring


def keyring_available() -> bool:
    keyring = _keyring_module()
    if keyring is None:
        return False
    try:
        backend = keyring.get_keyring()
        name = type(backend).__name__.lower()
        if "fail" in name or "null" in name:
            return False
        # A read probe is harmless and does not prompt.
        keyring.get_password(SERVICE_NAME, "__gather_probe__")
        return True
    except Exception:  # noqa: BLE001 - any backend error means "not usable"
        return False


class KeyringSecretStore:
    backend = "keyring"
    writable = True

    def __init__(self, service: str = SERVICE_NAME) -> None:
        keyring = _keyring_module()
        if keyring is None:
            raise SecretStoreError("the `keyring` package is not installed")
        self._keyring = keyring
        self.service = service

    def get(self, key: str) -> str | None:
        try:
            return self._keyring.get_password(self.service, key)
        except Exception as exc:  # noqa: BLE001
            raise SecretStoreError(f"keychain read failed: {exc}") from exc

    def set(self, key: str, value: str) -> None:
        try:
            self._keyring.set_password(self.service, key, value)
        except Exception as exc:  # noqa: BLE001
            raise SecretStoreError(f"keychain write failed: {exc}") from exc

    def delete(self, key: str) -> None:
        try:
            self._keyring.delete_password(self.service, key)
        except Exception:  # noqa: BLE001 - deleting a missing entry is fine
            pass


def build_secret_store(
    data_dir: str | Path,
    *,
    mode: str = "auto",
    log: Callable[[str], None] | None = None,
) -> SecretStore:
    """Resolve a secret backend.

    ``auto`` is the desktop default; ``keychain`` and ``file`` are explicit and
    never fall back. ``env`` exists for CLI/dev runs.
    """
    log = log or (lambda _message: None)
    mode = (mode or "auto").strip().lower()
    if mode == "env":
        return EnvSecretStore()
    if mode == "file":
        return FileSecretStore(Path(data_dir) / "secrets.json")
    if mode == "keychain":
        if not keyring_available():
            raise SecretStoreError(
                "no usable OS keychain; install a Secret Service provider or use "
                "--secrets file"
            )
        return KeyringSecretStore()
    if mode == "auto":
        if keyring_available():
            log("[secrets] using the OS keychain")
            return KeyringSecretStore()
        log("[secrets] no OS keychain available; using a 0600 file (not encrypted)")
        return FileSecretStore(Path(data_dir) / "secrets.json")
    raise SecretStoreError(
        f"unknown secret backend {mode!r}; use auto, keychain, file or env"
    )


def resolve_secrets(
    store: SecretStore | None,
    *,
    env: Mapping[str, str] | None = None,
    on_error: Callable[[str], None] | None = None,
) -> dict[str, str]:
    """Store first, then environment.

    A failure in one key does not discard the rest: a locked keychain should
    still let an engine start with whatever it can read and surface the error.
    """
    env = env if env is not None else os.environ
    resolved: dict[str, str] = {}
    for key in SECRET_KEYS:
        value: str | None = None
        if store is not None:
            try:
                value = store.get(key)
            except SecretStoreError as exc:
                if on_error is not None:
                    on_error(f"{key}: {exc}")
        if not value:
            value = env.get(key)
        if value:
            resolved[key] = value
    return resolved


def apply_stored_settings(settings: Settings, store: SecretStore | None) -> Settings:
    """Overlay persisted non-secret settings onto env-derived settings."""
    if store is None:
        return settings
    from dataclasses import fields as dc_fields
    valid_fields = {f.name for f in dc_fields(settings)}
    overrides: dict[str, Any] = {}
    for key, (field, cast) in STORED_SETTINGS.items():
        if field not in valid_fields:
            continue
        try:
            raw = store.get(key)
        except SecretStoreError:
            continue
        if raw is None or raw == "":
            continue
        try:
            overrides[field] = cast(raw)
        except (TypeError, ValueError):
            continue
    if overrides.get("chain") == ():
        overrides.pop("chain")
    return replace(settings, **overrides)


def secret_presence(
    store: SecretStore | None, *, env: Mapping[str, str] | None = None
) -> dict[str, bool]:
    resolved = resolve_secrets(store, env=env)
    return {key: key in resolved for key in SECRET_KEYS}
