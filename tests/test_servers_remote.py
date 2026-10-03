import subprocess
from pathlib import Path

import pytest

from handy.tools._servers import remote
from handy.tools._servers.config import Server

FAKE_SSH = str(Path(__file__).parent / "fakes" / "ssh")

HOSTILE = [
    "plain",
    "it's got 'quotes'",
    'double "quotes" and $HOME and `backticks`',
    "semi; colon && rm -rf ~ || true",
    "new\nline",
    "tab\there",
    "$(echo injected)",
]


@pytest.mark.parametrize("value", HOSTILE)
def test_script_quoting_survives_a_real_shell(value):
    script = remote.build_script([["printf", "%s", value]])
    out = subprocess.run(["sh", "-s"], input=script, capture_output=True, text=True, check=True)
    assert out.stdout == value


def test_parse_results_ignores_noise_and_pads_missing_fields():
    output = (
        "Welcome to compute01!\n"
        "RESULT\tdocs\trunning\tnohup\t123\t45\t\n"
        "RESULT\tui\tdown\n"
        "random chatter\n"
    )
    docs, ui = remote.parse_results(output)
    assert docs == remote.Result("docs", "running", "nohup", "123", 45, "")
    assert ui == remote.Result("ui", "down", "", "", None, "")


def test_unreachable_host_raises(monkeypatch):
    monkeypatch.setenv("HANDY_SERVERS_SSH", FAKE_SSH)
    with pytest.raises(remote.Unreachable, match="Connection refused"):
        remote.run("unreachable-host", [["hs_status", "a", "1", "unreachable-host"]])


def test_missing_ssh_binary_is_unreachable(monkeypatch):
    monkeypatch.setenv("HANDY_SERVERS_SSH", "/nonexistent/ssh")
    with pytest.raises(remote.Unreachable, match="cannot run"):
        remote.run("h", [])


def test_ssh_never_prompts():
    argv = remote.ssh_argv("h", "sh", "-s")
    assert "BatchMode=yes" in argv
    assert argv[-3:] == ["h", "sh", "-s"]


def test_call_shapes():
    server = Server("docs", "dave@compute01", 8000, "run it")
    assert remote.start_call(server, "tmux", resume=True) == [
        "hs_start",
        "docs",
        "8000",
        "compute01",
        "tmux",
        "run it",
        "1",
    ]
    assert remote.stop_call(server) == ["hs_stop", "docs", "compute01", "1"]
    assert remote.stop_call(server, hold=False)[-1] == "0"
    assert remote.attach_argv(server)[-5:] == [
        "dave@compute01",
        "tmux",
        "attach",
        "-t",
        "=handy-docs",
    ]
    assert "-t" in remote.attach_argv(server)
