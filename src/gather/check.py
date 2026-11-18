"""Probe the configured free-model chain, one model at a time.

    python -m gather.check

This is the honest feasibility test: it makes a real structured-output call to
each model with no fallback, and reports latency or the exact error. Free-tier
model ids rotate constantly, so run this before trusting a chain.
"""

from __future__ import annotations

import argparse
import sys
import time

from pydantic import BaseModel

from .config import load_settings
from .llm import ProviderLLM


class Ping(BaseModel):
    ok: bool
    role: str = ""


SYSTEM = "You are a model health check. Answer with the requested JSON only."
USER = 'Return {"ok": true, "role": "extractor"} exactly.'


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="gather check")
    parser.add_argument("--timeout", type=int, default=45)
    args = parser.parse_args(argv)

    settings = load_settings()
    available = settings.live_models

    print(f"configured chain ({len(settings.chain)}):")
    for spec in settings.chain:
        mark = "key ok " if spec in available else "no key "
        print(f"  [{mark}] {spec.label}")

    if not available:
        print("\nNo credentials. Set GROQ_API_KEY and/or OPENROUTER_API_KEY in .env")
        return 1

    print(f"\nprobing each model in isolation (timeout {args.timeout}s):")
    failures = 0
    for spec in available:
        llm = ProviderLLM(
            (spec,),
            max_calls=1,
            min_interval=0,
            timeout=args.timeout,
            log=lambda _: None,
        )
        started = time.monotonic()
        try:
            result = llm.json(Ping, SYSTEM, USER, hint="ping")
            elapsed = time.monotonic() - started
            print(f"  PASS  {spec.label:<48} {elapsed:5.1f}s  ok={result.ok}")
        except Exception as exc:  # noqa: BLE001
            elapsed = time.monotonic() - started
            failures += 1
            print(f"  FAIL  {spec.label:<48} {elapsed:5.1f}s  {type(exc).__name__}: {str(exc)[:110]}")

    usable = len(available) - failures
    print(f"\n{usable}/{len(available)} usable")
    if failures:
        print("Update GATHER_MODEL_CHAIN in .env with model ids that pass.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
