/**
 * AgentView — a local, read-only assistant for the workspace.
 *
 * The backend planner chooses tools (tasks, meetings, proposals, connections,
 * setup checks) and answers only from their observations. Mutations stay in
 * the existing Inbox / Tasks / Connections / Settings surfaces.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import type { KeyboardEvent, ReactNode } from "react";
import { Bot, Plus, Send, ShieldCheck, Sparkles } from "lucide-react";
import { engine, type AgentToolCall } from "../engine";
import { Badge, Button, Spinner } from "./ui";

interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  toolCalls?: AgentToolCall[];
  fixture?: boolean;
  degraded?: boolean;
  error?: boolean;
}

const STORAGE_KEY = "gather_agent_chat_v1";

const SUGGESTIONS = [
  "What needs my attention today?",
  "What is overdue?",
  "Summarize my latest meeting",
  "Show pending approvals",
  "Is my workspace set up correctly?",
  "Who owns the most open tasks?",
];

function newId(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return crypto.randomUUID();
  }
  return `${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function loadMessages(): ChatMessage[] {
  try {
    const raw = sessionStorage.getItem(STORAGE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed.filter(
      (item): item is ChatMessage =>
        item &&
        typeof item.id === "string" &&
        (item.role === "user" || item.role === "assistant") &&
        typeof item.content === "string",
    );
  } catch {
    return [];
  }
}

function ToolCalls({ calls }: { calls?: AgentToolCall[] }) {
  if (!calls?.length) return null;
  return (
    <div className="flex flex-wrap gap-1.5 mb-2">
      {calls.map((call, index) => (
        <span key={`${call.tool}-${index}`} title={call.error || call.tool}>
          <Badge variant={call.ok ? "muted" : "error"}>
            <Sparkles size={9} />
            {call.label || call.tool}
          </Badge>
        </span>
      ))}
    </div>
  );
}

/** Minimal markdown: paragraphs, bullet lists and **bold**. */
function MarkdownLite({ text }: { text: string }) {
  const blocks: ReactNode[] = [];
  const lines = text.split("\n");
  let list: string[] = [];

  const inline = (value: string) =>
    value
      .split(/(\*\*[^*]+\*\*)/g)
      .filter(Boolean)
      .map((part, index) =>
        part.startsWith("**") && part.endsWith("**") ? (
          <strong key={index} className="font-semibold">
            {part.slice(2, -2)}
          </strong>
        ) : (
          <span key={index}>{part}</span>
        ),
      );

  const flushList = () => {
    if (!list.length) return;
    blocks.push(
      <ul key={`list-${blocks.length}`} className="list-disc pl-5 space-y-1">
        {list.map((item, index) => (
          <li key={index}>{inline(item)}</li>
        ))}
      </ul>,
    );
    list = [];
  };

  lines.forEach((raw, index) => {
    const line = raw.trim();
    if (!line) {
      flushList();
      if (blocks.length && index !== lines.length - 1) {
        blocks.push(<div key={`gap-${index}`} className="h-1.5" />);
      }
      return;
    }
    if (line.startsWith("- ")) {
      list.push(line.slice(2));
      return;
    }
    flushList();
    if (line.startsWith("### ")) {
      blocks.push(
        <p key={`h-${index}`} className="font-semibold">
          {inline(line.slice(4))}
        </p>,
      );
      return;
    }
    blocks.push(<p key={`p-${index}`}>{inline(line)}</p>);
  });
  flushList();

  return (
    <div className="break-words space-y-1 text-[0.8125rem] leading-relaxed text-[oklch(var(--foreground))]">
      {blocks}
    </div>
  );
}

