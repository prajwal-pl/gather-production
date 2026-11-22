# gather

Meeting transcript → tracked follow-up tasks, with a human in the loop.

This is a working vertical slice of the architecture we discussed: connectors
normalize provider payloads into canonical **events**, LangGraph workflows make
the few decisions that need judgment, SQLite (shaped like Postgres) is the
system of record, and nothing leaves the building without approval.

```bash
uv sync
uv run gather demo          # offline, deterministic, no accounts
uv run gather demo --live   # real Groq / OpenRouter keys from .env
uv run gather check         # probe each free model in your chain
uv run gather seed          # org + demo members, so `serve` is usable
uv run gather sync          # poll every due connection once
uv run gather serve         # webhook + approval API on :8000 (development)
uv run gather-engine        # desktop engine: stdio JSON lines (Tauri spawns this)
```

`--data-dir <path>` works before or after the subcommand. Without it, state
goes to the OS app-data directory (`~/.local/share/gather` on Linux,
`~/Library/Application Support/gather` on macOS, `%APPDATA%\gather` on
Windows). `GATHER_DB` remains a single-file development override.

```
<data-dir>/
  gather.db            # tasks, meetings, events, proposals
  checkpoints.db       # paused graph runs / pending approvals
  engine.lock          # advisory lock: one engine per workspace
  secrets.json         # only if no OS keychain is available (0600)
  logs/engine.log      # rotated, redacted
```

Verified against the keys in this workspace (Nov 2026):

| model | latency |
|---|---|
| `groq:openai/gpt-oss-120b` | ~1.2s |
| `openrouter:nvidia/nemotron-3.5-lightning:free` | ~3.5s |

## The spine

```
Fireflies ──► poll (transcripts) ──► dedupe(raw_events) ──► meeting graph
                                                              │
                            load ─► extract ─► resolve ─► fan-out enrich
                                                              │
                                                    review [interrupt]
                                                              │ approve
                                                    persist ─► notify
Gmail ─────► poll (historyId) ─────► inbox graph: load ─► match ─► classify ─► apply
engine clock ─► overdue ─► draft nudge ─► proposal      (one thread, no cron)

webhooks ──► development/relay only (GATHER_INGRESS=http)
```

Every node before `review` is bookkeeping. Only `extract`, `classify` and
`draft_nudge` call a model, and each of them returns a validated schema.

## What the demo proves

| Claim | Where it is proven |
|---|---|
| Webhooks are idempotent | `raw_events` unique key; retry suppressed in demo step 1 |
| Polling is safe to retry | cursor advances only after dispatch; a failure backs off and replays |
| A stale Gmail cursor repairs itself | 404 → `reset` → bounded backfill |
| Long transcripts fit small contexts | `chunk_transcript` in `agents/extract.py` |
| Free models can be held to structure | JSON prompt + validate + one repair, `llm.py` |
| A run can pause for a human and resume | `interrupt()` + `checkpoints.db`, demo step 2 |
| An approval survives a restart | `tests/test_durability.py::test_pending_approval_survives_a_restart` |
| Schema upgrades are forward-only | `PRAGMA user_version` runner, `test_migrations_apply_in_order_and_only_once` |
| Duplicate tasks are not created | fingerprint + near-duplicate check, demo step 2 |
| External attendees are not assigned internal tasks | `resolve_owner`, test asserts `external_owner` |
| Replies update state without a human | `inbox_monitor` graph, demo step 4 |
| Follow-ups are drafted, not sent | scheduler creates a `nudge` proposal |
| Tenancy is explicit | every `Store` method takes `org_id` |
| Providers are interchangeable | `CapabilityHub` + `Connector` protocol |

```
tests/  61 passed
```

## Why free models are enough here

The design deliberately keeps the model on a short leash:

1. **Small prompts.** A chunk is ~9k chars, not a whole meeting. A summary is
   never re-sent.
2. **Structured output + validation.** If a free model wraps JSON in prose or
   code fences, `extract_json` recovers it; if the schema does not validate,
   one repair call runs, then the node degrades instead of crashing.
3. **A fallback chain.** Groq → OpenRouter free. A 429 on one provider rolls
   to the next.
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
  db.py                schema, migrations + repository (org-scoped)
  checkpoints.py       durable LangGraph checkpoints (SQLite)
  cli.py               `gather` entry point
  connectors/          base protocol, fireflies.py, gmail.py
  poller/              poll.py (schedule/sync/backoff), dispatch.py (event → graph)
  agents/              extract.py, classify.py, draft.py   ← only LLM call sites
  graphs/              meeting_to_tasks.py, inbox_monitor.py
  workers/scheduler.py overdue sweep + renewal report
  engine.py            desktop entry point (stdio, lock, logs, signals)
  ipc/                 protocol.py, router.py, stdio.py, handlers.py
  scheduler.py         the engine clock: poll / sweep / announce / cancel
  secrets.py           OS keychain, 0600 file fallback, stored settings
  events.py, runs.py   event bus + cooperative run registry
  lock.py              one-engine-per-workspace advisory lock
  api.py               webhooks, approval queue, reads
  demo.py              the end-to-end walkthrough
  fixtures/            realistic payloads for offline runs
```

`connectors/` is dumb I/O. If a prompt ever appears there, the design has
leaked.

## API

```bash
# Ask the poller to run now (development convenience)
curl -X POST localhost:8000/sync

