/**
 * SettingsView — API keys, model chain, engine info.
 * Keys are write-only (secrets.set IPC); presence booleans only are shown.
 */
import { useState } from "react";
import { Key, Eye, EyeOff, Save, Info } from "lucide-react";
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
    hint: "Free at console.groq.com — powers commitment extraction",
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
  {
    key: "GOOGLE_CLIENT_ID",
    label: "Google client ID",
    hint: "OAuth credential from Google Cloud Console",
    present: false,
  },
  {
    key: "GOOGLE_CLIENT_SECRET",
    label: "Google client secret",
    hint: "Paired with the client ID",
    present: false,
  },
  {
    key: "GMAIL_REFRESH_TOKEN",
    label: "Gmail refresh token",
    hint: "Run scripts/oauth_google.py to obtain",
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
    // TODO: replace with engine.secrets.set(field.key, value)
    await new Promise((r) => setTimeout(r, 300));
    setSaving(false);
    setSaved(true);
    setValue("");
    setTimeout(() => setSaved(false), 2000);
  }

  return (
    <div className="py-3 border-b border-[oklch(var(--border)/0.2)] last:border-0">
      <div className="flex items-start justify-between gap-4">
        <div className="flex-1">
          <div className="flex items-center gap-2 mb-0.5">
            <label className="text-[0.8125rem] font-medium text-[oklch(var(--foreground))]">
              {field.label}
            </label>
            {field.present ? (
              <Badge variant="success">Set</Badge>
            ) : (
              <Badge variant="muted">Not set</Badge>
            )}
          </div>
          <p className="text-[0.75rem] text-[oklch(var(--muted-foreground))]">{field.hint}</p>
        </div>
      </div>

      <div className="flex items-center gap-2 mt-2">
        <div className="flex-1 relative">
          <input
            type={visible ? "text" : "password"}
            value={value}
            onChange={(e) => setValue(e.target.value)}
            placeholder={field.present ? "Enter new value to rotate…" : "Paste key here…"}
            className="w-full h-8 px-3 pr-8 text-[0.8125rem] border border-[oklch(var(--border)/0.3)] bg-[oklch(var(--card))] text-[oklch(var(--foreground))] placeholder:text-[oklch(var(--muted-foreground))] focus:outline-none focus:border-[oklch(var(--primary))]"
            onKeyDown={(e) => e.key === "Enter" && handleSave()}
          />
          <button
            type="button"
            onClick={() => setVisible((v) => !v)}
            className="absolute right-2 top-1/2 -translate-y-1/2 text-[oklch(var(--muted-foreground))] hover:text-[oklch(var(--foreground))]"
          >
            {visible ? <EyeOff size={13} /> : <Eye size={13} />}
          </button>
        </div>
        <Button
          variant="outline"
          onClick={handleSave}
          disabled={!value.trim() || saving}
        >
          {saving ? <Spinner size={13} /> : saved ? "✓" : <Save size={13} />}
          {saved ? "Saved" : "Save"}
        </Button>
      </div>
    </div>
  );
}

export function SettingsView() {
  return (
    <div className="flex flex-col h-full">
      {/* Toolbar */}
      <div className="px-6 py-3 border-b border-[oklch(var(--border)/0.2)]">
        <h2 className="text-[0.9375rem] font-semibold text-[oklch(var(--foreground))]">
          Settings
        </h2>
        <p className="text-[0.75rem] text-[oklch(var(--muted-foreground))] mt-0.5">
          API keys are stored in the OS keychain — never in plain text
        </p>
      </div>

      <div className="flex-1 overflow-y-auto px-6 py-5 space-y-8">
        {/* API Keys */}
        <section>
          <div className="flex items-center gap-2 mb-3">
            <Key size={14} className="text-[oklch(var(--muted-foreground))]" />
            <h3 className="text-[0.8125rem] font-semibold text-[oklch(var(--foreground))] uppercase tracking-wide">
              API Keys
            </h3>
          </div>
          <div className="border border-[oklch(var(--border)/0.3)] bg-[oklch(var(--card))] px-4 divide-y divide-[oklch(var(--border)/0.2)]">
            {SECRET_FIELDS.map((field) => (
              <SecretRow key={field.key} field={field} />
            ))}
          </div>
        </section>

        {/* Model chain */}
        <section>
          <div className="flex items-center gap-2 mb-3">
            <Info size={14} className="text-[oklch(var(--muted-foreground))]" />
            <h3 className="text-[0.8125rem] font-semibold text-[oklch(var(--foreground))] uppercase tracking-wide">
              Model chain
            </h3>
          </div>
          <div className="border border-[oklch(var(--border)/0.3)] bg-[oklch(var(--card))] p-4 space-y-2">
            <p className="text-[0.75rem] text-[oklch(var(--muted-foreground))] leading-relaxed">
              gather tries models in order until one responds. The default chain
              uses free hosted tiers only — no local GPU required.
            </p>
            <div className="mt-3 space-y-1">
              {["groq:openai/gpt-oss-120b", "openrouter:nvidia/nemotron-3.5-lightning:free"].map(
                (m, i) => (
                  <div
                    key={m}
                    className="flex items-center gap-2 text-[0.75rem] text-[oklch(var(--foreground))] font-mono"
                  >
                    <span className="text-[oklch(var(--muted-foreground))]">{i + 1}.</span>
                    {m}
                  </div>
                )
              )}
            </div>
          </div>
        </section>

        {/* Engine info */}
        <section>
          <div className="border border-[oklch(var(--border)/0.3)] bg-[oklch(var(--muted)/0.3)] p-4">
            <p className="text-[0.75rem] font-medium text-[oklch(var(--foreground))] mb-1">
              Data directory
            </p>
            <code className="text-[0.75rem] text-[oklch(var(--muted-foreground))] font-mono">
              ~/.local/share/gather/
            </code>
            <p className="text-[0.75rem] text-[oklch(var(--muted-foreground))] mt-2 leading-relaxed">
              gather.db · checkpoints.db · logs/engine.log · secrets.json (if no keychain)
            </p>
          </div>
        </section>
      </div>
    </div>
  );
}
