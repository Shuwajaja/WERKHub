import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { Onboard } from "./Onboard";
import * as client from "../api/client";
import type { OnboardDiscovery } from "../api/types";

const discovery: OnboardDiscovery = {
  by_host: { claude: 1, cursor: 1 },
  discovered: [
    {
      name: "weather-mcp",
      source_host: "claude",
      command: "npx -y weather",
      args: ["-y", "weather"],
      url: "",
      transport: "stdio",
      needs_keys: ["WEATHER_API_KEY"],
    },
    {
      name: "docs-mcp",
      source_host: "cursor",
      command: "",
      args: [],
      url: "https://docs.example/mcp",
      transport: "sse",
      needs_keys: [],
    },
  ],
  would_adopt: [
    { id: "weather-mcp", transport: "stdio", trust_tier: "Unverified" },
    { id: "docs-mcp", transport: "sse", trust_tier: "Unverified" },
  ],
  skipped_hosts: [],
  apply_allowed: true,
};

describe("Onboard", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("shows a scanning state initially", () => {
    vi.spyOn(client, "fetchOnboard").mockReturnValue(new Promise(() => {}));
    render(<Onboard />);
    expect(screen.getByText(/scanning/i)).toBeInTheDocument();
  });

  it("lists discovered servers grouped by host", async () => {
    vi.spyOn(client, "fetchOnboard").mockResolvedValue(discovery);
    render(<Onboard />);
    expect(await screen.findByText("weather-mcp")).toBeInTheDocument();
    expect(screen.getByText("docs-mcp")).toBeInTheDocument();
  });

  it("shows env-var KEY names as presence-only chips", async () => {
    vi.spyOn(client, "fetchOnboard").mockResolvedValue(discovery);
    render(<Onboard />);
    expect(await screen.findByText("WEATHER_API_KEY")).toBeInTheDocument();
  });

  it("adopts servers via the endpoint after confirmation", async () => {
    vi.spyOn(client, "fetchOnboard").mockResolvedValue(discovery);
    const applySpy = vi.spyOn(client, "applyOnboard").mockResolvedValue({
      ok: true,
      added: ["weather-mcp", "docs-mcp"],
      by_host: { claude: 1, cursor: 1 },
      skipped_hosts: [],
    });
    vi.spyOn(window, "confirm").mockReturnValue(true);
    render(<Onboard />);
    await userEvent.click(await screen.findByRole("button", { name: /adopt 2/i }));
    expect(applySpy).toHaveBeenCalledTimes(1);
  });

  it("does not adopt when the confirmation is cancelled", async () => {
    vi.spyOn(client, "fetchOnboard").mockResolvedValue(discovery);
    const applySpy = vi.spyOn(client, "applyOnboard").mockResolvedValue({
      ok: true,
      added: [],
      by_host: {},
      skipped_hosts: [],
    });
    vi.spyOn(window, "confirm").mockReturnValue(false);
    render(<Onboard />);
    await userEvent.click(await screen.findByRole("button", { name: /adopt 2/i }));
    expect(applySpy).not.toHaveBeenCalled();
  });

  it("locks adoption when apply_allowed is false (fail-closed hint)", async () => {
    vi.spyOn(client, "fetchOnboard").mockResolvedValue({
      ...discovery,
      apply_allowed: false,
    });
    render(<Onboard />);
    expect(await screen.findByText(/WERK_ALLOW_HUB_ONBOARD/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /adopt/i })).not.toBeInTheDocument();
  });

  it("renders an empty state when no servers are found", async () => {
    vi.spyOn(client, "fetchOnboard").mockResolvedValue({
      by_host: {},
      discovered: [],
      would_adopt: [],
      skipped_hosts: [],
      apply_allowed: true,
    });
    render(<Onboard />);
    expect(await screen.findByText(/no mcp servers found/i)).toBeInTheDocument();
  });

  it("surfaces a visible error on scan failure (honest-degrade)", async () => {
    vi.spyOn(client, "fetchOnboard").mockRejectedValue(
      new Error("hub request failed: 503"),
    );
    render(<Onboard />);
    expect(await screen.findByText(/503/)).toBeInTheDocument();
  });
});
