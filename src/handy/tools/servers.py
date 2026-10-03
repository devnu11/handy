"""Start, stop and watch servers on remote hosts over ssh."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from handy.tools._servers import edit, ops, remote
from handy.tools._servers.config import TEMPLATE, ConfigError, Server, default_path, load

HELP = "start, stop and watch servers on remote hosts over ssh"

FAILED = {"failed", "unreachable", "port-conflict", "unhealthy", "busy"}


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--config", type=Path, help=f"config file (default: {_display(default_path())})"
    )
    actions = parser.add_subparsers(dest="action", metavar="<action>", required=True)

    actions.add_parser("init", help="create the config, seeded with commented-out examples")

    start = actions.add_parser(
        "start",
        help="start whatever isn't running (rate-limited per host)",
        description="Start every server that is down, leaving the rest alone. Without "
        f"names, a host checked in the last {ops.WINDOW // 60} minutes is skipped (this is "
        "what the login hook runs). Naming servers checks them now, and resumes any that "
        "were stopped on purpose.",
    )
    _names(start)
    _tmux(start)
    start.add_argument("--force", action="store_true", help="ignore the rate limit")
    start.add_argument(
        "--quiet", action="store_true", help="print only starts and failures (for login hooks)"
    )

    stop = actions.add_parser("stop", help="stop servers; they stay down until started by name")
    _names(stop)

    restart = actions.add_parser("restart", help="stop, then start")
    _names(restart)
    _tmux(restart)

    status = actions.add_parser("status", help="report state; changes nothing")
    _names(status)
    status.add_argument("--json", action="store_true", help="machine-readable output")

    logs = actions.add_parser("logs", help="show a server's log")
    logs.add_argument("name")
    logs.add_argument("-f", "--follow", action="store_true", help="keep printing new lines")
    logs.add_argument("-n", "--lines", type=int, default=100, help="lines to show (default: 100)")

    attach = actions.add_parser("attach", help="attach to a server running in tmux")
    attach.add_argument("name")

    actions.add_parser("edit", help="edit the config, validate it, offer to apply changes")

    test = actions.add_parser("test", help="smoke-test a host with a throwaway web server")
    test.add_argument("host")
    _tmux(test)


def _names(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("names", nargs="*", metavar="NAME", help="servers (default: all)")


def _tmux(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--tmux", action="store_true", help="run in tmux instead of nohup")


def run(args: argparse.Namespace) -> int:
    path = args.config or default_path()
    action = args.action
    if action == "init":
        return init(path)
    if action == "test":
        return 0 if ops.smoke_test(args.host, mode=_mode(args)) else 1

    if not path.exists():
        _error(f"no config at {_display(path)}; create one with `handy servers init`")
        return 1
    try:
        servers = load(path)
    except ConfigError as exc:
        for problem in exc.problems:
            _error(problem)
        return 1

    if action == "edit":
        return edit_config(path)
    if action in {"logs", "attach"}:
        server = _one(servers, args.name)
        if server is None:
            return 1
        return show_logs(server, args.lines, args.follow) if action == "logs" else attach(server)

    selected = _select(servers, args.names)
    if selected is None:
        return 1
    if not selected:
        if not getattr(args, "quiet", False):
            print(f"no servers configured in {_display(path)}")
        return 0

    if action == "status":
        return show_status(ops.status(selected), as_json=args.json)
    if action == "start":
        outcomes = ops.start(
            servers, selected, mode=_mode(args), force=args.force, resume=bool(args.names)
        )
        return report(outcomes, quiet=args.quiet)
    if action == "stop":
        return report(ops.stop(selected))
    if action == "restart":
        return report(ops.restart(servers, selected, mode=_mode(args)))
    raise AssertionError(f"unhandled action {action}")


def init(path: Path) -> int:
    if path.exists():
        print(f"{_display(path)} already exists; delete it first to start over")
        return 1
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(TEMPLATE, encoding="utf-8")
    print(f"created {_display(path)}; add servers with `handy servers edit`")
    return 0


def report(outcomes: list[ops.Outcome], quiet: bool = False) -> int:
    rows = []
    for outcome in outcomes:
        if quiet and outcome.state not in ops.NOTEWORTHY:
            continue
        if outcome.server is None:
            rows.append(f"{outcome.host}: {outcome.detail}")
            continue
        text = outcome.state
        if outcome.pid and outcome.state in {"started", "stopped", "running"}:
            text += f" (pid {outcome.pid})"
        if outcome.detail:
            text += f": {outcome.detail}"
        rows.append(_columns([outcome.server.name, outcome.server.address, text]))
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S ") if quiet else ""
    for row in _align(rows):
        print(stamp + row)
    return 1 if any(o.state in FAILED for o in outcomes) else 0


def show_status(outcomes: list[ops.Outcome], as_json: bool = False) -> int:
    if as_json:
        print(
            json.dumps(
                [
                    {
                        "name": o.server.name,
                        "host": o.server.host,
                        "port": o.server.port,
                        "state": o.state,
                        "mode": o.mode or None,
                        "pid": int(o.pid) if o.pid.isdigit() else None,
                        "uptime": o.uptime,
                        "detail": o.detail or None,
                    }
                    for o in outcomes
                    if o.server
                ],
                indent=2,
            )
        )
    else:
        rows = [_columns(["NAME", "HOST", "PORT", "STATE", "MODE", "PID", "UPTIME", ""])]
        for o in outcomes:
            if o.server is None:
                continue
            uptime = _duration(o.uptime) if o.uptime is not None else ""
            rows.append(
                _columns(
                    [
                        o.server.name,
                        o.server.host,
                        str(o.server.port),
                        o.state,
                        o.mode,
                        o.pid,
                        uptime,
                        o.detail,
                    ]
                )
            )
        for row in _align(rows):
            print(row.rstrip())
    return 0 if all(o.state in ops.HEALTHY for o in outcomes) else 1


def show_logs(server: Server, lines: int, follow: bool) -> int:
    argv, script = remote.logs_argv(server, lines, follow)
    return subprocess.run(argv, input=script, text=True, check=False).returncode


def attach(server: Server) -> int:
    (outcome,) = ops.status([server])
    if outcome.state == "unreachable":
        _error(f"{server.host}: {outcome.detail}")
        return 1
    if outcome.mode != "tmux":
        mode = outcome.mode or "not running"
        print(
            f"{server.name} is {mode}, not in tmux. Use `handy servers logs -f {server.name}`, "
            f"or `handy servers restart --tmux {server.name}` to debug it in tmux."
        )
        return 1
    return subprocess.call(remote.attach_argv(server))


def edit_config(path: Path) -> int:
    interactive = sys.stdin.isatty()
    before, after = edit.edit_until_valid(path, interactive=interactive)
    if after is None:
        return 1
    if before is None:
        print(f"{_display(path)} is valid again")
        return 0
    changes = edit.diff(before, after)
    for host in changes.hosts():
        ops.locks.clear_stamp(ops.stamp_path(host))
    if not changes:
        print("no server changes")
        return 0
    print(f"changes: {changes.describe()}")
    if not interactive or not input("apply them now? [y/N] ").strip().lower().startswith("y"):
        print("not applied; `handy servers start` will pick them up")
        return 0
    outcomes = []
    stale = changes.removed + [old for old, _ in changes.changed]
    if stale:
        outcomes += ops.stop(stale, hold=False)
    fresh = changes.added + [new for _, new in changes.changed]
    if fresh:
        outcomes += ops.start(after, fresh, force=True, resume=True)
    return report(outcomes)


def _mode(args: argparse.Namespace) -> str:
    return "tmux" if getattr(args, "tmux", False) else "nohup"


def _select(servers: list[Server], names: list[str]) -> list[Server] | None:
    if not names:
        return servers
    known = {s.name: s for s in servers}
    unknown = [n for n in names if n not in known]
    if unknown:
        _error(f"unknown server {', '.join(unknown)}; configured: {', '.join(known) or 'none'}")
        return None
    return [known[n] for n in dict.fromkeys(names)]


def _one(servers: list[Server], name: str) -> Server | None:
    selected = _select(servers, [name])
    return selected[0] if selected else None


def _columns(cells: list[str]) -> str:
    return "\t".join(cells)


def _align(rows: list[str]) -> list[str]:
    """Pad tab-separated rows into columns; rows without tabs pass through."""
    split = [row.split("\t") for row in rows]
    widths: dict[int, int] = {}
    for cells in split:
        if len(cells) > 1:
            for i, cell in enumerate(cells[:-1]):
                widths[i] = max(widths.get(i, 0), len(cell))
    return [
        "  ".join(cell.ljust(widths.get(i, 0)) for i, cell in enumerate(cells[:-1]))
        + "  "
        + cells[-1]
        if len(cells) > 1
        else cells[0]
        for cells in split
    ]


def _duration(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds}s"
    minutes, _ = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    days, hours = divmod(hours, 24)
    if days:
        return f"{days}d{hours}h"
    if hours:
        return f"{hours}h{minutes}m"
    return f"{minutes}m"


def _display(path: Path) -> str:
    home = str(Path.home())
    text = str(path)
    return "~" + text[len(home) :] if text.startswith(home + "/") else text


def _error(message: str) -> None:
    print(f"handy servers: {message}")
