import { useEffect, useRef, useState } from "react";
import {
  Activity,
  CheckCircle2,
  AlertTriangle,
  Cpu,
  ShieldCheck,
  ShieldAlert,
  Star,
  Users,
  HelpCircle,
  Box,
  Globe,
  Zap,
} from "lucide-react";
import { fetchStatus, fetchRuntimes, fetchRegistry } from "../api/client";
import type {
  HubStatus,
  RuntimesResponse,
  RegistryCatalog,
  TrustTier,
} from "../api/types";

const TIER_COLOR: Record<TrustTier, string> = {
  Official: "var(--audit)",
  "Security-Scanned": "var(--ok)",
  Community: "var(--muted)",
  Unverified: "var(--warn)",
};

// Tier rank → opacity: Official anchor = 1.0, Unverified warning = 0.65
const TIER_OPACITY: Record<TrustTier, number> = {
  Official: 1.0,
  "Security-Scanned": 0.92,
  Community: 0.78,
  Unverified: 0.65,
};

const TIER_ICON: Record<TrustTier, React.ReactNode> = {
  Official: <Star size={11} aria-hidden />,
  "Security-Scanned": <ShieldCheck size={11} aria-hidden />,
  Community: <Users size={11} aria-hidden />,
  Unverified: <ShieldAlert size={11} aria-hidden />,
};

// ── Spacing constants (named, not magic numbers) ────────────────────────────
const SP_XS = 4;   // 4px — tight inline gaps (e.g. icon + label)
const SP_SM = 6;   // 6px — compact row gaps
const SP_MD = 8;   // 8px — medium gaps / cell padding
const SP_LG = 10;  // 10px — header/footer gaps
const SP_XL = 12;  // 12px — grid gap, inset padding
const SP_2XL = 14; // 14px — card inner gap default
const SP_3XL = 16; // 16px — page-level vertical gaps
const SP_4XL = 18; // 18px — card inner gap KPI
const SP_5XL = 20; // 20px — card horizontal padding
const SP_6XL = 24; // 24px — card vertical padding KPI / page top
const SP_7XL = 5;  // 5px — runtime chip row padding-y (dedicated name)

const CARD: React.CSSProperties = {
  background: "var(--surface-1)",
  border: "1px solid var(--border)",
  borderRadius: "var(--radius-xs)",
  padding: `${SP_4XL}px ${SP_5XL}px`,
  display: "flex",
  flexDirection: "column",
  gap: SP_2XL,
};

const CARD_KPI: React.CSSProperties = {
  ...CARD,
  padding: `${SP_6XL}px ${SP_6XL}px ${SP_5XL}px`,
  gap: SP_4XL,
};

const SECTION_LABEL: React.CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: SP_SM,
  color: "var(--muted)",
  fontSize: 10,
  letterSpacing: "0.08em",
  fontFamily: "var(--font-mono)",
  textTransform: "uppercase" as const,
};

// CSS value only (no property name prefix)
const TRANSITION = `opacity var(--dur-fast) var(--ease-decelerate), background var(--dur-fast) var(--ease-decelerate)`;

function deriveTierCounts(
  capabilities: RegistryCatalog["capabilities"],
): Record<TrustTier, number> {
  const counts: Record<TrustTier, number> = {
    Official: 0,
    "Security-Scanned": 0,
    Community: 0,
    Unverified: 0,
  };
  for (const cap of capabilities) {
    counts[cap.trust_tier] = (counts[cap.trust_tier] ?? 0) + 1;
  }
  return counts;
}

type DonutSlice = {
  tier: TrustTier;
  count: number;
  pct: number;
  color: string;
};

function buildDonutSlices(counts: Record<TrustTier, number>): DonutSlice[] {
  const total = Object.values(counts).reduce((s, n) => s + n, 0);
  if (total === 0) return [];
  const tiers: TrustTier[] = [
    "Official",
    "Security-Scanned",
    "Community",
    "Unverified",
  ];
  return tiers
    .filter((t) => counts[t] > 0)
    .map((t) => ({
      tier: t,
      count: counts[t],
      pct: counts[t] / total,
      color: TIER_COLOR[t],
    }));
}

