/**
 * App.tsx — root layout: left sidebar nav + main content area.
 *
 * Nav items: Inbox (approval queue) · Tasks · Connections · Settings
 * Top-right: engine status pill (fixture / running / stopped).
 * Bottom: theme toggle + user profile.
 */
import React, { useEffect, useState } from "react";
import {
  Inbox,
  ListChecks,
  Bot,
  Plug,
  Settings,
  Circle,
  Sun,
  Moon,
} from "lucide-react";
import "./App.css";
import { engine, flags, withTimeout, type EngineStatus } from "./engine";
import { InboxView } from "./components/InboxView";
import { TasksView } from "./components/TasksView";
import { AgentView } from "./components/AgentView";
import { ConnectionsView } from "./components/ConnectionsView";
import { SettingsView } from "./components/SettingsView";
import { OnboardingView } from "./components/OnboardingView";
import { Spinner } from "./components/ui";

// ─── Types ───────────────────────────────────────────────────────────────────

type View =
  | "inbox"
  | "tasks"
  | "agent"
  | "connections"
  | "settings"
  | "onboarding";

interface NavItem {
  id: View;
  label: string;
  icon: React.ReactNode;
}

const NAV: NavItem[] = [
  { id: "inbox", label: "Inbox", icon: <Inbox size={15} /> },
  { id: "tasks", label: "Tasks", icon: <ListChecks size={15} /> },
  { id: "agent", label: "Agent", icon: <Bot size={15} /> },
  { id: "connections", label: "Connections", icon: <Plug size={15} /> },
  { id: "settings", label: "Settings", icon: <Settings size={15} /> },
];

// ─── Onboarding persistence helpers ──────────────────────────────────────────

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/**
 * Older builds wrote completion only to localStorage (they never told the
 * engine). Recognise that profile so an existing install is not asked to
 * onboard again, then migrate it to the durable stores.
 */
function legacyProfile(): { name: string; email: string; workspace: string } | null {
  try {
    const email = localStorage.getItem("gather_user_email") || "";
    const workspace = localStorage.getItem("gather_workspace") || "";
    const completed = localStorage.getItem("gather_onboarded") === "true";
    if (!completed && !email && !workspace) return null;
    return {
      name: localStorage.getItem("gather_user_name") || "Operator",
      email: email || "user@local",
      workspace,
    };
  } catch {
    return null;
  }
}

function clearOnboardingLocalStorage(): void {
  try {
    for (const key of [
      "gather_onboarded",
      "gather_user_name",
      "gather_user_email",
      "gather_workspace",
      "gather_connected_apps",
    ]) {
      localStorage.removeItem(key);
    }
  } catch {
    /* localStorage can be unavailable in hardened webviews */
  }
}

// ─── Engine status pill ───────────────────────────────────────────────────────

