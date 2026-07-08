import { useState } from "react";
import { Cockpit } from "./shell/Cockpit";
import { ErrorBoundary } from "./shell/ErrorBoundary";
import { ThemeSwitcher } from "./shell/ThemeSwitcher";
import type { ViewId } from "./shell/IconRail";
import { Board } from "./views/Board";
import { Timeline } from "./views/Timeline";
import { Connectors } from "./views/Connectors";
import { Onboard } from "./views/Onboard";
import { Registry } from "./views/Registry";
import { Approvals } from "./views/Approvals";
import { Permissions } from "./views/Permissions";
import { Console } from "./views/Console";

const VIEWS: Record<ViewId, () => React.JSX.Element> = {
  board: Board,
  timeline: Timeline,
  connectors: Connectors,
  onboard: Onboard,
  registry: Registry,
  approvals: Approvals,
  permissions: Permissions,
};

export function App() {
  const [view, setView] = useState<ViewId>("board");
  const [consoleOpen, setConsoleOpen] = useState(false);
  const Active = VIEWS[view];

  return (
    <div style={{ height: "100dvh", display: "flex", flexDirection: "column" }}>
      <div style={{ flex: 1, minHeight: 0 }}>
        <Cockpit
          active={view}
          onSelect={setView}
          commandBarRight={<ThemeSwitcher />}
        >
          <ErrorBoundary resetKey={view}>
            <Active />
          </ErrorBoundary>
        </Cockpit>
      </div>

      <div style={{ flexShrink: 0, borderTop: "1px solid var(--border)" }}>
        <button
          onClick={() => setConsoleOpen((o) => !o)}
          aria-expanded={consoleOpen}
          aria-controls="console-dock"
          className="werk-label"
          style={{
            width: "100%",
            textAlign: "left",
            background: "var(--surface-1)",
            border: "none",
            color: "var(--muted)",
            padding: "6px 12px",
            cursor: "pointer",
          }}
        >
          <span aria-hidden="true">{consoleOpen ? "▾" : "▸"}</span> Console
        </button>
        {consoleOpen && (
          <div
            id="console-dock"
            style={{ height: 220, overflow: "hidden", background: "var(--sunk)" }}
          >
            <ErrorBoundary resetKey="console">
              <Console />
            </ErrorBoundary>
          </div>
        )}
      </div>
    </div>
  );
}
