"""Command-line dispatcher.

Tools live in `handy/tools/`. Each module in that package is discovered
automatically and exposed as a subcommand. A tool module must define:

    HELP = "one-line description shown in `handy --help`"

    def run(args) -> int:
        ...

and may optionally define:

    def add_arguments(parser) -> None:
        parser.add_argument(...)

The subcommand name is the module name with underscores turned into hyphens,
unless the module sets `NAME` explicitly.
"""

from __future__ import annotations

import argparse
import importlib
import pkgutil
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from types import ModuleType

from handy import __version__, tools


@dataclass(frozen=True)
class Tool:
    name: str
    help: str
    module: ModuleType


def discover_tools() -> list[Tool]:
    """Import every tool module and return them sorted by subcommand name."""
    found = [_load(info.name) for info in _tool_modules()]
    return sorted(found, key=lambda tool: tool.name)


def _tool_modules() -> Iterator[pkgutil.ModuleInfo]:
    for info in pkgutil.iter_modules(tools.__path__):
        if not info.name.startswith("_"):
            yield info


def _load(module_name: str) -> Tool:
    module = importlib.import_module(f"{tools.__name__}.{module_name}")
    if not hasattr(module, "run"):
        raise TypeError(f"tool module {module.__name__!r} does not define run(args)")
    name = getattr(module, "NAME", module_name.replace("_", "-"))
    help_text = getattr(module, "HELP", "") or _first_line(module.__doc__)
    return Tool(name=name, help=help_text, module=module)


def _first_line(docstring: str | None) -> str:
    return docstring.strip().splitlines()[0] if docstring and docstring.strip() else ""


def build_parser(found: list[Tool] | None = None) -> argparse.ArgumentParser:
    found = discover_tools() if found is None else found
    parser = argparse.ArgumentParser(
        prog="handy",
        description="A collection of small Python tools that do handy things.",
    )
    parser.add_argument("--version", action="version", version=f"handy {__version__}")
    subparsers = parser.add_subparsers(dest="command", metavar="<tool>")
    for tool in found:
        subparser = subparsers.add_parser(
            tool.name,
            help=tool.help,
            description=tool.help or None,
        )
        add_arguments = getattr(tool.module, "add_arguments", None)
        if add_arguments is not None:
            add_arguments(subparser)
        subparser.set_defaults(_run=tool.module.run)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    run = getattr(args, "_run", None)
    if run is None:
        parser.print_help()
        return 1
    try:
        return run(args) or 0
    except KeyboardInterrupt:
        return 130
    except BrokenPipeError:
        # Downstream of something like `| head`; exit quietly.
        return 0


if __name__ == "__main__":
    sys.exit(main())
