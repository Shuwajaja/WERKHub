---
name: local-first-gen-media-pipeline
description: "Orchestration spine for local-first generative-media pipelines. Probes ComfyUI, Wan2GP, and stable-diffusion.cpp at startup concurrently; routes image, video, and audio jobs to the first healthy local backend; promotes to a cloud fallback (fal.ai, Runway, Replicate, operator-configured audio cloud) only when local is unavailable. All provider keys are presence-only env-var checks — names declared, values never read or embedded. Any unavailable backend emits a structured BackendStatus and a status=degraded result envelope; no silent failures. Use as the shared delegation target for image, video, and audio media sub-skills. Keywords: ComfyUI, Wan2GP, stable-diffusion.cpp, sd.cpp, fal.ai, Runway, Replicate, local diffusion, generative media, image generation, video generation, TTS pipeline, media orchestration, local-first AI, honest-degrade, pluggable backend, media spine, presence-only keys, cloud fallback, CAPABILITY_ORDER, MediaBackend, SubmitError, BackendStatus."
metadata:
  werk_category: "media"
  werk_risk: "external"
  werk_tags: "comfyui, wan2gp, stable-diffusion, fal-ai, runway, replicate, local-first, generative-media, image-generation, video-generation, tts, honest-degrade, pluggable-backend, media-pipeline, orchestration, presence-only-keys"
  werk_source: "ecc-prompt-engineering-loop-v4"
  werk_score: "4"
  werk_format: "progressive-disclosure (SKILL.md + references/)"
---
## When to use

Invoke this skill for any task that generates images, video, or audio via a diffusion or
generative model when you need:

- **Local-first routing** — ComfyUI, Wan2GP, or stable-diffusion.cpp when the machine can run them.
- **Honest cloud fallback** — fal.ai, Runway, or Replicate only when local probes fail; never silently.
- **Presence-only key checks** — env-var names declared; values never read or embedded.
- **Honest-degrade** — every missing backend surfaces a structured `BackendStatus` and a
  `status="degraded"` envelope; no silent swallows.
- **Shared spine** — one routing contract across image, video, audio sub-skills.

Do NOT use for: editing existing footage, LLM text inference, or any task where cloud spend is
not approved (`requires_approval=True` is set on every cloud path).

---

## Method

### 0 — Declare env-var names (never values)

Check presence only — boolean, never value. Never log, embed, or transmit values.
Ten env-var names are required; see `references/REFERENCE.md §Key env-var registry` for the
full table. One requires special attention: `AUDIO_CLOUD_KEY_ENV` is a **meta-variable** —
its value is the name of the actual audio-provider key (e.g. `ELEVENLABS_API_KEY`), not the
key itself. `probe_audio_cloud()` reads `AUDIO_CLOUD_KEY_ENV` to discover which key name to
presence-check.

### 1 — Probe all backends concurrently at startup; emit BackendStatus

Call `probe_all_concurrent(registry)` (from `references/REFERENCE.md §Probe implementations`)
at startup — never probe sequentially (7 backends × 2 s = 14 s worst case). Before each job,
call `reprobe_if_stale(kind, statuses, probe_registry)` to detect mid-session crashes; pass
`ttl_seconds=0` explicitly after any `SubmitError` to force an immediate re-probe. Emit the
full `backend_probe` JSON before any generation starts.

See `references/REFERENCE.md §Probe implementations` for all typed probe functions,
`build_probe_registry`, `probe_all_concurrent`, `reprobe_if_stale`, and the TTL table.

### 2 — Select backend by capability and priority

First `available=True` entry in `CAPABILITY_ORDER[capability]` wins. If none: return
`MediaResult(status="degraded")` with `degraded_backends` populated — never raise.
`AUDIO_CLOUD_BACKEND` is a symbolic constant for the operator-configured audio backend.

See `references/REFERENCE.md §Capability routing table` for `CAPABILITY_ORDER`,
`select_backend`, and `submit_with_fallback`.

### 3 — Submit via MediaBackend protocol; handle SubmitError

`poll()` accepts a full `MediaJob` (not a bare `job_id`). `submit()` raises `SubmitError` for
all failures; `retryable=True` tries the next backend, `retryable=False` returns
`status="failed"` immediately. In `generate()`, persist the cloud `job_id` to the WERK ledger
**between** `submit_with_fallback()` and `backend.poll()` — a crash before this point makes
cloud results unrecoverable. **Wan2GP note:** `Wan2GPAdapter.submit()` is synchronous
(blocks via `subprocess.run`); the ledger-persist block in `generate()` runs after
`submit()` has already returned, so it still persists the ID before any post-processing, but
a crash inside `submit()` itself cannot be recovered. See adapter comment in the reference.
**Guard:** if `ledger is None` and the selected backend is a cloud backend, `generate()`
raises `ValueError` rather than silently omitting ledger persistence. See
`references/REFERENCE.md §Submit-with-fallback dispatcher` for `generate()` with the full
guard and ledger-persistence block.

### 4 — Return MediaResult envelope

Every code path returns the same shape: `status` (`"ok"` | `"degraded"` | `"failed"`),
`capability`, `backend_used`, `output_path`, `degraded_backends`, `job_id`, `error`.
`status="ok"` requires `output_path` set. `status="degraded"` with a populated
`degraded_backends` list is a valid complete response. See
`references/REFERENCE.md §Data types and contracts` for frozen dataclass definitions.

### 5 — Register as CatalogCard (werktools integration)

See `references/REFERENCE.md §CatalogCard registration` for the full block.

---

## Example

```python
from gen_media import (
    MediaJob, generate, build_probe_registry, probe_all_concurrent
)

# 1. Probe all backends concurrently at startup
registry = build_probe_registry()
statuses = probe_all_concurrent(registry)   # ~2 s wall time regardless of backend count

# 2. Build a video generation job
job = MediaJob(capability="video", prompt="golden hour timelapse", output_dir="/tmp/media")

# 3. Run full submit -> ledger-persist -> poll -> write cycle
result = generate(job, "video", statuses, backends, probe_registry=registry, ledger=ledger)

# 4. Inspect result — output_path is set iff status == "ok"
assert result.status in ("ok", "degraded", "failed")
print(result.output_path, result.degraded_backends)
```

`generate()` handles routing, ledger persistence, polling, and file writing in one call.
See `references/REFERENCE.md §Submit-with-fallback dispatcher` for the full implementation.

---

## Pitfalls

- **ComfyUI poll unbounded** — `ws.recv()` has no timeout by default; stalled queue blocks forever. See `§Pitfalls extended`.
- **Wan2GP is synchronous** — `subprocess.run` blocks; crash inside submit loses the cloud ID before ledger-persist. See `§Pitfalls extended`.
- **Cloud job IDs lost** — persist `provider_job_id` in `generate()` before `poll()`, never inside `submit_with_fallback`. See `§Pitfalls extended`.
- **sd.cpp video endpoint unverified** — treat sd.cpp as image-only in stable releases; `/sdcpp/v1/vid_gen` will return a retryable 404. See `§Backends registry`.
- **Sequential startup probes** — always use `probe_all_concurrent`; a dict comprehension costs 14 s worst case. See `§Probe implementations`.

---

## Backends

See `references/REFERENCE.md §Backends registry` for the full table: capabilities, transport,
probe method, key env vars, and model config env vars per backend.
