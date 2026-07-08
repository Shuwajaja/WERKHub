import type { ReactNode } from "react";
import { IconRail, type ViewId } from "./IconRail";
import { CommandBar } from "./CommandBar";

export function Cockpit({
  active,
  onSelect,
  children,
  commandBarRight,
}: {
  active: ViewId;
  onSelect: (v: ViewId) => void;
  children: ReactNode;
  commandBarRight?: ReactNode;
}) {
  return (
    <div style={{ height: "100%", display: "flex", flexDirection: "column" }}>
      <CommandBar right={commandBarRight} />
      <div style={{ flex: 1, display: "flex", minHeight: 0 }}>
        <IconRail active={active} onSelect={onSelect} />
        <main
          style={{ flex: 1, minWidth: 0, overflow: "auto", padding: 16 }}
        >
          {children}
        </main>
      </div>
    </div>
  );
}
