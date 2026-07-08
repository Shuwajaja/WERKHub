# Local-first Generative Media Pipeline — Reference

Full implementations, schemas, tables, and edge-cases referenced by SKILL.md.

---

## Data types and contracts

### MediaJob

```python
from dataclasses import dataclass, field
from typing import Any
import uuid

@dataclass(frozen=True)
class MediaJob:
    """Immutable job descriptor passed to every MediaBackend."""
    capability: str            # "image" | "video" | "audio"
    prompt: str
    output_dir: str            # absolute path; backend writes output here
    job_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    negative_prompt: str = ""
    width: int = 1024
    height: int = 1024
    duration_seconds: float = 5.0   # video/audio only
    seed: int = -1                  # -1 = random
    extra: dict[str, Any] = field(default_factory=dict)

    def to_fal_args(self) -> dict[str, Any]:
        args: dict[str, Any] = {
            "prompt": self.prompt,
            "negative_prompt": self.negative_prompt,
            "seed": self.seed if self.seed >= 0 else None,
        }
        if self.capability == "image":
            args.update({"width": self.width, "height": self.height})
        elif self.capability == "video":
            args.update({"duration": self.duration_seconds})
        args.update(self.extra)
        return args

    def to_sdcpp_args(self) -> dict[str, Any]:
        return {
            "prompt": self.prompt,
            "negative_prompt": self.negative_prompt,
            "width": self.width,
            "height": self.height,
            "seed": self.seed,
            **self.extra,
        }
```

### JobResult

```python
from dataclasses import dataclass
from typing import Any, Literal

@dataclass(frozen=True)
class JobResult:
    """Raw result returned by MediaBackend.poll()."""
    status: Literal["ok", "failed"]
    data: bytes | dict[str, Any] | None = None
    reason: str = ""
    output_path: str | None = None
```

`status="ok"` with `output_path` set: adapter wrote the file itself (Wan2GP).
`status="ok"` with `data` set and `output_path=None`: `generate()` writes the file.
Both `data=None` and `output_path=None` on `status="ok"` is a contract violation.

### BackendStatus

```python
from dataclasses import dataclass, field
from typing import Literal
import time

BackendKind = Literal[
    "comfyui", "wan2gp", "sdcpp", "fal", "runway", "replicate", "audio-cloud"
]

@dataclass(frozen=True)
class BackendStatus:
    kind: BackendKind
    available: bool
    reason: str   # "ok" | "unreachable" | "key_absent" | "binary_missing"
                  # | "gpu_absent" | "cache_lru_missing" | "wgp_missing"
    launch_flags: tuple[str, ...] = ()
    probed_at: float = field(default_factory=time.monotonic)
```

### MediaResult envelope

```python
from dataclasses import dataclass
from typing import Literal

@dataclass(frozen=True)
class MediaResult:
    status: Literal["ok", "degraded", "failed"]
    capability: str
    backend_used: str | None
    output_path: str | None
    degraded_backends: list[BackendStatus]
    job_id: str | None
    error: str | None
```

`status="ok"` requires `output_path` set. `status="degraded"` with populated `degraded_backends` is a valid complete response.

### SubmitError

```python
class SubmitError(Exception):
    """
    reason tags: "network_error" | "http_4xx" | "http_5xx" | "payload_error" | "timeout" | "key_absent"
    retryable=True  -> try next backend in CAPABILITY_ORDER
    retryable=False -> return status="failed" immediately
    """
    def __init__(self, reason: str, message: str, *, status_code: int | None = None, retryable: bool = True) -> None:
        super().__init__(message)
        self.reason = reason
        self.status_code = status_code
        self.message = message
        self.retryable = retryable
```

### UserFacingError

```python
class UserFacingError(Exception):
    """Raised when a degraded state must surface a human-readable message to an interactive caller."""
```

### MediaBackend protocol

`poll()` accepts a full `MediaJob` (not a bare `job_id`). `submit()` returns the provider's job id string.

```python
from typing import Protocol

class MediaBackend(Protocol):
    def probe(self) -> BackendStatus: ...
    def submit(self, job: MediaJob) -> str: ...
    def poll(self, job: MediaJob, provider_job_id: str) -> JobResult: ...
```

---

## Key-presence check

```python
import os

def _key_present(env_var: str) -> bool:
    return bool(os.environ.get(env_var))
```

Call at startup and per-request. Never assert the value — only the boolean. Never log, transmit, or embed values.

---

## Probe implementations

### build_probe_registry and concurrent startup

