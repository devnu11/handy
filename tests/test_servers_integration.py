"""End to end through a fake ssh that runs the remote side locally.

These start, kill and restart real http.server processes, so each test cleans
up after itself via the `env` fixture.
"""

import os
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

FAKE_SSH = str(Path(__file__).parent / "fakes" / "ssh")
SERVE = f"{shlex.quote(sys.executable)} -m http.server $PORT --bind 127.0.0.1"
needs_tmux = pytest.mark.skipif(shutil.which("tmux") is None, reason="tmux not installed")


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@dataclass
class Env:
    root: Path
    config: Path
    remote_home: Path
    ssh_log: Path
    vars: dict

    def write(self, text: str) -> None:
        self.config.write_text(text)

    def cli(self, *args: str, state: Path | None = None) -> subprocess.CompletedProcess:
        env = dict(self.vars)
        if state is not None:
            env["XDG_STATE_HOME"] = str(state)
        return subprocess.run(
            [sys.executable, "-m", "handy", "servers", "--config", str(self.config), *args],
            capture_output=True,
            text=True,
            env=env,
            timeout=120,
            check=False,
        )

    def ssh_calls(self) -> int:
        return len(self.ssh_log.read_text().splitlines()) if self.ssh_log.exists() else 0

    def pidfile(self, name: str) -> Path:
        return self.remote_home / ".local/state/handy-servers/hosts/localhost" / f"{name}.pid"

    def pid(self, name: str) -> int:
        return int(self.pidfile(name).read_text().split()[0])


@pytest.fixture
def env(tmp_path):
    vars = dict(os.environ)
    vars.update(
        HANDY_SERVERS_SSH=FAKE_SSH,
        FAKE_SSH_HOME=str(tmp_path / "remote"),
        FAKE_SSH_LOG=str(tmp_path / "ssh.log"),
        XDG_STATE_HOME=str(tmp_path / "state"),
    )
    e = Env(tmp_path, tmp_path / "servers", tmp_path / "remote", tmp_path / "ssh.log", vars)
    yield e
    if e.config.exists():
        e.cli("stop")
    if shutil.which("tmux"):
        for line in subprocess.run(
            ["tmux", "ls", "-F", "#{session_name}"], capture_output=True, text=True, check=False
        ).stdout.split():
            if line.startswith("handy-it-"):
                subprocess.run(["tmux", "kill-session", "-t", f"={line}"], check=False)


def wait_for(predicate, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.2)
    return False


def answers(port: int) -> bool:
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=1)
        return True
    except OSError:
        return False


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


def status_of(env: Env, name: str) -> str:
    out = env.cli("status", name).stdout.splitlines()[1]
    return out.split()[3]


def test_start_status_and_serve(env):
    port = free_port()
    env.write(f"localhost:{port} it-web {SERVE}\n")
    result = env.cli("start")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "started (pid" in result.stdout
    assert wait_for(lambda: answers(port))
    assert wait_for(lambda: status_of(env, "it-web") == "running")


def test_start_is_idempotent(env):
    port = free_port()
    env.write(f"localhost:{port} it-web {SERVE}\n")
    env.cli("start")
    first = env.pid("it-web")
    again = env.cli("start", "it-web")  # named, so it really checks
    assert "running" in again.stdout
    assert env.pid("it-web") == first


def test_crashed_server_is_restarted(env):
    port = free_port()
    env.write(f"localhost:{port} it-web {SERVE}\n")
    env.cli("start")
    first = env.pid("it-web")
    os.killpg(first, signal.SIGKILL)
    assert wait_for(lambda: not alive(first))
    assert status_of(env, "it-web") == "down"
    result = env.cli("start", "--force")
    assert "started" in result.stdout
    assert env.pid("it-web") != first
    assert wait_for(lambda: answers(port))


def test_stop_kills_everything_the_command_spawned(env):
    port = free_port()
    child = env.root / "child.pid"
    env.write(f"localhost:{port} it-web {SERVE} & echo $! > {child}; wait\n")
    env.cli("start")
    assert wait_for(child.exists)
    child_pid = int(child.read_text())
    assert wait_for(lambda: answers(port))
    result = env.cli("stop")
    assert "stopped" in result.stdout
    assert wait_for(lambda: not alive(child_pid)), "the server outlived its parent shell"
    assert not answers(port)


def test_foreign_process_on_the_port_is_not_trampled(env):
    port = free_port()
    with socket.socket() as squatter:
        squatter.bind(("127.0.0.1", port))
        squatter.listen()
        env.write(f"localhost:{port} it-web {SERVE}\n")
        result = env.cli("start")
        assert "port-conflict" in result.stdout
        assert result.returncode == 1
        assert not env.pidfile("it-web").exists()


def test_concurrent_logins_launch_exactly_once(env):
    port = free_port()
    launches = env.root / "launches"
    env.write(f"localhost:{port} it-web echo x >> {launches}; exec {SERVE}\n")
    with ThreadPoolExecutor(5) as pool:
        results = list(pool.map(lambda _: env.cli("start", "--quiet"), range(5)))
    assert all(r.returncode == 0 for r in results), [r.stdout for r in results]
    assert launches.read_text().count("x") == 1
    assert env.ssh_calls() == 1, "other sessions should have skipped the host"


