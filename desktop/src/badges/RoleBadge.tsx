// PORTABLE ASSET — for WERKAgent crew badges, NOT used by WERKHub itself.
// Self-contained: depends only on ./roles (pure) + the .werk-stamp CSS class and
// A-FINAL CSS vars, which WERKAgent already ships. Lift this file + roles.ts as-is.
import { resolveFamily } from "./roles";

// Colored expert-role badge. Color = family, text = the specific role.
export function RoleBadge({ role }: { role: string }) {
  const fam = resolveFamily(role);
  return (
    <span
      className="werk-stamp"
      aria-label={`role: ${role}`}
      title={`${fam.label} · ${role}`}
      style={{
        color: fam.color,
        borderColor: fam.color,
        background: `color-mix(in srgb, ${fam.color} 8%, transparent)`,
        whiteSpace: "nowrap",
      }}
    >
      {role.toUpperCase()}
    </span>
  );
}
