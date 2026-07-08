import type {
  ServerCard,
  HubStatus,
  RuntimesResponse,
  RegistryCatalog,
  RegistryCandidate,
  ApprovalRecord,
  OnboardDiscovery,
  OnboardApplyResult,
} from "./types";

function token(): string {
  return (globalThis as Record<string, unknown>).__WERK_TOKEN__ as string ?? "";
}

async function get<T>(path: string): Promise<T> {
  const res = await fetch(path, { headers: { "X-Werk-Token": token() } });
  if (!res.ok) throw new Error(`hub request failed: ${res.status}`);
  return res.json() as Promise<T>;
}

async function post<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(path, {
    method: "POST",
    headers: { "X-Werk-Token": token(), "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`hub request failed: ${res.status}`);
  return res.json() as Promise<T>;
}

export const fetchConnectors = () => get<ServerCard[]>("/api/connectors");
export const fetchStatus = () => get<HubStatus>("/api/status");
export const fetchRuntimes = () => get<RuntimesResponse>("/api/runtimes");
export const fetchRegistry = () => get<RegistryCatalog>("/api/registry");
export const toggleConnector = (id: string) =>
  post<{ ok: boolean }>("/api/connectors/toggle", { id });
export const killServer = (pid: number, server_id: string) =>
  post<{ ok: boolean }>("/api/kill", { pid, server_id });
export const fetchApprovals = () => get<ApprovalRecord[]>("/api/approvals");
export const resolveApproval = (
  request_id: string,
  decision: "approve" | "deny",
) =>
  post<{ ok: boolean; request_id: string; status: string }>(
    "/api/approvals/resolve",
    { request_id, decision },
  );
// The onboarding Doctor — discover MCP servers in the operator's agent-host
// configs and (gated) adopt them as hub connectors. Presence-only: no secret
// values ever cross this boundary.
export const fetchOnboard = () => get<OnboardDiscovery>("/api/onboard");
export const applyOnboard = (host?: string) =>
  post<OnboardApplyResult>("/api/onboard/apply", host ? { host } : {});
export const searchRegistry = (query: string) =>
  post<{ ok: boolean; candidates: RegistryCandidate[]; warnings: string[] }>(
    "/api/registry/search",
    { query },
  );
