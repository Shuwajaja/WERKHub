"""Thin FastMCP server helper for werktools.

Requires the optional `werktools[server]` extra.
"""

from __future__ import annotations

import inspect
import json
import keyword
import sys
import traceback
from typing import Any, Callable

try:
    from fastmcp import FastMCP
except ImportError as exc:
    raise ImportError(
        "werktools.server requires FastMCP. "
        "Install it with: pip install werktools[server]"
    ) from exc

from .envelope import err

_TYPE_MAP: dict[str, Any] = {
    "string": str,
    "integer": int,
    "number": float,
    "boolean": bool,
    "object": dict,
    "array": list,
}


def make_server(name: str, version: str = "0.1") -> FastMCP:
    """Return a configured FastMCP instance."""
    return FastMCP(name=name, version=version)


def _annotation_for(schema: dict) -> Any:
    schema_type = schema.get("type") if isinstance(schema, dict) else None
    if not isinstance(schema_type, str):
        return Any
    return _TYPE_MAP.get(schema_type, Any)


def _signature_from_schema(input_schema: dict) -> tuple[inspect.Signature, dict[str, Any]]:
    properties = input_schema.get("properties", {})
    if not isinstance(properties, dict):
        properties = {}
    required = input_schema.get("required", [])
    if not isinstance(required, list):
        required = []
    required_set = {item for item in required if isinstance(item, str)}

    parameters: list[inspect.Parameter] = []
    annotations: dict[str, Any] = {}

    ordered_names = [
        name for name in properties if isinstance(name, str) and name in required_set
    ]
    ordered_names.extend(
        name for name in properties if isinstance(name, str) and name not in required_set
    )

    for name in ordered_names:
        if not name.isidentifier() or keyword.iskeyword(name):
            raise ValueError(f"Unsupported tool argument name: {name!r}")
        annotation = _annotation_for(properties.get(name, {}))
        annotations[name] = annotation
        default = inspect.Parameter.empty if name in required_set else None
        parameters.append(
            inspect.Parameter(
                name,
                inspect.Parameter.KEYWORD_ONLY,
                default=default,
                annotation=annotation,
            )
        )

    return inspect.Signature(parameters=parameters), annotations


def register(
    server: FastMCP,
    name: str,
    description: str,
    input_schema: dict,
    handler: Callable[[dict], dict],
    snapshot=None,
) -> None:
    """Register an envelope-returning handler as a FastMCP tool."""

    def tool_fn(**kwargs):
        args = dict(kwargs)

        if snapshot is not None:
            from .policy import decide

            verdict = decide(snapshot, name)
            if verdict != "allow":
                return json.dumps(
                    err(name, f"tool {name!r} is {verdict!r} under active policy")
                )

        try:
            result = handler(args)
        except Exception as exc:
            print(traceback.format_exc(), file=sys.stderr)
            return json.dumps(err(name, f"{type(exc).__name__}: {exc}"))

        try:
            return json.dumps(result)
        except (TypeError, ValueError) as exc:
            return json.dumps(err(name, f"handler returned non-serializable result: {exc}"))

    signature, annotations = _signature_from_schema(input_schema)
    tool_fn.__name__ = name
    tool_fn.__doc__ = description
    setattr(tool_fn, "__signature__", signature)
    tool_fn.__annotations__ = annotations

    server.tool(name=name, description=description)(tool_fn)
