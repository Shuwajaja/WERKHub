import { render, screen, act } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { Console } from "./Console";
import * as events from "../api/events";

describe("Console", () => {
  let capturedCallback: ((e: events.LedgerEvent) => void) | null = null;
  let unsubscribeSpy: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    capturedCallback = null;
    unsubscribeSpy = vi.fn();
    vi.spyOn(events, "subscribeLedger").mockImplementation((cb) => {
      capturedCallback = cb;
      return unsubscribeSpy;
    });
  });

  it("subscribes to the ledger on mount and shows hint line when empty", () => {
    render(<Console />);
    expect(events.subscribeLedger).toHaveBeenCalledTimes(1);
    expect(screen.getByText(/waiting for ledger events/i)).toBeInTheDocument();
  });

  it("renders incoming ledger events with timestamp and type in mono", async () => {
    render(<Console />);
    act(() => {
      capturedCallback!({
        kind: "ledger",
        id: "evt-001",
        ts: "2026-06-27T10:00:00Z",
        type: "TASK_STARTED",
      });
    });
    expect(await screen.findByText(/TASK_STARTED/)).toBeInTheDocument();
    expect(screen.getByText(/evt-001/)).toBeInTheDocument();
  });

  it("unsubscribes on unmount", () => {
    const { unmount } = render(<Console />);
    unmount();
    expect(unsubscribeSpy).toHaveBeenCalledTimes(1);
  });

  it("echos typed command with '$ ' prefix and stub note on submit", async () => {
    const user = userEvent.setup();
    render(<Console />);
    const input = screen.getByPlaceholderText(/hub verb/i);
    await user.type(input, "hs list");
    await user.keyboard("{Enter}");
    // "$ " and "hs list" are in sibling spans within the cmd div
    expect(screen.getByText("hs list")).toBeInTheDocument();
    // verify the prompt span with exact text "$ " is present
    expect(screen.getAllByText((_, el) => el?.textContent === "$ ").length).toBeGreaterThan(0);
    expect(screen.getByText(/execution wiring is pending/i)).toBeInTheDocument();
  });

  it("clears the input after submit", async () => {
    const user = userEvent.setup();
    render(<Console />);
    const input = screen.getByPlaceholderText(/hub verb/i) as HTMLInputElement;
    await user.type(input, "hs cancel 42");
    await user.keyboard("{Enter}");
    expect(input.value).toBe("");
  });

  it("does not submit on empty input", async () => {
    const user = userEvent.setup();
    render(<Console />);
    await user.keyboard("{Enter}");
    // stub note should NOT appear because nothing was submitted
    expect(screen.queryByText(/execution wiring is pending/i)).not.toBeInTheDocument();
  });

  it("has a pause/resume control for the live stream", async () => {
    const user = userEvent.setup();
    render(<Console />);
    const pauseBtn = screen.getByRole("button", { name: /pause/i });
    expect(pauseBtn).toBeInTheDocument();
    await user.click(pauseBtn);
    expect(screen.getByRole("button", { name: /resume/i })).toBeInTheDocument();
  });

  it("does not add new events to scrollback while paused", async () => {
    const user = userEvent.setup();
    render(<Console />);
    // pause the stream
    await user.click(screen.getByRole("button", { name: /pause/i }));
    act(() => {
      capturedCallback!({ kind: "ledger", id: "paused-evt", ts: "2026-06-27T11:00:00Z", type: "PAUSED_EVENT" });
    });
    expect(screen.queryByText(/PAUSED_EVENT/)).not.toBeInTheDocument();
  });

  it("shows a visible error banner when subscribeLedger throws (honest-degrade)", () => {
    vi.spyOn(events, "subscribeLedger").mockImplementation(() => {
      throw new Error("SSE connection refused");
    });
    render(<Console />);
    expect(screen.getByText(/SSE connection refused/i)).toBeInTheDocument();
  });
});
