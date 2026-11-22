/**
 * ConnectionsView — manage provider connections (Fireflies, Gmail).
 */
import { useEffect, useState } from "react";
import { RefreshCw, Plug, AlertCircle, CheckCircle2, Clock } from "lucide-react";
import { engine, type Connection } from "../engine";
import { Badge, Button, EmptyState, Spinner, formatDate } from "./ui";

const PROVIDER_LABELS: Record<string, string> = {
  fireflies: "Fireflies",
  gmail: "Gmail",
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
    <div className="border border-[oklch(var(--border)/0.3)] bg-[oklch(var(--card))] p-4">
      <div className="flex items-start justify-between gap-4">
        <div className="flex items-center gap-3">
          <span className="text-2xl leading-none">{PROVIDER_ICONS[conn.provider] || "🔌"}</span>
          <div>
            <p className="text-[0.875rem] font-semibold text-[oklch(var(--foreground))]">
              {PROVIDER_LABELS[conn.provider] || conn.provider}
            </p>
            {conn.user_id && (
              <p className="text-[0.75rem] text-[oklch(var(--muted-foreground))]">
                {conn.user_id}
              </p>
            )}
          </div>
        </div>
        <Badge variant={statusVariant}>
          {conn.status === "active" ? (
            <CheckCircle2 size={10} className="mr-1" />
          ) : conn.status === "error" ? (
            <AlertCircle size={10} className="mr-1" />
          ) : (
            <Clock size={10} className="mr-1" />
          )}
          {conn.status}
        </Badge>
      </div>

      {/* Sync times */}
      <div className="mt-3 grid grid-cols-2 gap-2">
        <div>
          <p className="text-[0.6875rem] uppercase tracking-wide text-[oklch(var(--muted-foreground))] mb-0.5">
            Last sync
          </p>
          <p className="text-[0.75rem] text-[oklch(var(--foreground))]">
            {formatDate(conn.last_sync_at)}
          </p>
        </div>
        <div>
          <p className="text-[0.6875rem] uppercase tracking-wide text-[oklch(var(--muted-foreground))] mb-0.5">
            Next sync
          </p>
          <p className="text-[0.75rem] text-[oklch(var(--foreground))]">
            {formatDate(conn.next_sync_at)}
          </p>
        </div>
      </div>

      {conn.last_error && (
        <div className="mt-3 p-2 bg-[oklch(var(--destructive)/0.08)] border border-[oklch(var(--destructive)/0.2)] text-[0.75rem] text-[oklch(var(--destructive))]">
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
    <div className="flex flex-col h-full">
      {/* Toolbar */}
      <div className="flex items-center justify-between px-6 py-3 border-b border-[oklch(var(--border)/0.2)]">
        <div>
          <h2 className="text-[0.9375rem] font-semibold text-[oklch(var(--foreground))]">
            Connections
          </h2>
          {!loading && (
            <p className="text-[0.75rem] text-[oklch(var(--muted-foreground))] mt-0.5">
              {connections.filter((c) => c.status === "active").length} active
            </p>
          )}
        </div>
        <div className="flex items-center gap-2">
          <Button variant="outline" onClick={handleSyncNow} disabled={syncing}>
            {syncing ? <Spinner size={13} /> : <RefreshCw size={13} />}
            Sync now
          </Button>
        </div>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-y-auto px-6 py-5">
        {loading ? (
          <div className="flex justify-center py-16">
            <Spinner size={24} />
          </div>
        ) : connections.length === 0 ? (
          <EmptyState
            icon={<Plug size={48} />}
            title="No connections"
            description="Connect Fireflies and Gmail to start ingesting meetings and watching your inbox."
          />
        ) : (
          <div className="space-y-3">
            {connections.map((conn) => (
              <ConnectionCard key={conn.id} conn={conn} />
            ))}
          </div>
        )}

        {/* Info box */}
        <div className="mt-6 p-4 border border-[oklch(var(--border)/0.2)] bg-[oklch(var(--muted)/0.3)]">
          <p className="text-[0.75rem] font-medium text-[oklch(var(--foreground))] mb-1">
            How polling works
          </p>
          <p className="text-[0.75rem] text-[oklch(var(--muted-foreground))] leading-relaxed">
            gather polls Fireflies every 5 minutes for new transcripts and Gmail
            every 5 minutes using a historyId cursor. No webhook server is
            required. The engine runs as a tray-resident process.
          </p>
        </div>
      </div>
    </div>
  );
}