```python
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable

ProbeFn = Callable[[], BackendStatus]

def build_probe_registry(
    comfyui_host: str = "127.0.0.1", comfyui_port: int = 8188,
    sdcpp_host: str = "127.0.0.1", sdcpp_port: int = 8080,
) -> dict[str, ProbeFn]:
    return {
        "comfyui":     lambda: probe_comfyui(comfyui_host, comfyui_port),
        "wan2gp":      probe_wan2gp,
        "sdcpp":       lambda: probe_sdcpp(sdcpp_host, sdcpp_port),
        "fal":         probe_fal,
        "runway":      probe_runway,
        "replicate":   probe_replicate,
        "audio-cloud": probe_audio_cloud,
    }


def probe_all_concurrent(probe_registry: dict[str, ProbeFn]) -> dict[str, BackendStatus]:
    """Run all probes concurrently (~2 s wall time regardless of backend count)."""
    statuses: dict[str, BackendStatus] = {}
    with ThreadPoolExecutor(max_workers=len(probe_registry)) as ex:
        future_to_kind = {ex.submit(fn): kind for kind, fn in probe_registry.items()}
        for future in as_completed(future_to_kind):
            kind = future_to_kind[future]
            try:
                statuses[kind] = future.result()
            except Exception:
                statuses[kind] = BackendStatus(kind=kind, available=False, reason="unreachable")
    return statuses
```

### reprobe_if_stale

```python
def reprobe_if_stale(
    kind: str,
    statuses: dict[str, BackendStatus],
    probe_registry: dict[str, ProbeFn],
    ttl_seconds: float = 60.0,
) -> BackendStatus:
    """Return fresh BackendStatus if cached entry is older than ttl_seconds.
    After any SubmitError, pass ttl_seconds=0 to force immediate re-probe."""
    cached = statuses.get(kind)
    if cached is None or (time.monotonic() - cached.probed_at) > ttl_seconds:
        fn = probe_registry.get(kind)
        if fn is None:
            raise KeyError(f"No probe registered for backend kind '{kind}'")
        return fn()
    return cached
```

TTL table:

| Backend type | TTL | Rationale |
|---|---|---|
| Local HTTP (ComfyUI, sd.cpp) | 60 s | Can crash silently |
| Local subprocess (Wan2GP) | 300 s | Binary presence rarely changes |
| Cloud key-presence | 300 s | Env vars stable during process lifetime |
| After any `SubmitError` | **0 s** | Always pass `ttl_seconds=0` |

### ComfyUI probe

```python
import requests

def probe_comfyui(host: str = "127.0.0.1", port: int = 8188) -> BackendStatus:
    url = f"http://{host}:{port}/system_stats"
    try:
        resp = requests.get(url, timeout=2)
        resp.raise_for_status()
        data = resp.json()
        if "cache_lru" not in data:
            return BackendStatus(kind="comfyui", available=True, reason="cache_lru_missing")
        return BackendStatus(kind="comfyui", available=True, reason="ok")
    except Exception:
        return BackendStatus(kind="comfyui", available=False, reason="unreachable")
```

Launch template: `comfyui --listen 127.0.0.1 --port 8188 --cache-lru 10`

### Wan2GP probe

```python
import shutil, os
from pathlib import Path

def probe_wan2gp() -> BackendStatus:
    wan2gp_dir = os.environ.get("WAN2GP_DIR", "")
    has_python = bool(shutil.which("python"))
    has_wgp = (Path(wan2gp_dir) / "wgp.py").exists() if wan2gp_dir else False
    if has_python and has_wgp:
        return BackendStatus(kind="wan2gp", available=True, reason="ok")
    reason = "binary_missing" if not has_python else "wgp_missing"
    return BackendStatus(kind="wan2gp", available=False, reason=reason)
```

### stable-diffusion.cpp probe

```python
import requests, shutil

def probe_sdcpp(host: str = "127.0.0.1", port: int = 8080) -> BackendStatus:
    url = f"http://{host}:{port}/health"
    try:
        requests.get(url, timeout=2).raise_for_status()
        return BackendStatus(kind="sdcpp", available=True, reason="ok")
    except Exception:
        if shutil.which("sd-server"):
            return BackendStatus(
                kind="sdcpp", available=False, reason="unreachable",
                launch_flags=("--host", "127.0.0.1", "--port", str(port)),
            )
        return BackendStatus(kind="sdcpp", available=False, reason="binary_missing")
```

### Cloud backend probes (presence-only)

