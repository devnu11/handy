"""The servers operations, as plain functions returning Outcomes.

Remote work is grouped by host so each host gets exactly one ssh session per
run, and hosts are handled in parallel so one dead host can't stall the rest.
"""

from __future__ import annotations

import contextlib
import random
import time
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from handy.tools._servers import locks, remote
from handy.tools._servers.config import Server, by_host, host_fingerprint

WINDOW = 600  # seconds: a host verified this recently is not checked again
LOCK_WAIT = 60  # seconds an explicit action waits for another session's check

# States that mean "fine" for status's exit code.
HEALTHY = {"running", "stopped"}
# States worth reporting even under --quiet.
NOTEWORTHY = {"started", "failed", "unreachable", "port-conflict", "unhealthy", "busy"}


@dataclass(frozen=True)
class Outcome:
    host: str
    state: str
    server: Server | None = None  # None for a host-level outcome (skipped, unreachable…)
    mode: str = ""
    pid: str = ""
    uptime: int | None = None
    detail: str = ""


def stamp_path(host: str) -> Path:
    return locks.state_dir() / "stamps" / locks.host_key(host)


def lock_path(host: str) -> Path:
    return locks.state_dir() / "locks" / locks.host_key(host)


def _per_host(
    servers: Iterable[Server], work: Callable[[str, list[Server]], list[Outcome]]
) -> list[Outcome]:
    grouped = by_host(list(servers))
    if not grouped:
        return []
    with ThreadPoolExecutor(max_workers=min(8, len(grouped))) as pool:
        batches = list(pool.map(lambda item: work(*item), grouped.items()))
    order = {server.name: i for i, server in enumerate(servers)}
    flat = [outcome for batch in batches for outcome in batch]
    return sorted(flat, key=lambda o: order.get(o.server.name, -1) if o.server else -1)


def _outcomes(host: str, servers: list[Server], results: list[remote.Result]) -> list[Outcome]:
    reported = {result.name: result for result in results}
    outcomes = []
    for server in servers:
        result = reported.get(server.name)
        if result is None:
            outcomes.append(
                Outcome(host, "failed", server, detail="no report from the remote host")
            )
            continue
        outcomes.append(
            Outcome(
                host,
                result.state,
                server,
                mode=result.mode,
                pid=result.pid,
                uptime=result.uptime,
                detail=result.detail,
            )
        )
    return outcomes


def _unreachable(host: str, servers: list[Server], error: remote.Unreachable) -> list[Outcome]:
    return [Outcome(host, "unreachable", server, detail=str(error)) for server in servers]


def status(servers: list[Server]) -> list[Outcome]:
    """Pure: asks each host, changes nothing, ignores the rate limit."""

    def work(host: str, group: list[Server]) -> list[Outcome]:
        try:
            results = remote.run(host, [remote.status_call(s) for s in group], timeout=60)
        except remote.Unreachable as exc:
            return _unreachable(host, group, exc)
        return _outcomes(host, group, results)

    return _per_host(servers, work)


def start(
    configured: list[Server],
    selected: list[Server],
    *,
    mode: str = "nohup",
    force: bool = False,
    resume: bool = False,
    clock: Callable[[], float] = time.time,
) -> list[Outcome]:
    """Start whatever is down.

    Rate-limited per host unless `force` or `resume`: `resume` means servers
    were named explicitly, which is deliberate in a way a blanket `start` (what
    the login hook runs) is not — and resuming a held server must never be
    refused because the host was checked a moment ago.

    `configured` is the whole config: a host's stamp fingerprint covers every
    server on it, and a stamp is only written when the run covered them all.
    """
    everything = by_host(configured)
    explicit = force or resume

    def work(host: str, group: list[Server]) -> list[Outcome]:
        on_host = everything.get(host, group)
        fingerprint = host_fingerprint(on_host)
        stamp = stamp_path(host)

        def skip_if_fresh() -> list[Outcome] | None:
            if explicit:
                return None
            age = locks.fresh_age(stamp, fingerprint, WINDOW, clock)
            if age is None:
                return None
            return [
                Outcome(
                    host,
                    "skipped",
                    detail=f"verified {_ago(age)} ago, skipping (--force to check now)",
                )
            ]

        skipped = skip_if_fresh()
        if skipped:
            return skipped
        lock = locks.DirLock(lock_path(host), clock=clock)
        if not lock.acquire(wait=LOCK_WAIT if explicit else 0):
            return [Outcome(host, "skipped", detail="being checked by another session, skipping")]
        try:
            skipped = skip_if_fresh()  # another session may have finished meanwhile
            if skipped:
                return skipped
            calls = [remote.start_call(s, mode, resume) for s in group]
            try:
                results = remote.run(host, calls, timeout=30 + 15 * len(group))
                outcomes = _outcomes(host, group, results)
            except remote.Unreachable as exc:
                # Stamped all the same, so a dead host doesn't cost every login
                # a connect timeout.
                outcomes = _unreachable(host, group, exc)
            if {s.name for s in group} == {s.name for s in on_host}:
                locks.write_stamp(stamp, fingerprint)
            return outcomes
        finally:
            lock.release()

    return _per_host(selected, work)


