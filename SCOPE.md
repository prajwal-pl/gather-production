# gather — Project Scope & Architecture

**Status:** draft v0.4 — Phases 1–2 implemented; product decisions resolved · **Date:** 2026-11-18 · **Shape:** local-first desktop app
**Engine:** bundled Python runtime (LangGraph) · **Shell:** Tauri v2

This is the reference document for what gather is, what it is not, and how the
pieces fit. Read this before changing architecture. The README explains how to
run the current prototype; this file explains where the prototype is going and
why.

---

## 0. TL;DR

- gather is a **single-user desktop assistant** that turns meeting transcripts
  into tracked follow-up tasks, watches the user's inbox for replies, and
  drafts nudges.
- It ships as a **Tauri app with a bundled Python sidecar**. All state is local
  SQLite. Credentials live in the OS keychain. There is no gather server.
- The current prototype was built as a small web service. The domain layer,
  graphs, prompts and schema survive. **The ingress layer does not**: a desktop
  app cannot receive provider webhooks, so ingestion becomes cursor-based
  polling (plus Slack Socket Mode, later).
- The other three server assumptions to remove: `InMemorySaver` checkpoints
  (must survive restart), `.env` secrets (must be keychain), and a repo-relative
  database path (must be an OS app-data directory with migrations).
- "Multi-tenant" becomes "one local workspace". `org_id` stays as the
  workspace key; row-level security and tenant routing go away.

---

## 1. What gather is

A local assistant with three loops:

| Loop | Trigger | Output |
|---|---|---|
| **Capture** | a meeting transcript appears | candidate follow-up tasks, each with owner, due date and a verbatim quote |
| **Act** | a human approves | tasks in the local tracker, assigned and notified |
| **Watch** | new mail / replies / time passing | task state updates, nudge drafts, escalations |

The product promise is narrow and worth protecting: **every task must be
traceable to a sentence someone actually said, and nothing is sent to another
person without a human seeing it first.**

---

## 2. Scope

### 2.1 In scope for v1

1. Connect **Fireflies** (or a pasted transcript) and **Gmail**.
2. Ingest a completed meeting, chunk it, extract commitments with a free hosted
   model, and present them for approval.
3. Persist approved tasks with source quote, speaker, meeting and timestamp.
4. Match inbound replies to tasks and apply state changes
   (`done`, `blocked`, `in_progress`, `date_change`).
5. Draft follow-up nudges for overdue tasks; approve before sending.
6. Run offline with bundled fixture data. Local models (Ollama) are deferred to
   v2; v1 reasoning uses free hosted models.
7. Survive app restart with pending approvals intact.
8. Export and delete all local data.

### 2.2 Explicit non-goals for v1

| Not doing | Why |
|---|---|
| Hosted multi-tenant service | The product is local-first; a server changes the trust story and the cost model. |
| Real-time webhooks | Requires a public endpoint. Deferred to an optional relay (see §5). |
| Slack / Teams ingestion | Slack needs Socket Mode + app distribution decisions; Teams needs tenant admin consent. v2. |
| Autonomous sending | Approval gate stays until there is eval data to justify auto-send. |
| Mobile / web clients | Desktop only. |
| Team sharing / sync | Single workspace. Schema leaves room, product does not promise it. |
| Fine-tuning / training on user data | Never without explicit opt-in; not planned. |

### 2.3 Assumptions to validate

- The user has (or will create) their own free Groq / OpenRouter key. A fully
  local model is not required for v1.
- The user is willing to authorize Gmail read access for their own mailbox.
- The app may be closed for hours; "background" means tray-resident, not
  always-on server.

---

## 3. Users and jobs

**Primary user:** an individual operator (founder, PM, consultant, lead) who
lives in meetings and email, and loses follow-ups between them.

| Job | Current pain | gather's answer |
|---|---|---|
| "What did I promise?" | re-read transcripts, memory | extracted commitments with quotes |
| "Who owes what?" | scattered notes | one local task list by owner |
| "Did they reply?" | inbox archaeology | reply classification wired to tasks |
| "Should I chase this?" | guilt and guesswork | overdue detection + drafted nudge |
| "Can I trust it?" | hallucinated task lists | quote + speaker + approval gate |

Non-user: an IT admin, a team lead managing others, anyone expecting a shared
workspace. Those are future products, not this one.

---

## 4. Delivery shape: Tauri + Python engine

### 4.1 Why a sidecar

The reasoning stack is Python (LangGraph, pydantic, provider SDKs). Rewriting it
in Rust would discard the part of the system that is actually hard. Tauri
provides the native shell, window, tray, updater and signing; Python provides
the engine.

