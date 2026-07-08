---
name: cinematic-ai-director
description: "Translate creative intent into structured cinematic shot directives (shot type, lens, camera movement, lighting, mood, aspect ratio) and dispatch a backend-agnostic video generation step — local Wan2GP or ComfyUI first, optional cloud API as a swappable fallback. Use when asked to direct, storyboard, or generate AI video; when you need shot-grammar vocabulary (dolly, rack focus, ECU, Dutch angle); when you want to escape a vendor-locked cinema-director tool; or when any of these keywords appear: cinematic prompt, shot list, camera movement, lens choice, video generation, Wan2GP, ComfyUI, film direction, AI videography, storyboard, backend-agnostic video, aspect ratio."
metadata:
  werk_category: "media-generation"
  werk_risk: "external"
  werk_tags: "video, cinematic, director, shot-grammar, wan2gp, comfyui, cloud-fallback, backend-agnostic, camera-movement, storyboard, local-inference"
  werk_source: "ecc-prompt-engineering-loop"
  werk_score: "3.9"
---
## When to use

Use this skill whenever you need to:

- Convert a creative brief, mood board, or script excerpt into precise shot directives ready for an AI video model.
- Generate video locally (Wan2GP / ComfyUI) and optionally fall back to a cloud API when local inference is unavailable or too slow.
- Build a multi-shot sequence with continuity locking across frames.
- Replace or audit any workflow currently locked to a single paid video API.

Do NOT use this skill for:
- Pure editing of existing footage (use `video-editing` instead).
- Beat-synced music video aesthetics without any shot-direction need (use `taste` instead).
- React/Remotion programmatic video composition (use `remotion-video-creation` instead).

---

## Method

### Step 1 — Receive and clarify the creative brief

Collect the minimum viable intent before touching any model:

- Subject and action (who does what, physics-based behavior)
- Emotional register (intimacy, dread, wonder, urgency — not adjectives, specific feelings)
- Scene context (location, time of day, narrative beat)
- Duration target (seconds per shot, total sequence length)
- Aspect ratio / output platform (landscape 16:9, portrait 9:16 for mobile/social, square 1:1)
- Backend preference or constraint (`local` / `cloud` / `auto`)

If any field is missing, ask one targeted question per gap. Do not proceed to Step 2 with vague input.

### Step 2 — Build the ShotDirective

For each shot, populate all nine fields of the Shot Grammar schema. Every field must be specific; reject placeholders.

```
ShotDirective:
  shot_type:      ECU | CU | MCU | MS | MWS | LS | ELS
  focal_equiv_mm: integer (24 | 35 | 50 | 85 | 135 | 200+)
  movement:       [see Camera Movement Normalizer below]
  movement_speed: slow | medium | fast | crash
  subject_frame:  rule-of-thirds position + headroom description
  depth_of_field: shallow (f/1.4–2.8) | mid (f/4–5.6) | deep (f/8–16)
  lighting_key:   key/fill/rim description + color temperature in Kelvin
  mood_note:      one sentence — directorial intent, not aesthetic label
  aspect_ratio:   "16:9" | "9:16" | "1:1" | "4:3" | "21:9"
  continuity:     wardrobe token, prop list, time-of-day lock (for sequences; EXCLUDED from prompt payload)
  audio_target:   ambient bed + foley note + beat-sync flag (EXCLUDED from prompt payload)
```

**Fields excluded from the `to_prompt()` payload:** `continuity` and `audio_target` carry production metadata, not model instructions. They are retained in the full `ShotDirective` object for logging, continuity locking (Step 7), and audio post-processing (Step 6), but must never appear in the serialized prompt string sent to a video model.

#### `to_prompt()` serialization contract

The serializer converts a `ShotDirective` into a single English paragraph of 80–120 words. Field inclusion order and transformation rules are fixed:

