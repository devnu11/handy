"""`handy servers edit`: open the config, insist it parses, report what changed."""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from handy.tools._servers.config import ConfigError, Server, parse

# Used only when neither VISUAL nor EDITOR is set: simple editors first.
FALLBACK_EDITORS = ("nano", "pico", "notepad", "vi")


def pick_editor(
    env: dict[str, str] | None = None, which: Callable[[str], str | None] = shutil.which
) -> list[str]:
    env = os.environ if env is None else env
    for var in ("VISUAL", "EDITOR"):
        if env.get(var, "").strip():
            return shlex.split(env[var])
    for name in FALLBACK_EDITORS:
        if name == "notepad" and os.name != "nt":
            continue
        if which(name):
            return [name]
    return ["vi"]


@dataclass
class Changes:
    added: list[Server] = field(default_factory=list)
    removed: list[Server] = field(default_factory=list)
    changed: list[tuple[Server, Server]] = field(default_factory=list)  # (old, new)

    def __bool__(self) -> bool:
        return bool(self.added or self.removed or self.changed)

    def describe(self) -> str:
        parts = []
        if self.changed:
            parts.append("restart " + ", ".join(new.name for _, new in self.changed))
        if self.added:
            parts.append("start " + ", ".join(s.name for s in self.added))
        if self.removed:
            parts.append("stop " + ", ".join(s.name for s in self.removed))
        return "; ".join(parts)

    def hosts(self) -> set[str]:
        """Every host whose set of servers changed, old and new."""
        hosts = {s.host for s in self.added} | {s.host for s in self.removed}
        for old, new in self.changed:
            hosts |= {old.host, new.host}
        return hosts


def diff(old: list[Server], new: list[Server]) -> Changes:
    """Servers are matched by name; an entry differs if its address or command does."""
    before = {s.name: s for s in old}
    after = {s.name: s for s in new}
    changes = Changes()
    for name, server in after.items():
        if name not in before:
            changes.added.append(server)
        elif _definition(before[name]) != _definition(server):
            changes.changed.append((before[name], server))
    changes.removed = [s for name, s in before.items() if name not in after]
    return changes


def _definition(server: Server) -> tuple:
    return (server.host, server.port, server.command)


def edit_until_valid(
    path: Path,
    *,
    run_editor: Callable[[list[str]], int] = subprocess.call,
    ask: Callable[[str], str] = input,
    interactive: bool = True,
    say: Callable[[str], None] = print,
) -> tuple[list[Server] | None, list[Server] | None]:
    """Run the editor until the file parses or the user gives up.

    Returns (servers before, servers after). `before` is None if the file was
    already invalid; `after` is None if the user quit with it still invalid.
    """
    try:
        before = parse(path.read_text(encoding="utf-8"), source=path.name)
    except ConfigError:
        before = None
    while True:
        run_editor([*pick_editor(), str(path)])
        try:
            return before, parse(path.read_text(encoding="utf-8"), source=path.name)
        except ConfigError as exc:
            for problem in exc.problems:
                say(f"handy servers: {problem}")
            if not interactive:
                return before, None
            answer = ask("[e]dit again or [q]uit leaving it as is? ").strip().lower()
            if answer.startswith("q"):
                return before, None
