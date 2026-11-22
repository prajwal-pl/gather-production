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
      className={`border-b border-[oklch(var(--border)/0.2)] last:border-0 ${
        task.status === "done" ? "opacity-50" : ""
      }`}
    >
      <div
        className="flex items-start gap-3 px-4 py-3 hover:bg-[oklch(var(--muted)/0.3)] cursor-pointer transition-colors"
        onClick={() => setExpanded((e) => !e)}
      >
        {/* Status select */}
        <select
          value={task.status}
          onClick={(e) => e.stopPropagation()}
          onChange={(e) => {
            e.stopPropagation();
            onStatusChange(task.id, e.target.value as TaskStatus);
          }}
          className="mt-0.5 text-[0.6875rem] border border-[oklch(var(--border)/0.3)] bg-[oklch(var(--card))] text-[oklch(var(--foreground))] px-1.5 py-0.5 h-5 leading-none flex-none appearance-none cursor-pointer"
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
          <div className="flex items-center gap-3 mt-1 flex-wrap">
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
                    ? "text-[oklch(var(--destructive))] font-medium"
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
        <div className="px-4 pb-3 ml-[calc(2rem+0.75rem)] space-y-2">
          {task.detail && (
            <p className="text-[0.75rem] text-[oklch(var(--muted-foreground))] leading-relaxed">
              {task.detail}
            </p>
          )}
          {task.source_quote && (
            <div className="flex gap-2">
              <Quote size={12} className="flex-none mt-0.5 text-[oklch(var(--muted-foreground))] opacity-50" />
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
    // Optimistic update
    setTasks((ts) => ts.map((t) => (t.id === id ? { ...t, status } : t)));
  }

  const open = tasks.filter((t) => t.status === "open").length;
  const overdue = tasks.filter(
    (t) => t.status === "open" && isOverdue(t.due_date)
  ).length;

  return (
    <div className="flex flex-col h-full">
      {/* Toolbar */}
      <div className="flex items-center justify-between px-6 py-3 border-b border-[oklch(var(--border)/0.2)]">
        <div>
          <h2 className="text-[0.9375rem] font-semibold text-[oklch(var(--foreground))]">
            Tasks
          </h2>
          {!loading && (
            <p className="text-[0.75rem] text-[oklch(var(--muted-foreground))] mt-0.5">
              {open} open{overdue > 0 ? ` · ${overdue} overdue` : ""}
            </p>
          )}
        </div>
        <Button variant="ghost" onClick={() => load(filter)} disabled={loading}>
          {loading ? <Spinner size={13} /> : null}
          Refresh
        </Button>
      </div>

      {/* Filter tabs */}
      <div className="flex border-b border-[oklch(var(--border)/0.2)] px-6">
        {FILTERS.map((f) => (
          <button
            key={f.value}
            onClick={() => setFilter(f.value)}
            className={`px-3 py-2 text-[0.75rem] font-medium border-b-2 transition-colors ${
              filter === f.value
                ? "border-[oklch(var(--primary))] text-[oklch(var(--foreground))]"
                : "border-transparent text-[oklch(var(--muted-foreground))] hover:text-[oklch(var(--foreground))]"
            }`}
          >
            {f.label}
          </button>
        ))}
      </div>

      {/* Task list */}
      <div className="flex-1 overflow-y-auto">
        {loading ? (
          <div className="flex justify-center py-16">
            <Spinner size={24} />
          </div>
        ) : tasks.length === 0 ? (
          <EmptyState
            icon={<ListChecks size={48} />}
            title="No tasks"
            description="Approve commitments from the inbox to populate this list."
          />
        ) : (
          <div>
            {tasks.map((task) => (
              <TaskRow key={task.id} task={task} onStatusChange={handleStatusChange} />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
