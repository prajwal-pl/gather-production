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
  Sparkles,
  Calendar,
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
      className={`border-b border-[oklch(var(--border)/0.5)] last:border-0 transition-all ${
        selected ? "bg-[oklch(var(--accent)/0.04)]" : "hover:bg-[oklch(var(--muted)/0.25)]"
      }`}
    >
      <div className="flex items-start gap-3.5 px-4 py-3">
        {/* Animated Checkbox */}
        <button
          onClick={onToggle}
          className={`mt-0.5 flex-none w-4 h-4 rounded border transition-all flex items-center justify-center ${
            selected
              ? "bg-[oklch(var(--accent))] border-[oklch(var(--accent))] text-white shadow-xs"
              : "border-[oklch(var(--border))] bg-[oklch(var(--card))]"
          }`}
          aria-checked={selected}
          role="checkbox"
        >
          {selected && (
            <svg width="10" height="8" viewBox="0 0 10 8" fill="none">
              <path d="M1 4L3.5 6.5L9 1" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
          )}
        </button>

        {/* Content */}
        <div className="flex-1 min-w-0">
          <p className="text-[0.8125rem] font-medium text-[oklch(var(--foreground))] leading-snug">
            {task.title}
          </p>
          <div className="flex items-center gap-3 mt-1.5 flex-wrap">
            {task.owner_name && (
              <span className="flex items-center gap-1 text-[0.75rem] text-[oklch(var(--muted-foreground))] font-medium">
                <Users size={11} />
                {task.owner_name}
              </span>
            )}
            {task.due_date && (
              <span
                className={`text-[0.75rem] flex items-center gap-1 ${
                  overdue
                    ? "text-[oklch(var(--destructive))] font-medium"
                    : "text-[oklch(var(--muted-foreground))]"
                }`}
              >
                <Calendar size={11} />
                {formatDate(task.due_date)}
                <span className="opacity-60">({relativeDate(task.due_date)})</span>
              </span>
            )}
            {task.confidence !== undefined && (
              <Badge variant={task.confidence >= 0.8 ? "success" : "warn"}>
                {Math.round(task.confidence * 100)}% confidence
              </Badge>
            )}
          </div>

          {/* Quote toggle */}
          {task.quote && (
            <div className="mt-2">
              <button
                onClick={() => setQuoteOpen((o) => !o)}
                className="flex items-center gap-1 text-[0.6875rem] text-[oklch(var(--muted-foreground))] hover:text-[oklch(var(--foreground))] transition-colors"
              >
                {quoteOpen ? <ChevronDown size={11} /> : <ChevronRight size={11} />}
                Verbatim transcript quote
              </button>
              {quoteOpen && (
                <blockquote className="mt-1.5 pl-3 border-l-2 border-[oklch(var(--accent)/0.5)] text-[0.75rem] text-[oklch(var(--muted-foreground))] italic leading-relaxed bg-[oklch(var(--muted)/0.3)] py-1.5 pr-2 rounded-r">
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
    <div className="border border-[oklch(var(--border))] bg-[oklch(var(--card))] rounded-xl shadow-xs overflow-hidden animate-slide-up">
      {/* Header */}
      <div className="flex items-start justify-between gap-4 px-5 pt-4 pb-3 border-b border-[oklch(var(--border))] bg-[oklch(var(--card))]">
        <div>
          <div className="flex items-center gap-2 mb-1">
            <Badge variant="accent">
              <Sparkles size={10} className="mr-0.5" /> Meeting commitments
            </Badge>
            <span className="text-[0.6875rem] text-[oklch(var(--muted-foreground))]">
              {formatDate(proposal.created_at)}
            </span>
          </div>
          <p className="text-[0.9375rem] font-semibold text-[oklch(var(--foreground))] leading-tight">
            {proposal.payload.meeting || "Meeting Transcript"}
          </p>
          <p className="text-[0.75rem] text-[oklch(var(--muted-foreground))] mt-0.5">
            {tasks.length} action item{tasks.length !== 1 ? "s" : ""} detected · Human approval required
          </p>
        </div>
        <button
          onClick={toggleAll}
          className="text-[0.75rem] font-medium text-[oklch(var(--muted-foreground))] hover:text-[oklch(var(--foreground))] transition-colors whitespace-nowrap mt-1"
        >
          {selected.size === tasks.length ? "Deselect all" : "Select all"}
        </button>
      </div>

      {/* Task candidates */}
      <div className="divide-y divide-[oklch(var(--border)/0.4)]">
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
      <div className="flex items-center justify-between px-5 py-3 border-t border-[oklch(var(--border))] bg-[oklch(var(--muted)/0.3)]">
        <span className="text-[0.75rem] text-[oklch(var(--muted-foreground))]">
          {selected.size} of {tasks.length} selected
        </span>
        <div className="flex items-center gap-2">
          <Button
            variant="ghost"
            onClick={handleReject}
            disabled={loading !== null}
            className="text-xs"
          >
            {loading === "reject" ? <Spinner size={12} /> : <XCircle size={12} />}
            Reject all
          </Button>
          <Button
            variant="primary"
            onClick={handleApprove}
            disabled={loading !== null || selected.size === 0}
            className="text-xs"
          >
            {loading === "approve" ? <Spinner size={12} /> : <CheckCircle2 size={12} />}
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
    <div className="border border-[oklch(var(--border))] bg-[oklch(var(--card))] rounded-xl shadow-xs overflow-hidden animate-slide-up">
      {/* Header */}
      <div className="px-5 pt-4 pb-3 border-b border-[oklch(var(--border))] bg-[oklch(var(--card))]">
        <div className="flex items-center gap-2 mb-1">
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
      <div className="px-5 py-4 space-y-2">
        <div className="flex items-center gap-2 text-[0.75rem] text-[oklch(var(--muted-foreground))]">
          <Mail size={12} />
          <span>To: <strong className="text-[oklch(var(--foreground))]">{proposal.payload.to}</strong></span>
        </div>
        <div className="text-[0.75rem] text-[oklch(var(--muted-foreground))]">
          Subject: <strong className="text-[oklch(var(--foreground))]">{proposal.payload.subject}</strong>
        </div>
        <div className="mt-2 p-3.5 bg-[oklch(var(--muted)/0.4)] border border-[oklch(var(--border)/0.6)] rounded-lg text-[0.8125rem] text-[oklch(var(--foreground))] leading-relaxed whitespace-pre-wrap font-sans">
          {proposal.payload.body}
        </div>
      </div>

      {/* Actions */}
      <div className="flex items-center justify-end gap-2 px-5 py-3 border-t border-[oklch(var(--border))] bg-[oklch(var(--muted)/0.3)]">
        <Button variant="ghost" onClick={handleReject} disabled={loading !== null} className="text-xs">
          {loading === "reject" ? <Spinner size={12} /> : <XCircle size={12} />}
          Discard
        </Button>
        <Button variant="primary" onClick={handleApprove} disabled={loading !== null} className="text-xs">
          {loading === "approve" ? <Spinner size={12} /> : <Mail size={12} />}
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
    <div className="flex flex-col h-full bg-[oklch(var(--background))]">
      {/* Toolbar */}
      <div className="flex items-center justify-between px-6 py-4 border-b border-[oklch(var(--border))]">
        <div>
          <h2 className="text-sm font-semibold text-[oklch(var(--foreground))] tracking-tight">
            Approval Inbox
          </h2>
          {!loading && (
            <p className="text-xs text-[oklch(var(--muted-foreground))] mt-0.5">
              {proposals.length > 0
                ? `${proposals.length} item${proposals.length !== 1 ? "s" : ""} awaiting your decision`
                : "No pending approvals"}
            </p>
          )}
        </div>
        <Button variant="outline" onClick={load} disabled={loading} className="text-xs h-8">
          {loading ? <Spinner size={12} /> : null}
          Refresh
        </Button>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-y-auto px-6 py-6 space-y-5">
        {loading ? (
          <div className="flex justify-center py-20">
            <Spinner size={24} />
          </div>
        ) : proposals.length === 0 ? (
          <EmptyState
            icon={<CheckCircle2 size={36} />}
            title="All caught up"
            description="New extracted meeting commitments and follow-up nudge drafts will appear here. Nothing leaves your desktop without your explicit sign-off."
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