function TrustDonut({ slices }: { slices: DonutSlice[] }) {
  const SIZE = 88;
  const CX = SIZE / 2;
  const CY = SIZE / 2;
  const R = 34;
  const INNER = 21;
  const GAP = 0.018;

  let cumulative = 0;
  const paths: React.ReactNode[] = slices.map((s) => {
    const start = cumulative + GAP / 2;
    const end = cumulative + s.pct - GAP / 2;
    cumulative += s.pct;

    const startAngle = start * 2 * Math.PI - Math.PI / 2;
    const endAngle = end * 2 * Math.PI - Math.PI / 2;

    const x1 = CX + R * Math.cos(startAngle);
    const y1 = CY + R * Math.sin(startAngle);
    const x2 = CX + R * Math.cos(endAngle);
    const y2 = CY + R * Math.sin(endAngle);
    const ix1 = CX + INNER * Math.cos(startAngle);
    const iy1 = CY + INNER * Math.sin(startAngle);
    const ix2 = CX + INNER * Math.cos(endAngle);
    const iy2 = CY + INNER * Math.sin(endAngle);
    const large = s.pct > 0.5 ? 1 : 0;

    const d = [
      `M ${x1} ${y1}`,
      `A ${R} ${R} 0 ${large} 1 ${x2} ${y2}`,
      `L ${ix2} ${iy2}`,
      `A ${INNER} ${INNER} 0 ${large} 0 ${ix1} ${iy1}`,
      "Z",
    ].join(" ");

    return (
      <path key={s.tier} d={d} fill={s.color} opacity={TIER_OPACITY[s.tier]} />
    );
  });

  return (
    <svg
      width={SIZE}
      height={SIZE}
      viewBox={`0 0 ${SIZE} ${SIZE}`}
      aria-hidden="true"
      focusable="false"
      style={{ flexShrink: 0 }}
    >
      {/* sunk track ring */}
      <circle
        cx={CX}
        cy={CY}
        r={(R + INNER) / 2}
        fill="none"
        stroke="var(--sunk)"
        strokeWidth={R - INNER}
        opacity={0.5}
      />
      {paths}
    </svg>
  );
}