```python
def probe_fal() -> BackendStatus:
    ok = _key_present("FAL_KEY")
    return BackendStatus(kind="fal", available=ok, reason="ok" if ok else "key_absent")

def probe_runway() -> BackendStatus:
    ok = _key_present("RUNWAY_API_SECRET")
    return BackendStatus(kind="runway", available=ok, reason="ok" if ok else "key_absent")

def probe_replicate() -> BackendStatus:
    ok = _key_present("REPLICATE_API_TOKEN")
    return BackendStatus(kind="replicate", available=ok, reason="ok" if ok else "key_absent")

def probe_audio_cloud() -> BackendStatus:
    # AUDIO_CLOUD_KEY_ENV holds the NAME of the actual auth key (e.g. "ELEVENLABS_API_KEY").
    key_env = os.environ.get("AUDIO_CLOUD_KEY_ENV", "")
    ok = _key_present(key_env) if key_env else False
    return BackendStatus(kind="audio-cloud", available=ok, reason="ok" if ok else "key_absent")
```

No network call at probe time for cloud backends — key presence is the only gate.

---

## Capability routing table

```python
AUDIO_CLOUD_BACKEND: str = "audio-cloud"

CLOUD_BACKENDS: frozenset[str] = frozenset({"fal", "runway", "replicate", "audio-cloud"})

CAPABILITY_ORDER: dict[str, list[str]] = {
    "image": ["comfyui", "sdcpp", "fal", "replicate"],
    "video": ["comfyui", "wan2gp", "fal", "runway", "replicate"],
    "audio": [AUDIO_CLOUD_BACKEND],
}

def select_backend(capability: str, statuses: dict[str, BackendStatus]) -> BackendStatus | None:
    for kind in CAPABILITY_ORDER[capability]:
        status = statuses.get(kind)
        if status and status.available:
            return status
    return None
```

---

## Submit-with-fallback dispatcher

`submit_with_fallback` tries each backend in order; returns on first accept. `generate()` adds ledger-persistence and poll steps.

**reprobe fix:** after a `SubmitError`, reprobe result is assigned to a local var first, then used to update `statuses[kind]`, then appended to `degraded` — so the degraded list reflects the fresh probe reason.

```python
def submit_with_fallback(
    job: MediaJob,
    capability: str,
    statuses: dict[str, BackendStatus],
    backends: dict[str, MediaBackend],
    probe_registry: dict[str, ProbeFn],
) -> tuple[MediaResult, str | None]:
    """Returns (MediaResult, provider_job_id). provider_job_id is None when status != 'ok'."""
    degraded: list[BackendStatus] = []
    for kind in CAPABILITY_ORDER[capability]:
        status = statuses.get(kind)
        if not (status and status.available):
            if status:
                degraded.append(status)
            continue
        try:
            provider_job_id = backends[kind].submit(job)
            return (
                MediaResult(
                    status="ok", capability=capability, backend_used=kind,
                    output_path=None, degraded_backends=degraded,
                    job_id=job.job_id, error=None,
                ),
                provider_job_id,
            )
        except SubmitError as exc:
            fresh_status = reprobe_if_stale(kind, statuses, probe_registry, ttl_seconds=0)
            statuses[kind] = fresh_status
            degraded.append(fresh_status)
            if not exc.retryable:
                return (
                    MediaResult(
                        status="failed", capability=capability, backend_used=None,
                        output_path=None, degraded_backends=degraded, job_id=None,
                        error=f"Non-retryable submit failure on '{kind}': {exc.message}",
                    ),
                    None,
                )
    return (
        MediaResult(
            status="degraded" if degraded else "failed",
            capability=capability, backend_used=None,
            output_path=None, degraded_backends=degraded,
            job_id=None, error="No available backend could accept the job",
        ),
        None,
    )
```

### generate() — full submit -> ledger-persist -> poll -> write cycle