function EngineStatusPill() {
  const [status, setStatus] = useState<EngineStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [failure, setFailure] = useState<string | null>(null);

  async function poll() {
    try {
      const s = await engine.status();
      setStatus(s);
      setFailure(null);
    } catch (err) {
      setStatus(null);
      const detail = await engine.diagnostics().catch(() => "");
      setFailure(detail || String(err));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    poll();
    const id = setInterval(poll, 5000);
    return () => clearInterval(id);
  }, []);

  if (loading) {
    return (
      <div className="flex items-center gap-1.5 text-[0.6875rem] text-[oklch(var(--muted-foreground))]">
        <Spinner size={10} />
        engine
      </div>
    );
  }

  async function restart() {
    setLoading(true);
    setFailure(null);
    try {
      await engine.restart();
    } catch (err) {
      setFailure(String(err));
    }
    await poll();
  }

  if (!status) {
    const lastLine = (failure || "").split("\n").filter(Boolean).slice(-1)[0] || "";
    const detail = lastLine.length > 40 ? `${lastLine.slice(0, 40)}…` : lastLine;
    return (
      <div
        onClick={restart}
        className="flex items-center gap-1.5 text-[0.6875rem] text-[oklch(var(--destructive))] cursor-pointer hover:opacity-80 transition-opacity"
        title={`${failure || "engine is not running"}\n\nClick to restart`}
      >
        <Circle size={7} className="fill-current" />
        stopped{detail ? ` · ${detail}` : ""}
      </div>
    );
  }

  return (
    <div className="flex items-center gap-1.5 text-[0.6875rem] text-[oklch(var(--muted-foreground))]">
      <Circle
        size={7}
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
  theme,
  onToggleTheme,
}: {
  active: View;
  onNav: (v: View) => void;
  theme: "light" | "dark";
  onToggleTheme: () => void;
}) {
  const [userName, setUserName] = React.useState(localStorage.getItem("gather_user_name") || "Operator");
  const [userWorkspace, setUserWorkspace] = React.useState(localStorage.getItem("gather_workspace") || "Local Vault");

  React.useEffect(() => {
    flags.get("user_name").then((v) => { if (v) setUserName(v); }).catch(() => {});
    flags.get("user_workspace").then((v) => { if (v) setUserWorkspace(v); }).catch(() => {});
  }, []);

  return (
    <aside className="w-[200px] flex-none flex flex-col border-r border-[oklch(var(--border))] bg-[oklch(var(--card))] h-full transition-colors duration-200">
      {/* Brand Header */}
      <div className="px-4 py-4 border-b border-[oklch(var(--border))] flex items-center justify-between">
        <div className="flex items-center gap-2">
          <div className="w-6 h-6 bg-[oklch(var(--primary))] text-[oklch(var(--primary-foreground))] rounded-md flex items-center justify-center font-bold text-xs shadow-xs">
            g
          </div>
          <span className="text-[0.875rem] font-semibold text-[oklch(var(--foreground))] tracking-tight">
            gather
          </span>
        </div>

        {/* Theme Toggle Button in Header */}
        <button
          onClick={onToggleTheme}
          className="w-7 h-7 rounded-md flex items-center justify-center text-[oklch(var(--muted-foreground))] hover:text-[oklch(var(--foreground))] hover:bg-[oklch(var(--muted)/0.7)] transition-all"
          title={`Switch to ${theme === "light" ? "dark" : "light"} mode`}
          aria-label="Toggle theme"
        >
          {theme === "light" ? <Moon size={14} /> : <Sun size={14} />}
        </button>
      </div>

      {/* Main Navigation */}
      <nav className="flex-1 py-3 px-2 space-y-1">
        {NAV.map((item) => {
          const isActive = active === item.id;
          return (
            <button
              key={item.id}
              onClick={() => onNav(item.id)}
              className={`w-full flex items-center gap-2.5 px-3 py-2 rounded-lg text-[0.8125rem] text-left transition-all ${
                isActive
                  ? "bg-[oklch(var(--primary))] text-[oklch(var(--primary-foreground))] font-medium shadow-xs"
                  : "text-[oklch(var(--muted-foreground))] hover:bg-[oklch(var(--muted)/0.6)] hover:text-[oklch(var(--foreground))]"
              }`}
            >
              <span className={isActive ? "text-[oklch(var(--primary-foreground))]" : ""}>
                {item.icon}
              </span>
              {item.label}
            </button>
          );
        })}
      </nav>

      {/* Footer Profile & Engine Status */}
      <div className="p-3 border-t border-[oklch(var(--border))] space-y-2.5">
        {/* User Mini Profile */}
        <div
          onClick={() => onNav("settings")}
          className="flex items-center gap-2.5 p-1.5 rounded-lg hover:bg-[oklch(var(--muted)/0.6)] cursor-pointer transition-colors"
          title="Open Settings"
        >
          <div className="w-7 h-7 rounded-full bg-[oklch(var(--accent)/0.15)] text-[oklch(var(--accent))] flex items-center justify-center text-xs font-semibold flex-none border border-[oklch(var(--accent)/0.25)]">
            {userName.slice(0, 2).toUpperCase()}
          </div>
          <div className="flex-1 min-w-0">
            <p className="text-xs font-medium text-[oklch(var(--foreground))] truncate leading-tight">
              {userName}
            </p>
            <p className="text-[0.6875rem] text-[oklch(var(--muted-foreground))] truncate">
              {userWorkspace}
            </p>
          </div>
        </div>

        {/* Engine status pill */}
        <div className="px-1.5">
          <EngineStatusPill />
        </div>
      </div>
    </aside>
  );
}

// ─── App ─────────────────────────────────────────────────────────────────────

