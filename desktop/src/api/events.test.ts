import { describe, it, expect, vi } from "vitest";
import { subscribeLedger } from "./events";

describe("subscribeLedger", () => {
  it("parses ledger frames and invokes the callback, and unsubscribe closes", () => {
    const handlers: Record<string, (e: MessageEvent) => void> = {};
    const close = vi.fn();
    class FakeES {
      constructor(public url: string) {}
      addEventListener(name: string, cb: (e: MessageEvent) => void) {
        handlers[name] = cb;
      }
      close = close;
    }
    vi.stubGlobal("EventSource", FakeES);

    const seen: unknown[] = [];
    const stop = subscribeLedger((evt) => seen.push(evt));
    handlers["ledger"](
      new MessageEvent("ledger", {
        data: JSON.stringify({ kind: "tool.call", id: "e1" }),
      }),
    );

    expect(seen).toEqual([{ kind: "tool.call", id: "e1" }]);
    stop();
    expect(close).toHaveBeenCalled();
  });

  it("emits one event per record when the frame data is a batch array", () => {
    const handlers: Record<string, (e: MessageEvent) => void> = {};
    class FakeES {
      constructor(public url: string) {}
      addEventListener(name: string, cb: (e: MessageEvent) => void) {
        handlers[name] = cb;
      }
      close = vi.fn();
    }
    vi.stubGlobal("EventSource", FakeES);

    const seen: unknown[] = [];
    subscribeLedger((evt) => seen.push(evt));
    // backend frames events as an array (data: [...]); empty arrays emit nothing
    handlers["ledger"](new MessageEvent("ledger", { data: "[]" }));
    expect(seen).toEqual([]);
    handlers["ledger"](
      new MessageEvent("ledger", {
        data: JSON.stringify([{ event_id: "a", payload: { type: "config.x" } }]),
      }),
    );
    expect(seen).toEqual([{ event_id: "a", payload: { type: "config.x" } }]);
  });

  it("ignores a malformed frame without throwing", () => {
    const handlers: Record<string, (e: MessageEvent) => void> = {};
    class FakeES {
      constructor(public url: string) {}
      addEventListener(name: string, cb: (e: MessageEvent) => void) {
        handlers[name] = cb;
      }
      close = vi.fn();
    }
    vi.stubGlobal("EventSource", FakeES);

    const seen: unknown[] = [];
    subscribeLedger((evt) => seen.push(evt));
    expect(() =>
      handlers["ledger"](new MessageEvent("ledger", { data: "{bad json" })),
    ).not.toThrow();
    expect(seen).toEqual([]);
  });
});
