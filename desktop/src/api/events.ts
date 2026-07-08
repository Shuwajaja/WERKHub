export interface LedgerEvent {
  kind?: string;
  id?: string;
  event_id?: string;
  ts?: string;
  type?: string;
  hash?: string;
  payload?: { type?: string; [k: string]: unknown };
  [k: string]: unknown;
}

export function subscribeLedger(
  onEvent: (e: LedgerEvent) => void,
  onError?: (err: Event) => void,
): () => void {
  const es = new EventSource("/api/events");
  es.addEventListener("ledger", (e) => {
    try {
      const parsed = JSON.parse((e as MessageEvent).data);
      // The backend frames events as a batch array (data: [...]); a single
      // object is also tolerated. Emit one event per record.
      const list = Array.isArray(parsed) ? parsed : [parsed];
      for (const item of list) onEvent(item as LedgerEvent);
    } catch {
      // skip malformed frame
    }
  });
  if (onError) {
    es.onerror = onError;
  }
  return () => es.close();
}
