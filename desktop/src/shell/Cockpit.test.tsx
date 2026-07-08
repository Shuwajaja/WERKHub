import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, it, expect, vi } from "vitest";
import { Cockpit } from "./Cockpit";

describe("Cockpit", () => {
  it("renders the wordmark, the nav items, and routes clicks", async () => {
    const onSelect = vi.fn();
    render(
      <Cockpit active="connectors" onSelect={onSelect}>
        <div>slot</div>
      </Cockpit>,
    );
    expect(screen.getByText("WERKHUB")).toBeInTheDocument();
    expect(screen.getByText("slot")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Board" }));
    expect(onSelect).toHaveBeenCalledWith("board");
    await userEvent.click(screen.getByRole("button", { name: "Onboard" }));
    expect(onSelect).toHaveBeenCalledWith("onboard");
  });
});