1. `shot_type` — spelled out (e.g. `"Close-Up"`, `"Medium Wide Shot"`)
2. `focal_equiv_mm` — rendered as `"{n}mm equivalent lens"`
3. `depth_of_field` — rendered as `"shallow depth of field"` / `"mid depth of field"` / `"deep focus"`
4. `movement` + `movement_speed` — concatenated as `"{speed} {canonical_movement}"` (e.g. `"slow Dolly In"`)
5. `subject_frame` — transcribed verbatim
6. `lighting_key` — transcribed verbatim
7. `mood_note` — transcribed verbatim
8. `aspect_ratio` — omitted from the text body; passed as a separate model parameter (e.g. `resolution`, `aspect_ratio`)
9. `continuity` — **excluded entirely** from the prompt string
10. `audio_target` — **excluded entirely** from the prompt string

After joining fields 1–7, append renderer-specific suffixes (e.g. `"Photorealistic, 30 FPS, 121 frames."`) and trim to the 80–120 word window. If the joined text exceeds 120 words, drop detail from `subject_frame` first, then `lighting_key`, never from `mood_note`.

#### Camera Movement Normalizer

Map free-text creative shorthand to AI-optimized vocabulary before serialization. Model adherence depends on exact phrasing.

| Creative shorthand | Canonical term to use |
|---|---|
| static, still, locked | `Locked Shot` (never "Static") |
| move closer, push in | `Dolly In / Push In` |
| pull back, reveal | `Dolly Out / Pull Out` |
| pan left / right | `smooth pan left` / `smooth pan right` |
| tilt up / down | `tilt up` / `tilt down` (always directional) |
| follow subject | `Tracking Shot following the subject` |
| side move | `Truck Left` / `Truck Right` |
| overhead drone | `Aerial Shot` + directional movement term |
| zoom in (lens, camera static) | `Zoom In` |
| Hitchcock, vertigo | `Dolly Zoom / Hitchcock Zoom / Vertigo effect` |
| handheld, shaky | `handheld camera movement, slight shake` |
| orbit, arc around | `arc shot orbiting the subject` |

**Known failure mode:** POV (point-of-view) shots produce inconsistent results across all current models. Minimize facial descriptions in POV prompts and flag the output as high-retry-risk.

#### Aspect ratio and resolution mapping

| `aspect_ratio` | Resolution (landscape/portrait) | Primary use case |
|---|---|---|
| `"16:9"` | 1280×720 (HD) or 1920×1080 (FHD) | Film, TV, desktop |
| `"9:16"` | 720×1280 (HD portrait) | Mobile/social (Reels, TikTok, Shorts) |
| `"1:1"` | 720×720 | Instagram square |
| `"4:3"` | 960×720 | Vintage/archival look |
| `"21:9"` | 1920×816 | Anamorphic / cinema ultra-wide |

**9:16 is a primary AI video form factor in 2026.** Portrait-first briefs (social media, mobile campaigns) must default to `"9:16"` not `"16:9"`. Both Wan2GP and cloud backends accept resolution as a direct parameter; pass the mapped pixel dimensions from this table.

### Step 3 — Select and probe the backend

Backend selection order: **local first, cloud fallback, honest-degrade if both unavailable**.

```python
backend = resolve_backend(preference=config.BACKEND)  # "local" | "cloud" | "auto"

def resolve_backend(preference):
    if preference in ("local", "auto"):
        wan = probe_wan2gp()       # check WanGPSession importable + VRAM
        if wan.available:
            return Wan2GPBackend(vram_profile=wan.vram_profile)
        comfy = probe_comfyui()    # check localhost:8188 /system_stats
        if comfy.available:
            return ComfyUIBackend()
    if preference in ("cloud", "auto"):
        key = os.environ.get("FAL_KEY") or os.environ.get("WAVESPEED_API_KEY")
        if key:
            return CloudBackend(cost_tier=config.COST_TIER)
    warn("No backend available — video generation step skipped.")
    return NullBackend()           # honest-degrade: logs directive, returns None
```

**VRAM auto-profile for Wan2GP:**

| VRAM | Quantization | Wan2GP --profile |
|---|---|---|
| <= 8 GB | GGUF / INT4 | `--profile 5` |
| 9–12 GB | FP8 | `--profile 3` |
| 13–16 GB | FP16 | `--profile 2` |
| > 16 GB | BF16 | `--profile 1` |

### Step 4 — Generate

All backends share one interface. The `ShotDirective` is passed whole; each backend calls `directive.to_prompt()` internally and reads `directive.aspect_ratio` to set resolution.

