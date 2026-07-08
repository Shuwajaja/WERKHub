import { useCallback, useEffect, useRef, useState } from "react";
import { DownloadCloud, KeyRound, RefreshCw, Plug, Server } from "lucide-react";
import { fetchOnboard, applyOnboard } from "../api/client";
import type { DiscoveredServer, OnboardDiscovery } from "../api/types";

function groupByHost(
  servers: DiscoveredServer[],
): { host: string; servers: DiscoveredServer[] }[] {
  const map = new Map<string, DiscoveredServer[]>();
  for (const s of servers) {
    const bucket = map.get(s.source_host) ?? [];
    bucket.push(s);
    map.set(s.source_host, bucket);
  }
  return [...map.entries()]
    .map(([host, list]) => ({ host, servers: list }))
    .sort((a, b) => a.host.localeCompare(b.host));
}

export function Onboard() {
  const [data, setData] = useState<OnboardDiscovery | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [statusMsg, setStatusMsg] = useState("");
  const aliveRef = useRef(true);

  const load = useCallback(() => {
    fetchOnboard()
      .then((d) => {
        if (!aliveRef.current) return;
        setData(d);
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

  const adopt = useCallback(() => {
    if (!data) return;
    const count = data.would_adopt.length;
    const ok = window.confirm(
      `Adopt ${count} MCP server${count === 1 ? "" : "s"} into the hub? ` +
        `Only server identities are stored — secret values stay in your shell environment.`,
    );
    if (!ok) return;
    setBusy(true);
    applyOnboard()
      .then((res) => {
        if (!aliveRef.current) return;
        setStatusMsg(
          res.added.length
            ? `Adopted ${res.added.length}: ${res.added.join(", ")}`
            : "Nothing new to adopt",
        );
        load();
      })
      .catch((e: unknown) => {
        if (!aliveRef.current) return;
        setErr(String((e as Error)?.message ?? e));
      })
      .finally(() => {
        if (aliveRef.current) setBusy(false);
      });
  }, [data, load]);

  if (err) {
    return (
      <div role="alert">
        <p style={{ padding: 16, color: "var(--err)" }}>
          Failed to scan agent hosts. {err}
        </p>
      </div>
    );
  }

  if (!data) {
    return (
      <div role="status" aria-live="polite">
        <p style={{ padding: 16, color: "var(--muted)" }}>Scanning your agent hosts…</p>
      </div>
    );
  }

  const groups = groupByHost(data.discovered);
  const totalFound = data.discovered.length;

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%" }}>
      <div aria-live="polite" aria-atomic="true" className="sr-only">
        {statusMsg}
      </div>

      <header
        style={{
          display: "flex",
          alignItems: "center",
          gap: 12,
          padding: "12px 16px",
          borderBottom: "1px solid var(--border)",
        }}
      >
        <DownloadCloud size={18} aria-hidden style={{ color: "var(--interact)" }} />
        <div style={{ flex: 1, minWidth: 0 }}>
          <p style={{ margin: 0, color: "var(--fg)", fontSize: 14, fontVariationSettings: "'wght' 540" }}>
            Onboard
          </p>
          <p style={{ margin: 0, color: "var(--muted)", fontSize: 11 }}>
            Import the MCP servers your agent harnesses already use — route them through the hub.
          </p>
        </div>
        <button
          onClick={load}
          aria-label="Rescan"
          title="Rescan"
          style={{
            display: "flex",
            alignItems: "center",
            gap: 6,
            padding: "6px 12px",
            background: "transparent",
            color: "var(--secondary)",
            border: "1px solid var(--border-strong)",
            borderRadius: "var(--radius-xs)",
            cursor: "pointer",
            fontSize: 12,
          }}
        >
          <RefreshCw size={13} aria-hidden /> Rescan
        </button>
      </header>

      <section aria-label="Discovered servers" style={{ flex: 1, overflow: "auto", padding: 16 }}>
        {totalFound === 0 ? (
          <div
            style={{
              display: "flex",
              flexDirection: "column",
              alignItems: "center",
              gap: 12,
              padding: "40px 24px",
              textAlign: "center",
              color: "var(--muted)",
            }}
          >
            <Server size={34} aria-hidden style={{ color: "var(--muted)" }} />
            <p style={{ margin: 0, color: "var(--fg)", fontSize: 14 }}>No MCP servers found</p>
            <p style={{ margin: 0, fontSize: 12, lineHeight: 1.6, maxWidth: 360 }}>
              None of your known agent hosts (Claude, Cursor, Gemini, Codex, …) declare an
              MCP server yet. Add one in your harness, then Rescan.
            </p>
          </div>
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: 20 }}>
            {groups.map(({ host, servers }) => (
              <div key={host}>
                <p
                  className="werk-label"
                  style={{ margin: "0 0 8px", fontSize: 10, color: "var(--audit)" }}
                >
                  {host} · {servers.length}
                </p>
                <ul style={{ listStyle: "none", margin: 0, padding: 0, display: "flex", flexDirection: "column", gap: 6 }}>
                  {servers.map((s) => (
                    <li
                      key={`${host}/${s.name}`}
                      style={{
                        display: "flex",
                        alignItems: "center",
                        gap: 10,
                        padding: "10px 12px",
                        background: "var(--surface-1)",
                        border: "1px solid var(--border)",
                        borderRadius: "var(--radius-xs)",
                      }}
                    >
                      <Plug size={15} aria-hidden style={{ color: "var(--secondary)", flexShrink: 0 }} />
                      <div style={{ flex: 1, minWidth: 0 }}>
                        <span style={{ color: "var(--fg)", fontSize: 13 }}>{s.name}</span>
                        <span
                          className="werk-num"
                          style={{ display: "block", color: "var(--muted)", fontSize: 11, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}
                        >
                          {s.command || s.url || "—"}
                        </span>
                      </div>
                      <span className="werk-stamp" style={{ color: "var(--muted)", fontSize: 10 }}>
                        {s.transport.toUpperCase()}
                      </span>
                      {s.needs_keys.map((k) => (
                        <span
                          key={k}
                          title={`Requires env var ${k} (value never read or stored)`}
                          style={{
                            display: "inline-flex",
                            alignItems: "center",
                            gap: 4,
                            padding: "2px 7px",
                            background: "var(--sunk)",
                            border: "1px solid var(--border)",
                            borderRadius: "var(--radius-xs)",
                            color: "var(--warn, var(--secondary))",
                            fontSize: 10,
                            fontFamily: "var(--font-mono)",
                          }}
                        >
                          <KeyRound size={10} aria-hidden /> {k}
                        </span>
                      ))}
                    </li>
                  ))}
                </ul>
              </div>
            ))}
          </div>
        )}
      </section>

      {totalFound > 0 && (
        <footer
          style={{
            display: "flex",
            alignItems: "center",
            gap: 12,
            padding: "12px 16px",
            borderTop: "1px solid var(--border)",
          }}
        >
          <p style={{ flex: 1, margin: 0, color: "var(--muted)", fontSize: 11, lineHeight: 1.5 }}>
            {data.would_adopt.length} new connector{data.would_adopt.length === 1 ? "" : "s"} ready ·{" "}
            <span style={{ color: "var(--secondary)" }}>identities only, no secret values stored</span>
          </p>
          {data.apply_allowed ? (
            <button
              onClick={adopt}
              disabled={busy || data.would_adopt.length === 0}
              style={{
                display: "flex",
                alignItems: "center",
                gap: 6,
                padding: "8px 18px",
                background: "var(--interact)",
                color: "var(--on-accent)",
                border: "none",
                borderRadius: "var(--radius-xs)",
                cursor: busy ? "wait" : data.would_adopt.length === 0 ? "default" : "pointer",
                opacity: busy || data.would_adopt.length === 0 ? 0.55 : 1,
                fontSize: 12,
                fontVariationSettings: "'wght' 540",
              }}
            >
              <DownloadCloud size={14} aria-hidden />
              {busy ? "Adopting…" : `Adopt ${data.would_adopt.length}`}
            </button>
          ) : (
            <span
              title="Set WERK_ALLOW_HUB_ONBOARD=1 in the hub's environment to enable adoption"
              style={{
                padding: "8px 14px",
                background: "var(--sunk)",
                border: "1px solid var(--border)",
                borderRadius: "var(--radius-xs)",
                color: "var(--muted)",
                fontSize: 11,
              }}
            >
              Adoption locked · set WERK_ALLOW_HUB_ONBOARD=1
            </span>
          )}
        </footer>
      )}
    </div>
  );
}
