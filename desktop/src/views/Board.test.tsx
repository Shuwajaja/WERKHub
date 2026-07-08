import { render, screen } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { Board } from "./Board";
import * as client from "../api/client";
import type { HubStatus, RuntimesResponse, RegistryCatalog } from "../api/types";

const mockStatus: HubStatus = {
  hub_name: "TestHub",
  profile_id: "default",
  total_processes: 5,
  processes: [
    { server_id: "s1", name: "server1", state: "running" },
    { server_id: "s2", name: "server2", state: "running" },
    { server_id: "s3", name: "server3", state: "idle" },
  ],
  chain_verified: true,
};

const mockRuntimes: RuntimesResponse = {
  generated_at: "2026-06-27T00:00:00Z",
  total: 3,
  detected: [
    { name: "Node.js", version: "20.11.0", kind: "runtime" },
    { name: "Python", version: "3.12.1", kind: "runtime" },
    { name: "Deno", version: "1.41.0", kind: "runtime" },
  ],
  probes: [],
};

const mockRegistry: RegistryCatalog = {
  capabilities: [
    { id: "c1", category: "files", trust_tier: "Official" },
    { id: "c2", category: "web", trust_tier: "Security-Scanned" },
    { id: "c3", category: "web", trust_tier: "Community" },
    { id: "c4", category: "db", trust_tier: "Unverified" },
    { id: "c5", category: "files", trust_tier: "Official" },
  ],
  category_counts: { files: 2, web: 2, db: 1 },
};