function MessageBubble({ message }: { message: ChatMessage }) {
  if (message.role === "user") {
    return (
      <div className="flex justify-end">
        <div className="max-w-[85%] rounded-2xl rounded-br-sm bg-[oklch(var(--primary))] text-[oklch(var(--primary-foreground))] px-4 py-2.5 shadow-xs">
          <p className="text-[0.8125rem] leading-relaxed whitespace-pre-wrap">{message.content}</p>
        </div>
      </div>
    );
  }

  return (
    <div className="flex gap-2.5">
      <div className="mt-0.5 w-7 h-7 flex-none rounded-lg bg-[oklch(var(--accent)/0.14)] text-[oklch(var(--accent))] border border-[oklch(var(--accent)/0.25)] flex items-center justify-center">
        <Bot size={14} />
      </div>
      <div
        className={`min-w-0 flex-1 rounded-xl border px-4 py-3 shadow-xs ${
          message.error
            ? "border-[oklch(var(--destructive)/0.3)] bg-[oklch(var(--destructive)/0.05)]"
            : "border-[oklch(var(--border))] bg-[oklch(var(--card))]"
        }`}
      >
        <ToolCalls calls={message.toolCalls} />
        <MarkdownLite text={message.content} />
        {(message.fixture || message.degraded) && (
          <div className="mt-2 flex items-center gap-2">
            {message.fixture && <Badge variant="warn">offline fixture</Badge>}
            {message.degraded && <Badge variant="warn">local data fallback</Badge>}
          </div>
        )}
      </div>
    </div>
  );
}

