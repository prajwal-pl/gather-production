"""The only layer that talks to a model.

Two implementations behind one interface:

* ``ProviderLLM``  — free-tier providers (Groq, OpenRouter) in a fallback chain.
* ``FixtureLLM``   — deterministic offline stand-in so the whole pipeline,
  including chunking, JSON repair, merge and dedupe, can be tested with no keys.

Everything above this file asks for *structured output*, never for prose in a
place where code expects data.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Callable, Mapping, TypeVar

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, ValidationError

from .config import PROVIDER_KEY_ENV, ModelSpec, Settings
from .domain import parse_due_date

T = TypeVar("T", bound=BaseModel)


class LLMError(RuntimeError):
    """No provider in the chain produced a usable answer."""


class BudgetExceeded(LLMError):
    """Per-run call budget hit. Callers should degrade, not retry."""


def estimate_tokens(text: str) -> int:
    """Cheap, provider-agnostic estimate. Good enough for chunk budgeting."""
    return max(1, len(text) // 4)


def extract_json(text: str) -> Any:
    """Recover a JSON value from a chatty model reply.

    Free models like to wrap JSON in code fences or add a sentence before it.
    Rather than fight that in every prompt, recover it here once.
    """
    text = (text or "").strip()
    if not text:
        raise ValueError("empty model response")

    fenced = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE | re.MULTILINE).strip()
    for candidate in (text, fenced):
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass

    decoder = json.JSONDecoder()
    for i, ch in enumerate(text):
        if ch not in "{[":
            continue
        try:
            value, _ = decoder.raw_decode(text[i:])
            return value
        except json.JSONDecodeError:
            continue
    raise ValueError(f"no JSON value found in response: {text[:200]!r}")


@dataclass
class Usage:
    calls: int = 0
    prompt_chars: int = 0
    completion_chars: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "calls": self.calls,
            "prompt_chars": self.prompt_chars,
            "completion_chars": self.completion_chars,
            "approx_prompt_tokens": self.prompt_chars // 4,
            "approx_completion_tokens": self.completion_chars // 4,
        }


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                if block.get("type") == "text" and block.get("text"):
                    parts.append(str(block["text"]))
                elif block.get("text"):
                    parts.append(str(block["text"]))
        return "\n".join(parts)
    return str(content)


class BaseLLM:
    usage: Usage

    def start_run(self) -> None:
        """Reset any per-run budget before a graph/agent run. No-op by default."""

    def complete(self, system: str, user: str, *, hint: str | None = None) -> str:  # pragma: no cover
        raise NotImplementedError

    def json(
        self,
        schema: type[T],
        system: str,
        user: str,
        *,
        hint: str | None = None,
    ) -> T:
        """Ask for a pydantic model, with one repair attempt."""
        contract = json.dumps(schema.model_json_schema(), separators=(",", ":"))
        instruction = (
            "\n\nReturn ONLY a JSON object that validates against this JSON Schema:\n"
            f"{contract}\n"
            "No markdown fences. No commentary before or after the JSON."
        )
        raw = self.complete(system, user + instruction, hint=hint)
        try:
            return schema.model_validate(extract_json(raw))
        except (ValidationError, ValueError) as first_error:
            repair = (
                user
                + instruction
                + f"\n\nYour previous reply was invalid ({first_error}). "
                "Reply again with corrected JSON only."
            )
            raw2 = self.complete(system, repair, hint=hint)
            return schema.model_validate(extract_json(raw2))


class ProviderLLM(BaseLLM):
    """Try each (provider, model) in order. Rate limits and outages roll over."""

    def __init__(
        self,
        specs: tuple[ModelSpec, ...],
        *,
        max_calls: int = 24,
        min_interval: float = 0.0,
        temperature: float = 0.1,
        timeout: int = 90,
        log: Callable[[str], None] = print,
        api_keys: Mapping[str, str] | None = None,
    ) -> None:
        if not specs:
            raise ValueError("ProviderLLM needs at least one model spec")
        self.specs = specs
        self.max_calls = max_calls
        self.min_interval = min_interval
        self.temperature = temperature
        self.timeout = timeout
        self.log = log
        # An explicit mapping (the engine's keychain view) is authoritative.
        # Only ``None`` means "read the process environment" for CLI/dev.
        self._keys_explicit = api_keys is not None
        self.api_keys = dict(api_keys or {})
        self.usage = Usage()
        self._cache: dict[str, Any] = {}
        self._last_call = 0.0
        # Per-thread, per-run budget: a sync tick and a chat can overlap on the
        # slow worker pool without resetting each other's counts.
        self._local = threading.local()

    def start_run(self) -> None:
        """``max_calls`` is a per-run budget, not a lifetime one."""
        self._local.calls = 0

    def _key(self, provider: str) -> str:
        env_name = PROVIDER_KEY_ENV[provider]
        if self._keys_explicit:
            return self.api_keys.get(env_name, "")
        return os.environ.get(env_name, "")

    def _model(self, spec: ModelSpec):
        if spec.label in self._cache:
            return self._cache[spec.label]
        kwargs = dict(
            model=spec.model,
            temperature=self.temperature,
            max_tokens=1600,
            max_retries=0,
        )
        if spec.provider == "groq":
            from langchain_groq import ChatGroq

            # Reasoning models burn the token budget before emitting content.
            # Low effort keeps latency and cost near a normal instruct model.
            if spec.model.startswith(("openai/gpt-oss", "qwen/")):
                kwargs["reasoning_effort"] = "low"
            model = ChatGroq(
                api_key=self._key("groq"),
                timeout=self.timeout,  # seconds
                **kwargs,
            )
        elif spec.provider == "openrouter":
            from langchain_openrouter import ChatOpenRouter

            model = ChatOpenRouter(
                api_key=self._key("openrouter"),
                app_title="gather",
                timeout=self.timeout * 1000,  # this SDK expects milliseconds
                **kwargs,
            )
        else:  # pragma: no cover - config guards this
            raise LLMError(f"unsupported provider: {spec.provider}")
        self._cache[spec.label] = model
        return model

    def _pace(self) -> None:
        if self.min_interval <= 0:
            return
        wait = self.min_interval - (time.monotonic() - self._last_call)
        if wait > 0:
            time.sleep(wait)
        self._last_call = time.monotonic()

    def complete(self, system: str, user: str, *, hint: str | None = None) -> str:
        calls_this_run = getattr(self._local, "calls", 0)
        if calls_this_run >= self.max_calls:
            raise BudgetExceeded(
                f"call budget reached ({self.max_calls}); degrading this run"
            )

        last_error: Exception | None = None
        for spec in self.specs:
            try:
                messages = [SystemMessage(content=system), HumanMessage(content=user)]
                reply = self._model(spec).invoke(messages)
                text = _content_text(reply.content).strip()
                if not text:
                    raise LLMError("empty response")
                self.usage.calls += 1
                self._local.calls = calls_this_run + 1
                self.usage.prompt_chars += len(system) + len(user)
                self.usage.completion_chars += len(text)
                self._pace()
                return text
            except BudgetExceeded:
                raise
            except Exception as exc:  # noqa: BLE001 - provider failures are expected
                last_error = exc
                self.log(f"[llm] {spec.label} failed: {type(exc).__name__}: {str(exc)[:140]}")
                time.sleep(1.0)
                continue

        raise LLMError(f"all models failed; last error: {last_error}")


# --------------------------------------------------------------------------- #
# Offline fixture model
# --------------------------------------------------------------------------- #

_COMMIT_CUE = re.compile(
    r"\b(i'll|i will|we'll|we will|will send|will share|will draft|will book|"
    r"will update|will review|will follow up|need to|let's|can you|please|i can)\b",
    re.IGNORECASE,
)
_DONE_CUE = re.compile(r"\b(already|done|sent it|finished|no longer)\b", re.IGNORECASE)
_DUE_HINT = re.compile(
    r"\b(?:by|before|until|on|for|due)\s+"
    r"(today|tomorrow|eod|end of day|(?:next\s+)?(?:week|monday|tuesday|wednesday|"
    r"thursday|friday|saturday|sunday)|\d{4}-\d{2}-\d{2})",
    re.IGNORECASE,
)
# The due date is stored in its own column, so keep it out of the title.
_DUE_CLAUSE = re.compile(
    r"\s+(?:by|before|until|on|for)\s+"
    r"(?:today|tomorrow|eod|end of day|(?:next\s+)?(?:week|monday|tuesday|wednesday|"
    r"thursday|friday|saturday|sunday)|\d{4}-\d{2}-\d{2}).*$",
    re.IGNORECASE,
)


def _block(text: str, marker: str) -> str:
    """Pull the <<< ... >>> block that follows a marker line."""
    pattern = rf"{re.escape(marker)}[^\n]*\n<<<\n?(.*?)\n?>>>"
    match = re.search(pattern, text, re.DOTALL)
    return match.group(1) if match else ""


class FixtureLLM(BaseLLM):
    """Deterministic stand-in with the same contract as a real model.

    It looks for explicit commitment cues in the transcript, so it exercises
    chunking, validation, owner resolution, merge and dedupe exactly like a live
    model would — it just isn't doing the reasoning.
    """

    def __init__(self, today: date | None = None, log: Callable[[str], None] = print) -> None:
        self.today = today or date.today()
        self.log = log
        self.usage = Usage()

    def complete(self, system: str, user: str, *, hint: str | None = None) -> str:
        self.usage.calls += 1
        self.usage.prompt_chars += len(system) + len(user)
        if hint == "commitments":
            payload = json.dumps({"commitments": self._commitments(user)})
        elif hint == "reply_intent":
            payload = json.dumps(self._reply_intent(user))
        elif hint == "nudge":
            payload = self._nudge(user)
        else:
            payload = "{}"
        self.usage.completion_chars += len(payload)
        return payload

    # -- commitments ------------------------------------------------------- #

    def _commitments(self, user: str) -> list[dict[str, Any]]:
        chunk = _block(user, "TRANSCRIPT")
        people = dict(re.findall(r"^- ([^<\n]+?) <([^>\n]+)>$", user, re.MULTILINE))

        out: list[dict[str, Any]] = []
        for raw_line in chunk.splitlines():
            line = raw_line.strip()
            match = re.match(r"^([A-Z][\w.'\- ]{1,40}):\s*(.+)$", line)
            if not match:
                continue
            speaker, text = match.group(1).strip(), match.group(2).strip()
            if not _COMMIT_CUE.search(text) or _DONE_CUE.search(text):
                continue
            if len(text.split()) < 4:
                continue

            due_match = _DUE_HINT.search(text)
            due = parse_due_date(due_match.group(1) if due_match else None, self.today)

            owner = speaker
            if re.match(r"^(can you|could you|please)\b", text, re.IGNORECASE):
                others = [name for name in people if name != speaker]
                owner = others[0] if others else speaker

            title = re.sub(
                r"^(perfect|great|thanks|thank you|sure|right|good|nice|absolutely|"
                r"agreed|sounds good)[.!,]\s*",
                "",
                text,
                flags=re.IGNORECASE,
            )
            title = re.sub(
                r"^(so|okay|ok|yeah|and|but|well)\s+", "", title, flags=re.IGNORECASE
            )
            title = re.sub(
                r"^(i'll|i will|we'll|we will|let's|can you|could you|please|"
                r"i need to|we need to|i can)\s+",
                "",
                title,
                flags=re.IGNORECASE,
            )
            title = re.split(r"[.!?]", title)[0].strip()[:80]
            title = _DUE_CLAUSE.sub("", title).strip()[:80]
            if not title:
                continue

            out.append(
                {
                    "title": title[0].upper() + title[1:],
                    "detail": text,
                    "owner_name": owner,
                    "owner_email": people.get(owner),
                    "due_date": due.isoformat() if due else None,
                    "quote": text,
                    "confidence": 0.92 if due else 0.78,
                }
            )
        return out

    # -- reply intent ------------------------------------------------------- #

    def _reply_intent(self, user: str) -> dict[str, Any]:
        subject = _first_line(user, "SUBJECT")
        body = _block(user, "MESSAGE") or user
        text = f"{subject} {body}".lower()

        due = None
        for phrase in ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"):
            if phrase in text:
                due = parse_due_date(phrase, self.today)
                break

        if any(k in text for k in ("pushed to", "slipped to", "slid to", "moving to", "aiming for", "land by")):
            return {
                "intent": "date_change",
                "new_due_date": due.isoformat() if due else None,
                "summary": "Owner renegotiated the delivery date.",
                "evidence": body[:240],
                "confidence": 0.88 if due else 0.55,
            }
        if any(k in text for k in ("blocked", "waiting on", "can't proceed", "cannot proceed")):
            return {
                "intent": "blocked",
                "new_due_date": None,
                "summary": "Owner reports being blocked.",
                "evidence": body[:240],
                "confidence": 0.82,
            }
        if any(k in text for k in ("done", "shipped", "sent it", "completed", "finished")):
            return {
                "intent": "done",
                "new_due_date": None,
                "summary": "Owner reports the task is complete.",
                "evidence": body[:240],
                "confidence": 0.85,
            }
        if any(k in text for k in ("working on", "in progress", "picking it up")):
            return {
                "intent": "in_progress",
                "new_due_date": due.isoformat() if due else None,
                "summary": "Owner is actively working on it.",
                "evidence": body[:240],
                "confidence": 0.7,
            }
        return {
            "intent": "unrelated",
            "new_due_date": None,
            "summary": "No task-relevant signal detected.",
            "evidence": "",
            "confidence": 0.4,
        }

    # -- nudge draft -------------------------------------------------------- #

    def _nudge(self, user: str) -> str:
        task = _first_line(user, "TASK") or "the open item"
        why = _first_line(user, "WHY") or "it is still open"
        return (
            f"Quick check-in on this one: {task}. Flagging it because {why}. "
            "If it has moved, a one-line update is enough — and if the date slipped, "
            "tell me the new one and I'll update the tracker."
        )


def _first_line(text: str, key: str) -> str:
    match = re.search(rf"^{re.escape(key)}:\s*(.+)$", text, re.MULTILINE)
    return match.group(1).strip() if match else ""


def build_llm(
    settings: Settings,
    *,
    log: Callable[[str], None] = print,
    force_fixture: bool = False,
    api_keys: Mapping[str, str] | None = None,
) -> BaseLLM:
    keys = dict(api_keys) if api_keys is not None else None
    if force_fixture:
        specs: tuple[ModelSpec, ...] = ()
    elif keys is not None:
        specs = tuple(
            spec
            for spec in settings.chain
            if keys.get(PROVIDER_KEY_ENV[spec.provider])
        )
    else:
        specs = settings.live_models
    if not specs:
        log("[llm] no provider keys found -> offline fixture model")
        return FixtureLLM(log=log)
    log("[llm] chain: " + " -> ".join(spec.label for spec in specs))
    return ProviderLLM(
        specs,
        max_calls=settings.max_calls_per_run,
        min_interval=settings.min_interval_s,
        log=log,
        api_keys=keys,
    )
