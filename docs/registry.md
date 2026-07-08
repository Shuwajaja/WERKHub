# Registry & capabilities

WERKHub treats **MCP tools** and **agent skills** as one unified type — a
*Capability* — stored in a local SQLite read-model. This gives the hub a single
answer to "when does a capability enter an agent's context, and when not?"

The full capability-registry design rationale lives in the internal design notes
and is summarized here in the public docs.

---

## The Capability model

Each capability row records:

| Field | Notes |
|---|---|
| `id` | Unique slug (e.g. `github.create_pr`) |
| `kind` | `tool` or `skill` |
| `category` | Free-form category tag |
| `risk` | `read` / `write` / `destructive` / `external` / `unknown` |
| `trust_tier` | `Official` / `Security-Scanned` / `Community-Unverified` |
| `needs_keys` | List of env-var **names** (never values) the capability requires |
| `relevance_tags` | Keywords for task-matching |
| `deluxe_base` | Whether this is a "Deluxe" baseline capability |

The `kind` distinction matters at surfacing time: a **skill** entering context
means its Markdown content is injected; a **tool** entering context means it is
registered as callable. Same selection pipeline, different action.

---

## Build the registry

The registry is not populated automatically. You build it once (and rebuild after
adding skills or updating the seed):

```bash
werktools hub registry build
# built .werktools/registry.db: 87 capabilities

# Also index local skills from a directory:
werktools hub registry build --skills-dir .werktools/skills
```

This populates a SQLite read-model at `.werktools/registry.db` from the embedded
seed (`hub/registry_seed.json`) plus any local skill cards.

---

## Browse capabilities

```bash
# List all capabilities
werktools hub registry list

# Filter by category
werktools hub registry list --category filesystem

# Show only Deluxe-baseline capabilities
werktools hub registry list --deluxe
```

Output columns: `id`, `kind`, `category`, `trust_tier`, `deluxe` marker.

---

## Select capabilities for a task

Given a natural-language task description, `registry select` runs the full
selection pipeline and returns a ranked shortlist:

```bash
werktools hub registry select "review the pull request and suggest improvements" --budget 8
```

The pipeline, all fail-closed:

1. **Relevance** — token overlap of task vs capability description and tags.
2. **Trust gate** — deny-by-default: only tiers the active profile allows;
   `Community-Unverified` requires explicit opt-in.
3. **Key presence** — capabilities whose required env-var names are absent are
   flagged (not silently excluded).
4. **Risk gate** — `write` and `destructive` capabilities require an approval
   token; they are listed as included but flagged accordingly.
5. **Budget** — the `--budget N` cap keeps context size bounded.

Each result carries a human-readable reason (`why in` / `why out`):

```
# selected for: review the pull request and suggest improvements
  + github.list_prs       (relevance: 0.82, trust: Official, key: present)
  + docs.search           (relevance: 0.71, trust: Official, key: present)
  + filesystem.read_file  (relevance: 0.61, trust: Community-Unverified, key: n/a)
# excluded (top reasons)
  - github.create_pr      (risk: write — approval required)
  - shell.run             (risk: destructive — deny in cautious profile)
```

---

## Search the registry (MCP surface)

Within a running hub session, agents can call the `registry_search` bridge tool
to find capabilities matching a query. This is the in-context equivalent of
`hub registry list`.

---

## Key handling: presence-only

Capabilities that need API keys record only the env-var **name** (e.g.
`GITHUB_TOKEN`, `STRIPE_SECRET_KEY`). The hub calls `os.environ` to check
presence — a boolean — and surfaces the result as `key_status`. No key value
is ever read, logged, returned, or stored. A capability whose required key is
absent is selectable but flagged `key-missing`.

---

## The Tier-1 seed

The embedded seed ships with ~70 curated servers (49 Docker-built, 21
first-party/Anthropic-connector). The seed is commit-pinned by being in source.
Real OCI content digests are not yet backfilled (honest-degrade: `digest=unpinned`).
A future release will add a Docker Hub / GHCR sweep to pin real digests.

Operators can override the seed at `.werktools/tier1_allowlist.json`.
A corrupt override fails closed to an empty allowlist — no silent promotion.
