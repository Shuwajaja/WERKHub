"""Shared runtime-row helper used by both the CLI doctor and the dashboard.

Merges a ``RuntimeProbe`` (detection result) with its ``RuntimeDescriptor``
(display metadata) into a plain dict suitable for JSON serialisation.

Pure stdlib, zero daemon constructs, zero I/O on import.  Both
``cli.py:_hub_doctor`` (JSON path) and ``dashboard.py:do_GET /api/runtimes``
import this one function — no drift.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .runtimes import RuntimeDescriptor, RuntimeProbe


def runtime_row(probe: "RuntimeProbe", descriptor: "RuntimeDescriptor") -> dict[str, Any]:
    """Merge probe detection results with descriptor display metadata.

    Returns a dict with all RuntimeProbe fields plus the display fields from
    the descriptor that callers need for rendering:
    - display_name: human-readable host name
    - monogram: two-letter monochrome chip (never a vendor logo)
    - at_risk: whether the host is deprecated/in-transition
    - at_risk_reason: empty string ("") by default; always present for stable JSON

    The token value is never read, copied, or present in the output.
    """
    row: dict[str, Any] = probe.to_dict()
    row["display_name"] = descriptor.display_name
    row["monogram"] = descriptor.monogram
    row["at_risk"] = descriptor.at_risk
    row["at_risk_reason"] = descriptor.at_risk_reason
    return row
