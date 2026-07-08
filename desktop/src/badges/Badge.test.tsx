import { render, screen } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import { IdentityBadge, AccessBadge } from "./Badge";
import { RoleBadge } from "./RoleBadge";
import { FAMILIES } from "./roles";

describe("IdentityBadge", () => {
  it("renders a terse model label with an accessible name", () => {
    render(<IdentityBadge model="claude-opus-4-8" />);
    const el = screen.getByText("claude opus");
    expect(el).toBeInTheDocument();
    expect(el).toHaveAttribute("aria-label", expect.stringMatching(/claude-opus-4-8/));
  });
});

describe("AccessBadge", () => {
  it("renders the known access posture", () => {
    render(<AccessBadge level="admin" />);
    const el = screen.getByText("ADMIN");
    expect(el).toHaveAttribute("aria-label", "access: admin");
    expect(el).toHaveAttribute("title", expect.stringMatching(/ADMIN/));
  });

  it("read_only normalizes its label", () => {
    render(<AccessBadge level="read_only" />);
    expect(screen.getByText("READ-ONLY")).toBeInTheDocument();
  });

  it("falls back gracefully for an unknown posture", () => {
    render(<AccessBadge level="weird" />);
    expect(screen.getByText("WEIRD")).toBeInTheDocument();
  });
});

describe("RoleBadge (portable / WERKAgent)", () => {
  it("renders the role label and colors by family", () => {
    render(<RoleBadge role="planner" />);
    const el = screen.getByText(/planner/i);
    expect(el).toBeInTheDocument();
    expect(el.style.color).toBeTruthy();
    expect(el).toHaveAttribute("aria-label", expect.stringMatching(/planner/i));
  });

  it("falls back to the muted unknown family for unknown roles", () => {
    render(<RoleBadge role="made-up" />);
    const el = screen.getByText(/made-up/i);
    expect(el).toHaveAttribute("title", expect.stringContaining(FAMILIES.unknown.label));
  });
});
