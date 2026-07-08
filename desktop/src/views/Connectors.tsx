import { useEffect, useState } from "react";
import {
  CheckCircle,
  ShieldCheck,
  Users,
  AlertTriangle,
  Cpu,
  Clock,
  Wrench,
  Hash,
  ServerCrash,
  Layers,
  type LucideIcon,
} from "lucide-react";
import { fetchConnectors } from "../api/client";
import type { ServerCard, TrustTier } from "../api/types";

/* ── tier config ──────────────────────────────────────────────────────────── */

type TierConfig = {
  color: string;
  Icon: LucideIcon;
};

const TIER: Record<TrustTier, TierConfig> = {
  Official: { color: "var(--audit)", Icon: CheckCircle },
  "Security-Scanned": { color: "var(--ok)", Icon: ShieldCheck },
  Community: { color: "var(--muted)", Icon: Users },
  Unverified: { color: "var(--warn)", Icon: AlertTriangle },
};

/* ── sub-components ───────────────────────────────────────────────────────── */

function LoadingState() {
  return (
    <div
      role="status"
      aria-live="polite"
      aria-atomic="true"
      style={{
        display: "flex",
        alignItems: "center",
        gap: 8,
        padding: "24px 20px",
        color: "var(--muted)",
        fontFamily: "var(--font-ui)",
        fontSize: 13,
      }}
    >
      <Layers size={14} aria-hidden="true" style={{ opacity: 0.5 }} />
      Loading connectors…
    </div>
  );
}

function ErrorState({ message }: { message: string }) {
  return (
    <div
      role="alert"
      style={{
        display: "flex",
        alignItems: "flex-start",
        gap: 10,
        padding: "20px 20px",
        color: "var(--err)",
        fontFamily: "var(--font-ui)",
        fontSize: 13,
        lineHeight: 1.5,
      }}
    >
      <ServerCrash size={15} aria-hidden="true" style={{ marginTop: 1, flexShrink: 0 }} />
      <span>
        <span style={{ fontVariationSettings: "'wght' 590" }}>Failed to load connectors.</span>{" "}
        {message}
      </span>
    </div>
  );
}

function EmptyState() {
  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        gap: 10,
        padding: "48px 24px",
        color: "var(--muted)",
        fontFamily: "var(--font-ui)",
        fontSize: 13,
        textAlign: "center",
      }}
    >
      <Layers size={22} aria-hidden="true" style={{ opacity: 0.3, marginBottom: 4 }} />
      <span style={{ color: "var(--secondary)", fontWeight: 500, fontSize: 13 }}>
        No connectors yet.
      </span>
      <span style={{ color: "var(--muted)", fontSize: 12 }}>
        Add one from the registry.
      </span>
    </div>
  );
}

/* ── trust tier stamp ─────────────────────────────────────────────────────── */

function TierStamp({ tier, tierConfig }: { tier: string; tierConfig: TierConfig }) {
  return (
    <span
      className="werk-stamp"
      style={{
        color: tierConfig.color,
        flexShrink: 0,
        fontSize: 10,
        letterSpacing: "0.04em",
        border: `1px solid ${tierConfig.color}`,
        borderRadius: 2,
        padding: "1px 4px",
        /* 8% opacity background via hex-alpha on top of the color */
        background: "transparent",
        boxShadow: `inset 0 0 0 100px color-mix(in srgb, ${tierConfig.color} 8%, transparent)`,
      }}
    >
      <span className="sr-only">Trust tier: </span>
      {tier.toUpperCase()}
    </span>
  );
}

/* ── connector row ────────────────────────────────────────────────────────── */

function ConnectorRow({
  card,
  selected,
  onSelect,
}: {
  card: ServerCard;
  selected: boolean;
  onSelect: () => void;
}) {
  const tier = TIER[card.trust_tier];
  return (
    <li style={{ listStyle: "none" }}>
      <button
        onClick={onSelect}
        className={`connector-row-btn${selected ? " werk-notch" : ""}`}
        aria-pressed={selected}
        aria-label={`${card.name}, trust tier: ${card.trust_tier}`}
        style={{
          width: "100%",
          textAlign: "left",
          background: selected ? "var(--surface-2)" : "transparent",
          border: "none",
          borderBottom: "1px solid var(--border)",
          color: "var(--fg)",
          paddingTop: 9,
          paddingBottom: 9,
          paddingLeft: 14,
          paddingRight: 14,
          cursor: "pointer",
          display: "flex",
          alignItems: "center",
          gap: 10,
          transition: "background var(--dur-fast) var(--ease-decelerate), box-shadow var(--dur-fast) var(--ease-decelerate)",
        }}
        onMouseEnter={(e) => {
          if (!selected) {
            (e.currentTarget as HTMLButtonElement).style.background =
              "var(--surface-1)";
          }
        }}
        onMouseLeave={(e) => {
          if (!selected) {
            (e.currentTarget as HTMLButtonElement).style.background = "transparent";
          }
        }}
      >
        {/* tier icon — fixed 20px column so all rows align */}
        <span
          style={{
            width: 20,
            height: 20,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            flexShrink: 0,
            opacity: selected ? 1 : 0.8,
          }}
        >
          <tier.Icon
            size={14}
            aria-hidden="true"
            style={{ color: tier.color }}
          />
        </span>

        {/* name */}
        <span
          style={{
            flex: 1,
            fontFamily: "var(--font-ui)",
            fontSize: 13,
            fontWeight: selected ? 500 : 400,
            color: selected ? "var(--fg)" : "var(--secondary)",
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
          }}
        >
          {card.name}
        </span>

        {/* trust stamp */}
        <TierStamp tier={card.trust_tier} tierConfig={tier} />
      </button>
    </li>
  );
}

