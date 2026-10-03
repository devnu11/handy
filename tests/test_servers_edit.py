from pathlib import Path

from handy.tools._servers import edit
from handy.tools._servers.config import Server


def test_editor_preference():
    present = {"nano", "pico", "vi"}.__contains__
    which = lambda name: name if present(name) else None  # noqa: E731
    assert edit.pick_editor({"VISUAL": "code -w", "EDITOR": "vim"}, which) == ["code", "-w"]
    assert edit.pick_editor({"EDITOR": "vim"}, which) == ["vim"]
    assert edit.pick_editor({"EDITOR": "  "}, which) == ["nano"]
    assert edit.pick_editor({}, lambda name: name if name == "pico" else None) == ["pico"]
    assert edit.pick_editor({}, lambda name: None) == ["vi"]


def test_diff_classifies_changes():
    old = [Server("keep", "h", 1, "a"), Server("move", "h", 2, "b"), Server("gone", "h2", 3, "c")]
    new = [Server("keep", "h", 1, "a"), Server("move", "h3", 2, "b"), Server("new", "h4", 4, "d")]
    changes = edit.diff(old, new)
    assert [s.name for s in changes.added] == ["new"]
    assert [s.name for s in changes.removed] == ["gone"]
    assert [(o.host, n.host) for o, n in changes.changed] == [("h", "h3")]
    assert changes.describe() == "restart move; start new; stop gone"
    assert changes.hosts() == {"h", "h2", "h3", "h4"}


def test_no_changes_is_falsy():
    servers = [Server("a", "h", 1, "x")]
    assert not edit.diff(servers, list(servers))


def _editor_writing(path: Path, *contents: str):
    queue = list(contents)

    def run(argv):
        assert argv[-1] == str(path)
        path.write_text(queue.pop(0))
        return 0

    return run


def test_valid_edit_returns_before_and_after(tmp_path):
    path = tmp_path / "servers"
    path.write_text("h:1 a old\n")
    before, after = edit.edit_until_valid(path, run_editor=_editor_writing(path, "h:1 a new\n"))
    assert before[0].command == "old"
    assert after[0].command == "new"


def test_invalid_edit_loops_until_fixed(tmp_path):
    path = tmp_path / "servers"
    path.write_text("h:1 a x\n")
    said = []
    _, after = edit.edit_until_valid(
        path,
        run_editor=_editor_writing(path, "broken\n", "h:1 a fixed\n"),
        ask=lambda prompt: "e",
        say=said.append,
    )
    assert after[0].command == "fixed"
    assert said == ["handy servers: servers:1: expected 'host:port name command'"]


def test_quitting_leaves_file_invalid(tmp_path):
    path = tmp_path / "servers"
    path.write_text("h:1 a x\n")
    _, after = edit.edit_until_valid(
        path, run_editor=_editor_writing(path, "broken\n"), ask=lambda p: "q", say=lambda m: None
    )
    assert after is None
    assert path.read_text() == "broken\n"


def test_non_interactive_never_prompts(tmp_path):
    path = tmp_path / "servers"
    path.write_text("h:1 a x\n")

    def ask(prompt):
        raise AssertionError("prompted without a terminal")

    _, after = edit.edit_until_valid(
        path,
        run_editor=_editor_writing(path, "broken\n"),
        ask=ask,
        interactive=False,
        say=lambda m: None,
    )
    assert after is None