```
┌───────────────────────────────────────────────┐
│ Tauri app (Rust + webview UI)                 │
│  · windows, tray, single-instance, deep links │
│  · spawns + supervises the engine             │
│  · renders state, sends commands              │
└───────────────┬───────────────────────────────┘
                │ stdio, one JSON object per line
┌───────────────▼───────────────────────────────┐
│ gather engine (bundled Python 3.14)           │
│  · RPC router over Runtime                    │
│  · scheduler (poll / sweep / token refresh)   │
│  · LangGraph runs, connector syncs            │
│  · SQLite + migrations + checkpoints          │
└───────────────────────────────────────────────┘
```

Rules:

- **Rust holds no business logic.** It supervises, transports and renders.
- **Python holds no UI concerns.** It emits state and events.
- The engine is a first-class bundled component, not an optional service. The
  binary ships inside the app bundle and updates with it.

### 4.2 Process model

One engine process per app instance.

| Concern | Decision |
|---|---|
| Spawn | Tauri `shell` plugin, `sidecar("gather-engine")` |
| Args | `--data-dir <app_data_dir> --ipc stdio --secrets keychain` |
| Restart | supervised, exponential backoff, max 5 attempts, then surface an error state |
| Shutdown | Tauri sends `engine.shutdown`, waits 3s, then kills |
| Single instance | Tauri single-instance plugin **and** an advisory lock file next to the DB |
| Dev mode | spawn `uv run python -m gather.engine` from the venv instead of the frozen binary |
| Logs | engine → stderr, rotated file under `<data-dir>/logs/`, redacted |

Why one process: SQLite is a single-writer store, the scheduler is stateful, and
two engines would double-poll providers and duplicate approvals.

### 4.3 IPC contract

**Transport: stdio JSON-lines.** One UTF-8 JSON object per line, flushed.

Chosen over a loopback HTTP server because it has no port, no auth token to
leak, no firewall prompt, and no other local process can reach it. It also
survives on locked-down corporate machines where binding sockets is restricted.

```jsonc
// request  (UI → engine)
{"id":"r1","method":"tasks.list","params":{"status":"open"}}

// response (engine → UI)
{"id":"r1","ok":true,"result":{"tasks":[...]}}
{"id":"r1","ok":false,"error":{"code":"not_found","message":"..."}}

// event    (engine → UI, unsolicited)
{"event":"run.progress","data":{"run_id":"...","stage":"extract","chunk":3,"total":8}}
{"event":"proposal.created","data":{"proposal_id":"..."}}
{"event":"sync.status","data":{"provider":"gmail","state":"ok","at":"..."}}
{"event":"engine.ready","data":{"version":"0.1.0","workspace_id":"..."}}
```

Method surface (thin adapter over `Runtime`; no new domain logic):

| Group | Methods |
|---|---|
| system | `health`, `engine.shutdown`, `settings.get`, `settings.update` |
| connections | `connections.list`, `connections.add`, `connections.remove`, `connections.test`, `sync.now` |
| capture | `meetings.list`, `meetings.get`, `meetings.ingest`, `runs.list`, `runs.get`, `runs.cancel` |
| tasks | `tasks.list`, `tasks.get`, `tasks.update` |
| approvals | `proposals.list`, `proposals.get`, `proposals.approve`, `proposals.reject` |
| models | `models.check`, `models.usage` |
| scheduler | `scheduler.status` |
| assistant | `agent.chat`, `agent.tools` |

Error codes: `bad_request`, `method_not_found`, `invalid_params`, `not_found`,
`conflict`, `cancelled`, `unauthorized`, `busy`, `internal_error`.

Implementation notes:

- Reader thread parses stdin; handlers run on **two** bounded daemon pools:
  four fast workers for reads/updates and two slow workers for graph runs,
  provider tests, model probes and sync. A long extraction cannot block
  `tasks.list`.
- A full queue answers `busy` instead of growing without bound.
- All writes to stdout go through one lock; stdout is **only** protocol. Logs go
  to stderr and a rotated `<data-dir>/logs/engine.log`, with known secret values
  redacted.
- Requests are idempotent where possible. `proposals.approve` and
  `proposals.reject` take a decision lock and return `{already: true}` on a
  second call. If the process died after resuming the graph but before recording
  the decision, approval detects the completed checkpoint and records it without
  re-running.
- Cancellation is cooperative. `runs.cancel` marks the run; the graph raises at
  the next chunk/candidate/event boundary. A cancelled poll leaves the cursor
  untouched, so the next tick retries with dedupe.
- Every event carries enough state that the UI can be rebuilt from
  `*.list` calls alone (events are optimizations, not truth).

### 4.4 Lifecycle states the UI needs

`starting → ready → syncing → running → needs_attention → stopping → stopped`

`needs_attention` covers: no model configured, token expired, keychain locked,
all providers failing. The app must be usable (browse tasks) in that state.