export function AgentView() {
  const [messages, setMessages] = useState<ChatMessage[]>(loadMessages);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);

  const history = useMemo(
    () =>
      messages
        .filter((message) => !message.error)
        .slice(-12)
        .map((message) => ({ role: message.role, content: message.content })),
    [messages],
  );

  useEffect(() => {
    try {
      sessionStorage.setItem(STORAGE_KEY, JSON.stringify(messages.slice(-50)));
    } catch {
      /* session storage is optional */
    }
  }, [messages]);

  useEffect(() => {
    const node = scrollRef.current;
    if (node) node.scrollTop = node.scrollHeight;
  }, [messages, busy]);

  useEffect(() => {
    textareaRef.current?.focus();
  }, []);

  async function send(text: string) {
    const content = text.trim();
    if (!content || busy) return;

    const userMessage: ChatMessage = { id: newId(), role: "user", content };
    const priorHistory = history;
    setMessages((prev) => [...prev, userMessage]);
    setInput("");
    setBusy(true);

    try {
      const reply = await engine.agent.chat(content, priorHistory);
      setMessages((prev) => [
        ...prev,
        {
          id: newId(),
          role: "assistant",
          content: reply.answer,
          toolCalls: reply.tool_calls,
          fixture: reply.fixture,
          degraded: reply.degraded,
        },
      ]);
    } catch (err) {
      setMessages((prev) => [
        ...prev,
        {
          id: newId(),
          role: "assistant",
          content: `I couldn't complete that request: ${String(err)}`,
          error: true,
        },
      ]);
    } finally {
      setBusy(false);
      textareaRef.current?.focus();
    }
  }

  function handleKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      void send(input);
    }
  }

  function startNewChat() {
    setMessages([]);
    setInput("");
    try {
      sessionStorage.removeItem(STORAGE_KEY);
    } catch {
      /* ignore */
    }
    textareaRef.current?.focus();
  }

  return (
    <div className="flex flex-col h-full bg-[oklch(var(--background))]">
      {/* Toolbar */}
      <div className="flex items-center justify-between px-6 py-4 border-b border-[oklch(var(--border))]">
        <div className="flex items-center gap-2.5">
          <div className="w-8 h-8 rounded-lg bg-[oklch(var(--accent)/0.14)] text-[oklch(var(--accent))] border border-[oklch(var(--accent)/0.25)] flex items-center justify-center">
            <Bot size={16} />
          </div>
          <div>
            <h2 className="text-sm font-semibold text-[oklch(var(--foreground))] tracking-tight">
              Agent
            </h2>
            <p className="text-xs text-[oklch(var(--muted-foreground))] mt-0.5">
              Ask about your workspace or get help setting it up.
            </p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <Badge variant="muted">
            <ShieldCheck size={10} /> read-only
          </Badge>
          <Button
            variant="outline"
            onClick={startNewChat}
            disabled={busy}
            className="text-xs h-8 gap-1.5"
          >
            <Plus size={12} />
            New chat
          </Button>
        </div>
      </div>

      {/* Conversation */}
      <div ref={scrollRef} className="flex-1 overflow-y-auto px-6">
        <div className="max-w-3xl mx-auto py-6 space-y-4">
          {messages.length === 0 && (
            <div className="animate-fade-in">
              <div className="flex flex-col items-center text-center py-10">
                <div className="w-12 h-12 rounded-2xl bg-[oklch(var(--accent)/0.14)] text-[oklch(var(--accent))] border border-[oklch(var(--accent)/0.25)] flex items-center justify-center mb-3">
                  <Bot size={24} />
                </div>
                <h3 className="text-[0.9375rem] font-semibold text-[oklch(var(--foreground))]">
                  What can I help with?
                </h3>
                <p className="text-[0.8125rem] text-[oklch(var(--muted-foreground))] mt-1 max-w-md leading-relaxed">
                  I can read your local tasks, meetings, approvals and connection
                  status. I cannot change anything — that stays behind the approval
                  gate.
                </p>
              </div>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                {SUGGESTIONS.map((suggestion) => (
                  <button
                    key={suggestion}
                    type="button"
                    onClick={() => void send(suggestion)}
                    className="text-left px-3.5 py-3 rounded-xl border border-[oklch(var(--border))] bg-[oklch(var(--card))] hover:border-[oklch(var(--primary)/0.35)] hover:bg-[oklch(var(--muted)/0.4)] transition-all text-[0.8125rem] text-[oklch(var(--foreground))]"
                  >
                    {suggestion}
                  </button>
                ))}
              </div>
            </div>
          )}

          {messages.map((message) => (
            <MessageBubble key={message.id} message={message} />
          ))}

          {busy && (
            <div className="flex gap-2.5">
              <div className="mt-0.5 w-7 h-7 flex-none rounded-lg bg-[oklch(var(--accent)/0.14)] text-[oklch(var(--accent))] border border-[oklch(var(--accent)/0.25)] flex items-center justify-center">
                <Bot size={14} />
              </div>
              <div className="flex items-center gap-2 rounded-xl border border-[oklch(var(--border))] bg-[oklch(var(--card))] px-4 py-3 text-xs text-[oklch(var(--muted-foreground))] shadow-xs">
                <Spinner size={13} />
                Reading your workspace…
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Composer */}
      <div className="border-t border-[oklch(var(--border))] bg-[oklch(var(--card)/0.6)] px-6 py-3">
        <div className="max-w-3xl mx-auto">
          <div className="flex items-end gap-2 rounded-xl border border-[oklch(var(--border))] bg-[oklch(var(--background))] px-3 py-2 focus-within:ring-1 focus-within:ring-[oklch(var(--primary))]">
            <textarea
              ref={textareaRef}
              value={input}
              onChange={(event) => setInput(event.target.value)}
              onKeyDown={handleKeyDown}
              disabled={busy}
              rows={2}
              placeholder="Ask about tasks, meetings, approvals or setup…"
              aria-label="Message the agent"
              className="flex-1 resize-none bg-transparent text-[0.8125rem] leading-relaxed text-[oklch(var(--foreground))] placeholder:text-[oklch(var(--muted-foreground))] focus:outline-none disabled:opacity-60"
            />
            <Button
              variant="primary"
              onClick={() => void send(input)}
              disabled={busy || !input.trim()}
              className="h-8 w-9 px-0 flex-none"
            >
              {busy ? <Spinner size={13} /> : <Send size={13} />}
            </Button>
          </div>
          <p className="mt-1.5 text-[0.6875rem] text-[oklch(var(--muted-foreground))]">
            Enter to send · Shift+Enter for a new line · read-only · tool results
            are shared with your configured model
          </p>
        </div>
      </div>
    </div>
  );
}
