/**
 * InboxView — pending proposals (task approvals + nudges).
 * The core user-facing loop of gather: nothing is created or sent without
 * the user clicking Approve.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import {
  CheckCircle2,
  XCircle,
  ChevronDown,
  ChevronRight,
  Users,
  Mail,
  Sparkles,
  Calendar,
  Plus,
  FileText,
} from "lucide-react";
import { listen } from "@tauri-apps/api/event";
import { engine, type Proposal, type ProposalTask, type TaskEdit } from "../engine";
import { Badge, Button, EmptyState, ErrorNotice, Spinner, formatDate, relativeDate, isOverdue } from "./ui";

// ─── Task row inside a task-approval proposal ────────────────────────────────

function TaskCandidate({
  task,
  selected,
  onToggle,
  onEdit,
  busy,
}: {
  task: ProposalTask;
  onEdit: (edit: TaskEdit) => void;
  busy: boolean;
  selected: boolean;
  onToggle: () => void;
}) {
  const [quoteOpen, setQuoteOpen] = useState(false);
  const [editing, setEditing] = useState(false);
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
          disabled={busy}
          className={`mt-0.5 flex-none w-4 h-4 rounded border transition-all flex items-center justify-center ${
            selected
              ? "bg-[oklch(var(--accent))] border-[oklch(var(--accent))] text-white shadow-xs"
              : "border-[oklch(var(--border))] bg-[oklch(var(--card))]"
          }`}
          aria-label={task.title}
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
          <p className="text-sm font-medium text-[oklch(var(--foreground))] leading-snug">
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

          {task.fingerprint && <button type="button" className="mt-2 text-xs underline underline-offset-2"
            aria-expanded={editing} onClick={() => setEditing((value) => !value)}>{editing ? "Hide edit fields" : "Edit task"}</button>}
          {editing && (
            <fieldset className="mt-2 flex flex-wrap gap-3" disabled={busy}>
              <label className="text-xs flex-1">Task title<input className="field mt-1" required maxLength={180}
                value={task.title} onChange={(event) => onEdit({ title: event.target.value, due_date: task.due_date || null })} /></label>
              <label className="text-xs">Due date<input className="field mt-1" type="date"
                value={task.due_date || ""} onChange={(event) => onEdit({ title: task.title, due_date: event.target.value || null })} /></label>
            </fieldset>
          )}
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
  onOpenMeeting,
}: {
  proposal: Proposal;
  onDone: () => void;
  onOpenMeeting: (id: string) => void;
}) {
  const tasks = proposal.payload.tasks || [];
  const [selected, setSelected] = useState<Set<number>>(
    () => new Set(tasks.map((_, i) => i))
  );
  const [loading, setLoading] = useState<"approve" | "reject" | null>(null);
  const [error, setError] = useState("");
  const [edits, setEdits] = useState<Record<string, TaskEdit>>({});
  const blankSelectedTitle = [...selected].some((index) => {
    const edit = edits[tasks[index]?.fingerprint || ""];
    return edit !== undefined && !edit.title.trim();
  });

  function toggleAll() {
    setSelected((s) =>
      s.size === tasks.length ? new Set() : new Set(tasks.map((_, i) => i))
    );
  }

  async function handleApprove() {
    setError("");
    setLoading("approve");
    try {
      const normalized = Object.fromEntries(Object.entries(edits).map(([fingerprint, edit]) => [
        fingerprint, { ...edit, title: edit.title.trim() },
      ]));
      const result = await engine.proposals.approve(proposal.id, [...selected], normalized);
      if (result.status !== "approved") throw new Error(`This proposal was already ${result.status}.`);
      onDone();
    } catch (err) {
      setError(String(err));
    } finally {
      setLoading(null);
    }
  }

  async function handleReject() {
    setError("");
    setLoading("reject");
    try {
      await engine.proposals.reject(proposal.id);
      onDone();
    } catch (err) {
      setError(String(err));
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
            {tasks.length} suggested task{tasks.length !== 1 ? "s" : ""}
          </p>
        </div>
        <button
          onClick={toggleAll}
          disabled={loading !== null}
          className="text-[0.75rem] font-medium text-[oklch(var(--muted-foreground))] hover:text-[oklch(var(--foreground))] transition-colors whitespace-nowrap mt-1"
        >
          {selected.size === tasks.length ? "Deselect all" : "Select all"}
        </button>
      </div>

      {proposal.payload.warning && <p className="px-5 py-2 text-sm">{proposal.payload.warning}</p>}
      {proposal.payload.meeting_id && (
        <div className="px-4 py-1"><Button variant="ghost" onClick={() => onOpenMeeting(proposal.payload.meeting_id!)}><FileText size={14} /> View transcript</Button></div>
      )}
      {error && <ErrorNotice>{error}</ErrorNotice>}
      {/* Task candidates */}
      <div className="divide-y divide-[oklch(var(--border)/0.4)]">
        {tasks.map((task, i) => (
          <TaskCandidate
            key={i}
            task={edits[task.fingerprint || ""] ? {
              ...task, title: edits[task.fingerprint || ""].title,
              due_date: edits[task.fingerprint || ""].due_date ?? undefined,
            } : task}
            busy={loading !== null}
            onEdit={(edit) => {
              if (task.fingerprint) setEdits((current) => ({ ...current, [task.fingerprint!]: edit }));
            }}
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
          {blankSelectedTitle ? "Enter a title for each selected task." : null}
          {!blankSelectedTitle && <>{selected.size} of {tasks.length} selected</>}
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
            disabled={loading !== null || selected.size === 0 || blankSelectedTitle}
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
  onDone: (message?: string) => void;
}) {
  const [loading, setLoading] = useState<"approve" | "reject" | null>(null);
  const [error, setError] = useState("");

  async function handleApprove() {
    setError("");
    setLoading("approve");
    try {
      const result = await engine.proposals.approve(proposal.id);
      if (result.status !== "approved") throw new Error(`This proposal was already ${result.status}.`);
      if (proposal.kind === "reply_review") onDone("Reply marked reviewed. You can update the task in Tasks.");
      else if (result.delivered?.delivered === "slack") onDone("Nudge posted to the configured Slack channel.");
      else if (result.delivered?.delivered) onDone("Nudge approved. Slack delivery failed.");
      else onDone("Nudge approved locally.");
    } catch (err) {
      setError(String(err));
    } finally {
      setLoading(null);
    }
  }

  async function handleReject() {
    setError("");
    setLoading("reject");
    try {
      await engine.proposals.reject(proposal.id);
      onDone();
    } catch (err) {
      setError(String(err));
    } finally {
      setLoading(null);
    }
  }

  const reply = proposal.kind === "reply_review";

  return (
    <div className="border border-[oklch(var(--border))] bg-[oklch(var(--card))] rounded-xl shadow-xs overflow-hidden animate-slide-up">
      {/* Header */}
      <div className="px-5 pt-4 pb-3 border-b border-[oklch(var(--border))] bg-[oklch(var(--card))]">
        <div className="flex items-center gap-2 mb-1">
          <Badge variant="warn">{reply ? "Reply review" : "Nudge draft"}</Badge>
          <span className="text-[0.6875rem] text-[oklch(var(--muted-foreground))]">
            {formatDate(proposal.created_at)}
          </span>
        </div>
        <p className="text-[0.9375rem] font-semibold text-[oklch(var(--foreground))] leading-tight">
          {proposal.payload.task?.title || proposal.payload.task_title || "Follow-up nudge"}
        </p>
      </div>

      {/* Email preview */}
      <div className="px-5 py-4 space-y-2">
        <div className="flex items-center gap-2 text-[0.75rem] text-[oklch(var(--muted-foreground))]">
          <Mail size={12} />
          <span>{reply ? "From" : "To"}: <strong className="text-[oklch(var(--foreground))]">{reply ? proposal.payload.message?.from : proposal.payload.to}</strong></span>
        </div>
        <div className="text-[0.75rem] text-[oklch(var(--muted-foreground))]">
          Subject: <strong className="text-[oklch(var(--foreground))]">{proposal.payload.message?.subject || proposal.payload.subject}</strong>
        </div>
        <div className="mt-2 p-3.5 bg-[oklch(var(--muted)/0.4)] border border-[oklch(var(--border)/0.6)] rounded-lg text-[0.8125rem] text-[oklch(var(--foreground))] leading-relaxed whitespace-pre-wrap font-sans">
          {proposal.payload.message?.snippet || proposal.payload.body}
        </div>
      </div>

      {error && <ErrorNotice>{error}</ErrorNotice>}
      {!reply && <p className="px-5 pb-3 text-xs text-[oklch(var(--muted-foreground))]">Approval posts this draft to Slack when a webhook is configured.</p>}
      {/* Actions */}
      <div className="flex items-center justify-end gap-2 px-5 py-3 border-t border-[oklch(var(--border))] bg-[oklch(var(--muted)/0.3)]">
        <Button variant="ghost" onClick={handleReject} disabled={loading !== null} className="text-xs">
          {loading === "reject" ? <Spinner size={12} /> : <XCircle size={12} />}
          Discard
        </Button>
        <Button variant="primary" onClick={handleApprove} disabled={loading !== null} className="text-xs">
          {loading === "approve" ? <Spinner size={12} /> : <Mail size={12} />}
          {reply ? "Mark reviewed" : "Approve nudge"}
        </Button>
      </div>
    </div>
  );
}

// ─── InboxView ───────────────────────────────────────────────────────────────

export function InboxView({ onOpenMeeting }: { onOpenMeeting: (id: string) => void }) {
  const [proposals, setProposals] = useState<Proposal[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [captureOpen, setCaptureOpen] = useState(false);
  const [title, setTitle] = useState("");
  const [transcript, setTranscript] = useState("");
  const [working, setWorking] = useState<"sample" | "transcript">();
  const [captureError, setCaptureError] = useState("");

  const latest = useRef(0);
  const load = useCallback(async () => {
    const request = ++latest.current;
    try {
      const result = await engine.proposals.list();
      if (request !== latest.current) return;
      setProposals(result.proposals);
      setError("");
    } catch (err) {
      if (request === latest.current) setError(String(err));
    } finally {
      if (request === latest.current) setLoading(false);
    }
  }, []);

  useEffect(() => {
    let active = true;
    const subscription = listen<{ data?: { scopes?: string[] } }>("data:changed", ({ payload }) => {
      if (active && payload.data?.scopes?.includes("proposals")) void load();
    });
    void subscription.catch(() => {});
    void load();
    return () => {
      active = false;
      latest.current += 1;
      void subscription.then((unlisten) => unlisten()).catch(() => {});
    };
  }, [load]);

  async function capture(sample = false) {
    setWorking(sample ? "sample" : "transcript");
    setCaptureError("");
    setNotice("");
    try {
      const result = sample ? await engine.meetings.sample() :
        await engine.meetings.ingest(crypto.randomUUID(), title.trim(), transcript.trim());
      await load();
      if (!sample) {
        setTitle("");
        setTranscript("");
        setCaptureOpen(false);
      }
      if (result.state.warning) setNotice(result.state.warning);
      else if (result.state.created?.length) setNotice("Tasks added by your workspace’s auto-approval policy.");
      else if (!result.proposal_ids.length) {
        setNotice(result.state.duplicates?.length ? "The commitments already have tracked tasks." : "Transcript saved. No commitments were found.");
      }
    } catch (err) {
      setCaptureError(String(err));
    } finally {
      setWorking(undefined);
    }
  }

  return (
    <section className="page">
      <header className="page-header">
        <div><h1>Review inbox</h1><p>{proposals.length ? `${proposals.length} awaiting review` : "Review meeting commitments and follow-ups"}</p></div>
        <div className="flex gap-2">
          <Button variant="outline" onClick={() => { setLoading(true); void load(); }} disabled={loading}>Refresh</Button>
          <Button onClick={() => { setCaptureError(""); setCaptureOpen(true); }} disabled={!!working || captureOpen}><Plus size={16} /> Add transcript</Button>
        </div>
      </header>
      <div className="page-body space-y-4">
        {captureOpen && (
          <form className="surface capture-form" onSubmit={(event) => { event.preventDefault(); void capture(); }}>
            <h2>Add a meeting transcript</h2>
            <div><label htmlFor="meeting-title">Meeting title</label><input className="field" id="meeting-title" required maxLength={180} value={title} onChange={(event) => setTitle(event.target.value)} /></div>
            <div><label htmlFor="transcript">Transcript</label><textarea className="field" id="transcript" required value={transcript} onChange={(event) => setTranscript(event.target.value)} placeholder="Maya: I'll send the revised launch checklist by Friday." /></div>
            <p className="text-sm text-[oklch(var(--muted-foreground))]">Use one speaker per line. When hosted models are configured, Gather sends transcript excerpts to the selected provider.</p>
            <div className="flex justify-end gap-2">
              <Button variant="ghost" onClick={() => { setCaptureOpen(false); setCaptureError(""); }} disabled={!!working}>Cancel</Button>
              <Button type="submit" disabled={!!working || !title.trim() || !transcript.trim()}>{working === "transcript" && <Spinner />} Extract commitments</Button>
            </div>
          </form>
        )}
        {captureError && <ErrorNotice>{captureError}</ErrorNotice>}
        {notice && <p className="surface px-4 py-3 text-sm" role="status">{notice}</p>}
        {error ? <ErrorNotice>{error}</ErrorNotice> : loading ? (
          <div className="flex justify-center py-20"><Spinner size={24} /></div>
        ) : proposals.length === 0 ? (
          <div className="surface">
            <EmptyState icon={<FileText size={26} />} title="Nothing to review yet"
              description="Add a meeting transcript to extract commitments, or try a sample meeting without connecting an account."
              action={<Button variant="outline" onClick={() => capture(true)} disabled={!!working || captureOpen}>{working === "sample" && <Spinner />} Try a sample meeting</Button>} />
          </div>
        ) : proposals.map((proposal) => proposal.kind === "task_approval" ? (
          <TaskApprovalCard key={proposal.id} proposal={proposal} onDone={load} onOpenMeeting={onOpenMeeting} />
        ) : (
          <NudgeApprovalCard key={proposal.id} proposal={proposal} onDone={(message) => {
            if (message) setNotice(message);
            void load();
          }} />
        ))}
      </div>
    </section>
  );
}