`health` reports `state` plus the facts behind it (`models`, `fixture_mode`,
`scheduler.running`, `scheduler.last_error`, `secrets_backend`, `ingress`), so
the shell renders a state rather than guessing from scattered booleans. The
scheduler's `scheduler.error` event is the push equivalent; `health` remains the
source of truth after a reconnect.

---

## 5. The constraint that reshapes the backend: no inbound webhooks

This is the most important architectural consequence of "desktop app".

A provider webhook needs a stable public HTTPS URL. A laptop does not have one.
Some form of tunnel per user is not shippable: it needs an account, a public
hostname, a daemon, and a security review.

**Therefore: desktop ingestion is pull-based, not push-based.**

### 5.1 Provider-by-provider

| Provider | Server-world design | Desktop design | Latency |
|---|---|---|---|
| **Fireflies** | thin webhook → fetch transcript | poll `transcripts(from, to)` every 5–15 min; dedupe on meeting id | minutes (transcripts are generated minutes after a call anyway) |
| **Gmail** | `users.watch` → Pub/Sub → webhook | poll `users.history.list(startHistoryId)`; store `historyId` from `getProfile`; 404 → full resync of last N days; back off on 429 | 1–3 min |
| **Google Calendar** | push channel (public HTTPS) | `events.list(syncToken=…)`; `410` → full resync | 2–5 min |
| **Google Drive** | push channel | `changes.getStartPageToken` → `changes.list(pageToken)` | 5 min |
| **Slack** | Events API webhook | **Socket Mode** (WebSocket, app-level token) — no public URL needed | real-time |
| **Teams** | Graph change notifications | Graph delta queries polled; still needs tenant admin consent | v2 |
| **Manual** | — | paste transcript / upload `.txt` / drop a file | instant |

Slack is the exception that proves the rule: Socket Mode exists precisely for
clients that cannot host a public endpoint. It is the right v2 connector, but it
drags in app distribution questions (§8.3), so it is not v1.

### 5.2 The seam: `EventSource`

Keep push support, but make it one implementation of a source, not the only one.

```
             ┌──────────────┐
  poll ─────▶│              │
             │  EventSource │────▶ canonical Event ──▶ raw_events (dedupe)
  push ─────▶│              │
  manual ───▶│              │
             └──────────────┘
```

- `PollSource`: reads `connections.next_sync_at`, calls `connector.sync`, emits
  events, advances `cursor`.
- `PushSource`: wraps today's `parse_webhook`; only used by the optional relay
  or a self-hosted deployment.
- `ManualSource`: file/paste ingestion; also the fallback when OAuth is
  unavailable.

The existing `Connector` protocol already has both `parse_webhook` and
`sync(conn, limit) -> SyncBatch`. That was accidental foresight; formalize it:

- `sync` becomes the **primary** path.
- `parse_webhook` becomes optional; add `push_capable: bool`.
- Add `backfill(conn, since)` for first connect and gap repair.

Implemented in Phase 2 (`poller/`): `PollSource` reads `due_connections`, calls
`sync`, dispatches each event, and advances the cursor *after* the handler
succeeds. A failed dispatch leaves the cursor in place, so the next tick replays
the window (already-handled events hit the dedupe key). Failures get capped
exponential backoff, `Retry-After` wins when present, and a dead credential
moves the connection to `reauth_required`. A `reset` batch (Gmail 404, or no
cursor yet) triggers `backfill` bounded to 14 days. `push_capable` and
`poll_interval_s` are on the connector; `GATHER_INGRESS=http` is the only mode
that registers webhook routes.

### 5.3 Dedupe still works

Polling re-reads a window, so the same object is seen repeatedly. `raw_events`
already has a unique `(provider, provider_event_id)` key. Keep it, and define
provider event ids as:

```
fireflies:<meeting_id>:<date_ms>      # no revision field; date is the proxy
gmail:<message_id>
calendar:<event_id>:<etag>
drive:<file_id>:<modified_time>
slack:<event_id>
```

If a provider later exposes a real revision or modified marker, use it instead;
the date fallback means a re-transcribed meeting keeps the same key.

Synthetic ids are fine: they only need to be stable and unique per real change.

### 5.4 What happens to `api.py`

- Keep it as a **dev/headless mode** (`gather serve`). It is the fastest way to
  test graphs and to demo without the Tauri shell.
- Do not ship it as the production transport.
- Rename the concept in code from "webhook edge" to "ingress"; `api.py` becomes
  one ingress adapter, `ipc/stdio.py` the other.
- The `require_org(org)` check in the routes disappears in stdio mode, because
  the workspace comes from settings, not from a URL segment. Keep it in HTTP
  mode.

### 5.5 The optional relay (future, not v1)