describe("Board", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("renders loading state initially", () => {
    vi.spyOn(client, "fetchStatus").mockResolvedValue(mockStatus);
    vi.spyOn(client, "fetchRuntimes").mockResolvedValue(mockRuntimes);
    vi.spyOn(client, "fetchRegistry").mockResolvedValue(mockRegistry);
    render(<Board />);
    expect(screen.getByText(/loading/i)).toBeInTheDocument();
  });

  it("shows hero KPI card with live connector count and total", async () => {
    vi.spyOn(client, "fetchStatus").mockResolvedValue(mockStatus);
    vi.spyOn(client, "fetchRuntimes").mockResolvedValue(mockRuntimes);
    vi.spyOn(client, "fetchRegistry").mockResolvedValue(mockRegistry);
    render(<Board />);
    // processes.length = 3 live, total_processes = 5
    expect(await screen.findByText("3")).toBeInTheDocument();
    expect(screen.getByText("5")).toBeInTheDocument();
  });

  it("shows chain_verified status as a stamp", async () => {
    vi.spyOn(client, "fetchStatus").mockResolvedValue(mockStatus);
    vi.spyOn(client, "fetchRuntimes").mockResolvedValue(mockRuntimes);
    vi.spyOn(client, "fetchRegistry").mockResolvedValue(mockRegistry);
    render(<Board />);
    expect(await screen.findByText(/chain verified/i)).toBeInTheDocument();
  });

  it("renders trust-posture accessible table with tier counts", async () => {
    vi.spyOn(client, "fetchStatus").mockResolvedValue(mockStatus);
    vi.spyOn(client, "fetchRuntimes").mockResolvedValue(mockRuntimes);
    vi.spyOn(client, "fetchRegistry").mockResolvedValue(mockRegistry);
    render(<Board />);
    // Official: 2, Security-Scanned: 1, Community: 1, Unverified: 1
    expect(await screen.findByText(/official/i)).toBeInTheDocument();
    expect(screen.getByText(/community/i)).toBeInTheDocument();
    expect(screen.getByText(/unverified/i)).toBeInTheDocument();
  });

  it("renders detected runtimes list", async () => {
    vi.spyOn(client, "fetchStatus").mockResolvedValue(mockStatus);
    vi.spyOn(client, "fetchRuntimes").mockResolvedValue(mockRuntimes);
    vi.spyOn(client, "fetchRegistry").mockResolvedValue(mockRegistry);
    render(<Board />);
    expect(await screen.findByText(/node\.js/i)).toBeInTheDocument();
    expect(screen.getByText(/python/i)).toBeInTheDocument();
    expect(screen.getByText(/deno/i)).toBeInTheDocument();
  });

  it("shows empty state for runtimes when none detected", async () => {
    vi.spyOn(client, "fetchStatus").mockResolvedValue(mockStatus);
    vi.spyOn(client, "fetchRuntimes").mockResolvedValue({
      ...mockRuntimes,
      detected: [],
      total: 0,
    });
    vi.spyOn(client, "fetchRegistry").mockResolvedValue(mockRegistry);
    render(<Board />);
    expect(await screen.findByText(/no runtimes detected/i)).toBeInTheDocument();
  });

  it("shows visible error when fetchStatus fails (honest-degrade)", async () => {
    vi.spyOn(client, "fetchStatus").mockRejectedValue(
      new Error("hub request failed: 503"),
    );
    vi.spyOn(client, "fetchRuntimes").mockResolvedValue(mockRuntimes);
    vi.spyOn(client, "fetchRegistry").mockResolvedValue(mockRegistry);
    render(<Board />);
    expect(await screen.findByText(/503/)).toBeInTheDocument();
  });

  it("shows visible error when fetchRuntimes fails (honest-degrade)", async () => {
    vi.spyOn(client, "fetchStatus").mockResolvedValue(mockStatus);
    vi.spyOn(client, "fetchRuntimes").mockRejectedValue(
      new Error("hub request failed: 502"),
    );
    vi.spyOn(client, "fetchRegistry").mockResolvedValue(mockRegistry);
    render(<Board />);
    expect(await screen.findByText(/502/)).toBeInTheDocument();
  });

  it("shows visible error when fetchRegistry fails (honest-degrade)", async () => {
    vi.spyOn(client, "fetchStatus").mockResolvedValue(mockStatus);
    vi.spyOn(client, "fetchRuntimes").mockResolvedValue(mockRuntimes);
    vi.spyOn(client, "fetchRegistry").mockRejectedValue(
      new Error("hub request failed: 500"),
    );
    render(<Board />);
    expect(await screen.findByText(/500/)).toBeInTheDocument();
  });

  it("renders trust-posture SVG donut (aria-hidden) and accessible table fallback", async () => {
    vi.spyOn(client, "fetchStatus").mockResolvedValue(mockStatus);
    vi.spyOn(client, "fetchRuntimes").mockResolvedValue(mockRuntimes);
    vi.spyOn(client, "fetchRegistry").mockResolvedValue(mockRegistry);
    render(<Board />);
    // The accessible table must be present (not just the SVG)
    expect(
      await screen.findByRole("table", { name: /trust posture/i }),
    ).toBeInTheDocument();
  });

  it("shows hub name in the header", async () => {
    vi.spyOn(client, "fetchStatus").mockResolvedValue(mockStatus);
    vi.spyOn(client, "fetchRuntimes").mockResolvedValue(mockRuntimes);
    vi.spyOn(client, "fetchRegistry").mockResolvedValue(mockRegistry);
    render(<Board />);
    expect(await screen.findByText(/testhub/i)).toBeInTheDocument();
  });

  it("reads live count from servers field when processes is absent", async () => {
    const statusWithServers: HubStatus = {
      ...mockStatus,
      total_processes: 99,
      processes: undefined,
      servers: [
        { server_id: "s1", name: "server1", state: "running" },
        { server_id: "s2", name: "server2", state: "running" },
        { server_id: "s3", name: "server3", state: "running" },
        { server_id: "s4", name: "server4", state: "running" },
        { server_id: "s5", name: "server5", state: "running" },
        { server_id: "s6", name: "server6", state: "running" },
        { server_id: "s7", name: "server7", state: "running" },
      ],
    };
    vi.spyOn(client, "fetchStatus").mockResolvedValue(statusWithServers);
    vi.spyOn(client, "fetchRuntimes").mockResolvedValue(mockRuntimes);
    vi.spyOn(client, "fetchRegistry").mockResolvedValue(mockRegistry);
    render(<Board />);
    // servers.length = 7, total_processes = 99 — both must appear
    expect(await screen.findByText("7")).toBeInTheDocument();
    expect(screen.getByText("99")).toBeInTheDocument();
  });
});