```python
class VideoBackend(ABC):
    def generate(self, directive: ShotDirective) -> GenerationResult: ...

@dataclass
class GenerationResult:
    success: bool
    output_path: str | None   # local file path or cloud URL
    backend_used: str
    error: str | None
```

Do not let backend details leak into the directive or the calling layer.

**Example Wan2GP call (internal to `Wan2GPBackend.generate`) — text-to-video mode:**

```python
resolution = ASPECT_RATIO_MAP[directive.aspect_ratio]  # e.g. "1280x720" or "720x1280"
WanGPSession.submit_task({
    "prompt": directive.to_prompt(),   # 80–120 words, continuity/audio excluded
    "resolution": resolution,
    "num_frames": 121,
    "num_inference_steps": 20,
    "guidance_scale": 3.5,
    "seed": 42,
})
```

**Example Wan2GP call — image-to-video mode (continuity locking):**

When `image_url` is provided (see Step 7), pass it as `"image"` to `WanGPSession.submit_task`. The session switches to the `i2v` task type automatically when an image is present; verify against `wgp.py` source for your Wan2GP version, as the parameter name (`"image"` vs `"image_path"` vs `"input_image"`) differs between releases.

```python
resolution = ASPECT_RATIO_MAP[directive.aspect_ratio]
task_params = {
    "prompt": directive.to_prompt(),
    "resolution": resolution,
    "num_frames": 121,
    "num_inference_steps": 20,
    "guidance_scale": 3.5,
    "seed": 42,
}
if directive.continuity_image_url:          # first-frame image from shot N
    task_params["image"] = directive.continuity_image_url   # i2v path
WanGPSession.submit_task(task_params)
```

`continuity_image_url` is a transient field set by the sequence assembler (Step 7) before calling `generate`; it is NOT part of the persisted `ShotDirective` object and is never serialized to `to_prompt()`.

### Step 5 — Score the output

Score every generated clip against the ShotDirective before accepting it. Minimum passing score: **3.5 / 5.0**.

| Dimension | Weight | What to check |
|---|---|---|
| Shot composition | 0.25 | Subject framing matches shot_type spec |
| Camera movement fidelity | 0.25 | Canonical movement term honored |
| Lens rendering | 0.20 | DoF and focal compression feel match focal_equiv_mm |
| Lighting match | 0.15 | Key light direction and color temp plausible |
| Creative intent | 0.15 | mood_note fulfilled (judge narratively, not technically) |

If score < 3.5: adjust the prompt (tighten movement term, reduce word count, raise or lower CFG) and retry. Max 3 retries before escalating to a higher cost_tier or requesting human review.

### Step 6 — Post-process audio (optional)

If `audio_target` is populated and `ELEVENLABS_API_KEY` is present:

1. POST the generated video to `/v1/music/video-to-music` (soundtrack) or `/v1/sound-generation` (foley/SFX).
2. Merge audio track with the video output.
3. If `ELEVENLABS_API_KEY` is absent: warn and skip — do not fail the whole generation.

For local audio: Wan2GP ships Ace Step, Stable Audio 3, and HeartMula. Use these via the same WanGPSession interface when the ElevenLabs key is absent.

### Step 7 — Continuity lock and sequence assembly

For multi-shot sequences:
- Extract `continuity` tokens from each ShotDirective before generation.
- Extract the first frame of shot N's output as a still image (e.g. via `ffmpeg -vframes 1`).
- Pass that still as `continuity_image_url` on the next `Wan2GPBackend.generate()` call (see Step 4 image-to-video example) to maintain character identity and prop state across cuts.
- For cloud backends (fal.ai), pass the same image as `image_url` in the request body (see Backends section for parameter mapping).
- Log the full ShotDirective alongside each GenerationResult for downstream editors.
- Hand off to `video-editing` or `remotion-video-creation` for final assembly.

---

## Worked example

**Brief:** "A forensic scientist discovers a hidden message in a lab at 3 AM. She's nervous. We want the audience to feel the wrongness before she does."

**Step 1 output (clarified intent):**
- Subject: female scientist, 30s, lab coat, bends toward microscope
- Emotion: dread — building wrongness before the character recognizes it
- Scene: empty lab, fluorescent overheads flickering, 3 AM
- Duration: 6-second shot, single cut
- Aspect ratio: `"16:9"` (film distribution target)
- Backend: auto (local preferred)

