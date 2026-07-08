"""Local audit verification and bundle export helpers."""

from __future__ import annotations

import hashlib
import json
import shutil
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..redaction import redact_payload

_GENESIS = "0" * 64


@dataclass(frozen=True)
class AuditVerification:
    """Result of JSONL hash-chain verification."""

    ok: bool
    record_count: int
    errors: tuple[str, ...]


@dataclass(frozen=True)
class BundleResult:
    """Result of exporting a local audit bundle."""

    exported_files: tuple[str, ...]
    missing_files: tuple[str, ...]
    manifest_path: str


def _expected_hash(raw: dict[str, Any]) -> str:
    # Single canonical form shared by werktools.ledger and tools.trace:
    # sorted keys, compact separators, ensure_ascii=False, hash field excluded.
    body = dict(raw)
    body.pop("hash", None)
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def verify_chain(path: str | Path) -> AuditVerification:
    """Verify a local JSONL hash chain.

    Mirrors trace.verify_trace semantics: unhashed records are tolerated only
    before the chain starts, and an unhashed record that carries a prev_hash
    counts as a cleared-hash tampering signal.
    """
    source = Path(path)
    if not source.exists():
        return AuditVerification(False, 0, (f"missing file: {source}",))
    errors: list[str] = []
    expected_prev = _GENESIS
    chain_started = False
    record_count = 0
    # Stream line-by-line to avoid loading the entire ledger into memory at once.
    # A large ledger would previously cause an O(file_size) allocation; this
    # keeps only one line in memory at a time.
    _LARGE_FILE_WARN_BYTES = 50 * 1024 * 1024  # 50 MB
    try:
        fsize = source.stat().st_size
    except OSError:
        fsize = 0
    if fsize > _LARGE_FILE_WARN_BYTES:
        warnings.warn(
            f"verify_chain: ledger file {source} is {fsize} bytes (>{_LARGE_FILE_WARN_BYTES}); "
            f"consider rotating or archiving the ledger",
            stacklevel=2,
        )
    with source.open("r", encoding="utf-8") as _fh:
        for index, line in enumerate(_fh, start=1):
            if not line.strip():
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError as exc:
                errors.append(f"record {index}: invalid json: {exc.msg}")
                continue
            if not isinstance(raw, dict):
                errors.append(f"record {index}: expected object")
                continue
            record_count += 1
            actual_hash = str(raw.get("hash", ""))
            prev_hash = str(raw.get("prev_hash", ""))
            if not actual_hash:
                if chain_started:
                    errors.append(f"record {index}: unhashed record after chained records")
                elif prev_hash:
                    errors.append(f"record {index}: unhashed record carries prev_hash")
                continue
            if prev_hash != expected_prev:
                errors.append(f"record {index}: prev_hash mismatch")
            if actual_hash != _expected_hash(raw):
                errors.append(f"record {index}: hash mismatch")
            chain_started = True
            expected_prev = actual_hash
    if record_count and not chain_started:
        # Fail closed: a fully-unhashed file is indistinguishable from one
        # whose hash and prev_hash fields were all cleared by tampering.
        errors.append("no hashed records: chain absent or fully cleared")
    return AuditVerification(ok=not errors, record_count=record_count, errors=tuple(errors))


def redact_jsonl(src: str | Path, out: str | Path) -> Path:
    """Write a redacted copy of a JSONL file."""
    source = Path(src)
    target = Path(out)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8") as handle:
        for idx, line in enumerate(source.read_text(encoding="utf-8").splitlines()):
            if not line.strip():
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError as exc:
                warnings.warn(f"redact_jsonl: skipping corrupt line {idx}: {exc}", stacklevel=2)
                continue
            redacted = redact_payload(raw)
            handle.write(json.dumps(redacted, sort_keys=True) + "\n")
    return target


def _copy_existing(paths: tuple[str | Path, ...], out_dir: Path, missing: list[str]) -> list[str]:
    exported: list[str] = []
    for raw_path in paths:
        path = Path(raw_path)
        if not path.exists():
            missing.append(str(path))
            continue
        target = out_dir / path.name
        shutil.copy2(path, target)
        exported.append(str(target))
    return exported


def export_bundle(
    out_dir: str | Path,
    traces: tuple[str | Path, ...] = (),
    evidence: tuple[str | Path, ...] = (),
) -> BundleResult:
    """Export requested trace and evidence files with a manifest."""
    root = Path(out_dir)
    root.mkdir(parents=True, exist_ok=True)
    missing: list[str] = []
    exported = _copy_existing(traces, root, missing)
    exported.extend(_copy_existing(evidence, root, missing))
    manifest = {
        "exported_files": exported,
        "missing_files": missing,
    }
    manifest_path = root / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return BundleResult(
        exported_files=tuple(exported),
        missing_files=tuple(missing),
        manifest_path=str(manifest_path),
    )


def write_report(trace_path: str | Path, out: str | Path) -> Path:
    """Write a readable local audit report for a JSONL chain."""
    verification = verify_chain(trace_path)
    target = Path(out)
    target.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# WERK Audit Report",
        "",
        "This is a local integrity/evidence report, not a legal compliance statement.",
        "",
        f"Trace: `{trace_path}`",
        f"OK: {verification.ok}",
        f"Records: {verification.record_count}",
        "",
        "## Errors",
        "",
    ]
    if verification.errors:
        lines.extend(f"- {error}" for error in verification.errors)
    else:
        lines.append("- none")
    lines.append("")
    target.write_text("\n".join(lines), encoding="utf-8")
    return target
