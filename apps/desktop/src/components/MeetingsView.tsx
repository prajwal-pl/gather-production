import { useEffect, useState } from "react";
import { ArrowLeft, FileText } from "lucide-react";
import { engine, type Meeting, type Task } from "../engine";
import { Button, EmptyState, ErrorNotice, Spinner, formatDate } from "./ui";

export function MeetingsView({ initialMeetingId }: { initialMeetingId?: string }) {
  const [selected, setSelected] = useState(initialMeetingId);
  const [meetings, setMeetings] = useState<Meeting[]>([]);
  const [detail, setDetail] = useState<{ meeting: Meeting; tasks: Task[] }>();
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError("");
    if (selected) {
      void engine.meetings.get(selected).then((result) => {
        if (!cancelled) setDetail(result);
      }).catch((err) => { if (!cancelled) setError(String(err)); })
        .finally(() => { if (!cancelled) setLoading(false); });
    } else {
      void engine.meetings.list().then((result) => {
        if (!cancelled) setMeetings(result.meetings);
      }).catch((err) => { if (!cancelled) setError(String(err)); })
        .finally(() => { if (!cancelled) setLoading(false); });
    }
    return () => { cancelled = true; };
  }, [selected]);

  return (
    <section className="page">
      <header className="page-header"><div><h1>Meetings</h1><p>Transcripts and the tasks they produced</p></div></header>
      <div className="page-body">
        {selected && <Button variant="ghost" onClick={() => setSelected(undefined)}><ArrowLeft size={16} /> All meetings</Button>}
        {error ? <ErrorNotice>{error}</ErrorNotice> : loading ? <Spinner /> : selected && detail ? (
          <article className="surface meeting-detail">
            <h2>{detail.meeting.title}</h2>
            <div className="meeting-transcript">{detail.meeting.transcript || "Transcript unavailable"}</div>
            <h3>Tracked tasks</h3>
            {detail.tasks.length ? <ul>{detail.tasks.map((task) => (
              <li key={task.id}><strong>{task.title}</strong>{task.source_quote && <blockquote>{task.source_quote}</blockquote>}</li>
            ))}</ul> : <p>No tracked tasks from this meeting yet.</p>}
          </article>
        ) : meetings.length ? (
          <div className="surface meeting-list">
            {meetings.map((meeting) => (
              <button key={meeting.id} onClick={() => setSelected(meeting.id)}>
                <FileText size={17} /><strong>{meeting.title}</strong><span>{formatDate(meeting.started_at)}</span>
              </button>
            ))}
          </div>
        ) : <EmptyState icon={<FileText size={24} />} title="No meetings yet" description="Import a transcript from the review inbox or sync Fireflies." />}
      </div>
    </section>
  );
}
