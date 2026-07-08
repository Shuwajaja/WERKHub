import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { Approvals } from "./Approvals";
import * as client from "../api/client";
import type { ApprovalRecord } from "../api/types";

const pending: ApprovalRecord[] = [
  {
    request_id: "apr_aaa111bbb222",
    tool_id: "bash",
    profile_id: "default",
    call_args: { cmd: "rm -rf /tmp/foo" },
    status: "pending",
    created_at: "2026-06-27T10:00:00Z",
  },
  {
    request_id: "apr_ccc333ddd444",
    tool_id: "file_write",
    profile_id: "planner",
    call_args: { path: "/etc/hosts" },
    status: "pending",
    created_at: "2026-06-27T10:05:00Z",
  },
];

describe("Approvals", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("shows loading state initially", () => {
    vi.spyOn(client, "fetchApprovals").mockReturnValue(new Promise(() => {}));
    render(<Approvals />);
    expect(screen.getByText(/loading/i)).toBeInTheDocument();
  });

  it("renders an empty state when there are no pending approvals", async () => {
    vi.spyOn(client, "fetchApprovals").mockResolvedValue([]);
    render(<Approvals />);
    expect(await screen.findByText(/no pending approvals/i)).toBeInTheDocument();
  });

  it("lists pending approvals by tool", async () => {
    vi.spyOn(client, "fetchApprovals").mockResolvedValue(pending);
    render(<Approvals />);
    expect(await screen.findByText("bash")).toBeInTheDocument();
    expect(screen.getByText("file_write")).toBeInTheDocument();
  });

  it("shows detail (request_id + redacted args) when selected", async () => {
    vi.spyOn(client, "fetchApprovals").mockResolvedValue(pending);
    render(<Approvals />);
    await userEvent.click(await screen.findByText("bash"));
    expect(await screen.findByText("apr_aaa111bbb222")).toBeInTheDocument();
    expect(screen.getByText(/rm -rf/)).toBeInTheDocument();
  });

  it("approves a pending request via the endpoint", async () => {
    vi.spyOn(client, "fetchApprovals").mockResolvedValue(pending);
    const resolveSpy = vi
      .spyOn(client, "resolveApproval")
      .mockResolvedValue({ ok: true, request_id: "apr_aaa111bbb222", status: "approved" });
    render(<Approvals />);
    await userEvent.click(await screen.findByText("bash"));
    await userEvent.click(await screen.findByRole("button", { name: /approve/i }));
    expect(resolveSpy).toHaveBeenCalledWith("apr_aaa111bbb222", "approve");
  });

  it("denies only after confirmation", async () => {
    vi.spyOn(client, "fetchApprovals").mockResolvedValue(pending);
    const resolveSpy = vi
      .spyOn(client, "resolveApproval")
      .mockResolvedValue({ ok: true, request_id: "apr_aaa111bbb222", status: "denied" });
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
    render(<Approvals />);
    await userEvent.click(await screen.findByText("bash"));
    await userEvent.click(await screen.findByRole("button", { name: /deny/i }));
    expect(confirmSpy).toHaveBeenCalled();
    expect(resolveSpy).toHaveBeenCalledWith("apr_aaa111bbb222", "deny");
  });

  it("does not deny when confirmation is cancelled", async () => {
    vi.spyOn(client, "fetchApprovals").mockResolvedValue(pending);
    const resolveSpy = vi
      .spyOn(client, "resolveApproval")
      .mockResolvedValue({ ok: true, request_id: "x", status: "denied" });
    vi.spyOn(window, "confirm").mockReturnValue(false);
    render(<Approvals />);
    await userEvent.click(await screen.findByText("bash"));
    await userEvent.click(await screen.findByRole("button", { name: /deny/i }));
    expect(resolveSpy).not.toHaveBeenCalled();
  });

  it("surfaces a visible error on fetch failure (honest-degrade)", async () => {
    vi.spyOn(client, "fetchApprovals").mockRejectedValue(
      new Error("hub request failed: 503"),
    );
    render(<Approvals />);
    expect(await screen.findByText(/503/)).toBeInTheDocument();
  });
});
