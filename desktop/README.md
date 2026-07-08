# WERKHub Desktop

Native **Deno Desktop** app for WERKHub — the supervisor console for the governed
MCP gateway. Lives in the WERK_HUB monorepo at `desktop/`; the Python `werktools`
backend (repo root) runs as a loopback sidecar. Replaces the old Electron wrapper.

> One product, one repo: `werktools` (Python backend — also usable standalone via
> `pip install` + `hub serve`) + `desktop/` (this app). They release together as
> **WERKHub**; the backend stays publishable to PyPI on its own.

- **Shell:** Command-Cockpit (icon-rail + command-bar + console drawer)
- **Stack:** Deno 2.9 (`deno desktop`, webview) · Vite 6 · React 19 · Tailwind 4 · Vitest
- **Brand:** A-FINAL (`#0c0d10` / `#6b78d6` / Seam-W `#c4ccd6`), Inter + IBM Plex Mono

## Develop

```bash
pnpm install
pnpm test            # Vitest (unit/component, TDD)
pnpm dev             # Vite dev server (proxies /api -> 127.0.0.1:7879)
```

Run the backend sidecar separately during dev:

```bash
python -c "import sys; sys.argv=['werktools','hub','dashboard','--port','7879']; from werktools.cli import main; main()"
```

## Native build — Deno Desktop is the release shell

**Decision (2026-06-27): full Deno.** `deno desktop` is the shipping shell — no Electron
fallback. It produces a native desktop app: a single `WERKHub.msi` (~31 MB) the user
double-clicks; it opens a native window (not a browser tab), starts the `werktools`
sidecar, and serves the React UI. `deno desktop` is experimental (Deno 2.9); the Vite
dev flow (`pnpm dev` + browser) stays as the dev fallback, not a release shell.

`main.ts` serves the built `dist/` and reverse-proxies `/api` to the sidecar (injecting
the session token).

**Open release item — bundle the backend:** `main.ts` currently spawns the sidecar via
`python -c "...werktools..."`, so the host needs Python + `werktools` installed (fine for
devs). To ship to non-dev users, bundle the backend to a standalone binary (PyInstaller →
`backend.exe`) and have `bridge/sidecar.ts` spawn that instead — set
`WERKHUB_BACKEND_CMD` to the bundled exe. Plus Windows code-signing of the `.msi`.

```bash
deno task build:web       # vite build -> dist/
deno task desktop:build   # -> WERKHub.msi (embeds dist/ + Deno runtime + webview)
deno task desktop:dev     # native window with HMR
```

**WARNING: Toolchain gotcha:** `deno desktop` (with `nodeModulesDir: auto`) writes a
`node_modules/.deno/` store that duplicates `react`, which breaks Vitest's resolver
("Invalid hook call: more than one copy of React"). Safe sequence: run `deno task
desktop:build` **after** testing, and if you need to test again afterwards run
`rm -rf node_modules/.deno && pnpm install` to restore a single React copy.

**Known gaps (experimental runtime):** clipboard / native file-picker are absent in the
webview and are routed through the Deno layer (planned in `bridge/`). The POST session
token is injected server-side by `main.ts` so the webview never holds it. If `deno
desktop` cannot run on a host, the Vite dev flow (`pnpm dev` + browser) is the verified
fallback and is shell-agnostic.

## Status

Plan 1 (foundation + Connectors slice) complete. Board/Timeline/Registry (Plan 2) and
Approvals/Permissions/Console (Plan 3) follow. Spec + plans live in the `werktools`
repo under `docs/superpowers/`.
