/**
 * engine.ts — typed IPC bridge between React and the Python gather engine.
 *
 * Every method calls a Tauri command registered in src-tauri/src/lib.rs.
 * The Rust side owns the JSON-lines transport, correlates request ids and
 * resolves the returned promise with the engine's `result` object — so these
 * methods behave like ordinary async functions.
 *
 * Argument names are snake_case because the commands use
 * `#[tauri::command(rename_all = "snake_case")]`, which keeps the JS boundary
 * identical to the Python protocol.
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
  fingerprint?: string;
}

export interface Proposal {
  id: string;
  kind: "task_approval" | "nudge" | "nudge_approval" | "reply_review";
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
  status: "active" | "paused" | "error" | string;
  mode: string;
  user_id?: string;
  last_sync_at?: string;
  next_sync_at?: string;
  last_error?: string;
}

export interface EngineStatus {
  /** ready | running | syncing | needs_attention */
  state: string;
  version: string;
  fixture_mode: boolean;
  org_id?: string;
  workspace?: string;
  data_dir?: string;
  schema_version?: number;
  providers?: string[];
  models?: string[];
  scheduler?: { enabled?: boolean; running?: boolean; ticks?: number };
  secrets_backend?: string;
}

export interface EngineInfo {
  version: string;
  workspace_id: string;
  data_dir: string;
  fixture_mode: boolean;
  models: string[];
  scheduler: boolean;
  pid: number;
}

// ─── Tauri invoke import ─────────────────────────────────────────────────────

import { invoke } from "@tauri-apps/api/core";

// ─── Persistent flags (file-backed, survives WebView cache clears in dev) ───────

export const flags = {
  get: (key: string): Promise<string | null> =>
    invoke<string | null>("get_flag", { key }),
  set: (key: string, value: string): Promise<void> =>
    invoke<void>("set_flag", { key, value }),
  delete: (key: string): Promise<void> =>
    invoke<void>("delete_flag", { key }),
};

/** "all" is a UI filter concept, not a task status. */
function statusParam(status?: string): string | null {
  if (!status || status === "all") return null;
  return status;
}

function rpc<T>(method: string, params?: Record<string, unknown>): Promise<T> {
  return invoke<T>("engine_rpc", { method, params: params ?? {} });
}

export { rpc as engineRpc };

/** Bound an IPC call so a stalled engine cannot block UI indefinitely. */
export function withTimeout<T>(promise: Promise<T>, ms: number): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error("timed out")), ms);
    promise.then(
      (value) => {
        clearTimeout(timer);
        resolve(value);
      },
      (error) => {
        clearTimeout(timer);
        reject(error);
      },
    );
  });
}

// ─── Public API ──────────────────────────────────────────────────────────────

export const engine = {
  status: async (): Promise<EngineStatus> => rpc<EngineStatus>("health"),

  /** True when the child process is alive (does not round-trip to Python). */
  running: async (): Promise<boolean> => invoke<boolean>("engine_running"),

  /** Last engine stderr lines — shown when the engine has stopped. */
  diagnostics: async (): Promise<string> => invoke<string>("engine_diagnostics"),

  /** Payload of the last `engine.ready` event, if one was seen. */
  info: async (): Promise<EngineInfo | null> => rpc<EngineInfo | null>("health"),

  onboarding_status: async (): Promise<{
    onboarded: boolean;
    needs_onboarding: boolean;
    workspace?: string;
  }> => rpc("onboarding.status"),

  onboarding_complete: async (params?: {
    org_name?: string;
  }): Promise<{ ok: boolean; onboarded: boolean; persisted?: boolean }> =>
    rpc("onboarding.complete", params ?? {}),

  onboarding_reset: async (): Promise<{ ok: boolean; onboarded: boolean }> =>
    rpc("onboarding.reset"),

  restart: async (): Promise<void> => { /* no-op: engine restarts are handled by the Tauri supervisor */ },

  tasks: {
    list: async (status?: string): Promise<{ tasks: Task[] }> =>
      rpc<{ tasks: Task[] }>("tasks.list", { status: statusParam(status) }),

    update: async (id: string, status: TaskStatus): Promise<{ task: Task }> =>
      rpc<{ task: Task }>("tasks.update", { task_id: id, status }),
  },

  proposals: {
    list: async (status?: "pending" | "approved" | "rejected"): Promise<{ proposals: Proposal[] }> =>
      rpc<{ proposals: Proposal[] }>("proposals.list", { status: status ?? "pending" }),

    approve: async (
      proposal_id: string,
      approved_indices?: number[],
    ): Promise<{ already: boolean; status: string; created?: unknown[] }> =>
      rpc("proposals.approve", { proposal_id, approved_indices }),

    reject: async (proposal_id: string): Promise<{ already: boolean; status: string }> =>
      rpc("proposals.reject", { proposal_id }),
  },

  connections: {
    list: async (): Promise<{
      connections: Connection[];
      providers: string[];
      credentials: Record<string, boolean>;
    }> => rpc("connections.list"),

    connect: async (
      provider: "gmail" | "fireflies",
      params?: Record<string, unknown>,
    ): Promise<{ ok: boolean; user_email?: string; provider: string }> =>
      rpc("connections.connect", { provider, ...params }),
  },

  scheduler: {
    runNow: async (): Promise<{ accepted: boolean; events?: number }> =>
      rpc("sync.now"),
  },
};
