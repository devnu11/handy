import pytest

from handy.tools._servers.config import (
    _ENTRY,
    HEADER,
    TEMPLATE,
    ConfigError,
    Server,
    by_host,
    default_path,
    host_fingerprint,
    parse,
)


def test_parses_a_simple_entry():
    (server,) = parse("compute01:8000  docs  ~/site  python3 -m http.server $PORT\n")
    assert server == Server(
        "docs", "compute01", 8000, "python3 -m http.server $PORT", directory="~/site", line=1
    )


@pytest.mark.parametrize(
    ("directory", "workdir"),
    [("~", "./"), ("~/site", "./site"), ("site", "./site"), ("-x", "./-x"), ("/srv/a", "/srv/a")],
)
def test_workdir_is_relative_to_home_unless_absolute(directory, workdir):
    (server,) = parse(f"h:1 a {directory} cmd\n")
    assert server.directory == directory
    assert server.workdir == workdir


@pytest.mark.parametrize(
    ("column", "directory"),
    [('"~/my site"', "~/my site"), ("'/srv/my site'", "/srv/my site"), ("'it\"s'", 'it"s')],
)
def test_quoted_directory_may_contain_spaces(column, directory):
    (server,) = parse(f"h:1  a  {column}  run  it\n")
    assert server.directory == directory
    assert server.command == "run  it"


def test_backslash_continues_like_bash():
    text = "h:1 ui ~ cd ~/app && \\\n    npm run dev --port $PORT\n"
    (server,) = parse(text)
    assert server.command == "cd ~/app &&     npm run dev --port $PORT"
    assert server.line == 1


def test_comments_and_blank_lines_are_skipped_but_hash_in_command_is_kept():
    text = "# comment\n\n   # indented comment\nh:1 a ~ echo '#not a comment' # shell comment\n"
    (server,) = parse(text)
    assert server.command == "echo '#not a comment' # shell comment"


def test_continuation_takes_the_next_line_verbatim_even_if_it_looks_like_a_comment():
    (server,) = parse("h:1 a ~ one \\\n# two\n")
    assert server.command == "one # two"


def test_trailing_backslash_at_end_of_file():
    (server,) = parse("h:1 a ~ echo hi \\")
    assert server.command == "echo hi"


def test_user_at_host():
    (server,) = parse("dave@compute01:80 web ~ cmd\n")
    assert server.host == "dave@compute01"
    assert server.hostname == "compute01"
    assert server.address == "dave@compute01:80"


@pytest.mark.parametrize(
    ("line", "message"),
    [
        ("compute01 docs ~ cmd", "should be host:port"),
        (":80 docs ~ cmd", "should be host:port"),
        ("h:0 docs ~ cmd", "1 to 65535"),
        ("h:70000 docs ~ cmd", "1 to 65535"),
        ("h:http docs ~ cmd", "1 to 65535"),
        ("h:80 -docs ~ cmd", "name '-docs'"),
        ("h:80 .hidden ~ cmd", "name '.hidden'"),
        ("h:80 a/b ~ cmd", "name 'a/b'"),
        ("h'x:80 a ~ cmd", 'host "h\'x"'),
        ("h:80 docs ~/x", "expected 'host:port name directory command'"),
        ('h:80 docs "~/my site cmd', "expected 'host:port name directory command'"),
        ('h:80 docs "~/my site"cmd', "expected 'host:port name directory command'"),
        ('h:80 docs "" cmd', "directory is empty"),
        ("h:80 docs ~dave cmd", "directory '~dave'"),
    ],
)
def test_rejects_bad_entries(line, message):
    with pytest.raises(ConfigError) as excinfo:
        parse(line + "\n", source="servers")
    (problem,) = excinfo.value.problems
    assert problem.startswith("servers:1: ")
    assert message in problem


def test_duplicates_are_reported_with_line_numbers():
    text = "a:1 x ~ cmd\n\nb:2 x ~ cmd\na:1 y ~ cmd\n"
    with pytest.raises(ConfigError) as excinfo:
        parse(text)
    assert excinfo.value.problems == [
        "servers:3: duplicate name 'x' (first on line 1)",
        "servers:4: a:1 is already used on line 1",
    ]


def test_same_port_on_one_host_conflicts_even_with_different_users():
    with pytest.raises(ConfigError, match="already used"):
        parse("me@h:80 a ~ cmd\nyou@h:80 b ~ cmd\n")


def test_every_problem_is_reported_at_once():
    with pytest.raises(ConfigError) as excinfo:
        parse("bad\nh:0 a ~ cmd\nh:1 ok ~ cmd\n")
    assert [p.split(":")[1] for p in excinfo.value.problems] == ["1", "2"]


def test_template_is_valid_and_inert():
    assert parse(TEMPLATE) == []


def _uncommented_template() -> list[str]:
    """The template with the example entries, and the lines they continue onto,
    uncommented by deleting the leading #, as its instructions say."""
    lines, continuing = [], False
    for line in TEMPLATE.splitlines():
        if continuing or line.startswith("#compute"):
            line = line[1:]
        continuing = line.endswith("\\") and not line.startswith("#")
        lines.append(line)
    return lines


def _column_starts(line: str) -> list[int]:
    match = _ENTRY.fullmatch(line)
    return [match.start(i) for i in range(1, 5)]


def test_template_examples_parse_once_uncommented():
    servers = parse("\n".join(_uncommented_template()))
    assert [s.name for s in servers] == ["docs", "ui"]
    assert [s.directory for s in servers] == ["~/site", "~/my app"]
    assert servers[1].command.endswith("--strictPort")
    assert "\\" not in servers[1].command


def test_template_ends_with_a_header_the_examples_line_up_under():
    lines = _uncommented_template()
    header = lines[-1]
    assert tuple(header.lower().split()) == HEADER
    for example in (line for line in lines if line.startswith("compute")):
        assert _column_starts(example) == _column_starts(header)


@pytest.mark.parametrize(
    "header", ["HOST:PORT NAME DIRECTORY COMMAND", "  host:port\tname  directory   command  "]
)
def test_header_line_is_skipped_anywhere(header):
    servers = parse(f"{header}\nh:1  a  ~  cmd\n{header}\n")
    assert [s.name for s in servers] == ["a"]


def test_tabs_and_spaces_both_separate_columns():
    (server,) = parse("h:1\ta \t ~/x\t\trun\tit\n")
    assert (server.name, server.directory, server.command) == ("a", "~/x", "run\tit")


def test_fingerprint_tracks_definitions_not_order():
    a = Server("a", "h", 1, "one")
    b = Server("b", "h", 2, "two")
    assert host_fingerprint([a, b]) == host_fingerprint([b, a])
    assert host_fingerprint([a, b]) != host_fingerprint([a, Server("b", "h", 2, "changed")])
    assert host_fingerprint([a, b]) != host_fingerprint([a, Server("b", "h", 2, "two", "/srv")])


def test_by_host_groups_in_order():
    servers = parse("h1:1 a ~ x\nh2:1 b ~ x\nh1:2 c ~ x\n")
    assert {host: [s.name for s in group] for host, group in by_host(servers).items()} == {
        "h1": ["a", "c"],
        "h2": ["b"],
    }


def test_default_path_respects_xdg(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert default_path() == tmp_path / "handy" / "servers"
