import { render, screen, act } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { Timeline } from "./Timeline";
import * as client from "../api/client";
import * as events from "../api/events";
import type { LedgerEvent } from "../api/events";

const seedEvents = [
  {
    event_id: "evt-001",
    ts: "2026-06-27T10:00:00Z",
    hash: "abc123",
    payload: { type: "config.update", key: "foo" },
  },
  {
    event_id: "evt-002",
    ts: "2026-06-27T10:01:00Z",
    hash: "def456",
    payload: { type: "process.kill.signal", pid: 42 },
  },
  {
    event_id: "evt-003",
    ts: "2026-06-27T10:02:00Z",
    hash: "ghi789",
    payload: { type: "approval.requested", id: "x" },
  },
];

const statusOk = {
  hub_name: "werk-hub",
  chain_verified: true,
  recent_events: seedEvents,
};

const statusFail = {
  hub_name: "werk-hub",
  chain_verified: false,
  recent_events: seedEvents,
};

describe("Timeline", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("seeds the feed from recent_events and shows type + hash", async () => {
    vi.spyOn(client, "fetchStatus").mockResolvedValue(statusOk);
    vi.spyOn(events, "subscribeLedger").mockReturnValue(() => {});
    render(<Timeline />);
    expect(await screen.findByText("config.update")).toBeInTheDocument();
    expect(screen.getByText("process.kill.signal")).toBeInTheDocument();
    expect(screen.getByText("approval.requested")).toBeInTheDocument();
    // short hash visible (getAllByText because the full hash is also in a visually-hidden a11y span)
    expect(screen.getAllByText(/abc123/).length).toBeGreaterThan(0);
  });

  it("shows newest events at the top (evt-003 before evt-001)", async () => {
    vi.spyOn(client, "fetchStatus").mockResolvedValue(statusOk);
    vi.spyOn(events, "subscribeLedger").mockReturnValue(() => {});
    render(<Timeline />);
    await screen.findByText("config.update");
    const rows = screen.getAllByRole("listitem");
    const texts = rows.map((r) => r.textContent ?? "");
    const idx003 = texts.findIndex((t) => t.includes("approval.requested"));
    const idx001 = texts.findIndex((t) => t.includes("config.update"));
    expect(idx003).toBeLessThan(idx001);
  });

  it("appends live ledger frames to the feed", async () => {
    vi.spyOn(client, "fetchStatus").mockResolvedValue({
      hub_name: "werk-hub",
      recent_events: [],
    });
    let captured: ((e: LedgerEvent) => void) | null = null;
    vi.spyOn(events, "subscribeLedger").mockImplementation((cb) => {
      captured = cb;
      return () => {};
    });
    render(<Timeline />);
    await screen.findByText(/no events/i);
    act(() => {
      captured!({ kind: "ledger", id: "live-001", payload: { type: "config.reload" } });
    });
    expect(await screen.findByText("config.reload")).toBeInTheDocument();
  });

  it("Pause toggle stops appending live frames", async () => {
    vi.spyOn(client, "fetchStatus").mockResolvedValue({
      hub_name: "werk-hub",
      recent_events: [],
    });
    let captured: ((e: LedgerEvent) => void) | null = null;
    vi.spyOn(events, "subscribeLedger").mockImplementation((cb) => {
      captured = cb;
      return () => {};
    });
    render(<Timeline />);
    // wait for mount
    await screen.findByRole("button", { name: /pause/i });
    await userEvent.click(screen.getByRole("button", { name: /pause/i }));
    act(() => {
      captured!({ kind: "ledger", id: "live-002", payload: { type: "should.not.appear" } });
    });
    expect(screen.queryByText("should.not.appear")).not.toBeInTheDocument();
    // resume
    await userEvent.click(screen.getByRole("button", { name: /resume/i }));
    act(() => {
      captured!({ kind: "ledger", id: "live-003", payload: { type: "after.resume" } });
    });
    expect(await screen.findByText("after.resume")).toBeInTheDocument();
  });

  it("shows chain_verified=true with VERIFIED label (not color alone)", async () => {
    vi.spyOn(client, "fetchStatus").mockResolvedValue(statusOk);
    vi.spyOn(events, "subscribeLedger").mockReturnValue(() => {});
    render(<Timeline />);
    await screen.findByText("config.update");
    expect(screen.getByText(/verified/i)).toBeInTheDocument();
  });

  it("shows chain_verified=false with UNVERIFIED label", async () => {
    vi.spyOn(client, "fetchStatus").mockResolvedValue(statusFail);
    vi.spyOn(events, "subscribeLedger").mockReturnValue(() => {});
    render(<Timeline />);
    await screen.findByText("config.update");
    expect(screen.getByText(/unverified/i)).toBeInTheDocument();
  });

  it("renders empty state when no events exist", async () => {
    vi.spyOn(client, "fetchStatus").mockResolvedValue({
      hub_name: "werk-hub",
      recent_events: [],
    });
    vi.spyOn(events, "subscribeLedger").mockReturnValue(() => {});
    render(<Timeline />);
    expect(await screen.findByText(/no events/i)).toBeInTheDocument();
  });

  it("surfaces a visible error when fetchStatus fails", async () => {
    vi.spyOn(client, "fetchStatus").mockRejectedValue(new Error("hub request failed: 503"));
    vi.spyOn(events, "subscribeLedger").mockReturnValue(() => {});
    render(<Timeline />);
    expect(await screen.findByText(/503/)).toBeInTheDocument();
  });

  it("unsubscribes on unmount", async () => {
    vi.spyOn(client, "fetchStatus").mockResolvedValue(statusOk);
    const unsub = vi.fn();
    vi.spyOn(events, "subscribeLedger").mockReturnValue(unsub);
    const { unmount } = render(<Timeline />);
    await screen.findByText("config.update");
    unmount();
    expect(unsub).toHaveBeenCalledTimes(1);
  });
});
