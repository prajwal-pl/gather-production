/**
 * App.tsx — root layout: left sidebar nav + main content area.
 *
 * Nav items: Inbox (approval queue) · Tasks · Connections · Settings
 * Top-right: engine status pill (fixture / running / stopped).
 */
import React, { useEffect, useState } from "react";
import {
  Inbox,
  ListChecks,
  Plug,
  Settings,
  Circle,
} from "lucide-react";
import "./App.css";
import { engine, type EngineStatus } from "./engine";
import { InboxView } from "./components/InboxView";
import { TasksView } from "./components/TasksView";
import { ConnectionsView } from "./components/ConnectionsView";
import { SettingsView } from "./components/SettingsView";
import { Spinner } from "./components/ui";

// ─── Types ───────────────────────────────────────────────────────────────────

type View = "inbox" | "tasks" | "connections" | "settings";

interface NavItem {
  id: View;
  label: string;
  icon: React.ReactNode;
}

const NAV: NavItem[] = [
  { id: "inbox", label: "Inbox", icon: <Inbox size={15} /> },
  { id: "tasks", label: "Tasks", icon: <ListChecks size={15} /> },
  { id: "connections", label: "Connections", icon: <Plug size={15} /> },
  { id: "settings", label: "Settings", icon: <Settings size={15} /> },
];

// ─── Engine status pill ───────────────────────────────────────────────────────

function EngineStatusPill() {
  const [status, setStatus] = useState<EngineStatus | null>(null);
  const [loading, setLoading] = useState(true);

  async function poll() {
    try {
      const s = await engine.status();
      setStatus(s);
    } catch {
      setStatus(null);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    poll();
    const id = setInterval(poll, 15000);
    return () => clearInterval(id);
  }, []);

  if (loading) {
    return (
      <div className="flex items-center gap-1.5 text-[0.6875rem] text-[oklch(var(--muted-foreground))]">
        <Spinner size={11} />
        engine
      </div>
    );
  }

  if (!status) {
    return (
      <div className="flex items-center gap-1.5 text-[0.6875rem] text-[oklch(var(--destructive))]">
        <Circle size={8} className="fill-current" />
        stopped
      </div>
    );
  }

  return (
    <div className="flex items-center gap-1.5 text-[0.6875rem] text-[oklch(var(--muted-foreground))]">
      <Circle
        size={8}
        className={`fill-current ${
          status.fixture_mode
            ? "text-[oklch(0.72_0.18_80)]"
            : "text-[oklch(var(--success))]"
        }`}
      />
      <span>
        {status.fixture_mode ? "fixture" : "live"} · v{status.version}
      </span>
    </div>
  );
}

// ─── Sidebar ──────────────────────────────────────────────────────────────────

function Sidebar({
  active,
  onNav,
}: {
  active: View;
  onNav: (v: View) => void;
}) {
  return (
    <aside className="w-[180px] flex-none flex flex-col border-r border-[oklch(var(--border)/0.3)] bg-[oklch(var(--card))] h-full">
      {/* Logo */}
      <div className="px-4 py-4 border-b border-[oklch(var(--border)/0.2)]">
        <div className="flex items-center gap-2">
          <div className="w-5 h-5 bg-[oklch(var(--primary))] flex items-center justify-center">
            <span className="text-[0.625rem] font-bold text-[oklch(var(--primary-foreground))]">
              g
            </span>
          </div>
          <span className="text-[0.875rem] font-semibold text-[oklch(var(--foreground))] tracking-tight">
            gather
          </span>
        </div>
      </div>

      {/* Nav */}
      <nav className="flex-1 py-2">
        {NAV.map((item) => (
          <button
            key={item.id}
            onClick={() => onNav(item.id)}
            className={`w-full flex items-center gap-2.5 px-4 py-2 text-[0.8125rem] text-left transition-colors ${
              active === item.id
                ? "bg-[oklch(var(--primary)/0.08)] text-[oklch(var(--foreground))] font-medium border-r-2 border-[oklch(var(--primary))]"
                : "text-[oklch(var(--muted-foreground))] hover:bg-[oklch(var(--muted)/0.5)] hover:text-[oklch(var(--foreground))]"
            }`}
          >
            <span
              className={
                active === item.id ? "text-[oklch(var(--foreground))]" : ""
              }
            >
              {item.icon}
            </span>
            {item.label}
          </button>
        ))}
      </nav>

      {/* Engine status */}
      <div className="px-4 py-3 border-t border-[oklch(var(--border)/0.2)]">
        <EngineStatusPill />
      </div>
    </aside>
  );
}

// ─── App ─────────────────────────────────────────────────────────────────────

export default function App() {
  const [view, setView] = useState<View>("inbox");

  const content: Record<View, React.ReactNode> = {
    inbox: <InboxView />,
    tasks: <TasksView />,
    connections: <ConnectionsView />,
    settings: <SettingsView />,
  };

  return (
    <div className="flex h-screen w-screen overflow-hidden bg-[oklch(var(--background))] text-[oklch(var(--foreground))]">
      <Sidebar active={view} onNav={setView} />
      <main className="flex-1 overflow-hidden">{content[view]}</main>
    </div>
  );
}
