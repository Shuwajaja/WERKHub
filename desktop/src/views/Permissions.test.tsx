import { render, screen } from "@testing-library/react";
import { describe, it, expect, vi, afterEach } from "vitest";
import { Permissions } from "./Permissions";
import * as client from "../api/client";

afterEach(() => vi.restoreAllMocks());

describe("Permissions", () => {
  it("shows the active profile_id from fetchStatus", async () => {
    vi.spyOn(client, "fetchStatus").mockResolvedValue({
      hub_name: "test-hub",
      profile_id: "werkagent-default",
    });
    render(<Permissions />);
    const matches = await screen.findAllByText(/werkagent-default/i);
    expect(matches.length).toBeGreaterThan(0);
  });

  it("renders the tool-lens explainer sections with werk-label headers", async () => {
    vi.spyOn(client, "fetchStatus").mockResolvedValue({
      hub_name: "test-hub",
      profile_id: "dev-profile",
    });
    render(<Permissions />);
    await screen.findAllByText(/dev-profile/i);
    expect(screen.getAllByText(/ACTIVE PROFILE/i).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/TOOL LENS MODEL/i).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/pending backend endpoint/i).length).toBeGreaterThan(0);
  });

  it("shows an empty state when profile_id is absent", async () => {
    vi.spyOn(client, "fetchStatus").mockResolvedValue({
      hub_name: "test-hub",
    });
    render(<Permissions />);
    await screen.findByText(/no profile/i);
  });

  it("surfaces a visible error when fetchStatus fails (honest-degrade)", async () => {
    vi.spyOn(client, "fetchStatus").mockRejectedValue(
      new Error("hub unreachable: 503"),
    );
    render(<Permissions />);
    expect(await screen.findByText(/503/)).toBeInTheDocument();
  });
});
