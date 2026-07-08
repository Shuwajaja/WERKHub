#!/usr/bin/env python3
"""Unicode-safety / ASCII-smuggling scanner.

Python port of ecc/scripts/ci/check-unicode-safety.js.
Stdlib-only and self-contained.

Intentional deltas from the JS reference (must_fix documentation)
------------------------------------------------------------------
1. Character-index tracking: Python tracks CHARACTER position (char_index += 1)
   because Python str slicing is by code-point index, not byte offset. The JS
   version uses UTF-16 code-unit index (index += char.length), which differs
   for astral-plane chars. The Python approach is internally consistent.

2. _sanitize intentionally omits the 6 markdown whitespace-normalization
   transforms present in JS sanitizeText (lines 164-170 of the JS source):
   strip leading spaces before '**', strip space after opening '**', normalize
   '##' heading spacing, normalize '> ' blockquote spacing, normalize '- ' list
   spacing, and normalize '1. ' ordered-list spacing.
   These are cosmetic whitespace normalizations unrelated to security scanning.
   This port focuses on the security-critical dangerous-invisible and emoji
   stripping; whitespace normalization is out of scope.
   The --write mode docstring does NOT claim to mirror JS sanitizeText exactly.

3. WRITE_MODE_SKIP paths are adapted to Python file paths (not JS paths).
   JS skips 'scripts/ci/check-unicode-safety.js' and its test; Python skips
   'scripts/check_unicode_safety.py' and 'tests/test_unicode_safety.py'.
   This is intentional, not an oversight.

4. --strict flag is a Python-side addition. The JS version always exits 1 on
   violations. The Python port adds a non-strict mode (exit 0 with warning)
   for local development use; CI always uses --strict.

Exit codes:
  0  clean (or --write applied all fixes, or non-strict mode with violations)
  1  violations remain (--strict mode only)
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Iterator, NamedTuple

# ---------------------------------------------------------------------------
# Configuration -- mirrors the JS source exactly
# ---------------------------------------------------------------------------

IGNORED_DIRS: frozenset[str] = frozenset(
    [".git", "node_modules", ".dmux", ".next", ".venv", "_site", "coverage", "dist", "venv", "__pycache__"]
)

TEXT_EXTENSIONS: frozenset[str] = frozenset(
    [
        ".md", ".mdx", ".txt",
        ".js", ".cjs", ".mjs", ".ts", ".tsx", ".jsx",
        ".json", ".toml", ".yml", ".yaml",
        ".sh", ".bash", ".zsh", ".ps1",
        ".py", ".rs",
    ]
)

# Only these extensions are rewritten in --write mode (JS: writableExtensions)
WRITABLE_EXTENSIONS: frozenset[str] = frozenset([".md", ".mdx", ".txt"])

# Skip rewriting these files in --write mode.
# Paths are Python-adapted (see module docstring item 3).
WRITE_MODE_SKIP: frozenset[str] = frozenset(
    [
        str(Path("scripts") / "check_unicode_safety.py"),
        str(Path("tests") / "test_unicode_safety.py"),
    ]
)

# ---------------------------------------------------------------------------
# Code-point predicate -- ported 1-to-1 from isDangerousInvisibleCodePoint
# ---------------------------------------------------------------------------

def _is_dangerous(cp: int) -> bool:
    """Return True for invisible / smuggling code points."""
    return (
        # Zero-width spaces / joiners / non-joiners
        (0x200B <= cp <= 0x200D)
        # Word joiner
        or cp == 0x2060
        # BOM / ZWNBSP
        or cp == 0xFEFF
        # BiDi embedding / override / isolate controls
        or (0x202A <= cp <= 0x202E)
        or (0x2066 <= cp <= 0x2069)
        # Variation selectors (VS1-VS16)
        or (0xFE00 <= cp <= 0xFE0F)
        # Variation selectors supplement
        or (0xE0100 <= cp <= 0xE01EF)
        # Unicode Tag block U+E0000-U+E007F -- canonical ASCII-smuggling vector
        or (0xE0000 <= cp <= 0xE007F)
        # Mongolian vowel separator (zero-width format control, Unicode 6.3+)
        or cp == 0x180E
        # Hangul choseong / jungseong filler (zero-width, shape-engine abuse)
        or cp in (0x115F, 0x1160)
        # Invisible math operators U+2061-U+2064
        or (0x2061 <= cp <= 0x2064)
        # Hangul filler (Discord/Twitter smuggling attacks)
        or cp == 0x3164
    )


# ---------------------------------------------------------------------------
# Allowlisted "emoji-like" symbols that the JS version deliberately keeps
# ---------------------------------------------------------------------------

ALLOWED_SYMBOL_CPS: frozenset[int] = frozenset(
    [
        0x00A9,  # (c) copyright sign
        0x00AE,  # (r) registered sign
        0x2122,  # (tm) trade mark sign
    ]
)


def _is_extended_pictographic(cp: int) -> bool:
    """Rough Python approximation of Unicode Extended_Pictographic property.

    Covers the main emoji ranges. The JS version uses the full Unicode
    property via the 'u' flag regex; we replicate the practical effect.
    """
    return (
        (0x1F300 <= cp <= 0x1F9FF)
        or (0x2600 <= cp <= 0x27BF)
        or (0x2300 <= cp <= 0x23FF)
        or (0x2B00 <= cp <= 0x2BFF)
        or (0x1FA00 <= cp <= 0x1FAFF)
        or cp in (0x203C, 0x2049, 0x2122, 0x2139, 0x2194, 0x2195, 0x2196,
                  0x2197, 0x2198, 0x2199, 0x21A9, 0x21AA, 0x231A, 0x231B,
                  0x2328, 0x23CF, 0x24C2, 0x25AA, 0x25AB, 0x25B6, 0x25C0,
                  0x25FB, 0x25FC, 0x25FD, 0x25FE, 0x2614, 0x2615, 0x2648,
                  0x2649, 0x264A, 0x264B, 0x264C, 0x264D, 0x264E, 0x264F,
                  0x2650, 0x2651, 0x2652, 0x2653, 0x267F, 0x2693, 0x26A1,
                  0x26AA, 0x26AB, 0x26BD, 0x26BE, 0x26C4, 0x26C5, 0x26CE,
                  0x26D4, 0x26EA, 0x26F2, 0x26F3, 0x26F5, 0x26FA, 0x26FD,
                  0x2702, 0x2705, 0x2708, 0x2709, 0x270A, 0x270B, 0x270C,
                  0x270D, 0x270F, 0x2712, 0x2714, 0x2716, 0x271D, 0x2721,
                  0x2728, 0x2733, 0x2734, 0x2744, 0x2747, 0x274C, 0x274E,
                  0x2753, 0x2754, 0x2755, 0x2757, 0x2763, 0x2764, 0x2795,
                  0x2796, 0x2797, 0x27A1, 0x27B0, 0x27BF, 0x2934, 0x2935,
                  0x3030, 0x303D, 0x3297, 0x3299)
    )


def _is_emoji(cp: int) -> bool:
    return _is_extended_pictographic(cp) and cp not in ALLOWED_SYMBOL_CPS


# ---------------------------------------------------------------------------
# Violation record
# ---------------------------------------------------------------------------

class Violation(NamedTuple):
    file: str
    kind: str           # "dangerous-invisible" | "emoji"
    char: str
    code_point: str     # "U+XXXX"
    line: int
    column: int


def _line_col(text: str, char_index: int) -> tuple[int, int]:
    """Return 1-based (line, col) for character index in text.

    Receives a CHARACTER index (not a byte offset) so slicing is correct
    regardless of whether preceding characters are ASCII or multi-byte.
    """
    before = text[:char_index]
    line = before.count("\n") + 1
    last_nl = before.rfind("\n")
    col = char_index - last_nl  # 1-based: distance past the newline
    return line, col


# ---------------------------------------------------------------------------
# Per-file scanning
# ---------------------------------------------------------------------------

def _scan(text: str, rel_path: str) -> list[Violation]:
    violations: list[Violation] = []
    char_index = 0  # CHARACTER index (must_fix #1: was byte offset, now char count)
    for ch in text:
        cp = ord(ch)
        if _is_dangerous(cp):
            line, col = _line_col(text, char_index)
            violations.append(
                Violation(
                    file=rel_path,
                    kind="dangerous-invisible",
                    char=ch,
                    code_point=f"U+{cp:04X}",
                    line=line,
                    column=col,
                )
            )
        elif _is_emoji(cp):
            line, col = _line_col(text, char_index)
            violations.append(
                Violation(
                    file=rel_path,
                    kind="emoji",
                    char=ch,
                    code_point=f"U+{cp:04X}",
                    line=line,
                    column=col,
                )
            )
        char_index += 1  # advance by ONE character (not UTF-8 byte length)
    return violations


def _strip_dangerous(text: str) -> str:
    return "".join(ch for ch in text if not _is_dangerous(ord(ch)))


def _sanitize(text: str) -> str:
    """Strip dangerous invisibles and targeted emoji replacements.

    Intentionally omits the 6 markdown whitespace-normalization transforms
    from the JS sanitizeText reference (strip leading spaces before '**',
    normalize '##' heading spacing, etc.). Those are cosmetic; this port
    focuses on the security-critical invisible/smuggling stripping only.
    See module docstring item 2.
    """
    text = _strip_dangerous(text)
    # Targeted emoji replacements (mirrors JS targetedReplacements).
    # Built via chr() so the source file stays ASCII-clean and does not
    # trigger the scanner itself.
    _w = chr(0x26A0)    # WARNING SIGN
    _vs = chr(0xFE0F)   # VARIATION SELECTOR-16 (emoji presentation)
    _st = chr(0x23ED)   # NEXT TRACK BUTTON (skip)
    _ok = chr(0x2705)   # WHITE HEAVY CHECK MARK
    _ng = chr(0x274C)   # CROSS MARK
    _sp = chr(0x2728)   # SPARKLE
    replacements = [
        (_w + _vs, "WARNING:"),
        (_w, "WARNING:"),
        (_st + _vs, "SKIPPED:"),
        (_st, "SKIPPED:"),
        (_ok, "PASS:"),
        (_ng, "FAIL:"),
        (_sp, ""),
    ]
    for src, dst in replacements:
        text = text.replace(src, dst)
    # Strip remaining emoji (keep allowlisted symbols)
    result = []
    for ch in text:
        cp = ord(ch)
        if _is_emoji(cp):
            continue
        result.append(ch)
    return "".join(result)


# ---------------------------------------------------------------------------
# File-system walk -- mirrors JS listFiles
# ---------------------------------------------------------------------------

def _walk(root: Path) -> Iterator[Path]:
    for entry in root.iterdir():
        if entry.name in IGNORED_DIRS:
            continue
        if entry.is_dir():
            yield from _walk(entry)
        elif entry.is_file() and entry.suffix.lower() in TEXT_EXTENSIONS:
            yield entry


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Unicode-safety / ASCII-smuggling scanner (stdlib-only).",
    )
    parser.add_argument(
        "root",
        nargs="?",
        default=".",
        help="Directory to scan (default: current directory).",
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help="Apply safe ASCII fixes to .md/.mdx/.txt files in-place.",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit 1 on any violation (use in CI). Default: warn only.",
    )
    args = parser.parse_args(argv)

    scan_root = Path(args.root).resolve()
    write_mode: bool = args.write
    strict: bool = args.strict

    changed_files: list[str] = []
    all_violations: list[Violation] = []

    for file_path in sorted(_walk(scan_root)):
        rel = str(file_path.relative_to(scan_root))
        try:
            text = file_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue

        if (
            write_mode
            and rel not in WRITE_MODE_SKIP
            and file_path.suffix.lower() in WRITABLE_EXTENSIONS
        ):
            sanitized = _sanitize(text)
            if sanitized != text:
                file_path.write_text(sanitized, encoding="utf-8")
                changed_files.append(rel)
                text = sanitized

        all_violations.extend(_scan(text, rel))

    if changed_files:
        print(f"Sanitized {len(changed_files)} file(s):")
        for f in changed_files:
            print(f"  - {f}")

    if all_violations:
        print("Unicode safety violations detected:", file=sys.stderr)
        for v in all_violations:
            print(
                f"  {v.file}:{v.line}:{v.column}  {v.kind}  {v.code_point}",
                file=sys.stderr,
            )
        if strict:
            return 1
        # local (non-strict) mode: warn but succeed
        print("WARNING: violations found (pass --strict to fail in CI)", file=sys.stderr)
        return 0

    print("Unicode safety check passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
