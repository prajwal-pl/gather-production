/**
 * SettingsView — API keys, model chain, theme preference, engine diagnostics.
 * Keys are write-only through `settings.update`; only presence booleans are shown.
 */
import { useEffect, useState } from "react";
import {
  Moon,
  Sun,
  User,
  RotateCcw,
  ShieldCheck,
  Cpu,
  KeyRound,
  Eye,
  EyeOff,
  Save,
} from "lucide-react";
import { Badge, Button, ErrorNotice, Spinner } from "./ui";
import { engine, flags, type SettingsSnapshot } from "../engine";

const MODEL_KEYS = [
  {
    key: "GROQ_API_KEY",
    label: "Groq API key",
    hint: "Credentials for the configured Groq models.",
  },
  {
    key: "OPENROUTER_API_KEY",
    label: "OpenRouter API key",
    hint: "Fallback model chain at openrouter.ai",
  },
];

const GOOGLE_KEYS = [
  { key: "GOOGLE_CLIENT_ID", label: "Google OAuth client ID", hint: "Use the client ID from a Desktop app OAuth credential." },
  { key: "GOOGLE_CLIENT_SECRET", label: "Google OAuth client secret", hint: "Use the secret from the same credential." },
];

function SecretField({
  name,
  label,
  hint,
  present,
  backend,
  onChanged,
  onRestartRequired,
}: {
  name: string;
  label: string;
  hint: string;
  present: boolean;
  backend?: string;
  onChanged: () => void;
  onRestartRequired: () => void;
}) {
  const [value, setValue] = useState("");
  const [visible, setVisible] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function save() {
    const trimmed = value.trim();
    if (!trimmed) return;
    setSaving(true);
    setError(null);
    try {
      await engine.settings.update({ secrets: { [name]: trimmed } });
      setValue("");
      onChanged();
      onRestartRequired();
    } catch (err) {
      setError(String(err));
    } finally {
      setSaving(false);
    }
  }

  async function remove() {
    setSaving(true);
    setError(null);
    try {
      await engine.settings.update({ secrets: { [name]: "" } });
      onChanged();
      onRestartRequired();
    } catch (err) {
      setError(String(err));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="py-3 border-b border-[oklch(var(--border))] last:border-0">
      <div className="flex items-start justify-between gap-4">
        <div className="flex-1">
          <div className="flex items-center gap-2 mb-0.5">
            <label className="text-xs font-semibold text-[oklch(var(--foreground))]">
              {label}
            </label>
            {present ? <Badge variant="success">Set</Badge> : <Badge variant="muted">Not set</Badge>}
            {backend && (
              <span className="text-[0.625rem] text-[oklch(var(--muted-foreground))]">
                {backend}
              </span>
            )}
          </div>
          <p className="text-[0.6875rem] text-[oklch(var(--muted-foreground))]">{hint}</p>
        </div>
      </div>

      <div className="flex items-center gap-2 mt-2">
        <div className="flex-1 relative">
          <input
            type={visible ? "text" : "password"}
            value={value}
            onChange={(event) => setValue(event.target.value)}
            placeholder={present ? "Enter a new key to replace…" : "Paste key here…"}
            aria-label={label}
            className="w-full h-8 px-3 pr-8 text-xs bg-[oklch(var(--background))] border border-[oklch(var(--border))] rounded-lg text-[oklch(var(--foreground))] placeholder:text-[oklch(var(--muted-foreground))] focus:outline-none focus:ring-1 focus:ring-[oklch(var(--primary))]"
            onKeyDown={(event) => {
              if (event.key === "Enter") void save();
            }}
          />
          <button
            type="button"
            onClick={() => setVisible((current) => !current)}
            aria-label={visible ? "Hide key" : "Show key"}
            className="absolute right-2.5 top-1/2 -translate-y-1/2 text-[oklch(var(--muted-foreground))] hover:text-[oklch(var(--foreground))]"
          >
            {visible ? <EyeOff size={13} /> : <Eye size={13} />}
          </button>
        </div>
        <Button
          variant="outline"
          onClick={() => void save()}
          disabled={!value.trim() || saving}
          className="h-8 text-xs"
        >
          {saving ? <Spinner size={12} /> : <Save size={12} />}
          Save
        </Button>
        {present && (
          <Button
            variant="ghost"
            onClick={() => void remove()}
            disabled={saving}
            className="h-8 text-xs text-[oklch(var(--destructive))]"
          >
            Remove
          </Button>
        )}
      </div>

      {error && <p className="mt-1.5 text-[0.6875rem] text-[oklch(var(--destructive))]">{error}</p>}
    </div>
  );
}

export function SettingsView({
  theme,
  onToggleTheme,
  onResetOnboarding,
}: {
  theme?: "light" | "dark";
  onToggleTheme?: () => void;
  onResetOnboarding?: () => Promise<void>;
}) {
  // Profile lives in the file-backed flag store; localStorage is only a cache.
  const [userName, setUserName] = useState(
    () => localStorage.getItem("gather_user_name") || "Operator",
  );
  const [userEmail, setUserEmail] = useState(
    () => localStorage.getItem("gather_user_email") || "",
  );
  const [userWorkspace, setUserWorkspace] = useState(
    () => localStorage.getItem("gather_workspace") || "Local Vault",
  );
  const [snapshot, setSnapshot] = useState<SettingsSnapshot | null>(null);
  const [settingsError, setSettingsError] = useState<string | null>(null);
  const [restartRequired, setRestartRequired] = useState(false);
  const [resetError, setResetError] = useState("");
  const [resetting, setResetting] = useState(false);

  async function loadSettings() {
    try {
      setSnapshot(await engine.settings.get());
      setSettingsError(null);
    } catch (err) {
      setSettingsError(String(err));
    }
  }

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
    void loadSettings();
    return () => {
      active = false;
    };
  }, []);

  const modelChain = snapshot?.chain ?? [];
  const backend = snapshot?.secrets_backend === "keyring" ? "OS keychain" : snapshot?.secrets_backend;
  const hasLiveModel = Boolean(
    snapshot?.secrets?.GROQ_API_KEY || snapshot?.secrets?.OPENROUTER_API_KEY,
  );

  return (
    <div className="flex flex-col h-full bg-[oklch(var(--background))]">
      {/* Toolbar */}
      <header className="page-header">
        <div>
          <h1>Settings</h1>
          <p className="text-xs text-[oklch(var(--muted-foreground))] mt-0.5">
            Manage credentials, extraction models, and appearance
          </p>
        </div>
      </header>

      <div className="page-body space-y-6">
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
                  {userEmail ? `${userEmail} · ` : ""}{userWorkspace}
                </p>
              </div>
            </div>
            {onResetOnboarding && (
              <Button
                variant="outline"
                disabled={resetting}
                onClick={() => {
                  setResetting(true);
                  setResetError("");
                  void onResetOnboarding().catch((err) => setResetError(String(err))).finally(() => setResetting(false));
                }}
                className="text-xs h-8 gap-1.5"
              >
                <RotateCcw size={12} />
                Re-run Onboarding
              </Button>
            )}
          </div>
          {resetError && <ErrorNotice>{resetError}</ErrorNotice>}
        </section>

        {/* AI credentials */}
        <section>
          <div className="flex items-center gap-2 mb-2.5">
            <KeyRound size={14} className="text-[oklch(var(--muted-foreground))]" />
            <h3 className="text-xs font-semibold text-[oklch(var(--foreground))] uppercase tracking-wide">
              AI Credentials
            </h3>
          </div>
          <div className="border border-[oklch(var(--border))] bg-[oklch(var(--card))] rounded-xl px-4 shadow-xs">
            {MODEL_KEYS.map((field) => (
              <SecretField
                key={field.key}
                name={field.key}
                label={field.label}
                hint={field.hint}
                present={Boolean(snapshot?.secrets?.[field.key])}
                backend={backend}
                onChanged={() => void loadSettings()}
                onRestartRequired={() => setRestartRequired(true)}
              />
            ))}
          </div>
          {settingsError && (
            <p className="mt-1.5 text-[0.6875rem] text-[oklch(var(--destructive))]">
              {settingsError}
            </p>
          )}
          {restartRequired && (
            <p className="mt-2 text-[0.6875rem] text-[oklch(0.55_0.18_80)]">
              Saved. Restart the app to activate the new model credentials.
            </p>
          )}
          <p className="mt-1.5 text-[0.6875rem] text-[oklch(var(--muted-foreground))]">
            Keys are write-only and never returned to the UI. Without one, the
            engine runs in offline fixture mode.
          </p>
        </section>

        <section>
          <h3 className="text-sm font-semibold mb-2">Google OAuth credentials</h3>
          <div className="surface px-4">
            {GOOGLE_KEYS.map((field) => (
              <SecretField key={field.key} name={field.key} label={field.label} hint={field.hint}
                present={Boolean(snapshot?.secrets?.[field.key])} backend={backend}
                onChanged={() => void loadSettings()} onRestartRequired={() => {}} />
            ))}
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
              Gather tries these models in order. Hosted models receive transcript excerpts when you run extraction.
            </p>
            {!hasLiveModel && snapshot && (
              <div className="p-2.5 rounded-lg bg-[oklch(0.72_0.18_80/0.12)] border border-[oklch(0.72_0.18_80/0.25)] text-[0.6875rem] text-[oklch(0.55_0.18_80)]">
                Offline fixture mode — add a Groq or OpenRouter key above to enable live reasoning.
              </div>
            )}
            <div className="space-y-1.5">
              {modelChain.map((label, index) => (
                <div
                  key={label}
                  className="flex items-center justify-between p-2 rounded-lg bg-[oklch(var(--background))] border border-[oklch(var(--border)/0.6)] text-xs"
                >
                  <span className="font-mono text-[oklch(var(--foreground))]">
                    {index + 1}. {label}
                  </span>
                  <Badge variant={index === 0 ? "success" : "muted"}>
                    {index === 0 ? "Primary" : "Fallback"}
                  </Badge>
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
              {snapshot?.data_dir || "Loading path…"}
            </code>
            <p className="text-[0.6875rem] text-[oklch(var(--muted-foreground))] mt-2 leading-relaxed">
              Workspace data is stored in SQLite. Credential storage: {backend || "unknown"}.
            </p>
          </div>
        </section>
      </div>
    </div>
  );
}