**Step 2 — ShotDirective:**
```yaml
shot_type: CU
focal_equiv_mm: 85
movement: "slow Dolly In / Push In"
movement_speed: slow
subject_frame: "center frame, slight low-angle, tight on her eyes and brow"
depth_of_field: shallow (f/2.0, background lab equipment bokeh)
lighting_key: "cold fluorescent key at 5500K with a subtle green cast, no fill, hard rim from off-screen monitor glow at 3200K"
mood_note: "The camera moves toward her as if something is already watching — wrongness precedes her recognition."
aspect_ratio: "16:9"
continuity: "white lab coat, silver microscope at frame left, 3 AM darkness outside frame"
audio_target: "low 40Hz sub hum (HVAC), single fluorescent flicker at frame 60, no music"
```

**`to_prompt()` output (94 words — continuity and audio_target excluded):**
"Close-Up, 85mm equivalent lens, shallow depth of field, slow Dolly In. Female scientist in white lab coat bends toward silver microscope, center frame, slight low angle tight on eyes and brow. Cold fluorescent key light at 5500K with a green cast, hard rim from monitor glow at 3200K, no fill. Background lab equipment in soft bokeh. The camera moves toward her as if something watches before she knows it. Darkness outside frame. Fluorescent flicker once mid-shot. Photorealistic, 30 FPS, 121 frames."

**Step 3 — Backend probe result:**
- Wan2GP detected, 12 GB VRAM → FP8 profile selected → `Wan2GPBackend(vram_profile="fp8")`

**Step 4 — Generate (text-to-video, first shot in sequence):**
```python
result = Wan2GPBackend.generate(directive)
# Internal call: no continuity_image_url on shot 1
# WanGPSession.submit_task({
#   "prompt": directive.to_prompt(),
#   "resolution": "1280x720",
#   "num_frames": 121,
#   "num_inference_steps": 20,
#   "guidance_scale": 3.5,
#   "seed": 42
# })
```

**Step 5 — Score:**
```
Shot composition:      4/5  (CU framing correct; slight center bias acceptable)
Camera movement:       5/5  (Dolly In honored)
Lens rendering:        4/5  (bokeh correct; focal compression acceptable)
Lighting match:        4/5  (green cast present; rim from monitor weak)
Creative intent:       4/5  (wrongness feeling present; consider second take with slower dolly)
Weighted total:        4.2 / 5.0  → PASS
```

**Step 6 — Audio:**
`ELEVENLABS_API_KEY` present → POST to `/v1/sound-generation` with foley descriptor "40Hz HVAC hum, single fluorescent flicker at 2 seconds" → merged into output MP4.

---

## Pitfalls

**POV shots fail consistently.** All current video models (Wan 2.1, 2.2, Kling, Veo) produce inconsistent results for first-person perspective. Do not promise POV delivery; offer Tracking Shot as the closest substitute.

**"Static" is worse than "Locked Shot".** The word "static" is under-represented in training data for motion control. Always use `Locked Shot` or `Tripod Shot` for unmoving cameras.

**CFG=3.5 feels wrong but is correct.** The cinematic two-pass Wan 2.2 pipeline uses CFG=3.5, which is lower than most users expect. Higher CFG (7–12) produces over-saturated, literal output that loses subtlety. Do not let the caller override this without documenting the trade-off.

**Prompt length matters.** Under 60 words: under-specified, model fills gaps randomly. Over 150 words: model loses thread, contradictions emerge. Keep to 80–120 words.

**9:16 is not a fallback — it is primary for social/mobile.** Never default to `"16:9"` when the brief mentions Reels, TikTok, Shorts, or mobile campaigns. Ask for the target platform if unstated.

**`continuity` and `audio_target` must never appear in the prompt string.** These fields carry production state, not model instructions. Including them in the prompt wastes token budget and can confuse motion control. They live in the full `ShotDirective` object only.

**`continuity_image_url` is a transient call-site argument, not a persisted field.** The sequence assembler sets it immediately before each `generate()` call; it is not stored in the `ShotDirective` dataclass and never reaches `to_prompt()`.

