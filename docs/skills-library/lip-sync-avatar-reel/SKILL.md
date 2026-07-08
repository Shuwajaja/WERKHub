---
name: lip-sync-avatar-reel
description: "End-to-end pipeline skill for producing a lip-sync talking-head avatar reel: script → TTS voice → lip-sync video → assembled reel. Backend-agnostic at every stage; degrades honestly when a backend is unavailable. Tier probe always starts at Tier 0 (local CPU) and ascends only on failure — never defaults to cloud. Use whenever the user wants to make a person say something on video, create an explainer or spokesperson reel, or chain TTS + lip-sync + assembly — even if they do not use the exact terms. Trigger keywords: talking-head, avatar reel, lip-sync, TTS voiceover, audio-driven avatar, script to video, voice clone, spokesperson video, ComfyUI lip-sync, Wav2Lip, MuseTalk, LatentSync, LTX lip-sync, Wan2GP, ElevenLabs, Kokoro, Sync Labs, D-ID, Hedra, reel assembly."
metadata:
  werk_category: "video-production"
  werk_risk: "external"
  werk_tags: "video, avatar, lip-sync, tts, talking-head, comfyui, wan2gp, musetalk, latentsync, elevenlabs, kokoro, ffmpeg, reel, honest-degrade, backend-agnostic"
  werk_source: "ecc-prompt-engineering-loop"
  werk_score: "3.8"
---
## When to Use

Use this skill when you need to produce a short talking-head avatar reel from a script, with no hard dependency on any single paid API or GPU tier. It covers every stage — scripting, TTS voice synthesis, lip-sync video generation, and final reel assembly — and specifies explicit honest-degrade behavior for each stage so the pipeline always produces something useful rather than silently failing.

Trigger conditions:
- User wants a "spokesperson" or "explainer" video from a script
- User asks to "make someone say X" using a reference image or portrait video
- User wants to produce a voiceover + avatar video without subscribing to a SaaS platform
- User has a GPU-poor machine and wants to know what is still possible locally
- User wants to plug in their own cloud API key without hardcoding it

---

## Tier Probe Rule (MANDATORY)

**Always probe from Tier 0 upward. Never skip tiers or start at a higher tier by default.**

```
Tier 0 (CPU / no key) → Tier 1 (local GPU, no key) → Tier 2 (cloud, bring-your-own key)
```

Ascend to the next tier only when the current tier is explicitly unavailable (missing binary, missing VRAM, import error, etc.). Emit a named warning at each demotion:

```
[WARN] TTS Tier 0 (Kokoro) unavailable: <reason> — trying Tier 1
[WARN] TTS Tier 1 (Orpheus) unavailable: <reason> — trying Tier 2
```

This rule prevents implementations from silently defaulting to cloud APIs, incurring costs or leaking keys, when a local path would have worked.

---

## Stage Map and Honest-Degrade Table

| Stage | Tier 0 (CPU / no key) | Tier 1 (local GPU, no key) | Tier 2 (cloud, bring-your-own key) |
|---|---|---|---|
| TTS | Kokoro-82M (Apache 2.0, CPU) | Orpheus TTS 3B (GPU) or XTTS v2 | ElevenLabs (`ELEVENLABS_API_KEY`) |
| Lip-sync | Wav2Lip (4-6 GB VRAM, degrades to CPU slowly) | MuseTalk 1.5 or LatentSync v1.6 in ComfyUI | Sync Labs API (`SYNCLABS_API_KEY`) or D-ID (`DID_API_KEY`) |
| Assembly | FFmpeg (always available) | FFmpeg + Real-ESRGAN upscale | FFmpeg (same) |

If a stage cannot run at any tier, emit a named warning (`[WARN] TTS stage skipped: no TTS backend available`) and output a placeholder file (e.g., `tts_skipped.txt` containing the script) so downstream stages can be tested independently.

---

## Numbered Steps

### Step 1 — Prepare and validate the script

