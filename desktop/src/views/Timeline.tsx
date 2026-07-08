import { useEffect, useRef, useState, useSyncExternalStore } from "react";
import {
  Pause,
  Play,
  CheckCircle2,
  XCircle,
  Settings2,
  Skull,
  ShieldAlert,
  Radio,
} from "lucide-react";
import { fetchStatus } from "../api/client";
import { subscribeLedger } from "../api/events";
import type { LedgerEvent } from "../api/events";
import type { LedgerEventRecord } from "../api/types";

// ─── constants ───────────────────────────────────────────────────────────────

const FAINT_OPACITY = 0.55;

// ─── category helpers ────────────────────────────────────────────────────────

type Category = "config" | "kill" | "approval" | "event";

function category(type: string | undefined): Category {
  const t = type ?? "";
  if (t.startsWith("config.")) return "config";
  if (t.startsWith("process.kill.")) return "kill";
  if (t.startsWith("approval.")) return "approval";
  return "event";
}

// Derive a short label from the event type for the generic "event" bucket
function eventLabel(type: string): string {
  // Strip common dot-prefix namespaces and uppercase the first segment
  const parts = type.split(".");
  if (parts.length >= 2) {
    // e.g. "ledger.commit" → "LEDGER", "system.ready" → "SYSTEM"
    return parts[0].toUpperCase();
  }
  return "SYSTEM";
}

const CATEGORY_META: Record<
  Category,
  { color: string; borderColor: string; Icon?: React.ComponentType<{ size?: number; "aria-hidden"?: boolean }> }
> = {
  config:   { color: "var(--audit)",     borderColor: "var(--audit)",     Icon: Settings2 },
  kill:     { color: "var(--err)",       borderColor: "var(--err)",       Icon: Skull },
  approval: { color: "var(--warn)",      borderColor: "var(--warn)",      Icon: ShieldAlert },
  event:    { color: "var(--secondary)", borderColor: "var(--interact)",  Icon: undefined },
};

// ─── row helpers ─────────────────────────────────────────────────────────────

interface Row {
  id: string;
  ts: string;
  type: string;
  hash: string;
}

function toRow(r: LedgerEventRecord): Row {
  return {
    id: r.event_id,
    ts: r.ts,
    type: r.payload?.type ?? "unknown",
    hash: r.hash ?? r.prev_hash ?? "",
  };
}

function fromLiveEvent(e: LedgerEvent): Row {
  const type = e.payload?.type ?? e.type ?? e.kind ?? "event";
  const ts = e.ts ?? new Date().toISOString();
  const id = e.event_id ?? e.id ?? `live-${type}-${ts}`;
  return { id, ts, type, hash: e.hash ?? "" };
}

// ─── prefers-reduced-motion ──────────────────────────────────────────────────

function subscribeReducedMotion(cb: () => void): () => void {
  if (typeof window === "undefined" || typeof window.matchMedia !== "function") return () => {};
  const mq = window.matchMedia("(prefers-reduced-motion: reduce)");
  mq.addEventListener("change", cb);
  return () => mq.removeEventListener("change", cb);
}
function getReducedMotionSnapshot(): boolean {
  if (typeof window === "undefined" || typeof window.matchMedia !== "function") return false;
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}
function getReducedMotionServerSnapshot(): boolean {
  return false;
}

// ─── component ───────────────────────────────────────────────────────────────