If real-time becomes a requirement, add a tiny hosted "relay" whose only job is
to hold provider webhooks and forward them to connected desktops over a
WebSocket, authenticated by a per-install key. It stores nothing. This preserves
the push path without turning gather into a multi-tenant SaaS. Do not build it
until polling latency is measured to be a real problem.

---

## 6. Current code audit

What exists today, honestly assessed.

### 6.1 Keep (the valuable core)

| Module | Why it survives |
|---|---|
| `domain.py` | canonical objects + deterministic date/dupe helpers; no server assumptions |
| `llm.py` | provider chain, JSON recovery/repair, fixture model, budget guard |
| `agents/` | narrow prompts, structured output; only place a model is called |
| `graphs/meeting_to_tasks.py` | extraction → resolve → fan-out → interrupt → persist |
| `graphs/inbox_monitor.py` | match → classify → apply, deterministic-first |
| `db.py` (schema + repository) | org-scoped access already; SQLite is the desktop DB anyway |
| `connectors/base.py` | capability/hub design is provider-agnostic |
| `workers/scheduler.py` | becomes the in-process tick body |
| `fixtures/` | offline demo + CI; also the app's "sample workspace" |
| `tests/` | 39 passing tests; keep and extend |

### 6.2 Change (server-shaped)

| Now | Must become | Why |
|---|---|---|
| `InMemorySaver` in both graphs | `SqliteSaver` in `<data-dir>/checkpoints.db` | pending approvals must survive quit/restart/update |
| webhook routes as sole ingress | `PollSource` + optional `PushSource`; stdio IPC | no public URL on a laptop |
| `GATHER_DB` / repo-relative `.gather/` | `--data-dir` passed by Tauri, OS app-data path | app bundles are read-only; data belongs in user dirs |
| `.env` for keys/secrets | OS keychain via `keyring` | never ship secrets in a bundle; `.env` is dev-only |
| `executescript(SCHEMA)` | `PRAGMA user_version` migration runner | users update the app and keep their DB |
| `FastAPI` as the app surface | dev-only headless mode | production transport is stdio |
| `org_id` validation from URL | workspace id from settings | no URL segment, one workspace |
| single `Store` connection shared blindly | explicit writer discipline + `busy_timeout` | scheduler + RPC + graph threads all touch the DB |
| `notify.py` posting to a Slack webhook | OS notifications + optional Slack | a desktop app should use the OS; webhook is a setting |

### 6.3 Drop / defer

- `langchain` meta-package dependency — not imported anywhere; it only inflates
  the bundle. Keep `langchain-core`, `langgraph`, provider packages.
- `uvicorn`/`fastapi` from runtime deps → optional `dev` group (keep for
  `gather serve`).
- `python-dotenv` in production → dev only.
- Multi-tenant/RLS planning — SQLite has none; single workspace.
- `openrouter/free` auto-router — unreliable in testing; use named free models.
- Teams, Drive, Calendar: later.

### 6.4 Known fragile spots to fix during the move

1. **Date arithmetic** is already handled deterministically (`due_from_text`).
   Keep extending that pattern; do not let the model compute dates.
2. **Provider timeouts**: `langchain-openrouter` takes **milliseconds**;
   `langchain-groq` takes seconds. This bit us once; encode it in tests.
3. **Reasoning models** (`openai/gpt-oss-*`) can return empty `content` if the
   token cap is too low. Keep `reasoning_effort="low"` and a healthy cap.
4. **Graph resume identity**: proposals store `thread_id`, which is what makes
   restart-resume possible. Preserve that invariant.
5. **`extract` degrades silently** to zero tasks on total model failure. On
   desktop this must become a visible `failed` state with a retry action, or a
   meeting will vanish without explanation.

---

## 7. Data and durability

### 7.1 Location

```
<app_data_dir>/
  gather.db            # tasks, events, meetings, connections, proposals
  checkpoints.db       # LangGraph checkpoints (approvals in flight)
  logs/gather.log      # redacted
  exports/             # optional data export
  lock                 # single-instance advisory lock
```

