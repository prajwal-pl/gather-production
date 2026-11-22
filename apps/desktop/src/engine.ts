/**
 * engine.ts — typed IPC bridge between React and the Python gather engine.
 *
 * In Phase 4 scaffold mode the engine process isn't spawned yet, so every
 * call resolves with realistic mock data.  When the Rust IPC layer is wired
 * (lib.rs engine spawn + command forwarding) replace `mockInvoke` with the
 * real `invoke` import from @tauri-apps/api/core.
 *
 * Shape contract:  every Tauri command returns  { ok, result?, error? }
 * matching the JSON-lines protocol defined in ipc/protocol.py.
 */

// ─── Types ──────────────────────────────────────────────────────────────────

export type TaskStatus =
  | "open"
  | "in_progress"
  | "blocked"
  | "done"
  | "dismissed";

export interface Task {
  id: string;
  title: string;
  detail?: string;
  owner_email?: string;
  due_date?: string;
  status: TaskStatus;
  source_meeting_id?: string;
  source_quote?: string;
  confidence?: number;
  created_at?: string;
  updated_at?: string;
}

export interface ProposalTask {
  title: string;
  owner_name?: string;
  owner_email?: string;
  due_date?: string;
  quote?: string;
  confidence?: number;
}

export interface Proposal {
  id: string;
  kind: "task_approval" | "nudge_approval" | "reply_review";
  status: "pending" | "approved" | "rejected";
  thread_id?: string;
  created_at?: string;
  payload: {
    meeting?: string;
    tasks?: ProposalTask[];
    // nudge
    subject?: string;
    body?: string;
    to?: string;
    // reply
    task_title?: string;
    intent?: string;
    summary?: string;
  };
}

export interface Connection {
  id: string;
  provider: "fireflies" | "gmail" | string;
  status: "active" | "paused" | "error";
  mode: string;
  user_id?: string;
  last_sync_at?: string;
  next_sync_at?: string;
  last_error?: string;
}

export interface EngineStatus {
  version: string;
  fixture_mode: boolean;
  scheduler: { enabled: boolean; running: boolean; ticks?: number };
  uptime_s?: number;
}

// ─── Mock data (Phase 4 scaffold) ───────────────────────────────────────────

const MOCK_TASKS: Task[] = [
  {
    id: "t1",
    title: "Send revised proposal to Acme Corp",
    detail: "Include the revised pricing and updated timeline",
    owner_email: "alice@example.com",
    due_date: "2026-11-25",
    status: "open",
    source_quote: "Alice said she'd send the revised proposal by end of week",
    confidence: 0.92,
    created_at: "2026-11-20T09:00:00Z",
  },
  {
    id: "t2",
    title: "Schedule onboarding call with new team member",
    owner_email: "bob@example.com",
    due_date: "2026-11-23",
    status: "in_progress",
    source_quote: "Bob will schedule the onboarding call before the sprint ends",
    confidence: 0.87,
    created_at: "2026-11-19T14:30:00Z",
  },
  {
    id: "t3",
    title: "Review and merge authentication PR",
    owner_email: "carol@example.com",
    due_date: "2026-11-22",
    status: "blocked",
    detail: "Waiting on security sign-off",
    source_quote: "Carol committed to reviewing the auth PR this sprint",
    confidence: 0.78,
    created_at: "2026-11-18T11:00:00Z",
  },
  {
    id: "t4",
    title: "Update API documentation",
    owner_email: "dave@example.com",
    due_date: "2026-11-15",
    status: "done",
    created_at: "2026-11-10T10:00:00Z",
  },
];

const MOCK_PROPOSALS: Proposal[] = [
  {
    id: "p1",
    kind: "task_approval",
    status: "pending",
    thread_id: "meeting-ff001",
    created_at: "2026-11-22T08:00:00Z",
    payload: {
      meeting: "Q4 Planning — Engineering Sync",
      tasks: [
        {
          title: "Draft RFC for new caching layer",
          owner_name: "Alice Chen",
          owner_email: "alice@example.com",
          due_date: "2026-11-30",
          quote: "Alice will draft the RFC for the caching layer by end of November",
          confidence: 0.91,
        },
        {
          title: "Set up staging environment for beta",
          owner_name: "Bob Kim",
          owner_email: "bob@example.com",
          due_date: "2026-11-28",
          quote: "Bob said he'll have staging ready before the beta starts",
          confidence: 0.85,
        },
        {
          title: "Collect customer feedback on v2 dashboard",
          owner_name: "Carol Davis",
          owner_email: "carol@example.com",
          quote: "Carol will gather feedback from the top 5 accounts",
          confidence: 0.79,
        },
      ],
    },
  },
  {
    id: "p2",
    kind: "nudge_approval",
    status: "pending",
    created_at: "2026-11-22T09:30:00Z",
    payload: {
      task_title: "Review and merge authentication PR",
      to: "carol@example.com",
      subject: "Re: Auth PR review",
      body: "Hey Carol — just checking in on the auth PR review. It's been a couple of days and the security sign-off is blocking the release. Even a quick pass to flag blockers would help. Let me know if you need anything.",
    },
  },
];