export default function App() {
  const [view, setView] = useState<View>("onboarding");
  const [loading, setLoading] = useState(true);

  // Theme management: persisted in localStorage with system preference fallback
  const [theme, setTheme] = useState<"light" | "dark">(() => {
    const saved = localStorage.getItem("gather-theme");
    if (saved === "light" || saved === "dark") return saved;
    return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  });

  // Apply theme class to document
  useEffect(() => {
    const root = document.documentElement;
    if (theme === "dark") {
      root.classList.add("dark");
    } else {
      root.classList.remove("dark");
    }
    localStorage.setItem("gather-theme", theme);
  }, [theme]);

  const toggleTheme = () => {
    setTheme((prev) => (prev === "light" ? "dark" : "light"));
  };

  // Poll engine status + onboarding state
  useEffect(() => {
    let cancelled = false;

    async function checkState() {
      // 1. Fast path: the file-backed flag survives WebView cache clears and
      // dev-server restarts. localStorage alone does not.
      const flagValue = await withTimeout(flags.get("onboarded"), 1500).catch(() => null);
      if (flagValue === "true") {
        if (!cancelled) {
          setView("inbox");
          setLoading(false);
        }
        return;
      }

      // 2. Migrate an install completed by an older build (localStorage only).
      const legacy = legacyProfile();
      if (legacy) {
        await Promise.allSettled([
          withTimeout(flags.set("onboarded", "true"), 1500),
          withTimeout(flags.set("user_name", legacy.name), 1500),
          withTimeout(flags.set("user_email", legacy.email), 1500),
          withTimeout(flags.set("user_workspace", legacy.workspace), 1500),
        ]);
        try {
          localStorage.setItem("gather_onboarded", "true");
        } catch {
          /* best effort legacy marker */
        }
        engine
          .onboarding_complete({ org_name: legacy.workspace || undefined })
          .catch(() => {});
        if (!cancelled) {
          setView("inbox");
          setLoading(false);
        }
        return;
      }

      // 3. Ask the engine. It may still be booting, so retry briefly instead
      // of mis-reporting an onboarded user as new. Each attempt is bounded so
      // a wedged engine cannot leave the app on the splash screen forever.
      for (let attempt = 0; attempt < 6; attempt += 1) {
        if (cancelled) return;
        try {
          const ob = await withTimeout(engine.onboarding_status(), 1200);
          if (ob.onboarded) {
            await withTimeout(flags.set("onboarded", "true"), 1500).catch(() => {});
            if (!cancelled) setView("inbox");
          } else {
            if (!cancelled) setView("onboarding");
          }
          if (!cancelled) setLoading(false);
          return;
        } catch {
          await sleep(250 + attempt * 150);
        }
      }

      // Engine never answered: show onboarding, but keep the flag check above
      // as the primary path for existing users.
      if (!cancelled) {
        setView("onboarding");
        setLoading(false);
      }
    }

    checkState();
    return () => {
      cancelled = true;
    };
  }, []);

  if (loading) {
    return (
      <div className="flex h-screen w-screen overflow-hidden bg-[oklch(var(--background))] text-[oklch(var(--foreground))]">
        <div className="flex flex-col items-center justify-center h-full w-full gap-3">
          <Spinner size={28} />
          <span className="text-xs text-[oklch(var(--muted-foreground))] font-medium tracking-wide">
            Initializing gather vault...
          </span>
        </div>
      </div>
    );
  }

  // Full-screen Onboarding View when active
  if (view === "onboarding") {
    return (
      <div className="flex h-screen w-screen overflow-hidden bg-[oklch(var(--background))] text-[oklch(var(--foreground))] relative">
        {/* Floating Theme Toggle in Onboarding */}
        <div className="absolute top-6 right-8 z-50">
          <button
            onClick={toggleTheme}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-medium bg-[oklch(var(--card))] border border-[oklch(var(--border))] text-[oklch(var(--foreground))] hover:bg-[oklch(var(--muted)/0.8)] shadow-xs transition-all"
            title="Toggle color theme"
          >
            {theme === "light" ? <Moon size={13} /> : <Sun size={13} />}
            <span className="capitalize">{theme}</span>
          </button>
        </div>

        <main className="flex-1 overflow-y-auto bg-[oklch(var(--background))] text-[oklch(var(--foreground))]">
          <OnboardingView onComplete={() => setView("inbox")} />
        </main>
      </div>
    );
  }

  const resetOnboarding = async () => {
    // Clear every place completion could be cached, including the engine
    // marker, otherwise the next launch would skip onboarding again.
    clearOnboardingLocalStorage();
    await Promise.allSettled([
      withTimeout(flags.delete("onboarded"), 2000),
      withTimeout(engine.onboarding_reset(), 2000),
    ]);
    setView("onboarding");
  };

  const content: Record<View, React.ReactNode> = {
    inbox: <InboxView />,
    tasks: <TasksView />,
    agent: <AgentView />,
    connections: <ConnectionsView />,
    settings: (
      <SettingsView
        theme={theme}
        onToggleTheme={toggleTheme}
        onResetOnboarding={resetOnboarding}
      />
    ),
    onboarding: <OnboardingView onComplete={() => setView("inbox")} />,
  };

  return (
    <div className="flex h-screen w-screen overflow-hidden bg-[oklch(var(--background))] text-[oklch(var(--foreground))] font-sans antialiased">
      <Sidebar active={view} onNav={setView} theme={theme} onToggleTheme={toggleTheme} />
      <main className="flex-1 overflow-hidden bg-[oklch(var(--background))]">
        {content[view]}
      </main>
    </div>
  );
}
