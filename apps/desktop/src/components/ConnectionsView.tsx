import { useEffect, useState } from "react";
import { Mail, Mic, RefreshCw } from "lucide-react";
import { engine, type Connection } from "../engine";
import { Badge, Button, ErrorNotice, Spinner, formatDate } from "./ui";

export function ConnectionsView({ onOpenSettings }: { onOpenSettings: () => void }) {
  const [connections, setConnections] = useState<Connection[]>([]);
  const [key, setKey] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string>();
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  async function load() {
    const result = await engine.connections.list();
    setConnections(result.connections);
  }

  useEffect(() => {
    void load().catch((err) => setError(String(err))).finally(() => setLoading(false));
  }, []);

  async function connect(provider: "fireflies" | "gmail") {
    setBusy(provider);
    setError("");
    setNotice("");
    try {
      await engine.connections.connect(provider, provider === "fireflies" ? { api_key: key.trim() } : undefined);
      if (provider === "fireflies") setKey("");
      await load();
    } catch (err) {
      setError(String(err));
    } finally {
      setBusy(undefined);
    }
  }

  async function sync() {
    setBusy("sync");
    setError("");
    setNotice("");
    try {
      const result = await engine.scheduler.runNow();
      const events = result.events ?? result.poll?.events ?? 0;
      setNotice(!result.accepted ? "A sync is already in progress." : events ? `${events} new events processed.` : "No new events from sources due for polling.");
      await load();
    } catch (err) {
      setError(String(err));
    } finally {
      setBusy(undefined);
    }
  }

  return (
    <section className="page">
      <header className="page-header">
        <div><h1>Connections</h1><p>Import meetings and match email replies to tasks</p></div>
        <Button variant="outline" onClick={sync} disabled={loading || !!busy || !connections.some((row) => row.status === "active")}>
          {busy === "sync" ? <Spinner /> : <RefreshCw size={15} />} Sync now
        </Button>
      </header>
      <div className="page-body space-y-4">
        {error && <ErrorNotice>{error}</ErrorNotice>}
        {notice && <p className="surface px-4 py-3 text-sm" role="status">{notice}</p>}
        {loading ? <Spinner /> : (["fireflies", "gmail"] as const).map((provider) => {
          const rows = connections.filter((connection) => connection.provider === provider);
          const fireflies = provider === "fireflies";
          const Icon = fireflies ? Mic : Mail;
          return (
            <article className="surface p-5 space-y-4" key={provider}>
              <div className="flex gap-3 items-start">
                <Icon size={21} className="mt-1 text-[oklch(var(--accent))]" />
                <div>
                  <h2 className="font-semibold">{fireflies ? "Fireflies" : "Gmail"}</h2>
                  <p className="text-sm text-[oklch(var(--muted-foreground))]">{fireflies ? "Bring in completed meeting transcripts." : "Match changes and replies in your inbox to tracked tasks."}</p>
                </div>
              </div>
              {rows.map((row) => (
                <div key={row.id} className="border-t pt-3 space-y-2 text-sm">
                  <Badge variant={row.status === "active" ? "success" : "warn"}>{row.status.replace(/_/g, " ")}</Badge>
                  <p className="text-[oklch(var(--muted-foreground))]">Last sync {formatDate(row.last_sync_at)} · Next poll {formatDate(row.next_sync_at)}</p>
                  {row.last_error && <ErrorNotice>{row.last_error}</ErrorNotice>}
                </div>
              ))}
              {fireflies ? (
                <form onSubmit={(event) => { event.preventDefault(); void connect("fireflies"); }}>
                  <label htmlFor="fireflies-key" className="text-sm font-medium">Fireflies API key</label>
                  <div className="flex flex-wrap items-center gap-2 mt-1.5">
                    <input className="field max-w-xs" id="fireflies-key" type="password" autoComplete="off" value={key} onChange={(event) => setKey(event.target.value)} />
                    <Button type="submit" variant="outline" disabled={!!busy || !key.trim()}>{busy === provider && <Spinner />}{rows.length ? "Update key" : "Connect Fireflies"}</Button>
                  </div>
                </form>
              ) : (
                <div className="flex flex-wrap gap-2">
                  <Button variant="outline" onClick={() => connect("gmail")} disabled={!!busy}>{busy === provider && <Spinner />}{rows.length ? "Reconnect Gmail" : "Sign in with Google"}</Button>
                  <Button variant="ghost" onClick={onOpenSettings}>Configure Google credentials</Button>
                </div>
              )}
            </article>
          );
        })}
      </div>
    </section>
  );
}
