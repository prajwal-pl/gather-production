/**
 * OnboardingView — Smooth, interactive onboarding flow.
 *
 * Steps:
 * 1. Account & Workspace setup
 * 2. Select tools to connect (interactive tick-off)
 * 3. Connect & authorize selected tools
 * 4. AI Engine configuration (free hosted chain / fixture)
 * 5. Launch confirmation
 */
import { useState } from "react";
import {
  User,
  Mail,
  Building,
  Check,
  CheckCircle2,
  ArrowRight,
  ArrowLeft,
  Sparkles,
  ShieldCheck,
  Plug,
  Calendar,
  MessageSquare,
  Key,
  Lock,
  Cpu,
} from "lucide-react";
import { Badge, Button, Spinner } from "./ui";

export interface OnboardingData {
  name: string;
  email: string;
  workspace: string;
  role: string;
  selectedApps: string[];
  connectedApps: Record<string, boolean>;
  useFixtureAi: boolean;
}

interface AppOption {
  id: string;
  name: string;
  category: string;
  icon: string;
  badge?: string;
  description: string;
  recommended?: boolean;
}

const AVAILABLE_APPS: AppOption[] = [
  {
    id: "fireflies",
    name: "Fireflies.ai",
    category: "Transcripts & Recordings",
    icon: "🎙️",
    badge: "Primary Source",
    description: "Automatically pulls transcripts, speaker turns, and notes from Zoom, Meet, and Teams.",
    recommended: true,
  },
  {
    id: "gmail",
    name: "Google Workspace / Gmail",
    category: "Email & Reply Loop",
    icon: "✉️",
    badge: "Watch Loop",
    description: "Watches for inbound replies to follow-ups and matches them against active tasks.",
    recommended: true,
  },
  {
    id: "calendar",
    name: "Google Calendar",
    category: "Schedule & Attendees",
    icon: "📅",
    badge: "Context",
    description: "Correlates meeting attendees with email addresses to resolve task owners accurately.",
    recommended: false,
  },
  {
    id: "slack",
    name: "Slack (Socket Mode)",
    category: "Nudges & Alerts",
    icon: "💬",
    badge: "Outbound",
    description: "Receive interactive approval prompts and follow-up nudges directly in your Slack DMs.",
    recommended: false,
  },
  {
    id: "offline_files",
    name: "Local Transcript Drop",
    category: "Zero Cloud",
    icon: "📄",
    badge: "Offline Ready",
    description: "Drop VTT, SRT, or plain text transcripts directly into gather without connecting an account.",
    recommended: true,
  },
];

const ROLES = [
  "Founder / Executive",
  "Engineering Lead",
  "Product Manager",
  "Consultant / Advisor",
  "Operations Lead",
];

