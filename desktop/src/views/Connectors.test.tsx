import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, it, expect, vi } from "vitest";
import { Connectors } from "./Connectors";
import * as client from "../api/client";

const cards = [
  {
    id: "fs",
    name: "Filesystem",
    transport: "stdio",
    trust_tier: "Security-Scanned" as const,
    enabled: true,
    pid: 4012,
    idle_for_s: 12,
  },
];

describe("Connectors", () => {
  it("loads, lists connectors with trust stamp, and shows detail on select", async () => {
    vi.spyOn(client, "fetchConnectors").mockResolvedValue(cards);
    render(<Connectors />);
    expect(await screen.findByText("Filesystem")).toBeInTheDocument();
    expect(screen.getByText(/SECURITY-SCANNED/i)).toBeInTheDocument();
    await userEvent.click(screen.getByText("Filesystem"));
    expect(await screen.findByText(/pid/i)).toBeInTheDocument();
    expect(screen.getByText("4012")).toBeInTheDocument();
  });

  it("renders an empty state when there are no connectors", async () => {
    vi.spyOn(client, "fetchConnectors").mockResolvedValue([]);
    render(<Connectors />);
    expect(await screen.findByText(/no connectors/i)).toBeInTheDocument();
  });

  it("surfaces a visible error when the fetch fails (honest-degrade)", async () => {
    vi.spyOn(client, "fetchConnectors").mockRejectedValue(
      new Error("hub request failed: 503"),
    );
    render(<Connectors />);
    expect(await screen.findByText(/503/)).toBeInTheDocument();
  });
});