- macOS: `~/Library/Application Support/<bundle-id>/`
- Windows: `%APPDATA%\<bundle-id>\`
- Linux: `$XDG_DATA_HOME/<bundle-id>/`

Tauri resolves this and passes it in. Python must not guess.

### 7.2 Migrations

```
PRAGMA user_version = 0  →  apply m1, m2, ... in order, in a transaction
```

Rules:
- Forward-only. Never rewrite a shipped migration.
- Every migration is tested against a DB created by the previous version.
- `checkpoints.db` is coupled to the engine version; if a graph schema changes
  incompatibly, drain or discard stale checkpoints explicitly (and tell the
  user), rather than crashing on resume.

### 7.3 Retention

Polling makes `raw_events` grow monotonically. Keep the **ids** (they are the
dedupe key) but prune payloads after ~90 days. Add a retention job to the hourly
sweep. Meetings and tasks are user data: keep until deleted.

### 7.4 Concurrency

- SQLite WAL is on; set `busy_timeout=5000`.
- One writer at a time: the repository lock already serializes writes. Keep it,
  and add a test that runs a graph and a scheduler tick concurrently.
- At most **one** meeting-extraction run at a time (free-tier rate limits), and
  at most **two** connector syncs. Queue the rest.

### 7.5 Workspace semantics

`org_id` is retained but means **workspace id**: one stable UUID per install,
stored in settings. It exists so the schema does not need a rename and so a
future "shared workspace" feature has a key. It does **not** imply tenancy,
isolation guarantees, or user accounts. `users` means "people I work with"
(owners/assignees), not auth principals.

---

## 8. Identity, secrets, OAuth

### 8.1 Secrets

| Secret | Storage |
|---|---|
| Groq / OpenRouter keys | OS keychain |
| Ollama endpoint | settings (not secret) |
| Google refresh token | OS keychain |
| Slack app/bot tokens | OS keychain |
| Provider webhook signing secrets (if relay) | OS keychain |

Key naming: `gather:<workspace_id>:<provider>:<field>`.

Use Python `keyring` (Keychain / Credential Manager / Secret Service). If the
keychain is unavailable, fall back to a `0600` file **and show a warning**; do
not silently degrade. `.env` remains a dev convenience only.

**Never** log tokens, transcript bodies or email bodies. Add a redaction filter
at the logging boundary; test it.

### 8.2 Google (Gmail, Calendar, Drive)

- OAuth client type: **Desktop app** (public client, no secret).
- Flow: authorization code + **PKCE**, redirect to `http://127.0.0.1:<ephemeral>/callback`.
- Scopes, requested incrementally:
  - Gmail: `gmail.readonly` (+ `gmail.send` only when the user enables send).
  - Calendar: `calendar.readonly`.
  - Drive: `drive.readonly` (only if file context is enabled).
- Store the refresh token in the keychain; refresh proactively ~5 min before
  expiry; on `invalid_grant`, mark the connection `reauth_required` and surface
  it in the UI.
- **Distribution risk:** `gmail.readonly` is a *restricted* scope. Public
  distribution needs Google verification plus an annual CASA security
  assessment — weeks of lead time. For a personal build, an OAuth consent screen
  in "Testing" mode with the user as a test user is sufficient. Resolved in
  §16: personal build first, public release deferred.

### 8.3 Slack (v2)

- Two options, both awkward on desktop:
  1. **Bring-your-own Slack app** + Socket Mode. User creates an app, enables
     Socket Mode, pastes an app-level token. No public URL. Honest but fiddly.
  2. Loopback OAuth. Slack's redirect URL rules are hostile to arbitrary local
     ports; custom schemes are not reliably supported.
- Recommendation: v2, ship option 1 with a guided setup screen, and treat public
  distribution as a separate project.

### 8.4 Microsoft / Teams (later)

Azure AD app registration, tenant admin consent, Graph delta polling. Not v1.

---

## 9. Scheduling

There is no server cron. The engine has an in-process scheduler.

| Tick | Cadence | Work |
|---|---|---|
| `poll` | 30–60 s check; per-connection due time with jitter | connector syncs |
| `sweep` | hourly | overdue tasks → nudge proposals; token refresh; retention; renewal warnings |
| `backfill` | on connect | last 14–30 days, bounded |
| `catchup` | on wake / launch | run anything overdue immediately |

Rules:
- **Wall-clock jump detection:** if `now - last_tick > 2 × interval`, assume the
  machine slept and run catch-up once, then resume. Do not replay every missed
  tick.
- **Jitter** every poll so reconnects do not synchronize.
- **Backoff** per connection: 1×, 2×, 4×, capped at 30 min, reset on success;
  surface `last_error` in the UI.
- **No work while the app is closed.** State this plainly in the product:
  "gather works while it is running." The mitigation is a tray-resident app with
  launch-at-login, not a background daemon.
- If true background operation is ever required, that is a separate decision
  (launchd / Task Scheduler / systemd user unit, or the relay).

Cancellation:
- `runs.cancel` sets a flag; extraction checks it between chunks; connectors
  check it between pages. LangGraph nodes are not preemptible mid-call, so
  cancel is cooperative and bounded by one model call.

---

## 10. Models

### 10.1 Providers

| Provider | Role | Notes |
|---|---|---|
| **Ollama** (local) | deferred to v2 | strong offline/private story, but v1 is hosted-only; revisit after extraction quality is stable |
| Groq | fast hosted default | `openai/gpt-oss-120b` with `reasoning_effort="low"` |
| OpenRouter | fallback | named free models, not the auto-router |

