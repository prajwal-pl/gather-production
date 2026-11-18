from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")


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
    "openrouter:inclusionai/ling-3.2-flash:free",
)

PROVIDER_KEY_ENV = {
    "groq": "GROQ_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
}


def _parse_chain(raw: str) -> tuple[ModelSpec, ...]:
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
    db_path: Path = ROOT / ".gather" / "gather.db"
    chain: tuple[ModelSpec, ...] = ()
    # Free tiers are rate limited. Pace calls and cap work per run.
    min_interval_s: float = 0.0
    max_calls_per_run: int = 24
    chunk_chars: int = 9000
    max_chunks: int = 12
    auto_approve: bool = False
    fireflies_secret: str = ""
    gmail_token: str = ""
    slack_signing_secret: str = ""
    slack_webhook_url: str = ""

    @property
    def live_models(self) -> tuple[ModelSpec, ...]:
        """Only the specs whose API key is actually present."""
        return tuple(
            spec for spec in self.chain if os.environ.get(PROVIDER_KEY_ENV[spec.provider])
        )

    @property
    def fixture_mode(self) -> bool:
        return not self.live_models


def load_settings(**overrides) -> Settings:
    base: dict = {
        "org_name": os.environ.get("GATHER_ORG_NAME", "Acme"),
        "db_path": Path(os.environ.get("GATHER_DB", ROOT / ".gather" / "gather.db")),
        "chain": _parse_chain(os.environ.get("GATHER_MODEL_CHAIN", ",".join(DEFAULT_CHAIN))),
        "min_interval_s": float(os.environ.get("GATHER_MIN_INTERVAL", "0")),
        "max_calls_per_run": int(os.environ.get("GATHER_MAX_CALLS", "24")),
        "chunk_chars": int(os.environ.get("GATHER_CHUNK_CHARS", "9000")),
        "max_chunks": int(os.environ.get("GATHER_MAX_CHUNKS", "12")),
        "auto_approve": _flag("GATHER_AUTO_APPROVE", "0"),
        "fireflies_secret": os.environ.get("FIREFLIES_WEBHOOK_SECRET", ""),
        "gmail_token": os.environ.get("GMAIL_WEBHOOK_TOKEN", ""),
        "slack_signing_secret": os.environ.get("SLACK_SIGNING_SECRET", ""),
        "slack_webhook_url": os.environ.get("SLACK_WEBHOOK_URL", ""),
    }
    base.update(overrides)
    return Settings(**base)
