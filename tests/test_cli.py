import argparse

import pytest

from handy import cli


def test_discovers_at_least_one_tool():
    assert cli.discover_tools(), "no tools discovered in handy/tools/"


@pytest.mark.parametrize("tool", cli.discover_tools(), ids=lambda tool: tool.name)
def test_every_tool_is_well_formed(tool):
    """Contract check: adding a tool module shouldn't break the dispatcher."""
    assert tool.help, f"{tool.name} needs a HELP string or a module docstring"
    assert callable(tool.module.run)
    assert "_" not in tool.name, f"{tool.name} should use hyphens, not underscores"


def test_parser_exposes_each_tool_as_a_subcommand():
    """Every tool gets a subparser wired to its own run(). Checked without
    parsing an empty argv, since a tool is free to have required arguments."""
    choices = _subparser_choices(cli.build_parser())
    for tool in cli.discover_tools():
        assert tool.name in choices, f"{tool.name} has no subparser"
        assert choices[tool.name].get_default("_run") is tool.module.run


def _subparser_choices(parser):
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return action.choices
    raise AssertionError("the handy parser has no subcommands")


def test_no_command_prints_help_and_fails(capsys):
    assert cli.main([]) == 1
    assert "usage: handy" in capsys.readouterr().out


def test_version(capsys):
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["--version"])
    assert excinfo.value.code == 0
    assert "handy" in capsys.readouterr().out


def test_top_level_help_signs_off(capsys):
    """`handy --help` ends with the Red Green sign-off."""
    with pytest.raises(SystemExit):
        cli.main(["--help"])
    assert "Keep your stick on the ice." in capsys.readouterr().out