1. Accept the script as a plain-text string or file path.
2. If a brand-voice profile exists (e.g., a local `voice-profile.json` with rhythm, claim style, and banned phrases), apply it before TTS. There is no canonical schema path bundled with this skill — use the profile file the user provides.
3. Split into segments if the reel has multiple takes or scene cuts. Each segment maps to one TTS call and one lip-sync call.
4. Check input portrait image resolution. If the short side is below 512 px, warn and offer to upscale with Real-ESRGAN before the lip-sync step. Both MuseTalk and LatentSync operate on 256x256 face crops; low source resolution compounds blurriness.
5. **Single-face guard:** Inspect the input portrait for multiple faces before passing it to any lip-sync model. Most local lip-sync models have undefined behavior when more than one face is present — they may pick the wrong face, crash, or produce distorted output silently. If multiple faces are detected, require the user to crop to a single-face portrait before continuing. Emit:
   ```
   [WARN] Multiple faces detected in reference_portrait.jpg.
   Lip-sync models require exactly one face. Please crop to a single subject.
   ```
   A lightweight detection check using OpenCV is sufficient:
   ```python
   import cv2
   detector = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
   img = cv2.imread("reference_portrait.jpg")
   faces = detector.detectMultiScale(img, 1.1, 4)
   if len(faces) > 1:
       raise RuntimeError(f"[WARN] {len(faces)} faces detected — crop to one face before lip-sync.")
   ```

### Step 2 — TTS voice synthesis

Probe backends in Tier 0 → 1 → 2 order. Stop at the first available one.

**Tier 0 (Kokoro-82M, CPU-safe, Apache 2.0) — try this first:**

```python
from kokoro import KPipeline
import soundfile as sf
import numpy as np

pipeline = KPipeline(lang_code="a")  # "a" = American English
samples, sample_rate = [], 24000
for _, _, audio in pipeline("Your script here.", voice="af_heart", speed=1.0):
    samples.append(audio)

sf.write("tts_output.wav", np.concatenate(samples), sample_rate)
# Output: 24 kHz stereo WAV — resample before lip-sync (see Step 3 audio note)
```

**Tier 1 (Orpheus TTS 3B, local GPU) — only if Tier 0 unavailable:**

```bash
python -m orpheus_tts \
  --text "$(cat script.txt)" \
  --output tts_output.wav \
  --device cuda
```

If Orpheus is not installed, fall back to XTTS v2:

```bash
tts --text "$(cat script.txt)" \
    --model_name "tts_models/multilingual/multi-dataset/xtts_v2" \
    --out_path tts_output.wav \
    --speaker_wav reference_voice.wav \
    --language en
```

**Tier 2 (ElevenLabs) — only if Tier 0 and Tier 1 are both unavailable. Requires `ELEVENLABS_API_KEY` in environment, never in code:**

```python
import os, httpx, pathlib

def tts_elevenlabs(script: str, voice_id: str, out_path: str) -> str:
    key = os.environ.get("ELEVENLABS_API_KEY")
    if not key:
        raise EnvironmentError("[WARN] ELEVENLABS_API_KEY not set — no Tier 2 TTS available")
    resp = httpx.post(
        f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}",
        headers={"xi-api-key": key, "Content-Type": "application/json"},
        json={"text": script, "model_id": "eleven_turbo_v2_5",
              "voice_settings": {"stability": 0.5, "similarity_boost": 0.8}},
        timeout=60,
    )
    resp.raise_for_status()
    pathlib.Path(out_path).write_bytes(resp.content)
    return out_path
    # Output: MP3 by default — convert to WAV before lip-sync (see Step 3 audio note)
```

Output contract: `tts_output.wav` (or `.mp3`). If all tiers fail, write `tts_skipped.txt` with the script and warn.

### Step 3 — Audio format normalization (MANDATORY before lip-sync)

**This step must run before any lip-sync model receives the TTS output.** Format mismatches are the top real-world failure mode and are always silent — the model will either crash or produce desynchronized output with no error message.

| TTS source | Default output | Required for Wav2Lip | Conversion needed |
|---|---|---|---|
| Kokoro-82M | 24 kHz stereo WAV | 16 kHz mono WAV | Yes |
| ElevenLabs | MP3 (variable rate) | 16 kHz mono WAV | Yes |
| XTTS v2 | 24 kHz mono WAV | 16 kHz mono WAV | Yes (rate only) |
| Orpheus TTS | 24 kHz mono WAV | 16 kHz mono WAV | Yes (rate only) |

