import type { ReactNode } from "react";

export function CommandBar({ right }: { right?: ReactNode }) {
  return (
    <header
      aria-label="Application toolbar"
      style={{
        height: 36,
        display: "flex",
        alignItems: "center",
        gap: 0,
        padding: "0 0 0 12px",
        background: "var(--surface-1)",
        borderBottom: "1px solid var(--border)",
        flexShrink: 0,
      }}
    >
      <img
        src="/seam-w.svg"
        width={14}
        height={14}
        alt=""
        aria-hidden="true"
        style={{ display: "block", flexShrink: 0, opacity: 0.9 }}
      />
      <span
        style={{
          width: 1,
          height: 16,
          background: "var(--border-strong)",
          margin: "0 10px",
          flexShrink: 0,
        }}
        aria-hidden="true"
      />
      <span
        className="werk-label"
        style={{
          color: "var(--muted)",
        }}
      >
        WERKHUB
      </span>
      <div
        style={{
          marginLeft: "auto",
          display: "flex",
          alignItems: "center",
          paddingRight: 12,
          gap: 8,
        }}
      >
        {right ?? (
          <span
            className="werk-label"
            style={{
              color: "var(--faint)",
            }}
          >
            READY
          </span>
        )}
      </div>
    </header>
  );
}