```python
import os
from pathlib import Path

def generate(
    job: MediaJob,
    capability: str,
    statuses: dict[str, BackendStatus],
    backends: dict[str, MediaBackend],
    probe_registry: dict[str, ProbeFn],
    ledger=None,
) -> MediaResult:
    """
    Full cycle: submit -> persist ledger -> poll -> write output file.
    GUARD: raises ValueError if ledger=None and a cloud backend is selected.
    """
    submit_result, provider_job_id = submit_with_fallback(
        job, capability, statuses, backends, probe_registry
    )
    if submit_result.status != "ok" or provider_job_id is None:
        return submit_result

    if ledger is None and submit_result.backend_used in CLOUD_BACKENDS:
        raise ValueError(
            f"ledger=None is not allowed when a cloud backend ('{submit_result.backend_used}')"
            f" was selected. Pass a WERK ledger instance to persist provider_job_id ({provider_job_id!r})."
        )

    # CRITICAL: persist provider_job_id BEFORE entering poll loop.
    # Wan2GP note: submit() is synchronous (subprocess.run blocks); a crash inside
    # submit() cannot be recovered — the ID was never persisted. See §Pitfalls.
    if ledger is not None:
        with ledger.begin_job(job.job_id) as entry:
            entry.set_cloud_id(provider_job_id)

    backend = backends[submit_result.backend_used]
    poll_result = backend.poll(job, provider_job_id)

    if poll_result.status == "failed":
        return MediaResult(
            status="failed", capability=capability,
            backend_used=submit_result.backend_used, output_path=None,
            degraded_backends=submit_result.degraded_backends,
            job_id=job.job_id, error=poll_result.reason,
        )

    out_path = poll_result.output_path
    if out_path is None and poll_result.data:
        ext = {"image": "png", "video": "mp4", "audio": "wav"}.get(capability, "bin")
        out_path = str(Path(job.output_dir) / f"{job.job_id}.{ext}")
        Path(out_path).write_bytes(
            poll_result.data if isinstance(poll_result.data, bytes)
            else str(poll_result.data).encode()
        )

    if out_path is None:
        return MediaResult(
            status="failed", capability=capability,
            backend_used=submit_result.backend_used, output_path=None,
            degraded_backends=submit_result.degraded_backends,
            job_id=job.job_id,
            error="Backend returned ok with no data and no output_path (contract violation)",
        )

    return MediaResult(
        status="ok", capability=capability,
        backend_used=submit_result.backend_used, output_path=out_path,
        degraded_backends=submit_result.degraded_backends,
        job_id=job.job_id, error=None,
    )
```

---

## Backend adapter implementations

### Workflow path convention

```python
import os, json
from pathlib import Path

WORKFLOW_BASE_DIR: Path = Path(
    os.environ.get("GEN_MEDIA_WORKFLOW_DIR", "skills/gen-media/workflows")
)

def load_workflow(capability: str, version: str = "v1") -> dict:
    path = WORKFLOW_BASE_DIR / capability / f"{capability}_{version}.json"
    return json.loads(path.read_text())
```

### parameterise_workflow

```python
import copy

def parameterise_workflow(workflow: dict, job: MediaJob) -> dict:
    """
    Return a new workflow dict (deep copy) with job parameters applied. Never mutates input.
    Default node layout (override via job.extra["workflow_node_map"]):
      "6" -> positive prompt, "7" -> negative prompt,
      "3" -> sampler (seed), "5" -> latent image (width/height).
    """
    node_map: dict[str, str] = job.extra.get("workflow_node_map", {
        "positive_prompt": "6", "negative_prompt": "7",
        "sampler": "3", "latent_image": "5",
    })
    wf = copy.deepcopy(workflow)
    if (n := wf.get(node_map["positive_prompt"])):
        n["inputs"]["text"] = job.prompt
    if (n := wf.get(node_map["negative_prompt"])):
        n["inputs"]["text"] = job.negative_prompt
    if (n := wf.get(node_map["sampler"])) and job.seed >= 0:
        n["inputs"]["seed"] = job.seed
    if (n := wf.get(node_map["latent_image"])):
        n["inputs"]["width"] = job.width
        n["inputs"]["height"] = job.height
    return wf
```

### ComfyUI adapter (HTTP + WebSocket)

