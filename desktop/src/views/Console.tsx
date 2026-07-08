import { useEffect, useRef, useState } from "react";
import { subscribeLedger } from "../api/events";
import type { LedgerEvent } from "../api/events";

let _lineIdCounter = 0;
function nextLineId(): string {
  return `line-${++_lineIdCounter}`;
}

type ScrollbackLine =
  | { kind: "event"; id: string; lineId: string; ts: string; type: string; raw: LedgerEvent }
  | { kind: "cmd"; lineId: string; text: string }
  | { kind: "stub"; lineId: string };

function formatTs(ts: unknown): string {
  if (!ts || typeof ts !== "string") return "—";
  try {
    return new Date(ts).toISOString().replace("T", " ").slice(0, 19);
  } catch {
    return String(ts);
  }
}

export function Console() {
  const [lines, setLines] = useState<ScrollbackLine[]>([]);
  const [paused, setPaused] = useState(false);
  const [input, setInput] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [streamOffline, setStreamOffline] = useState(false);
  const pausedRef = useRef(false);
  const scrollRef = useRef<HTMLUListElement>(null);

  useEffect(() => {
    pausedRef.current = paused;
  }, [paused]);

  useEffect(() => {
    let unsub: (() => void) | null = null;
    try {
      unsub = subscribeLedger(
        (e: LedgerEvent) => {
          // any frame means the stream is alive again
          setStreamOffline(false);
          if (pausedRef.current) return;
          setLines((prev) => [
            ...prev,
            {
              kind: "event",
              lineId: nextLineId(),
              id: e.event_id ?? e.id ?? `ev-${nextLineId()}`,
              ts: typeof e.ts === "string" ? e.ts : "",
              type: e.payload?.type ?? e.type ?? e.kind ?? "event",
              raw: e,
            },
          ]);
        },
        () => {
          // EventSource fires onerror on transient reconnects too; surface a
          // soft non-blocking status rather than replacing the whole view.
          setStreamOffline(true);
        },
      );
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : String(e));
    }
    return () => {
      if (unsub) unsub();
    };
  }, []);

  // Auto-scroll to bottom when lines change, guarded by paused state and reduced-motion preference
  useEffect(() => {
    const el = scrollRef.current;
    const reducedMotion =
      typeof window.matchMedia === "function"
        ? window.matchMedia("(prefers-reduced-motion: reduce)").matches
        : false;
    if (el && !pausedRef.current && !reducedMotion) {
      el.scrollTop = el.scrollHeight;
    }
  }, [lines]);

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    const trimmed = input.trim();
    if (!trimmed) return;
    setLines((prev) => [
      ...prev,
      { kind: "cmd", lineId: nextLineId(), text: trimmed },
      { kind: "stub", lineId: nextLineId() },
    ]);
    setInput("");
  }

  if (err) {
    return (
      <div
        role="alert"
        style={{ padding: 16, color: "var(--err)", fontFamily: "var(--font-mono)" }}
      >
        <span aria-hidden="true">WARNING: </span>
        Error: {err}
      </div>
    );
  }

  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        height: "100%",
        background: "var(--bg)",
        fontFamily: "var(--font-mono)",
      }}
    >
      {/* Toolbar */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 10,
          padding: "6px 12px",
          borderBottom: "1px solid var(--border)",
          background: "var(--surface-1)",
        }}
      >
        <span
          className="werk-label"
          style={{ color: "var(--brand)", letterSpacing: "0.08em" }}
        >
          CONSOLE
        </span>
        {streamOffline && (
          <span
            role="status"
            className="werk-stamp"
            style={{ color: "var(--muted)" }}
            title="Live stream reconnecting"
          >
            STREAM OFFLINE
          </span>
        )}
        <span style={{ flex: 1 }} />
        <button
          onClick={() => setPaused((p) => !p)}
          style={{
            background: "none",
            border: "1px solid var(--border)",
            color: paused ? "var(--warn)" : "var(--muted)",
            fontFamily: "var(--font-mono)",
            fontSize: 11,
            padding: "2px 8px",
            cursor: "pointer",
            letterSpacing: "0.06em",
          }}
          aria-label={paused ? "Resume" : "Pause"}
        >
          {paused ? "RESUME" : "PAUSE"}
        </button>
        {paused && (
          <span
            className="werk-stamp"
            style={{ color: "var(--warn)" }}
          >
            PAUSED
          </span>
        )}
      </div>

      {/* Scrollback */}
      <ul
        ref={scrollRef}
        role="log"
        aria-live="polite"
        aria-label="Ledger event log"
        style={{
          flex: 1,
          overflow: "auto",
          background: "var(--sunk)",
          padding: "10px 14px",
          display: "flex",
          flexDirection: "column",
          gap: 3,
          margin: 0,
          listStyle: "none",
        }}
      >
        {lines.length === 0 && (
          <li role="status" style={{ color: "var(--faint)", fontSize: 12 }}>
            — waiting for ledger events —
          </li>
        )}
        {lines.map((line) => {
          if (line.kind === "event") {
            return (
              <li key={line.lineId} style={{ display: "flex", gap: 10, alignItems: "baseline" }}>
                <span
                  className="werk-num"
                  style={{ color: "var(--muted)", fontSize: 11, minWidth: 152, flexShrink: 0 }}
                >
                  {formatTs(line.ts)}
                </span>
                <span
                  className="werk-stamp"
                  style={{ color: "var(--audit)", flexShrink: 0 }}
                >
                  {line.type}
                </span>
                <span
                  className="werk-num"
                  style={{ color: "var(--faint)", fontSize: 11 }}
                >
                  {line.id}
                </span>
              </li>
            );
          }
          if (line.kind === "cmd") {
            return (
              <li key={line.lineId} style={{ color: "var(--fg)", fontSize: 13 }}>
                <span style={{ color: "var(--interact)" }}>$ </span>
                {line.text}
              </li>
            );
          }
          // stub
          return (
            <li key={line.lineId} style={{ color: "var(--muted)", fontSize: 11, fontStyle: "italic" }}>
              [execution wiring is pending — command not sent to hub]
            </li>
          );
        })}
      </ul>

      {/* Command input */}
      <form
        onSubmit={handleSubmit}
        style={{
          display: "flex",
          alignItems: "center",
          padding: "6px 12px",
          borderTop: "1px solid var(--border)",
          background: "var(--surface-1)",
          gap: 6,
        }}
      >
        <span style={{ color: "var(--interact)", fontSize: 14, userSelect: "none" }}>
          &gt;{" "}
        </span>
        <input
          type="text"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="hub verb (e.g. hs list)"
          aria-label="Hub command input"
          style={{
            flex: 1,
            background: "none",
            border: "none",
            color: "var(--fg)",
            fontFamily: "var(--font-mono)",
            fontSize: 13,
            caretColor: "var(--interact)",
          }}
          autoComplete="off"
          spellCheck={false}
        />
        <button
          type="submit"
          aria-label="Submit command"
          style={{
            background: "none",
            border: "1px solid var(--border)",
            color: "var(--muted)",
            fontFamily: "var(--font-mono)",
            fontSize: 11,
            padding: "2px 8px",
            cursor: "pointer",
            letterSpacing: "0.06em",
          }}
        >
          RUN
        </button>
      </form>
    </div>
  );
}