**Ollama is deliberately out of v1.** Shipping one model path keeps the first
release smaller; the fixture model still exercises the offline demo and tests.
Add `ollama` as a first-class spec provider (`http://127.0.0.1:11434`,
OpenAI-compatible) in v2, before any release that promises offline use. When it
lands, it becomes the default whenever it is reachable.

### 10.2 Workload split

| Task | Need | Suggested |
|---|---|---|
| Commitment extraction | quality, long input | best available model, chunked |
| Reply classification | speed, cheap | small model |
| Nudge drafting | tone, cheap | small model |

Allow a separate chain per role later; start with one chain and a documented
rationale. Keep the per-run call budget and pacing.

### 10.3 Failure behavior

- Total model failure must **not** discard the meeting. Persist the transcript,
  mark the run `needs_model`, and offer retry.
- Show which provider answered during a run (`models.usage`), so a user can tell
  when they are on a slow fallback.
- Streaming is unnecessary; chunk-level progress is enough.

### 10.4 Cost

Free tiers are the target, so: chunk (~9k chars), cap chunks, cap calls per run,
pace calls, fall back on 429, and never re-summarize the whole transcript for a
downstream step. The existing `cost control` in `llm.py` is the template.

---

## 11. Privacy and data handling

- **Local by default.** Transcripts, emails and tasks never leave the machine
  except as prompt text to the model provider the user chose.
- **Per-connector pause** and a global **offline mode** (fixture model; local
  inference arrives in v2).
- **No telemetry** by default. If added, opt-in only, and never content.
- **Redaction before send** (emails, phone numbers) is a settings option, not a
  default, because it can damage extraction quality.
- **Deletion** must be real: delete DB rows, checkpoints, logs, keychain
  entries. "Delete workspace" is a first-class action.
- Logs are redacted and local; the UI exposes "open log folder", not "upload".

---

## 12. UI surfaces (v1)

| Surface | Purpose | Must show |
|---|---|---|
| Onboarding | connect + configure | provider status, scope explanation, model choice, "try with sample data" |
| **Approval inbox** | the centerpiece | meeting, task, owner, due, **verbatim quote + speaker + timestamp**, related email, approve/edit/reject, bulk approve |
| Tasks | the tracker | group by owner / status / due; event timeline; mark done; snooze |
| Agent | ask + fetch + setup | read-only tool use, which tool ran, offline/degraded state, next step |
| Meeting detail | trust and audit | transcript, extracted tasks, quotes, re-run extraction |
| Connections | health | last sync, next sync, error, reauth, pause |
| Settings | control | models/keys, cadence, privacy, export, delete, diagnostics |
| Tray | ambient | pending count, sync now, pause, quit |
| Status bar | honesty | "synced 4m ago", model in use, last error |

Trust rules for the UI:
- Never show a task without its quote available in one click.
- Never show a "sent" state that did not actually happen.
- Distinguish `draft`, `approved`, `sent`, `failed` explicitly.

---

## 13. Packaging, size and performance budgets

| Budget | Target | Notes |
|---|---|---|
| Engine ready | < 1.5 s | onedir, lazy provider imports |
| First window paint | < 1.5 s | show cached state immediately |
| Idle CPU | ~0 | event-driven sleeps |
| Engine RSS | 150–250 MB | Python + pydantic + langgraph |
| Bundle | 60–120 MB | trim `langchain`, `fastapi`, `uvicorn` from runtime |
| Extraction | 1–2 s per chunk hosted | show progress, allow cancel; local models are v2 |

Packaging decisions:
- **PyInstaller `onedir`**, not `onefile`: faster start, fewer AV false
  positives, easier signing. Nuitka is the fallback if startup or size misses.
- Sidecar naming follows the target triple
  (`gather-engine-<triple>`), configured under `bundle.externalBin`.
- Sign **nested binaries**: the sidecar must be signed and notarized as part of
  the app bundle on macOS.
- CI matrix: macOS (arm64 + x64 or universal), Windows x64, Linux x64.
- **Python 3.14 packaging risk:** verify PyInstaller support before committing
  to 3.14 for release builds; pinning the *build* interpreter to 3.12/3.13 is
  acceptable if the runtime behaviour is identical.
- Auto-update via the Tauri updater; migrations must be forward-only and safe.

---

## 14. Risks