const MOCK_CONNECTIONS: Connection[] = [
  {
    id: "c1",
    provider: "fireflies",
    status: "active",
    mode: "org",
    last_sync_at: "2026-11-22T10:00:00Z",
    next_sync_at: "2026-11-22T10:05:00Z",
  },
  {
    id: "c2",
    provider: "gmail",
    status: "active",
    mode: "user",
    user_id: "alice@example.com",
    last_sync_at: "2026-11-22T10:01:00Z",
    next_sync_at: "2026-11-22T10:06:00Z",
  },
];

// ─── Mock invoke (replace with real Tauri invoke in Phase 4 full wiring) ────

function delay(ms: number) {
  return new Promise<void>((r) => setTimeout(r, ms));
}

// In-memory state for the mock so approve/reject actually updates the list
let _proposals = [...MOCK_PROPOSALS];
let _tasks = [...MOCK_TASKS];

async function mockInvoke<T>(method: string, params?: Record<string, unknown>): Promise<T> {
  await delay(120 + Math.random() * 80);

  switch (method) {
    case "engine_health":
      return { version: "0.1.0", fixture_mode: true } as T;

    case "engine_status":
      return {
        version: "0.1.0",
        fixture_mode: true,
        scheduler: { enabled: true, running: true, ticks: 42 },
        uptime_s: 3610,
      } as T;

    case "tasks_list": {
      const { status } = (params || {}) as { status?: string };
      const filtered = status && status !== "all"
        ? _tasks.filter((t) => t.status === status)
        : _tasks;
      return { tasks: filtered } as T;
    }

    case "tasks_update": {
      const { id, status } = (params || {}) as { id: string; status: TaskStatus };
      _tasks = _tasks.map((t) => (t.id === id ? { ...t, status } : t));
      return { ok: true } as T;
    }

    case "proposals_list":
      return { proposals: _proposals.filter((p) => p.status === "pending") } as T;

    case "proposals_approve": {
      const { proposal_id, approved_indices } = (params || {}) as {
        proposal_id: string;
        approved_indices?: number[];
      };
      _proposals = _proposals.map((p) =>
        p.id === proposal_id ? { ...p, status: "approved" } : p
      );
      if (approved_indices !== undefined) {
        const proposal = MOCK_PROPOSALS.find((p) => p.id === proposal_id);
        const tasks = proposal?.payload.tasks || [];
        const newTasks = tasks
          .filter((_, i) => approved_indices.includes(i))
          .map((t, i) => ({
            id: `new-${Date.now()}-${i}`,
            title: t.title,
            owner_email: t.owner_email,
            due_date: t.due_date,
            status: "open" as TaskStatus,
            source_quote: t.quote,
            confidence: t.confidence,
            created_at: new Date().toISOString(),
          }));
        _tasks = [..._tasks, ...newTasks];
      }
      return { ok: true, status: "approved" } as T;
    }

    case "proposals_reject": {
      const { proposal_id } = (params || {}) as { proposal_id: string };
      _proposals = _proposals.map((p) =>
        p.id === proposal_id ? { ...p, status: "rejected" } : p
      );
      return { ok: true, status: "rejected" } as T;
    }

    case "connections_list":
      return { connections: MOCK_CONNECTIONS } as T;

    case "scheduler_run_now":
      return { ok: true, events: 2 } as T;

    default:
      throw new Error(`Unknown mock method: ${method}`);
  }
}

// ─── Public API ──────────────────────────────────────────────────────────────

export const engine = {
  status: () => mockInvoke<EngineStatus>("engine_status"),
  tasks: {
    list: (status?: string) =>
      mockInvoke<{ tasks: Task[] }>("tasks_list", { status }),
    update: (id: string, status: TaskStatus) =>
      mockInvoke("tasks_update", { id, status }),
  },
  proposals: {
    list: () => mockInvoke<{ proposals: Proposal[] }>("proposals_list"),
    approve: (proposal_id: string, approved_indices?: number[]) =>
      mockInvoke("proposals_approve", { proposal_id, approved_indices }),
    reject: (proposal_id: string) =>
      mockInvoke("proposals_reject", { proposal_id }),
  },
  connections: {
    list: () => mockInvoke<{ connections: Connection[] }>("connections_list"),
  },
  scheduler: {
    runNow: () => mockInvoke("scheduler_run_now"),
  },
};
