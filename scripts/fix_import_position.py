"""Verschiebe 'import errno as _errno_mod' an den Dateianfang."""
import sys
from pathlib import Path

SRC = Path(__file__).parent.parent / "src" / "werktools" / "hub" / "lifecycle.py"

text = SRC.read_text(encoding="utf-8")

# Entferne das mitten in der Datei stehende 'import errno as _errno_mod'
# Es steht zwischen _win_is_alive und _posix_is_alive, eigenständige Zeile
old_mid_import = "\n\nimport errno as _errno_mod\n\n"
new_mid = "\n\n"
if old_mid_import in text:
    text = text.replace(old_mid_import, new_mid, 1)
    print("Mid-file import removed")
else:
    print("Mid-file import NOT FOUND — trying alternative patterns")
    # Try matching variations
    for variant in [
        "\nimport errno as _errno_mod\n",
        "import errno as _errno_mod\n\n",
        "\nimport errno as _errno_mod",
    ]:
        if variant in text:
            text = text.replace(variant, "\n", 1)
            print(f"Removed variant: {variant!r}")
            break
    else:
        print("No variant found")
        sys.exit(1)

# Füge es nach 'from typing import ...' ein
target = "from typing import Any, Callable, Literal, cast"
old_insert = target + "\n\n"
new_insert = target + "\nimport errno as _errno_mod\n\n"

if old_insert in text:
    text = text.replace(old_insert, new_insert, 1)
    print("Import added at top of file")
else:
    print(f"Target '{target}' not found!")
    # try to find the typing import with different whitespace
    for line in text.splitlines():
        if "from typing import" in line and "cast" in line:
            print(f"Found: {line!r}")
            break
    sys.exit(1)

SRC.write_text(text, encoding="utf-8")
print("Done!")
