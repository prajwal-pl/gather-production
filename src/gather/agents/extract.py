"""Extraction: transcript -> candidate commitments.

This is the highest-value LLM call in the product, so the prompt is strict and
the output is narrow. Everything after this file is deterministic.
"""

from __future__ import annotations

from datetime import date, datetime
from difflib import SequenceMatcher
from typing import Any, Callable

from ..domain import Commitment, CommitmentBatch, Meeting, Person, due_from_text, normalize_title, parse_due_date
from ..llm import BaseLLM, BudgetExceeded, LLMError, estimate_tokens

SYSTEM = """You are a meticulous meeting analyst. You extract concrete follow-up commitments from a transcript.

A commitment is a specific action that a named person agreed to do, or was explicitly asked to do.

Include it only when the transcript supports it. Exclude:
- vague intentions ("we should think about...", "maybe later")
- work that is already finished ("I already sent it")
- general discussion, opinions, or questions that assign no action

For each commitment return:
- title: short imperative phrase, max 12 words, WITHOUT any due date or owner
- detail: one sentence of context
- owner_name: the person responsible, spelled as in the transcript
- owner_email: only if the participant list provides it, otherwise null
- due_date: YYYY-MM-DD if stated or clearly implied, otherwise null
- quote: the exact sentence(s) that justify it
- confidence: 0.0-1.0, lower when the wording is indirect

Rules:
- Never invent an owner, a date, or work that is not in the text.
- If someone says "can you..." the owner is the person being asked, not the speaker.
- If the same commitment is repeated, return it once.
- If the chunk contains no commitments, return {"commitments": []}.

Return ONLY a JSON object: {"commitments": [...]}"""


def chunk_transcript(
    text: str,
    *,
    max_chars: int = 9000,
    max_chunks: int = 12,
    overlap_lines: int = 2,
) -> tuple[list[str], bool]:
    """Split by speaker turns into budget-sized chunks.

    Free tiers meter tokens hard, so a 60-minute transcript must never be sent
    as one call. Two lines of overlap keeps a commitment that spans a boundary
    intact in at least one chunk.
    """
    lines = text.splitlines()
    chunks: list[str] = []
    current: list[str] = []
    size = 0

    for line in lines:
        if current and size + len(line) + 1 > max_chars:
            chunks.append("\n".join(current))
            current = current[-overlap_lines:] if overlap_lines else []
            size = sum(len(item) + 1 for item in current)
        current.append(line)
        size += len(line) + 1
    if current:
        chunks.append("\n".join(current))

    truncated = len(chunks) > max_chunks
    return chunks[:max_chunks], truncated


def extract_from_chunk(
    llm: BaseLLM,
    *,
    meeting: Meeting,
    chunk: str,
    index: int,
    total: int,
    today: date,
) -> list[Commitment]:
    people = "\n".join(
        f"- {p.name} <{p.email}>" if p.email else f"- {p.name}" for p in meeting.participants
    )
    user = f"""MEETING: {meeting.title}
DATE: {meeting.started_at.isoformat() if meeting.started_at else "unknown"}
TODAY: {today.isoformat()}
PARTICIPANTS:
{people or "- unknown"}

TRANSCRIPT (chunk {index + 1} of {total}):
<<<
{chunk}
>>>"""
    batch = llm.json(CommitmentBatch, SYSTEM, user, hint="commitments")
    reference = meeting.started_at.date() if meeting.started_at else today
    results: list[Commitment] = []
    for item in batch.commitments:
        if not item.title.strip():
            continue
        # The model proposes a date; the transcript decides it.
        parsed = due_from_text(item.quote, reference) or parse_due_date(item.due_date, reference)
        item.due_date = parsed.isoformat() if parsed else None
        results.append(item)
    return results


def extract_commitments(
    llm: BaseLLM,
    meeting: Meeting,
    *,
    max_chars: int = 9000,
    max_chunks: int = 12,
    today: date | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> tuple[list[Commitment], dict[str, Any]]:
    today = today or date.today()
    chunks, truncated = chunk_transcript(meeting.transcript, max_chars=max_chars, max_chunks=max_chunks)

    found: list[Commitment] = []
    errors: list[str] = []
    hint = ""
    for index, chunk in enumerate(chunks):
        if progress is not None:
            # Raises RunCancelled at the active run's next boundary.
            progress(index, len(chunks))
        try:
            found.extend(
                extract_from_chunk(
                    llm, meeting=meeting, chunk=chunk, index=index, total=len(chunks), today=today
                )
            )
        except BudgetExceeded:
            hint = "stopped early: per-run model budget reached"
            break
        except (LLMError, ValueError) as exc:
            errors.append(f"chunk {index + 1}: {exc}")
            continue

    merged = merge_commitments(found)
    stats = {
        "chunks": len(chunks),
        "truncated": truncated,
        "raw_commitments": len(found),
        "after_merge": len(merged),
        "approx_prompt_tokens": estimate_tokens(meeting.transcript),
        "errors": errors,
        "note": hint,
    }
    return merged, stats


def merge_commitments(items: list[Commitment]) -> list[Commitment]:
    """Collapse restatements. Deterministic: same owner + near-identical title."""
    merged: list[Commitment] = []
    for item in items:
        owner = (item.owner_email or item.owner_name or "").strip().lower()
        title = normalize_title(item.title)
        for existing in merged:
            same_owner = (existing.owner_email or existing.owner_name or "").strip().lower() == owner
            if not same_owner:
                continue
            if SequenceMatcher(None, normalize_title(existing.title), title).ratio() >= 0.84:
                if item.confidence > existing.confidence:
                    existing.confidence = item.confidence
                if len(item.quote) > len(existing.quote):
                    existing.quote = item.quote
                if existing.due_date is None:
                    existing.due_date = item.due_date
                if not existing.owner_email and item.owner_email:
                    existing.owner_email = item.owner_email
                break
        else:
            merged.append(item.model_copy())
    return merged


def resolve_owner(commitment: Commitment, users: list[dict]) -> dict | None:
    """Exact match only. Fuzzy identity resolution belongs to a human, not a prompt."""
    if commitment.owner_email:
        email = commitment.owner_email.strip().lower()
        for user in users:
            if user["email"].lower() == email:
                return user

    name = commitment.owner_name.strip().lower()
    if not name:
        return None
    for user in users:
        if user["name"].strip().lower() == name:
            return user
    for user in users:
        parts = user["name"].strip().lower().split()
        if any(part == name for part in parts if len(part) > 2):
            return user
    return None


def is_near_duplicate(title: str, owner_email: str | None, existing_tasks: list[dict]) -> dict | None:
    target = normalize_title(title)
    if not target:
        return None
    for task in existing_tasks:
        if (task.get("owner_email") or "").lower() != (owner_email or "").lower():
            continue
        if SequenceMatcher(None, normalize_title(task["title"]), target).ratio() >= 0.86:
            return task
    return None