def stop(selected: list[Server], *, hold: bool = True) -> list[Outcome]:
    """Stop servers. With `hold`, they stay down until started by name.
    Clears each host's stamp so an immediate `start` isn't rate-limited."""

    def work(host: str, group: list[Server]) -> list[Outcome]:
        locks.clear_stamp(stamp_path(host))
        try:
            results = remote.run(
                host, [remote.stop_call(s, hold) for s in group], timeout=20 + 15 * len(group)
            )
        except remote.Unreachable as exc:
            return _unreachable(host, group, exc)
        return _outcomes(host, group, results)

    return _per_host(selected, work)


def restart(
    configured: list[Server], selected: list[Server], *, mode: str = "nohup"
) -> list[Outcome]:
    stopped = stop(selected, hold=False)
    failed = {o.server.name for o in stopped if o.server and o.state in {"failed", "unreachable"}}
    retry = [s for s in selected if s.name not in failed]
    started = start(configured, retry, mode=mode, force=True, resume=True)
    return [o for o in stopped if o.server and o.server.name in failed] + started


def _ago(seconds: float) -> str:
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    return f"{seconds // 60}m"


def smoke_test(host: str, *, mode: str = "nohup", say: Callable[[str], None] = print) -> bool:
    """Exercise a real host end to end with a throwaway http.server.
    Touches no stamps and leaves nothing behind."""

    def check(ok: bool, what: str) -> bool:
        say(f"  {'ok  ' if ok else 'FAIL'}  {what}")
        return ok

    server = None
    for _ in range(5):
        port = random.randint(20000, 60000)
        candidate = Server(
            name=f"handy-test-{port}",
            host=host,
            port=port,
            command="exec python3 -m http.server $PORT --bind 127.0.0.1",
        )
        try:
            (result,) = remote.run(host, [remote.status_call(candidate)], timeout=30)
        except remote.Unreachable as exc:
            return check(False, f"reach {host} over ssh: {exc}")
        if result.state == "down":
            server = candidate
            break
    if server is None:
        return check(False, "find a free port")
    say(f"testing {host} with {server.name} ({mode})")

    passed = True
    try:
        (result,) = remote.run(host, [remote.start_call(server, mode, resume=True)], timeout=60)
        passed &= check(
            result.state == "started", f"start -> {result.state} {result.detail}".rstrip()
        )
        state = result.state
        deadline = time.monotonic() + 15
        while state != "running" and time.monotonic() < deadline:
            time.sleep(1)
            (result,) = remote.run(host, [remote.status_call(server)], timeout=30)
            state = result.state
        passed &= check(state == "running", f"status -> {state}")
        (http,) = remote.run(host, [remote.http_call(server)], timeout=30)
        passed &= check(http.state == "ok", f"answers HTTP -> {http.state} {http.detail}".rstrip())
        log = remote.read_log(server)
        passed &= check("GET /" in log, "request shows up in logs")
        (result,) = remote.run(host, [remote.stop_call(server, hold=False)], timeout=60)
        passed &= check(result.state == "stopped", f"stop -> {result.state}")
        (result,) = remote.run(host, [remote.status_call(server)], timeout=30)
        passed &= check(result.state == "down", f"status after stop -> {result.state}")
    except remote.Unreachable as exc:
        passed = check(False, f"lost {host}: {exc}")
    finally:
        with contextlib.suppress(remote.Unreachable):
            remote.run(host, [remote.stop_call(server, hold=False), remote.forget_call(server)])
    say("passed" if passed else "FAILED")
    return passed
