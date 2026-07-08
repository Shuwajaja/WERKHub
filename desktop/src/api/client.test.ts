import { describe, it, expect, vi, beforeEach } from "vitest";
import { fetchConnectors, fetchStatus } from "./client";

beforeEach(() => {
  (globalThis as Record<string, unknown>).__WERK_TOKEN__ = "tok123";
});

describe("api client", () => {
  it("sends the X-Werk-Token header on GET and parses connectors", async () => {
    const json = [
      {
        id: "fs",
        name: "Filesystem",
        transport: "stdio",
        trust_tier: "Security-Scanned",
        enabled: true,
      },
    ];
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve(json),
    } as Response);
    vi.stubGlobal("fetch", fetchMock);

    const out = await fetchConnectors();

    expect(fetchMock).toHaveBeenCalledWith("/api/connectors", {
      headers: { "X-Werk-Token": "tok123" },
    });
    expect(out[0].id).toBe("fs");
    expect(out[0].trust_tier).toBe("Security-Scanned");
  });

  it("throws a typed error on non-ok response", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ ok: false, status: 503 } as Response),
    );
    await expect(fetchStatus()).rejects.toThrow("hub request failed: 503");
  });
});
