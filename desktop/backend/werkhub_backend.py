"""PyInstaller entry point for the bundled WERK Hub backend.

This produces a standalone executable so the desktop app needs no pre-installed
Python or `werktools`. It is just the werktools CLI: the Electron shell launches
it with `hub dashboard --host 127.0.0.1 --port <p> --config <userData>/hub.json`,
and `main()` parses those args exactly like the `werktools` console script.
"""
from __future__ import annotations

from werktools.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
