"""Running remote.sh on a host over ssh and reading back what it reports."""

from __future__ import annotations

import os
import shlex
import subprocess
from dataclasses import dataclass
from functools import cache
from importlib import resources

from handy.tools._servers.config import Server

# Never prompt for a password or host key: in a login hook a prompt would hang
# the background job forever. Fail fast instead and report the host unreachable.
SSH_OPTIONS = ["-o", "BatchMode=yes", "-o", "ConnectTimeout=5"]


@dataclass(frozen=True)
class Result:
    name: str
    state: str
    mode: str = ""
    pid: str = ""
    uptime: int | None = None
    detail: str = ""


class Unreachable(Exception):
    """ssh couldn't run the script at all."""


@cache
def remote_script() -> str:
    return resources.files(__package__).joinpath("remote.sh").read_text(encoding="utf-8")


def ssh_command() -> list[str]:
    """The ssh to run; HANDY_SERVERS_SSH overrides it (tests point it at a fake)."""
    return shlex.split(os.environ.get("HANDY_SERVERS_SSH", "ssh"))


def ssh_argv(host: str, *remote: str, tty: bool = False) -> list[str]:
    return [*ssh_command(), *SSH_OPTIONS, *(["-t"] if tty else []), host, *remote]


def build_script(calls: list[list[str]]) -> str:
    """remote.sh followed by one quoted function call per line."""
    lines = [remote_script()]
    lines += [" ".join(shlex.quote(str(arg)) for arg in call) for call in calls]
    return "\n".join(lines) + "\n"


def parse_results(output: str) -> list[Result]:
    """Pick RESULT lines out of the output; anything else (a chatty remote
    .bashrc, say) is ignored."""
    results = []
    for line in output.splitlines():
        if not line.startswith("RESULT\t"):
            continue
        fields = line.split("\t")[1:]
        fields += [""] * (6 - len(fields))
        name, state, mode, pid, uptime, detail = fields[:6]
        results.append(
            Result(
                name=name,
                state=state,
                mode=mode,
                pid=pid,
                uptime=int(uptime) if uptime.lstrip("-").isdigit() else None,
                detail=detail,
            )
        )
    return results


def run(host: str, calls: list[list[str]], timeout: float = 120) -> list[Result]:
    """Run calls on host in one ssh session. Raises Unreachable on ssh failure."""
    try:
        proc = subprocess.run(
            ssh_argv(host, "sh", "-s"),
            input=build_script(calls),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise Unreachable(f"no answer within {timeout:.0f}s") from exc
    except FileNotFoundError as exc:
        raise Unreachable(f"cannot run {ssh_command()[0]}: {exc}") from exc
    results = parse_results(proc.stdout)
    if proc.returncode == 255 or (proc.returncode != 0 and not results):
        lines = proc.stderr.strip().splitlines()
        raise Unreachable(lines[-1] if lines else f"ssh exited {proc.returncode}")
    return results


# One builder per remote function, so call shapes live in one place.


def status_call(server: Server) -> list[str]:
    return ["hs_status", server.name, str(server.port), server.hostname]


def start_call(server: Server, mode: str, resume: bool = False) -> list[str]:
    return [
        "hs_start",
        server.name,
        str(server.port),
        server.hostname,
        mode,
        server.command,
        str(int(resume)),
    ]


def stop_call(server: Server, hold: bool = True) -> list[str]:
    return ["hs_stop", server.name, server.hostname, str(int(hold))]


def forget_call(server: Server) -> list[str]:
    return ["hs_forget", server.name, server.hostname]


def http_call(server: Server) -> list[str]:
    return ["hs_http_ok", str(server.port)]


def logs_argv(server: Server, lines: int, follow: bool) -> tuple[list[str], str]:
    """argv and stdin for streaming a log straight to the terminal."""
    script = build_script([["hs_logs", server.name, server.hostname, str(lines), str(int(follow))]])
    return ssh_argv(server.host, "sh", "-s"), script


def attach_argv(server: Server) -> list[str]:
    return ssh_argv(server.host, "tmux", "attach", "-t", f"=handy-{server.name}", tty=True)


def read_log(server: Server, lines: int = 50, timeout: float = 30) -> str:
    argv, script = logs_argv(server, lines, follow=False)
    proc = subprocess.run(
        argv, input=script, capture_output=True, text=True, timeout=timeout, check=False
    )
    return proc.stdout
