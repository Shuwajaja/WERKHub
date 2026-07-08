"""Local hub dashboard: stdlib HTTP + SSE ledger tail + gated kill.

`werktools hub dashboard` serves one inline HTML page plus JSON/SSE endpoints
on 127.0.0.1 — a read-only fleet/evidence view with a kill button gated by
WERK_ALLOW_HUB_KILL. Stdlib only (no FastMCP); the only long-lived loops
live inside run_dashboard / the request handler, started from the CLI.
"""

from __future__ import annotations

import dataclasses
import ipaddress
import json
import os
import secrets
import signal
import sys
import warnings
from dataclasses import dataclass
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .contracts import HubConfig
from .dashboard_fonts import IBM_PLEX_MONO_400, IBM_PLEX_MONO_600, SPACE_GROTESK_600
from .ledger import recent_events_verified, record_event
from .store_bridge import persist_hub_config

POLL_INTERVAL_S = 2.0
_IS_WINDOWS = os.name == "nt"


def _registry_catalog_json(config_path: "str | Path | None") -> bytes:
    """Return JSON bytes for the capability catalog from registry.db.

    The DB is expected at ``Path(config_path).parent / "registry.db"`` — the
    same convention the CLI uses.  Fail-closed: if config_path is None, the DB
    is missing, or it is empty, returns an empty capabilities list without
    raising.  Keys are presence-checked via os.environ; values are never read,
    logged, or returned.
    """
    from .registry_db import category_counts, query_capabilities

    if config_path is None:
        return json.dumps({"capabilities": [], "category_counts": {}}).encode("utf-8")

    db_path = Path(config_path).parent / "registry.db"
    caps = query_capabilities(db_path)  # fail-closed to [] on missing/corrupt DB

    rows: list[dict[str, Any]] = []
    for cap in caps:
        # Compute keys_present: all required env vars must be present in os.environ.
        # NEVER read or return a value — presence (bool) only.
        names: list[str] = list(cap.needs_keys)
        keys_present: bool = all(name in os.environ for name in names) if names else True
        rows.append(
            {
                "id": cap.id,
                "kind": cap.kind,
                "category": cap.category,
                "trust_tier": cap.trust_tier,
                "deluxe_base": cap.deluxe_base,
                "maintenance": cap.maintenance,
                "popularity": cap.popularity,
                "needs_keys": names,
                "keys_present": keys_present,
            }
        )

    counts = category_counts(db_path)
    return json.dumps({"capabilities": rows, "category_counts": counts}).encode("utf-8")

DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>WERK Hub</title>
<style>__WERK_FONTS__
/* A-FINAL canonical tokens (+ handoff Bento tones) */
:root{
--wa-page:#07080a;--wa-bg:#0c0d10;--wa-surface:#131519;--wa-card:#171a1f;--wa-raised:#1c2026;--wa-sunk:#08090b;
--wa-text:#e8ecf2;--wa-2:#b0b8c8;--wa-muted:#8b93a3;--wa-faint:#737d94;--wa-dis:#4a5060;--wa-dis-2:#6b7384;
--wa-indigo:#6b78d6;--wa-accent:#7b88db;--wa-ok:#4ea46e;--wa-warn:#d7a13a;--wa-danger:#e0584e;
--wa-audit:#cdb267;--wa-brand:#c4ccd6;
--hair:rgba(255,255,255,.07);--hair-2:rgba(255,255,255,.08);--hair-3:rgba(255,255,255,.13);--hair-h:rgba(255,255,255,.17);
--font-body:Inter,system-ui,-apple-system,sans-serif;
--font-disp:"Space Grotesk",Inter,system-ui,sans-serif;
--font-mono:"IBM Plex Mono",ui-monospace,SFMono-Regular,monospace;
--ease:cubic-bezier(.4,0,.2,1);}
*{box-sizing:border-box}
body{margin:0;color:var(--wa-text);font-family:var(--font-body);font-size:13px;-webkit-font-smoothing:antialiased;text-rendering:optimizeLegibility;
background:radial-gradient(1200px 640px at 78% -8%,rgba(107,120,214,.06),transparent 58%),var(--wa-page);
background-image:radial-gradient(rgba(255,255,255,.05) 1px,transparent 1.4px);background-size:24px 24px}
.mono{font-family:var(--font-mono)}
.sr-only{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap;border:0}
.sr-only-focusable:focus{position:static;width:auto;height:auto;clip:auto;white-space:normal;margin:0;padding:4px 8px;background:var(--wa-raised);color:var(--wa-text);border-radius:3px;z-index:100}
:focus-visible{outline:2px solid var(--wa-indigo);outline-offset:2px}
button:active{transform:scale(.97)}
[hidden]{display:none!important}
.wrap{padding:30px 28px 56px}
.shell{width:1360px;max-width:100%;margin:0 auto}
.frame{border:1px solid var(--hair-2);border-radius:16px;overflow:hidden;background:var(--wa-bg);box-shadow:0 40px 100px -50px rgba(0,0,0,.9)}
/* animations */
@keyframes wkBr{0%,100%{opacity:1;box-shadow:0 0 0 0 rgba(78,164,110,.5)}50%{opacity:.55;box-shadow:0 0 0 5px rgba(78,164,110,0)}}
@keyframes wkBrI{0%,100%{opacity:1;box-shadow:0 0 0 0 rgba(107,120,214,.5)}50%{opacity:.55;box-shadow:0 0 0 5px rgba(107,120,214,0)}}
@keyframes wkIn{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:none}}
.wkbr{animation:wkBr 2.4s var(--ease) infinite}.wkbri{animation:wkBrI 2.4s var(--ease) infinite}
/* shell: collapsible sidebar + main column */
.frame.app{display:flex}
.side{width:236px;flex-shrink:0;display:flex;flex-direction:column;background:linear-gradient(180deg,var(--wa-surface),var(--wa-bg));border-right:1px solid var(--hair);transition:width .22s var(--ease)}
.app.col .side{width:62px}
.side-top{display:flex;align-items:center;gap:11px;padding:16px 16px 13px;height:57px;overflow:hidden}
.brand-mark{flex-shrink:0}
.brand-word{font-family:var(--font-disp);font-weight:600;letter-spacing:.15em;font-size:13px;color:var(--wa-brand);white-space:nowrap}
.app.col .brand-word{opacity:0;pointer-events:none}
.nav{display:flex;flex-direction:column;gap:3px;padding:8px 11px;flex:1;overflow-y:auto;scrollbar-width:none}
.nav::-webkit-scrollbar{display:none}
.nav-sec{font-size:9px;font-weight:600;text-transform:uppercase;letter-spacing:.16em;color:var(--wa-faint);padding:14px 10px 6px;white-space:nowrap}
.app.col .nav-sec{opacity:0}
.tab{display:flex;align-items:center;gap:12px;width:100%;padding:9px 11px;border-radius:8px;color:var(--wa-2);cursor:pointer;background:transparent;border:1px solid transparent;font-family:var(--font-body);font-size:13px;font-weight:510;text-align:left;white-space:nowrap;transition:background .14s,color .14s}
.tab:hover{background:rgba(255,255,255,.04);color:var(--wa-text)}
.tab.active{background:var(--wa-raised);color:var(--wa-text);border-color:var(--hair-2);box-shadow:inset 2px 0 0 var(--wa-indigo)}
.tab svg{width:18px;height:18px;flex-shrink:0}
.app.col .tab{justify-content:center;padding:9px 0}
.app.col .tab .lbl{opacity:0;width:0;overflow:hidden}
.side-foot{padding:11px;border-top:1px solid var(--hair);display:flex;flex-direction:column;gap:9px}
.tb-spacer{flex:1}
.tb-status{display:inline-flex;align-items:center;gap:8px;font-family:var(--font-mono);font-size:11px;color:var(--wa-muted);padding:0 8px;white-space:nowrap}
.tb-dot{width:6px;height:6px;border-radius:50%;background:var(--wa-ok);flex-shrink:0}
.app.col .tb-status .lbl{opacity:0;width:0;overflow:hidden}
.tb-clock{font-family:var(--font-mono);font-size:11px;color:var(--wa-faint)}
.kill-btn{display:flex;align-items:center;justify-content:center;gap:7px;font-family:var(--font-body);font-weight:510;text-transform:uppercase;letter-spacing:.1em;font-size:10px;padding:9px;border-radius:7px;border:1px solid var(--wa-warn);color:var(--wa-warn);background:rgba(215,161,58,.07);cursor:pointer;transition:filter .12s;white-space:nowrap}
.kill-btn:hover{filter:brightness(1.12)}
.app.col .kill-btn .lbl{display:none}
/* main column + slim header */
.main{flex:1;min-width:0;display:flex;flex-direction:column}
.head{display:flex;align-items:center;gap:13px;padding:13px 22px;min-height:57px;border-bottom:1px solid var(--hair);background:linear-gradient(180deg,var(--wa-surface),var(--wa-bg))}
.burger{width:30px;height:30px;border-radius:7px;border:1px solid var(--hair-3);background:transparent;color:var(--wa-2);cursor:pointer;display:flex;align-items:center;justify-content:center;flex-shrink:0;transition:color .12s,border-color .12s}
.burger:hover{color:var(--wa-text);border-color:var(--hair-h)}
.crumb{font-family:var(--font-disp);font-size:15px;font-weight:600;color:var(--wa-text);letter-spacing:.01em}
.crumb .sub{font-family:var(--font-body);font-weight:400;font-size:12.5px;color:var(--wa-faint);margin-left:9px}
/* panel fold (show/hide sections) */
.chev{width:22px;height:22px;border-radius:5px;border:1px solid transparent;background:transparent;color:var(--wa-faint);cursor:pointer;display:flex;align-items:center;justify-content:center;flex-shrink:0;transition:color .12s,background .12s}
.chev:hover{color:var(--wa-text);background:rgba(255,255,255,.05)}
.chev svg{transition:transform .18s var(--ease)}
.folded .chev svg{transform:rotate(-90deg)}
.folded .foldbody{display:none}
/* kill confirm strip */
.kill-strip{display:flex;align-items:center;gap:14px;padding:13px 22px;border-bottom:1px solid rgba(215,161,58,.3);background:linear-gradient(180deg,rgba(215,161,58,.09),rgba(215,161,58,.02));animation:wkIn .24s ease}
.kill-strip .title{font-size:12.5px;font-weight:510;color:var(--wa-text)}
.kill-strip .body{font-size:11px;color:var(--wa-muted);margin-top:2px}
.kill-strip .flag{font-family:var(--font-mono);color:var(--wa-warn)}
.strip-btn{font-family:var(--font-body);font-weight:510;text-transform:uppercase;letter-spacing:.06em;font-size:10px;padding:8px 13px;border-radius:3px;border:1px solid var(--hair-3);cursor:pointer}
.strip-btn.disabled{color:var(--wa-dis);background:transparent;cursor:not-allowed}
.strip-btn.dismiss{color:var(--wa-2);background:var(--wa-raised)}
/* tabpanes */
.pane{display:none}.pane.show{display:block;animation:wkIn .24s ease}
/* board bento */
.board{padding:18px;display:grid;grid-template-columns:repeat(12,1fr);gap:14px}
.tile{background:linear-gradient(180deg,var(--wa-raised),var(--wa-card));border:1px solid var(--hair-3);border-radius:12px;padding:18px 20px;position:relative;overflow:hidden;box-shadow:0 14px 30px -12px rgba(0,0,0,.85),inset 0 1px 0 rgba(255,255,255,.06);transition:border-color .19s var(--ease),background .19s var(--ease),box-shadow .19s var(--ease),transform .19s var(--ease)}
.tile:hover{border-color:var(--hair-h);box-shadow:0 16px 32px -18px rgba(0,0,0,.85);transform:translateY(-1px)}
.tile .rule{position:absolute;left:0;top:0;right:0;height:2px;opacity:.8}
.tile .label{font-family:var(--font-body);font-weight:600;text-transform:uppercase;letter-spacing:.15em;font-size:10px;color:var(--wa-faint)}
.s5{grid-column:span 5}.s4{grid-column:span 4}.s3{grid-column:span 3}.s12{grid-column:span 12}
.s6{grid-column:span 6}.s7{grid-column:span 7}.s8{grid-column:span 8}
.figure{font-family:var(--font-mono);font-weight:600;line-height:1;color:var(--wa-text)}
/* sparkline */
.spark{display:flex;align-items:flex-end;gap:4px;height:46px;margin-top:18px}
.spark span{flex:1;border-radius:2px}
.hero{padding:22px 26px}
.herorow{display:flex;align-items:center;gap:34px;flex-wrap:wrap}
.herofleet{flex:1;min-width:240px}
.herofleet .spark{height:54px}
.herodiv{width:1px;align-self:stretch;background:var(--hair-2)}
.herotrust{flex-shrink:0}
.skel{display:flex;flex-direction:column;gap:10px;margin-top:8px}
.skel-row{display:flex;align-items:center;gap:9px}
.skel-dot{width:18px;height:18px;border-radius:5px;background:rgba(255,255,255,.05);flex-shrink:0}
.skel-bar{height:8px;border-radius:3px;background:rgba(255,255,255,.05)}
.skel-bar.sm{margin-left:auto;background:rgba(215,161,58,.10)}
.skel-spark{display:flex;align-items:flex-end;gap:5px;height:36px;margin:10px 0 4px}
.skel-spark span{flex:1;border-radius:2px 2px 0 0;background:rgba(255,255,255,.05)}
/* trust donut */
.donut{width:116px;height:116px;border-radius:50%;flex-shrink:0;display:flex;align-items:center;justify-content:center;background:conic-gradient(var(--wa-dis) 0 100%)}
.donut .hole{width:82px;height:82px;border-radius:50%;background:var(--wa-card);display:flex;flex-direction:column;align-items:center;justify-content:center}
.legend{display:flex;flex-direction:column;gap:9px;font-size:11.5px}
.legend .li{display:flex;align-items:center;gap:8px}
.legend .sw{width:9px;height:9px;border-radius:2px}
.legend .v{margin-left:auto;font-family:var(--font-mono);color:var(--wa-text)}
/* needs you (amber) */
.tile.amber{background:linear-gradient(180deg,rgba(215,161,58,.08),var(--wa-surface));border-color:rgba(215,161,58,.28);display:flex;flex-direction:column}
.amber .dot{width:7px;height:7px;border-radius:50%;background:var(--wa-warn)}
.btn-amber{margin-top:14px;width:100%;font-family:var(--font-body);font-weight:510;font-size:11px;padding:9px;border-radius:6px;border:none;background:var(--wa-warn);color:var(--wa-bg);cursor:pointer;transition:filter .12s}
.btn-amber:hover{filter:brightness(1.08)}
/* honest-degrade seam */
.seam{border:1px dashed rgba(215,161,58,.6)!important;background:repeating-linear-gradient(135deg,rgba(215,161,58,.05) 0 7px,transparent 7px 15px),var(--wa-sunk)!important;box-shadow:inset 0 2px 12px -4px rgba(0,0,0,.55)!important}
.seam-flag{display:inline-flex;align-items:center;gap:5px;font-family:var(--font-mono);font-size:10.5px;font-weight:600;text-transform:uppercase;letter-spacing:.1em;color:var(--wa-warn);border:1px dashed rgba(215,161,58,.7);border-radius:3px;padding:3px 8px;margin-top:10px}
.seam-note{font-size:11px;color:var(--wa-faint);line-height:1.4;margin-top:8px}
/* rows */
.rowhead{display:flex;align-items:center;gap:10px;margin-bottom:14px}
.rowhead .ln{flex:1;height:1px;background:var(--hair-2)}
.rowhead .cnt{font-family:var(--font-mono);font-size:10px;color:var(--wa-ok)}
.wkrow{display:flex;align-items:center;gap:13px;padding:10px 6px;border-bottom:1px solid var(--hair);transition:background .16s}
.wkrow:last-child{border-bottom:0}
.wkrow:hover{background:rgba(255,255,255,.025)}
.glyph-tile{width:38px;height:38px;border-radius:9px;background:var(--wa-raised);border:1px solid rgba(255,255,255,.1);display:flex;align-items:center;justify-content:center;padding:8px;flex-shrink:0}
.glyph-tile svg{width:22px;height:22px;display:block}
.mono-chip{display:inline-flex;align-items:center;justify-content:center;width:22px;height:22px;border-radius:3px;font-size:9px;font-weight:600;font-family:var(--font-mono);background:transparent;color:var(--wa-2)}
.rname{font-size:13.5px;font-weight:510;color:var(--wa-text);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.rmeta{font-family:var(--font-mono);font-size:10.5px;color:var(--wa-faint);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.state-stamp{font-family:var(--font-body);font-weight:510;text-transform:uppercase;letter-spacing:.06em;font-size:9px;padding:3px 8px;border-radius:3px;border:1px solid currentColor;flex-shrink:0}
.at-risk-badge{display:inline-block;font-family:var(--font-body);font-weight:600;text-transform:uppercase;letter-spacing:.06em;font-size:9px;padding:3px 8px;border-radius:3px;border:1px solid rgba(215,161,58,.55);background:rgba(215,161,58,.16);color:var(--wa-warn);flex-shrink:0}
.presence-pill{display:inline-block;font-family:var(--font-mono);font-size:10px;padding:2px 6px;border-radius:3px;border:1px solid var(--hair-3);color:var(--wa-muted);margin-left:4px}
/* evidence well */
.well{background:var(--wa-sunk);border:1px solid var(--hair-2);border-radius:12px;padding:16px 20px}
.evi-rows{display:flex;flex-direction:column;font-family:var(--font-mono);font-size:11.5px}
.evi-row{display:grid;grid-template-columns:74px 160px 1fr auto 74px;gap:14px;align-items:center;padding:5px 6px;border-radius:4px}
.evi-row:hover{background:rgba(255,255,255,.025)}
#evidence{font-family:var(--font-mono);font-size:11px;color:var(--wa-faint);white-space:pre-wrap;word-break:break-word;max-height:150px;overflow-y:auto;margin:8px 0 0}
.link{font-size:11px;color:var(--wa-accent);text-decoration:underline;cursor:pointer;background:none;border:none;font-family:var(--font-body)}
/* tables (timeline / connectors / fleet) */
.tabwrap{padding:18px 22px}
.thead{display:grid;gap:12px;padding:0 8px 8px;font-family:var(--font-body);font-weight:510;text-transform:uppercase;letter-spacing:.06em;font-size:9.5px;color:var(--wa-faint)}
.trow{display:grid;gap:12px;align-items:center;padding:9px 8px;border-radius:5px;border-bottom:1px solid rgba(255,255,255,.05);transition:background .16s}
.trow:hover{background:rgba(255,255,255,.025)}
.cols-conn{grid-template-columns:26px 1.5fr .7fr 1.3fr .8fr auto}
.cols-ev{grid-template-columns:74px 170px 1fr auto 74px}
.cols-fleet{grid-template-columns:1.4fr .8fr .7fr .6fr .6fr .6fr auto}
.dotc{width:7px;height:7px;border-radius:50%}
.trust-official{display:inline-block;font-family:var(--font-body);font-weight:510;font-size:9.5px;letter-spacing:.04em;padding:2px 7px;border-radius:3px;border:1px solid var(--wa-ok);color:var(--wa-ok)}
.trust-scanned{display:inline-block;font-family:var(--font-body);font-weight:510;font-size:9.5px;letter-spacing:.04em;padding:2px 7px;border-radius:3px;border:1px solid var(--wa-audit);color:var(--wa-audit)}
.trust-unverified{display:inline-block;font-family:var(--font-body);font-weight:510;font-size:9.5px;letter-spacing:.04em;padding:2px 7px;border-radius:3px;border:1px solid var(--wa-muted);color:var(--wa-muted)}
.count-pill{font-family:var(--font-mono);font-size:11px;color:var(--wa-faint);border:1px solid var(--hair-3);border-radius:99px;padding:1px 8px}
.kill-row-btn{font-family:var(--font-body);font-weight:510;font-size:10px;text-transform:uppercase;letter-spacing:.04em;padding:4px 10px;border-radius:3px;border:1px solid var(--wa-danger);color:var(--wa-danger);background:transparent;cursor:pointer;transition:all .14s}
.kill-row-btn:hover:not([disabled]){background:var(--wa-danger);color:var(--wa-bg)}
.kill-row-btn[disabled]{color:var(--wa-dis);border-color:var(--hair);cursor:not-allowed;opacity:.6}
/* connectors toolbar + registry */
.conn-tools{display:flex;gap:8px;align-items:center;margin-bottom:13px;flex-wrap:wrap}
input[type=search],input[type=text]{background:var(--wa-sunk);border:1px solid var(--hair-3);border-radius:6px;padding:6px 10px;color:var(--wa-text);font-size:12px;font-family:var(--font-body)}
input:focus{border-color:var(--wa-indigo);box-shadow:0 0 0 3px rgba(107,120,214,.32)}
::placeholder{color:var(--wa-faint)}
.fpill{background:transparent;border:1px solid var(--hair-3);border-radius:99px;padding:3px 11px;font-size:11px;font-weight:510;color:var(--wa-muted);cursor:pointer;transition:all .14s;font-family:var(--font-body)}
.fpill:hover{color:var(--wa-text);border-color:var(--wa-muted)}
.fpill.active{background:var(--wa-indigo);color:var(--wa-bg);border-color:var(--wa-indigo)}
.action-btn{font-family:var(--font-body);font-weight:510;font-size:11px;padding:6px 12px;border-radius:6px;border:none;background:var(--wa-indigo);color:var(--wa-bg);cursor:pointer;transition:filter .12s}
.action-btn:hover{filter:brightness(1.08)}
.toggle-btn{font-family:var(--font-body);font-weight:510;font-size:10px;padding:4px 10px;border-radius:3px;border:1px solid var(--hair-3);color:var(--wa-muted);background:transparent;cursor:pointer;transition:all .14s}
.toggle-btn:hover{color:var(--wa-text);border-color:var(--wa-indigo)}
.subhead{font-family:var(--font-body);font-weight:510;text-transform:uppercase;letter-spacing:.12em;font-size:11px;color:var(--wa-muted);margin:22px 0 12px}
/* status bar */
.statusbar{display:flex;align-items:center;gap:16px;padding:9px 22px;border-top:1px solid var(--hair);font-family:var(--font-mono);font-size:11px;color:var(--wa-faint);background:var(--wa-sunk)}
.statusbar .ok{color:var(--wa-ok)}
/* modal */
.scrim{position:fixed;inset:0;background:rgba(7,8,10,.72);display:flex;align-items:center;justify-content:center;z-index:50;animation:wkIn .16s ease}
.modal{width:560px;max-width:92vw;background:var(--wa-card);border:1px solid var(--hair-3);border-radius:12px;box-shadow:0 40px 90px -30px rgba(0,0,0,.85);overflow:hidden}
.modal-head{display:flex;align-items:center;gap:10px;padding:16px 20px;border-bottom:1px solid var(--hair-2)}
.modal-body{padding:18px 20px}
.x-btn{color:var(--wa-muted);font-size:16px;line-height:1;padding:2px 6px;background:none;border:none;cursor:pointer}
*::-webkit-scrollbar{width:9px;height:9px}*::-webkit-scrollbar-thumb{background:rgba(255,255,255,.1);border-radius:6px}*::-webkit-scrollbar-track{background:transparent}
@media (max-width:980px){.cols-fleet,.cols-conn,.cols-ev{font-size:10px}}
@media (max-width:900px){.side{width:60px}.brand-word,.nav-sec{opacity:0;pointer-events:none}.tab{justify-content:center;padding:9px 0}.tab .lbl,.tb-status .lbl,.kill-btn .lbl{opacity:0;width:0;overflow:hidden}.board{display:block;padding:16px 16px}.board>*{margin:0 0 14px}.wkrow{flex-wrap:wrap;row-gap:7px}.herodiv{display:none}.herorow{gap:20px}}
@media (max-width:640px){.board{display:block;padding:16px 12px}.board>*{margin:0 0 14px}.wrap{padding:16px 12px}.side{width:56px}.head{padding:11px 14px}.crumb .sub{display:none}}
@media (prefers-reduced-motion:reduce){*{animation:none!important;transition:none!important}}
</style></head><body>
<a class="sr-only sr-only-focusable" href="#main">Skip to main content</a>
<div class="wrap"><div class="shell"><div class="frame app" id="app">
<!-- sidebar -->
<aside class="side" aria-label="Sidebar">
<div class="side-top">
<svg class="brand-mark" viewBox="0 0 24 24" width="21" height="21" fill="none" stroke="#c4ccd6" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3.5 6 L7.5 18 L11 9.5"></path><path d="M13 9.5 L16.5 18 L20.5 6"></path></svg>
<span class="brand-word">WERK&nbsp;HUB</span>
</div>
<nav class="nav" role="tablist" aria-orientation="vertical" aria-label="Views" id="tablist">
<div class="nav-sec">Control plane</div>
<button class="tab active" role="tab" id="tab-board" data-tab="board" aria-selected="true" aria-controls="pane-board" aria-label="Board"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true"><rect x="3" y="3" width="7" height="9" rx="1.5"></rect><rect x="14" y="3" width="7" height="5" rx="1.5"></rect><rect x="14" y="12" width="7" height="9" rx="1.5"></rect><rect x="3" y="16" width="7" height="5" rx="1.5"></rect></svg><span class="lbl">Board</span></button>
<button class="tab" role="tab" id="tab-timeline" data-tab="timeline" aria-selected="false" aria-controls="pane-timeline" tabindex="-1" aria-label="Timeline"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true"><circle cx="12" cy="12" r="9"></circle><path d="M12 7v5l3.5 2"></path></svg><span class="lbl">Timeline</span></button>
<button class="tab" role="tab" id="tab-connectors" data-tab="connectors" aria-selected="false" aria-controls="pane-connectors" tabindex="-1" aria-label="Connectors"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M7 8V5a2 2 0 0 1 4 0v3M13 8V5a2 2 0 0 1 4 0v3M5 8h14v3a7 7 0 0 1-14 0zM12 18v3"></path></svg><span class="lbl">Connectors</span></button>
<button class="tab" role="tab" id="tab-registry" data-tab="registry" aria-selected="false" aria-controls="pane-registry" tabindex="-1" aria-label="Registry"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true"><ellipse cx="12" cy="6" rx="8" ry="3"></ellipse><path d="M4 6v12c0 1.7 3.6 3 8 3s8-1.3 8-3V6M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3"></path></svg><span class="lbl">Registry</span></button>
</nav>
<div class="side-foot">
<span class="tb-status" role="img" aria-label="Hub status: fail-closed"><span class="tb-dot wkbr"></span><span class="lbl">fail-closed</span></span>
<button class="kill-btn" id="kill-toggle" aria-expanded="false" aria-controls="kill-strip" aria-haspopup="dialog" aria-label="Kill switch"><svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" aria-hidden="true"><circle cx="12" cy="12" r="9"></circle><path d="M12 7.5v5.5"></path></svg><span class="lbl">Kill switch</span></button>
</div>
</aside>
<!-- main column -->
<div class="main">
<header class="head" role="banner">
<button class="burger" id="side-toggle" aria-label="Collapse sidebar" aria-expanded="true"><svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="M4 6h16M4 12h16M4 18h16"></path></svg></button>
<span class="crumb" id="crumb">Board<span class="sub">fleet &amp; evidence overview</span></span>
<span class="tb-spacer"></span>
<span class="tb-clock wk-clock" aria-hidden="true">--:--:--</span>
</header>
<!-- kill confirm strip -->
<div class="kill-strip" id="kill-strip" role="alertdialog" aria-label="Confirm halt all servers" hidden>
<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="#d7a13a" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M10.3 3.6 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.6a2 2 0 0 0-3.4 0Z"></path><path d="M12 9v4"></path><circle cx="12" cy="16.6" r="0.4" fill="#d7a13a"></circle></svg>
<div style="flex:1"><div class="title">Halt all downstream MCP servers?</div><div class="body">Gated. The kill switch requires <span class="flag">WERK_ALLOW_HUB_KILL=1</span>. <span id="kill-strip-state">Currently unset, so this action is blocked.</span></div></div>
<button class="strip-btn disabled" id="kill-confirm" disabled>Confirm halt</button>
<button class="strip-btn dismiss" id="kill-dismiss">Dismiss</button>
</div>
<main id="main" tabindex="-1">
<!-- ===== BOARD ===== -->
<section class="pane show" id="pane-board" role="tabpanel" aria-labelledby="tab-board" tabindex="0">
<div class="board" id="strip">
<!-- fleet hero band (connectors live + trust posture) -->
<div class="tile s12 hero"><span class="rule" style="background:var(--wa-ok)"></span>
<div class="herorow">
<div class="herofleet">
<div style="display:flex;align-items:center;justify-content:space-between"><span class="label">Connectors live</span><span class="mono" id="cl-trend" style="font-size:10px;color:var(--wa-faint)">live</span></div>
<div style="display:flex;align-items:flex-end;gap:7px;margin-top:10px"><span class="figure" id="cl-live" style="font-size:66px">0</span><span class="mono" id="cl-total" style="font-size:22px;color:var(--wa-muted);margin-bottom:8px">/ 0</span></div>
<div class="spark" id="cl-spark" aria-hidden="true"></div>
</div>
<div class="herodiv"></div>
<div class="herotrust">
<span class="label">Trust posture</span>
<div style="display:flex;align-items:center;gap:18px;margin-top:12px">
<div class="donut" id="trust-donut"><div class="hole"><span class="mono" id="trust-total" style="font-size:27px;font-weight:600">0</span><span style="font-size:9px;color:var(--wa-faint);text-transform:uppercase;letter-spacing:.06em">conn</span></div></div>
<div class="legend" id="trust-legend" aria-live="polite"></div>
</div>
</div>
</div>
</div>
<!-- needs you (seam: approvals API not wired) -->
<div class="tile amber s4 seam">
<div style="display:flex;align-items:center;gap:8px"><span class="dot"></span><span class="label" style="color:var(--wa-warn)">Needs you</span></div>
<span class="figure" id="needs-count" style="font-size:40px;margin:12px 0 4px;color:var(--wa-dis-2)">n/a</span>
<span class="seam-note">View pending approvals in the desktop app or <span class="mono">hub approvals list</span>.</span>
<span class="seam-flag">use desktop / CLI</span>
<button class="btn-amber" id="open-queue" aria-haspopup="dialog">Review queue</button>
</div>
<!-- agent runtimes (real) -->
<div class="tile s8" id="runtimes">
<div class="rowhead"><span class="label">Agent runtimes</span><span class="ln"></span><span class="cnt" id="rt-running">scanning</span><button class="chev" data-fold="runtimes" aria-expanded="true" aria-label="Collapse agent runtimes"><svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" aria-hidden="true"><path d="m6 9 6 6 6-6"></path></svg></button></div>
<div class="foldbody">
<div style="display:flex;flex-direction:column;gap:9px" id="runtimelist"></div>
<div class="seam-note" id="runtimes-note" aria-live="polite"></div>
</div>
</div>
<!-- model routing (seam: no routing endpoint) -->
<div class="tile s6 seam">
<div class="rowhead"><span class="label">Model routing</span><span class="ln"></span></div>
<div class="skel" aria-hidden="true"><div class="skel-row"><span class="skel-dot"></span><span class="skel-bar" style="width:42%"></span><span class="skel-bar sm" style="width:16%"></span></div><div class="skel-row"><span class="skel-dot"></span><span class="skel-bar" style="width:54%"></span><span class="skel-bar sm" style="width:12%"></span></div><div class="skel-row"><span class="skel-dot"></span><span class="skel-bar" style="width:34%"></span><span class="skel-bar sm" style="width:20%"></span></div></div>
<span class="seam-note">Provider routing (OpenRouter / DeepSeek / Ollama) is configured via worker manifests but not exposed on the dashboard API yet.</span>
<span class="seam-flag">not wired</span>
</div>
<!-- spend (seam: no spend endpoint) -->
<div class="tile s6 seam" style="display:flex;flex-direction:column">
<span class="label">Spend today</span>
<span class="figure" style="font-size:28px;margin:10px 0 2px;color:var(--wa-dis-2)">$ n/a</span>
<div class="skel-spark" aria-hidden="true"><span style="height:34%"></span><span style="height:52%"></span><span style="height:40%"></span><span style="height:66%"></span><span style="height:48%"></span><span style="height:72%"></span><span style="height:58%"></span></div>
<span class="seam-note">Cost ledger exists (<span class="mono">hub-cost.jsonl</span>) but no spend endpoint yet.</span>
<span class="seam-flag">not wired</span>
</div>
<!-- evidence ledger (real, SSE) -->
<div class="tile s12 well" id="ev-tile" style="background:var(--wa-sunk)">
<div class="rowhead" style="margin-bottom:11px"><span class="label">Evidence ledger</span><span class="dotc wkbr" style="background:var(--wa-ok)"></span><span class="mono" id="chain-state" style="font-size:10px;color:var(--wa-faint)">chain …</span><span class="ln"></span><button class="link" data-tab-jump="timeline">view all →</button><button class="chev" data-fold="ev-tile" aria-expanded="true" aria-label="Collapse evidence ledger"><svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" aria-hidden="true"><path d="m6 9 6 6 6-6"></path></svg></button></div>
<div class="foldbody"><div id="evidence" aria-live="polite"></div></div>
</div>
</div>
</section>
<!-- ===== TIMELINE ===== -->
<section class="pane" id="pane-timeline" role="tabpanel" aria-labelledby="tab-timeline" tabindex="0" hidden>
<div class="tabwrap">
<div class="rowhead"><span class="label">Evidence ledger</span><span class="dotc wkbr" style="background:var(--wa-ok)"></span><span class="mono" style="font-size:10px;color:var(--wa-faint)">hash-chain · 30s poll</span><span class="ln"></span><span class="mono" id="tl-count" style="font-size:10px;color:var(--wa-muted)">30s poll</span></div>
<div id="evidence-full" style="max-height:520px;overflow-y:auto;margin:0"></div>
</div>
</section>
<!-- ===== CONNECTORS ===== -->
<section class="pane" id="pane-connectors" role="tabpanel" aria-labelledby="tab-connectors" tabindex="0" hidden>
<div class="tabwrap">
<div class="rowhead"><span class="label">MCP connectors</span><span class="count-pill" id="conn-count">0</span><span class="ln"></span><span class="mono" style="font-size:10px;color:var(--wa-faint)">tier-1 allowlist · deny-by-default</span></div>
<div class="conn-tools">
<input id="conn-search" type="search" placeholder="Filter by id, transport, trust…" aria-label="Filter connectors">
<button class="fpill active" data-filter="all">All</button>
<button class="fpill" data-filter="enabled">Enabled</button>
<button class="fpill" data-filter="official">Official</button>
<button class="fpill" data-filter="unverified">Unverified</button>
</div>
<div role="table" aria-label="MCP connectors">
<div class="thead cols-conn" role="row"><span role="columnheader"></span><span role="columnheader">id</span><span role="columnheader">transport</span><span role="columnheader">trust</span><span role="columnheader">state</span><span role="columnheader" style="text-align:right">action</span></div>
<div id="connlist" role="rowgroup"></div>
</div>
<div id="conn-empty" class="seam-note" style="text-align:center;padding:18px" hidden>No connectors match the current filter.</div>
<div id="connnote" class="seam-note" aria-live="polite"></div>
<!-- running process fleet (real, supervise + kill) -->
<div class="subhead">Running processes</div>
<div role="table" aria-label="Running downstream processes">
<div class="thead cols-fleet" role="row"><span role="columnheader">server</span><span role="columnheader">profile</span><span role="columnheader">state</span><span role="columnheader">pid</span><span role="columnheader">idle</span><span role="columnheader">ram</span><span role="columnheader" style="text-align:right">action</span></div>
<div id="fleet" role="rowgroup"></div>
<div id="fleet-status" class="sr-only" aria-live="polite" aria-atomic="true"></div>
</div>
<!-- registry browse -->
<div class="subhead">Registry browse <span class="count-pill" style="margin-left:6px">WERK_ALLOW_HUB_REGISTRY=1</span></div>
<div class="conn-tools"><input id="reg-query" type="text" placeholder="search query (empty = list all)" aria-label="Registry search query" style="flex:1"><button class="action-btn" id="reg-search-btn">Search</button></div>
<div id="reg-status" class="seam-note"></div>
<div id="reg-results"></div>
<div class="seam-note">Add writes hub.json and takes effect on the next hub serve start, not live. Only installable (stdio) servers can be added.</div>
</div>
</section>
<!-- ===== REGISTRY ===== -->
<section class="pane" id="pane-registry" role="tabpanel" aria-labelledby="tab-registry" tabindex="0" hidden>
<div class="tabwrap">
<div class="rowhead"><span class="label">Capability Registry</span><span class="count-pill" id="reg-total">0</span><span class="ln"></span><span class="mono" style="font-size:10px;color:var(--wa-faint)">skills + tools · read-only · fail-closed</span></div>
<div class="conn-tools" id="reg-filter-bar">
<input id="reg-cat-filter" type="search" placeholder="Filter by category, id, maintainer…" aria-label="Filter registry capabilities" style="min-width:200px">
<button class="fpill active" data-reg-filter="all">All</button>
<button class="fpill" data-reg-filter="official">Official</button>
<button class="fpill" data-reg-filter="deluxe">Deluxe</button>
<button class="fpill" data-reg-filter="key-missing">Key missing</button>
<button class="fpill" data-reg-filter="key-present">Key present</button>
</div>
<div role="table" aria-label="Capability registry">
<div class="thead" style="grid-template-columns:1.6fr .6fr .8fr 1fr .7fr .5fr .5fr" role="row">
<span role="columnheader">id</span><span role="columnheader">kind</span><span role="columnheader">category</span><span role="columnheader">trust</span><span role="columnheader">maintenance</span><span role="columnheader">deluxe</span><span role="columnheader">keys</span></div>
<div id="reg-cap-list" role="rowgroup"></div>
</div>
<div id="reg-empty" class="seam-note" style="text-align:center;padding:18px" hidden>No capabilities match the current filter.</div>
<div id="reg-note" class="seam-note" aria-live="polite"></div>
</div>
</section>
<!-- status bar -->
<div class="statusbar">
<span class="ok">● relay ok</span>
<span id="sb-conn">0 / 0 connectors</span>
<span id="sb-rt">0 runtimes</span>
<span class="tb-spacer" style="flex:1"></span>
<span id="sb-kill">WERK_ALLOW_HUB_KILL=0</span>
<span class="wk-clock" style="color:var(--wa-muted)" aria-hidden="true">--:--:--</span>
</div>
</main>
</div></div></div></div>
<!-- approval queue modal (seam) -->
<div class="scrim" id="queue-scrim" hidden>
<div class="modal" role="dialog" aria-modal="true" aria-label="Approval queue">
<div class="modal-head"><span style="width:7px;height:7px;border-radius:50%;background:var(--wa-warn)"></span><span class="label" style="color:var(--wa-warn)">Approval queue</span><span class="tb-spacer" style="flex:1"></span><button class="x-btn" id="queue-close" aria-label="Close">x</button></div>
<div class="modal-body">
<div class="seam-note" style="line-height:1.6">Resolve pending approvals from the WERKHub desktop app (gated by <span class="mono">WERK_ALLOW_HUB_APPROVALS</span>) or the CLI:</div>
<pre class="mono" style="font-size:11.5px;color:var(--wa-2);background:var(--wa-sunk);border:1px solid var(--hair-2);border-radius:6px;padding:12px;margin-top:12px">hub approvals list
hub approvals approve &lt;request_id&gt;
hub approvals deny &lt;request_id&gt;</pre>
<span class="seam-flag">use desktop / CLI</span>
</div>
</div>
</div>
<script nonce="__WERK_NONCE__">
var _tab="board",_connFilter={text:"",pill:"all"},_connDebounce=null,_lastConn=[];
function esc(s){return String(s==null?"":s).replace(/[&<>"']/g,function(m){return {"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[m];});}
function _pct(n,d){return d>0?Math.round(n/d*100):0;}
function $(id){return document.getElementById(id);}
function _set(id,v){var e=$(id);if(e)e.textContent=v;}
/* colored host brand glyphs (faithful re-creations, operator-approved; swap official assets if licensed) */
var GLYPH={
claude:'<svg viewBox="0 0 24 24" fill="#D97757" aria-hidden="true"><path d="M12.0 2.2 L12.95 8.7 L15.1 3.05 L13.75 9.1 L17.55 4.35 L14.45 9.95 L19.65 6.45 L15.05 10.95 L21.8 9.4 L15.4 11.95 L21.8 12.0 L15.4 12.05 L21.8 14.6 L15.05 13.05 L19.65 17.55 L14.45 14.05 L17.55 19.65 L13.75 14.9 L15.1 20.95 L12.95 15.3 L12.0 21.8 L11.05 15.3 L8.9 20.95 L10.25 14.9 L6.45 19.65 L9.55 14.05 L4.35 17.55 L8.95 13.05 L2.2 14.6 L8.6 12.05 L2.2 12.0 L8.6 11.95 L2.2 9.4 L8.95 10.95 L4.35 6.45 L9.55 9.95 L6.45 4.35 L10.25 9.1 L8.9 3.05 L11.05 8.7 Z"></path></svg>',
codex:'<svg viewBox="0 0 24 24" fill="none" stroke="#ececec" stroke-width="1.45" aria-hidden="true"><ellipse cx="12" cy="12" rx="9.3" ry="3.85"></ellipse><ellipse cx="12" cy="12" rx="9.3" ry="3.85" transform="rotate(60 12 12)"></ellipse><ellipse cx="12" cy="12" rx="9.3" ry="3.85" transform="rotate(120 12 12)"></ellipse></svg>',
mcp:'<svg viewBox="0 0 24 24" fill="none" stroke="#e8ecf2" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><g transform="translate(12,12) scale(1.18) translate(-11.05,-12.7)"><path d="M3.3 14.1 L10.7 6.7 C11.9 5.5 13.7 5.5 14.8 6.7 C15.9 7.8 15.9 9.6 14.8 10.7 L8.7 16.8"></path><path d="M6.5 17.3 L13.9 9.9 C15.0 8.8 16.8 8.8 17.9 9.9 C19.1 11.0 19.1 12.9 17.9 14.0 L12.3 19.6"></path><path d="M11.0 8.0 L14.6 11.6" opacity="0.55"></path></g></svg>',
gemini:'<svg viewBox="0 0 24 24" aria-hidden="true"><defs><linearGradient id="wkGem" x1="2" y1="4" x2="22" y2="20" gradientUnits="userSpaceOnUse"><stop offset="0" stop-color="#4285F4"></stop><stop offset="0.5" stop-color="#9168C0"></stop><stop offset="1" stop-color="#D96570"></stop></linearGradient></defs><path fill="url(#wkGem)" d="M12 1.6 C12.4 6.9 17.1 11.6 22.4 12 C17.1 12.4 12.4 17.1 12 22.4 C11.6 17.1 6.9 12.4 1.6 12 C6.9 11.6 11.6 6.9 12 1.6 Z"></path></svg>',
deepseek:'<svg viewBox="0 0 24 24" fill="none" stroke="#4D6BFE" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><g transform="translate(0.15,2.51)"><path d="M21.4 6.4 C20.1 7.6 19.4 8.7 19.1 10.0 C18.7 8.9 18.0 8.2 17.0 7.9"></path><path d="M19.0 10.1 C17.7 13.9 14.3 16.4 10.2 16.4 C6.6 16.4 3.4 14.3 2.3 11.0 C3.9 12.9 6.2 14.1 8.8 14.1 C10.8 14.1 12.4 13.3 12.4 11.9 C12.4 10.9 11.6 10.3 10.3 10.0 C7.9 9.5 6.0 8.4 6.0 6.2 C6.0 4.0 8.0 2.4 10.6 2.6 C13.6 2.8 15.6 5.0 19.0 10.1 Z"></path><circle cx="8.0" cy="6.3" r="0.9" fill="#4D6BFE" stroke="none"></circle></g></svg>',
ollama:'<svg viewBox="0 0 24 24" fill="#e8ecf2" aria-hidden="true"><path transform="translate(12,12) scale(1.25) translate(-12,-12.4)" d="M8.0 9.0 C8.0 6.6 8.7 4.9 9.7 4.9 C10.2 4.9 10.6 5.3 10.9 6.1 C11.2 6.0 11.6 6.0 12.0 6.0 C12.4 6.0 12.8 6.0 13.1 6.1 C13.4 5.3 13.8 4.9 14.3 4.9 C15.3 4.9 16.0 6.6 16.0 9.0 L16.0 10.4 C16.6 11.0 17.0 11.9 17.0 13.0 L17.0 17.4 C17.0 18.8 16.3 19.9 15.4 19.9 C14.8 19.9 14.4 19.4 14.4 18.6 L14.4 17.2 C14.4 16.6 14.0 16.2 13.4 16.2 L10.6 16.2 C10.0 16.2 9.6 16.6 9.6 17.2 L9.6 18.6 C9.6 19.4 9.2 19.9 8.6 19.9 C7.7 19.9 7.0 18.8 7.0 17.4 L7.0 13.0 C7.0 11.9 7.4 11.0 8.0 10.4 Z M9.6 9.2 C9.2 9.2 8.9 9.6 8.9 10.1 C8.9 10.6 9.2 11.0 9.6 11.0 C10.0 11.0 10.3 10.6 10.3 10.1 C10.3 9.6 10.0 9.2 9.6 9.2 Z M14.4 9.2 C14.0 9.2 13.7 9.6 13.7 10.1 C13.7 10.6 14.0 11.0 14.4 11.0 C14.8 11.0 15.1 10.6 15.1 10.1 C15.1 9.6 14.8 9.2 14.4 9.2 Z"></path></svg>',
openrouter:'<svg viewBox="0 0 24 24" fill="none" stroke="#9aa6c4" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><g transform="translate(12,12) scale(1.18) translate(-9.25,-12)"><path d="M2.6 12 H7.4 C10.6 12 11.4 7.4 14.8 7.4"></path><path d="M7.4 12 C10.6 12 11.4 16.6 14.8 16.6"></path><path d="M13.4 5.1 L16.8 7.4 L13.4 9.7" fill="#9aa6c4" stroke="none"></path><path d="M13.4 14.3 L16.8 16.6 L13.4 18.9" fill="#9aa6c4" stroke="none"></path><circle cx="3.0" cy="12" r="1.3" fill="#9aa6c4" stroke="none"></circle></g></svg>'};
function _glyphFor(hostId){
 var h=String(hostId||"").toLowerCase();
 if(h.indexOf("claude")>=0)return GLYPH.claude;
 if(h.indexOf("codex")>=0)return GLYPH.codex;
 if(h.indexOf("gemini")>=0)return GLYPH.gemini;
 if(h.indexOf("deepseek")>=0)return GLYPH.deepseek;
 if(h.indexOf("ollama")>=0)return GLYPH.ollama;
 if(h.indexOf("openrouter")>=0)return GLYPH.openrouter;
 if(h.indexOf("mcp")>=0)return GLYPH.mcp;
 return null;}
function _glyphTile(hostId,mono){
 var g=_glyphFor(hostId);
 var inner=g?g:'<span class="mono-chip">'+esc((mono||hostId||"?").slice(0,2).toUpperCase())+'</span>';
 return '<div class="glyph-tile">'+inner+'</div>';}
/* ---- tabs ---- */
function _showTab(t){_tab=t;
 ["board","timeline","connectors","registry"].forEach(function(n){
  var p=$("pane-"+n);if(p){p.hidden=(n!==t);p.classList.toggle("show",n===t);}});
 document.querySelectorAll(".tab").forEach(function(b){var on=b.dataset.tab===t;b.classList.toggle("active",on);b.setAttribute("aria-selected",on?"true":"false");b.tabIndex=on?0:-1;});
 var _cr=$("crumb"),_CB={board:["Board","fleet & evidence overview"],timeline:["Timeline","hash-chained event log"],connectors:["Connectors","MCP servers & processes"],registry:["Registry","skills & tools catalog"]};
 if(_cr&&_CB[t])_cr.innerHTML=esc(_CB[t][0])+'<span class="sub">'+esc(_CB[t][1])+'</span>';}
document.querySelectorAll(".tab").forEach(function(b){b.addEventListener("click",function(){_showTab(b.dataset.tab);});});
(function(){var tl=$("tablist");if(!tl)return;tl.addEventListener("keydown",function(e){
 var tabs=[].slice.call(tl.querySelectorAll(".tab"));var i=tabs.indexOf(document.activeElement);if(i<0)return;var j=i;
 if(e.key==="ArrowRight"||e.key==="ArrowDown")j=(i+1)%tabs.length;
 else if(e.key==="ArrowLeft"||e.key==="ArrowUp")j=(i+tabs.length-1)%tabs.length;
 else if(e.key==="Home")j=0;else if(e.key==="End")j=tabs.length-1;else return;
 e.preventDefault();tabs[j].focus();_showTab(tabs[j].dataset.tab);});})();
/* ---- sidebar collapse + panel folds (persisted) ---- */
(function(){var app=$("app"),tg=$("side-toggle");if(app&&tg){
 var setCol=function(c){app.classList.toggle("col",c);tg.setAttribute("aria-expanded",c?"false":"true");tg.setAttribute("aria-label",c?"Expand sidebar":"Collapse sidebar");try{localStorage.setItem("wk_side",c?"1":"0");}catch(e){}};
 tg.addEventListener("click",function(){setCol(!app.classList.contains("col"));});
 try{if(localStorage.getItem("wk_side")==="1")setCol(true);}catch(e){}}
 document.querySelectorAll(".chev[data-fold]").forEach(function(c){var id=c.getAttribute("data-fold"),t=$(id);if(!t)return;
  var setFold=function(f){t.classList.toggle("folded",f);c.setAttribute("aria-expanded",f?"false":"true");c.setAttribute("aria-label",(f?"Expand":"Collapse")+(c.getAttribute("aria-label")||"").replace(/^\w+/,""));try{localStorage.setItem("wk_fold_"+id,f?"1":"0");}catch(e){}};
  c.addEventListener("click",function(){setFold(!t.classList.contains("folded"));});
  try{if(localStorage.getItem("wk_fold_"+id)==="1")setFold(true);}catch(e){}});})();
/* ---- clock ---- */
function _tick(){var d=new Date();var p=function(n){return String(n).padStart(2,"0");}
 var t=p(d.getHours())+":"+p(d.getMinutes())+":"+p(d.getSeconds());
 document.querySelectorAll(".wk-clock").forEach(function(n){n.textContent=t;});}
/* ---- kill strip ---- */
function _openStrip(){var s=$("kill-strip");s.hidden=false;$("kill-toggle").setAttribute("aria-expanded","true");var f=s.querySelector("button:not([disabled])");if(f)f.focus();}
function _closeStrip(){$("kill-strip").hidden=true;$("kill-toggle").setAttribute("aria-expanded","false");$("kill-toggle").focus();}
$("kill-toggle").addEventListener("click",function(){if($("kill-strip").hidden)_openStrip();else _closeStrip();});
$("kill-dismiss").addEventListener("click",_closeStrip);
/* ---- queue modal (seam) ---- */
function _openQueue(){$("queue-scrim").hidden=false;var c=$("queue-close");if(c)c.focus();}
function _closeQueue(){$("queue-scrim").hidden=true;var o=$("open-queue");if(o)o.focus();}
$("open-queue").addEventListener("click",_openQueue);
$("queue-close").addEventListener("click",_closeQueue);
$("queue-scrim").addEventListener("click",function(e){if(e.target===this)_closeQueue();});
$("queue-scrim").addEventListener("keydown",function(e){if(e.key==="Tab"){e.preventDefault();var c=$("queue-close");if(c)c.focus();}});
document.addEventListener("keydown",function(e){if(e.key!=="Escape")return;
 if(!$("queue-scrim").hidden)_closeQueue();else if(!$("kill-strip").hidden)_closeStrip();});
document.querySelectorAll("[data-tab-jump]").forEach(function(b){b.addEventListener("click",function(){_showTab(b.dataset.tabJump);});});
/* ---- trust badge ---- */
function _trustBadge(t){
 if(t==="Official")return '<span class="trust-official">Official</span>';
 if(t==="Security-Scanned")return '<span class="trust-scanned">Security-Scanned</span>';
 return '<span class="trust-unverified">Community-Unverified</span>';}
function _stateTone(st){st=String(st||"").toLowerCase();
 if(st==="live"||st==="running")return "var(--wa-ok)";
 if(st==="idle")return "var(--wa-faint)";
 if(st==="gated")return "var(--wa-warn)";
 if(st==="dead")return "var(--wa-danger)";return "var(--wa-muted)";}
/* ---- connectors ---- */
function _renderTrust(list){
 var off=0,scan=0,unv=0;
 list.forEach(function(c){var t=c.trust_tier||"Community-Unverified";if(t==="Official")off++;else if(t==="Security-Scanned")scan++;else unv++;});
 var total=list.length;_set("trust-total",total);
 var po=_pct(off,total),ps=_pct(scan,total);
 var d=$("trust-donut");if(d)d.style.background="conic-gradient(var(--wa-ok) 0 "+po+"%,var(--wa-audit) 0 "+(po+ps)+"%,var(--wa-dis-2) 0 100%)";
 var lg=$("trust-legend");if(lg)lg.innerHTML=
  '<div class="li"><span class="sw" style="background:var(--wa-ok)"></span><span style="color:var(--wa-2)">Official</span><span class="v">'+off+'</span></div>'+
  '<div class="li"><span class="sw" style="background:var(--wa-audit)"></span><span style="color:var(--wa-2)">Scanned</span><span class="v">'+scan+'</span></div>'+
  '<div class="li"><span class="sw" style="background:var(--wa-dis-2)"></span><span style="color:var(--wa-2)">Unverified</span><span class="v">'+unv+'</span></div>';}
function _renderConnLive(list){
 var on=list.filter(function(c){return c.enabled!==false;}).length,total=list.length;
 _set("cl-live",on);_set("cl-total","/ "+total);
 _set("conn-count",total);_set("sb-conn",on+" / "+total+" connectors");
 var sp=$("cl-spark");if(sp)sp.innerHTML=list.map(function(c){var lv=c.enabled!==false;return '<span style="height:'+(lv?100:34)+'%;background:'+(lv?"var(--wa-ok)":"var(--wa-dis)")+'"></span>';}).join("")||'<span style="height:30%;background:var(--wa-dis)"></span>';}
function _renderConnectors(list){
 _lastConn=list;var txt=_connFilter.text.toLowerCase(),pill=_connFilter.pill;
 var f=list.filter(function(c){
  if(pill==="enabled"&&c.enabled===false)return false;
  if(pill==="official"&&(c.trust_tier||"")!=="Official")return false;
  if(pill==="unverified"&&(c.trust_tier||"")==="Official")return false;
  if(txt){var hay=(c.id+"|"+(c.transport||"stdio")+"|"+(c.trust_tier||"")).toLowerCase();if(hay.indexOf(txt)<0)return false;}
  return true;});
 var empty=$("conn-empty");
 if(f.length===0){$("connlist").innerHTML="";empty.hidden=false;return;}
 empty.hidden=true;
 $("connlist").innerHTML=f.map(function(c,i){
  var on=c.enabled!==false,st=on?"live":"off",tone=_stateTone(st);
  var notch="";
  return '<div class="trow cols-conn" role="row" style="'+notch+'">'+
   '<span class="dotc" role="cell" style="background:'+tone+'"></span>'+
   '<span class="mono" role="cell" style="font-size:13px;color:var(--wa-text)">'+esc(c.id)+'</span>'+
   '<span class="mono" role="cell" style="font-size:11px;color:var(--wa-muted)">'+esc(c.transport||"stdio")+'</span>'+
   '<span role="cell">'+_trustBadge(c.trust_tier||"Community-Unverified")+'</span>'+
   '<span class="mono" role="cell" style="font-size:11px;color:'+tone+'">'+st+'</span>'+
   '<span role="cell" style="text-align:right"><button class="toggle-btn" data-conn-id="'+esc(c.id)+'" aria-label="'+(on?"Disable":"Enable")+' '+esc(c.id)+'">'+(on?"Disable":"Enable")+'</button></span></div>';}).join("");
 _set("connnote","Toggle writes hub.json (needs WERK_ALLOW_HUB_CONFIG_WRITE); takes effect on next hub serve start.");}
async function loadConnectors(){
 try{var r=await fetch("/api/connectors");var list=await r.json();
 _renderConnLive(list);_renderTrust(list);_renderConnectors(list);}catch(e){_set("connnote","Connectors unreachable: "+e.message);}}
$("conn-search").addEventListener("input",function(e){clearTimeout(_connDebounce);var v=e.target.value;
 _connDebounce=setTimeout(function(){_connFilter.text=v;_renderConnectors(_lastConn);},160);});
document.querySelectorAll("#pane-connectors .fpill").forEach(function(b){b.addEventListener("click",function(){
 document.querySelectorAll("#pane-connectors .fpill").forEach(function(x){x.classList.remove("active");});
 b.classList.add("active");_connFilter.pill=b.dataset.filter||"all";_renderConnectors(_lastConn);});});
/* ---- fleet + status ---- */
async function refresh(){
 try{var r=await fetch("/api/status");var s=await r.json();}catch(e){return;}
 var chainOk=s.chain_verified!==false;
 _set("chain-state",chainOk?"hash-chain verified":"chain ERROR");
 var killOn=(typeof s.kill_allowed!=="undefined")?s.kill_allowed:false;
 _set("sb-kill","WERK_ALLOW_HUB_KILL="+(killOn?"1":"0"));
 _set("kill-strip-state",killOn?"Flag is set. Halting is permitted.":"Currently unset, so this action is blocked.");
 var cf=$("kill-confirm");if(cf){cf.disabled=!killOn;cf.classList.toggle("disabled",!killOn);}
 var procs=s.processes||[];
 var fleetStatus=$("fleet-status");if(fleetStatus){var running=procs.filter(function(p){return p.state==="running"||p.state==="live";}).length;fleetStatus.textContent=procs.length+" processes, "+running+" running";}
 $("fleet").innerHTML=procs.length===0?'<div class="seam-note" style="padding:12px 8px">No tracked downstream processes.</div>':procs.map(function(p){
  var tone=_stateTone(p.state);
  return '<div class="trow cols-fleet" role="row">'+
   '<span class="mono" role="cell" style="color:var(--wa-text)">'+esc(p.server_id)+'</span>'+
   '<span role="cell" style="color:var(--wa-muted)">'+esc(p.profile_id)+'</span>'+
   '<span class="mono" role="cell" style="color:'+tone+'">'+esc(p.state)+'</span>'+
   '<span class="mono" role="cell">'+(p.pid||"-")+'</span>'+
   '<span class="mono" role="cell">'+(p.idle_seconds!=null?p.idle_seconds+"s":"-")+'</span>'+
   '<span class="mono" role="cell">'+(p.ram_bytes!=null?Math.round(p.ram_bytes/1024)+"K":"-")+'</span>'+
   '<span role="cell" style="text-align:right"><button class="kill-row-btn" '+(p.killable&&p.pid!=null?"":"disabled")+' data-kill-pid="'+(p.pid!=null?esc(p.pid):"")+'" data-kill-sid="'+esc(p.server_id)+'" aria-label="Kill '+esc(p.server_id)+'">Kill</button></span></div>';}).join("");}
/* ---- runtimes (real, colored glyphs) ---- */
async function loadRuntimes(){
 try{var r=await fetch("/api/runtimes");if(!r.ok){_set("runtimes-note","Runtimes unavailable ("+r.status+")");return;}
 var d=await r.json();var probes=d.probes||[];
 var running=probes.filter(function(p){return p.detected;}).length;
 _set("rt-running",running+" detected");_set("sb-rt",running+" runtimes");
 $("runtimelist").innerHTML=probes.map(function(p){
  var detected=p.detected,tone=detected?"var(--wa-ok)":"var(--wa-faint)",label=detected?"detected":"missing";
  var meta="";if(p.token_env_present)meta+="token-env ";if(p.token_file_present)meta+="token-file ";
  if(!meta)meta=esc(p.host_id);
  var rid="risk-"+esc(p.host_id);
  var risk=p.at_risk?' <span class="at-risk-badge" aria-describedby="'+rid+'">at-risk</span><span id="'+rid+'" class="sr-only">'+esc(p.at_risk_reason)+'</span>':"";
  return '<div class="wkrow"'+(p.at_risk?' style="border-left:2px solid var(--wa-warn)"':'')+'>'+_glyphTile(p.host_id,p.monogram)+
   '<div style="flex:1;min-width:0"><div class="rname">'+esc(p.display_name||p.host_id)+'</div><div class="rmeta">'+esc(meta.trim())+'</div></div>'+
   '<span class="state-stamp" style="color:'+tone+'">'+label+'</span>'+risk+'</div>';}).join("");
 _set("runtimes-note","Detected: "+((d.detected||[]).join(", ")||"none"));}
 catch(e){_set("runtimes-note","Runtimes probe failed: "+e.message);}}
/* ---- registry ---- */
const WERK_TOKEN="__WERK_SESSION_TOKEN__";
async function kill(pid,sid){try{var r=await fetch("/api/kill",{method:"POST",headers:{"X-Werk-Token":WERK_TOKEN},body:JSON.stringify({pid:pid,server_id:sid})});if(!r.ok){var d=await r.json().catch(function(){return{};});alert("Kill failed ("+r.status+"): "+(d.error||"unknown"));}}catch(e){alert("Kill error: "+e.message);}refresh();}
async function toggleConn(id){try{var r=await fetch("/api/connectors/toggle",{method:"POST",headers:{"X-Werk-Token":WERK_TOKEN},body:JSON.stringify({id:id})});if(!r.ok){var d=await r.json().catch(function(){return{};});alert("Toggle failed ("+r.status+"): "+(d.error||"unknown"));}}catch(e){alert("Toggle error: "+e.message);}loadConnectors();}
async function regSearch(){var q=$("reg-query").value,st=$("reg-status");st.textContent="Searching…";$("reg-results").innerHTML="";
 try{var r=await fetch("/api/registry/search",{method:"POST",headers:{"X-Werk-Token":WERK_TOKEN,"Content-Type":"application/json"},body:JSON.stringify({query:q})});
 var d=await r.json();if(!r.ok){st.textContent="Error: "+esc(d.error||r.status);return;}
 window._regCands=d.candidates||[];var w=d.warnings||[];
 st.textContent=window._regCands.length+" result(s)"+(w.length?" ("+w.length+" warning(s))":"");
 $("reg-results").innerHTML=window._regCands.length===0?'<div class="seam-note" style="padding:4px 8px">No results.</div>':
  '<div class="thead cols-conn" style="grid-template-columns:1.6fr 1fr 2fr auto"><span>id</span><span>name</span><span>description</span><span></span></div>'+
  window._regCands.map(function(c,i){return '<div class="trow" style="grid-template-columns:1.6fr 1fr 2fr auto"><span class="mono" style="color:var(--wa-text)">'+esc(c.id)+'</span><span>'+esc(c.name)+'</span><span style="color:var(--wa-muted)">'+esc(c.description)+'</span><span style="text-align:right">'+
  (c.installable?'<button class="action-btn" data-add-idx="'+i+'">Add</button>':'<span class="seam-note">not installable</span>')+'</span></div>';}).join("");
 }catch(e){st.textContent="Failed: "+e.message;}}
async function addConn(i){var c=(window._regCands||[])[i];if(!c)return;
 try{var r=await fetch("/api/connectors/add",{method:"POST",headers:{"X-Werk-Token":WERK_TOKEN,"Content-Type":"application/json"},body:JSON.stringify({id:c.id})});
 var d=await r.json();if(!r.ok){alert("Add failed ("+r.status+"): "+(d.error||"unknown"));return;}
 alert("Added "+c.id+". Restart hub serve to connect.");loadConnectors();}catch(e){alert("Add error: "+e.message);}}
$("reg-search-btn").addEventListener("click",regSearch);
/* delegated, XSS-safe: ids/pids read from data-* (raw), never interpolated into inline onclick */
document.addEventListener("click",function(e){var t=e.target;if(!t||!t.dataset)return;if(t.disabled)return;
 if(t.dataset.connId!==undefined)toggleConn(t.dataset.connId);
 else if(t.dataset.killPid!==undefined)kill(parseInt(t.dataset.killPid,10),t.dataset.killSid||"");
 else if(t.dataset.addIdx!==undefined)addConn(parseInt(t.dataset.addIdx,10));});
/* ---- registry catalog ---- */
var _lastReg=[],_regFilter={text:"",pill:"all"},_regDebounce=null;
function _keysBadge(cap){
 var names=cap.needs_keys||[];var present=cap.keys_present;
 if(!names.length)return '<span class="presence-pill" style="color:var(--wa-faint)">no keys</span>';
 if(present)return '<span class="presence-pill" style="color:var(--wa-ok)">key set</span>';
 return '<span class="at-risk-badge" style="font-size:9px">key missing</span>';}
function _renderRegistry(list){
 _lastReg=list;var txt=_regFilter.text.toLowerCase(),pill=_regFilter.pill;
 var f=list.filter(function(c){
  if(pill==="official"&&c.trust_tier!=="Official")return false;
  if(pill==="deluxe"&&!c.deluxe_base)return false;
  if(pill==="key-missing"&&(c.keys_present||!c.needs_keys||!c.needs_keys.length))return false;
  if(pill==="key-present"&&!c.keys_present)return false;
  if(txt){var hay=(c.id+"|"+(c.category||"")+"|"+(c.trust_tier||"")+"|"+(c.maintainer||"")).toLowerCase();if(hay.indexOf(txt)<0)return false;}
  return true;});
 var empty=$("reg-empty");if(!empty)return;
 if(f.length===0){$("reg-cap-list").innerHTML="";empty.hidden=false;return;}
 empty.hidden=true;
 $("reg-cap-list").innerHTML=f.map(function(c){
  var deluxeMark=c.deluxe_base?'<span class="trust-official" style="border-color:var(--wa-indigo);color:var(--wa-indigo)">Deluxe</span>':'';
  return '<div class="trow" style="grid-template-columns:1.6fr .6fr .8fr 1fr .7fr .5fr .5fr" role="row">'+
   '<span class="mono" role="cell" style="font-size:12px;color:var(--wa-text)">'+esc(c.id)+'</span>'+
   '<span class="mono" role="cell" style="font-size:11px;color:var(--wa-muted)">'+esc(c.kind||"tool")+'</span>'+
   '<span role="cell" style="font-size:11px;color:var(--wa-2)">'+esc(c.category||"other")+'</span>'+
   '<span role="cell">'+_trustBadge(c.trust_tier||"Community-Unverified")+'</span>'+
   '<span class="mono" role="cell" style="font-size:10.5px;color:var(--wa-faint)">'+esc(c.maintenance||"unknown")+'</span>'+
   '<span role="cell">'+deluxeMark+'</span>'+
   '<span role="cell">'+_keysBadge(c)+'</span></div>';}).join("");}
async function loadRegistry(){
 var note=$("reg-note");
 try{var r=await fetch("/api/registry");
 if(!r.ok){if(note)note.textContent="Registry unavailable ("+r.status+")";return;}
 var d=await r.json();var caps=d.capabilities||[];
 _set("reg-total",caps.length);
 _renderRegistry(caps);
 if(note)note.textContent="";}
 catch(e){if(note)note.textContent="Registry unavailable: "+e.message;}}
$("reg-cat-filter").addEventListener("input",function(e){clearTimeout(_regDebounce);var v=e.target.value;
 _regDebounce=setTimeout(function(){_regFilter.text=v;_renderRegistry(_lastReg);},160);});
document.querySelectorAll("#pane-registry .fpill").forEach(function(b){b.addEventListener("click",function(){
 document.querySelectorAll("#pane-registry .fpill").forEach(function(x){x.classList.remove("active");});
 b.classList.add("active");_regFilter.pill=b.dataset.regFilter||"all";_renderRegistry(_lastReg);});});
/* evidence (SSE) */
function _evTone(t){t=String(t||"");
 if(/deny|denied|fail|error|block/.test(t))return "var(--wa-danger)";
 if(/downgrade/.test(t))return "var(--wa-audit)";
 if(/approval|grant/.test(t))return "var(--wa-warn)";
 if(/worker|dispatch|spawn|probed|staged/.test(t))return "var(--wa-muted)";
 if(/complete|consumed|approved|call|ok/.test(t))return "var(--wa-ok)";
 return "var(--wa-2)";}
function _evRow(ev){if(ev==null||typeof ev!=="object")return "";
 var p=(ev.payload&&typeof ev.payload==="object")?ev.payload:ev;
 var kind=p.type||ev.type||"event";
 var ts=String(ev.ts||"");var tm=ts.indexOf("T")>=0?ts.slice(11,19):ts.slice(0,8);
 var det=[];Object.keys(p).forEach(function(k){if(k!=="type")det.push(k+"="+p[k]);});
 var detail=det.join(" ")||"-";var sha=String(ev.hash||"").slice(0,7);var eid=String(ev.event_id||"");
 return '<div class="evi-row"><span style="color:var(--wa-faint)">'+esc(tm)+'</span>'+
  '<span style="color:'+_evTone(kind)+'">'+esc(kind)+'</span>'+
  '<span style="color:var(--wa-2)">'+esc(detail)+'</span>'+
  '<span style="color:var(--wa-faint);font-size:10.5px">'+esc(eid)+'</span>'+
  '<span style="color:var(--wa-dis-2);text-align:right">'+esc(sha)+'</span></div>';}
function _renderLedger(arr,limit){if(!Array.isArray(arr))return "";var a=arr.slice().reverse();if(limit)a=a.slice(0,limit);return a.map(_evRow).join("");}
function _onLedger(e){var arr;try{arr=JSON.parse(e.data);}catch(_e){arr=null;}
 var ev=$("evidence"),ef=$("evidence-full");
 var dot=document.querySelector(".tb-dot");if(dot)dot.style.background="var(--wa-ok)";
 if(!Array.isArray(arr)){if(ev)ev.textContent=e.data;if(ef)ef.textContent=e.data;return;}
 if(ev)ev.innerHTML='<div class="evi-rows">'+_renderLedger(arr,6)+'</div>';
 if(ef)ef.innerHTML='<div class="evi-rows">'+_renderLedger(arr)+'</div>';}
/* server emits a NAMED 'ledger' SSE event; addEventListener catches it (onmessage only fires for unnamed events) */
var es=new EventSource("/api/events");es.addEventListener("ledger",_onLedger);es.onmessage=_onLedger;
es.onerror=function(){_set("chain-state","SSE disconnected");var dot=document.querySelector(".tb-dot");if(dot)dot.style.background="var(--wa-warn)";};
_tick();setInterval(_tick,1000);
refresh();loadConnectors();loadRuntimes();loadRegistry();setInterval(function(){refresh();loadConnectors();loadRuntimes();},2000);
</script></body></html>"""

# Self-hosted brand fonts (zero-external): inject @font-face data: URIs once at import.
_FONT_FACE_CSS = (
    "@font-face{font-family:'Space Grotesk';font-style:normal;font-weight:600;font-display:swap;"
    "src:url(data:font/woff2;base64," + SPACE_GROTESK_600 + ") format('woff2')}"
    "@font-face{font-family:'IBM Plex Mono';font-style:normal;font-weight:400;font-display:swap;"
    "src:url(data:font/woff2;base64," + IBM_PLEX_MONO_400 + ") format('woff2')}"
    "@font-face{font-family:'IBM Plex Mono';font-style:normal;font-weight:600;font-display:swap;"
    "src:url(data:font/woff2;base64," + IBM_PLEX_MONO_600 + ") format('woff2')}"
)
DASHBOARD_HTML = DASHBOARD_HTML.replace("__WERK_FONTS__", _FONT_FACE_CSS)

@dataclass(frozen=True)
class ProcessRow:
    server_id: str
    profile_id: str
    state: str
    pid: int | None
    idle_seconds: float | None
    ram_bytes: int | None
    killable: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "server_id": self.server_id,
            "profile_id": self.profile_id,
            "state": self.state,
            "pid": self.pid,
            "idle_seconds": self.idle_seconds,
            "ram_bytes": self.ram_bytes,
            "killable": self.killable,
        }


@dataclass(frozen=True)
class DashboardSnapshot:
    hub_name: str
    generated_at: str
    total_processes: int
    reclaimable_ram_bytes: int
    processes: tuple[ProcessRow, ...]
    recent_events: tuple[dict[str, Any], ...]
    chain_verified: bool
    chain_errors: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "hub_name": self.hub_name,
            "generated_at": self.generated_at,
            "total_processes": self.total_processes,
            "reclaimable_ram_bytes": self.reclaimable_ram_bytes,
            "processes": [p.to_dict() for p in self.processes],
            "recent_events": list(self.recent_events),
            "chain_verified": self.chain_verified,
            "chain_errors": self.chain_errors,
            "kill_allowed": kill_allowed(),
        }


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def kill_allowed() -> bool:
    return os.environ.get("WERK_ALLOW_HUB_KILL") == "1"


def config_write_allowed() -> bool:
    """Whether the dashboard may write hub.json (toggle/remove connectors).

    Fail-closed: only the explicit env opt-in enables it; absent => no write.
    """
    return os.environ.get("WERK_ALLOW_HUB_CONFIG_WRITE") == "1"


def registry_browse_allowed() -> bool:
    """Whether the dashboard may make outbound calls to the MCP registry.

    Fail-closed: off by default so a localhost page cannot trigger outbound
    network calls without explicit operator consent.
    """
    return os.environ.get("WERK_ALLOW_HUB_REGISTRY") == "1"


def approvals_allowed() -> bool:
    """Whether the dashboard may resolve approval requests (approve/deny).

    Fail-closed: off by default; explicit opt-in required.
    """
    return os.environ.get("WERK_ALLOW_HUB_APPROVALS") == "1"


def onboard_allowed() -> bool:
    """Whether the dashboard may adopt discovered MCP servers into hub.json.

    Fail-closed: off by default; explicit opt-in required (it writes config).
    """
    return os.environ.get("WERK_ALLOW_HUB_ONBOARD") == "1"


def _approvals_dir(ledger_path: str | Path) -> Path:
    """Return the hub-approvals dir the CLI uses (sibling of the ledger)."""
    return Path(ledger_path).parent / "hub-approvals"


def stub_processes(config: HubConfig) -> list[ProcessRow]:
    rows: list[ProcessRow] = []
    for server in config.servers:
        if not server.enabled:
            continue
        rows.append(
            ProcessRow(
                server_id=server.id,
                profile_id=config.default_profile,
                state="unknown",
                pid=None,
                idle_seconds=None,
                ram_bytes=None,
                killable=False,
            )
        )
    return rows


def build_snapshot(
    config: HubConfig,
    ledger_path: str | Path,
    ledger_tail: int = 50,
    process_rows: list[ProcessRow] | None = None,
) -> DashboardSnapshot:
    rows = process_rows if process_rows is not None else stub_processes(config)
    reclaimable = sum(r.ram_bytes or 0 for r in rows if r.state != "running")
    event_list, chain_verified, chain_errors = recent_events_verified(ledger_path, limit=ledger_tail)
    return DashboardSnapshot(
        hub_name=config.name,
        generated_at=_now_iso(),
        total_processes=len(rows),
        reclaimable_ram_bytes=reclaimable,
        processes=tuple(rows),
        recent_events=tuple(event_list),
        chain_verified=chain_verified,
        chain_errors=chain_errors,
    )


def snapshot_to_json(snapshot: DashboardSnapshot) -> bytes:
    return json.dumps(snapshot.to_dict()).encode("utf-8")


def _win_terminate(pid: int) -> None:
    import ctypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
    handle = kernel32.OpenProcess(0x0001, False, pid)  # PROCESS_TERMINATE
    if not handle:
        raise OSError(f"OpenProcess({pid}) failed")
    try:
        if not kernel32.TerminateProcess(handle, 1):
            raise OSError(f"TerminateProcess({pid}) failed")
    finally:
        kernel32.CloseHandle(handle)


def process_kill(
    pid: int,
    server_id: str,
    config: HubConfig,
    ledger_path: str | Path,
    fleet_pids: set[int] | None = None,
) -> dict[str, Any]:
    """Kill a process from the dashboard. Fail-closed: PermissionError (no
    ledger write) unless WERK_ALLOW_HUB_KILL is set. The pid must belong to the
    live fleet (``fleet_pids`` = the killable rows of the current snapshot);
    a non-fleet pid is rejected before any OS call and ledgered as failed."""
    if not kill_allowed():
        raise PermissionError("WERK_ALLOW_HUB_KILL not set; kill denied")
    allowed_pids = fleet_pids if fleet_pids is not None else set()
    if pid not in allowed_pids:
        record_event(
            ledger_path,
            "process.kill.failed",
            {"pid": pid, "server_id": server_id, "error": "pid not in live fleet"},
        )
        return {"ok": False, "pid": pid, "server_id": server_id, "error": "pid not in live fleet"}
    record_event(ledger_path, "process.kill.requested", {"pid": pid, "server_id": server_id, "requester": "dashboard"})
    try:
        if _IS_WINDOWS:
            _win_terminate(pid)
        else:
            os.kill(pid, signal.SIGTERM)
    except (OSError, ProcessLookupError) as exc:
        record_event(ledger_path, "process.kill.failed", {"pid": pid, "server_id": server_id, "error": str(exc)})
        return {"ok": False, "pid": pid, "server_id": server_id, "error": str(exc)}
    record_event(ledger_path, "process.kill.completed", {"pid": pid, "server_id": server_id})
    return {"ok": True, "pid": pid, "server_id": server_id, "error": None}


def make_handler(
    config: HubConfig,
    ledger_path: str | Path,
    ledger_tail: int = 50,
    config_path: str | Path | None = None,
    registry_http_get: Any = None,
) -> type[BaseHTTPRequestHandler]:
    session_token = secrets.token_urlsafe(32)
    # Per-handler nonce: scoped to this process/session so every server start
    # rotates the nonce. Injected into both the CSP header and the <script> tag
    # so 'unsafe-inline' can be removed without breaking the single script block.
    script_nonce = secrets.token_urlsafe(24)
    page_html = (
        DASHBOARD_HTML
        .replace("__WERK_SESSION_TOKEN__", session_token)
        .replace("__WERK_NONCE__", script_nonce)
    )
    # Mutable holder so a successful connector write is reflected in THIS
    # dashboard's own view. The running `hub serve` (a separate process) still
    # only picks up hub.json at its next start -- no live serve hot-reload.
    cfg_box = [config]
    # Server-side stash of the last registry-search candidates, keyed by id.
    # `add` looks a candidate up HERE (by id) rather than trusting a candidate
    # dict sent by the browser -- so untrusted registry data is never rebuilt
    # from client input (no confused-deputy), and the wire only carries the id.
    # One-element list (same pattern as cfg_box): a single list-item assignment
    # is atomic under CPython's GIL, eliminating the clear→populate race window
    # that existed with a plain dict under ThreadingHTTPServer.
    reg_box: list[dict] = [{}]

    class _Handler(BaseHTTPRequestHandler):
        def _send(self, code: int, body: bytes, content_type: str = "application/json") -> None:
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header(
                "Content-Security-Policy",
                f"default-src 'self'; script-src 'nonce-{script_nonce}'; style-src 'unsafe-inline'; font-src 'self' data:",
            )
            self.end_headers()
            self.wfile.write(body)

        def _host_is_loopback(self) -> bool:
            raw = self.headers.get("Host") or ""
            # Correctly parse both [::1]:port and host:port formats
            if raw.startswith("["):
                # IPv6 bracketed form: [addr]:port or [addr]
                host = raw.lstrip("[").split("]")[0]
            else:
                host = raw.rsplit(":", 1)[0]
            if host in ("localhost",):
                return True
            try:
                return ipaddress.ip_address(host).is_loopback
            except ValueError:
                return False

        def _origin_is_same(self) -> bool:
            origin = self.headers.get("Origin")
            if origin is None:
                return True
            return origin.rstrip("/") == f"http://{self.headers.get('Host', '')}".rstrip("/")

        def do_GET(self):  # noqa: N802
            if self.path == "/":
                if not self._host_is_loopback():
                    self._send(403, b'{"error":"forbidden"}')
                    return
                self._send(200, page_html.encode("utf-8"), "text/html; charset=utf-8")
            elif self.path == "/api/status":
                if not self._host_is_loopback():
                    self._send(403, b'{"error":"forbidden"}')
                    return
                self._send(200, snapshot_to_json(build_snapshot(cfg_box[0], ledger_path, ledger_tail)))
            elif self.path == "/api/events":
                if not self._host_is_loopback():
                    self._send(403, b'{"error":"forbidden"}')
                    return
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("X-Frame-Options", "DENY")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header(
                    "Content-Security-Policy",
                    f"default-src 'self'; script-src 'nonce-{script_nonce}'; style-src 'unsafe-inline'; font-src 'self' data:",
                )
                self.end_headers()
                snapshot = build_snapshot(cfg_box[0], ledger_path, ledger_tail)
                # retry: 30000 throttles browser reconnects from ~3s to 30s,
                # reducing the ThreadingHTTPServer thread-storm on disconnect.
                frame = (
                    "retry: 30000\n"
                    f"event: ledger\ndata: {json.dumps([e for e in snapshot.to_dict()['recent_events']])}\n\n"
                )
                try:
                    self.wfile.write(frame.encode("utf-8"))
                except (BrokenPipeError, ConnectionResetError, OSError):
                    pass
            elif self.path == "/api/connectors":
                if not self._host_is_loopback():
                    self._send(403, b'{"error":"forbidden"}')
                    return
                # List of configured downstream servers. redact=True masks auth
                # headers / env / secret-looking args so no secret reaches the
                # dashboard JSON. Reads cfg_box so a write in this session shows.
                connectors = [s.to_dict(redact=True) for s in cfg_box[0].servers]
                self._send(200, json.dumps(connectors).encode("utf-8"))
            elif self.path == "/api/registry":
                if not self._host_is_loopback():
                    self._send(403, b'{"error":"forbidden"}')
                    return
                self._send(200, _registry_catalog_json(config_path))
            elif self.path == "/api/approvals":
                if not self._host_is_loopback():
                    self._send(403, b'{"error":"forbidden"}')
                    return
                # List pending approvals; redact the one-use token. call_args is
                # already stored redacted on disk. Read-only: no ledger event.
                from .approvals import list_records

                rows = []
                for record in list_records(_approvals_dir(ledger_path), status="pending"):
                    row = record.to_dict()
                    row.pop("token", None)
                    rows.append(row)
                self._send(200, json.dumps(rows).encode("utf-8"))
            elif self.path == "/api/onboard":
                if not self._host_is_loopback():
                    self._send(403, b'{"error":"forbidden"}')
                    return
                if config_path is None:
                    self._send(503, b'{"error":"onboard requires a config_path"}')
                    return
                # Dry-run discovery of MCP servers in the operator's agent-host
                # configs (Claude/Cursor/Gemini/...). PRESENCE-ONLY: needs_keys
                # holds env-var KEY names, never values. No write, no ledger.
                from .onboarding import onboard

                result = onboard(config_path, apply=False)
                payload = {
                    "by_host": result.by_host,
                    "discovered": [d.to_dict() for d in result.discovered],
                    "would_adopt": [
                        {
                            "id": c.id,
                            "transport": c.transport,
                            "trust_tier": c.trust_tier,
                        }
                        for c in result.connectors
                    ],
                    "skipped_hosts": list(result.skipped_hosts),
                    "apply_allowed": onboard_allowed(),
                }
                self._send(200, json.dumps(payload).encode("utf-8"))
            elif self.path == "/api/runtimes":
                if not self._host_is_loopback():
                    self._send(403, b'{"error":"forbidden"}')
                    return
                # Pure host-detection snapshot: subprocess-free (probe_versions=False).
                # No ledger event on GET — avoid spamming the ledger on auto-refresh.
                try:
                    from .runtime_row import runtime_row
                    from .runtimes import DESCRIPTORS, probe_all

                    report = probe_all(probe_versions=False)
                    desc_by_id = {d.host_id: d for d in DESCRIPTORS}
                    probes = [
                        runtime_row(p, desc_by_id[p.host_id])
                        for p in report.probes
                        if p.host_id in desc_by_id
                    ]
                    payload = {
                        "generated_at": report.generated_at,
                        "total": len(probes),
                        "detected": list(report.detected_hosts()),
                        "probes": probes,
                    }
                    self._send(200, json.dumps(payload).encode("utf-8"))
                except Exception as exc:
                    warnings.warn(f"/api/runtimes probe failed: {type(exc).__name__}: {exc}", stacklevel=1)
                    self._send(500, json.dumps({"error": f"{type(exc).__name__}: {exc}"}).encode("utf-8"))
            else:
                self._send(404, b'{"error":"not found"}')

        def _gate_state_change(self) -> bool:
            """Shared fail-closed gate for any state-changing POST: loopback +
            same-origin, then the per-session token. Sends the 403 and returns
            False on failure; returns True when the caller may proceed."""
            if not (self._host_is_loopback() and self._origin_is_same()):
                self._send(403, b'{"ok":false,"error":"origin/host rejected"}')
                return False
            if not secrets.compare_digest(self.headers.get("X-Werk-Token", ""), session_token):
                self._send(403, b'{"ok":false,"error":"session token rejected"}')
                return False
            return True

        def do_POST(self):  # noqa: N802
            if self.path == "/api/kill":
                self._do_kill()
            elif self.path in ("/api/connectors/toggle", "/api/connectors/remove"):
                self._do_connector_write()
            elif self.path == "/api/registry/search":
                self._do_registry_search()
            elif self.path == "/api/connectors/add":
                self._do_connector_add()
            elif self.path == "/api/approvals/resolve":
                self._do_approvals_resolve()
            elif self.path == "/api/onboard/apply":
                self._do_onboard_apply()
            else:
                self._send(404, b'{"error":"not found"}')

        def _do_kill(self):
            # Drain the body up front (see _do_registry_search) before any gate
            # so a rejected request never leaves an unread body (Windows RST race).
            _cl = int(self.headers.get("Content-Length") or 0)
            if _cl > 65536:
                self.close_connection = True
                self._send(413, b'{"error":"request body too large"}')
                return
            raw = self.rfile.read(_cl)
            if not self._gate_state_change():
                return
            try:
                payload = json.loads(raw or b"{}")
                pid = int(payload["pid"])
                server_id = str(payload.get("server_id", ""))
            except (ValueError, KeyError, TypeError):
                self._send(400, b'{"ok":false,"error":"malformed kill request"}')
                return
            snapshot = build_snapshot(cfg_box[0], ledger_path, ledger_tail)
            fleet_pids = {p.pid for p in snapshot.processes if p.killable and p.pid is not None}
            try:
                result = process_kill(pid, server_id, cfg_box[0], ledger_path, fleet_pids=fleet_pids)
            except PermissionError as exc:
                self._send(403, json.dumps({"ok": False, "error": str(exc)}).encode("utf-8"))
                return
            self._send(200, json.dumps(result).encode("utf-8"))

        def _do_onboard_apply(self):
            # Drain the body before gating (see _do_kill) to avoid the Windows
            # RST race on a rejected request.
            _cl = int(self.headers.get("Content-Length") or 0)
            if _cl > 65536:
                self.close_connection = True
                self._send(413, b'{"error":"request body too large"}')
                return
            raw = self.rfile.read(_cl)
            if not self._gate_state_change():
                return
            if not onboard_allowed():
                self._send(403, b'{"ok":false,"error":"WERK_ALLOW_HUB_ONBOARD not set"}')
                return
            if config_path is None:
                self._send(503, b'{"ok":false,"error":"onboard requires a config_path"}')
                return
            # Optional host filter (e.g. adopt only Claude's servers). Absent /
            # empty => adopt from all discovered hosts.
            host_filter = None
            try:
                payload = json.loads(raw or b"{}")
                if isinstance(payload, dict) and payload.get("host"):
                    host_filter = str(payload["host"])
            except (ValueError, TypeError):
                self._send(400, b'{"ok":false,"error":"malformed onboard request"}')
                return
            # Reject an unknown host_filter outright — a typo must not look like a
            # successful no-op (added=[] with ok=True). Empty => all hosts.
            if host_filter is not None:
                from .onboarding import _MCP_CONFIG_PATH

                if host_filter not in _MCP_CONFIG_PATH:
                    self._send(
                        400,
                        json.dumps(
                            {"ok": False, "error": f"unknown host: {host_filter!r}"}
                        ).encode("utf-8"),
                    )
                    return
            # onboard(apply=True) writes hub.json AND records the
            # config.connector.added ledger event itself — do not double-record.
            from .onboarding import onboard

            result = onboard(config_path, apply=True, host_filter=host_filter)
            # Hot-swap the live config box so the new connectors show up at once.
            try:
                from .registry import load_config

                cfg_box[0] = load_config(config_path)
            except Exception:  # pragma: no cover - refresh is best-effort
                pass
            self._send(
                200,
                json.dumps(
                    {
                        "ok": True,
                        "added": list(result.added),
                        "by_host": result.by_host,
                        "skipped_hosts": list(result.skipped_hosts),
                    }
                ).encode("utf-8"),
            )

        def _do_approvals_resolve(self):
            # Drain the body before gating (see _do_kill) to avoid the Windows
            # RST race on a rejected request.
            _cl = int(self.headers.get("Content-Length") or 0)
            if _cl > 65536:
                self.close_connection = True
                self._send(413, b'{"error":"request body too large"}')
                return
            raw = self.rfile.read(_cl)
            if not self._gate_state_change():
                return
            if not approvals_allowed():
                self._send(403, b'{"ok":false,"error":"WERK_ALLOW_HUB_APPROVALS not set"}')
                return
            try:
                payload = json.loads(raw or b"{}")
                request_id = str(payload["request_id"])
                decision = str(payload["decision"])
            except (ValueError, KeyError, TypeError):
                self._send(400, b'{"ok":false,"error":"malformed resolve request"}')
                return
            if decision not in ("approve", "deny"):
                self._send(
                    400,
                    json.dumps({"ok": False, "error": f"invalid decision: {decision!r}"}).encode("utf-8"),
                )
                return
            from .approvals import (
                _validate_request_id,
                approve_request,
                deny_request,
            )

            # Reject a malformed/path-traversal id with 400 before any store
            # access, so a bad format is not misreported as a 409 conflict.
            try:
                _validate_request_id(request_id)
            except ValueError:
                self._send(
                    400,
                    json.dumps({"ok": False, "error": f"invalid request_id: {request_id!r}"}).encode("utf-8"),
                )
                return
            adir = _approvals_dir(ledger_path)
            try:
                if decision == "approve":
                    record = approve_request(adir, ledger_path, request_id, resolved_by="dashboard")
                else:
                    record = deny_request(adir, ledger_path, request_id, resolved_by="dashboard")
            except KeyError:
                self._send(
                    404,
                    json.dumps({"ok": False, "error": f"unknown request_id: {request_id}"}).encode("utf-8"),
                )
                return
            except ValueError as exc:
                self._send(409, json.dumps({"ok": False, "error": str(exc)}).encode("utf-8"))
                return
            self._send(
                200,
                json.dumps({"ok": True, "request_id": record.request_id, "status": record.status}).encode("utf-8"),
            )

        def _do_connector_write(self):
            # Drain the body up front (see _do_kill / _do_registry_search) before
            # any gate so a rejected request never leaves an unread body on the
            # socket (Windows RST / WinError 10053 race).  The three gate checks
            # (_gate_state_change, config_write_allowed, config_path) follow after
            # the body has been fully consumed.
            _cl = int(self.headers.get("Content-Length") or 0)
            if _cl > 65536:
                self.close_connection = True
                self._send(413, b'{"error":"request body too large"}')
                return
            raw = self.rfile.read(_cl)
            if not self._gate_state_change():
                return
            if not config_write_allowed():
                self._send(403, b'{"ok":false,"error":"WERK_ALLOW_HUB_CONFIG_WRITE not set"}')
                return
            if config_path is None:
                self._send(403, b'{"ok":false,"error":"dashboard has no config path; writes disabled"}')
                return
            try:
                payload = json.loads(raw or b"{}")
                conn_id = str(payload["id"])
            except (ValueError, KeyError, TypeError):
                self._send(400, b'{"ok":false,"error":"malformed connector request"}')
                return
            current = cfg_box[0]
            match = next((s for s in current.servers if s.id == conn_id), None)
            if match is None:
                self._send(404, json.dumps({"ok": False, "error": f"unknown connector {conn_id}"}).encode("utf-8"))
                return
            remove = self.path.endswith("/remove")
            if remove:
                new_servers = tuple(s for s in current.servers if s.id != conn_id)
                event, enabled = "config.connector.removed", None
            else:
                new_servers = tuple(
                    dataclasses.replace(s, enabled=not s.enabled) if s.id == conn_id else s
                    for s in current.servers
                )
                enabled = not match.enabled
                event = "config.connector.toggled"
            new_config = dataclasses.replace(current, servers=new_servers)
            try:
                persist_hub_config(new_config, config_path)
            except OSError as exc:
                self._send(500, json.dumps({"ok": False, "error": f"persist failed: {exc}"}).encode("utf-8"))
                return
            cfg_box[0] = new_config
            record_event(
                ledger_path,
                event,
                {"connector_id": conn_id, "enabled": enabled, "requester": "dashboard"},
            )
            self._send(200, json.dumps({"ok": True, "id": conn_id, "enabled": enabled, "removed": remove}).encode("utf-8"))

        def _do_registry_search(self):
            # Outbound network -> gated like a write (host+origin+token) PLUS its
            # own env opt-in, off by default. No config change. The full
            # candidates are stashed server-side (reg_box); the wire returns only
            # id/name/description/installable so untrusted registry payloads are
            # not round-tripped through the browser.
            from .discovery import candidate_to_downstream, search_registry

            # Drain the body up front so an early reject never leaves an unread
            # request body (a Windows RST / WinError 10053 race otherwise).
            _cl = int(self.headers.get("Content-Length") or 0)
            if _cl > 65536:
                self.close_connection = True
                self._send(413, b'{"error":"request body too large"}')
                return
            raw = self.rfile.read(_cl)
            if not self._gate_state_change():
                return
            if not registry_browse_allowed():
                self._send(403, b'{"ok":false,"error":"WERK_ALLOW_HUB_REGISTRY not set"}')
                return
            try:
                payload = json.loads(raw or b"{}")
                query = str(payload.get("query", ""))
            except (ValueError, TypeError):
                self._send(400, b'{"ok":false,"error":"malformed search request"}')
                return
            candidates, search_warns = search_registry(query, http_get=registry_http_get)
            # Atomic single-item assignment (GIL-safe under CPython): replaces
            # the prior clear→populate pattern that had a race window.
            new_stash = {cand.id: cand for cand in candidates}
            reg_box[0] = new_stash
            rows = [
                {
                    "id": cand.id,
                    "name": cand.name,
                    "description": cand.description,
                    "installable": candidate_to_downstream(cand) is not None,
                }
                for cand in candidates
            ]
            self._send(200, json.dumps({"ok": True, "candidates": rows, "warnings": search_warns}).encode("utf-8"))

        def _do_connector_add(self):
            # Same gate as a config write. The candidate is looked up by id from
            # the server-side stash of the last search -- never rebuilt from a
            # browser-sent dict (no confused-deputy).
            from .discovery import candidate_to_downstream

            # Drain the body up front (see _do_registry_search) before any gate.
            _cl = int(self.headers.get("Content-Length") or 0)
            if _cl > 65536:
                self.close_connection = True
                self._send(413, b'{"error":"request body too large"}')
                return
            raw = self.rfile.read(_cl)
            if not self._gate_state_change():
                return
            if not config_write_allowed():
                self._send(403, b'{"ok":false,"error":"WERK_ALLOW_HUB_CONFIG_WRITE not set"}')
                return
            if config_path is None:
                self._send(403, b'{"ok":false,"error":"dashboard has no config path; writes disabled"}')
                return
            try:
                payload = json.loads(raw or b"{}")
                conn_id = str(payload["id"])
            except (ValueError, KeyError, TypeError):
                self._send(400, b'{"ok":false,"error":"malformed add request"}')
                return
            stash = reg_box[0]
            cand = stash.get(conn_id)
            if cand is None:
                self._send(404, b'{"ok":false,"error":"unknown candidate; search first"}')
                return
            server = candidate_to_downstream(cand)
            if server is None:
                self._send(400, b'{"ok":false,"error":"candidate is not installable as a stdio server"}')
                return
            current = cfg_box[0]
            if any(s.id == server.id for s in current.servers):
                self._send(409, json.dumps({"ok": False, "error": f"connector {server.id} already configured"}).encode("utf-8"))
                return
            new_config = dataclasses.replace(current, servers=(*current.servers, server))
            try:
                persist_hub_config(new_config, config_path)
            except OSError as exc:
                self._send(500, json.dumps({"ok": False, "error": f"persist failed: {exc}"}).encode("utf-8"))
                return
            cfg_box[0] = new_config
            record_event(
                ledger_path,
                "config.connector.added",
                {"connector_id": server.id, "command": server.command, "requester": "dashboard"},
            )
            self._send(200, json.dumps({"ok": True, "id": server.id, "command": server.command}).encode("utf-8"))

        def log_message(self, *args):
            return

        def log_error(self, fmt, *args):  # type: ignore[override]
            warnings.warn(f"dashboard HTTP error: {fmt % args}", stacklevel=2)

    return _Handler


def run_dashboard(
    config: HubConfig,
    ledger_path: str | Path,
    host: str = "127.0.0.1",
    port: int = 7879,
    ledger_tail: int = 50,
    open_browser: bool = False,
    config_path: str | Path | None = None,
    registry_http_get: Any = None,
) -> None:
    try:
        _bind_is_loopback = ipaddress.ip_address(host).is_loopback
    except ValueError:
        _bind_is_loopback = host in ("localhost", "")
    if not _bind_is_loopback:
        print(
            f"WARNING: binding the hub dashboard to non-loopback host {host!r} exposes "
            "server pids, ledger tail, and the gated kill endpoint to the network.",
            file=sys.stderr,
        )
    handler = make_handler(
        config, ledger_path, ledger_tail, config_path=config_path, registry_http_get=registry_http_get
    )
    httpd = ThreadingHTTPServer((host, port), handler)
    actual = httpd.server_address[1]
    print(f"Dashboard: http://{host}:{actual}")
    if open_browser:
        import webbrowser

        webbrowser.open(f"http://{host}:{actual}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        httpd.shutdown()