```python
import uuid, json, time, requests, websocket

COMFYUI_POLL_TIMEOUT_SECONDS: float = 120.0

class ComfyUIAdapter:
    def __init__(self, host: str = "127.0.0.1", port: int = 8188):
        self.base = f"http://{host}:{port}"
        self.ws_base = f"ws://{host}:{port}"

    def probe(self) -> BackendStatus:
        return probe_comfyui()

    def submit(self, job: MediaJob) -> str:
        workflow = load_workflow(job.capability)
        parameterised = parameterise_workflow(workflow, job)
        if "nodes" in parameterised:
            raise SubmitError(
                reason="payload_error",
                message="Workflow is in UI format. Re-export via 'Save (API Format)'.",
                retryable=False,
            )
        try:
            resp = requests.post(f"{self.base}/prompt", json={"prompt": parameterised}, timeout=10)
            resp.raise_for_status()
        except requests.RequestException as exc:
            raise SubmitError(reason="network_error", message=str(exc)) from exc
        return resp.json()["prompt_id"]

    def poll(self, job: MediaJob, provider_job_id: str) -> JobResult:
        """WebSocket poll with COMFYUI_POLL_TIMEOUT_SECONDS deadline."""
        client_id = str(uuid.uuid4())
        ws = websocket.create_connection(f"{self.ws_base}/ws?clientId={client_id}")
        ws.settimeout(COMFYUI_POLL_TIMEOUT_SECONDS)
        deadline = time.monotonic() + COMFYUI_POLL_TIMEOUT_SECONDS
        try:
            while time.monotonic() < deadline:
                try:
                    msg = json.loads(ws.recv())
                except websocket.WebSocketTimeoutException:
                    return JobResult(status="failed", reason=f"poll_timeout after {COMFYUI_POLL_TIMEOUT_SECONDS:.0f}s")
                if msg.get("type") == "executing" and msg["data"].get("node") is None:
                    break
            else:
                return JobResult(status="failed", reason=f"poll_timeout: deadline exceeded")
        finally:
            ws.close()
        history = requests.get(f"{self.base}/history/{provider_job_id}").json()
        for node_output in history[provider_job_id]["outputs"].values():
            for media_type in ("images", "videos", "gifs"):
                if media_type in node_output:
                    item = node_output[media_type][0]
                    url = f"{self.base}/view?filename={item['filename']}&subfolder={item.get('subfolder', '')}&type=output"
                    return JobResult(status="ok", data=requests.get(url).content)
        return JobResult(status="failed", reason="no output in history")
```

Normalise workflow JSON (sort keys, 2-space indent) via pre-commit hook before committing.

### Wan2GP adapter (subprocess)

Wan2GP's `wgp.py` accepts job parameters via a zip file in its input queue directory containing `config.json`. Signals failure via `error_queue.zip`.

**SYNCHRONOUS:** `submit()` calls `subprocess.run()` and blocks for full generation duration. A crash inside `submit()` loses the job before `generate()` can persist the ID — not recoverable across restarts.

```python
import json, subprocess, zipfile
from pathlib import Path

def build_wan2gp_input_zip(job: MediaJob) -> Path:
    fps = job.extra.get("fps", 16)
    num_frames = int(job.duration_seconds * fps)
    config = {
        "prompt": job.prompt, "negative_prompt": job.negative_prompt,
        "seed": job.seed if job.seed >= 0 else None,
        "width": job.width, "height": job.height, "num_frames": num_frames,
        **{k: v for k, v in job.extra.items() if k not in ("fps", "workflow_node_map")},
    }
    zip_path = Path(job.output_dir) / f"{job.job_id}_input.zip"
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("config.json", json.dumps(config, indent=2))
    return zip_path


class Wan2GPAdapter:
    def __init__(self, wan2gp_dir: str):
        self.wan2gp_dir = Path(wan2gp_dir)

    def probe(self) -> BackendStatus:
        return probe_wan2gp()

    def submit(self, job: MediaJob) -> str:
        job_zip_path = build_wan2gp_input_zip(job)
        out_dir = Path(job.output_dir) / job.job_id
        out_dir.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(
            ["python", "wgp.py", "--process", str(job_zip_path), "--output-dir", str(out_dir)],
            capture_output=True, cwd=self.wan2gp_dir,
        )
        if result.returncode != 0 or (out_dir / "error_queue.zip").exists():
            raise SubmitError(reason="payload_error", message=result.stderr.decode(), retryable=True)
        return job.job_id

    def poll(self, job: MediaJob, provider_job_id: str) -> JobResult:
        out_dir = Path(job.output_dir) / provider_job_id
        video_files = sorted(out_dir.glob("*.mp4"))
        if not video_files:
            video_files = sorted(out_dir.glob("*.webm")) or sorted(out_dir.glob("*.mkv"))
        if video_files:
            return JobResult(status="ok", output_path=str(video_files[0]))
        return JobResult(status="failed", reason=f"No video file found in {out_dir}")
```

Do **not** import `shared/api.py` in-process — couples to Wan2GP's pinned torch/CUDA versions.

### stable-diffusion.cpp adapter

> **Video endpoint unverified.** `/sdcpp/v1/vid_gen` absent from documented stable releases (mid-2025). Raises `SubmitError(retryable=True)` on 404; falls through to fal.ai/Runway/Replicate. Remove `"sdcpp"` from `CAPABILITY_ORDER["video"]` unless your build exposes this endpoint.

