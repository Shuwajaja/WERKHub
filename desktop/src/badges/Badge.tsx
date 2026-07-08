// WERKHub badges: who is connected (identity) + what access posture they carry.
// The engineering-role badge lives in RoleBadge.tsx (portable, for WERKAgent).
import { shortModel, vendorOf, VENDOR_COLOR, resolveAccess } from "./roles";

// Neutral model / CLI / harness identity badge — short, simple, one vendor dot.
export function IdentityBadge({ model }: { model: string }) {
  const label = shortModel(model);
  const dot = VENDOR_COLOR[vendorOf(model)];
  return (
    <span
      aria-label={`model: ${model}`}
      title={model}
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 6,
        fontFamily: "var(--font-mono)",
        fontSize: 11,
        color: "var(--secondary)",
        background: "var(--surface-2)",
        border: "1px solid var(--border-strong)",
        borderRadius: 3,
        padding: "2px 8px",
        whiteSpace: "nowrap",
      }}
    >
      <span
        aria-hidden="true"
        style={{ width: 6, height: 6, borderRadius: "50%", background: dot, flexShrink: 0 }}
      />
      {label}
    </span>
  );
}

// WERKHub access-posture badge — admin / balanced / read_only (the real role axis).
export function AccessBadge({ level }: { level: string }) {
  const a = resolveAccess(level);
  return (
    <span
      className="werk-stamp"
      aria-label={`access: ${a.level}`}
      title={`access posture: ${a.label}`}
      style={{
        color: a.color,
        borderColor: a.color,
        background: `color-mix(in srgb, ${a.color} 8%, transparent)`,
        whiteSpace: "nowrap",
      }}
    >
      {a.label}
    </span>
  );
}
