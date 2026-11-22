/**
 * InboxView — pending proposals (task approvals + nudges).
 * The core user-facing loop of gather: nothing is created or sent without
 * the user clicking Approve.
 */
import { useEffect, useState } from "react";
import {
  CheckCircle2,
  XCircle,
  ChevronDown,
  ChevronRight,
  Users,
  Mail,
} from "lucide-react";
import { engine, type Proposal, type ProposalTask } from "../engine";
import { Badge, Button, EmptyState, Spinner, formatDate, relativeDate, isOverdue } from "./ui";

// ─── Task row inside a task-approval proposal ────────────────────────────────

function TaskCandidate({
  task,
  selected,
  onToggle,
}: {
  task: ProposalTask;
  selected: boolean;
  onToggle: () => void;
}) {
  const [quoteOpen, setQuoteOpen] = useState(false);
  const overdue = isOverdue(task.due_date);

  return (
    <div
      className={`border-b border-[oklch(var(--border)/0.2)] last:border-0 transition-colors ${
        selected ? "bg-[oklch(var(--primary)/0.04)]" : ""
      }`}
    >
      <div className="flex items-start gap-3 px-4 py-3">
        {/* Checkbox */}
        <button
          onClick={onToggle}
          className="mt-0.5 flex-none w-4 h-4 border border-[oklch(var(--border)/0.5)] flex items-center justify-center transition-colors hover:border-[oklch(var(--primary))]"
          style={{
            background: selected ? "oklch(var(--primary))" : "transparent",
          }}
          aria-checked={selected}
          role="checkbox"
        >
          {selected && (
            <svg width="10" height="8" viewBox="0 0 10 8" fill="none">
              <path d="M1 4L3.5 6.5L9 1" stroke="oklch(var(--primary-foreground))" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
          )}
        </button>

        {/* Content */}
        <div className="flex-1 min-w-0">
          <p className="text-[0.8125rem] font-medium text-[oklch(var(--foreground))] leading-snug">
            {task.title}
          </p>
          <div className="flex items-center gap-3 mt-1 flex-wrap">
            {task.owner_name && (
              <span className="flex items-center gap-1 text-[0.75rem] text-[oklch(var(--muted-foreground))]">
                <Users size={11} />
                {task.owner_name}
              </span>
            )}
            {task.due_date && (
              <span
                className={`text-[0.75rem] flex items-center gap-1 ${
                  overdue
                    ? "text-[oklch(var(--destructive))]"
                    : "text-[oklch(var(--muted-foreground))]"
                }`}
              >
                {formatDate(task.due_date)}
                <span className="opacity-60">({relativeDate(task.due_date)})</span>
              </span>
            )}
            {task.confidence !== undefined && (
              <Badge variant={task.confidence >= 0.8 ? "success" : "warn"}>
                {Math.round(task.confidence * 100)}% confident
              </Badge>
            )}
          </div>

          {/* Quote toggle */}
          {task.quote && (
            <div className="mt-1.5">
              <button
                onClick={() => setQuoteOpen((o) => !o)}
                className="flex items-center gap-1 text-[0.6875rem] text-[oklch(var(--muted-foreground))] hover:text-[oklch(var(--foreground))] transition-colors"
              >
                {quoteOpen ? <ChevronDown size={11} /> : <ChevronRight size={11} />}
                Source quote
              </button>
              {quoteOpen && (
                <blockquote className="mt-1.5 pl-3 border-l-2 border-[oklch(var(--border)/0.4)] text-[0.75rem] text-[oklch(var(--muted-foreground))] italic leading-relaxed">
                  "{task.quote}"
                </blockquote>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

// ─── Task approval card ───────────────────────────────────────────────────────

function TaskApprovalCard({
  proposal,
  onDone,
}: {
  proposal: Proposal;
  onDone: () => void;
}) {
  const tasks = proposal.payload.tasks || [];
  const [selected, setSelected] = useState<Set<number>>(
    () => new Set(tasks.map((_, i) => i))
  );
  const [loading, setLoading] = useState<"approve" | "reject" | null>(null);

  function toggleAll() {
    setSelected((s) =>
      s.size === tasks.length ? new Set() : new Set(tasks.map((_, i) => i))
    );
  }

  async function handleApprove() {
    setLoading("approve");
    try {
      await engine.proposals.approve(proposal.id, [...selected]);
      onDone();
    } finally {
      setLoading(null);
    }
  }

  async function handleReject() {
    setLoading("reject");
    try {
      await engine.proposals.reject(proposal.id);
      onDone();
    } finally {
      setLoading(null);
    }
  }

  return (
    <div className="border border-[oklch(var(--border)/0.3)] bg-[oklch(var(--card))]">
      {/* Header */}
      <div className="flex items-start justify-between gap-4 px-4 pt-4 pb-3 border-b border-[oklch(var(--border)/0.2)]">
        <div>
          <div className="flex items-center gap-2 mb-0.5">
            <Badge variant="default">Task approval</Badge>
            <span className="text-[0.6875rem] text-[oklch(var(--muted-foreground))]">
              {formatDate(proposal.created_at)}
            </span>
          </div>
          <p className="text-[0.9375rem] font-semibold text-[oklch(var(--foreground))] leading-tight">
            {proposal.payload.meeting || "Meeting"}
          </p>
          <p className="text-[0.75rem] text-[oklch(var(--muted-foreground))] mt-0.5">
            {tasks.length} commitment{tasks.length !== 1 ? "s" : ""} extracted
          </p>
        </div>
        <button
          onClick={toggleAll}
          className="text-[0.75rem] text-[oklch(var(--muted-foreground))] hover:text-[oklch(var(--foreground))] transition-colors whitespace-nowrap mt-1"
        >
          {selected.size === tasks.length ? "Deselect all" : "Select all"}
        </button>
      </div>

      {/* Task candidates */}
      <div>
        {tasks.map((task, i) => (
          <TaskCandidate
            key={i}
            task={task}
            selected={selected.has(i)}
            onToggle={() =>
              setSelected((s) => {
                const next = new Set(s);
                next.has(i) ? next.delete(i) : next.add(i);
                return next;
              })
            }
          />
        ))}
      </div>

      {/* Footer actions */}
      <div className="flex items-center justify-between px-4 py-3 border-t border-[oklch(var(--border)/0.2)] bg-[oklch(var(--muted)/0.3)]">
        <span className="text-[0.75rem] text-[oklch(var(--muted-foreground))]">
          {selected.size} of {tasks.length} selected
        </span>
        <div className="flex items-center gap-2">
          <Button
            variant="ghost"
            onClick={handleReject}
            disabled={loading !== null}
          >
            {loading === "reject" ? <Spinner size={13} /> : <XCircle size={13} />}
            Reject all
          </Button>
          <Button
            variant="primary"
            onClick={handleApprove}
            disabled={loading !== null || selected.size === 0}
          >
            {loading === "approve" ? <Spinner size={13} /> : <CheckCircle2 size={13} />}
            Approve {selected.size > 0 ? `(${selected.size})` : ""}
          </Button>
        </div>
      </div>
    </div>
  );
}

// ─── Nudge approval card ─────────────────────────────────────────────────────

function NudgeApprovalCard({
  proposal,
  onDone,
}: {
  proposal: Proposal;
  onDone: () => void;
}) {
  const [loading, setLoading] = useState<"approve" | "reject" | null>(null);

  async function handleApprove() {
    setLoading("approve");
    try {
      await engine.proposals.approve(proposal.id);
      onDone();
    } finally {
      setLoading(null);
    }
  }

  async function handleReject() {
    setLoading("reject");
    try {
      await engine.proposals.reject(proposal.id);
      onDone();
    } finally {
      setLoading(null);
    }
  }

  return (
    <div className="border border-[oklch(var(--border)/0.3)] bg-[oklch(var(--card))]">
      {/* Header */}
      <div className="px-4 pt-4 pb-3 border-b border-[oklch(var(--border)/0.2)]">
        <div className="flex items-center gap-2 mb-0.5">
          <Badge variant="warn">Nudge draft</Badge>
          <span className="text-[0.6875rem] text-[oklch(var(--muted-foreground))]">
            {formatDate(proposal.created_at)}
          </span>
        </div>
        <p className="text-[0.9375rem] font-semibold text-[oklch(var(--foreground))] leading-tight">
          {proposal.payload.task_title || "Follow-up nudge"}
        </p>
      </div>

      {/* Email preview */}
      <div className="px-4 py-3 space-y-2">
        <div className="flex items-center gap-2 text-[0.75rem] text-[oklch(var(--muted-foreground))]">
          <Mail size={12} />
          <span>To: <strong className="text-[oklch(var(--foreground))]">{proposal.payload.to}</strong></span>
        </div>
        <div className="text-[0.75rem] text-[oklch(var(--muted-foreground))]">
          Subject: <strong className="text-[oklch(var(--foreground))]">{proposal.payload.subject}</strong>
        </div>
        <div className="mt-2 p-3 bg-[oklch(var(--muted)/0.4)] border border-[oklch(var(--border)/0.2)] text-[0.8125rem] text-[oklch(var(--foreground))] leading-relaxed whitespace-pre-wrap">
          {proposal.payload.body}
        </div>
      </div>

      {/* Actions */}
      <div className="flex items-center justify-end gap-2 px-4 py-3 border-t border-[oklch(var(--border)/0.2)] bg-[oklch(var(--muted)/0.3)]">
        <Button variant="ghost" onClick={handleReject} disabled={loading !== null}>
          {loading === "reject" ? <Spinner size={13} /> : <XCircle size={13} />}
          Discard
        </Button>
        <Button variant="primary" onClick={handleApprove} disabled={loading !== null}>
          {loading === "approve" ? <Spinner size={13} /> : <Mail size={13} />}
          Send nudge
        </Button>
      </div>
    </div>
  );
}

// ─── InboxView ───────────────────────────────────────────────────────────────

export function InboxView() {
  const [proposals, setProposals] = useState<Proposal[]>([]);
  const [loading, setLoading] = useState(true);

  async function load() {
    setLoading(true);
    try {
      const { proposals } = await engine.proposals.list();
      setProposals(proposals);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
  }, []);

  return (
    <div className="flex flex-col h-full">
      {/* Toolbar */}
      <div className="flex items-center justify-between px-6 py-3 border-b border-[oklch(var(--border)/0.2)]">
        <div>
          <h2 className="text-[0.9375rem] font-semibold text-[oklch(var(--foreground))]">
            Approval Inbox
          </h2>
          {!loading && proposals.length > 0 && (
            <p className="text-[0.75rem] text-[oklch(var(--muted-foreground))] mt-0.5">
              {proposals.length} pending
            </p>
          )}
        </div>
        <Button variant="ghost" onClick={load} disabled={loading}>
          {loading ? <Spinner size={13} /> : null}
          Refresh
        </Button>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-y-auto px-6 py-5 space-y-4">
        {loading ? (
          <div className="flex justify-center py-16">
            <Spinner size={24} />
          </div>
        ) : proposals.length === 0 ? (
          <EmptyState
            icon={<CheckCircle2 size={48} />}
            title="All caught up"
            description="New task proposals and nudge drafts appear here as the engine processes meetings and watches your inbox."
          />
        ) : (
          proposals.map((p) =>
            p.kind === "task_approval" ? (
              <TaskApprovalCard key={p.id} proposal={p} onDone={load} />
            ) : (
              <NudgeApprovalCard key={p.id} proposal={p} onDone={load} />
            )
          )
        )}
      </div>
    </div>
  );
}
