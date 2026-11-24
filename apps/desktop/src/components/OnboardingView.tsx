import { useState } from "react";
import { ArrowRight, AudioLines } from "lucide-react";
import { engine, flags } from "../engine";
import { Button, ErrorNotice, Spinner } from "./ui";

export function OnboardingView({ onComplete }: { onComplete: () => void }) {
  const [workspace, setWorkspace] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");

  async function finish() {
    setPending(true);
    setError("");
    try {
      const label = workspace.trim() || "Personal workspace";
      await flags.set("user_workspace", label);
      const result = await engine.onboarding_complete();
      if (!result.persisted) await flags.set("onboarded", "true");
      localStorage.setItem("gather_workspace", label);
      onComplete();
    } catch (err) {
      setError(String(err));
    } finally {
      setPending(false);
    }
  }

  return (
    <section className="setup">
      <div className="setup-card">
        <div className="brand-mark"><AudioLines size={23} /></div>
        <h1>Set up your workspace</h1>
        <p>Bring meeting transcripts into Gather, review the commitments, and track the follow-ups you approve.</p>
        <form onSubmit={(event) => { event.preventDefault(); void finish(); }}>
          <label htmlFor="workspace-name">Workspace name</label>
          <input id="workspace-name" className="field" value={workspace} maxLength={80}
            onChange={(event) => setWorkspace(event.target.value)} placeholder="Personal workspace" />
          <Button type="submit" disabled={pending}>
            {pending ? <Spinner /> : null} Open workspace <ArrowRight size={16} />
          </Button>
        </form>
        <p className="setup-note">You can try a sample meeting or connect your accounts after setup.</p>
        {error && <ErrorNotice>{error}</ErrorNotice>}
      </div>
    </section>
  );
}
