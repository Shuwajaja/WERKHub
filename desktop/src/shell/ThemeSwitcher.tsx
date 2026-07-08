import { useState } from "react";

// The 4 themes come from design-system.json (generated into tokens.css as
// :root + [data-theme]). Add a theme there + regen, and add its key here.
const THEMES = ["graphite", "light", "paper", "nord"] as const;
type Theme = (typeof THEMES)[number];

// Per-theme preview dot = that theme's interaction accent (distinct at a glance).
const ACCENT: Record<Theme, string> = {
  graphite: "#6b78d6",
  light: "#5159b8",
  paper: "#c89a4e",
  nord: "#5fb0c9",
};

const STORAGE_KEY = "werk-theme";

function applyTheme(t: Theme) {
  document.documentElement.setAttribute("data-theme", t);
  try {
    localStorage.setItem(STORAGE_KEY, t);
  } catch {
    // storage unavailable — theme still applies for the session
  }
}

function initialTheme(): Theme {
  try {
    const saved = localStorage.getItem(STORAGE_KEY);
    if (saved && (THEMES as readonly string[]).includes(saved)) return saved as Theme;
  } catch {
    // ignore
  }
  return "graphite";
}

export function ThemeSwitcher() {
  const [theme, setTheme] = useState<Theme>(initialTheme);

  const handleSelect = (t: Theme) => {
    setTheme(t);
    applyTheme(t);
  };

  return (
    <div role="group" aria-label="Theme" style={{ display: "flex", gap: 5 }}>
      {THEMES.map((t) => {
        const isActive = theme === t;
        return (
          <button
            key={t}
            type="button"
            aria-label={`${t} theme`}
            aria-pressed={isActive}
            title={t}
            onClick={() => handleSelect(t)}
            style={{
              width: 14,
              height: 14,
              borderRadius: "50%",
              padding: 0,
              cursor: "pointer",
              background: ACCENT[t],
              border: isActive
                ? "2px solid var(--fg)"
                : "1px solid var(--border-strong)",
              transition: "border-color var(--dur-fast) var(--ease-decelerate)",
            }}
          />
        );
      })}
    </div>
  );
}
