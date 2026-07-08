import { useEffect, useState } from "react";
import { Eye, Check, Ban, Coins, User, AlertTriangle } from "lucide-react";
import { fetchStatus } from "../api/client";
import { AccessBadge } from "../badges/Badge";
import type { HubStatus } from "../api/types";

const LENS_ROWS = [
  {
    key: "VISIBLE TOOLS",
    desc: "Tools surfaced in the agent context window",
    Icon: Eye,
    color: "var(--audit)",
    borderColor: "var(--audit)",
  },
  {
    key: "ALLOWED TOOLS",
    desc: "Tools the agent may actually invoke",
    Icon: Check,
    color: "var(--ok)",
    borderColor: "var(--ok)",
  },
  {
    key: "BLOCKED TOOLS",
    desc: "Tools permanently denied regardless of request",
    Icon: Ban,
    color: "var(--err)",
    borderColor: "var(--err)",
  },
  {
    key: "BUDGET / ROLE",
    desc: "Maximum cumulative spend cap per mission",
    Icon: Coins,
    color: "var(--warn)",
    borderColor: "var(--warn)",
  },
] as const;

export function Permissions() {
  const [status, setStatus] = useState<HubStatus | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetchStatus()
      .then((s) => { if (!cancelled) setStatus(s); })
      .catch((e: unknown) => {
        if (!cancelled) setErr(String(e instanceof Error ? e.message : e));
      });
    return () => { cancelled = true; };
  }, []);

  if (err) {
    return (
      <p role="alert" aria-live="assertive" style={{ padding: 16, color: "var(--err)" }}>
        Failed to load permissions. {err}
      </p>
    );
  }

  if (!status) {
    return (
      <p role="status" aria-live="polite" style={{ padding: 16, color: "var(--muted)" }}>
        Loading…
      </p>
    );
  }

  return (
    <div
      style={{
        padding: "20px 20px 24px",
        display: "flex",
        flexDirection: "column",
        gap: 28,
        maxWidth: 720,
      }}
    >
      {/* ── ACTIVE PROFILE ── */}
      <section aria-labelledby="section-active-profile">
        <h2
          id="section-active-profile"
          className="werk-label"
          style={{ color: "var(--muted)", margin: 0, marginBottom: 10 }}
        >
          ACTIVE PROFILE
        </h2>

        {status.profile_id ? (
          <div
            style={{
              background: "var(--surface-2)",
              border: "1px solid var(--border-strong)",
              padding: "12px 16px",
              display: "flex",
              alignItems: "center",
              gap: 12,
              transition: `background var(--dur-fast) var(--ease-decelerate)`,
            }}
            onMouseEnter={(e) => {
              (e.currentTarget as HTMLDivElement).style.background = "var(--state-hover)";
            }}
            onMouseLeave={(e) => {
              (e.currentTarget as HTMLDivElement).style.background = "var(--surface-2)";
            }}
          >
            <User
              aria-hidden
              size={14}
              style={{ color: "var(--interact)", flexShrink: 0 }}
            />
            <span
              className="werk-num"
              style={{ color: "var(--fg)", fontSize: 14, flex: 1 }}
            >
              {status.profile_id}
            </span>
            <AccessBadge level={status.profile_id} />
            <span
              className="werk-stamp"
              style={{
                color: "var(--ok)",
                background: "var(--state-approve)",
                padding: "2px 6px",
                display: "flex",
                alignItems: "center",
                gap: 5,
              }}
            >
              <Check aria-hidden size={10} />
              ACTIVE
            </span>
          </div>
        ) : (
          <div
            style={{
              background: "var(--surface-1)",
              border: "1px solid var(--border)",
              padding: "12px 16px",
              display: "flex",
              alignItems: "center",
              gap: 10,
            }}
          >
            <AlertTriangle aria-hidden size={13} style={{ color: "var(--warn)", flexShrink: 0 }} />
            <p style={{ color: "var(--muted)", margin: 0, fontSize: 13 }}>
              No profile set — hub returned no profile_id.
            </p>
          </div>
        )}
      </section>

      {/* ── TOOL LENS MODEL ── */}
      <section aria-labelledby="section-tool-lens">
        <h2
          id="section-tool-lens"
          className="werk-label"
          style={{ color: "var(--muted)", margin: 0, marginBottom: 10 }}
        >
          TOOL LENS MODEL
        </h2>

        <div
          style={{
            background: "var(--surface-1)",
            border: "1px solid var(--border)",
            display: "flex",
            flexDirection: "column",
          }}
        >
          {/* Description row */}
          <p
            style={{
              margin: 0,
              padding: "14px 16px 12px",
              color: "var(--secondary)",
              fontSize: 13,
              lineHeight: 1.65,
              borderBottom: "1px solid var(--border)",
            }}
          >
            The tool-lens model controls which tools an agent profile may call.
            Each profile carries three sets —{" "}
            <span style={{ color: "var(--fg)" }}>visible</span>,{" "}
            <span style={{ color: "var(--fg)" }}>allowed</span>, and{" "}
            <span style={{ color: "var(--fg)" }}>blocked</span> — plus a
            per-role budget cap.
          </p>

          {/* Lens rows */}
          {LENS_ROWS.map(({ key, desc, Icon, color, borderColor }, i) => (
            <div
              key={key}
              style={{
                display: "flex",
                alignItems: "center",
                gap: 14,
                padding: "10px 16px",
                borderTop: i === 0 ? undefined : "1px solid var(--border)",
                boxShadow: `inset 2px 0 0 ${borderColor}`,
              }}
            >
              <Icon
                aria-hidden
                size={14}
                style={{ color, flexShrink: 0 }}
              />
              <span
                className="werk-label"
                style={{ color: "var(--muted)", minWidth: 138, fontSize: 10 }}
              >
                {key}
              </span>
              <span
                style={{ color: "var(--secondary)", flex: 1, fontSize: 12 }}
              >
                {desc}
              </span>
            </div>
          ))}

          {/* Footer note — pending disclaimer (collapsed from per-row stamps) */}
          <p
            style={{
              margin: 0,
              padding: "8px 16px 10px",
              color: "var(--faint)",
              fontSize: 10,
              borderTop: "1px solid var(--border)",
              lineHeight: 1.5,
              display: "flex",
              alignItems: "center",
              gap: 6,
            }}
          >
            <AlertTriangle aria-hidden size={9} style={{ color: "var(--warn)", flexShrink: 0 }} />
            <span>
              Tool counts pending backend endpoint
              {status.profile_id && (
                <>
                  {" — profile "}
                  <span className="werk-num" style={{ color: "var(--secondary)" }}>
                    {status.profile_id}
                  </span>
                  {" is active and will populate once the profile/policy endpoint is available"}
                </>
              )}
              .
            </span>
          </p>
        </div>
      </section>
    </div>
  );
}