```python
import time, requests

class SDCppAdapter:
    def __init__(self, host: str = "127.0.0.1", port: int = 8080):
        self.base = f"http://{host}:{port}"

    def probe(self) -> BackendStatus:
        return probe_sdcpp()

    def submit(self, job: MediaJob) -> str:
        endpoint = "/sdcpp/v1/img_gen" if job.capability == "image" else "/sdcpp/v1/vid_gen"
        try:
            resp = requests.post(f"{self.base}{endpoint}", json=job.to_sdcpp_args(), timeout=10)
            resp.raise_for_status()
        except requests.HTTPError as exc:
            if exc.response is not None and exc.response.status_code == 404:
                raise SubmitError(
                    reason="http_4xx",
                    message=f"{endpoint} not found — not supported by this sd.cpp build",
                    retryable=True,
                ) from exc
            raise SubmitError(reason="http_5xx", message=str(exc)) from exc
        except requests.RequestException as exc:
            raise SubmitError(reason="network_error", message=str(exc)) from exc
        return resp.json()["job_id"]

    def poll(self, job: MediaJob, provider_job_id: str) -> JobResult:
        while True:
            data = requests.get(f"{self.base}/sdcpp/v1/jobs/{provider_job_id}", timeout=10).json()
            if data["status"] == "Completed":
                return JobResult(status="ok", data=requests.get(data["result_url"]).content)
            if data["status"] in ("Failed", "Cancelled"):
                return JobResult(status="failed", reason=data.get("error", "unknown"))
            time.sleep(2)
```

For single-image previews, use `POST /v1/images/generations` (OpenAI-compat, synchronous).

### fal.ai adapter

> Pin `fal-client>=0.5,<1`. Verify with `pip show fal-client`.

```python
import os, fal_client

FAL_MODEL_DEFAULTS: dict[str, str] = {
    "image": "fal-ai/flux/dev",
    "video": "fal-ai/wan/v2.1/image-to-video",
    "audio": "fal-ai/kokoro",
}

def _fal_model(capability: str) -> str:
    return os.environ.get(f"FAL_MODEL_{capability.upper()}", FAL_MODEL_DEFAULTS[capability])


class FalAdapter:
    def probe(self) -> BackendStatus:
        return probe_fal()

    def submit(self, job: MediaJob) -> str:
        try:
            handler = fal_client.submit(_fal_model(job.capability), arguments=job.to_fal_args())
        except Exception as exc:
            raise SubmitError(reason="network_error", message=str(exc), retryable=True) from exc
        return handler.request_id

    def poll(self, job: MediaJob, provider_job_id: str) -> JobResult:
        try:
            result = fal_client.result(_fal_model(job.capability), provider_job_id)
            return JobResult(status="ok", data=result)
        except Exception as exc:
            return JobResult(status="failed", reason=str(exc))
```

`fal_client.result()` is synchronous in `fal-client>=0.5`. For async contexts wrap with `asyncio.to_thread`. Never hardcode model paths — always use `FAL_MODEL_<CAPABILITY>` env vars.

### Runway / Replicate adapters

Implement the same `submit -> poll` pattern against each provider's REST API. Both must raise `SubmitError` — never bare exceptions.

| Provider | Key env var | Model config env var |
|---|---|---|
| Runway | `RUNWAY_API_SECRET` | (single endpoint per capability) |
| Replicate | `REPLICATE_API_TOKEN` | `REPLICATE_MODEL_IMAGE`, `REPLICATE_MODEL_VIDEO` |

### Audio cloud adapter

The `AUDIO_CLOUD_BACKEND` constant identifies the operator-installed `MediaBackend` for audio. The adapter must:

1. Call `_key_present(<AUDIO_CLOUD_KEY_ENV>)` in `probe()`.
2. Return `BackendStatus(available=False, reason="key_absent")` when absent — never raise.
3. Raise `SubmitError(reason="key_absent", retryable=False)` from `submit()` when key is absent.

Known implementations: ElevenLabs Turbo v2/v2.5 (`ELEVENLABS_API_KEY` — v1 removed Dec 2025), Cartesia Sonic (`CARTESIA_API_KEY`).

---

## Backends registry

### Local

| Backend | Capabilities | Transport | Probe | Notes |
|---|---|---|---|---|
| **ComfyUI** | image, video | HTTP + WebSocket | GET `/system_stats` 2s | Launch: `--listen 127.0.0.1 --port 8188 --cache-lru 10`. API format only. |
| **Wan2GP** | video | subprocess (synchronous) | binary + `wgp.py` in `WAN2GP_DIR` | Blocks caller thread. Parse exit code + `error_queue.zip`. Not recoverable on crash. |
| **sd.cpp** | image, video* | HTTP async + OpenAI-compat | GET `/health` 2s | *Video via `/sdcpp/v1/vid_gen` is provisional — treat as image-only in stable releases. |

