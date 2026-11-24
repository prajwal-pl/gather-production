import { useEffect, useState } from "react";
import { AudioLines, Bot, FileText, Inbox, ListChecks, Moon, Plug, Settings, Sun } from "lucide-react";
import "./App.css";
import { engine, flags, type EngineStatus } from "./engine";
import { InboxView } from "./components/InboxView";
import { TasksView } from "./components/TasksView";
import { AgentView } from "./components/AgentView";
import { ConnectionsView } from "./components/ConnectionsView";
import { SettingsView } from "./components/SettingsView";
import { OnboardingView } from "./components/OnboardingView";
import { MeetingsView } from "./components/MeetingsView";
import { Button, ErrorNotice, Spinner } from "./components/ui";

const navigation = [
  { id: "inbox", label: "Review inbox", Icon: Inbox },
  { id: "tasks", label: "Tasks", Icon: ListChecks },
  { id: "meetings", label: "Meetings", Icon: FileText },
  { id: "agent", label: "Agent", Icon: Bot },
  { id: "connections", label: "Connections", Icon: Plug },
  { id: "settings", label: "Settings", Icon: Settings },
] as const;

type View = typeof navigation[number]["id"];
const isDesktop = "__TAURI_INTERNALS__" in window;

export default function App() {
  const [view, setView] = useState<View>("inbox");
  const [meetingId, setMeetingId] = useState<string>();
  const [onboarded, setOnboarded] = useState<boolean>();
  const [workspace, setWorkspace] = useState("Personal workspace");
  const [status, setStatus] = useState<EngineStatus>();
  const [failure, setFailure] = useState("");
  const [retry, setRetry] = useState(0);
  const [theme, setTheme] = useState<"light" | "dark">(() => {
    const saved = localStorage.getItem("gather-theme");
    if (saved === "light" || saved === "dark") return saved;
    return matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  });

  useEffect(() => {
    document.documentElement.classList.toggle("dark", theme === "dark");
    localStorage.setItem("gather-theme", theme);
  }, [theme]);

  useEffect(() => {
    if (!isDesktop) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;

    async function check() {
      try {
        const health = await engine.status();
        if (cancelled) return;
        setStatus(health);
        setFailure("");
        if (onboarded === undefined) {
          const [setup, saved, label] = await Promise.all([
            engine.onboarding_status(),
            flags.get("onboarded"),
            flags.get("user_workspace"),
          ]);
          if (cancelled) return;
          const legacy = localStorage.getItem("gather_onboarded") === "true";
          if (legacy && !setup.onboarded && saved !== "true") {
            const result = await engine.onboarding_complete();
            if (!result.persisted) await flags.set("onboarded", "true");
          }
          if (cancelled) return;
          setWorkspace(label || localStorage.getItem("gather_workspace") || "Personal workspace");
          setOnboarded(setup.onboarded || saved === "true" || legacy);
        }
      } catch (err) {
        const details = await engine.diagnostics().catch(() => "");
        if (cancelled) return;
        setStatus(undefined);
        setFailure(details || String(err));
      } finally {
        if (!cancelled) timer = setTimeout(check, 5000);
      }
    }

    void check();
    return () => { cancelled = true; clearTimeout(timer); };
  }, [onboarded, retry]);

  async function reconnect() {
    try {
      await engine.restart();
      setFailure("");
      setRetry((value) => value + 1);
    } catch (err) {
      setFailure(String(err));
    }
  }

  async function resetOnboarding() {
    await engine.onboarding_reset();
    await flags.delete("onboarded");
    localStorage.removeItem("gather_onboarded");
    setOnboarded(false);
    setView("inbox");
  }

  const toggleTheme = () => setTheme((value) => value === "light" ? "dark" : "light");

  if (!isDesktop || onboarded === undefined) {
    return (
      <section className="setup">
        <div className="setup-card">
          <div className="brand-mark"><AudioLines size={23} /></div>
          <h1>{!isDesktop ? "Open Gather on your desktop" : failure ? "Gather couldn’t reach the engine" : "Opening Gather"}</h1>
          {!isDesktop ? <p>From the desktop project directory, run <code>pnpm tauri dev</code> to open the app.</p> :
            failure ? <><ErrorNotice>{failure}</ErrorNotice><Button onClick={reconnect}>Retry connection</Button></> :
              <p className="flex gap-2 items-center"><Spinner /> Connecting to your workspace</p>}
        </div>
      </section>
    );
  }

  if (!onboarded) return <OnboardingView onComplete={() => {
    setWorkspace(localStorage.getItem("gather_workspace") || "Personal workspace");
    setOnboarded(true);
  }} />;

  return (
    <div className="app-shell">
      <aside className="app-sidebar">
        <div className="app-brand"><div className="brand-mark"><AudioLines size={18} /></div><span>gather</span></div>
        <div className="workspace-name" title={workspace}>{workspace}</div>
        <nav aria-label="Workspace">
          {navigation.map(({ id, label, Icon }) => (
            <button key={id} type="button" aria-current={view === id ? "page" : undefined} onClick={() => {
              setMeetingId(undefined);
              setView(id);
            }}><Icon size={18} /> <span>{label}</span></button>
          ))}
        </nav>
        <div className="sidebar-footer">
          {failure ? <button className="engine-failure" onClick={reconnect} title={failure}>Retry engine</button> :
            <span className="engine-state" role="status">{!status ? "Connecting" : status.fixture_mode ? "Local extraction" : status.state === "syncing" ? "Syncing" : "Engine online"}</span>}
          <button className="theme-toggle" onClick={toggleTheme} aria-label="Toggle color theme">{theme === "light" ? <Moon size={17} /> : <Sun size={17} />}</button>
        </div>
      </aside>
      <main className="app-main" key={retry}>
        {view === "inbox" && <InboxView onOpenMeeting={(id) => { setMeetingId(id); setView("meetings"); }} />}
        {view === "tasks" && <TasksView />}
        {view === "meetings" && <MeetingsView initialMeetingId={meetingId} />}
        {view === "agent" && <AgentView />}
        {view === "connections" && <ConnectionsView onOpenSettings={() => setView("settings")} />}
        {view === "settings" && <SettingsView theme={theme} onToggleTheme={toggleTheme} onResetOnboarding={resetOnboarding} />}
      </main>
    </div>
  );
}
