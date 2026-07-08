export type TrustTier =
  | "Official"
  | "Security-Scanned"
  | "Community"
  | "Unverified";

export interface ServerCard {
  id: string;
  name: string;
  transport: string;
  trust_tier: TrustTier;
  enabled: boolean;
  state?: string;
  pid?: number;
  idle_for_s?: number;
  tool_count?: number;
}

export interface HubServer {
  server_id: string;
  name: string;
  state: string;
  pid?: number;
  uptime_s?: number;
  idle_for_s?: number;
  tool_count?: number;
}

export interface LedgerEventRecord {
  event_id: string;
  ts: string;
  prev_hash?: string;
  hash?: string;
  payload: { type: string; [k: string]: unknown };
}

export interface HubStatus {
  hub_name: string;
  profile_id?: string;
  generated_at?: string;
  total_processes?: number;
  processes?: HubServer[];
  servers?: HubServer[];
  recent_events?: LedgerEventRecord[];
  chain_verified?: boolean;
  chain_errors?: number;
  kill_allowed?: boolean;
}

export interface RuntimeInfo {
  name?: string;
  version?: string;
  kind?: string;
  [k: string]: unknown;
}

export interface RuntimesResponse {
  generated_at: string;
  total: number;
  detected: Array<string | RuntimeInfo>;
  probes: unknown[];
}

export interface Capability {
  id: string;
  kind?: string;
  category: string;
  trust_tier: TrustTier;
  maintenance?: string;
  popularity?: string;
  needs_keys?: string[];
  keys_present?: boolean;
  what_it_is?: string;
  maintainer?: string;
}

export interface RegistryCatalog {
  capabilities: Capability[];
  category_counts: Record<string, number>;
}

// Pending approval as returned by GET /api/approvals (one-use token redacted).
export interface ApprovalRecord {
  request_id: string;
  tool_id: string;
  profile_id: string;
  call_args: Record<string, unknown>;
  status: string;
  created_at: string;
  args_hash?: string;
  resolved_at?: string;
  resolved_by?: string;
}

export interface RegistryCandidate {
  id: string;
  name: string;
  description: string;
  installable: boolean;
}

// One MCP server discovered in an agent-host config (Claude/Cursor/...).
// PRESENCE-ONLY: needs_keys holds env-var KEY names, never values.
export interface DiscoveredServer {
  name: string;
  source_host: string;
  command: string;
  args: string[];
  url: string;
  transport: string;
  needs_keys: string[];
}

export interface OnboardCandidate {
  id: string;
  transport: string;
  trust_tier: TrustTier;
}

// GET /api/onboard — dry-run discovery across the operator's agent hosts.
export interface OnboardDiscovery {
  by_host: Record<string, number>;
  discovered: DiscoveredServer[];
  would_adopt: OnboardCandidate[];
  skipped_hosts: string[];
  apply_allowed: boolean;
}

// POST /api/onboard/apply — result of adopting discovered servers.
export interface OnboardApplyResult {
  ok: boolean;
  added: string[];
  by_host: Record<string, number>;
  skipped_hosts: string[];
}
