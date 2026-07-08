import { useEffect, useRef, useState, useCallback } from "react";
import { ShieldCheck, CheckCircle, XCircle } from "lucide-react";
import { fetchApprovals, resolveApproval } from "../api/client";
import type { ApprovalRecord } from "../api/types";

import "./Approvals.css";

function formatTs(raw: string): string {
  const d = new Date(raw);
  if (isNaN(d.getTime())) return raw;
  const hh = String(d.getUTCHours()).padStart(2, "0");
  const mm = String(d.getUTCMinutes()).padStart(2, "0");
  const ss = String(d.getUTCSeconds()).padStart(2, "0");
  return `${hh}:${mm}:${ss} · ${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, "0")}-${String(d.getUTCDate()).padStart(2, "0")}`;
}

export function Approvals() {
  const [items, setItems] = useState<ApprovalRecord[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [sel, setSel] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [statusMsg, setStatusMsg] = useState<string>("");
  const aliveRef = useRef(true);

  const load = useCallback(() => {
    fetchApprovals()
      .then((rows) => {
        if (!aliveRef.current) return;
        setItems(rows);
        setErr(null);
      })
      .catch((e: unknown) => {
        if (!aliveRef.current) return;
        setErr(String((e as Error)?.message ?? e));
      });
  }, []);

  useEffect(() => {
    aliveRef.current = true;
    load();
    return () => {
      aliveRef.current = false;
    };
  }, [load]);

  const resolve = useCallback(
    (record: ApprovalRecord, decision: "approve" | "deny") => {
      if (decision === "deny") {
        const ok = window.confirm(
          `Deny approval for "${record.tool_id}" (profile ${record.profile_id})? This cannot be undone.`,
        );
        if (!ok) return;
      }
      setBusy(record.request_id);
      resolveApproval(record.request_id, decision)
        .then((res) => {
          if (!aliveRef.current) return;
          setStatusMsg(`${record.tool_id} ${res.status}`);
          setSel(null);
          load();
        })
        .catch((e: unknown) => {
          if (!aliveRef.current) return;
          setErr(String((e as Error)?.message ?? e));
        })
        .finally(() => {
          if (aliveRef.current) setBusy(null);
        });
    },
    [load],
  );

  if (err) {
    return (
      <div role="alert">
        <p style={{ padding: 16, color: "var(--err)" }}>
          Failed to load approvals. {err}
        </p>
      </div>
    );
  }

  if (!items) {
    return (
      <div role="status" aria-live="polite">
        <p style={{ padding: 16, color: "var(--muted)" }}>Loading…</p>
      </div>
    );
  }

  if (items.length === 0) {
    return (
      <div
        className="blueprint-grid"
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          height: "100%",
          minHeight: 280,
          overflow: "hidden",
        }}
      >
        <div
          style={{
            display: "flex",
            flexDirection: "column",
            alignItems: "center",
            gap: 14,
            padding: "32px 40px",
            background: "var(--surface-1)",
            border: "1px solid var(--border-strong)",
            maxWidth: 380,
            textAlign: "center",
          }}
        >
          <ShieldCheck size={38} aria-hidden style={{ color: "var(--ok)" }} />
          <p
            style={{
              margin: 0,
              color: "var(--fg)",
              fontSize: 15,
              fontVariationSettings: "'wght' 510",
            }}
          >
            No pending approvals
          </p>
          <p style={{ margin: 0, color: "var(--muted)", fontSize: 12, lineHeight: 1.6 }}>
            The gate is clear — every tool call your agents requested has been
            decided. New requests appear here for approve / deny.
          </p>
        </div>
      </div>
    );
  }

  const active = items.find((i) => i.request_id === sel) ?? null;

  return (
    <div style={{ display: "flex", height: "100%" }}>
      <div aria-live="polite" aria-atomic="true" className="sr-only">
        {statusMsg}
      </div>

      <ul
        aria-label="Pending approvals"
        style={{
          width: "42%",
          margin: 0,
          padding: 8,
          listStyle: "none",
          borderRight: "1px solid var(--border)",
          overflow: "auto",
        }}
      >
        {items.map((item) => {
          const isSel = sel === item.request_id;
          return (
            <li key={item.request_id}>
              <button
                onClick={() => {
                  setSel(item.request_id);
                  setStatusMsg(`Reviewing ${item.tool_id}`);
                }}
                aria-pressed={isSel}
                className={`approval-row${isSel ? " approval-row--selected" : ""}`}
              >
                <span style={{ fontSize: 13, color: isSel ? "var(--fg)" : "var(--secondary)" }}>
                  {item.tool_id}
                </span>
                <span className="werk-num" style={{ color: "var(--muted)", fontSize: 11 }}>
                  {item.profile_id} · {formatTs(item.created_at)}
                </span>
              </button>
            </li>
          );
        })}
      </ul>

      <section aria-label="Approval detail" style={{ flex: 1, padding: 16, overflow: "auto" }}>
        {active ? (
          <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
            <dl
              className="werk-num"
              style={{
                display: "grid",
                gridTemplateColumns: "84px 1fr",
                gap: 6,
                margin: 0,
                color: "var(--secondary)",
                fontSize: 12,
              }}
            >
              <dt className="werk-label" style={{ fontSize: 10, alignSelf: "center" }}>tool</dt>
              <dd style={{ margin: 0 }}>
                <span className="werk-stamp" style={{ color: "var(--audit)" }}>
                  {active.tool_id.toUpperCase()}
                </span>
              </dd>
              <dt className="werk-label" style={{ fontSize: 10, alignSelf: "center" }}>profile</dt>
              <dd style={{ margin: 0, color: "var(--fg)" }}>{active.profile_id}</dd>
              <dt className="werk-label" style={{ fontSize: 10, alignSelf: "center" }}>request_id</dt>
              <dd style={{ margin: 0, color: "var(--fg)" }}>{active.request_id}</dd>
              <dt className="werk-label" style={{ fontSize: 10, alignSelf: "center" }}>created</dt>
              <dd style={{ margin: 0, color: "var(--fg)" }}>{formatTs(active.created_at)}</dd>
            </dl>

            <div>
              <span className="werk-label" style={{ fontSize: 10 }}>call args (redacted)</span>
              <pre
                style={{
                  marginTop: 6,
                  background: "var(--sunk)",
                  padding: 12,
                  color: "var(--secondary)",
                  fontFamily: "var(--font-mono)",
                  fontSize: 12,
                  overflowX: "auto",
                  border: "1px solid var(--border)",
                }}
              >
                {JSON.stringify(active.call_args, null, 2)}
              </pre>
            </div>

            <div style={{ display: "flex", gap: 8, borderTop: "1px solid var(--border)", paddingTop: 12 }}>
              <button
                onClick={() => resolve(active, "approve")}
                disabled={busy === active.request_id}
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 6,
                  padding: "7px 16px",
                  background: "var(--interact)",
                  color: "var(--on-accent)",
                  border: "none",
                  borderRadius: "var(--radius-xs)",
                  cursor: busy ? "wait" : "pointer",
                  opacity: busy === active.request_id ? 0.6 : 1,
                  fontSize: 12,
                  fontVariationSettings: "'wght' 510",
                }}
              >
                <CheckCircle size={13} aria-hidden />
                {busy === active.request_id ? "Approving…" : "Approve"}
              </button>
              <button
                onClick={() => resolve(active, "deny")}
                disabled={busy === active.request_id}
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 6,
                  padding: "7px 16px",
                  background: "transparent",
                  color: "var(--err)",
                  border: "1px solid var(--err)",
                  borderRadius: "var(--radius-xs)",
                  cursor: busy ? "wait" : "pointer",
                  opacity: busy === active.request_id ? 0.6 : 1,
                  fontSize: 12,
                  fontVariationSettings: "'wght' 510",
                }}
              >
                <XCircle size={13} aria-hidden />
                Deny
              </button>
            </div>
          </div>
        ) : (
          <p style={{ color: "var(--muted)", fontSize: 13 }}>Select an approval to review.</p>
        )}
      </section>
    </div>
  );
}