export function OnboardingView({ onComplete }: { onComplete: () => void }) {
  // Step tracking: 1 to 5
  const [currentStep, setCurrentStep] = useState<number>(1);

  // Form state
  const [name, setName] = useState<string>(() => localStorage.getItem("gather_user_name") || "");
  const [email, setEmail] = useState<string>(() => localStorage.getItem("gather_user_email") || "");
  const [workspace, setWorkspace] = useState<string>(() => localStorage.getItem("gather_workspace") || "");
  const [role, setRole] = useState<string>("Product Manager");

  // App selection state (ticked apps)
  const [selectedApps, setSelectedApps] = useState<string[]>(["fireflies", "gmail", "offline_files"]);

  // Connection auth simulation state
  const [connectedApps, setConnectedApps] = useState<Record<string, boolean>>({
    offline_files: true,
  });
  const [connectingApp, setConnectingApp] = useState<string | null>(null);
  const [firefliesKey, setFirefliesKey] = useState<string>("");

  // AI preferences
  const [useFixtureAi, setUseFixtureAi] = useState<boolean>(true);

  // Toggle app selection
  const toggleApp = (id: string) => {
    setSelectedApps((prev) =>
      prev.includes(id) ? prev.filter((a) => a !== id) : [...prev, id]
    );
  };

  // Simulate connecting an application
  const handleConnectApp = async (appId: string) => {
    setConnectingApp(appId);
    await new Promise((r) => setTimeout(r, 700));
    setConnectedApps((prev) => ({ ...prev, [appId]: true }));
    setConnectingApp(null);
  };

  // Complete onboarding
  const handleFinish = () => {
    localStorage.setItem("gather_user_name", name.trim() || "Operator");
    localStorage.setItem("gather_user_email", email.trim() || "user@local");
    localStorage.setItem("gather_workspace", workspace.trim() || `${name.trim() || "Local"}'s Workspace`);
    localStorage.setItem("gather_onboarded", "true");
    localStorage.setItem("gather_connected_apps", JSON.stringify(selectedApps));
    onComplete();
  };

  // Validation
  const canProceedStep1 = name.trim().length > 0 && email.trim().length > 0;

  return (
    <div className="min-h-screen w-full flex flex-col justify-between bg-[oklch(var(--background))] text-[oklch(var(--foreground))] px-6 py-8 transition-colors duration-300">
      {/* Top Header / Progress Bar */}
      <div className="max-w-3xl w-full mx-auto">
        <div className="flex items-center justify-between mb-6">
          <div className="flex items-center gap-2.5">
            <div className="w-7 h-7 bg-[oklch(var(--primary))] text-[oklch(var(--primary-foreground))] rounded-lg flex items-center justify-center font-bold text-xs shadow-sm">
              g
            </div>
            <span className="font-semibold text-sm tracking-tight text-[oklch(var(--foreground))]">
              gather <span className="opacity-40 font-normal">| setup</span>
            </span>
          </div>

          <div className="flex items-center gap-1.5 text-xs text-[oklch(var(--muted-foreground))]">
            <span>Step {currentStep} of 5</span>
          </div>
        </div>

        {/* Step indicator pills */}
        <div className="grid grid-cols-5 gap-2 mb-8">
          {[
            { step: 1, label: "Account" },
            { step: 2, label: "Select Tools" },
            { step: 3, label: "Connect" },
            { step: 4, label: "AI Reasoning" },
            { step: 5, label: "Launch" },
          ].map((item) => {
            const isDone = currentStep > item.step;
            const isCurrent = currentStep === item.step;
            return (
              <div key={item.step} className="flex flex-col gap-1.5">
                <div
                  className={`h-1.5 rounded-full transition-all duration-300 ${
                    isDone
                      ? "bg-[oklch(var(--accent))]"
                      : isCurrent
                      ? "bg-[oklch(var(--primary))]"
                      : "bg-[oklch(var(--muted))]"
                  }`}
                />
                <span
                  className={`text-[0.6875rem] font-medium transition-colors ${
                    isCurrent
                      ? "text-[oklch(var(--foreground))]"
                      : "text-[oklch(var(--muted-foreground))]"
                  }`}
                >
                  {item.label}
                </span>
              </div>
            );
          })}
        </div>
      </div>

      {/* Main Content Card Container */}
      <div className="max-w-2xl w-full mx-auto flex-1 flex flex-col justify-center">
        {/* ── STEP 1: ACCOUNT & WORKSPACE ── */}
        {currentStep === 1 && (
          <div className="bg-[oklch(var(--card))] border border-[oklch(var(--border))] rounded-2xl p-8 shadow-sm animate-slide-up">
            <div className="flex items-center gap-2 mb-2">
              <Badge variant="accent">
                <Sparkles size={11} className="mr-0.5" /> Single-User Local Vault
              </Badge>
            </div>
            <h1 className="text-xl font-semibold text-[oklch(var(--foreground))] tracking-tight">
              Create your local workspace
            </h1>
            <p className="text-xs text-[oklch(var(--muted-foreground))] mt-1 mb-6 leading-relaxed">
              gather runs entirely on your desktop. Your transcripts, action items, and contacts remain on your machine in encrypted SQLite.
            </p>

            <div className="space-y-4">
              <div>
                <label className="block text-xs font-medium text-[oklch(var(--foreground))] mb-1.5">
                  Your Full Name
                </label>
                <div className="relative">
                  <User size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-[oklch(var(--muted-foreground))]" />
                  <input
                    type="text"
                    value={name}
                    onChange={(e) => setName(e.target.value)}
                    placeholder="e.g. Alex Mercer"
                    className="w-full h-9 pl-9 pr-3 text-xs bg-[oklch(var(--background))] border border-[oklch(var(--border))] rounded-lg text-[oklch(var(--foreground))] placeholder:text-[oklch(var(--muted-foreground))] focus:outline-none focus:ring-1 focus:ring-[oklch(var(--primary))]"
                    autoFocus
                  />
                </div>
              </div>

              <div>
                <label className="block text-xs font-medium text-[oklch(var(--foreground))] mb-1.5">
                  Primary Work Email
                </label>
                <div className="relative">
                  <Mail size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-[oklch(var(--muted-foreground))]" />
                  <input
                    type="email"
                    value={email}
                    onChange={(e) => setEmail(e.target.value)}
                    placeholder="alex@company.com"
                    className="w-full h-9 pl-9 pr-3 text-xs bg-[oklch(var(--background))] border border-[oklch(var(--border))] rounded-lg text-[oklch(var(--foreground))] placeholder:text-[oklch(var(--muted-foreground))] focus:outline-none focus:ring-1 focus:ring-[oklch(var(--primary))]"
                  />
                </div>
              </div>

              <div>
                <label className="block text-xs font-medium text-[oklch(var(--foreground))] mb-1.5">
                  Workspace Name
                </label>
                <div className="relative">
                  <Building size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-[oklch(var(--muted-foreground))]" />
                  <input
                    type="text"
                    value={workspace}
                    onChange={(e) => setWorkspace(e.target.value)}
                    placeholder={name ? `${name.split(" ")[0]}'s Workspace` : "Personal Workspace"}
                    className="w-full h-9 pl-9 pr-3 text-xs bg-[oklch(var(--background))] border border-[oklch(var(--border))] rounded-lg text-[oklch(var(--foreground))] placeholder:text-[oklch(var(--muted-foreground))] focus:outline-none focus:ring-1 focus:ring-[oklch(var(--primary))]"
                  />
                </div>
              </div>

              <div>
                <label className="block text-xs font-medium text-[oklch(var(--foreground))] mb-1.5">
                  Primary Focus / Role
                </label>
                <div className="flex flex-wrap gap-1.5">
                  {ROLES.map((r) => (
                    <button
                      key={r}
                      type="button"
                      onClick={() => setRole(r)}
                      className={`text-xs px-2.5 py-1 rounded-full border transition-all ${
                        role === r
                          ? "bg-[oklch(var(--primary))] text-[oklch(var(--primary-foreground))] border-[oklch(var(--primary))]"
                          : "bg-[oklch(var(--background))] text-[oklch(var(--muted-foreground))] border-[oklch(var(--border))] hover:text-[oklch(var(--foreground))]"
                      }`}
                    >
                      {r}
                    </button>
                  ))}
                </div>
              </div>
            </div>

            {/* Live Identity Badge Preview */}
            <div className="mt-6 p-3.5 bg-[oklch(var(--muted)/0.4)] border border-[oklch(var(--border)/0.5)] rounded-xl flex items-center justify-between">
              <div className="flex items-center gap-3">
                <div className="w-9 h-9 rounded-full bg-[oklch(var(--accent)/0.15)] text-[oklch(var(--accent))] flex items-center justify-center font-semibold text-xs border border-[oklch(var(--accent)/0.3)]">
                  {name ? name.slice(0, 2).toUpperCase() : "OP"}
                </div>
                <div>
                  <div className="flex items-center gap-2">
                    <span className="text-xs font-semibold text-[oklch(var(--foreground))]">
                      {name || "Operator"}
                    </span>
                    <Badge variant="muted">{role}</Badge>
                  </div>
                  <span className="text-[0.6875rem] text-[oklch(var(--muted-foreground))]">
                    {email || "local@gather.vault"} · {workspace || "Local Workspace"}
                  </span>
                </div>
              </div>
              <div className="flex items-center gap-1 text-[0.6875rem] text-[oklch(var(--accent))] font-medium">
                <Lock size={12} /> OS Keychain
              </div>
            </div>

            <div className="mt-6 flex justify-end">
              <Button
                variant="primary"
                onClick={() => setCurrentStep(2)}
                disabled={!canProceedStep1}
                className="gap-2"
              >
                <span>Continue to Tool Selection</span>
                <ArrowRight size={14} />
              </Button>
            </div>
          </div>
        )}

        {/* ── STEP 2: SELECT APPLICATIONS TO CONNECT ── */}
        {currentStep === 2 && (
          <div className="bg-[oklch(var(--card))] border border-[oklch(var(--border))] rounded-2xl p-8 shadow-sm animate-slide-up">
            <div className="flex items-center justify-between mb-1">
              <Badge variant="accent">
                <Plug size={11} className="mr-0.5" /> Integration Registry
              </Badge>
              <span className="text-xs text-[oklch(var(--muted-foreground))]">
                {selectedApps.length} tools selected
              </span>
            </div>
            <h1 className="text-xl font-semibold text-[oklch(var(--foreground))] tracking-tight">
              Tick off tools to integrate
            </h1>
            <p className="text-xs text-[oklch(var(--muted-foreground))] mt-1 mb-6 leading-relaxed">
              Select what gather should listen to. Transcripts provide commitments; inboxes provide the reply watch loop.
            </p>

            {/* List of interactive selectable cards */}
            <div className="space-y-2.5 max-h-[380px] overflow-y-auto pr-1">
              {AVAILABLE_APPS.map((app) => {
                const isSelected = selectedApps.includes(app.id);
                return (
                  <div
                    key={app.id}
                    onClick={() => toggleApp(app.id)}
                    className={`p-3.5 rounded-xl border cursor-pointer transition-all duration-150 flex items-start gap-3.5 ${
                      isSelected
                        ? "bg-[oklch(var(--accent)/0.06)] border-[oklch(var(--accent)/0.4)] shadow-xs"
                        : "bg-[oklch(var(--background))] border-[oklch(var(--border))] hover:border-[oklch(var(--primary)/0.25)] opacity-85"
                    }`}
                  >
                    {/* Animated Checkbox */}
                    <div
                      className={`mt-0.5 w-5 h-5 rounded-md flex items-center justify-center transition-all ${
                        isSelected
                          ? "bg-[oklch(var(--accent))] text-white shadow-xs scale-100"
                          : "border border-[oklch(var(--border))] bg-[oklch(var(--card))]"
                      }`}
                    >
                      {isSelected && <Check size={12} strokeWidth={3} />}
                    </div>

                    <div className="flex-1 min-w-0">
                      <div className="flex items-center justify-between gap-2">
                        <div className="flex items-center gap-2">
                          <span className="text-base leading-none">{app.icon}</span>
                          <span className="text-xs font-semibold text-[oklch(var(--foreground))]">
                            {app.name}
                          </span>
                        </div>
                        {app.badge && (
                          <Badge variant={app.recommended ? "success" : "muted"}>
                            {app.badge}
                          </Badge>
                        )}
                      </div>
                      <p className="text-[0.6875rem] text-[oklch(var(--muted-foreground))] mt-1 leading-relaxed">
                        {app.description}
                      </p>
                    </div>
                  </div>
                );
              })}
            </div>

            <div className="mt-6 flex items-center justify-between pt-4 border-t border-[oklch(var(--border)/0.5)]">
              <Button
                variant="ghost"
                onClick={() => setCurrentStep(1)}
                className="gap-1.5"
              >
                <ArrowLeft size={14} /> Back
              </Button>
              <Button
                variant="primary"
                onClick={() => setCurrentStep(3)}
                disabled={selectedApps.length === 0}
                className="gap-2"
              >
                <span>Connect Selected Tools ({selectedApps.length})</span>
                <ArrowRight size={14} />
              </Button>
            </div>
          </div>
        )}

        {/* ── STEP 3: CONNECT SELECTED APPLICATIONS ── */}
        {currentStep === 3 && (
          <div className="bg-[oklch(var(--card))] border border-[oklch(var(--border))] rounded-2xl p-8 shadow-sm animate-slide-up">
            <div className="flex items-center justify-between mb-1">
              <Badge variant="accent">
                <ShieldCheck size={11} className="mr-0.5" /> Direct Local Auth
              </Badge>
              <span className="text-xs text-[oklch(var(--muted-foreground))]">
                {Object.values(connectedApps).filter(Boolean).length} of {selectedApps.length} active
              </span>
            </div>
            <h1 className="text-xl font-semibold text-[oklch(var(--foreground))] tracking-tight">
              Authorize your applications
            </h1>
            <p className="text-xs text-[oklch(var(--muted-foreground))] mt-1 mb-6 leading-relaxed">
              Connect accounts or configure access. Credentials are saved directly to your OS keychain. You can also skip any and connect later.
            </p>

            <div className="space-y-4 max-h-[380px] overflow-y-auto pr-1">
              {selectedApps.map((appId) => {
                const app = AVAILABLE_APPS.find((a) => a.id === appId);
                if (!app) return null;
                const isConnected = Boolean(connectedApps[appId]);
                const isConnecting = connectingApp === appId;

                return (
                  <div
                    key={appId}
                    className="p-4 rounded-xl border border-[oklch(var(--border))] bg-[oklch(var(--background))]"
                  >
                    <div className="flex items-center justify-between mb-3">
                      <div className="flex items-center gap-2.5">
                        <span className="text-lg leading-none">{app.icon}</span>
                        <div>
                          <span className="text-xs font-semibold text-[oklch(var(--foreground))] block">
                            {app.name}
                          </span>
                          <span className="text-[0.6875rem] text-[oklch(var(--muted-foreground))]">
                            {app.category}
                          </span>
                        </div>
                      </div>

                      {isConnected ? (
                        <span className="inline-flex items-center gap-1 text-[0.6875rem] font-medium text-[oklch(var(--success))] bg-[oklch(var(--success)/0.12)] px-2.5 py-0.5 rounded-full border border-[oklch(var(--success)/0.25)]">
                          <CheckCircle2 size={11} /> Connected
                        </span>
                      ) : (
                        <span className="text-[0.6875rem] text-[oklch(var(--muted-foreground))] bg-[oklch(var(--muted))] px-2 py-0.5 rounded-full">
                          Pending Auth
                        </span>
                      )}
                    </div>

                    {/* Specific auth controls per provider */}
                    {appId === "gmail" && (
                      <div className="pt-2 border-t border-[oklch(var(--border)/0.5)]">
                        {isConnected ? (
                          <div className="flex items-center justify-between text-xs text-[oklch(var(--muted-foreground))]">
                            <span>Authenticated as <strong className="text-[oklch(var(--foreground))]">{email || "user@company.com"}</strong></span>
                            <span className="text-[0.6875rem] text-[oklch(var(--accent))]">Sync cursor active (5m)</span>
                          </div>
                        ) : (
                          <div className="flex items-center justify-between gap-3">
                            <span className="text-[0.6875rem] text-[oklch(var(--muted-foreground))]">
                              OAuth 2.0 with read-only inbox cursor and draft creation.
                            </span>
                            <Button
                              variant="outline"
                              onClick={() => handleConnectApp("gmail")}
                              disabled={isConnecting}
                              className="text-xs h-7 px-3 flex-none"
                            >
                              {isConnecting ? <Spinner size={12} /> : <Plug size={12} />}
                              Authorize Gmail
                            </Button>
                          </div>
                        )}
                      </div>
                    )}

                    {appId === "fireflies" && (
                      <div className="pt-2 border-t border-[oklch(var(--border)/0.5)] space-y-2">
                        {isConnected ? (
                          <div className="flex items-center justify-between text-xs text-[oklch(var(--muted-foreground))]">
                            <span>Fireflies GraphQL connected · Auto-polling transcripts</span>
                            <span className="text-[0.6875rem] text-[oklch(var(--accent))]">Active</span>
                          </div>
                        ) : (
                          <div className="flex items-center gap-2">
                            <input
                              type="password"
                              value={firefliesKey}
                              onChange={(e) => setFirefliesKey(e.target.value)}
                              placeholder="Paste Fireflies API Key..."
                              className="flex-1 h-7 px-2.5 text-xs bg-[oklch(var(--card))] border border-[oklch(var(--border))] rounded-md text-[oklch(var(--foreground))] placeholder:text-[oklch(var(--muted-foreground))] focus:outline-none"
                            />
                            <Button
                              variant="outline"
                              onClick={() => handleConnectApp("fireflies")}
                              disabled={isConnecting}
                              className="text-xs h-7 px-3 flex-none"
                            >
                              {isConnecting ? <Spinner size={12} /> : <Key size={12} />}
                              Connect API
                            </Button>
                          </div>
                        )}
                      </div>
                    )}

                    {appId === "calendar" && (
                      <div className="pt-2 border-t border-[oklch(var(--border)/0.5)] flex items-center justify-between">
                        <span className="text-[0.6875rem] text-[oklch(var(--muted-foreground))]">
                          Links with your primary Google Calendar account.
                        </span>
                        {!isConnected ? (
                          <Button
                            variant="outline"
                            onClick={() => handleConnectApp("calendar")}
                            disabled={isConnecting}
                            className="text-xs h-7 px-3"
                          >
                            {isConnecting ? <Spinner size={12} /> : <Calendar size={12} />}
                            Link Calendar
                          </Button>
                        ) : (
                          <span className="text-xs text-[oklch(var(--success))]">Linked ✓</span>
                        )}
                      </div>
                    )}

                    {appId === "slack" && (
                      <div className="pt-2 border-t border-[oklch(var(--border)/0.5)] flex items-center justify-between">
                        <span className="text-[0.6875rem] text-[oklch(var(--muted-foreground))]">
                          Socket Mode app token for interactive DM approvals.
                        </span>
                        {!isConnected ? (
                          <Button
                            variant="outline"
                            onClick={() => handleConnectApp("slack")}
                            disabled={isConnecting}
                            className="text-xs h-7 px-3"
                          >
                            {isConnecting ? <Spinner size={12} /> : <MessageSquare size={12} />}
                            Configure
                          </Button>
                        ) : (
                          <span className="text-xs text-[oklch(var(--success))]">Linked ✓</span>
                        )}
                      </div>
                    )}

                    {appId === "offline_files" && (
                      <div className="pt-2 border-t border-[oklch(var(--border)/0.5)] text-xs text-[oklch(var(--muted-foreground))]">
                        Local transcript ingestion enabled. You can drag and drop transcripts into gather anytime.
                      </div>
                    )}
                  </div>
                );
              })}
            </div>

            <div className="mt-6 flex items-center justify-between pt-4 border-t border-[oklch(var(--border)/0.5)]">
              <Button
                variant="ghost"
                onClick={() => setCurrentStep(2)}
                className="gap-1.5"
              >
                <ArrowLeft size={14} /> Back
              </Button>
              <div className="flex items-center gap-2">
                <Button
                  variant="ghost"
                  onClick={() => setCurrentStep(4)}
                  className="text-xs text-[oklch(var(--muted-foreground))]"
                >
                  Configure Remaining Later
                </Button>
                <Button
                  variant="primary"
                  onClick={() => setCurrentStep(4)}
                  className="gap-2"
                >
                  <span>Continue to AI Setup</span>
                  <ArrowRight size={14} />
                </Button>
              </div>
            </div>
          </div>
        )}

        {/* ── STEP 4: AI REASONING CHAIN ── */}
        {currentStep === 4 && (
          <div className="bg-[oklch(var(--card))] border border-[oklch(var(--border))] rounded-2xl p-8 shadow-sm animate-slide-up">
            <div className="flex items-center gap-2 mb-2">
              <Badge variant="accent">
                <Cpu size={11} className="mr-0.5" /> Free Hosted Reasoning
              </Badge>
            </div>
            <h1 className="text-xl font-semibold text-[oklch(var(--foreground))] tracking-tight">
              AI model chain
            </h1>
            <p className="text-xs text-[oklch(var(--muted-foreground))] mt-1 mb-6 leading-relaxed">
              gather uses free hosted models for commitment extraction. No local GPU required, and nothing is used to train third-party models.
            </p>

            {/* Model Chain Card */}
            <div className="border border-[oklch(var(--border))] rounded-xl p-4 bg-[oklch(var(--background))] space-y-3">
              <div className="flex items-center justify-between">
                <span className="text-xs font-semibold text-[oklch(var(--foreground))]">
                  Default Failover Chain
                </span>
                <span className="text-[0.6875rem] text-[oklch(var(--accent))] font-medium">
                  Verified Free Tiers
                </span>
              </div>

              <div className="space-y-2">
                <div className="flex items-center justify-between p-2.5 rounded-lg bg-[oklch(var(--card))] border border-[oklch(var(--border)/0.6)]">
                  <div className="flex items-center gap-2 font-mono text-xs text-[oklch(var(--foreground))]">
                    <span className="w-5 h-5 rounded-full bg-[oklch(var(--primary)/0.1)] text-[oklch(var(--primary))] flex items-center justify-center text-[0.6875rem] font-bold">1</span>
                    groq:openai/gpt-oss-120b
                  </div>
                  <Badge variant="success">~1.2s Latency</Badge>
                </div>

                <div className="flex items-center justify-between p-2.5 rounded-lg bg-[oklch(var(--card))] border border-[oklch(var(--border)/0.6)]">
                  <div className="flex items-center gap-2 font-mono text-xs text-[oklch(var(--foreground))]">
                    <span className="w-5 h-5 rounded-full bg-[oklch(var(--muted))] text-[oklch(var(--muted-foreground))] flex items-center justify-center text-[0.6875rem] font-bold">2</span>
                    openrouter:nvidia/nemotron-3.5-lightning:free
                  </div>
                  <Badge variant="muted">Fallback</Badge>
                </div>
              </div>
            </div>

            {/* Fixture Mode Option */}
            <div className="mt-4 p-4 rounded-xl border border-[oklch(var(--border)/0.7)] bg-[oklch(var(--muted)/0.3)] flex items-start gap-3 cursor-pointer" onClick={() => setUseFixtureAi(!useFixtureAi)}>
              <div
                className={`mt-0.5 w-4 h-4 rounded flex items-center justify-center border transition-all ${
                  useFixtureAi
                    ? "bg-[oklch(var(--primary))] border-[oklch(var(--primary))] text-white"
                    : "border-[oklch(var(--border))] bg-[oklch(var(--card))]"
                }`}
              >
                {useFixtureAi && <Check size={10} strokeWidth={3} />}
              </div>
              <div className="flex-1">
                <div className="flex items-center gap-2">
                  <span className="text-xs font-semibold text-[oklch(var(--foreground))]">
                    Enable offline fixture demo mode
                  </span>
                  <Badge variant="muted">Recommended for first run</Badge>
                </div>
                <p className="text-[0.6875rem] text-[oklch(var(--muted-foreground))] mt-0.5 leading-relaxed">
                  Allows you to test all extraction, proposal, and approval loops right now using bundled meeting fixtures without needing API keys.
                </p>
              </div>
            </div>

            <div className="mt-6 flex items-center justify-between pt-4 border-t border-[oklch(var(--border)/0.5)]">
              <Button
                variant="ghost"
                onClick={() => setCurrentStep(3)}
                className="gap-1.5"
              >
                <ArrowLeft size={14} /> Back
              </Button>
              <Button
                variant="primary"
                onClick={() => setCurrentStep(5)}
                className="gap-2"
              >
                <span>Continue to Summary</span>
                <ArrowRight size={14} />
              </Button>
            </div>
          </div>
        )}

        {/* ── STEP 5: LAUNCH & CONFIRMATION ── */}
        {currentStep === 5 && (
          <div className="bg-[oklch(var(--card))] border border-[oklch(var(--border))] rounded-2xl p-8 shadow-sm animate-slide-up text-center">
            <div className="w-14 h-14 rounded-2xl bg-[oklch(var(--success)/0.14)] text-[oklch(var(--success))] mx-auto flex items-center justify-center mb-4 border border-[oklch(var(--success)/0.3)] shadow-xs">
              <CheckCircle2 size={32} />
            </div>

            <h1 className="text-xl font-semibold text-[oklch(var(--foreground))] tracking-tight">
              Ready to gather
            </h1>
            <p className="text-xs text-[oklch(var(--muted-foreground))] mt-1 mb-6 max-w-sm mx-auto leading-relaxed">
              Your local vault is configured. Every task extracted from meetings will require your explicit approval before anything is tracked or sent.
            </p>

            {/* Ready summary */}
            <div className="p-4 rounded-xl border border-[oklch(var(--border))] bg-[oklch(var(--background))] text-left space-y-2.5 mb-6">
              <div className="flex items-center justify-between text-xs">
                <span className="text-[oklch(var(--muted-foreground))]">Operator:</span>
                <span className="font-semibold text-[oklch(var(--foreground))]">{name || "Alex Mercer"}</span>
              </div>
              <div className="flex items-center justify-between text-xs">
                <span className="text-[oklch(var(--muted-foreground))]">Workspace:</span>
                <span className="font-medium text-[oklch(var(--foreground))]">{workspace || "Personal Workspace"}</span>
              </div>
              <div className="flex items-center justify-between text-xs">
                <span className="text-[oklch(var(--muted-foreground))]">Active Connectors:</span>
                <span className="font-medium text-[oklch(var(--accent))]">
                  {selectedApps.length} tools ({selectedApps.join(", ")})
                </span>
              </div>
              <div className="flex items-center justify-between text-xs">
                <span className="text-[oklch(var(--muted-foreground))]">Reasoning Engine:</span>
                <span className="font-medium text-[oklch(var(--foreground))]">
                  {useFixtureAi ? "Offline Fixture Mode (Instant demo)" : "Groq / OpenRouter Free Tier"}
                </span>
              </div>
              <div className="flex items-center justify-between text-xs pt-1.5 border-t border-[oklch(var(--border)/0.5)]">
                <span className="text-[oklch(var(--muted-foreground))]">Safety Guarantee:</span>
                <span className="text-[oklch(var(--success))] font-medium flex items-center gap-1">
                  <ShieldCheck size={13} /> 100% Human Approval Gate
                </span>
              </div>
            </div>

            <Button
              variant="primary"
              onClick={handleFinish}
              className="w-full h-10 text-sm gap-2 font-semibold shadow-md"
            >
              <span>Enter gather Workspace</span>
              <ArrowRight size={16} />
            </Button>
          </div>
        )}
      </div>

      {/* Footer Info */}
      <div className="max-w-2xl w-full mx-auto flex items-center justify-between text-[0.6875rem] text-[oklch(var(--muted-foreground))] pt-4">
        <span>gather v0.1.0 · Local-first desktop assistant</span>
        <span className="flex items-center gap-1">
          <ShieldCheck size={12} /> OS Keychain Secured
        </span>
      </div>
    </div>
  );
}
