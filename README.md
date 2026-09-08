# handy

A collection of small Python tools that do handy things.

Every tool is a subcommand of a single `handy` CLI. Adding a new one means
dropping a file into [`src/handy/tools/`](src/handy/tools/) — no registry to
update, no wiring.

## Setup

```sh
uv sync
```

## Usage

```sh
uv run handy --help          # list every tool
uv run handy bigfiles ~/code # run one
```

To get a bare `handy` on your PATH:

```sh
uv tool install --editable .
```

## Tools

| Tool | What it does |
| --- | --- |
| `bigfiles` | List the largest files under a directory. |

## Adding a tool

Create `src/handy/tools/your_tool.py`. The module name becomes the subcommand,
with underscores turned into hyphens (`your_tool` → `handy your-tool`).

```python
"""One-line description."""

import argparse

HELP = "shown in `handy --help`"


def add_arguments(parser: argparse.ArgumentParser) -> None:
    """Optional. Declare the subcommand's flags and positionals."""
    parser.add_argument("path")


def run(args: argparse.Namespace) -> int:
    """Required. Return an exit code; returning None means success."""
    print(args.path)
    return 0
```

That's the whole contract. `handy/cli.py` discovers the module, builds its
subparser, and dispatches to `run`. Keep the heavy lifting in a plain function
that `run` calls so it stays testable without going through argparse.

Shared helpers that aren't tools go in `src/handy/util.py` — anything inside
`handy/tools/` is treated as a subcommand unless its name starts with `_`.

## Development

```sh
uv run pytest              # tests
uv run ruff check .        # lint
uv run ruff format .       # format
```

`tests/test_cli.py` checks every discovered tool against the contract above, so
a malformed tool module fails the suite rather than the CLI.
