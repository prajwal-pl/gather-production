from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# Dev convenience only. The bundled engine has its own data directory and its
# secrets come from the OS keychain, so a missing python-dotenv is not an error.
try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - exercised by the bundled sidecar
    load_dotenv = None  # type: ignore[assignment]

if load_dotenv is not None and (ROOT / ".env").exists():
    load_dotenv(ROOT / ".env")

APP_DIR_NAME = "gather"


@dataclass(frozen=True, slots=True)
class ModelSpec:
    provider: str
    model: str

    @property
    def label(self) -> str:
        return f"{self.provider}:{self.model}"


# Verified free chain (2026-11). Model ids rotate; `python -m gather.check` re-probes.
DEFAULT_CHAIN = (
    "groq:openai/gpt-oss-120b",
    "openrouter:nvidia/nemotron-3.5-lightning:free",
)

PROVIDER_KEY_ENV = {
    "groq": "GROQ_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
}


def default_data_dir() -> Path:
    """Where a desktop app is allowed to write.

    Tauri normally resolves this and passes ``--data-dir``. The fallback keeps
    ``python -m gather`` usable while developing without scattering files into
    the source tree.
    """
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_DIR_NAME
    if os.name == "nt":
        base = os.environ.get("APPDATA")
        return (Path(base) if base else Path.home() / "AppData" / "Roaming") / APP_DIR_NAME
    base = os.environ.get("XDG_DATA_HOME")
    return (Path(base) if base else Path.home() / ".local" / "share") / APP_DIR_NAME


def parse_chain(raw: str) -> tuple[ModelSpec, ...]:
    specs: list[ModelSpec] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        provider, _, model = part.partition(":")
        if provider in PROVIDER_KEY_ENV and model.strip():
            specs.append(ModelSpec(provider, model.strip()))
    return tuple(specs)


def _flag(name: str, default: str = "0") -> bool:
    return os.environ.get(name, default).strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True, slots=True)
class Settings:
    org_name: str = "Acme"
    # Storage is one directory. The shell owns the path; Python never guesses.
    data_dir: Path = field(default_factory=default_data_dir)
    db_path: Path = field(default_factory=lambda: default_data_dir() / "gather.db")
    checkpoints_path: Path = field(
        default_factory=lambda: default_data_dir() / "checkpoints.db"
    )
    chain: tuple[ModelSpec, ...] = ()
    # Free tiers are rate limited. Pace calls and cap work per run.
    min_interval_s: float = 0.0
    max_calls_per_run: int = 24
    chunk_chars: int = 9000
    max_chunks: int = 12
    auto_approve: bool = False
    # "http" enables the dev webhook routes; "poll"/"stdio" leave ingestion to
    # the poller (and, later, the stdio engine).
    ingress: str = "http"
    fireflies_secret: str = ""
    gmail_token: str = ""
    google_client_id: str = ""
    google_client_secret: str = ""
    gmail_refresh_token: str = ""
    slack_signing_secret: str = ""
    slack_webhook_url: str = ""
    # Engine scheduler. Fine-grained connection scheduling still lives in the
    # ``connections`` table; this is how often the engine checks what is due.
    poll_interval_s: float = 30.0
    sweep_interval_s: float = 6 * 60 * 60
    scheduler_enabled: bool = True
    # Persisted in the secret/settings store (`GATHER_ONBOARDED`). This is the
    # authoritative "first run finished" marker for the desktop shell.
    onboarded: bool = False

    @property
    def live_models(self) -> tuple[ModelSpec, ...]:
        """Only the specs whose API key is actually present."""
        return tuple(
            spec for spec in self.chain if os.environ.get(PROVIDER_KEY_ENV[spec.provider])
        )

    @property
    def fixture_mode(self) -> bool:
        return not self.live_models


def _path(value: str | Path | None) -> Path | None:
    return Path(value).expanduser() if value else None


def load_settings(**overrides) -> Settings:
    """Resolve storage paths, then everything else.

    Precedence:
      ``--data-dir`` / explicit kwargs  >  environment  >  OS app-data default
      ``GATHER_DB`` remains a development override and intentionally bypasses
      ``data_dir``; checkpoints then live next to that database.
    """
    explicit_dir = _path(overrides.pop("data_dir", None))
    env_dir = _path(os.environ.get("GATHER_DATA_DIR"))
    db_override = _path(overrides.pop("db_path", None)) or _path(os.environ.get("GATHER_DB"))

    # A bare DB override still pins the effective directory, so tests and
    # `GATHER_DB=...` runs keep all state beside that file.
    data_dir = (
        explicit_dir
        or env_dir
        or (db_override.parent if db_override else default_data_dir())
    )
    db_path = db_override or data_dir / "gather.db"

    ckpt_override = _path(overrides.pop("checkpoints_path", None)) or _path(
        os.environ.get("GATHER_CHECKPOINTS")
    )
    checkpoints_path = ckpt_override or db_path.parent / "checkpoints.db"

    base: dict = {
        "org_name": os.environ.get("GATHER_ORG_NAME", "Acme"),
        "data_dir": data_dir,
        "db_path": db_path,
        "checkpoints_path": checkpoints_path,
        "chain": parse_chain(os.environ.get("GATHER_MODEL_CHAIN", ",".join(DEFAULT_CHAIN))),
        "min_interval_s": float(os.environ.get("GATHER_MIN_INTERVAL", "0")),
        "max_calls_per_run": int(os.environ.get("GATHER_MAX_CALLS", "24")),
        "chunk_chars": int(os.environ.get("GATHER_CHUNK_CHARS", "9000")),
        "max_chunks": int(os.environ.get("GATHER_MAX_CHUNKS", "12")),
        "auto_approve": _flag("GATHER_AUTO_APPROVE", "0"),
        "ingress": os.environ.get("GATHER_INGRESS", "http").strip().lower(),
        "fireflies_secret": os.environ.get("FIREFLIES_WEBHOOK_SECRET", ""),
        "gmail_token": os.environ.get("GMAIL_WEBHOOK_TOKEN", ""),
        "google_client_id": os.environ.get("GOOGLE_CLIENT_ID", ""),
        "google_client_secret": os.environ.get("GOOGLE_CLIENT_SECRET", ""),
        "gmail_refresh_token": os.environ.get("GMAIL_REFRESH_TOKEN", ""),
        "slack_signing_secret": os.environ.get("SLACK_SIGNING_SECRET", ""),
        "slack_webhook_url": os.environ.get("SLACK_WEBHOOK_URL", ""),
        "poll_interval_s": float(os.environ.get("GATHER_POLL_INTERVAL", "30")),
        "sweep_interval_s": float(os.environ.get("GATHER_SWEEP_INTERVAL", str(6 * 60 * 60))),
        "scheduler_enabled": _flag("GATHER_SCHEDULER", "1"),
    }
    base.update(overrides)
    settings = Settings(**base)
    if settings.ingress not in {"http", "poll", "stdio"}:
        raise ValueError(
            f"unknown GATHER_INGRESS={settings.ingress!r}; use http, poll or stdio"
        )
    return settings
