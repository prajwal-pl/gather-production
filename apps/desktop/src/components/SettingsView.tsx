/**
 * SettingsView — API keys, model chain, theme preference, engine diagnostics.
 * Keys are write-only (secrets.set IPC); presence booleans only are shown.
 */
import { useEffect, useState } from "react";
import {
  Moon,
  Sun,
  User,
  RotateCcw,
  ShieldCheck,
  Cpu,
} from "lucide-react";
import { Button } from "./ui";
import { flags } from "../engine";

export function SettingsView({
  theme,
  onToggleTheme,
  onResetOnboarding,
}: {
  theme?: "light" | "dark";
  onToggleTheme?: () => void;
  onResetOnboarding?: () => void;
}) {
  // Profile lives in the file-backed flag store; localStorage is only a cache.
  const [userName, setUserName] = useState(
    () => localStorage.getItem("gather_user_name") || "Operator",
  );
  const [userEmail, setUserEmail] = useState(
    () => localStorage.getItem("gather_user_email") || "local@gather.vault",
  );
  const [userWorkspace, setUserWorkspace] = useState(
    () => localStorage.getItem("gather_workspace") || "Local Vault",
  );

  useEffect(() => {
    let active = true;
    Promise.allSettled([
      flags.get("user_name"),
      flags.get("user_email"),
      flags.get("user_workspace"),
    ]).then(([name, email, workspace]) => {
      if (!active) return;
      if (name.status === "fulfilled" && name.value) setUserName(name.value);
      if (email.status === "fulfilled" && email.value) setUserEmail(email.value);
      if (workspace.status === "fulfilled" && workspace.value) setUserWorkspace(workspace.value);
    });
    return () => {
      active = false;
    };
  }, []);

  return (
    <div className="flex flex-col h-full bg-[oklch(var(--background))]">
      {/* Toolbar */}
      <div className="px-6 py-4 border-b border-[oklch(var(--border))] flex items-center justify-between">
        <div>
          <h2 className="text-sm font-semibold text-[oklch(var(--foreground))] tracking-tight">
            Settings & Vault Preferences
          </h2>
          <p className="text-xs text-[oklch(var(--muted-foreground))] mt-0.5">
            Credentials and state stored locally in your OS Keychain and SQLite database
          </p>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto px-6 py-6 space-y-6">
        {/* Account & Profile Summary */}
        <section>
          <div className="flex items-center gap-2 mb-2.5">
            <User size={14} className="text-[oklch(var(--muted-foreground))]" />
            <h3 className="text-xs font-semibold text-[oklch(var(--foreground))] uppercase tracking-wide">
              Operator Profile
            </h3>
          </div>
          <div className="border border-[oklch(var(--border))] bg-[oklch(var(--card))] rounded-xl p-4 flex items-center justify-between shadow-xs">
            <div className="flex items-center gap-3">
              <div className="w-10 h-10 rounded-full bg-[oklch(var(--accent)/0.15)] text-[oklch(var(--accent))] flex items-center justify-center font-bold text-sm border border-[oklch(var(--accent)/0.3)]">
                {userName.slice(0, 2).toUpperCase()}
              </div>
              <div>
                <p className="text-xs font-semibold text-[oklch(var(--foreground))]">
                  {userName}
                </p>
                <p className="text-[0.6875rem] text-[oklch(var(--muted-foreground))]">
                  {userEmail} · {userWorkspace}
                </p>
              </div>
            </div>
            {onResetOnboarding && (
              <Button
                variant="outline"
                onClick={onResetOnboarding}
                className="text-xs h-8 gap-1.5"
              >
                <RotateCcw size={12} />
                Re-run Onboarding
              </Button>
            )}
          </div>
        </section>

        {/* Appearance & Dark Mode Toggle */}
        <section>
          <div className="flex items-center gap-2 mb-2.5">
            {theme === "dark" ? (
              <Moon size={14} className="text-[oklch(var(--muted-foreground))]" />
            ) : (
              <Sun size={14} className="text-[oklch(var(--muted-foreground))]" />
            )}
            <h3 className="text-xs font-semibold text-[oklch(var(--foreground))] uppercase tracking-wide">
              Interface Theme
            </h3>
          </div>
          <div className="border border-[oklch(var(--border))] bg-[oklch(var(--card))] rounded-xl p-4 flex items-center justify-between shadow-xs">
            <div>
              <p className="text-xs font-semibold text-[oklch(var(--foreground))]">
                Color Scheme
              </p>
              <p className="text-[0.6875rem] text-[oklch(var(--muted-foreground))] mt-0.5">
                Switch between refined warm alabaster and high-contrast obsidian slate
              </p>
            </div>
            {onToggleTheme && (
              <div className="flex items-center gap-1.5 p-1 bg-[oklch(var(--background))] border border-[oklch(var(--border))] rounded-lg">
                <button
                  type="button"
                  onClick={() => theme === "dark" && onToggleTheme()}
                  className={`flex items-center gap-1.5 px-3 py-1 rounded-md text-xs font-medium transition-all ${
                    theme === "light"
                      ? "bg-[oklch(var(--card))] text-[oklch(var(--foreground))] shadow-xs"
                      : "text-[oklch(var(--muted-foreground))] hover:text-[oklch(var(--foreground))]"
                  }`}
                >
                  <Sun size={12} /> Light
                </button>
                <button
                  type="button"
                  onClick={() => theme === "light" && onToggleTheme()}
                  className={`flex items-center gap-1.5 px-3 py-1 rounded-md text-xs font-medium transition-all ${
                    theme === "dark"
                      ? "bg-[oklch(var(--card))] text-[oklch(var(--foreground))] shadow-xs"
                      : "text-[oklch(var(--muted-foreground))] hover:text-[oklch(var(--foreground))]"
                  }`}
                >
                  <Moon size={12} /> Dark
                </button>
              </div>
            )}
          </div>
        </section>

        {/* Model chain */}
        <section>
          <div className="flex items-center gap-2 mb-2.5">
            <Cpu size={14} className="text-[oklch(var(--muted-foreground))]" />
            <h3 className="text-xs font-semibold text-[oklch(var(--foreground))] uppercase tracking-wide">
              Reasoning Engine
            </h3>
          </div>
          <div className="border border-[oklch(var(--border))] bg-[oklch(var(--card))] rounded-xl p-4 space-y-3 shadow-xs">
            <p className="text-xs text-[oklch(var(--muted-foreground))] leading-relaxed">
              gather executes models in sequence until one satisfies the extraction schema. The default chain uses verified free hosted tiers.
            </p>
            <div className="space-y-1.5">
              {[
                { label: "1. groq:openai/gpt-oss-120b", desc: "Primary extraction, ~1.2s latency" },
                { label: "2. openrouter:nvidia/nemotron-3.5-lightning:free", desc: "Fallback model" },
              ].map((m) => (
                <div
                  key={m.label}
                  className="flex items-center justify-between p-2 rounded-lg bg-[oklch(var(--background))] border border-[oklch(var(--border)/0.6)] text-xs"
                >
                  <span className="font-mono text-[oklch(var(--foreground))]">{m.label}</span>
                  <span className="text-[0.6875rem] text-[oklch(var(--muted-foreground))]">{m.desc}</span>
                </div>
              ))}
            </div>
          </div>
        </section>

        {/* Engine info */}
        <section>
          <div className="border border-[oklch(var(--border))] bg-[oklch(var(--muted)/0.3)] rounded-xl p-4">
            <div className="flex items-center gap-2 mb-1">
              <ShieldCheck size={14} className="text-[oklch(var(--accent))]" />
              <p className="text-xs font-medium text-[oklch(var(--foreground))]">
                Data directory & security boundary
              </p>
            </div>
            <code className="text-xs text-[oklch(var(--muted-foreground))] font-mono">
              ~/.local/share/gather/
            </code>
            <p className="text-[0.6875rem] text-[oklch(var(--muted-foreground))] mt-2 leading-relaxed">
              All files are owned by your user account: gather.db · checkpoints.db · logs/engine.log · secrets.json
            </p>
          </div>
        </section>
      </div>
    </div>
  );
}