### Cloud fallback

| Backend | Capabilities | Key env var | Model config env var | Notes |
|---|---|---|---|---|
| **fal.ai** | image, video, audio | `FAL_KEY` | `FAL_MODEL_{IMAGE,VIDEO,AUDIO}` | Persist `request_id` before poll. Pin `fal-client>=0.5,<1`. |
| **Runway** | video | `RUNWAY_API_SECRET` | — | Async REST poll. |
| **Replicate** | image, video | `REPLICATE_API_TOKEN` | `REPLICATE_MODEL_{IMAGE,VIDEO}` | After fal.ai and runway in video order. |
| **audio-cloud** | audio | operator-set via `AUDIO_CLOUD_KEY_ENV` | operator-set | Abstract slot; map `AUDIO_CLOUD_BACKEND` constant at install time. |

---

## Adding a new backend — checklist

1. Implement `MediaBackend` protocol: `probe`, `submit`, `poll(job, provider_job_id)`.
   - `probe()` must return `BackendStatus` — never raise.
   - `submit()` must raise `SubmitError` — never bare exceptions.
   - `poll()` must return `JobResult` with `data` or `output_path` set on `status="ok"`.
2. Add `_key_present(env_var)` guard in `probe()` for cloud; socket/binary check for local.
3. Register kind string in `CAPABILITY_ORDER` at the correct priority position.
4. Add kind to `CLOUD_BACKENDS` if cloud provider (requires ledger).
5. Add row to Backends registry above and zero-arg lambda to `build_probe_registry()`.
6. Test honest-degrade: remove key / kill binary → verify `BackendStatus.available=False` and `degraded_backends` populated.
7. Test `SubmitError`: `retryable=True` falls through; `retryable=False` returns `status="failed"` immediately.
8. Test `poll()` contract: `status="ok"` must have `output_path` or `data` — never both `None`.
9. Verify `generate()` guard: `ledger=None` + cloud backend → `ValueError`.

---

## CatalogCard registration (werktools integration)

```python
CatalogCard(
    card_id="gen-media-spine",
    kind="skill",
    title="Local-first Generative Media Pipeline",
    summary="Local ComfyUI/Wan2GP/sd.cpp with cloud audio/video fallback",
    tags=("image", "video", "audio", "comfyui", "wan2gp", "sdcpp", "fal", "runway"),
    profiles=("media",),
    source="skills/gen-media-pipeline/SKILL.md",
    risk="external",
    requires_approval=True,
    metadata={
        "launch_flags": {"comfyui": ["--listen", "127.0.0.1", "--port", "8188", "--cache-lru", "10"]},
        "capability_order": CAPABILITY_ORDER,
        "cloud_backends": list(CLOUD_BACKENDS),
        "audio_cloud_backend": AUDIO_CLOUD_BACKEND,
        "env_vars": {
            "FAL_KEY": "fal.ai auth (presence only)",
            "RUNWAY_API_SECRET": "Runway auth (presence only)",
            "REPLICATE_API_TOKEN": "Replicate auth (presence only)",
            "WAN2GP_DIR": "path to directory containing wgp.py",
            "FAL_MODEL_IMAGE": "fal.ai model for image",
            "FAL_MODEL_VIDEO": "fal.ai model for video",
            "FAL_MODEL_AUDIO": "fal.ai model for audio",
            "REPLICATE_MODEL_IMAGE": "Replicate model version for image",
            "REPLICATE_MODEL_VIDEO": "Replicate model version for video",
            "GEN_MEDIA_WORKFLOW_DIR": "root dir for ComfyUI workflow JSON files",
            "AUDIO_CLOUD_KEY_ENV": "name of the actual audio-provider key env var (meta-variable)",
        },
    },
)
```

---

## Probe output schema

Emit as the **first structured output** before any generation begins:

```json
{
  "backend_probe": [
    {"kind": "comfyui",     "available": true,  "reason": "ok"},
    {"kind": "wan2gp",      "available": false, "reason": "binary_missing"},
    {"kind": "sdcpp",       "available": false, "reason": "unreachable"},
    {"kind": "fal",         "available": true,  "reason": "ok"},
    {"kind": "runway",      "available": false, "reason": "key_absent"},
    {"kind": "replicate",   "available": false, "reason": "key_absent"},
    {"kind": "audio-cloud", "available": false, "reason": "key_absent"}
  ]
}
```