**Exact Wan2GP settings dict keys are not stable across versions.** The field names (`num_frames` vs `video_length`, `guidance_scale` vs `cfg_scale`, `"image"` vs `"image_path"`) differ between Wan2GP releases. Verify against `wgp.py` source before production use; do not rely solely on documentation.

**Do not treat Shot Grammar adherence as guaranteed.** The 9-point schema improves adherence; it does not enforce it. Audio targets (point 7) and exact focal-length rendering (point 3) are the least reliably honored. Score every clip.

**Cloud API prices change.** Cost-tier estimates (`economy` ≈ Hailuo at $0.01–0.03/s, `standard` ≈ Kling at $0.04–0.08/s, `premium` ≈ Veo 3 at $0.15–0.50/s) are order-of-magnitude guides as of mid-2026. Check provider pricing pages before billing clients.

**Never embed API keys.** All provider credentials are presence-only via environment variables (`FAL_KEY`, `WAVESPEED_API_KEY`, `ELEVENLABS_API_KEY`). The skill reads only whether a key is present, never its value. Fail closed if a required key is absent.

---

## Backends

This skill defines a `VideoBackend` ABC. Swap backends by changing one constructor argument or environment variable. The `ShotDirective` (including `aspect_ratio`) is identical across all backends; each backend resolves `aspect_ratio` to provider-specific parameters internally.

### Local — Wan2GP (preferred default)

- **Install:** `pip install wan2gp` or clone `github.com/deepbeepmeep/Wan2GP`
- **Interface:** `WanGPSession.submit_task(settings: dict) -> SessionJob` (in-process Python API, no Gradio UI required)
- **HTTP alternative:** FastAPI server on port 7861 — POST `/generate` with the same settings dict
- **Key parameters:** `resolution`, `num_frames`, `num_inference_steps`, `guidance_scale`, `seed`; add `"image"` key for image-to-video (i2v) mode
- **VRAM floor:** ~6 GB (GGUF quantized); 16 GB+ for BF16 full quality
- **Cinematic defaults:** `guidance_scale=3.5`, `num_inference_steps=20`, `num_frames=121` (4s at 30 FPS)
- **Resolution:** derived from `directive.aspect_ratio` via the aspect ratio table (Step 2); default `"1280x720"` for `"16:9"`, `"720x1280"` for `"9:16"`
- **Quantization selection:** auto-probed via VRAM — GGUF (<=8 GB), FP8 (9–12 GB), FP16 (13–16 GB), BF16 (>16 GB)
- **Image-to-video:** pass `"image": <path_or_url>` in the settings dict; the session auto-selects the i2v model checkpoint
- **No API key required**

### Local — ComfyUI

- **Install:** `github.com/Comfy-Org/ComfyUI`; probe endpoint: `GET localhost:8188/system_stats`
- **Interface:** POST `/prompt` with a JSON workflow graph; poll `/history/{prompt_id}` for output
- **Cinematic workflow:** dual-model high-noise + low-noise Wan 2.2 pipeline — high-noise pass (steps=20, CFG=3.5, euler/beta, noise range 0–10), low-noise pass (steps=20, CFG=3.5, euler/beta, noise range 10–end)
- **Resolution:** pass `directive.aspect_ratio` mapped to pixel dimensions (e.g. `720x1280` for `"9:16"`)
- **Frames:** 121, MP4/H.264 output
- **Python clients:** `github.com/xy200303/ComfyUiApi`, `github.com/sugarkwork/Comfyui_api_client`
- **Cloud variant:** Same JSON workflow can POST to ComfyUI Cloud (beta, comfy.org) or a RunPod serverless endpoint — change the POST target URL only
- **No API key required for local**

### Cloud fallback — provider-agnostic (fal.ai or WaveSpeedAI)

This tier is explicitly backend-agnostic. The calling layer selects a provider based on which key is present; swapping providers requires only a string change.

**fal.ai:**
- **Credential:** `FAL_KEY` environment variable (presence-only check)
- **Interface:** `fal.run("<model-endpoint>", {prompt, image_url, duration, aspect_ratio})`
- **Concrete endpoint examples by cost tier:**

