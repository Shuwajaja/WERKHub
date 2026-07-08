import {
  LayoutGrid,
  Activity,
  Plug,
  DownloadCloud,
  BookMarked,
  ShieldCheck,
  KeyRound,
  type LucideIcon,
} from "lucide-react";

export type ViewId =
  | "board"
  | "timeline"
  | "connectors"
  | "onboard"
  | "registry"
  | "approvals"
  | "permissions";

type NavGroup = { id: ViewId; label: string; Icon: LucideIcon }[];

const OPERATIONAL: NavGroup = [
  { id: "board", label: "Board", Icon: LayoutGrid },
  { id: "timeline", label: "Timeline", Icon: Activity },
  { id: "connectors", label: "Connectors", Icon: Plug },
  { id: "onboard", label: "Onboard", Icon: DownloadCloud },
];

const GOVERNANCE: NavGroup = [
  { id: "registry", label: "Registry", Icon: BookMarked },
  { id: "approvals", label: "Approvals", Icon: ShieldCheck },
  { id: "permissions", label: "Permissions", Icon: KeyRound },
];

export function IconRail({
  active,
  onSelect,
}: {
  active: ViewId;
  onSelect: (v: ViewId) => void;
}) {
  return (
    <>
      <style>{`
        .werk-rail-btn {
          height: 44px;
          border: none;
          cursor: pointer;
          display: flex;
          align-items: center;
          justify-content: center;
          transition: color var(--dur-fast) var(--ease-decelerate), box-shadow var(--dur-fast) var(--ease-decelerate);
          background: transparent;
          color: var(--muted);
          box-shadow: inset 2px 0 0 transparent;
          padding-left: 0;
          position: relative;
          width: 100%;
        }
        .werk-rail-btn:hover {
          color: var(--interact);
          background: transparent;
        }
        .werk-rail-btn.is-active {
          color: var(--fg);
          box-shadow: inset 2px 0 0 var(--interact);
          background: transparent;
        }
        @media (prefers-reduced-motion: reduce) {
          .werk-rail-btn { transition: none; }
        }
        .werk-rail-indicator {
          width: 32px;
          height: 32px;
          border-radius: var(--radius-xs);
          display: flex;
          align-items: center;
          justify-content: center;
          transition: background var(--dur-fast) var(--ease-decelerate);
        }
        .werk-rail-btn.is-active .werk-rail-indicator {
          background: var(--surface-3);
        }
        .werk-rail-btn:hover .werk-rail-indicator {
          background: var(--float);
        }
        @media (prefers-reduced-motion: reduce) {
          .werk-rail-indicator { transition: none; }
        }
        .werk-rail-divider {
          height: 8px;
          flex-shrink: 0;
        }
      `}</style>
      <nav
        aria-label="Views"
        style={{
          width: 56,
          background: "var(--surface-1)",
          borderRight: "1px solid var(--border)",
          display: "flex",
          flexDirection: "column",
          paddingTop: 8,
        }}
      >
        {OPERATIONAL.map(({ id, label, Icon }) => {
          const isActive = active === id;
          return (
            <button
              key={id}
              aria-label={label}
              title={label}
              aria-current={isActive ? "page" : undefined}
              onClick={() => onSelect(id)}
              className={`werk-rail-btn${isActive ? " is-active" : ""}`}
            >
              <span className="werk-rail-indicator">
                <Icon size={18} aria-hidden="true" />
              </span>
            </button>
          );
        })}
        <div className="werk-rail-divider" aria-hidden="true" />
        {GOVERNANCE.map(({ id, label, Icon }) => {
          const isActive = active === id;
          return (
            <button
              key={id}
              aria-label={label}
              title={label}
              aria-current={isActive ? "page" : undefined}
              onClick={() => onSelect(id)}
              className={`werk-rail-btn${isActive ? " is-active" : ""}`}
            >
              <span className="werk-rail-indicator">
                <Icon size={18} aria-hidden="true" />
              </span>
            </button>
          );
        })}
      </nav>
    </>
  );
}