**Wav2Lip requires exactly 16 kHz mono WAV.** MuseTalk and LatentSync are more tolerant, but normalizing to 16 kHz mono WAV is safe for all models and eliminates the mismatch class of bugs entirely.

```bash
# Normalize to 16 kHz mono WAV (works for WAV and MP3 input alike)
ffmpeg -y \
  -i tts_output.wav \
  -ar 16000 \
  -ac 1 \
  tts_normalized.wav
```

Use `tts_normalized.wav` as the audio input for all lip-sync backends below.

### Step 4 — Lip-sync video generation

Input: `reference_portrait.jpg` (or a short head-turn video) + `tts_normalized.wav` (from Step 3).
Output: `lipsync_output.mp4` (may be audio-silent — see Pitfalls).

**Single-face requirement:** Confirm Step 1 single-face guard passed before proceeding.

**Tier 1 option A — LatentSync in ComfyUI (recommended for new projects, 8 GB VRAM):**

Install ComfyUI first if not already present:
```bash
# Option A: pip (minimal install)
pip install comfyui

# Option B: full repo install
git clone https://github.com/comfyanonymous/ComfyUI
cd ComfyUI && pip install -r requirements.txt
```

Then install the LatentSync custom node via ComfyUI Manager, or manually:
```bash
cd ComfyUI/custom_nodes
git clone https://github.com/ShmuelRonen/ComfyUI-LatentSyncWrapper
cd ComfyUI-LatentSyncWrapper && pip install -r requirements.txt
```

Load the `LatentSync v1.6` workflow. On first run it auto-downloads model weights (requires internet; warn in air-gapped environments).
Set inputs: `image` node → `reference_portrait.jpg`; `audio` node → `tts_normalized.wav`.
Queue and export `lipsync_output.mp4`.

CLI trigger via ComfyUI API:

```bash
curl -s http://127.0.0.1:8188/prompt \
  -H "Content-Type: application/json" \
  -d @latentsync_payload.json | python -c "import sys,json; print(json.load(sys.stdin)['prompt_id'])"
```

`latentsync_payload.json` is the ComfyUI API-format workflow export for LatentSync v1.6. To obtain it:
1. Open the LatentSync workflow in ComfyUI.
2. Menu → **Export (API format)** → save as `latentsync_payload.json` alongside this pipeline.
3. Edit the `image` and `audio` node values in the JSON to point at your actual file paths.

A minimal template (fill in node IDs from your own export):

```json
{
  "prompt": {
    "<image_node_id>": {
      "inputs": { "image": "reference_portrait.jpg" },
      "class_type": "LoadImage"
    },
    "<audio_node_id>": {
      "inputs": { "audio": "tts_normalized.wav" },
      "class_type": "LoadAudio"
    },
    "<latentsync_node_id>": {
      "inputs": {
        "image": ["<image_node_id>", 0],
        "audio": ["<audio_node_id>", 0]
      },
      "class_type": "LatentSyncNode"
    }
  }
}
```

Node IDs are assigned by ComfyUI and differ per installation — always export from your running ComfyUI instance rather than copying a template verbatim.

**Tier 1 option B — MuseTalk 1.5 (best quality, 6-8 GB VRAM):**

MuseTalk's `inference.py` accepts `--video_path`, which expects a video file. If your input is a static portrait image (`.jpg`/`.png`), first convert it to a single-frame video:

```bash
# Convert static portrait to a 1-second video (required when --video_path is used)
ffmpeg -y -loop 1 -i reference_portrait.jpg -t 1 -r 25 portrait.mp4
```

Then run MuseTalk:

```bash
git clone https://github.com/TMElyralab/MuseTalk
cd MuseTalk && pip install -r requirements.txt
python inference.py \
  --video_path ../portrait.mp4 \
  --audio_path ../tts_normalized.wav \
  --result_dir ../output/ \
  --fps 25
# Output: output/lipsync_output.mp4
```

**Tier 1 option C — Wan2GP InfiniteTalk (GPU-poor, RTX 3060 12 GB or better):**

```bash
python wgp.py \
  --task infinite_talk \
  --image reference_portrait.jpg \
  --audio tts_normalized.wav \
  --output wan_output.mp4
```

