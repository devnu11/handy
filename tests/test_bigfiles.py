import pytest

from handy.tools import bigfiles


@pytest.fixture
def tree(tmp_path):
    (tmp_path / "small.txt").write_bytes(b"a" * 10)
    (tmp_path / "big.txt").write_bytes(b"a" * 1000)
    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / "medium.txt").write_bytes(b"a" * 100)
    hidden = tmp_path / ".hidden"
    hidden.mkdir()
    (hidden / "huge.txt").write_bytes(b"a" * 5000)
    (tmp_path / ".dotfile").write_bytes(b"a" * 5000)
    return tmp_path


def test_orders_by_size_descending(tree):
    results = bigfiles.largest_files(tree, limit=10)
    assert [path.name for _, path in results] == ["big.txt", "medium.txt", "small.txt"]


def test_respects_limit(tree):
    assert len(bigfiles.largest_files(tree, limit=2)) == 2


def test_skips_hidden_by_default(tree):
    names = {path.name for _, path in bigfiles.largest_files(tree, limit=10)}
    assert names == {"big.txt", "medium.txt", "small.txt"}


def test_include_hidden(tree):
    names = {path.name for _, path in bigfiles.largest_files(tree, limit=10, include_hidden=True)}
    assert {"huge.txt", ".dotfile"} <= names


def test_skips_symlinks(tmp_path):
    target = tmp_path / "real.txt"
    target.write_bytes(b"a" * 100)
    (tmp_path / "link.txt").symlink_to(target)
    assert [path.name for _, path in bigfiles.largest_files(tmp_path, limit=10)] == ["real.txt"]


def test_empty_directory(tmp_path):
    assert bigfiles.largest_files(tmp_path, limit=10) == []


def test_run_rejects_non_directory(tmp_path, capsys):
    from handy.cli import build_parser

    missing = tmp_path / "nope"
    args = build_parser().parse_args(["bigfiles", str(missing)])
    assert args._run(args) == 1
    assert "not a directory" in capsys.readouterr().out


def test_run_prints_sizes(tree, capsys):
    from handy.cli import build_parser

    args = build_parser().parse_args(["bigfiles", str(tree), "-n", "1"])
    assert args._run(args) == 0
    out = capsys.readouterr().out
    assert "big.txt" in out
    assert "1000 B" in out
