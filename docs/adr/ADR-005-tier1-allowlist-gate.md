# ADR-005: Deny-by-default Tier-1 install allowlist

Status: accepted
Date: 2026-06-19
Author: [CLAUDE]

## Context

The Official MCP Registry browse/install flow (`hub/discovery.py`:
`search_registry` -> `stage_install` -> `approve_and_write`) can bring an
arbitrary third-party server into hub.json. P1 needed a curated trust gate so
that only pre-vetted servers are promoted, while keeping the registry itself
discovery-only. The trust vocabulary is fixed by [ADR-004](ADR-004-string-trust-tiers.md).

## Decision

**A commit-pinned Tier-1 allowlist gates installs deny-by-default. A candidate
not on the allowlist is never silently promoted — it stays
`Community-Unverified` and carries an `[UNVETTED]` note.**

- **Curated seed embedded in `hub/allowlist.py`.** 70 servers (49 Docker-built +
  21 first-party/Anthropic-connector), transcribed from the Tier-1 MCP research.
  Being in source makes the seed commit-pinned by construction.
- **Optional operator override file** at `HubConfig.tier1_allowlist_path`
  (default `.werktools/tier1_allowlist.json`, resolved like `ledger_path`).
  Resolution: absent file -> use the embedded seed; present + valid -> use it;
  present + **invalid -> fail closed to an empty allowlist** and emit
  `registry.allowlist.error` (a corrupt override never promotes anything).
- **Gate is opt-in at the library boundary** (`allowlist_path=None` preserves
  the pre-P1 behaviour) but **always on from the CLI**, which passes the config
  path. `stage_install` records the decided tier in the connector metadata;
  `approve_and_write` re-checks at approve time and **downgrades** a server that
  was staged Tier-1 but no longer matches, emitting
  `registry.allowlist.tier_downgrade`.
- **Honest-degrade on digests.** The research carried no real OCI content
  digests (a Docker Hub sweep was out of scope), so the seed ships
  `image_digest=None` and the trust note renders `digest=unpinned`. Fabricating
  a `sha256` would violate the no-fiction rule. The OCI digest-pin rewrite in
  `approve_and_write` only fires for an override entry that carries a real,
  format-validated (`^sha256:[0-9a-f]{64}$`) digest.

## Consequences

- Nothing untrusted reaches hub.json silently; every promotion is explainable
  (`trust_source` + `trust_note`) and every gate action is ledgered.
- Three new EVENT_NAMES: `registry.allowlist.error`,
  `registry.allowlist.tier_downgrade` (+ `runtime.probed` from ADR-004 scope) —
  EVENT_NAMES total 40 -> 43.

## Known limitation: id reconciliation (v0)

`allowlist.Tier1Allowlist.get` matches the seed `server_id` (a lowercased slug)
against the registry candidate id **case-insensitively**. The two id schemes
still diverge on other axes (underscores, run-collapse, edge dashes) because
`RegistryCandidate.from_dict` uses a different sanitizer. A miss therefore
fails in the **safe direction** (`Community-Unverified`, deny-by-default), never
an over-trust. **P2:** extract one shared slug function applied by both
`RegistryCandidate.from_dict` and the allowlist so the id spaces reconcile by
construction.

## Revisit Trigger

- A Docker Hub / GHCR digest sweep that backfills real `image_digest` values
  (then digest-pinning becomes the norm for Docker-built Tier-1 entries).
- A Tier-2 / community-review promotion path, or an MCP-Scoreboard source that
  lets the first-party/Anthropic entries be re-gated from conditional to
  confirmed.
