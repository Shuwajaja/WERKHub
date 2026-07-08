// Generates src/tokens.css + src/shadcn-preset.css from design-system.json.
// Run: node scripts/gen-tokens.mjs   (or: pnpm gen:tokens)
// Edit design-system.json — never edit the generated CSS by hand.
import { readFileSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const ds = JSON.parse(readFileSync(join(root, "design-system.json"), "utf8"));

const skip = (k) => k.startsWith("$");
const block = (obj) =>
  Object.entries(obj)
    .filter(([k]) => !skip(k))
    .map(([k, v]) => `  --${k}: ${v};`)
    .join("\n");

const def = ds.meta.default;
const themeNames = Object.keys(ds.themes).filter((t) => t !== def);

// ── static tail: signature classes + globals (reference the generated tokens) ──
const TAIL = `
html, body, #root {
  height: 100%; margin: 0;
  background: var(--bg); color: var(--fg);
  font-family: var(--font-ui);
  font-feature-settings: "cv01" 1, "ss03" 1;
}
.werk-label {
  font-family: var(--font-ui);
  font-variation-settings: "wght" var(--label-weight);
  font-size: var(--label-size);
  letter-spacing: var(--label-tracking);
  text-transform: uppercase;
  color: var(--muted);
}
.werk-num {
  font-family: var(--font-mono);
  font-variant-numeric: tabular-nums lining-nums;
  font-feature-settings: "tnum" 1;
}
.werk-stamp {
  display: inline-block; border: 1px solid currentColor;
  padding: 0.15rem 0.55rem; border-radius: var(--radius-sm);
  font-family: var(--font-mono); font-size: 0.6rem; letter-spacing: 0.06em;
}
.werk-notch { box-shadow: inset 2px 0 0 var(--interact); }
.werk-rule { height: 1px; background: var(--border); border: 0; margin: 0; }
.werk-live {
  display: inline-flex; align-items: center; gap: 5px;
  background: var(--ok); color: var(--on-accent);
  padding: 0.1rem 0.45rem; border-radius: var(--radius-sm);
  font-family: var(--font-mono); font-size: 0.6rem; letter-spacing: 0.06em;
}
.blueprint-grid {
  background-image:
    linear-gradient(var(--border-subtle) 1px, transparent 1px),
    linear-gradient(90deg, var(--border-subtle) 1px, transparent 1px);
  background-size: 28px 28px;
  -webkit-mask-image: radial-gradient(circle at center, #000 0%, transparent 75%);
  mask-image: radial-gradient(circle at center, #000 0%, transparent 75%);
}
.sr-only {
  position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px;
  overflow: hidden; clip: rect(0,0,0,0); white-space: nowrap; border: 0;
}
:focus-visible { outline: none; box-shadow: var(--focus-ring); }
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after {
    animation-duration: 0.01ms !important;
    animation-iteration-count: 1 !important;
    transition-duration: 0.01ms !important;
  }
}
`;

const HEADER = `/* GENERATED from design-system.json by scripts/gen-tokens.mjs — do not edit by hand. */\n`;

let css = `${HEADER}@import "tailwindcss";\n\n:root {\n`;
css += block(ds.shared) + "\n";
css += block(ds.text) + "\n";
css += block(ds.themes[def]) + "\n";
css += block(ds.artifacts) + "\n";
css += "}\n\n";

for (const t of themeNames) {
  css += `:root[data-theme="${t}"] {\n${block(ds.themes[t])}\n}\n\n`;
}
css += TAIL;

writeFileSync(join(root, "src", "tokens.css"), css);

// ── shadcn preset: alias shadcn vars to our tokens (auto-flips with theme) ──
let pre = `${HEADER}/* shadcn/ui preset — import after tokens.css to render any shadcn component/block on-brand. */\n:root {\n`;
pre += Object.entries(ds.shadcnMap)
  .filter(([k]) => !skip(k))
  .map(([k, v]) => `  --${k}: var(${v});`)
  .join("\n");
pre += `\n  --radius: var(--radius-xs);\n}\n`;
writeFileSync(join(root, "src", "shadcn-preset.css"), pre);

console.log(`tokens.css: ${1 + themeNames.length} themes (${def} default) · shadcn-preset.css: ${Object.keys(ds.shadcnMap).filter((k) => !skip(k)).length} aliases`);