| Risk | Impact | Mitigation |
|---|---|---|
| No public webhooks | ingestion latency; some providers hard to support | polling + Socket Mode; manual import fallback; optional relay later |
| Google restricted scopes | public distribution blocked for weeks | personal/testing mode first; decide distribution before promising a release |
| Python 3.14 → PyInstaller | cannot ship | verify early; pin build interpreter if needed |
| Bundle size / startup | poor first-run experience | trim deps, onedir, lazy imports, measure in CI |
| Free-tier rate limits | extraction stalls mid-meeting | chunk caps, pacing, fallback chain, visible progress, retry |
| No local inference in v1 | the app needs a key and a network to do real work | fixtures for demos; Ollama is the first v2 feature |
| Model quality on small free models | missed or invented tasks | strict prompt, quote requirement, confidence, human gate, eval set |
| App closed = no work | missed nudges | tray + launch at login; state the limitation |
| SQLite checkpointer + threads | resume bugs | single writer discipline, integration test for restart-resume |
| OAuth token expiry | silent data gaps | proactive refresh, `reauth_required` state surfaced in UI |
| Keychain unavailable (Linux) | cannot store secrets | explicit fallback + warning, never silent |

---

## 15. Migration plan (ordered, smallest safe steps)

**Phase 0 — decide and document.** This file. ✔

**Phase 1 — durability and portability** (no product change) ✔
1. ✔ `Settings.data_dir` resolved from `--data-dir` / `GATHER_DATA_DIR`, with OS
   defaults; `GATHER_DB` is a dev override; `checkpoints.db` sits beside the DB.
2. ✔ `apply_migrations()` in `db.py` (`PRAGMA user_version`, one transaction per
   step, refuses a newer schema); legacy unversioned DBs are adopted in place.
3. ✔ `Checkpoints` wraps `SqliteSaver`; both graphs require a checkpointer;
   a restart-resume test reopens the DB and completes the approval.
4. ✔ `langchain` removed; `fastapi`/`uvicorn`/`python-dotenv` are dev-only and
   `config.py` tolerates a missing dotenv in the bundled engine.

**Phase 2 — event sources** ✔
5. ✔ `poller/` with `PollSource` (`poll.py`), `process_event` (`dispatch.py`),
   and connection scheduling fields (`next_sync_at`, `backoff_until`,
   `error_count`, `last_sync_at`, `last_error`) in migration v2.
6. ✔ Real `FirefliesConnector.sync()`: `transcripts(fromDate, toDate)` window,
   50/page pagination, 14-day first backfill, 24-hour overlap, revision ids.
7. ✔ `GmailConnector.sync()`: `users.history.list`, `historyId` cursor, full
   history pagination, 404 → `reset` → bounded backfill, 401 → reauth, 429 →
   `Retry-After`.
8. ✔ Webhooks gated behind `GATHER_INGRESS=http` (`poll`/`stdio` leave ingestion
   to the poller). Dev-only `POST /sync` and `gather sync` run one tick.

**Phase 3 — engine IPC** ✔
9. ✔ `ipc/stdio.py` JSON-lines server over `Runtime` (`protocol.py`,
   `router.py`, `handlers.py`), with fast/slow worker pools, a thread-safe stdout
   writer, `engine.ready`/`run.*`/`sync.status`/`proposal.*` events, and a
   `gather.engine` entry point (`gather-engine` script + `python -m
   gather.engine`).
10. ✔ `scheduler.py`: one non-overlapping tick that polls due connections, runs
    the nudge sweep when due, republishes pending proposals, survives failures,
    catches up immediately after start/sleep, and runs as a cancellable run.
    `GATHER_POLL_INTERVAL` / `GATHER_SWEEP_INTERVAL` control cadence;
    `--no-scheduler` disables it for tests.
11. ✔ `secrets.py`: OS keychain via `keyring` with an explicit `0600` file
    fallback (`auto`), or `keychain`/`file`/`env` modes. Secrets are resolved
    store-first, then environment, and injected into the LLM/connectors without
    a required `.env`; `settings.update` never returns values. Non-secret
    settings are persisted in the same store and overlaid at startup.
12. ✔ One engine per workspace: `lock.py` advisory lock released by the OS on
    crash. UTF-8 stdio, redacted rotated logs, SIGTERM/SIGINT shutdown.
    Continued in Phase 4 (`13` onward).
    Verified by 63 tests, including a subprocess that spawns the engine and
    performs ingest → proposal → approve → tasks over JSON lines.

**Phase 4 — Tauri shell**
13. ✔ `apps/desktop/` (Rust + web UI), sidecar config, engine supervision,
    stdio transport. Dev mode runs the project virtualenv (`GATHER_PYTHON`,
    `GATHER_ENGINE`, `GATHER_DATA_DIR`, `GATHER_FIXTURE`, `GATHER_SCHEDULER`
    override); release mode prefers a `gather-engine` sidecar next to the
    executable.
    The shell owns request/response correlation (`id` + `oneshot` + timeout),
    so commands return the engine's `result` directly and the web UI never
    has to subscribe to transport events. Engine stderr is captured for
    `engine_diagnostics`, and the supervisor restarts a crashed engine up to
    ten times, resetting the counter on `engine.ready`.
