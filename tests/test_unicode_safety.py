"""Tests for scripts/check_unicode_safety.py.

All fixtures are inline (no on-disk fixture files needed).
Positive fixtures: files containing each banned code-point class must be caught.
Negative fixture: clean file passes.

Non-ASCII fixture (must_fix #1 verification):
  test_line_col_non_ascii_before_violation asserts that a non-ASCII character
  (e.g. U+00E9, e-acute) preceding a violation does not corrupt the column
  number. This catches the old byte_index bug.

IMPORTANT: all non-ASCII characters in test fixtures are constructed at
runtime using chr() or escape sequences, never as literal characters in the
committed source file. This keeps the source ASCII-clean.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

# ---------------------------------------------------------------------------
# Load the script as a module without installing it
# ---------------------------------------------------------------------------

_SCRIPT = Path(__file__).parent.parent / "scripts" / "check_unicode_safety.py"

spec = importlib.util.spec_from_file_location("check_unicode_safety", _SCRIPT)
assert spec and spec.loader
_mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(_mod)  # type: ignore[union-attr]

_is_dangerous = _mod._is_dangerous
_is_emoji = _mod._is_emoji
_scan = _mod._scan
_sanitize = _mod._sanitize
main = _mod.main

# ---------------------------------------------------------------------------
# Fixture helpers -- all non-ASCII built at runtime, never literal in source
# ---------------------------------------------------------------------------

ZWSP = chr(0x200B)          # U+200B ZERO WIDTH SPACE
BIDI_OVERRIDE = chr(0x202E) # U+202E RIGHT-TO-LEFT OVERRIDE
BIDI_ISOLATE = chr(0x2066)  # U+2066 LEFT-TO-RIGHT ISOLATE
BIDI_POP = chr(0x2069)      # U+2069 POP DIRECTIONAL ISOLATE
SPARKLE = chr(0x2728)       # U+2728 SPARKLE (emoji)
CHECK = chr(0x2705)         # U+2705 WHITE HEAVY CHECK MARK (emoji)
CROSS = chr(0x274C)         # U+274C CROSS MARK (emoji)
WARN_EMOJI = chr(0x26A0)    # U+26A0 WARNING SIGN
FE0F = chr(0xFE0F)          # U+FE0F VARIATION SELECTOR-16
SKIP_EMOJI = chr(0x23ED)    # U+23ED NEXT TRACK BUTTON
E_ACUTE = chr(0x00E9)       # U+00E9 LATIN SMALL LETTER E WITH ACUTE (non-ASCII)
COPYRIGHT = chr(0x00A9)     # U+00A9 COPYRIGHT SIGN (allowlisted)
REGISTERED = chr(0x00AE)    # U+00AE REGISTERED SIGN (allowlisted)


# ---------------------------------------------------------------------------
# Code-point predicate unit tests
# ---------------------------------------------------------------------------

class TestIsDangerous:
    def test_zero_width_space(self):
        assert _is_dangerous(0x200B)

    def test_zero_width_non_joiner(self):
        assert _is_dangerous(0x200C)

    def test_zero_width_joiner(self):
        assert _is_dangerous(0x200D)

    def test_bidi_left_to_right_embedding(self):
        assert _is_dangerous(0x202A)

    def test_bidi_right_to_left_override(self):
        assert _is_dangerous(0x202E)

    def test_bidi_isolate_start(self):
        assert _is_dangerous(0x2066)

    def test_bidi_isolate_end(self):
        assert _is_dangerous(0x2069)

    def test_unicode_tag_block_start(self):
        assert _is_dangerous(0xE0000)

    def test_unicode_tag_block_ascii_a(self):
        # U+E0041 = TAG LATIN CAPITAL LETTER A -- the canonical smuggling vector
        assert _is_dangerous(0xE0041)

    def test_unicode_tag_block_end(self):
        assert _is_dangerous(0xE007F)

    def test_invisible_math_function_application(self):
        assert _is_dangerous(0x2061)

    def test_invisible_math_invisible_times(self):
        assert _is_dangerous(0x2062)

    def test_invisible_math_invisible_separator(self):
        assert _is_dangerous(0x2063)

    def test_invisible_math_invisible_plus(self):
        assert _is_dangerous(0x2064)

    def test_mongolian_vowel_separator(self):
        assert _is_dangerous(0x180E)

    def test_hangul_choseong_filler(self):
        assert _is_dangerous(0x115F)

    def test_hangul_jungseong_filler(self):
        assert _is_dangerous(0x1160)

    def test_hangul_filler(self):
        assert _is_dangerous(0x3164)

    def test_bom(self):
        assert _is_dangerous(0xFEFF)

    def test_word_joiner(self):
        assert _is_dangerous(0x2060)

    def test_normal_ascii_not_dangerous(self):
        for cp in range(0x20, 0x7F):
            assert not _is_dangerous(cp), f"U+{cp:04X} should not be dangerous"

    def test_latin1_letter_not_dangerous(self):
        assert not _is_dangerous(0x00E9)  # e with acute

    def test_zwj_in_emoji_sequence_flagged(self):
        # U+200D (ZWJ) IS in the dangerous set -- the JS scanner flags it too.
        # Only ZWJ *inside emoji skin-tone sequences* would be allowlisted in
        # a future version; the current JS version flags it globally.
        assert _is_dangerous(0x200D)


class TestIsEmoji:
    def test_copyright_allowed(self):
        assert not _is_emoji(0x00A9)

    def test_registered_allowed(self):
        assert not _is_emoji(0x00AE)

    def test_trade_mark_allowed(self):
        assert not _is_emoji(0x2122)

    def test_sparkle_is_emoji(self):
        assert _is_emoji(0x2728)

    def test_check_mark_is_emoji(self):
        assert _is_emoji(0x2705)

    def test_cross_mark_is_emoji(self):
        assert _is_emoji(0x274C)


# ---------------------------------------------------------------------------
# Per-file scan integration tests -- positive fixtures (must be caught)
# ---------------------------------------------------------------------------

class TestScanPositiveFixtures:
    def _violations_of_kind(self, text: str, kind: str) -> list:
        return [v for v in _scan(text, "fixture.py") if v.kind == kind]

    def test_detects_zero_width_space(self):
        text = "hello" + ZWSP + "world"
        vs = self._violations_of_kind(text, "dangerous-invisible")
        assert len(vs) == 1
        assert vs[0].code_point == "U+200B"

    def test_detects_bidi_override(self):
        text = "abc" + BIDI_OVERRIDE + "def"
        vs = self._violations_of_kind(text, "dangerous-invisible")
        assert any(v.code_point == "U+202E" for v in vs)

    def test_detects_bidi_isolate(self):
        text = "abc" + BIDI_ISOLATE + "def" + BIDI_POP
        vs = self._violations_of_kind(text, "dangerous-invisible")
        codes = {v.code_point for v in vs}
        assert "U+2066" in codes
        assert "U+2069" in codes

    def test_detects_tag_block_ascii_smuggling(self):
        # Build a tag-block string: TAG LATIN SMALL LETTER H = U+E0068
        tag_text = "".join(chr(0xE0000 + ord(c)) for c in "hack")
        text = "normal " + tag_text + " text"
        vs = self._violations_of_kind(text, "dangerous-invisible")
        assert len(vs) == 4  # h, a, c, k each flagged

    def test_detects_invisible_math_operators(self):
        for cp in (0x2061, 0x2062, 0x2063, 0x2064):
            text = "f" + chr(cp) + "x"
            vs = self._violations_of_kind(text, "dangerous-invisible")
            assert any(v.code_point == f"U+{cp:04X}" for v in vs)

    def test_detects_hangul_filler(self):
        text = "a" + chr(0x3164) + "b"
        vs = self._violations_of_kind(text, "dangerous-invisible")
        assert any(v.code_point == "U+3164" for v in vs)

    def test_detects_emoji_sparkle(self):
        text = "great " + SPARKLE + " job"
        vs = self._violations_of_kind(text, "emoji")
        assert any(v.code_point == "U+2728" for v in vs)

    def test_line_col_reported_correctly(self):
        text = "line1\n" + "line2" + ZWSP + "end"
        vs = _scan(text, "f.py")
        assert len(vs) == 1
        assert vs[0].line == 2
        # column = distance past the newline: 'line2' is 5 chars, so col = 6
        assert vs[0].column == 6

    def test_line_col_non_ascii_before_violation(self):
        """Non-ASCII character (U+00E9, e-acute) before the violation must not
        corrupt the column number.

        This test specifically validates the must_fix #1 correction:
        char_index is incremented by 1 (character count) not by UTF-8 byte
        length. U+00E9 is 2 bytes in UTF-8, so the old byte_index code would
        report column 7 here instead of the correct 6.

        Text: 'caf' + E_ACUTE + '\\n' + ZWSP + 'rest'
          - 'caf' = 3 chars
          - E_ACUTE (U+00E9) = 1 char (2 bytes in UTF-8)
          - '\\n' = 1 char  [total: 5 chars on line 1]
          - ZWSP is at character index 5, which is the 1st char on line 2 -> col=1
        """
        text = "caf" + E_ACUTE + "\n" + ZWSP + "rest"
        vs = _scan(text, "f.py")
        assert len(vs) == 1
        assert vs[0].line == 2
        # ZWSP is the first character after the newline -> column 1
        assert vs[0].column == 1


class TestScanNegativeFixture:
    def test_clean_file_produces_no_violations(self):
        text = (
            "# Normal Python source\n"
            "def hello(name: str) -> str:\n"
            "    return f'hello, {name}'\n"
        )
        assert _scan(text, "clean.py") == []

    def test_copyright_symbol_not_flagged(self):
        # U+00A9 is in ALLOWED_SYMBOL_CPS
        text = "Copyright (c) 2026 " + COPYRIGHT + " WERK"
        assert _scan(text, "readme.md") == []

    def test_registered_symbol_not_flagged(self):
        text = "WERKAgent" + REGISTERED + " is a product."
        assert _scan(text, "readme.md") == []


# ---------------------------------------------------------------------------
# Sanitize tests
# ---------------------------------------------------------------------------

class TestSanitize:
    def test_strips_zero_width_space(self):
        assert ZWSP not in _sanitize("a" + ZWSP + "b")

    def test_strips_tag_block(self):
        tag = chr(0xE0041)
        assert tag not in _sanitize("a" + tag + "b")

    def test_replaces_warning_emoji(self):
        result = _sanitize(WARN_EMOJI + FE0F + "some text")
        assert result.startswith("WARNING:")

    def test_replaces_check_mark_emoji(self):
        result = _sanitize(CHECK + " done")
        assert "PASS:" in result

    def test_replaces_cross_mark_emoji(self):
        result = _sanitize(CROSS + " error")
        assert "FAIL:" in result

    def test_clean_text_unchanged(self):
        text = "# hello world\nno issues here\n"
        assert _sanitize(text) == text


# ---------------------------------------------------------------------------
# CLI main() integration tests (no filesystem scan needed)
# ---------------------------------------------------------------------------

class TestMainCli:
    def test_no_violations_returns_0(self, tmp_path: Path):
        (tmp_path / "clean.md").write_text("# Hello world\n", encoding="utf-8")
        rc = main([str(tmp_path)])
        assert rc == 0

    def test_violation_strict_returns_1(self, tmp_path: Path):
        content = "a" + ZWSP + "b"
        (tmp_path / "bad.md").write_text(content, encoding="utf-8")
        rc = main(["--strict", str(tmp_path)])
        assert rc == 1

    def test_violation_no_strict_returns_0(self, tmp_path: Path):
        # Non-strict mode: violations reported but exit 0
        content = "a" + ZWSP + "b"
        (tmp_path / "bad.md").write_text(content, encoding="utf-8")
        rc = main([str(tmp_path)])
        assert rc == 0

    def test_write_mode_strips_dangerous_chars(self, tmp_path: Path):
        p = tmp_path / "note.md"
        p.write_text("hello" + ZWSP + "world", encoding="utf-8")
        rc = main(["--write", "--strict", str(tmp_path)])
        assert rc == 0
        assert ZWSP not in p.read_text(encoding="utf-8")

    def test_write_mode_does_not_touch_py_files(self, tmp_path: Path):
        p = tmp_path / "src.py"
        original = "x = 1" + ZWSP + "\n"
        p.write_text(original, encoding="utf-8")
        main(["--write", str(tmp_path)])
        # .py is not in WRITABLE_EXTENSIONS; file must be untouched
        assert p.read_text(encoding="utf-8") == original