export function Timeline() {
  const [rows, setRows] = useState<Row[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [chainVerified, setChainVerified] = useState<boolean | undefined>(undefined);
  const [paused, setPaused] = useState(false);
  // latest event type for the aria-live sibling announcement
  const [liveAnnounce, setLiveAnnounce] = useState("");

  const pausedRef = useRef(paused);
  pausedRef.current = paused;

  const prefersReduced = useSyncExternalStore(
    subscribeReducedMotion,
    getReducedMotionSnapshot,
    getReducedMotionServerSnapshot,
  );

  useEffect(() => {
    let alive = true;

    fetchStatus()
      .then((status) => {
        if (!alive) return;
        const seed = [...(status.recent_events ?? []).map(toRow)].reverse();
        setRows(seed);
        setChainVerified(status.chain_verified);
      })
      .catch((e: unknown) => {
        if (!alive) return;
        setErr(String((e as Error)?.message ?? e));
      });

    const unsub = subscribeLedger((e: LedgerEvent) => {
      if (pausedRef.current) return;
      const row = fromLiveEvent(e);
      setRows((prev) => [row, ...(prev ?? [])]);
      setLiveAnnounce(`New event: ${row.type}`);
    });

    return () => {
      alive = false;
      unsub();
    };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps — subscribeLedger and fetchStatus are stable module-level imports

  // ── error ──
  if (err) {
    return (
      <p
        role="alert"
        style={{ padding: "12px 16px", color: "var(--err)", fontFamily: "var(--font-mono)", fontSize: 12 }}
      >
        Failed to load timeline. {err}
      </p>
    );
  }

  // ── loading ──
  if (rows === null) {
    return (
      <p role="status" aria-live="polite" style={{ padding: "12px 16px", color: "var(--muted)", fontFamily: "var(--font-mono)", fontSize: 12 }}>
        Loading…
      </p>
    );
  }

  const colTemplate = "max-content 1fr 72px 80px";

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", background: "var(--bg)" }}>

      {/* ── sticky header ── */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 10,
          padding: "6px 12px",
          borderBottom: "1px solid var(--border-strong)",
          background: "var(--surface-1)",
          flexShrink: 0,
          position: "sticky",
          top: 0,
          zIndex: 1,
        }}
      >
        {/* view label */}
        <span className="werk-label" style={{ color: "var(--secondary)", letterSpacing: "0.08em", fontSize: 11 }}>
          LEDGER TIMELINE
        </span>

        {/* live pulse indicator */}
        <Radio
          size={12}
          aria-hidden
          className={!paused && !prefersReduced ? "werk-live" : undefined}
          style={{
            color: paused ? "var(--muted)" : "var(--ok)",
            flexShrink: 0,
          }}
        />

        {/* chain-verified stamp */}
        {chainVerified !== undefined && (
          <span
            className="werk-stamp"
            style={{
              display: "inline-flex",
              alignItems: "center",
              gap: 4,
              color: chainVerified ? "var(--ok)" : "var(--err)",
              border: `1px solid ${chainVerified ? "var(--ok)" : "var(--err)"}`,
              padding: "1px 5px",
              letterSpacing: "0.1em",
              fontSize: 10,
              marginLeft: "auto",
            }}
          >
            {chainVerified ? (
              <CheckCircle2 size={11} aria-hidden />
            ) : (
              <XCircle size={11} aria-hidden />
            )}
            {chainVerified ? "VERIFIED" : "UNVERIFIED"}
          </span>
        )}

        {/* pause / play toggle */}
        <button
          onClick={() => setPaused((p) => !p)}
          aria-label={paused ? "Resume" : "Pause"}
          className="werk-btn-ghost"
          onMouseDown={(e) => {
            (e.currentTarget as HTMLButtonElement).style.background = "var(--surface-2)";
          }}
          onMouseUp={(e) => {
            (e.currentTarget as HTMLButtonElement).style.background = paused ? "var(--surface-3)" : "";
          }}
          onMouseLeave={(e) => {
            (e.currentTarget as HTMLButtonElement).style.background = paused ? "var(--surface-3)" : "";
          }}
          style={{
            display: "inline-flex",
            alignItems: "center",
            gap: 5,
            background: paused ? "var(--surface-3)" : "none",
            border: "1px solid",
            borderColor: paused ? "var(--warn)" : "var(--border)",
            color: paused ? "var(--warn)" : "var(--secondary)",
            fontFamily: "var(--font-ui)",
            fontSize: 11,
            padding: "2px 7px",
            cursor: "pointer",
            borderRadius: 0,
            transition: prefersReduced ? "none" : "color var(--dur-base) var(--ease-decelerate), background var(--dur-base) var(--ease-decelerate)",
          }}
        >
          {paused ? <Play size={11} aria-hidden /> : <Pause size={11} aria-hidden />}
          {paused ? "Resume" : "Pause"}
        </button>
      </div>

      {/* ── column header rule ── */}
      {rows.length > 0 && (
        <div
          style={{
            display: "grid",
            gridTemplateColumns: colTemplate,
            gap: "0 12px",
            padding: "3px 12px",
            borderBottom: "1px solid var(--border)",
            background: "var(--sunk)",
            flexShrink: 0,
          }}
        >
          {(["TYPE", "EVENT", "HASH", "TIME"] as const).map((h) => (
            <span
              key={h}
              className="werk-label"
              style={{ color: "var(--faint)", fontSize: 9, letterSpacing: "0.1em" }}
            >
              {h}
            </span>
          ))}
        </div>
      )}

      {/* ── hidden aria-live for new events ── */}
      <span aria-live="polite" aria-atomic="true" className="sr-only">
        {liveAnnounce}
      </span>

      {/* ── feed ── */}
      {rows.length === 0 ? (
        /* left-aligned empty state occupying the column grid zone */
        <div style={{ padding: "0 0", flex: 1 }}>
          {/* ghost column header so empty state aligns with data rows */}
          <div
            style={{
              display: "grid",
              gridTemplateColumns: colTemplate,
              gap: "0 12px",
              padding: "3px 12px",
              borderBottom: "1px solid var(--border)",
              background: "var(--sunk)",
            }}
          >
            {(["TYPE", "EVENT", "HASH", "TIME"] as const).map((h) => (
              <span
                key={h}
                className="werk-label"
                style={{ color: "var(--faint)", fontSize: 9, letterSpacing: "0.1em" }}
              >
                {h}
              </span>
            ))}
          </div>
          {/* single instrument-status row */}
          <div
            style={{
              display: "grid",
              gridTemplateColumns: colTemplate,
              gap: "0 12px",
              padding: "6px 12px",
              borderBottom: "1px solid var(--border)",
              alignItems: "center",
            }}
          >
            <span
              className="werk-stamp"
              style={{
                display: "inline-flex",
                alignItems: "center",
                gap: 4,
                color: "var(--secondary)",
                border: "1px solid var(--border-strong)",
                padding: "1px 5px",
                letterSpacing: "0.08em",
                fontSize: 9,
                justifySelf: "start",
              }}
            >
              <Radio size={11} aria-hidden />
              LIVE
            </span>
            <p
              role="status"
              style={{
                margin: 0,
                fontFamily: "var(--font-mono)",
                fontSize: 12,
                color: "var(--muted)",
                letterSpacing: "0.04em",
              }}
            >
              No events yet — live feed active, listening for ledger activity.
            </p>
            <span />
            <span
              className="werk-num"
              style={{ color: "var(--faint)", fontSize: 11, textAlign: "right" }}
            >
              —
            </span>
          </div>
        </div>
      ) : (
        <ul
          style={{
            margin: 0,
            padding: 0,
            listStyle: "none",
            overflow: "auto",
            flex: 1,
          }}
        >
          {rows.map((row) => {
            const cat = category(row.type);
            const { color, borderColor, Icon } = CATEGORY_META[cat];
            // For the generic "event" category, derive a label from the type itself
            const stampLabel = cat === "event" ? eventLabel(row.type) : cat.toUpperCase();

            return (
              <li
                key={row.id}
                className="werk-row-li werk-notch"
                style={{
                  display: "grid",
                  gridTemplateColumns: colTemplate,
                  alignItems: "center",
                  gap: "0 12px",
                  padding: "3px 12px",
                  borderBottom: "1px solid var(--border)",
                  transition: prefersReduced ? "none" : `background var(--dur-base) var(--ease-decelerate), box-shadow var(--dur-base) var(--ease-decelerate)`,
                  cursor: "default",
                }}
              >
                {/* category stamp + optional icon */}
                <span
                  className="werk-stamp"
                  style={{
                    display: "inline-flex",
                    alignItems: "center",
                    gap: 4,
                    color,
                    fontSize: 9,
                    letterSpacing: "0.08em",
                    justifySelf: "start",
                    borderLeft: `2px solid ${borderColor}`,
                    paddingLeft: 4,
                  }}
                >
                  {Icon && <Icon size={11} aria-hidden />}
                  {stampLabel}
                </span>

                {/* event type — mono, truncated */}
                <span
                  style={{
                    color: "var(--fg)",
                    fontFamily: "var(--font-mono)",
                    fontSize: 12,
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                    whiteSpace: "nowrap",
                  }}
                >
                  {row.type}
                </span>

                {/* short hash + visually-hidden full hash */}
                <span
                  className="werk-num"
                  style={{
                    color: "var(--faint)",
                    fontSize: 11,
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                    whiteSpace: "nowrap",
                  }}
                >
                  {row.hash ? (
                    <>
                      {row.hash.slice(0, 8)}
                      <span className="sr-only">{` (full hash: ${row.hash})`}</span>
                    </>
                  ) : (
                    <span style={{ opacity: FAINT_OPACITY }}>—</span>
                  )}
                </span>

                {/* timestamp */}
                <time
                  dateTime={row.ts}
                  className="werk-num"
                  style={{ color: "var(--muted)", fontSize: 11, textAlign: "right" }}
                >
                  {new Date(row.ts).toLocaleTimeString()}
                </time>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
