import os
import threading
import time

from handy.tools._servers import locks


def test_lock_is_exclusive_until_released(tmp_path):
    first = locks.DirLock(tmp_path / "lock")
    second = locks.DirLock(tmp_path / "lock")
    assert first.acquire()
    assert not second.acquire()
    first.release()
    assert second.acquire()
    second.release()
    assert not (tmp_path / "lock").exists()


def test_lock_records_its_owner(tmp_path):
    with locks.DirLock(tmp_path / "lock") as held:
        assert held
        pid = (tmp_path / "lock" / "owner").read_text().split()[0]
        assert pid == str(os.getpid())


def test_stale_lock_is_broken(tmp_path):
    path = tmp_path / "lock"
    path.mkdir()
    old = time.time() - 1000
    os.utime(path, (old, old))
    lock = locks.DirLock(path, stale_after=300)
    assert lock.acquire()
    lock.release()


def test_fresh_lock_is_respected(tmp_path):
    (tmp_path / "lock").mkdir()
    assert not locks.DirLock(tmp_path / "lock", stale_after=300).acquire()


def test_waiting_acquire_gets_the_lock_once_released(tmp_path):
    holder = locks.DirLock(tmp_path / "lock")
    assert holder.acquire()
    threading.Timer(0.3, holder.release).start()
    waiter = locks.DirLock(tmp_path / "lock")
    assert waiter.acquire(wait=5, poll=0.05)
    waiter.release()


def test_waiting_acquire_gives_up(tmp_path):
    (tmp_path / "lock").mkdir()
    start = time.monotonic()
    assert not locks.DirLock(tmp_path / "lock").acquire(wait=0.3, poll=0.05)
    assert time.monotonic() - start < 2


def test_stamp_freshness(tmp_path):
    stamp = tmp_path / "stamps" / "h"
    assert locks.fresh_age(stamp, "fp", 600) is None
    locks.write_stamp(stamp, "fp")
    assert locks.fresh_age(stamp, "fp", 600) is not None
    assert locks.fresh_age(stamp, "other", 600) is None, "a config change invalidates it"
    later = time.time() + 601
    assert locks.fresh_age(stamp, "fp", 600, clock=lambda: later) is None


def test_stamp_age_is_reported(tmp_path):
    stamp = tmp_path / "h"
    locks.write_stamp(stamp, "fp")
    age = locks.fresh_age(stamp, "fp", 600, clock=lambda: stamp.stat().st_mtime + 125)
    assert age == 125


def test_clear_stamp_is_idempotent(tmp_path):
    stamp = tmp_path / "h"
    locks.write_stamp(stamp, "fp")
    locks.clear_stamp(stamp)
    locks.clear_stamp(stamp)
    assert locks.read_stamp(stamp) is None


def test_host_key_is_file_safe():
    assert locks.host_key("dave@compute-01.corp") == "dave@compute-01.corp"
    assert locks.host_key("a/b c") == "a_b_c"


def test_state_dir_respects_xdg(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    assert locks.state_dir() == tmp_path / "handy-servers"
