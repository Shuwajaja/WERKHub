import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { Registry } from "./Registry";
import * as client from "../api/client";
import type { RegistryCatalog, RegistryCandidate } from "../api/types";

const mockCatalog: RegistryCatalog = {
  capabilities: [
    {
      id: "cap-fs",
      category: "Filesystem",
      trust_tier: "Security-Scanned",
      what_it_is: "Access the local filesystem",
      maintainer: "MCP Core Team",
      maintenance: "active",
    },
    {
      id: "cap-web",
      category: "Web",
      trust_tier: "Official",
      what_it_is: "Fetch pages from the web",
      maintainer: "Anthropic",
      maintenance: "stable",
    },
    {
      id: "cap-community",
      category: "AI",
      trust_tier: "Community",
      what_it_is: "An AI tool",
      maintainer: "Someone",
      maintenance: "unknown",
    },
    {
      id: "cap-unverified",
      category: "Misc",
      trust_tier: "Unverified",
      what_it_is: "Unknown tool",
      maintainer: "Unknown",
      maintenance: "unknown",
    },
  ],
  category_counts: { Filesystem: 1, Web: 1, AI: 1, Misc: 1 },
};

const mockCandidates: RegistryCandidate[] = [
  { id: "r1", name: "ResultOne", description: "First search result", installable: true },
  { id: "r2", name: "ResultTwo", description: "Second search result", installable: false },
];

describe("Registry", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("loads and renders catalog capabilities with trust stamps", async () => {
    vi.spyOn(client, "fetchRegistry").mockResolvedValue(mockCatalog);
    render(<Registry />);
    // Shows capabilities from catalog
    expect(await screen.findByText("cap-fs")).toBeInTheDocument();
    expect(screen.getByText("cap-web")).toBeInTheDocument();
    // Trust stamps present (may appear in filter row + list row)
    expect(screen.getAllByText(/SECURITY-SCANNED/i).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/OFFICIAL/i).length).toBeGreaterThan(0);
  });

  it("shows detail panel when a capability is selected", async () => {
    vi.spyOn(client, "fetchRegistry").mockResolvedValue(mockCatalog);
    render(<Registry />);
    await screen.findByText("cap-fs");
    await userEvent.click(screen.getByText("cap-fs"));
    expect(await screen.findByText(/Access the local filesystem/i)).toBeInTheDocument();
    expect(screen.getByText(/MCP Core Team/i)).toBeInTheDocument();
    // "Filesystem" appears in detail panel category field (and also in filter row — multiple OK)
    expect(screen.getAllByText(/Filesystem/i).length).toBeGreaterThan(0);
  });

  it("filters capabilities by trust tier when a filter stamp is clicked", async () => {
    vi.spyOn(client, "fetchRegistry").mockResolvedValue(mockCatalog);
    render(<Registry />);
    await screen.findByText("cap-fs");

    // Click the Official filter
    const officialFilter = screen.getByRole("button", { name: /^Official$/i });
    await userEvent.click(officialFilter);

    expect(screen.getByText("cap-web")).toBeInTheDocument();
    expect(screen.queryByText("cap-fs")).not.toBeInTheDocument();
  });

  it("searches and shows candidates on submit", async () => {
    vi.spyOn(client, "fetchRegistry").mockResolvedValue(mockCatalog);
    vi.spyOn(client, "searchRegistry").mockResolvedValue({
      ok: true,
      candidates: mockCandidates,
      warnings: [],
    });
    render(<Registry />);
    await screen.findByText("cap-fs");

    const input = screen.getByPlaceholderText(/search/i);
    await userEvent.type(input, "result");
    await userEvent.keyboard("{Enter}");

    expect(await screen.findByText("ResultOne")).toBeInTheDocument();
    expect(screen.getByText("ResultTwo")).toBeInTheDocument();
  });

  it("shows search error honestly when searchRegistry fails", async () => {
    vi.spyOn(client, "fetchRegistry").mockResolvedValue(mockCatalog);
    vi.spyOn(client, "searchRegistry").mockRejectedValue(new Error("search-503"));
    render(<Registry />);
    await screen.findByText("cap-fs");

    const input = screen.getByPlaceholderText(/search/i);
    await userEvent.type(input, "query");
    await userEvent.keyboard("{Enter}");

    expect(await screen.findByText(/search-503/i)).toBeInTheDocument();
  });

  it("shows empty state when catalog has no capabilities", async () => {
    vi.spyOn(client, "fetchRegistry").mockResolvedValue({
      capabilities: [],
      category_counts: {},
    });
    render(<Registry />);
    expect(await screen.findByText(/no capabilities/i)).toBeInTheDocument();
  });

  it("surfaces a visible error when fetchRegistry fails (honest-degrade)", async () => {
    vi.spyOn(client, "fetchRegistry").mockRejectedValue(new Error("hub request failed: 503"));
    render(<Registry />);
    expect(await screen.findByText(/503/)).toBeInTheDocument();
  });

  it("renders sub-nav tabs: Skills, Connectors, Plugins", async () => {
    vi.spyOn(client, "fetchRegistry").mockResolvedValue(mockCatalog);
    render(<Registry />);
    // Tabs now use role="tab" (inside role="tablist") per ARIA tabs pattern
    expect(await screen.findByRole("tab", { name: /Skills/i })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /Connectors/i })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /Plugins/i })).toBeInTheDocument();
  });

  it("shows empty search results state when no candidates match", async () => {
    vi.spyOn(client, "fetchRegistry").mockResolvedValue(mockCatalog);
    vi.spyOn(client, "searchRegistry").mockResolvedValue({
      ok: true,
      candidates: [],
      warnings: [],
    });
    render(<Registry />);
    await screen.findByText("cap-fs");

    const input = screen.getByPlaceholderText(/search/i);
    await userEvent.type(input, "nothing");
    await userEvent.keyboard("{Enter}");

    expect(await screen.findByText(/no results/i)).toBeInTheDocument();
  });
});