function TrustTable({ slices }: { slices: DonutSlice[] }) {
  const total = slices.reduce((s, sl) => s + sl.count, 0);
  return (
    <table
      aria-label="Trust posture"
      style={{
        width: "100%",
        borderCollapse: "collapse",
        fontSize: 11,
        fontFamily: "var(--font-mono)",
      }}
    >
      <caption
        style={{
          captionSide: "top",
          textAlign: "left",
          color: "var(--faint)",
          fontSize: 10,
          paddingBottom: SP_SM,
          letterSpacing: "0.06em",
          textTransform: "uppercase",
        }}
      >
        Trust posture breakdown
      </caption>
      <thead>
        <tr>
          <th
            style={{
              textAlign: "left",
              color: "var(--faint)",
              paddingBottom: SP_7XL,
              fontWeight: 400,
              fontSize: 10,
            }}
          >
            Tier
          </th>
          <th
            style={{
              textAlign: "right",
              color: "var(--faint)",
              paddingBottom: SP_7XL,
              fontWeight: 400,
              fontSize: 10,
            }}
          >
            #
          </th>
          <th
            style={{
              textAlign: "right",
              color: "var(--faint)",
              paddingBottom: SP_7XL,
              fontWeight: 400,
              fontSize: 10,
            }}
          >
            %
          </th>
        </tr>
      </thead>
      <tbody>
        {slices.map((s) => (
          <tr key={s.tier}>
            {/* display:flex on <td> is invalid HTML spec — wrap icon+text in inner span */}
            <td
              style={{
                color: s.color,
                paddingBottom: SP_XS,
              }}
            >
              <span style={{ display: "flex", alignItems: "center", gap: SP_XS }}>
                <span aria-hidden="true">{TIER_ICON[s.tier]}</span>
                <span style={{ fontSize: 11 }}>{s.tier}</span>
              </span>
            </td>
            <td
              className="werk-num"
              style={{
                textAlign: "right",
                color: "var(--fg)",
                paddingBottom: SP_XS,
                fontSize: 12,
              }}
            >
              {s.count}
            </td>
            <td
              className="werk-num"
              style={{
                textAlign: "right",
                color: "var(--secondary)",
                paddingBottom: SP_XS,
                fontSize: 11,
              }}
            >
              {total > 0 ? Math.round((s.count / total) * 100) : 0}%
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

const RUNTIME_CHIP_COLOR: Record<string, string> = {
  "node.js": "var(--ok)",
  python: "var(--audit)",
  deno: "var(--interact)",
  bun: "var(--warn)",
};

// Map runtime names to distinct lucide icons for non-color differentiation
const RUNTIME_ICON: Record<string, React.ReactNode> = {
  "node.js": <Box size={11} aria-hidden />,
  python: <Globe size={11} aria-hidden />,
  deno: <Zap size={11} aria-hidden />,
};

function RuntimeChip({ name }: { name: string }) {
  const key = name.toLowerCase();
  const color = RUNTIME_CHIP_COLOR[key] ?? "var(--muted)";
  const icon = RUNTIME_ICON[key] ?? <Cpu size={11} aria-hidden />;
  return (
    // Remove aria-hidden so assistive tech can read the title
    <span
      title={name}
      style={{
        display: "inline-flex",
        alignItems: "center",
        justifyContent: "center",
        width: 20,
        height: 20,
        borderRadius: "var(--radius-sm)",
        background: "var(--surface-3)",
        border: "1px solid var(--border)",
        color,
        flexShrink: 0,
      }}
    >
      {icon}
    </span>
  );
}

export function Board() {
  const [status, setStatus] = useState<HubStatus | null>(null);
  const [runtimes, setRuntimes] = useState<RuntimesResponse | null>(null);
  const [registry, setRegistry] = useState<RegistryCatalog | null>(null);
  const [errors, setErrors] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  // Announcement message driven by state — concurrent-safe (no imperative ref write in .then)
  const [liveMessage, setLiveMessage] = useState("");
  // Ref kept for stable DOM node identity (aria live region must not unmount/remount)
  const statusRef = useRef<HTMLSpanElement>(null);

  // Runtime list hover state for dimming siblings
  const [hoveredRtIndex, setHoveredRtIndex] = useState<number | null>(null);

  useEffect(() => {
    let cancelled = false;

    Promise.all([
      fetchStatus().catch((e: unknown) => {
        if (!cancelled) {
          setErrors((prev) => [
            ...prev,
            `Status: ${e instanceof Error ? e.message : String(e)}`,
          ]);
        }
        return null;
      }),
      fetchRuntimes().catch((e: unknown) => {
        if (!cancelled) {
          setErrors((prev) => [
            ...prev,
            `Runtimes: ${e instanceof Error ? e.message : String(e)}`,
          ]);
        }
        return null;
      }),
      fetchRegistry().catch((e: unknown) => {
        if (!cancelled) {
          setErrors((prev) => [
            ...prev,
            `Registry: ${e instanceof Error ? e.message : String(e)}`,
          ]);
        }
        return null;
      }),
    ]).then(([s, r, reg]) => {
      if (cancelled) return;
      if (s) setStatus(s);
      if (r) setRuntimes(r);
      if (reg) setRegistry(reg);
      setLoading(false);
    });

    return () => {
      cancelled = true;
    };
  }, []);

  // Announce load completion in a React effect — concurrent-safe (no direct ref write in Promise.then)
  useEffect(() => {
    if (!loading) {
      setLiveMessage("Board loaded.");
    }
  }, [loading]);

  if (loading && errors.length === 0) {
    return (
      <p
        role="status"
        aria-live="polite"
        style={{
          padding: "24px 20px",
          color: "var(--muted)",
          fontFamily: "var(--font-mono)",
          fontSize: 12,
          letterSpacing: "0.06em",
        }}
      >
        Loading…
      </p>
    );
  }

  if (errors.length > 0 && !status && !runtimes && !registry) {
    return (
      <div role="alert" style={{ padding: "24px 20px" }}>
        {errors.map((e) => (
          <p
            key={e}
            style={{
              color: "var(--err)",
              fontFamily: "var(--font-mono)",
              fontSize: 12,
              margin: "0 0 6px",
            }}
          >
            {e}
          </p>
        ))}
      </div>
    );
  }

  const liveCount = (status?.processes ?? status?.servers)?.length ?? 0;
  const totalCount = status?.total_processes ?? 0;
  const chainVerified = status?.chain_verified;
  const hubName = status?.hub_name ?? "—";

  const detected = (runtimes?.detected ?? []).map((d) =>
    typeof d === "string" ? { name: d } : d,
  );
  const capabilities = registry?.capabilities ?? [];
  const tierCounts = deriveTierCounts(capabilities);
  const donutSlices = buildDonutSlices(tierCounts);

  const sortedRuntimes = [...detected].sort((a, b) => {
    const av = parseFloat(a.version ?? "0");
    const bv = parseFloat(b.version ?? "0");
    return bv - av;
  });

  const liveRatio = totalCount > 0 ? liveCount / totalCount : 0;
  const kpiColor =
    liveRatio >= 0.8
      ? "var(--ok)"
      : liveRatio >= 0.4
        ? "var(--warn)"
        : "var(--err)";

  const kpiPct = Math.round(liveRatio * 100);
  const kpiStatusLabel =
    liveRatio >= 0.8
      ? "healthy"
      : liveRatio >= 0.4
        ? "degraded"
        : "critical";

  return (
    <main
      style={{
        padding: `${SP_5XL}px ${SP_5XL}px ${SP_6XL}px`,
        display: "flex",
        flexDirection: "column",
        gap: SP_3XL,
        minHeight: 0,
      }}
    >
      {/* Persistent live region for load completion announcements (WCAG 4.1.3) */}
      <span
        ref={statusRef}
        role="status"
        aria-live="polite"
        className="sr-only"
      >
        {liveMessage}
      </span>

      {/* ── Header row ── */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: SP_LG,
          borderBottom: "1px solid var(--border)",
          paddingBottom: SP_XL,
        }}
      >
        <span
          className="werk-label"
          style={{ color: "var(--muted)", fontSize: 10 }}
        >
          BOARD
        </span>
        <span
          style={{
            color: "var(--secondary)",
            fontSize: 13,
            fontFamily: "var(--font-ui)",
          }}
        >
          {hubName}
        </span>

        {chainVerified !== undefined && (
          <span
            className="werk-stamp"
            style={{
              color: chainVerified ? "var(--ok)" : "var(--err)",
              borderColor: chainVerified
                ? "var(--state-approve)"
                : "var(--state-danger)",
              marginLeft: "auto",
              display: "inline-flex",
              alignItems: "center",
              gap: SP_7XL,
              fontSize: 10,
            }}
          >
            {chainVerified ? (
              <CheckCircle2 size={11} aria-hidden />
            ) : (
              <AlertTriangle size={11} aria-hidden />
            )}
            {chainVerified ? "CHAIN VERIFIED" : "CHAIN ERROR"}
          </span>
        )}
      </div>

      {/* ── Partial-failure notices ── */}
      {errors.length > 0 && (
        <div
          role="alert"
          style={{
            background: "var(--state-danger)",
            border: "1px solid var(--state-danger)",
            borderRadius: "var(--radius-sm)",
            padding: `${SP_MD}px ${SP_XL}px`,
          }}
        >
          {errors.map((e) => (
            <p
              key={e}
              style={{
                color: "var(--err)",
                fontSize: 11,
                fontFamily: "var(--font-mono)",
                margin: "0 0 2px",
              }}
            >
              {e}
            </p>
          ))}
        </div>
      )}

      {/* ── Bento grid — KPI card dominates with span 2 ── */}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "1fr 1.4fr 1fr",
          gap: SP_XL,
          alignItems: "start",
        }}
      >
        {/* ① Hero KPI — wider padding + gap for hierarchy */}
        <div style={CARD_KPI}>
          <div style={SECTION_LABEL}>
            <Activity size={12} aria-hidden />
            CONNECTORS
          </div>

          <div
            style={{
              display: "flex",
              alignItems: "baseline",
              gap: 0,
              flexWrap: "wrap",
            }}
          >
            {/* live count — hero */}
            <span
              className="werk-num"
              style={{
                fontSize: 48,
                lineHeight: 1,
                color: kpiColor,
                letterSpacing: "-0.02em",
                marginRight: SP_SM,
                transition: TRANSITION,
              }}
            >
              {liveCount}
            </span>
            <span
              style={{
                color: "var(--muted)",
                fontSize: 12,
                fontFamily: "var(--font-mono)",
                alignSelf: "flex-end",
                marginBottom: SP_SM,
                marginRight: SP_XL,
              }}
            >
              live
            </span>

            {/* divider */}
            <span
              style={{
                color: "var(--border-strong)",
                fontSize: 28,
                lineHeight: 1,
                alignSelf: "center",
                marginRight: SP_XL,
                userSelect: "none",
              }}
            >
              /
            </span>

            {/* total */}
            <span
              className="werk-num"
              style={{
                fontSize: 28,
                lineHeight: 1,
                color: "var(--secondary)",
                letterSpacing: "-0.01em",
                marginRight: SP_SM,
              }}
            >
              {totalCount}
            </span>
            <span
              style={{
                color: "var(--faint)",
                fontSize: 12,
                fontFamily: "var(--font-mono)",
                alignSelf: "flex-end",
                marginBottom: SP_XS,
              }}
            >
              total
            </span>
          </div>

          {/* utilisation bar — 3px height + accessible text */}
          <div>
            <div
              style={{
                height: 3,
                background: "var(--sunk)",
                borderRadius: "var(--radius-xs)",
                overflow: "hidden",
                position: "relative",
              }}
            >
              <div
                style={{
                  height: "100%",
                  width: `${kpiPct}%`,
                  background: kpiColor,
                  borderRadius: "var(--radius-xs)",
                  transition: `width var(--dur-slow) var(--ease-decelerate)`,
                }}
              />
            </div>
            <div
              style={{
                display: "flex",
                justifyContent: "flex-end",
                marginTop: 3,
              }}
            >
              <span
                className="werk-num"
                style={{ fontSize: 10, color: "var(--faint)" }}
              >
                {kpiPct}%
              </span>
            </div>
            {/* Visually hidden status for WCAG 1.4.1 (color not sole differentiator) */}
            <span
              style={{
                position: "absolute",
                width: 1,
                height: 1,
                overflow: "hidden",
                clip: "rect(0,0,0,0)",
                whiteSpace: "nowrap",
              }}
            >
              Status: {kpiStatusLabel} ({kpiPct}%)
            </span>
          </div>
        </div>

        {/* ② Trust posture */}
        <div style={CARD}>
          <div style={SECTION_LABEL}>
            <ShieldCheck size={12} aria-hidden />
            TRUST POSTURE
          </div>

          {donutSlices.length === 0 ? (
            <div
              style={{
                display: "flex",
                flexDirection: "column",
                alignItems: "center",
                gap: SP_MD,
                padding: `${SP_3XL}px 0`,
                color: "var(--faint)",
              }}
            >
              <HelpCircle size={22} aria-hidden />
              <span
                style={{
                  fontFamily: "var(--font-mono)",
                  fontSize: 11,
                  letterSpacing: "0.04em",
                }}
              >
                No registry data.
              </span>
            </div>
          ) : (
            <div
              style={{
                display: "flex",
                gap: SP_2XL,
                alignItems: "flex-start",
              }}
            >
              <TrustDonut slices={donutSlices} />
              <div style={{ flex: 1, minWidth: 0 }}>
                <TrustTable slices={donutSlices} />
              </div>
            </div>
          )}
        </div>

        {/* ③ Runtimes */}
        <div style={CARD}>
          <div style={SECTION_LABEL}>
            <Cpu size={12} aria-hidden />
            RUNTIMES
          </div>

          {sortedRuntimes.length === 0 ? (
            <div
              style={{
                display: "flex",
                flexDirection: "column",
                alignItems: "center",
                gap: SP_MD,
                padding: `${SP_3XL}px 0`,
                color: "var(--faint)",
              }}
            >
              <HelpCircle size={22} aria-hidden />
              <span
                style={{
                  fontFamily: "var(--font-mono)",
                  fontSize: 11,
                  letterSpacing: "0.04em",
                }}
              >
                No runtimes detected.
              </span>
            </div>
          ) : (
            <ul
              style={{
                listStyle: "none",
                margin: 0,
                padding: 0,
                display: "flex",
                flexDirection: "column",
                gap: SP_XS,
              }}
            >
              {sortedRuntimes.map((rt, i) => {
                const isHovered = hoveredRtIndex === i;
                const isDimmed =
                  hoveredRtIndex !== null && hoveredRtIndex !== i;
                return (
                  <li
                    key={`${rt.name ?? "?"}-${i}`}
                    onMouseEnter={() => setHoveredRtIndex(i)}
                    onMouseLeave={() => setHoveredRtIndex(null)}
                    style={{
                      display: "flex",
                      alignItems: "center",
                      gap: SP_MD,
                      padding: `${SP_7XL}px ${SP_SM}px`,
                      borderRadius: "var(--radius-sm)",
                      background: isHovered ? "var(--state-accent)" : "var(--surface-2)",
                      border: "1px solid var(--border)",
                      boxShadow: isHovered
                        ? "inset 2px 0 0 var(--interact)"
                        : "none",
                      opacity: isDimmed ? 0.75 : 1,
                      transition: `box-shadow var(--dur-fast) var(--ease-decelerate), background var(--dur-fast) var(--ease-decelerate), opacity var(--dur-fast) var(--ease-decelerate)`,
                    }}
                  >
                    <RuntimeChip name={rt.name ?? ""} />
                    <span
                      style={{
                        color: "var(--fg)",
                        fontSize: 12,
                        fontFamily: "var(--font-ui)",
                        flex: 1,
                        minWidth: 0,
                        overflow: "hidden",
                        textOverflow: "ellipsis",
                        whiteSpace: "nowrap",
                      }}
                    >
                      {rt.name ?? "unknown"}
                    </span>
                    <span
                      className="werk-num"
                      style={{
                        color: "var(--muted)",
                        fontSize: 11,
                        flexShrink: 0,
                      }}
                    >
                      {rt.version ?? "—"}
                    </span>
                  </li>
                );
              })}
            </ul>
          )}
        </div>
      </div>
    </main>
  );
}
