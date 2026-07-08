# DESIGN.md — WERKHub dashboard direction lock (FL-1)

The locked reference for the WERKHub control-plane dashboard
(`src/werktools/hub/dashboard.py`, the `DASHBOARD_HTML` string). The frontend
loop judges every iteration against the **binary checklist** below — never fresh
taste. If an item is not a clear pass, it is the work; if all pass, the surface
is done.

## Direction

**Named direction:** *Mission-control / operator console* — dark-luxury, in the
Linear/Grafana/Vercel-dashboard family but darker, denser, and audit-first. It is
a read-mostly control plane for a single technical operator, not a marketing
surface. It must read as an intentional instrument, not a generic admin template.

**References (concrete):**
- **Linear** — sidebar restraint, caption-tier labels, calm density.
- **Grafana** — dense observability panels, panel show/hide, evidence/log density.
- **Vercel dashboard** — collapse-the-sidebar-for-fullscreen, quiet chrome.

**Dials:** VISUAL_DENSITY high (8) · MOTION low (3) · VARIANCE low.

**Round 2 — raised bar (deliberate direction change):**
- **One dominant focal anchor.** The board must not read as four equal-weight quadrants.
  The fleet headline (connectors live) is the hero: clearly larger scale + a real
  inline data-viz (a per-connector state sparkline, live data — not a "not wired" seam),
  so the eye lands there first (≈1.5–2× the weight of its neighbours).
- **Seam tiles carry a skeleton of their future content.** "Not wired" panels show a
  muted ghost of their shape (e.g. routing = ghost provider rows, spend = ghost bars)
  behind the dashed-amber + NOT WIRED flag — so they read as scaffolded/coming, never
  empty or broken.

## Tokens (A-FINAL canon — do not drift)

- **Surfaces:** `--wa-page #07080a` · `--wa-bg #0c0d10` · `--wa-surface #131519` ·
  `--wa-card #171a1f` · `--wa-raised #1c2026` · `--wa-sunk #08090b`.
- **Text:** `--wa-text #e8ecf2` · `--wa-2 #b0b8c8` · `--wa-muted #8b93a3` ·
  `--wa-faint #737d94`.
- **Semantic (one meaning each):** `--wa-indigo #6b78d6` = INTERACTION / selection
  ONLY · `--wa-ok #4ea46e` = ok/live · `--wa-warn #d7a13a` = needs-you / seam /
  at-risk · `--wa-danger #e0584e` = fail · `--wa-audit #cdb267` = scanned/audit ·
  `--wa-brand #c4ccd6` = platinum, brand only.
- **Type:** display = Space Grotesk 600 (self-hosted woff2); data/numbers =
  IBM Plex Mono 400/600 (self-hosted); body = system stack. Display tracking
  tight; labels are a caption tier (10px, tracked, faint).
- **Motion:** compositor-only (transform/opacity), 120–190ms, `cubic-bezier(.16,1,.3,1)`;
  `:active` scale(.97) push; everything collapses under `prefers-reduced-motion`.
- **Depth:** live/wired tiles ELEVATED (lighter raised surface + shadow); seam
  ("not wired") tiles RECESSED (sunk surface + inset + dashed amber + bold flag).
- **Shape:** governance/trust/state badges = rectangular stamps (radius ≤3px).
  Interactive filter pills may be pills.

## Ban list (taste-skill + canon)

Em-dash (— and –) anywhere · indigo on anything that is not interaction/selection ·
AI-purple / neon / outer glows · pure black or pure white · Inter as a *named*
default display font · card-shadow spam / uniform elevation everywhere · generic
admin-template look · fake data presented as real (placeholders must be honest, e.g.
`n/a`) · any external/CDN asset at runtime · removing `style-src 'unsafe-inline'`
(the bento needs inline style attrs; locked by test) · changing EVENT_NAMES.

## Binary checklist (the rubric — each item PASS / FAIL against the above)

1. Reads as dark mission-control operator console, not a generic admin template.
2. Sidebar collapses to a working icon-rail; active nav uses indigo only.
3. Every colour has exactly one meaning; indigo appears ONLY on interaction/selection.
4. No AI-purple, no neon, no outer glow; off-black/off-white (never #000/#fff).
5. Space Grotesk (display) + IBM Plex Mono (all numbers) actually load; clear
   caption→label→figure scale step.
6. Trust/governance/state badges are rectangular stamps (≤3px); only filter pills are pills.
7. Live tiles read as elevated/foreground; seam tiles read as deliberately parked
   (recessed + dashed amber + readable "not wired"), never broken/empty.
8. Zero em-dash / en-dash on the rendered page.
9. Icon-only collapsed nav keeps accessible names; visible focus; aria-live on
   auto-updating regions; WCAG AA contrast on text and controls.
10. Usable with no clipping/overflow at 320 / 768 / 1024 / 1440; board collapses to
    one column on mobile; tabs/nav reachable.
11. Motion is compositor-only, ≤200ms, reduced-motion honored; buttons have a
    designed hover + `:active` push.
12. Honest-degrade: not-wired features are labelled, no fabricated numbers.
13. The board has ONE dominant focal anchor (hero fleet tile: larger figure + a real
    per-connector sparkline), clearly heavier than its neighbours — not equal quadrants.
14. Seam tiles show a muted skeleton of their future content (ghost rows/bars), reading
    as scaffolded/coming, not empty.

## Gate (every iteration)

pytest (incl `tests/test_hub_dashboard.py`) · ruff · mypy · `node --check` on the
inline JS · live render at 320/768/1024/1440 + collapsed, console clean. Single-writer
gated commits on `main`, push to private origin.