14. ✔ Approval inbox → tasks → connections → "Sync now".
    Settings persistence is still a placeholder in the web UI.
    Verified by `cargo test` (spawn → health → ingest → propose → approve a
    selected subset → tasks) and by 63 Python tests.
    Remaining for this phase: tray, single instance, power events, deep links.

**Phase 5 — packaging**
15. PyInstaller spec, dependency trim, per-OS CI, signing/notarization, updater.

**Phase 6 — connectors**
16. Slack (Socket Mode), Calendar, Drive, Teams.

Each phase leaves the app runnable and the tests green.

---

## 16. Decisions (resolved 2026-11-18)

| # | Question | Decision | Consequence |
|---|---|---|---|
| 1 | Background posture | **Tray-resident + launch at login** | Build tray menu, single-instance lock, quit semantics, "works while running" messaging. The scheduler runs for the life of the process. |
| 2 | Distribution scope | **Personal now, public later** | OAuth consent screen in Testing mode; no Google CASA work in v1. Keep the flow verification-compatible so public release stays possible. |
| 3 | Engine IPC | **stdio JSON-lines** | No port or token. Logs go to stderr; stdout is protocol only. `api.py` stays as the headless dev adapter. |
| 4 | Local models in v1 | **No — hosted free models only** | Ollama moves to v2. V1 requires a Groq/OpenRouter key; fixtures remain the offline demo path. |
| 5 | Frontend stack | **React + TypeScript (Vite)** | `apps/desktop/` uses React. Component libraries are available for the approval inbox and task views. |
| 6 | Launch platforms | **Linux first, then macOS; Windows via CI** | Phase 5 proves packaging on Linux, adds signed/notarized macOS next, and defers Windows until the pipeline is stable. |

These resolve the Phase 4 blockers. Phases 2 and 3 were never blocked by them.

---

## 17. Glossary

| Term | Meaning here |
|---|---|
| **Engine** | the bundled Python process; hosts graphs, connectors, DB, scheduler |
| **Shell** | the Tauri app; window, tray, supervision, IPC client |
| **Connector** | dumb provider I/O: verify, fetch, normalize, execute one capability |
| **Capability** | a verb such as `search_messages`, `get_transcript`, `send_message` |
| **Event** | canonical envelope keyed by `kind` (`meeting.completed`, `message.received`, …) |
| **Proposal** | something awaiting a human: an extracted task set, a reply review, a nudge |
| **Run** | one execution of a LangGraph workflow, identified by `thread_id` |
| **Workspace** | the single local tenant; stored as `org_id` in the schema |
| **Checkpoint** | serialized graph state that lets an interrupted run resume |
| **Poll source** | cursor-based ingestion loop (the desktop default) |
| **Push source** | webhook delivery (dev/relay only) |

---

## 18. Decision log

| # | Decision | Rationale |
|---|---|---|
| D1 | Desktop app, not SaaS | local data, user-owned keys, no server cost, strongest privacy story |
| D2 | Python engine as a bundled sidecar | keep LangGraph/pydantic; Rust provides shell, not reasoning |
| D3 | stdio JSON-lines IPC | no ports, no auth surface, no firewall prompts, OS-neutral |
| D4 | Poll, don't push | a laptop cannot host a webhook URL; Socket Mode where a provider offers it |
| D5 | Keep `org_id` as workspace id | avoid a schema-wide rename; leaves room for shared workspaces later |
| D6 | SQLite + migrations + SqliteSaver | single-user, local, durable across restarts and updates |
| D7 | Human approval gate | trust is the product; auto-send needs eval evidence |
| D8 | Free hosted models for v1; local models in v2 | matches the no-cost constraint; hosted-only keeps the first release smaller |
| D9 | Deterministic dates, ownership, dedupe | models are unreliable at arithmetic and identity; code is not |
| D10 | Quote required on every task | auditability and the fastest way to spot a bad extraction |
| D11 | Slack deferred to v2 | Socket Mode is right but app distribution is a project of its own |
| D12 | Keep `api.py` as dev/headless mode | fastest inner loop; not the shipping transport |
| D13 | Tray-resident, launch at login | makes the watch loop feel proactive; the app-closed limitation is stated plainly |
| D14 | Personal build first, public release later | avoids Google verification + CASA blocking v1; keeps the flow compatible |
| D15 | No Ollama in v1 | one model path ships sooner; offline demos still use fixtures |
| D16 | React + TypeScript in the webview | widest ecosystem for the approval inbox and task views |
| D17 | Linux first, macOS next, Windows via CI | build where it can be tested natively; stage the signing work |
