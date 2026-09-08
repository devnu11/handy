"""Shared helpers for tools. Not a tool itself, so it lives outside `handy/tools/`."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

_UNITS = ("B", "KB", "MB", "GB", "TB", "PB")


def format_size(num_bytes: int) -> str:
    """Render a byte count as a short human-readable string ("1.4 MB")."""
    size = float(num_bytes)
    for unit in _UNITS:
        if abs(size) < 1024 or unit == _UNITS[-1]:
            precision = 0 if unit == "B" else 1
            return f"{size:.{precision}f} {unit}"
        size /= 1024
    raise AssertionError("unreachable")


def read_json(path: Path | None) -> Any:
    """Load JSON from a file, or from stdin when `path` is None or "-"."""
    if path is None or str(path) == "-":
        return json.load(sys.stdin)
    with Path(path).open(encoding="utf-8") as handle:
        return json.load(handle)


def write_json(data: Any, path: Path | None = None) -> None:
    """Write JSON to a file, or to stdout when `path` is None or "-"."""
    text = json.dumps(data, indent=2)
    if path is None or str(path) == "-":
        print(text)
    else:
        Path(path).write_text(text + "\n", encoding="utf-8")
