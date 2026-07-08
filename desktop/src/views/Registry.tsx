// TODO: Replace monogram icon tiles with real brand logos (Simple Icons) when assets are available.
import { useEffect, useRef, useState, useSyncExternalStore } from "react";
import {
  Search,
  Layers,
  Plug,
  Puzzle,
  ShieldCheck,
  BadgeCheck,
  Users,
  AlertTriangle,
  List,
  PackageSearch,
  ServerOff,
  Filter as FilterIcon,
} from "lucide-react";
import { fetchRegistry, searchRegistry } from "../api/client";
import type { Capability, RegistryCandidate, TrustTier } from "../api/types";

// ── Trust tier lucide icons ──────────────────────────────────────────────────
const TIER_ICON_MAP: Record<TrustTier, React.ReactNode> = {
  Official: <BadgeCheck size={12} aria-hidden="true" />,
  "Security-Scanned": <ShieldCheck size={12} aria-hidden="true" />,
  Community: <Users size={12} aria-hidden="true" />,
  Unverified: <AlertTriangle size={12} aria-hidden="true" />,
};

// ── Brand colors per trust tier ─────────────────────────────────────────────
const TIER_COLOR: Record<TrustTier, string> = {
  Official: "var(--audit)",
  "Security-Scanned": "var(--ok)",
  Community: "var(--muted)",
  Unverified: "var(--warn)",
};

// Monogram background tint: first letter of category → deterministic hue from
// the A-FINAL palette. Real Simple Icons replace these later.
const CATEGORY_TINTS: Record<string, string> = {
  Filesystem: "var(--interact)",
  Web: "var(--ok)",
  AI: "var(--audit)",
  Misc: "var(--muted)",
};
function tintFor(category: string): string {
  return CATEGORY_TINTS[category] ?? "var(--secondary)";
}

type Tab = "Skills" | "Connectors" | "Plugins";
// Renamed from Filter to TrustFilter to avoid collision with the Filter lucide icon
type TrustFilter = "All" | TrustTier;

const TABS: { label: Tab; icon: React.ReactNode }[] = [
  { label: "Skills", icon: <Layers size={13} aria-hidden="true" /> },
  { label: "Connectors", icon: <Plug size={13} aria-hidden="true" /> },
  { label: "Plugins", icon: <Puzzle size={13} aria-hidden="true" /> },
];

const FILTERS: TrustFilter[] = ["All", "Security-Scanned", "Official", "Community", "Unverified"];

const FILTER_ICON_MAP: Record<TrustFilter, React.ReactNode> = {
  All: <List size={11} aria-hidden="true" />,
  "Security-Scanned": <ShieldCheck size={11} aria-hidden="true" />,
  Official: <BadgeCheck size={11} aria-hidden="true" />,
  Community: <Users size={11} aria-hidden="true" />,
  Unverified: <AlertTriangle size={11} aria-hidden="true" />,
};

// Tier color used only for the icon in the off-state stamp (semantic signal, not decoration)
const FILTER_TIER_COLORS: Record<TrustFilter, string> = {
  All: "var(--brand)",
  "Security-Scanned": "var(--ok)",
  Official: "var(--audit)",
  Community: "var(--muted)",
  Unverified: "var(--warn)",
};

// ── Reduced-motion hook ──────────────────────────────────────────────────────
// Uses useSyncExternalStore so the value re-evaluates reactively when the
// OS-level prefers-reduced-motion setting changes mid-session.
function subscribe(cb: () => void): () => void {
  if (typeof window === "undefined" || typeof window.matchMedia !== "function") return () => {};
  const mq = window.matchMedia("(prefers-reduced-motion: reduce)");
  mq.addEventListener("change", cb);
  return () => mq.removeEventListener("change", cb);
}
function getSnapshot(): boolean {
  if (typeof window === "undefined" || typeof window.matchMedia !== "function") return false;
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}
function useReducedMotion(): boolean {
  return useSyncExternalStore(subscribe, getSnapshot, () => false);
}

