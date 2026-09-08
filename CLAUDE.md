# CLAUDE.md

## What this is

A collection of small, unrelated Python utilities exposed as subcommands of one
`handy` CLI. Tools are independent — don't build coupling between them.

## Commands

```sh
uv sync                 # install deps into .venv
uv run pytest           # tests
uv run ruff check .     # lint
uv run ruff format .    # format
uv run handy <tool>     # run a tool
```

Always use `uv run`; there is no separately activated virtualenv.

## Layout

- `src/handy/cli.py` — the dispatcher. Discovers tool modules and builds the argparse tree.
- `src/handy/tools/` — one module per tool. Everything here becomes a subcommand.
- `src/handy/util.py` — helpers shared across tools. Not a tool.
- `tests/` — mirrors the source layout (`test_<module>.py`).

## Adding a tool

Drop a module in `src/handy/tools/`. There is no registry to update. The module
name becomes the subcommand name with underscores as hyphens. It must define
`run(args) -> int` and should define `HELP`; `add_arguments(parser)` is optional.
See the "Adding a tool" section of [README.md](README.md) for the template.

Conventions to follow when writing one:

- Put the real work in a plain, importable function; keep `run` to argument
  unpacking, printing, and the exit code. Tests should exercise the plain
  function, not argparse.
- Return a non-zero exit code on failure rather than raising or calling
  `sys.exit`. The dispatcher already handles `KeyboardInterrupt` and
  `BrokenPipeError`.
- Print results to stdout, errors to stdout prefixed `handy <tool>: `.
- Stay in the standard library unless a dependency clearly earns its place. Add
  runtime deps with `uv add`, dev-only deps with `uv add --dev`.
- Prefix a module with `_` to keep it out of the CLI (discovery skips those).

`tests/test_cli.py` enforces the tool contract across every discovered module,
so a new tool that breaks the shape fails the suite.
