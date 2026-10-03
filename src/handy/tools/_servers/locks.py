"""Local coordination: per-host locks and rate-limit stamps.

Locks are directories, because mkdir is atomic on local disks and NFS alike,
while flock is missing on macOS and unreliable over NFS. A home directory
shared over NFS therefore makes both the lock and the rate limit global
across machines, which is what we want.
"""

from __future__ import annotations

import os
import re
import shutil
import socket
import time
from collections.abc import Callable
from pathlib import Path

Clock = Callable[[], float]


def state_dir() -> Path:
    base = os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state"
    return Path(base) / "handy-servers"


def host_key(host: str) -> str:
    """A file-name-safe key for a host."""
    return re.sub(r"[^A-Za-z0-9_.@-]", "_", host)


class DirLock:
    """A mkdir-based lock. A lock older than `stale_after` seconds is presumed
    abandoned (its holder crashed) and broken."""

    def __init__(self, path: Path, stale_after: float = 300, clock: Clock = time.time):
        self.path = path
        self.stale_after = stale_after
        self.clock = clock
        self.held = False

    def acquire(self, wait: float = 0, poll: float = 0.25) -> bool:
        """Take the lock, retrying for up to `wait` seconds if it is held."""
        deadline = self.clock() + wait
        while True:
            if self._try_acquire():
                return True
            if self.clock() >= deadline:
                return False
            time.sleep(poll)

    def _try_acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        for _ in range(2):
            try:
                self.path.mkdir()
            except FileExistsError:
                if not self._break_if_stale():
                    return False
                continue
            info = f"{os.getpid()} {socket.gethostname()} {int(self.clock())}\n"
            (self.path / "owner").write_text(info, encoding="utf-8")
            self.held = True
            return True
        return False

    def release(self) -> None:
        if self.held:
            shutil.rmtree(self.path, ignore_errors=True)
            self.held = False

    def _break_if_stale(self) -> bool:
        try:
            age = self.clock() - self.path.stat().st_mtime
        except FileNotFoundError:
            return True  # released between our mkdir and stat; just retry
        if age < self.stale_after:
            return False
        # Rename first so only one breaker wins; then the loser's mkdir retry
        # simply competes for the fresh lock like anyone else.
        tomb = self.path.with_name(f"{self.path.name}.stale-{os.getpid()}")
        try:
            self.path.rename(tomb)
        except OSError:
            return True
        shutil.rmtree(tomb, ignore_errors=True)
        return True

    def __enter__(self) -> bool:
        return self.acquire()

    def __exit__(self, *exc) -> None:
        self.release()


def read_stamp(path: Path, clock: Clock = time.time) -> tuple[float, str] | None:
    """(age in seconds, fingerprint) of a stamp, or None if there isn't one."""
    try:
        age = clock() - path.stat().st_mtime
        fingerprint = path.read_text(encoding="utf-8").strip()
    except (FileNotFoundError, NotADirectoryError):
        return None
    return age, fingerprint


def fresh_age(
    path: Path, fingerprint: str, window: float, clock: Clock = time.time
) -> float | None:
    """The stamp's age if it is younger than `window` and matches the current
    config fingerprint; otherwise None, meaning a check is due."""
    stamp = read_stamp(path, clock)
    if stamp is None:
        return None
    age, recorded = stamp
    if recorded != fingerprint or age >= window or age < 0:
        return None
    return age


def write_stamp(path: Path, fingerprint: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}")
    tmp.write_text(fingerprint + "\n", encoding="utf-8")
    tmp.replace(path)


def clear_stamp(path: Path) -> None:
    path.unlink(missing_ok=True)