def test_two_machines_racing_still_launch_once(env):
    """Separate local state (two machines) but one remote home: only the
    remote start lock stands between them, even with --force."""
    port = free_port()
    launches = env.root / "launches"
    env.write(f"localhost:{port} it-web echo x >> {launches}; exec {SERVE}\n")
    states = [env.root / "machine-a", env.root / "machine-b", env.root / "machine-c"]
    with ThreadPoolExecutor(3) as pool:
        list(pool.map(lambda s: env.cli("start", "--force", state=s), states))
    assert launches.read_text().count("x") == 1


def test_rate_limit_skips_and_says_so(env):
    port = free_port()
    env.write(f"localhost:{port} it-web {SERVE}\n")
    env.cli("start")
    calls = env.ssh_calls()

    again = env.cli("start")
    assert "verified" in again.stdout and "--force" in again.stdout
    assert env.ssh_calls() == calls

    quiet = env.cli("start", "--quiet")
    assert quiet.stdout == ""
    assert env.ssh_calls() == calls

    env.cli("start", "--force")
    assert env.ssh_calls() == calls + 1


def test_stop_holds_until_started_by_name(env):
    port = free_port()
    env.write(f"localhost:{port} it-web {SERVE}\n")
    env.cli("start")
    env.cli("stop")
    blanket = env.cli("start")  # what the login hook runs
    assert "stopped on purpose" in blanket.stdout
    assert not answers(port)
    named = env.cli("start", "it-web")
    assert "started" in named.stdout
    assert wait_for(lambda: answers(port))


def test_config_change_invalidates_the_rate_limit(env):
    port, other = free_port(), free_port()
    env.write(f"localhost:{port} it-web {SERVE}\n")
    env.cli("start")
    env.write(f"localhost:{port} it-web {SERVE}\nlocalhost:{other} it-two {SERVE}\n")
    result = env.cli("start")
    assert "verified" not in result.stdout
    assert "it-two" in result.stdout and "started" in result.stdout


def test_status_is_pure(env):
    port = free_port()
    env.write(f"localhost:{port} it-web {SERVE}\n")
    result = env.cli("status")
    assert result.returncode == 1
    assert "down" in result.stdout
    assert not env.pidfile("it-web").exists()
    assert not (env.root / "state" / "handy-servers" / "stamps").exists()


def test_logs_show_output(env):
    port = free_port()
    env.write(f"localhost:{port} it-web {SERVE}\n")
    env.cli("start")
    assert wait_for(lambda: answers(port))
    assert wait_for(lambda: "GET /" in env.cli("logs", "it-web").stdout)


def test_immediate_failure_is_reported(env):
    env.write(f"localhost:{free_port()} it-bad echo boom; exit 3\n")
    result = env.cli("start")
    assert result.returncode == 1
    assert "failed" in result.stdout and "boom" in result.stdout


def test_unreachable_host_is_reported_and_rate_limited(env):
    env.write("unreachable-box:80 it-web cmd\n")
    first = env.cli("start")
    assert "unreachable" in first.stdout and "Connection refused" in first.stdout
    assert first.returncode == 1
    second = env.cli("start")
    assert "verified" in second.stdout, "a dead host shouldn't cost every login a timeout"


def test_restart_replaces_the_process(env):
    port = free_port()
    env.write(f"localhost:{port} it-web {SERVE}\n")
    env.cli("start")
    first = env.pid("it-web")
    result = env.cli("restart")
    assert "started" in result.stdout
    assert env.pid("it-web") != first
    assert wait_for(lambda: not alive(first))


def test_init_creates_an_inert_config_and_refuses_to_overwrite(env):
    assert env.cli("init").returncode == 0
    assert "no servers configured" in env.cli("status").stdout
    again = env.cli("init")
    assert again.returncode == 1 and "already exists" in again.stdout


def test_bad_config_is_reported_with_line_numbers(env):
    env.write("# fine\nnot-an-entry\n")
    result = env.cli("status")
    assert result.returncode == 1
    assert "handy servers: servers:2:" in result.stdout


@needs_tmux
def test_tmux_mode(env):
    port = free_port()
    env.write(f"localhost:{port} it-tmux {SERVE}\n")
    result = env.cli("start", "--tmux")
    assert "started" in result.stdout
    assert wait_for(lambda: answers(port))
    line = env.cli("status").stdout.splitlines()[1].split()
    assert line[3:5] == ["running", "tmux"]
    assert (
        subprocess.run(["tmux", "has-session", "-t", "=handy-it-tmux"], check=False).returncode == 0
    )
    assert wait_for(lambda: "GET /" in env.cli("logs", "it-tmux").stdout)
    env.cli("stop")
    assert (
        subprocess.run(
            ["tmux", "has-session", "-t", "=handy-it-tmux"], capture_output=True, check=False
        ).returncode
        != 0
    )
    assert not answers(port)


def test_attach_explains_nohup_mode(env):
    port = free_port()
    env.write(f"localhost:{port} it-web {SERVE}\n")
    env.cli("start")
    result = env.cli("attach", "it-web")
    assert result.returncode == 1
    assert "logs -f it-web" in result.stdout