/* ── detail field ─────────────────────────────────────────────────────────── */

function DetailField({
  Icon,
  label,
  value,
  mono = true,
  small = false,
}: {
  Icon: LucideIcon;
  label: string;
  value: string | number | undefined | null;
  mono?: boolean;
  small?: boolean;
}) {
  const display = value !== null && value !== undefined ? String(value) : "—";
  return (
    <div
      style={{
        display: "grid",
        gridTemplateColumns: "16px 1fr",
        gridTemplateRows: "auto auto",
        columnGap: 8,
        rowGap: 2,
        alignItems: "start",
      }}
    >
      <Icon
        size={small ? 11 : 13}
        aria-hidden="true"
        style={{ color: "var(--faint)", marginTop: 2, gridRow: "1 / 3" }}
      />
      <span
        className="werk-label"
        style={{
          fontSize: 9,
          color: "var(--muted)",
          letterSpacing: "0.08em",
          lineHeight: 1.2,
        }}
      >
        {label}
      </span>
      <span
        className={mono ? "werk-num" : undefined}
        style={{
          fontSize: small ? 11 : 13,
          color: small ? "var(--secondary)" : "var(--fg)",
          lineHeight: 1.3,
        }}
      >
        {display}
      </span>
    </div>
  );
}

/* ── detail panel ─────────────────────────────────────────────────────────── */

function DetailPanel({ card }: { card: ServerCard }) {
  const tier = TIER[card.trust_tier];
  return (
    <section
      aria-label={`Details for ${card.name}`}
      style={{
        display: "flex",
        flexDirection: "column",
        gap: 0,
        height: "100%",
        overflow: "auto",
      }}
    >
      {/* header */}
      <div
        style={{
          padding: "14px 18px 12px",
          borderBottom: "1px solid var(--border-strong)",
          display: "flex",
          alignItems: "flex-start",
          gap: 10,
        }}
      >
        <tier.Icon
          size={16}
          aria-hidden="true"
          style={{ color: tier.color, flexShrink: 0, marginTop: 2 }}
        />
        <div style={{ flex: 1, minWidth: 0 }}>
          <h2
            style={{
              margin: 0,
              fontFamily: "var(--font-ui)",
              fontSize: 16,
              fontVariationSettings: "'wght' 590",
              color: "var(--fg)",
              overflow: "hidden",
              textOverflow: "ellipsis",
              whiteSpace: "nowrap",
              lineHeight: 1.3,
            }}
          >
            {card.name}
          </h2>
          <span
            style={{
              display: "block",
              fontFamily: "var(--font-ui)",
              fontSize: 11,
              color: "var(--secondary)",
              marginTop: 2,
              overflow: "hidden",
              textOverflow: "ellipsis",
              whiteSpace: "nowrap",
            }}
          >
            {card.transport}
          </span>
        </div>
        <TierStamp tier={card.trust_tier} tierConfig={tier} />
      </div>

      {/* readout grid — two-tier rhythm */}
      <div
        style={{
          padding: "16px 18px",
          display: "flex",
          flexDirection: "column",
          gap: 16,
        }}
      >
        {/* primary row: high-signal fields */}
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "0 24px" }}>
          <DetailField Icon={Wrench} label="TOOLS" value={card.tool_count} />
          <DetailField Icon={Cpu} label="TRANSPORT" value={card.transport} mono={false} />
        </div>
        {/* secondary row: metadata */}
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "0 24px" }}>
          <DetailField Icon={Hash} label="PID" value={card.pid} small />
          <DetailField Icon={Clock} label="IDLE (S)" value={card.idle_for_s} small />
        </div>
      </div>
    </section>
  );
}

function EmptyDetail() {
  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        paddingLeft: 18,
        paddingTop: "40%",
        height: "100%",
        boxSizing: "border-box",
        color: "var(--faint)",
        fontFamily: "var(--font-ui)",
        fontSize: 12,
        letterSpacing: "0.02em",
      }}
    >
      <Layers
        size={18}
        aria-hidden="true"
        style={{ opacity: 0.4, marginBottom: 8, color: "var(--faint)" }}
      />
      Select a connector.
    </div>
  );
}

/* ── main view ────────────────────────────────────────────────────────────── */

export function Connectors() {
  const [cards, setCards] = useState<ServerCard[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [sel, setSel] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    fetchConnectors()
      .then((data) => {
        if (!controller.signal.aborted) setCards(data);
      })
      .catch((e) => {
        if (!controller.signal.aborted) setErr(String(e?.message ?? e));
      });
    return () => controller.abort();
  }, []);

  if (err) return <ErrorState message={err} />;
  if (!cards) return <LoadingState />;
  if (cards.length === 0) return <EmptyState />;

  const active = cards.find((c) => c.id === sel) ?? null;

  return (
    <div style={{ display: "flex", height: "100%", overflow: "hidden" }}>
      {/* list panel */}
      <ul
        aria-label="Connector list"
        style={{
          width: "42%",
          minWidth: 180,
          margin: 0,
          padding: 0,
          listStyle: "none",
          borderRight: "1px solid var(--border)",
          overflowY: "auto",
          flexShrink: 0,
        }}
      >
        {cards.map((c) => (
          <ConnectorRow
            key={c.id}
            card={c}
            selected={sel === c.id}
            onSelect={() => setSel(c.id)}
          />
        ))}
      </ul>

      {/* detail panel */}
      <div style={{ flex: 1, overflow: "hidden", background: "var(--surface-1)" }}>
        {active ? <DetailPanel card={active} /> : <EmptyDetail />}
      </div>
    </div>
  );
}
