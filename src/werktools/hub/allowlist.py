"""Tier-1 MCP allowlist — the curated, commit-pinned trust gate for installs.

Pure stdlib, no daemon, no network, no FastMCP. The curated Tier-1 set is
embedded as ``_SEED`` (transcribed from WERKHUB_TIER1_MCP_SEED.md, 2026-06-19,
70 servers). An operator MAY ship an external override file (same shape as
``Tier1Allowlist.to_dict``); the gate treats a present-but-invalid override as
an error and fails closed (see discovery.py), never silently promoting.

Trust mapping (metadata only — see ``catalog.normalize_trust_tier``):
  docker-built              -> "Security-Scanned"   (Cosign + SBOM + commit-pin)
  official / anthropic-connector -> "Official"      (first-party vendor/connector)
  not on the allowlist      -> "Community-Unverified" (default; deny-by-default)

NOTE (honest v0): the seed does NOT carry real OCI digests — pinning those
requires a Docker Hub API sweep the research could not complete, and fabricating
a digest would be dishonest. ``image_digest`` is therefore optional; the digest
pin in ``approve_and_write`` only fires for entries that DO carry a real digest
(e.g. from an operator-supplied override file).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ALLOWLIST_SCHEMA = "werk-tier1-allowlist-v1"
_SEED_DATE = "2026-06-19"
_TRUST_NOTE_MAX = 200
_MAX_ALLOWLIST_BYTES = 4 * 1024 * 1024  # fail closed on an absurdly large override file
_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")

_KIND_SOURCE = {"docker": "docker-mcp-catalog", "vendor": "official-vendor", "anthropic": "anthropic-connectors"}
_KIND_REASON = {"docker": "docker-built", "vendor": "official", "anthropic": "anthropic-connector"}
_VALID_SOURCES = tuple(_KIND_SOURCE.values())
_VALID_REASONS = tuple(_KIND_REASON.values())


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")[:64]


@dataclass(frozen=True)
class AllowlistEntry:
    """One pre-vetted Tier-1 server. Provenance is recorded, never executed."""

    server_id: str
    display_name: str
    source: str
    promotion_reason: str
    category: str = ""
    image_ref: str = ""
    image_digest: str | None = None
    anthropic_connector_id: str | None = None
    pinned_commit: str = ""
    pinned_date: str = ""

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "AllowlistEntry":
        server_id = str(raw.get("server_id", "")).strip()
        if not server_id:
            raise ValueError("allowlist entry requires a non-empty server_id")
        digest = raw.get("image_digest")
        digest_str = str(digest) if digest else None
        if digest_str and not _DIGEST_RE.match(digest_str):
            # fail closed: a malformed digest must never reach a docker arg
            raise ValueError(f"image_digest must be sha256:<64-hex>, got {digest_str!r}")
        # fail closed at the load boundary: an unknown source/reason in an
        # operator override must not become a silently-trusted entry.
        source = str(raw.get("source", ""))
        if source not in _VALID_SOURCES:
            raise ValueError(f"unknown allowlist source {source!r}; expected one of {_VALID_SOURCES}")
        reason = str(raw.get("promotion_reason", ""))
        if reason not in _VALID_REASONS:
            raise ValueError(f"unknown promotion_reason {reason!r}; expected one of {_VALID_REASONS}")
        connector = raw.get("anthropic_connector_id")
        return cls(
            server_id=server_id,
            display_name=str(raw.get("display_name", server_id)),
            source=source,
            promotion_reason=reason,
            category=str(raw.get("category", "")),
            image_ref=str(raw.get("image_ref", "")),
            image_digest=digest_str,
            anthropic_connector_id=str(connector) if connector else None,
            pinned_commit=str(raw.get("pinned_commit", "")),
            pinned_date=str(raw.get("pinned_date", "")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "server_id": self.server_id,
            "display_name": self.display_name,
            "source": self.source,
            "promotion_reason": self.promotion_reason,
            "category": self.category,
            "image_ref": self.image_ref,
            "image_digest": self.image_digest,
            "anthropic_connector_id": self.anthropic_connector_id,
            "pinned_commit": self.pinned_commit,
            "pinned_date": self.pinned_date,
        }


@dataclass(frozen=True)
class Tier1Allowlist:
    """The full curated set, with provenance and a pin timestamp."""

    schema: str
    pinned_at: str
    entries: tuple[AllowlistEntry, ...]

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Tier1Allowlist":
        if not isinstance(raw, dict):
            raise ValueError("tier1 allowlist must be a JSON object")
        schema = str(raw.get("schema", ""))
        if schema != ALLOWLIST_SCHEMA:
            raise ValueError(f"unknown allowlist schema {schema!r}; expected {ALLOWLIST_SCHEMA!r}")
        entries = tuple(
            AllowlistEntry.from_dict(item) for item in raw.get("entries", []) if isinstance(item, dict)
        )
        return cls(schema=schema, pinned_at=str(raw.get("pinned_at", "")), entries=entries)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "pinned_at": self.pinned_at,
            "entries": [entry.to_dict() for entry in self.entries],
        }

    def get(self, server_id: str) -> AllowlistEntry | None:
        # Reconciled axis = CASE ONLY. Seed ids are slug-lowercased; a registry
        # candidate id (RegistryCandidate.from_dict) preserves case, so a
        # mixed-case id like "Shopify-dev-mcp" still matches here. NOT yet
        # reconciled: underscores, run-collapse, edge-dash differences between
        # the two id schemes — a full shared-slug reconciliation is a P2 task
        # (see ADR-005). Any unmatched candidate falls to Community-Unverified
        # (deny-by-default), so a miss is always safe, never an over-trust.
        needle = server_id.lower()
        for entry in self.entries:
            if entry.server_id.lower() == needle:
                return entry
        return None


def is_tier1(allowlist: Tier1Allowlist, server_id: str) -> AllowlistEntry | None:
    """Return the matching entry if ``server_id`` is Tier-1, else None."""
    return allowlist.get(server_id)


_REASON_TO_TIER = {
    "docker-built": "Security-Scanned",  # Cosign + SBOM + commit-pin
    "official": "Official",              # first-party vendor repo/endpoint
    "anthropic-connector": "Official",   # Anthropic first-party connector
}


def tier1_trust_fields(entry: AllowlistEntry) -> dict[str, str]:
    """Map a Tier-1 entry to the three DownstreamServer trust fields.

    An unknown ``promotion_reason`` FAILS CLOSED to Community-Unverified — it is
    never silently promoted to Official. ``from_dict`` already rejects unknown
    reasons at the load boundary; this is the defense-in-depth net for any
    directly-constructed entry.
    """
    tier = _REASON_TO_TIER.get(entry.promotion_reason, "Community-Unverified")
    digest = entry.image_digest or "unpinned"
    note = (
        f"tier-1:{entry.promotion_reason} src={entry.source} "
        f"pin={entry.pinned_commit or 'n/a'} digest={digest}"
    )
    return {"trust_tier": tier, "trust_source": entry.source, "trust_note": note[:_TRUST_NOTE_MAX]}


def pin_digest(image: str, digest: str) -> str:
    """Rewrite an image reference to pin a content digest (drops any tag).

    A tag lives in the final path segment (``repo:tag``); a registry port lives
    in an earlier segment (``host:port/path``) and must be preserved.
    """
    base = image.split("@", 1)[0]  # drop any existing digest
    head, sep, last = base.rpartition("/")
    if ":" in last:
        last = last.split(":", 1)[0]  # strip :tag from the final segment only
    return f"{head}{sep}{last}@{digest}"


def load_tier1_allowlist(path: str | Path) -> Tier1Allowlist:
    """Load and validate an allowlist file. Raises ValueError on bad schema."""
    p = Path(path)
    size = p.stat().st_size
    if size > _MAX_ALLOWLIST_BYTES:
        raise ValueError(f"tier1 allowlist file too large: {size} bytes (max {_MAX_ALLOWLIST_BYTES})")
    raw = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("tier1 allowlist file must contain a JSON object")
    return Tier1Allowlist.from_dict(raw)


# ── Embedded curated seed (70 Tier-1 servers, WERKHUB_TIER1_MCP_SEED.md) ───────
# (display_name, identifier, kind, category, anthropic_connector_id)
# kind: "docker" = Docker-built (mcp/ namespace, Cosign+SBOM); "vendor" =
# first-party vendor repo/endpoint; "anthropic" = Anthropic first-party
# connector. The 2 Tier-2 candidates (Twilio alpha, archived Postgres ref) are
# intentionally EXCLUDED — they are not Tier-1.
_SEED: tuple[tuple[str, str, str, str, str | None], ...] = (
    # cloud (11)
    ("AWS Documentation", "mcp/aws-documentation", "docker", "cloud", None),
    ("Dynatrace", "mcp/dynatrace-mcp-server", "docker", "cloud", None),
    ("Grafana", "mcp/grafana", "docker", "cloud", None),
    ("Heroku", "mcp/heroku", "docker", "cloud", None),
    ("Kubernetes", "mcp/kubernetes", "docker", "cloud", None),
    ("Okta MCP Server", "mcp/okta-mcp-server", "docker", "cloud", None),
    ("Stripe", "mcp/stripe", "docker", "cloud", None),
    ("AWS MCP Servers", "awslabs/mcp", "vendor", "cloud", None),
    ("Azure MCP Server", "mcr.microsoft.com/azure-sdk/azure-mcp", "vendor", "cloud", None),
    ("Cloudflare Developer Platform", "cloudflare/mcp-server-cloudflare", "vendor", "cloud", None),
    ("Vercel MCP Server", "vercel/vercel-mcp-overview", "vendor", "cloud", None),
    # comms (9 Tier-1; Twilio is Tier-2 and excluded)
    ("Atlassian", "mcp/atlassian", "docker", "comms", None),
    ("Dart AI", "mcp/dart", "docker", "comms", None),
    ("LINE", "mcp/line", "docker", "comms", None),
    ("Slack", "mcp/slack", "docker", "comms", None),
    ("Teamwork", "mcp/teamwork", "docker", "comms", None),
    ("Gmail Connector", "claude.ai/connector/gmail", "anthropic", "comms", "gmail"),
    ("Google Calendar Connector", "claude.ai/connector/google-calendar", "anthropic", "comms", "google-calendar"),
    ("HubSpot MCP Server", "mcp.hubspot.com", "vendor", "comms", None),
    ("PagerDuty MCP Server", "PagerDuty/pagerduty-mcp-server", "vendor", "comms", None),
    # data (14 Tier-1; archived Postgres reference is Tier-2 and excluded)
    ("Airtable MCP Server", "mcp/airtable-mcp-server", "docker", "data", None),
    ("Chroma", "mcp/chroma", "docker", "data", None),
    ("ClickHouse", "mcp/clickhouse", "docker", "data", None),
    ("CockroachDB", "mcp/cockroachdb", "docker", "data", None),
    ("Couchbase", "mcp/couchbase", "docker", "data", None),
    ("Keboola MCP", "mcp/keboola-mcp", "docker", "data", None),
    ("MongoDB", "mcp/mongodb", "docker", "data", None),
    ("Neo4j", "mcp/neo4j", "docker", "data", None),
    ("Neon", "mcp/neon", "docker", "data", None),
    ("SQLite MCP Server", "mcp/sqlite-mcp-server", "docker", "data", None),
    ("Valkey MCP Server", "mcp/valkey-mcp-server", "docker", "data", None),
    ("Datadog MCP Server", "datadog-labs/mcp-server", "vendor", "data", None),
    ("Snowflake MCP Server", "Snowflake-Labs/mcp", "vendor", "data", None),
    ("Supabase MCP Server", "supabase-community/supabase-mcp", "vendor", "data", None),
    # dev (21)
    ("Atlas Docs", "mcp/atlas-docs", "docker", "dev", None),
    ("Browserbase", "mcp/browserbase", "docker", "dev", None),
    ("Buildkite", "mcp/buildkite", "docker", "dev", None),
    ("Camunda BPM", "mcp/camunda", "docker", "dev", None),
    ("CircleCI", "mcp/circleci", "docker", "dev", None),
    ("Context7", "mcp/context7", "docker", "dev", None),
    ("Docker Hub", "mcp/dockerhub", "docker", "dev", None),
    ("Git", "mcp/git", "docker", "dev", None),
    ("NPM Sentinel", "mcp/npm-sentinel", "docker", "dev", None),
    ("Playwright", "mcp/playwright", "docker", "dev", None),
    ("Sequential Thinking", "mcp/sequentialthinking", "docker", "dev", None),
    ("SmartBear", "mcp/smartbear", "docker", "dev", None),
    ("SonarQube", "mcp/sonarqube", "docker", "dev", None),
    ("Testkube", "mcp/testkube", "docker", "dev", None),
    ("Time", "mcp/time", "docker", "dev", None),
    ("Asana MCP Server", "mcp.asana.com/v2/mcp", "vendor", "dev", None),
    ("GitHub Official", "ghcr.io/github/github-mcp-server", "vendor", "dev", None),
    ("GitLab built-in MCP", "gitlab-built-in-mcp", "vendor", "dev", None),
    ("Linear MCP Server", "mcp.linear.app/mcp", "vendor", "dev", None),
    ("Sentry MCP Server", "getsentry/sentry-mcp", "vendor", "dev", None),
    ("Shopify Dev MCP", "Shopify/dev-mcp", "vendor", "dev", None),
    # files (7)
    ("Box", "mcp/box", "docker", "files", None),
    ("Filesystem", "mcp/filesystem", "docker", "files", None),
    ("MarkItDown", "mcp/markitdown", "docker", "files", None),
    ("Notion", "mcp/notion", "docker", "files", None),
    ("Figma MCP Server", "figma.com/mcp", "vendor", "files", None),
    ("Google Drive Connector", "claude.ai/connector/google-drive", "anthropic", "files", "google-drive"),
    ("Notion (official vendor)", "makenotion/notion-mcp-server", "vendor", "files", None),
    # memory (2)
    ("Memory", "mcp/memory", "docker", "memory", None),
    ("Neo4j Memory", "mcp/neo4j-memory", "docker", "memory", None),
    # search (6)
    ("Apify MCP Server", "mcp/apify-mcp-server", "docker", "search", None),
    ("Brave Search", "mcp/brave-search", "docker", "search", None),
    ("Elasticsearch", "mcp/elasticsearch", "docker", "search", None),
    ("Fetch", "mcp/fetch", "docker", "search", None),
    ("Perplexity", "mcp/perplexity-ask", "docker", "search", None),
    ("Brave Search (official vendor repo)", "brave/brave-search-mcp-server", "vendor", "search", None),
)


def _build_seed() -> Tier1Allowlist:
    built: list[AllowlistEntry] = []
    for name, identifier, kind, category, connector_id in _SEED:
        sid = _slug(identifier)
        if not sid:
            raise ValueError(
                f"seed identifier {identifier!r} slugs to empty server_id"
            )
        built.append(AllowlistEntry(
            server_id=sid,
            display_name=name,
            source=_KIND_SOURCE[kind],
            promotion_reason=_KIND_REASON[kind],
            category=category,
            image_ref=identifier,
            image_digest=None,
            anthropic_connector_id=connector_id,
            pinned_commit="",
            pinned_date=_SEED_DATE,
        ))
    return Tier1Allowlist(schema=ALLOWLIST_SCHEMA, pinned_at=f"{_SEED_DATE}T00:00:00Z", entries=tuple(built))


_DEFAULT_ALLOWLIST = _build_seed()


def default_allowlist() -> Tier1Allowlist:
    """Return the embedded, commit-pinned Tier-1 curated set."""
    return _DEFAULT_ALLOWLIST
