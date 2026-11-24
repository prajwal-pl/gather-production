/**
 * Shared primitive UI components used across all views.
 * Plain HTML + Tailwind with clean OKLCH design tokens.
 */
import React from "react";

// ─── Badge ───────────────────────────────────────────────────────────────────

export type BadgeVariant = "default" | "success" | "warn" | "error" | "muted" | "accent";

const BADGE_STYLES: Record<BadgeVariant, string> = {
  default: "bg-[oklch(var(--primary)/0.08)] text-[oklch(var(--primary))] border-[oklch(var(--primary)/0.18)]",
  success: "bg-[oklch(var(--success)/0.12)] text-[oklch(var(--success))] border-[oklch(var(--success)/0.25)]",
  warn:    "bg-[oklch(0.72_0.18_80/0.14)] text-[oklch(0.55_0.18_80)] border-[oklch(0.72_0.18_80/0.25)]",
  error:   "bg-[oklch(var(--destructive)/0.12)] text-[oklch(var(--destructive))] border-[oklch(var(--destructive)/0.25)]",
  muted:   "bg-[oklch(var(--muted)/0.7)] text-[oklch(var(--muted-foreground))] border-[oklch(var(--border)/0.5)]",
  accent:  "bg-[oklch(var(--accent)/0.12)] text-[oklch(var(--accent))] border-[oklch(var(--accent)/0.25)]",
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
      className={`inline-flex items-center gap-1 px-2.5 py-0.5 text-[0.6875rem] font-medium tracking-wide rounded-full border transition-colors ${BADGE_STYLES[variant]} ${className}`}
    >
      {children}
    </span>
  );
}

// ─── Button ──────────────────────────────────────────────────────────────────

export type ButtonVariant = "primary" | "ghost" | "outline" | "danger" | "accent";

const BTN_BASE =
  "inline-flex items-center justify-center gap-1.5 h-8 px-3.5 text-[0.8125rem] font-medium rounded-lg transition-all duration-150 active:scale-[0.98] disabled:opacity-40 disabled:cursor-not-allowed disabled:active:scale-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[oklch(var(--primary))] shadow-sm";

const BTN_VARIANT: Record<ButtonVariant, string> = {
  primary:
    "bg-[oklch(var(--primary))] text-[oklch(var(--primary-foreground))] hover:bg-[oklch(var(--primary)/0.88)] shadow-[oklch(var(--primary)/0.15)]",
  ghost:
    "bg-transparent hover:bg-[oklch(var(--muted)/0.8)] text-[oklch(var(--foreground))] shadow-none",
  outline:
    "border border-[oklch(var(--border))] bg-[oklch(var(--card))] hover:bg-[oklch(var(--muted)/0.6)] text-[oklch(var(--foreground))]",
  danger:
    "bg-[oklch(var(--destructive)/0.1)] text-[oklch(var(--destructive))] hover:bg-[oklch(var(--destructive)/0.18)] border border-[oklch(var(--destructive)/0.25)] shadow-none",
  accent:
    "bg-[oklch(var(--accent))] text-[oklch(var(--accent-foreground))] hover:bg-[oklch(var(--accent)/0.88)]",
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

export function Spinner({ size = 16, className = "" }: { size?: number; className?: string }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      className={`animate-spin text-current ${className}`}
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
  action,
}: {
  icon: React.ReactNode;
  title: string;
  description?: string;
  action?: React.ReactNode;
}) {
  return (
    <div className="flex flex-col items-center justify-center gap-3 py-16 px-4 text-center animate-fade-in">
      <div className="text-[oklch(var(--muted-foreground))] opacity-50 p-3 rounded-2xl bg-[oklch(var(--muted)/0.5)] border border-[oklch(var(--border)/0.5)]">
        {icon}
      </div>
      <p className="text-[0.9375rem] font-medium text-[oklch(var(--foreground))]">{title}</p>
      {description && (
        <p className="text-[0.8125rem] text-[oklch(var(--muted-foreground))] max-w-sm leading-relaxed">
          {description}
        </p>
      )}
      {action && <div className="mt-2">{action}</div>}
    </div>
  );
}

// ─── Divider ─────────────────────────────────────────────────────────────────

export function Divider({ className = "" }: { className?: string }) {
  return <hr className={`border-[oklch(var(--border)/0.5)] my-2 ${className}`} />;
}

// ─── Helpers ─────────────────────────────────────────────────────────────────

export function formatDate(iso?: string | null): string {
  if (!iso) return "—";
  try {
    const d = new Date(iso.includes("T") ? iso : iso + "T00:00:00");
    return d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
  } catch {
    return iso;
  }
}

export function relativeDate(iso?: string | null): string {
  if (!iso) return "";
  try {
    const d = new Date(iso.includes("T") ? iso : iso + "T00:00:00");
    const now = new Date();
    now.setHours(0, 0, 0, 0);
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
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  return new Date(due_date + "T00:00:00") < today;
}

export function ErrorNotice({ children }: { children: React.ReactNode }) {
  return <p role="alert" className="error-notice">{children}</p>;
}
