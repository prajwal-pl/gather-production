/**
 * ConnectionsView — manage provider connections (Fireflies, Gmail).
 */
import { useEffect, useState } from "react";
import { RefreshCw, Plug, AlertCircle, CheckCircle2, Clock, ShieldCheck } from "lucide-react";
import { engine, type Connection } from "../engine";
import { Badge, Button, EmptyState, Spinner, formatDate } from "./ui";

const PROVIDER_LABELS: Record<string, string> = {
  fireflies: "Fireflies.ai",
  gmail: "Google Workspace / Gmail",
};

const PROVIDER_ICONS: Record<string, string> = {
  fireflies: "🎙️",
  gmail: "✉️",
};

function ConnectionCard({ conn }: { conn: Connection }) {
  const statusVariant =
    conn.status === "active"
      ? "success"
      : conn.status === "error"
      ? "error"
      : "muted";

  return (
    <div className="border border-[oklch(var(--border))] bg-[oklch(var(--card))] rounded-xl p-5 shadow-xs transition-all hover:border-[oklch(var(--primary)/0.25)]">
      <div className="flex items-start justify-between gap-4">
        <div className="flex items-center gap-3">
          <span className="text-2xl leading-none p-2 rounded-lg bg-[oklch(var(--background))] border border-[oklch(var(--border)/0.5)]">
            {PROVIDER_ICONS[conn.provider] || "🔌"}
          </span>
          <div>
            <p className="text-xs font-semibold text-[oklch(var(--foreground))]">
              {PROVIDER_LABELS[conn.provider] || conn.provider}
            </p>
            {conn.user_id && (
              <p className="text-[0.6875rem] text-[oklch(var(--muted-foreground))] mt-0.5">
                {conn.user_id}
              </p>
            )}
          </div>
        </div>
        <Badge variant={statusVariant}>
          {conn.status === "active" ? (
            <CheckCircle2 size={10} className="mr-0.5" />
          ) : conn.status === "error" ? (
            <AlertCircle size={10} className="mr-0.5" />
          ) : (
            <Clock size={10} className="mr-0.5" />
          )}
          {conn.status}
        </Badge>
      </div>

      {/* Sync times */}
      <div className="mt-4 pt-3 border-t border-[oklch(var(--border)/0.5)] grid grid-cols-2 gap-3">
        <div>
          <p className="text-[0.6875rem] uppercase tracking-wide text-[oklch(var(--muted-foreground))] mb-0.5">
            Last cursor sync
          </p>
          <p className="text-xs text-[oklch(var(--foreground))] font-medium">
            {formatDate(conn.last_sync_at)}
          </p>
        </div>
        <div>
          <p className="text-[0.6875rem] uppercase tracking-wide text-[oklch(var(--muted-foreground))] mb-0.5">
            Next scheduled poll
          </p>
          <p className="text-xs text-[oklch(var(--foreground))] font-medium">
            {formatDate(conn.next_sync_at)}
          </p>
        </div>
      </div>

      {conn.last_error && (
        <div className="mt-3 p-2.5 bg-[oklch(var(--destructive)/0.08)] border border-[oklch(var(--destructive)/0.2)] rounded-lg text-xs text-[oklch(var(--destructive))]">
          {conn.last_error}
        </div>
      )}
    </div>
  );
}

export function ConnectionsView() {
  const [connections, setConnections] = useState<Connection[]>([]);
  const [loading, setLoading] = useState(true);
  const [syncing, setSyncing] = useState(false);

  async function load() {
    setLoading(true);
    try {
      const { connections } = await engine.connections.list();
      setConnections(connections);
    } finally {
      setLoading(false);
    }
  }

  async function handleSyncNow() {
    setSyncing(true);
    try {
      await engine.scheduler.runNow();
      await load();
    } finally {
      setSyncing(false);
    }
  }

  useEffect(() => {
    load();
  }, []);

  return (
    <div className="flex flex-col h-full bg-[oklch(var(--background))]">
      {/* Toolbar */}
      <div className="flex items-center justify-between px-6 py-4 border-b border-[oklch(var(--border))]">
        <div>
          <h2 className="text-sm font-semibold text-[oklch(var(--foreground))] tracking-tight">
            Connected Data Sources
          </h2>
          {!loading && (
            <p className="text-xs text-[oklch(var(--muted-foreground))] mt-0.5">
              {connections.filter((c) => c.status === "active").length} active provider connectors
            </p>
          )}
        </div>
        <div className="flex items-center gap-2">
          <Button variant="outline" onClick={handleSyncNow} disabled={syncing} className="text-xs h-8">
            {syncing ? <Spinner size={12} /> : <RefreshCw size={12} />}
            Sync now
          </Button>
        </div>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-y-auto px-6 py-6 space-y-4">
        {loading ? (
          <div className="flex justify-center py-20">
            <Spinner size={24} />
          </div>
        ) : connections.length === 0 ? (
          <EmptyState
            icon={<Plug size={36} />}
            title="No connections active"
            description="Authorize Fireflies or Gmail to start ingesting transcripts and watching email threads."
          />
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {connections.map((conn) => (
              <ConnectionCard key={conn.id} conn={conn} />
            ))}
          </div>
        )}

        {/* Polling Info Banner */}
        <div className="mt-6 p-4 rounded-xl border border-[oklch(var(--border))] bg-[oklch(var(--card))] shadow-xs flex items-start gap-3">
          <ShieldCheck size={16} className="text-[oklch(var(--accent))] flex-none mt-0.5" />
          <div>
            <p className="text-xs font-semibold text-[oklch(var(--foreground))] mb-0.5">
              Desktop Ingestion Protocol
            </p>
            <p className="text-xs text-[oklch(var(--muted-foreground))] leading-relaxed">
              gather polls Fireflies and Gmail via cursor-based synchronization (historyId). Webhooks and inbound ports are not required, protecting your laptop from public internet exposure.
            </p>
          </div>
        </div>
      </div>
    </div>
  );
}
