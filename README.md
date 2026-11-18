# gather

Meeting transcript → tracked follow-up tasks, with a human in the loop.

This is a working vertical slice of the architecture we discussed: connectors
normalize provider payloads into canonical **events**, LangGraph workflows make
the few decisions that need judgment, SQLite (shaped like Postgres) is the
system of record, and nothing leaves the building without approval.

```bash
uv sync
python -m gather.demo          # offline, deterministic, no accounts
python -m gather.demo --live   # real Groq / OpenRouter keys from .env
python -m gather.check         # probe each free model in your chain
python -m gather.seed          # org + demo members, so `serve` is usable
python -m gather.serve         # webhook + approval API on :8000
```

Verified against the keys in this workspace (Nov 2026):

| model | latency |
|---|---|
| `groq:openai/gpt-oss-120b` | ~1.2s |
| `openrouter:nvidia/nemotron-3.5-lightning:free` | ~3.5s |
| `openrouter:inclusionai/ling-3.2-flash:free` | ~2.4s |

## The spine

```
Fireflies ──► parse_webhook ──► dedupe(raw_events) ──► meeting graph
                                                        │
                          load ─► extract ─► resolve ─► fan-out enrich
                                                        │
                                              review [interrupt]
                                                        │ approve
                                              persist ─► notify
Gmail ─────► parse_webhook ──► inbox graph: load ─► match ─► classify ─► apply
cron ──────────────────────► scheduler: overdue ─► draft nudge ─► proposal
```

Every node before `review` is bookkeeping. Only `extract`, `classify` and
`draft_nudge` call a model, and each of them returns a validated schema.

## What the demo proves

| Claim | Where it is proven |
|---|---|
| Webhooks are idempotent | `raw_events` unique key; retry suppressed in demo step 1 |
| Long transcripts fit small contexts | `chunk_transcript` in `agents/extract.py` |
| Free models can be held to structure | JSON prompt + validate + one repair, `llm.py` |
| A run can pause for a human and resume | `interrupt()` + checkpointer, demo step 2 |
| Duplicate tasks are not created | fingerprint + near-duplicate check, demo step 2 |
| External attendees are not assigned internal tasks | `resolve_owner`, test asserts `external_owner` |
| Replies update state without a human | `inbox_monitor` graph, demo step 4 |
| Follow-ups are drafted, not sent | scheduler creates a `nudge` proposal |
| Tenancy is explicit | every `Store` method takes `org_id` |
| Providers are interchangeable | `CapabilityHub` + `Connector` protocol |

```
tests/test_spine.py ......... 7 passed
```

## Why free models are enough here

The design deliberately keeps the model on a short leash:

1. **Small prompts.** A chunk is ~9k chars, not a whole meeting. A summary is
   never re-sent.
2. **Structured output + validation.** If a free model wraps JSON in prose or
   code fences, `extract_json` recovers it; if the schema does not validate,
   one repair call runs, then the node degrades instead of crashing.
3. **A fallback chain.** Groq → Groq small → OpenRouter free. A 429 on one
   provider rolls to the next.
4. **Call budget per run.** `GATHER_MAX_CALLS` stops a runaway retry loop.
5. **Pacing.** `GATHER_MIN_INTERVAL` respects free-tier tokens-per-minute.
6. **Deterministic work stays deterministic.** Dedupe, ownership, due-date
   normalization and state transitions never touch the model.

Free-tier limits change often. Defaults are in `config.py`; override with
`GATHER_MODEL_CHAIN`. Check `https://openrouter.ai/models?max_price=0` for the
current free list and put the exact id in `.env`.

## Layout

```
src/gather/
  domain.py            canonical objects + deterministic helpers
  llm.py               provider chain, JSON repair, offline fixture model
  db.py                schema + repository (org-scoped)
  connectors/          base protocol, fireflies.py, gmail.py
  agents/              extract.py, classify.py, draft.py   ← only LLM call sites
  graphs/              meeting_to_tasks.py, inbox_monitor.py
  workers/scheduler.py overdue sweep + renewal report
  api.py               webhooks, approval queue, reads
  demo.py              the end-to-end walkthrough
  fixtures/            realistic payloads for offline runs
```

`connectors/` is dumb I/O. If a prompt ever appears there, the design has
leaked.

## API

```bash
# Fireflies-shaped delivery (fixture mode returns the bundled meeting)
curl -X POST localhost:8000/webhooks/fireflies/<org_id> \
     -H 'content-type: application/json' \
     -d '{"event":"meeting.transcribed","meetingId":"ff_9f3a2c"}'

# What is waiting for a human
curl localhost:8000/proposals

# Approve everything in a task proposal
curl -X POST localhost:8000/proposals/<id>/approve -d '{}'

# Reject (the graph resumes with nothing approved and exits cleanly)
curl -X POST localhost:8000/proposals/<id>/reject
```

`/health` reports which models are active and how many calls this process has
made.

## What is mocked, and what is real

**Real and exercised here:** the model chain (Groq + two OpenRouter free
models), JSON recovery and validation, chunking, graph orchestration,
interrupt/resume, task state machine, ownership resolution, scheduler, the HTTP
edge and approval queue.

**Fixture-backed:** Fireflies transcript bodies and Gmail message bodies/sends.
The connector HTTP paths are implemented but need your `FIREFLIES_API_KEY` and
a Gmail token to exercise.

**Deliberately not built yet:** Google OAuth token refresh, Pub/Sub
subscription renewal, Slack app install, Postgres/RLS, vector search, and
Drive/Calendar/Teams connectors.

## Production swap list

1. SQLite → Postgres, with RLS on `org_id` and the same repository methods.
2. `InMemorySaver` → `PostgresSaver` so approvals survive a restart.
3. Inline background tasks → a real queue (or LangGraph Platform).
4. Fixtures → live Fireflies GraphQL and Gmail `history.list`.
5. Add `sync()` gap repair + subscription renewal to the scheduler.

## Next connectors

Add one file in `connectors/`, declare its `capabilities`, register it in
`runtime.py`, and map its payloads to `Event`/`Message`. No graph or prompt
changes. Slack is the natural third: the same `message.received` route, plus
interactive "Done" buttons that write straight to the task.