Note: Wan2GP has a known audio-drop bug (issue #1095). Apply the mandatory FFmpeg mux in Step 5 regardless.

**Tier 0 — Wav2Lip (legacy, 4-6 GB VRAM, blurry mouth region but highest sync accuracy metric):**

```bash
python inference.py \
  --checkpoint_path checkpoints/wav2lip_gan.pth \
  --face reference_portrait.jpg \
  --audio tts_normalized.wav \
  --outfile lipsync_output.mp4
```

Wav2Lip requires 16 kHz mono WAV. Passing any other format causes silent desync. Confirm `tts_normalized.wav` was produced by Step 3 before running this command.

**Tier 2 (cloud fallback — no local GPU available):**

Provider is selected by env var `LIPSYNC_PROVIDER` (values: `synclabs`, `did`, `hedra`). Key names are presence-checked only — never read the value in application logic, only confirm existence.

```python
import os

def get_lipsync_provider() -> str:
    provider = os.environ.get("LIPSYNC_PROVIDER", "").lower()
    if not provider:
        raise RuntimeError(
            "[WARN] No lip-sync backend available at any tier. "
            "Set LIPSYNC_PROVIDER (synclabs|did|hedra) or install a local model."
        )
    return provider

def require_key(env_var: str) -> None:
    if not os.environ.get(env_var):
        raise RuntimeError(f"[WARN] {env_var} not set — cannot use this provider")

provider = get_lipsync_provider()

if provider == "synclabs":
    require_key("SYNCLABS_API_KEY")
    # POST to https://api.synclabs.so/video
elif provider == "did":
    require_key("DID_API_KEY")
    # POST to https://api.d-id.com/talks
elif provider == "hedra":
    require_key("HEDRA_API_KEY")
    # POST to Hedra API
else:
    raise RuntimeError(f"[WARN] Unknown LIPSYNC_PROVIDER value: {provider!r}")
```

Use the Sync Labs provider when quality is the priority (rated highest in 2026 comparisons). Use D-ID when cost is constrained (free tier available). Hedra is an alternative if the others are unavailable.

If all cloud calls fail, emit `[WARN] Lip-sync stage skipped` and pass `tts_normalized.wav` + `reference_portrait.jpg` to the assembly step as a static-image video.

### Step 5 — Mandatory FFmpeg audio mux

Run unconditionally after Step 4. Wan2GP drops audio silently; other models may embed audio at the wrong sample rate. This step is the honest-degrade glue.

```bash
ffmpeg -y \
  -i lipsync_output.mp4 \
  -i tts_normalized.wav \
  -c:v copy \
  -c:a aac \
  -map 0:v:0 \
  -map 1:a:0 \
  -shortest \
  lipsync_with_audio.mp4
```

If `lipsync_output.mp4` does not exist (lip-sync skipped), build a static-image video first:

```bash
ffmpeg -y -loop 1 -i reference_portrait.jpg \
  -i tts_normalized.wav \
  -c:v libx264 -tune stillimage -c:a aac \
  -shortest \
  lipsync_with_audio.mp4
```

### Step 6 — Reel assembly

Concatenate segments (if multi-scene), optionally burn captions, and export.

```bash
# Write file list
printf "file 'segment_01.mp4'\nfile 'segment_02.mp4'\n" > filelist.txt

# Concatenate
ffmpeg -y -f concat -safe 0 -i filelist.txt -c copy reel_raw.mp4

# Optional: burn subtitle captions (ASS format from transcription)
ffmpeg -y -i reel_raw.mp4 \
  -vf "ass=captions.ass" \
  -c:a copy \
  reel_final.mp4
```

For animated word-highlight captions synced to the TTS audio, a Remotion-based approach works well. If your project includes a Remotion setup, use it here; otherwise the `ass=captions.ass` filter above is a reliable built-in alternative.

### Step 7 — Quality gate check

Emit a structured summary before declaring done:

```
[REEL SUMMARY]
TTS backend used: elevenlabs / orpheus / xtts / kokoro / skipped
Audio normalized: yes (tts_normalized.wav, 16 kHz mono WAV)
Lip-sync backend used: latentsync / musetalk / wan2gp / wav2lip / synclabs / did / static-image
Single-face guard: passed / skipped
Audio mux: ok
Output: reel_final.mp4 (duration: Xs, resolution: WxH)
Skipped stages: <list or none>
Warnings: <list or none>
```

Never claim the reel is complete without printing this summary.

---

## Worked Example

**Goal:** 30-second explainer reel from a script, single portrait image, RTX 3070 (8 GB VRAM), ElevenLabs key available.

```
ELEVENLABS_API_KEY=<your key>   # set in shell, never in code
LIPSYNC_PROVIDER=               # empty — use local LatentSync (Tier 1)
```

1. Script: `"Welcome to WERKCommander. Build AI pipelines that actually run."` — 8 seconds.
2. Portrait check: single face confirmed, short side ≥ 512 px.
3. TTS probe: Tier 0 (Kokoro) available → use Kokoro → `tts_output.wav` (24 kHz stereo).
4. Audio normalize: `ffmpeg -ar 16000 -ac 1` → `tts_normalized.wav` (16 kHz mono WAV).
5. Lip-sync: LatentSync v1.6 in ComfyUI, `portrait.jpg` + `tts_normalized.wav` → `lipsync_output.mp4`.
6. FFmpeg mux: mux audio onto video → `lipsync_with_audio.mp4`.
7. Assembly: single segment, add captions via `ass=captions.ass` filter → `reel_final.mp4`.
8. Summary printed. No skipped stages. Output: `reel_final.mp4`, 8s, 512x512.

Degrade scenario: Kokoro unavailable → `[WARN] TTS Tier 0 unavailable — trying Tier 1`; pipeline continues with Orpheus. ElevenLabs key present but unused (Tier 0 succeeded first).

---

## Pitfalls

**Audio format mismatch (top real-world failure mode):** Wav2Lip requires 16 kHz mono WAV. Kokoro outputs 24 kHz stereo WAV; ElevenLabs defaults to MP3; XTTS outputs 24 kHz mono WAV. Feeding any of these directly into Wav2Lip causes silent desynchronization — no error, just a reel where mouth movement and audio are out of phase. Always run Step 3 (audio normalization) before any lip-sync model. MuseTalk and LatentSync tolerate higher sample rates, but normalizing first eliminates the entire mismatch class.

**MuseTalk `--video_path` requires a video file, not a JPEG:** Passing a static `.jpg` directly to `inference.py --video_path` may fail or produce incorrect output depending on the MuseTalk version. Always convert a static portrait to a short video first: `ffmpeg -loop 1 -i portrait.jpg -t 1 -r 25 portrait.mp4`, then pass `portrait.mp4` as `--video_path`.

**Multi-face portraits cause undefined behavior:** Most local lip-sync models are trained on single-face inputs. When the portrait contains two or more faces, these models may pick the wrong face, produce distorted composites, or crash without a clear error. Always run the single-face guard in Step 1 before passing the image to any lip-sync model.

**Wan2GP audio-drop (issue #1095):** Wan2GP silently drops audio from the output video on 480p/720p runs. The FFmpeg mux in Step 5 is the fix. Do not skip Step 5 even if the model claims to embed audio.

**LatentSync model auto-download fails in air-gapped environments:** On first run, LatentSync downloads model weights. In environments without internet access, pre-download and specify a local path in the ComfyUI node config. Emit `[WARN] LatentSync model not found — check network or set local model path`.

**`latentsync_payload.json` must come from your own ComfyUI export:** The JSON schema for ComfyUI API prompts uses node IDs that differ per installation. Never copy a template payload from the internet and expect it to work. Always export from your running ComfyUI instance via Menu → Export (API format).

**Low source image resolution compounds blurriness:** MuseTalk and LatentSync operate on 256x256 face crops. If the input portrait is below 512 px on the short side, run Real-ESRGAN upscale before the lip-sync step:

```bash
python inference_realesrgan.py -n RealESRGAN_x4plus \
  -i reference_portrait.jpg -o portrait_upscaled.jpg
```

**Voice cloning requires a paid plan on cloud TTS providers:** Instant voice clone is a paid feature on most cloud TTS platforms. The default API key on free tiers gives access to pre-made voices only. If a voice clone is requested without a paid plan, warn and fall back to a pre-made voice. Never hard-code a specific voice ID — accept it as a parameter.

**Piper TTS license risk:** The original Piper MIT repository was archived in late 2025. Active forks use GPL-3.0. Avoid Piper for commercial outputs. Use Kokoro-82M (Apache 2.0) as Tier 0 instead.

**LTX 2.3 single-pass path is experimental:** LTX 2.3 collapses lip-sync and image-to-video animation into one ComfyUI graph, but requires a large model pipeline and has a 480p ceiling on consumer GPUs. Gate behind a `--experimental-ltx` flag; do not use as a default. Reference: [Lightricks/LTX-2.3 on Hugging Face](https://huggingface.co/Lightricks/LTX-2.3); community LipSync node pack: [GeekatplayStudio/LTX-2-3-LipSync](https://github.com/GeekatplayStudio/LTX-2-3-LipSync).

**Identity drift at high resolution:** No local model perfectly preserves facial identity at 720p or above. Honest resolution ceiling: 512x512 for Wav2Lip, 256x256 face crop composited back at higher resolution for MuseTalk. If the user requires photorealistic 1080p identity preservation, degrade to a cloud lip-sync API rather than ship artifacts.

**No local GPU available at all:** If CUDA/MPS is absent and CPU inference is too slow (hours for 30 seconds), skip local lip-sync entirely, warn, and produce a static-image video (portrait + audio) as the honest-degrade output.

---

## Backends

### TTS Backends

| Backend | Tier | License | VRAM | Key Required |
|---|---|---|---|---|
| Kokoro-82M | 0 (CPU) | Apache 2.0 | None | No |
| Orpheus TTS 3B | 1 (GPU) | Open weights | ~8 GB | No |
| XTTS v2 | 1 (GPU) | Mozilla Public | ~8 GB | No |
| ElevenLabs API | 2 (cloud) | Commercial | None | `ELEVENLABS_API_KEY` |

Install Kokoro: `pip install kokoro soundfile`
Install Orpheus: `pip install orpheus-tts`
Install XTTS: `pip install TTS` (Coqui TTS)

The cloud TTS API key is presence-checked only. The key value is never read or logged by this skill.

### Lip-Sync Backends

| Backend | Tier | VRAM Floor | Quality (2026) | Key Required |
|---|---|---|---|---|
| Wav2Lip | 0 (CPU/low GPU) | 4-6 GB | Low (blurry mouth) | No |
| MuseTalk 1.5 | 1 (GPU) | ~6-8 GB | High | No |
| LatentSync v1.6 | 1 (GPU, ComfyUI) | ~8 GB | High | No |
| Wan2GP InfiniteTalk | 1 (GPU, low-VRAM) | ~8 GB (480p) | Medium-High | No |
| Sync Labs API | 2 (cloud) | None | Highest (2026) | `SYNCLABS_API_KEY` |
| D-ID API | 2 (cloud) | None | Medium | `DID_API_KEY` |
| Hedra | 2 (cloud) | None | Medium | `HEDRA_API_KEY` |

Cloud provider is selected via `LIPSYNC_PROVIDER` env var (`synclabs`, `did`, `hedra`). Default: local inference (Tier 0 → 1 before Tier 2).

MuseTalk install: `git clone https://github.com/TMElyralab/MuseTalk && pip install -r requirements.txt`
LatentSync install: ComfyUI + `ComfyUI-LatentSyncWrapper` custom node via ComfyUI Manager. See https://github.com/comfyanonymous/ComfyUI for ComfyUI base install.
Wan2GP install: `git clone https://github.com/deepbeepmeep/Wan2GP && pip install -r requirements.txt`
Sync Labs: REST API only — see https://docs.synclabs.so

### Assembly Backend

FFmpeg is the only assembly backend. It is universally available and handles all output formats from all lip-sync models. No cloud alternative is used for assembly.

```
ffmpeg >= 6.0 recommended
Real-ESRGAN (optional, for low-resolution portrait upscaling)
```

### Experimental Backend

LTX 2.3 (ComfyUI, single-pass, audio-driven) collapses Steps 3-5 into one graph. Gate behind `--experimental-ltx`. Requires a large model pipeline (~22B parameters) and has a 480p ceiling on consumer GPUs.
- Model weights: https://huggingface.co/Lightricks/LTX-2.3
- Community LipSync ComfyUI node pack: https://github.com/GeekatplayStudio/LTX-2-3-LipSync