`reason` strings are machine-readable — map to human messages in UI layers.

---

## Key env-var registry

| Env var | Backend | Purpose | Required |
|---|---|---|---|
| `FAL_KEY` | fal.ai | Auth token | For fal.ai cloud paths |
| `FAL_MODEL_IMAGE` | fal.ai | Model endpoint for image | Optional; has default |
| `FAL_MODEL_VIDEO` | fal.ai | Model endpoint for video | Optional; has default |
| `FAL_MODEL_AUDIO` | fal.ai | Model endpoint for audio | Optional; has default |
| `RUNWAY_API_SECRET` | Runway | Auth token | For Runway cloud paths |
| `REPLICATE_API_TOKEN` | Replicate | Auth token | For Replicate cloud paths |
| `REPLICATE_MODEL_IMAGE` | Replicate | Model version for image | Required if Replicate used |
| `REPLICATE_MODEL_VIDEO` | Replicate | Model version for video | Required if Replicate used |
| `WAN2GP_DIR` | Wan2GP | Path to directory containing `wgp.py` | Required if Wan2GP used |
| `AUDIO_CLOUD_KEY_ENV` | audio-cloud | **Meta-variable:** value is the name of the real key (e.g. `ELEVENLABS_API_KEY`). `probe_audio_cloud()` reads this to discover which key to presence-check. | Required if audio enabled |
| `GEN_MEDIA_WORKFLOW_DIR` | ComfyUI | Root for workflow JSON files | Optional; defaults to `skills/gen-media/workflows` |

All values are presence-checked only. Never log, embed, or transmit values.

---

## Pitfalls — extended notes

### ComfyUI VRAM regression

Without `--cache-lru N`, models unload between runs — 30-90 s cold-load on consumer GPUs. Verify `cache_lru` key in `/system_stats`. If absent, emit `reason="cache_lru_missing"` (still `available=True`, log as WARNING).

### ComfyUI wrong workflow JSON format

API format = top-level integer node IDs. UI format = `nodes` array. Always export via "Save (API Format)". The `parameterise_workflow` check for `"nodes"` catches this before POST.

### Wan2GP Python env coupling

Never import `shared/api.py` in-process — pins to Wan2GP's torch/CUDA versions. Always use the subprocess adapter.

### Losing cloud job IDs across restarts

Ledger persistence belongs in `generate()`, between `submit_with_fallback()` and `backend.poll()`. The canonical pattern:

```python
if ledger is not None:
    with ledger.begin_job(job.job_id) as entry:
        entry.set_cloud_id(provider_job_id)  # committed before poll begins
poll_result = backend.poll(job, provider_job_id)
```

Wan2GP: `submit()` is synchronous, so the persist block runs after `wgp.py` exits. A crash *inside* `submit()` cannot be recovered.

### Sequential startup probes

```python
# WRONG — sequential, 14 s worst case for 7 backends:
statuses = {kind: fn() for kind, fn in probe_registry.items()}
# CORRECT:
statuses = probe_all_concurrent(probe_registry)
```

### Audio degradation UX

| Caller context | Recommended response |
|---|---|
| Batch pipeline | `status="degraded"`, log `AUDIO_CLOUD_KEY_ENV` name, skip audio |
| Interactive agent | Surface key name via `UserFacingError` |
| Requires audio | Return `status="failed"`, surface env-var name, block |

Never silently route to a local TTS engine (Coqui/Piper/Kokoro) without explicit caller opt-in.

### Noisy workflow JSON diffs

Pre-commit hook — sort keys, 2-space indent:

```yaml
- repo: local
  hooks:
    - id: normalise-comfyui-workflows
      name: Normalise ComfyUI workflow JSON
      language: python
      files: skills/gen-media/workflows/.*\.json$
      entry: python -c "
import sys, json
for f in sys.argv[1:]:
    data = json.loads(open(f).read())
    open(f,'w').write(json.dumps(data, sort_keys=True, indent=2) + '\n')
"
```

### Stale probe cache

Always call `reprobe_if_stale(..., ttl_seconds=0)` after any `SubmitError`. Default TTL is 60 s — omitting `ttl_seconds=0` leaves a crashed backend in rotation for up to a minute.

### poll() contract violation guard

`generate()` guards against `status="ok"` with both `data=None` and `output_path=None`, converting to `status="failed"` with a diagnostic message.