# Fireflies-shaped delivery (development/relay only, GATHER_INGRESS=http)
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

## The engine (desktop)

The Tauri shell will spawn one engine and talk to it over stdio; `api.py` stays
as a headless dev adapter. stdout is protocol only, logs go to stderr and
`<data-dir>/logs/engine.log` (rotated and redacted). One advisory lock next to
the DB makes a second engine refuse to start.

```bash
uv run gather-engine --data-dir ~/.local/share/gather --secrets auto
# auto: OS keychain if available, else a 0600 secrets.json with a warning
# keychain: require the OS keychain; file: force the 0600 file; env: dev only

# request
{"id":"r1","method":"tasks.list","params":{"status":"open"}}
# response
{"id":"r1","ok":true,"result":{"tasks":[...]}}
# event
{"event":"run.progress","data":{"run_id":"run_ab12","stage":"extract","chunk":2,"total":5}}
```

Methods: `health`, `engine.shutdown`, `settings.get/update`,
`connections.list/add/remove/test`, `sync.now`, `meetings.list/get/ingest`,
`runs.list/get/cancel`, `tasks.list/get/update`,
`proposals.list/get/approve/reject`, `models.check/usage`, `scheduler.status`.

### How the Tauri shell launches it (`apps/desktop`)

In development the shell resolves the interpreter itself: it prefers
`<repo>/.venv/bin/python` (the only one with the dependencies), sets
`PYTHONPATH=<repo>/src`, runs from the repository root and passes `--secrets
file`. A release build prefers a `gather-engine` sidecar next to the executable
(Phase 5) and only then falls back to an interpreter.

| Variable | Effect |
|---|---|
| `GATHER_PYTHON` | interpreter to use instead of the project venv |
| `GATHER_ENGINE` | a `gather-engine` binary; bypasses interpreter resolution |
| `GATHER_DATA_DIR` | passed through as `--data-dir` |
| `GATHER_FIXTURE=1` | adds `--fixture` (offline connectors + model) |
| `GATHER_SCHEDULER=1` | enables the background poller (off in dev by default) |
| `GATHER_SECRETS` | `auto` (default in release) / `file` (default in dev) / `env` |
| `GATHER_DEBUG_IPC=1` | log every request/response line to the shell's stderr |

If the engine stops, the shell keeps the last 40 stderr lines and exposes them
through `engine_diagnostics`; the UI polls `health` every 5 s and shows the
reason next to the status pill. The supervisor restarts a crashed engine up to
ten times and resets the counter on `engine.ready`.

The scheduler is one non-overlapping tick: poll due connections, run the overdue
sweep when due, announce new proposals, then wait. It catches up immediately
after start or sleep. A tick is a cancellable run; cancellation stops at the
next event/chunk boundary and leaves the cursor untouched.

Settings (model chain, pacing, auto-approve) can be written through
`settings.update`; they are stored beside the secrets and overlaid at startup.
Secret values are never returned over IPC, only whether each is present.
`--fixture` and `--no-scheduler` exist for tests.

## What is mocked, and what is real

**Real and exercised here:** the model chain (Groq + one OpenRouter free
model), JSON recovery and validation, chunking, graph orchestration,
interrupt/resume, task state machine, ownership resolution, scheduler, the HTTP
edge and approval queue. Live ingestion is verified end to end: Fireflies
transcript listing and Gmail OAuth refresh → history/backfill → full message
fetch (636 messages in a 14-day backfill).

**Fixture-backed:** Gmail *sending* and the offline demo. The connector send
path is implemented but has not been exercised against a real mailbox.

**Polling, not webhooks:** a desktop app has no public URL, so ingestion is
pull-based. Fireflies is listed by date window (14-day first backfill, 24-hour
overlap, dedupe on `meeting_id + date`). Gmail walks `users.history.list` from
its `historyId`; a 404 triggers a bounded backfill. Connections carry their own
schedule (`next_sync_at`), retry with capped exponential backoff, and stop on a
dead credential (`reauth_required`) instead of hammering it. Webhook routes
still exist for development or a relay, gated by `GATHER_INGRESS=http`.

**Deliberately not built yet:** Pub/Sub subscription renewal, Slack app install,
Postgres/RLS, vector search, and Drive/Calendar/Teams connectors.

## Production swap list

1. `apps/desktop/` Tauri shell: window, tray, single instance, supervision and
   the React approval inbox (Phase 4).
2. PyInstaller sidecar builds, per-OS CI, signing/notarization, updater
   (Phase 5).
3. Ollama, so the app still works offline.
4. Slack (Socket Mode), Calendar, Drive, Teams (Phase 6).

Done already: durable `SqliteSaver` checkpoints, a versioned schema, real
Fireflies/Gmail polling with cursor repair and backoff, Google OAuth refresh
tokens, an ingress switch that keeps webhooks out of the desktop path, the
stdio engine with cooperative cancellation, a keychain-backed secret store, and
the one-engine-per-workspace lock.

## Next connectors

Add one file in `connectors/`, declare its `capabilities`, register it in
`runtime.py`, and map its payloads to `Event`/`Message`. No graph or prompt
changes. Slack is the natural third: the same `message.received` route, plus
interactive "Done" buttons that write straight to the task.