| Cost tier | Endpoint string | Approximate price |
|---|---|---|
| `economy` | `"fal-ai/minimax/hailuo-02/standard/image-to-video"` | ~$0.01–0.03/s |
| `standard` | `"fal-ai/kling-video/v1.6/pro/image-to-video"` | ~$0.04–0.08/s |
| `premium` | `"fal-ai/veo3"` | ~$0.15–0.50/s |

- **Parameter mapping for `fal-ai/kling-video/v1.6/pro/image-to-video` (standard tier):**

```python
import fal_client as fal

resolution = ASPECT_RATIO_MAP[directive.aspect_ratio]   # e.g. "1280x720"
result = fal.run(
    "fal-ai/kling-video/v1.6/pro/image-to-video",
    arguments={
        "prompt": directive.to_prompt(),        # ShotDirective.to_prompt()
        "image_url": continuity_image_url,      # first-frame of previous shot, or None for t2v
        "duration": "5",                        # seconds as string; Kling accepts "5" or "10"
        "aspect_ratio": directive.aspect_ratio, # "16:9" | "9:16" | "1:1" accepted natively
    }
)
output_url = result["video"]["url"]
```

- For text-to-video (no continuity image): use `"fal-ai/kling-video/v1.6/pro/text-to-video"` and omit `image_url`.
- **Aspect ratio:** pass `directive.aspect_ratio` directly; fal.ai accepts `"16:9"` / `"9:16"` / `"1:1"` natively for Kling and most other models.
- **450+ endpoints available** — swap the endpoint string for Wan 2.6, LTX 2.0, Hunyuan Video, Luma Dream Machine, etc. Parameter names (`image_url`, `duration`, `aspect_ratio`) are consistent across Kling-family endpoints; verify for other model families.
- **Cold start:** 5–10s

**WaveSpeedAI:**
- **Credential:** `WAVESPEED_API_KEY` environment variable (presence-only check)
- **Interface:** `wavespeed.run("<model-slug>", {prompt, duration})`
- **Use when:** primary cloud key absent or rate-limited; same cost-tier logic applies

### Audio — ElevenLabs (optional post-process)

- **Credential:** `ELEVENLABS_API_KEY` environment variable (presence-only check)
- **Endpoints:** `POST /v1/sound-generation` (foley/SFX), `POST /v1/music/video-to-music` (soundtrack)
- **Input source:** `directive.audio_target` (excluded from prompt, consumed here)
- **Local alternative:** Wan2GP ships Ace Step, Stable Audio 3, and HeartMula — use via `WanGPSession` when ElevenLabs key is absent
- **Honest-degrade:** if both cloud key and local audio are unavailable, warn and return video-only output

### Null backend (honest-degrade)

- Activated when no local runtime is reachable and no cloud key is present
- Logs the full `ShotDirective` to stdout/file for manual review
- Returns `GenerationResult(success=False, output_path=None, backend_used="null", error="No backend available")`
- Never silently discards the directive

---

## Integration notes (werktools)

Register the skill in the hub catalog as a SKILL.md file with the following header block (parsed by `_parse_markdown_card` in `catalog.py`):

```
# cinematic-ai-director

Tags: video, cinematic, director, shot-grammar, wan2gp, comfyui, cloud-fallback, backend-agnostic, camera-movement, storyboard, local-inference
Profiles: *
Risk: external
Trust: Community-Unverified
Requires Approval: true
```

**Trust tier rationale:** `"Community-Unverified"` is the correct value from `TRUST_TIERS = ("Official", "Security-Scanned", "Community-Unverified")` in `catalog.py`. `"standard"` is not in that tuple and silently degrades to `"Community-Unverified"` anyway via `normalize_trust_tier()`. The `requires_approval=True` flag is the load-bearing cost gate for cloud-backend modes; trust tier is metadata only in P1 (ADR-004).

`match_skills(directory, task="generate a cinematic shot of...")` will surface this card via the existing dispatch layer at `src/werktools/tools/skills.py`.

Cross-links: `taste` (aesthetic layer), `video-editing` (post-assembly), `remotion-video-creation` (programmatic composition), `videodb` (server-side editing and indexing), `gan-style-harness` (GE loop rubric pattern).
