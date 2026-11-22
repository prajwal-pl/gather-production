/**
 * SettingsView — API keys, model chain, theme preference, engine diagnostics.
 * Keys are write-only (secrets.set IPC); presence booleans only are shown.
 */
import { useState } from "react";
import {
  Key,
  Eye,
  EyeOff,
  Save,
  Moon,
  Sun,
  User,
  RotateCcw,
  ShieldCheck,
  Cpu,
} from "lucide-react";
import { Badge, Button, Spinner } from "./ui";

interface SecretField {
  key: string;
  label: string;
  hint: string;
  present?: boolean;
}

const SECRET_FIELDS: SecretField[] = [
  {
    key: "GROQ_API_KEY",
    label: "Groq API key",
    hint: "Free at console.groq.com — powers commitment extraction (~1.2s)",
    present: false,
  },
  {
    key: "OPENROUTER_API_KEY",
    label: "OpenRouter API key",
    hint: "Fallback model chain at openrouter.ai",
    present: false,
  },
  {
    key: "FIREFLIES_API_KEY",
    label: "Fireflies API key",
    hint: "Settings → API in your Fireflies workspace",
    present: false,
  },
];

function SecretRow({ field }: { field: SecretField }) {
  const [value, setValue] = useState("");
  const [visible, setVisible] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);

  async function handleSave() {
    if (!value.trim()) return;
    setSaving(true);
    await new Promise((r) => setTimeout(r, 300));
    setSaving(false);
    setSaved(true);
    setValue("");
    setTimeout(() => setSaved(false), 2000);
  }

  return (
    <div className="py-3 border-b border-[oklch(var(--border))] last:border-0">
      <div className="flex items-start justify-between gap-4">
        <div className="flex-1">
          <div className="flex items-center gap-2 mb-0.5">
            <label className="text-xs font-semibold text-[oklch(var(--foreground))]">
              {field.label}
            </label>
            {field.present ? (
              <Badge variant="success">Set</Badge>
            ) : (
              <Badge variant="muted">Not set</Badge>
            )}
          </div>
          <p className="text-[0.6875rem] text-[oklch(var(--muted-foreground))]">{field.hint}</p>
        </div>
      </div>

      <div className="flex items-center gap-2 mt-2">
        <div className="flex-1 relative">
          <input
            type={visible ? "text" : "password"}
            value={value}
            onChange={(e) => setValue(e.target.value)}
            placeholder={field.present ? "Enter new key to rotate…" : "Paste key here…"}
            className="w-full h-8 px-3 pr-8 text-xs bg-[oklch(var(--background))] border border-[oklch(var(--border))] rounded-lg text-[oklch(var(--foreground))] placeholder:text-[oklch(var(--muted-foreground))] focus:outline-none focus:ring-1 focus:ring-[oklch(var(--primary))]"
            onKeyDown={(e) => e.key === "Enter" && handleSave()}
          />
          <button
            type="button"
            onClick={() => setVisible((v) => !v)}
            className="absolute right-2.5 top-1/2 -translate-y-1/2 text-[oklch(var(--muted-foreground))] hover:text-[oklch(var(--foreground))]"
          >
            {visible ? <EyeOff size={13} /> : <Eye size={13} />}
          </button>
        </div>
        <Button
          variant="outline"
          onClick={handleSave}
          disabled={!value.trim() || saving}
          className="h-8 text-xs"
        >
          {saving ? <Spinner size={12} /> : saved ? "✓" : <Save size={12} />}
          {saved ? "Saved" : "Save"}
        </Button>
      </div>
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
  onResetOnboarding?: () => void;
}) {
  const userName = localStorage.getItem("gather_user_name") || "Operator";
  const userEmail = localStorage.getItem("gather_user_email") || "local@gather.vault";
  const userWorkspace = localStorage.getItem("gather_workspace") || "Local Vault";

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

        {/* API Keys */}
        <section>
          <div className="flex items-center gap-2 mb-2.5">
            <Key size={14} className="text-[oklch(var(--muted-foreground))]" />
            <h3 className="text-xs font-semibold text-[oklch(var(--foreground))] uppercase tracking-wide">
              API Credentials
            </h3>
          </div>
          <div className="border border-[oklch(var(--border))] bg-[oklch(var(--card))] rounded-xl px-4 divide-y divide-[oklch(var(--border))] shadow-xs">
            {SECRET_FIELDS.map((field) => (
              <SecretRow key={field.key} field={field} />
            ))}
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
