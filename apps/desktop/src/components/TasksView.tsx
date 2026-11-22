/**
 * TasksView — the local task list.
 * Filterable by status; inline status changes via dropdown.
 */
import { useEffect, useState } from "react";
import { ListChecks, Calendar, User, Quote } from "lucide-react";
import { engine, type Task, type TaskStatus } from "../engine";
import { Badge, Button, EmptyState, Spinner, formatDate, relativeDate, isOverdue } from "./ui";

const STATUS_LABELS: Record<TaskStatus, string> = {
  open: "Open",
  in_progress: "In progress",
  blocked: "Blocked",
  done: "Done",
  dismissed: "Dismissed",
};

const STATUS_BADGE: Record<TaskStatus, "default" | "success" | "warn" | "error" | "muted"> = {
  open: "default",
  in_progress: "warn",
  blocked: "error",
  done: "success",
  dismissed: "muted",
};

const FILTERS: Array<{ label: string; value: string }> = [
  { label: "Active", value: "all" },
  { label: "Open", value: "open" },
  { label: "In progress", value: "in_progress" },
  { label: "Blocked", value: "blocked" },
  { label: "Done", value: "done" },
];

function TaskRow({
  task,
  onStatusChange,
}: {
  task: Task;
  onStatusChange: (id: string, status: TaskStatus) => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const overdue = task.status === "open" && isOverdue(task.due_date);

  return (
    <div
      className={`border-b border-[oklch(var(--border)/0.5)] last:border-0 transition-colors ${
        task.status === "done" ? "opacity-55" : ""
      }`}
    >
      <div
        className="flex items-start gap-3.5 px-4 py-3 hover:bg-[oklch(var(--muted)/0.35)] cursor-pointer transition-colors"
        onClick={() => setExpanded((e) => !e)}
      >
        {/* Status select dropdown */}
        <select
          value={task.status}
          onClick={(e) => e.stopPropagation()}
          onChange={(e) => {
            e.stopPropagation();
            onStatusChange(task.id, e.target.value as TaskStatus);
          }}
          className="mt-0.5 text-[0.6875rem] font-medium border border-[oklch(var(--border))] bg-[oklch(var(--card))] text-[oklch(var(--foreground))] px-2 py-0.5 h-6 rounded-md leading-none flex-none appearance-none cursor-pointer hover:border-[oklch(var(--primary)/0.4)] transition-colors focus:outline-none"
        >
          {Object.entries(STATUS_LABELS).map(([value, label]) => (
            <option key={value} value={value}>
              {label}
            </option>
          ))}
        </select>

        {/* Main content */}
        <div className="flex-1 min-w-0">
          <p
            className={`text-[0.8125rem] font-medium leading-snug ${
              task.status === "done"
                ? "line-through text-[oklch(var(--muted-foreground))]"
                : "text-[oklch(var(--foreground))]"
            }`}
          >
            {task.title}
          </p>
          <div className="flex items-center gap-3 mt-1.5 flex-wrap">
            {task.owner_email && (
              <span className="flex items-center gap-1 text-[0.75rem] text-[oklch(var(--muted-foreground))]">
                <User size={11} />
                {task.owner_email}
              </span>
            )}
            {task.due_date && (
              <span
                className={`flex items-center gap-1 text-[0.75rem] ${
                  overdue
                    ? "text-[oklch(var(--destructive))] font-semibold"
                    : "text-[oklch(var(--muted-foreground))]"
                }`}
              >
                <Calendar size={11} />
                {formatDate(task.due_date)}
                <span className="opacity-60">({relativeDate(task.due_date)})</span>
              </span>
            )}
            <Badge variant={STATUS_BADGE[task.status]}>{STATUS_LABELS[task.status]}</Badge>
          </div>
        </div>
      </div>

      {/* Expanded detail */}
      {expanded && (task.detail || task.source_quote) && (
        <div className="px-4 pb-3.5 ml-10 space-y-2 animate-fade-in">
          {task.detail && (
            <p className="text-[0.75rem] text-[oklch(var(--muted-foreground))] leading-relaxed">
              {task.detail}
            </p>
          )}
          {task.source_quote && (
            <div className="flex gap-2 p-2.5 rounded-lg bg-[oklch(var(--muted)/0.3)] border border-[oklch(var(--border)/0.5)]">
              <Quote size={12} className="flex-none mt-0.5 text-[oklch(var(--accent))]" />
              <blockquote className="text-[0.75rem] text-[oklch(var(--muted-foreground))] italic leading-relaxed">
                "{task.source_quote}"
              </blockquote>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export function TasksView() {
  const [tasks, setTasks] = useState<Task[]>([]);
  const [filter, setFilter] = useState("all");
  const [loading, setLoading] = useState(true);

  async function load(status = filter) {
    setLoading(true);
    try {
      const { tasks } = await engine.tasks.list(status);
      setTasks(tasks);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load(filter);
  }, [filter]);

  async function handleStatusChange(id: string, status: TaskStatus) {
    await engine.tasks.update(id, status);
    setTasks((ts) => ts.map((t) => (t.id === id ? { ...t, status } : t)));
  }

  const open = tasks.filter((t) => t.status === "open").length;
  const overdue = tasks.filter(
    (t) => t.status === "open" && isOverdue(t.due_date)
  ).length;

  return (
    <div className="flex flex-col h-full bg-[oklch(var(--background))]">
      {/* Toolbar */}
      <div className="flex items-center justify-between px-6 py-4 border-b border-[oklch(var(--border))]">
        <div>
          <h2 className="text-sm font-semibold text-[oklch(var(--foreground))] tracking-tight">
            Tracked Tasks
          </h2>
          {!loading && (
            <p className="text-xs text-[oklch(var(--muted-foreground))] mt-0.5">
              {open} open{overdue > 0 ? ` · ${overdue} overdue` : ""}
            </p>
          )}
        </div>
        <Button variant="outline" onClick={() => load(filter)} disabled={loading} className="text-xs h-8">
          {loading ? <Spinner size={12} /> : null}
          Refresh
        </Button>
      </div>

      {/* Filter tabs */}
      <div className="flex border-b border-[oklch(var(--border))] px-6 gap-1 bg-[oklch(var(--card))]">
        {FILTERS.map((f) => (
          <button
            key={f.value}
            onClick={() => setFilter(f.value)}
            className={`px-3 py-2.5 text-xs font-medium border-b-2 transition-all ${
              filter === f.value
                ? "border-[oklch(var(--primary))] text-[oklch(var(--foreground))] font-semibold"
                : "border-transparent text-[oklch(var(--muted-foreground))] hover:text-[oklch(var(--foreground))]"
            }`}
          >
            {f.label}
          </button>
        ))}
      </div>

      {/* Task list container */}
      <div className="flex-1 overflow-y-auto p-6">
        {loading ? (
          <div className="flex justify-center py-20">
            <Spinner size={24} />
          </div>
        ) : tasks.length === 0 ? (
          <EmptyState
            icon={<ListChecks size={36} />}
            title="No tasks match filter"
            description="Approve extracted commitments from the Inbox to track follow-up tasks."
          />
        ) : (
          <div className="border border-[oklch(var(--border))] bg-[oklch(var(--card))] rounded-xl shadow-xs overflow-hidden divide-y divide-[oklch(var(--border)/0.5)]">
            {tasks.map((task) => (
              <TaskRow key={task.id} task={task} onStatusChange={handleStatusChange} />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
