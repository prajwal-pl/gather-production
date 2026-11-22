/**
 * Shared primitive UI components used across all views.
 * Plain HTML + Tailwind — no external component library dependency.
 */
import React from "react";

// ─── Badge ───────────────────────────────────────────────────────────────────

type BadgeVariant = "default" | "success" | "warn" | "error" | "muted";

const BADGE_STYLES: Record<BadgeVariant, string> = {
  default: "bg-[oklch(var(--primary)/0.1)] text-[oklch(var(--primary))]",
  success: "bg-[oklch(var(--success)/0.12)] text-[oklch(var(--success))]",
  warn:    "bg-[oklch(0.72_0.18_80/0.15)] text-[oklch(0.55_0.18_80)]",
  error:   "bg-[oklch(var(--destructive)/0.12)] text-[oklch(var(--destructive))]",
  muted:   "bg-[oklch(var(--muted))] text-[oklch(var(--muted-foreground))]",
};

export function Badge({
  variant = "default",
  children,
  className = "",
}: {
  variant?: BadgeVariant;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <span
      className={`inline-flex items-center px-2 py-0.5 text-[0.6875rem] font-medium uppercase tracking-wide leading-none ${BADGE_STYLES[variant]} ${className}`}
    >
      {children}
    </span>
  );
}

// ─── Button ──────────────────────────────────────────────────────────────────

type ButtonVariant = "primary" | "ghost" | "outline" | "danger";

const BTN_BASE =
  "inline-flex items-center justify-center gap-1.5 h-8 px-3 text-[0.8125rem] font-medium transition-colors disabled:opacity-40 disabled:cursor-not-allowed focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[oklch(var(--primary))]";

const BTN_VARIANT: Record<ButtonVariant, string> = {
  primary:
    "bg-[oklch(var(--primary))] text-[oklch(var(--primary-foreground))] hover:bg-[oklch(var(--primary)/0.85)]",
  ghost:
    "bg-transparent hover:bg-[oklch(var(--muted))] text-[oklch(var(--foreground))]",
  outline:
    "border border-[oklch(var(--border)/0.4)] bg-transparent hover:bg-[oklch(var(--muted))] text-[oklch(var(--foreground))]",
  danger:
    "bg-[oklch(var(--destructive)/0.1)] text-[oklch(var(--destructive))] hover:bg-[oklch(var(--destructive)/0.2)] border border-[oklch(var(--destructive)/0.3)]",
};

export function Button({
  variant = "primary",
  children,
  className = "",
  disabled,
  onClick,
  type = "button",
}: {
  variant?: ButtonVariant;
  children: React.ReactNode;
  className?: string;
  disabled?: boolean;
  onClick?: () => void;
  type?: "button" | "submit";
}) {
  return (
    <button
      type={type}
      disabled={disabled}
      onClick={onClick}
      className={`${BTN_BASE} ${BTN_VARIANT[variant]} ${className}`}
    >
      {children}
    </button>
  );
}

// ─── Spinner ─────────────────────────────────────────────────────────────────

export function Spinner({ size = 16 }: { size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      className="animate-spin text-[oklch(var(--muted-foreground))]"
    >
      <circle cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="3" strokeOpacity="0.25" />
      <path
        d="M12 2a10 10 0 0 1 10 10"
        stroke="currentColor"
        strokeWidth="3"
        strokeLinecap="round"
      />
    </svg>
  );
}

// ─── Empty state ─────────────────────────────────────────────────────────────

export function EmptyState({
  icon,
  title,
  description,
}: {
  icon: React.ReactNode;
  title: string;
  description?: string;
}) {
  return (
    <div className="flex flex-col items-center justify-center gap-3 py-16 text-center">
      <div className="text-[oklch(var(--muted-foreground))] opacity-40">{icon}</div>
      <p className="text-[0.875rem] font-medium text-[oklch(var(--foreground))]">{title}</p>
      {description && (
        <p className="text-[0.75rem] text-[oklch(var(--muted-foreground))] max-w-xs">
          {description}
        </p>
      )}
    </div>
  );
}

// ─── Divider ─────────────────────────────────────────────────────────────────

export function Divider() {
  return <hr className="border-[oklch(var(--border)/0.3)]" />;
}

// ─── Helpers ─────────────────────────────────────────────────────────────────

export function formatDate(iso?: string | null): string {
  if (!iso) return "—";
  try {
    const d = new Date(iso.includes("T") ? iso : iso + "T00:00:00Z");
    return d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
  } catch {
    return iso;
  }
}

export function relativeDate(iso?: string | null): string {
  if (!iso) return "";
  try {
    const d = new Date(iso.includes("T") ? iso : iso + "T00:00:00Z");
    const now = new Date();
    const diffMs = d.getTime() - now.getTime();
    const diffDays = Math.round(diffMs / 86400000);
    if (diffDays === 0) return "Today";
    if (diffDays === 1) return "Tomorrow";
    if (diffDays === -1) return "Yesterday";
    if (diffDays < 0) return `${Math.abs(diffDays)}d overdue`;
    return `in ${diffDays}d`;
  } catch {
    return "";
  }
}

export function isOverdue(due_date?: string | null): boolean {
  if (!due_date) return false;
  const d = new Date(due_date + "T00:00:00Z");
  return d < new Date();
}
