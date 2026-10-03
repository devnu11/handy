"""The servers config: one `host:port name command` entry per logical line.

Physical lines ending in a backslash continue onto the next, as in bash.
Blank lines and lines starting with `#` are skipped between entries; a `#`
inside a command is left alone, since shell commands use it too.
"""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from pathlib import Path

# Names end up in remote file names and the tmux session name.
_NAME = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]*")
# Hosts are interpolated into a generated shell script and ssh argv.
_HOST = re.compile(r"(?:[A-Za-z0-9_.-]+@)?[A-Za-z0-9_][A-Za-z0-9_.-]*")

TEMPLATE = """\
# handy servers — one server per line:
#
#   host:port   name   command
#
# The command runs in a POSIX shell on the remote host, starting in $HOME, with
# SERVER and PORT exported. Chain steps yourself with &&, || or ;. End a line
# with a backslash to continue the command on the next line.
#
# Lines starting with # are comments. Uncomment and edit an example to start.

# Static files with Python's built-in web server:
#compute01:8000   docs   cd ~/site && python3 -m http.server $PORT --bind 0.0.0.0

# A Vue (Vite) dev server:
#compute02:5173   ui     cd ~/app && \\
#                        npm run dev -- --host 0.0.0.0 --port $PORT --strictPort
"""


class ConfigError(Exception):
    """One or more problems in a config file; `problems` lists them all."""

    def __init__(self, problems: list[str]):
        super().__init__("\n".join(problems))
        self.problems = problems


@dataclass(frozen=True)
class Server:
    name: str
    host: str  # as given, possibly user@host; this is what ssh connects to
    port: int
    command: str
    line: int = 0

    @property
    def hostname(self) -> str:
        """The host without any user@ prefix — exported to the command as SERVER."""
        return self.host.rpartition("@")[2]

    @property
    def address(self) -> str:
        return f"{self.host}:{self.port}"


def default_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config"
    return Path(base) / "handy" / "servers"


def load(path: Path) -> list[Server]:
    return parse(path.read_text(encoding="utf-8"), source=path.name)


def parse(text: str, source: str = "servers") -> list[Server]:
    """Parse config text. Raises ConfigError listing every problem found."""
    servers: list[Server] = []
    problems: list[str] = []
    names: dict[str, int] = {}
    addresses: dict[tuple[str, int], int] = {}

    for lineno, logical in _logical_lines(text):
        where = f"{source}:{lineno}"
        try:
            server = _parse_entry(logical, lineno)
        except ValueError as exc:
            problems.append(f"{where}: {exc}")
            continue
        if server.name in names:
            problems.append(
                f"{where}: duplicate name {server.name!r} (first on line {names[server.name]})"
            )
            continue
        key = (server.hostname, server.port)
        if key in addresses:
            problems.append(
                f"{where}: {server.hostname}:{server.port} is already used on line {addresses[key]}"
            )
            continue
        names[server.name] = lineno
        addresses[key] = lineno
        servers.append(server)

    if problems:
        raise ConfigError(problems)
    return servers


def _logical_lines(text: str):
    """Yield (first physical line number, joined text) for each entry."""
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        start = i + 1
        line = lines[i]
        i += 1
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        parts = []
        # A continuation always takes the next physical line verbatim.
        while line.endswith("\\"):
            parts.append(line[:-1])
            if i >= len(lines):
                line = ""
                break
            line = lines[i]
            i += 1
        parts.append(line)
        yield start, "".join(parts)


def _parse_entry(logical: str, lineno: int) -> Server:
    fields = logical.split(None, 2)
    if len(fields) < 3:
        raise ValueError("expected 'host:port name command'")
    address, name, command = fields
    host, sep, port_text = address.rpartition(":")
    if not sep or not host:
        raise ValueError(f"address {address!r} should be host:port")
    if not _HOST.fullmatch(host):
        raise ValueError(f"host {host!r} has characters that aren't allowed")
    if not port_text.isdigit() or not 1 <= int(port_text) <= 65535:
        raise ValueError(f"port {port_text!r} should be a number from 1 to 65535")
    if not _NAME.fullmatch(name):
        raise ValueError(
            f"name {name!r} may only use letters, digits, '_', '.' and '-', "
            "and can't start with '.' or '-' (it becomes part of file names)"
        )
    return Server(name=name, host=host, port=int(port_text), command=command.strip(), line=lineno)


def host_fingerprint(servers: list[Server]) -> str:
    """Hash of every entry on one host, so editing any of them invalidates its stamp."""
    digest = hashlib.sha256()
    for server in sorted(servers, key=lambda s: s.name):
        digest.update(f"{server.name}\0{server.host}\0{server.port}\0{server.command}\n".encode())
    return digest.hexdigest()[:16]


def by_host(servers: list[Server]) -> dict[str, list[Server]]:
    grouped: dict[str, list[Server]] = {}
    for server in servers:
        grouped.setdefault(server.host, []).append(server)
    return grouped