export function Registry() {
  const reducedMotion = useReducedMotion();
  const transition = reducedMotion
    ? "none"
    : "background var(--dur-base) var(--ease-decelerate), opacity var(--dur-base) var(--ease-decelerate)";

  const [catalog, setCatalog] = useState<Capability[] | null>(null);
  const [fetchErr, setFetchErr] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>("Connectors");
  const [filter, setFilter] = useState<TrustFilter>("All");
  const [sel, setSel] = useState<string | null>(null);

  // Search state
  const [query, setQuery] = useState("");
  const [searching, setSearching] = useState(false);
  const [searchResults, setSearchResults] = useState<RegistryCandidate[] | null>(null);
  const [searchErr, setSearchErr] = useState<string | null>(null);

  // Search input wrapper focus state for focus-within ring
  const [searchFocused, setSearchFocused] = useState(false);

  // Hover states for tab buttons
  const [tabHover, setTabHover] = useState<Tab | null>(null);
  // Hover state for list rows
  const [rowHover, setRowHover] = useState<string | null>(null);
  // Hover state for filter buttons
  const [filterHover, setFilterHover] = useState<TrustFilter | null>(null);
  // Hover state for search submit button
  const [searchBtnHover, setSearchBtnHover] = useState(false);

  // Abort ref for handleSearch
  const searchAbortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    fetchRegistry()
      .then((data) => {
        if (!controller.signal.aborted) {
          setCatalog(data.capabilities);
        }
      })
      .catch((e) => {
        if (!controller.signal.aborted) {
          setFetchErr(String(e?.message ?? e));
        }
      });
    return () => controller.abort();
  }, []);

  async function handleSearch(e: React.FormEvent) {
    e.preventDefault();
    if (!query.trim()) return;

    // Abort any in-flight search
    if (searchAbortRef.current) {
      searchAbortRef.current.abort();
    }
    const controller = new AbortController();
    searchAbortRef.current = controller;

    setSearching(true);
    setSearchErr(null);
    setSearchResults(null);
    try {
      const res = await searchRegistry(query.trim());
      if (!controller.signal.aborted) {
        setSearchResults(res.candidates);
      }
    } catch (e) {
      if (!controller.signal.aborted) {
        setSearchErr(String((e as Error)?.message ?? e));
      }
    } finally {
      if (!controller.signal.aborted) {
        setSearching(false);
      }
    }
  }

  // ── Render: fetch error ──────────────────────────────────────────────────
  if (fetchErr) {
    return (
      <div
        role="alert"
        style={{
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          justifyContent: "center",
          gap: 12,
          padding: 40,
          color: "var(--err)",
          minHeight: 200,
        }}
      >
        <ServerOff size={28} aria-hidden="true" style={{ opacity: 0.7 }} />
        <span style={{ fontFamily: "var(--font-ui)", fontSize: 13 }}>
          Failed to load registry. {fetchErr}
        </span>
      </div>
    );
  }

  if (!catalog) {
    return (
      <p role="status" style={{ padding: 16, color: "var(--muted)", fontFamily: "var(--font-ui)", fontSize: 13 }}>
        Loading…
      </p>
    );
  }

  // ── Filtered list ────────────────────────────────────────────────────────
  // Tab filtering uses Capability.kind (e.g. "Skills", "Connectors", "Plugins").
  // Capabilities with no kind field fall through to all tabs so they are never hidden.
  const tabFiltered = catalog.filter(
    (c) => !c.kind || c.kind.toLowerCase() === tab.toLowerCase()
  );
  const filtered =
    filter === "All" ? tabFiltered : tabFiltered.filter((c) => c.trust_tier === filter);

  const active = catalog.find((c) => c.id === sel) ?? null;

  return (
    <>
      <div className="registry-root" style={{ display: "flex", flexDirection: "column", height: "100%" }}>
        {/* ── Sub-nav tab row ── */}
        <nav
          role="tablist"
          aria-label="Registry sections"
          style={{
            display: "flex",
            gap: 2,
            padding: "6px 10px",
            borderBottom: "1px solid var(--border)",
            background: "var(--surface-1)",
          }}
        >
          {TABS.map(({ label, icon }) => {
            const isActive = tab === label;
            const isHovered = tabHover === label;
            return (
              <button
                key={label}
                role="tab"
                aria-selected={isActive}
                onClick={() => {
                  setTab(label);
                  setSel(null);
                }}
                onMouseEnter={() => setTabHover(label)}
                onMouseLeave={() => setTabHover(null)}
                style={{
                  background: isActive ? "var(--surface-3)" : "transparent",
                  border: `1px solid ${
                    isActive
                      ? "var(--interact)"
                      : isHovered
                      ? "var(--border-strong)"
                      : "var(--border)"
                  }`,
                  color: isActive ? "var(--fg)" : "var(--muted)",
                  padding: "4px 14px",
                  minHeight: 28,
                  cursor: "pointer",
                  fontFamily: "var(--font-ui)",
                  fontSize: 12,
                  letterSpacing: "0.02em",
                  display: "inline-flex",
                  alignItems: "center",
                  gap: 6,
                  transition,
                }}
              >
                {icon}
                {label}
              </button>
            );
          })}
        </nav>

        {/* ── Search bar ── */}
        <form
          onSubmit={handleSearch}
          style={{
            display: "flex",
            gap: 6,
            padding: "10px 10px 8px",
            borderBottom: "1px solid var(--border)",
            background: "var(--surface-1)",
            alignItems: "center",
          }}
        >
          <label
            htmlFor="registry-search"
            style={{
              position: "absolute",
              width: 1,
              height: 1,
              padding: 0,
              margin: -1,
              overflow: "hidden",
              clip: "rect(0,0,0,0)",
              whiteSpace: "nowrap",
              border: 0,
            }}
          >
            Search registry
          </label>
          <div
            onFocus={() => setSearchFocused(true)}
            onBlur={() => setSearchFocused(false)}
            style={{
              flex: 1,
              display: "flex",
              alignItems: "center",
              gap: 6,
              background: "var(--sunk)",
              border: `1px solid ${searchFocused ? "var(--interact)" : "var(--border)"}`,
              padding: "4px 8px",
              transition,
            }}
          >
            <Search size={13} aria-hidden="true" style={{ color: "var(--faint)", flexShrink: 0 }} />
            <input
              id="registry-search"
              type="text"
              placeholder="Search registry…"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              style={{
                flex: 1,
                background: "transparent",
                border: "none",
                outline: "none",
                color: "var(--fg)",
                fontFamily: "var(--font-ui)",
                fontSize: 13,
                padding: 0,
              }}
            />
          </div>
          <button
            type="submit"
            disabled={searching}
            aria-describedby={searching ? "search-status" : undefined}
            onMouseEnter={() => setSearchBtnHover(true)}
            onMouseLeave={() => setSearchBtnHover(false)}
            style={{
              background: "var(--interact)",
              border: "1px solid transparent",
              color: "var(--fg)",
              padding: "5px 14px",
              minHeight: 28,
              cursor: searching ? "default" : "pointer",
              opacity: searching ? 0.55 : searchBtnHover ? 0.82 : 1,
              fontFamily: "var(--font-ui)",
              fontSize: 12,
              letterSpacing: "0.03em",
              transition,
            }}
          >
            {searching ? "…" : "Search"}
          </button>
          <span
            id="search-status"
            role="status"
            aria-live="polite"
            style={{
              position: "absolute",
              width: 1,
              height: 1,
              overflow: "hidden",
              clip: "rect(0,0,0,0)",
              whiteSpace: "nowrap",
            }}
          >
            {searching ? "Search in progress" : ""}
          </span>
        </form>

        {/* ── Search results live region ── */}
        <div aria-live="polite" aria-atomic="false">
          {searchErr && (
            <p style={{ margin: "8px 10px", color: "var(--err)", fontSize: 12, fontFamily: "var(--font-ui)" }}>
              Search failed: {searchErr}
            </p>
          )}
          {searchResults !== null && (
            <div
              style={{
                padding: "8px 10px",
                borderBottom: "1px solid var(--border)",
                background: "var(--surface-2)",
              }}
            >
              <span className="werk-label" style={{ color: "var(--muted)", fontSize: 10 }}>
                Search results
              </span>
              {/* Announced count for screen readers */}
              <span
                className="sr-only"
              >
                {searchResults.length} result{searchResults.length !== 1 ? "s" : ""} found
              </span>
              {searchResults.length === 0 ? (
                <div
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: 8,
                    marginTop: 8,
                    color: "var(--muted)",
                    fontSize: 12,
                    fontFamily: "var(--font-ui)",
                  }}
                >
                  <PackageSearch size={14} aria-hidden="true" style={{ opacity: 0.6 }} />
                  No results found.
                </div>
              ) : (
                <ul
                  role="list"
                  style={{ listStyle: "none", margin: "6px 0 0", padding: 0, display: "flex", flexDirection: "column", gap: 3 }}
                >
                  {searchResults.map((r) => (
                    <li
                      key={r.id}
                      role="listitem"
                      style={{
                        display: "flex",
                        alignItems: "center",
                        gap: 10,
                        padding: "5px 8px",
                        background: "var(--surface-3)",
                        color: "var(--fg)",
                        fontSize: 12,
                        fontFamily: "var(--font-ui)",
                        borderLeft: "2px solid var(--interact)",
                      }}
                    >
                      <span style={{ fontVariationSettings: "'wght' 510" }}>{r.name}</span>
                      <span style={{ color: "var(--secondary)", fontSize: 11 }}>{r.description}</span>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )}
        </div>

        {/* ── Trust tier filter row ── */}
        <div
          role="group"
          aria-label="Filter by trust tier"
          style={{
            display: "flex",
            gap: 4,
            padding: "6px 10px",
            borderBottom: "1px solid var(--border)",
            background: "var(--surface-2)",
            flexWrap: "wrap",
            alignItems: "center",
          }}
        >
          <FilterIcon size={11} aria-hidden="true" style={{ color: "var(--faint)", marginRight: 2 }} />
          {FILTERS.map((f) => {
            const isActive = filter === f;
            const isHovered = filterHover === f;
            return (
              <button
                key={f}
                aria-pressed={isActive}
                onClick={() => {
                  setFilter(f);
                  setSel(null);
                }}
                onMouseEnter={() => setFilterHover(f)}
                onMouseLeave={() => setFilterHover(null)}
                className="werk-stamp"
                style={{
                  // Active: surface lift + indigo border (selection, not tier color)
                  // Off: border-only with tier-colored icon, muted text
                  color: isActive ? "var(--fg)" : "var(--muted)",
                  background: isActive ? "var(--surface-3)" : "transparent",
                  border: `1px solid ${
                    isActive
                      ? "var(--interact)"
                      : isHovered
                      ? "var(--border-strong)"
                      : "var(--border)"
                  }`,
                  minHeight: 24,
                  minWidth: 24,
                  cursor: "pointer",
                  display: "inline-flex",
                  alignItems: "center",
                  gap: 4,
                  opacity: isActive ? 1 : 0.8,
                  transition,
                }}
              >
                {/* Icon carries tier color as semantic signal in off-state; active uses fg */}
                <span style={{ color: isActive ? "var(--fg)" : FILTER_TIER_COLORS[f], display: "inline-flex", alignItems: "center" }}>
                  {FILTER_ICON_MAP[f]}
                </span>
                {f}
              </button>
            );
          })}
        </div>

        {/* ── Main master-detail ── */}
        {catalog.length === 0 ? (
          <div
            style={{
              display: "flex",
              flexDirection: "column",
              alignItems: "center",
              justifyContent: "center",
              gap: 10,
              padding: 48,
              color: "var(--muted)",
              flex: 1,
            }}
          >
            <PackageSearch size={28} aria-hidden="true" style={{ opacity: 0.45 }} />
            <span style={{ fontFamily: "var(--font-ui)", fontSize: 13 }}>
              No capabilities found in registry.
            </span>
          </div>
        ) : (
          <div style={{ display: "flex", flex: 1, overflow: "hidden" }}>
            {/* ── List panel ── */}
            <ul
              style={{
                width: "42%",
                margin: 0,
                padding: "6px",
                listStyle: "none",
                borderRight: "1px solid var(--border)",
                overflow: "auto",
              }}
            >
              {filtered.length === 0 ? (
                <li
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: 8,
                    padding: "12px 8px",
                    color: "var(--muted)",
                    fontSize: 12,
                    fontFamily: "var(--font-ui)",
                  }}
                >
                  <FilterIcon size={13} aria-hidden="true" style={{ opacity: 0.5 }} />
                  No capabilities match this filter.
                </li>
              ) : (
                filtered.map((cap) => {
                  const isSelected = sel === cap.id;
                  const isHovered = rowHover === cap.id;
                  return (
                    <li key={cap.id}>
                      <button
                        aria-pressed={isSelected}
                        aria-label={`${cap.id}, trust tier: ${cap.trust_tier}`}
                        onClick={() => setSel(cap.id)}
                        onMouseEnter={() => setRowHover(cap.id)}
                        onMouseLeave={() => setRowHover(null)}
                        className={isSelected ? "werk-notch" : ""}
                        style={{
                          width: "100%",
                          textAlign: "left",
                          background: isSelected
                            ? "var(--surface-3)"
                            : isHovered
                            ? "var(--surface-2)"
                            : "transparent",
                          border: "none",
                          color: "var(--fg)",
                          padding: "7px 8px",
                          cursor: "pointer",
                          display: "flex",
                          alignItems: "center",
                          gap: 10,
                          transition,
                        }}
                      >
                        {/* Monogram tile — sunk fill so it remains readable at all row states */}
                        <span
                          aria-hidden="true"
                          style={{
                            width: 30,
                            height: 30,
                            display: "flex",
                            alignItems: "center",
                            justifyContent: "center",
                            background: "var(--sunk)",
                            color: tintFor(cap.category),
                            fontFamily: "var(--font-mono)",
                            fontVariationSettings: "'wght' 590",
                            fontSize: 13,
                            flexShrink: 0,
                            borderLeft: `2px solid ${tintFor(cap.category)}`,
                          }}
                        >
                          {cap.category.charAt(0).toUpperCase()}
                        </span>
                        <span style={{ flex: 1, display: "flex", flexDirection: "column", gap: 4, minWidth: 0 }}>
                          <span
                            className="werk-num"
                            style={{ fontSize: 12, fontFamily: "var(--font-mono)", color: "var(--fg)", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}
                          >
                            {cap.id}
                          </span>
                          <span
                            className="werk-stamp"
                            aria-hidden="true"
                            style={{
                              color: TIER_COLOR[cap.trust_tier],
                              alignSelf: "flex-start",
                              display: "inline-flex",
                              alignItems: "center",
                              gap: 3,
                            }}
                          >
                            {TIER_ICON_MAP[cap.trust_tier]}
                            {cap.trust_tier.toUpperCase()}
                          </span>
                        </span>
                      </button>
                    </li>
                  );
                })
              )}
            </ul>

            {/* ── Detail panel ── */}
            <section
              aria-label="Capability details"
              style={{
                flex: 1,
                padding: 16,
                overflow: "auto",
                background: active ? "var(--surface-2)" : "transparent",
                transition: transition !== "none" ? "background var(--dur-base) var(--ease-decelerate)" : "none",
              }}
            >
              {active ? (
                <dl style={{ color: "var(--secondary)", display: "grid", gap: 14, margin: 0 }}>
                  {/* ID — primary identifier: larger, mono, full fg, extra bottom margin */}
                  <div style={{ marginBottom: 6 }}>
                    <dt className="werk-label" style={{ color: "var(--muted)", fontSize: 10 }}>
                      ID
                    </dt>
                    <dd
                      className="werk-num"
                      style={{
                        margin: 0,
                        color: "var(--fg)",
                        marginTop: 4,
                        fontSize: "var(--text-lg, 17px)",
                        fontVariationSettings: "'wght' 590",
                        fontFamily: "var(--font-mono)",
                        letterSpacing: "0.01em",
                      }}
                    >
                      {active.id}
                    </dd>
                  </div>
                  {/* WHAT IT IS — descriptive primary field, noticeably larger */}
                  {active.what_it_is && (
                    <div>
                      <dt className="werk-label" style={{ color: "var(--muted)", fontSize: 10 }}>
                        WHAT IT IS
                      </dt>
                      <dd style={{ margin: 0, color: "var(--fg)", marginTop: 3, fontSize: 13, fontFamily: "var(--font-ui)", lineHeight: 1.5 }}>
                        {active.what_it_is}
                      </dd>
                    </div>
                  )}
                  {/* Secondary fields: dimmer/smaller */}
                  <div>
                    <dt className="werk-label" style={{ color: "var(--muted)", fontSize: 10 }}>
                      CATEGORY
                    </dt>
                    <dd style={{ margin: 0, color: "var(--secondary)", marginTop: 3, fontSize: 12, fontFamily: "var(--font-ui)" }}>
                      {active.category}
                    </dd>
                  </div>
                  {active.maintainer && (
                    <div>
                      <dt className="werk-label" style={{ color: "var(--muted)", fontSize: 10 }}>
                        MAINTAINER
                      </dt>
                      <dd style={{ margin: 0, color: "var(--secondary)", marginTop: 3, fontSize: 12, fontFamily: "var(--font-ui)" }}>
                        {active.maintainer}
                      </dd>
                    </div>
                  )}
                  {active.maintenance && (
                    <div>
                      <dt className="werk-label" style={{ color: "var(--muted)", fontSize: 10 }}>
                        MAINTENANCE
                      </dt>
                      <dd style={{ margin: 0, color: "var(--secondary)", marginTop: 3, fontSize: 12, fontFamily: "var(--font-ui)" }}>
                        {active.maintenance}
                      </dd>
                    </div>
                  )}
                  <div>
                    <dt className="werk-label" style={{ color: "var(--muted)", fontSize: 10 }}>
                      TRUST TIER
                    </dt>
                    <dd style={{ margin: 0, marginTop: 5 }}>
                      <span
                        className="werk-stamp"
                        aria-label={`Trust tier: ${active.trust_tier}`}
                        style={{
                          color: TIER_COLOR[active.trust_tier],
                          display: "inline-flex",
                          alignItems: "center",
                          gap: 4,
                        }}
                      >
                        {TIER_ICON_MAP[active.trust_tier]}
                        {active.trust_tier.toUpperCase()}
                      </span>
                    </dd>
                  </div>
                </dl>
              ) : (
                <div
                  style={{
                    display: "flex",
                    flexDirection: "column",
                    alignItems: "center",
                    justifyContent: "center",
                    gap: 10,
                    height: "100%",
                    minHeight: 120,
                    color: "var(--faint)",
                  }}
                >
                  <Layers size={22} aria-hidden="true" style={{ opacity: 0.35 }} />
                  <span style={{ fontFamily: "var(--font-ui)", fontSize: 12 }}>
                    Select a capability.
                  </span>
                </div>
              )}
            </section>
          </div>
        )}
      </div>
    </>
  );
}
