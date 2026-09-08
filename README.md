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
| `jira-query` | Run a JQL query and write the matching issues as JSON. |
| `jira-stats` | Bucketize those issues by component and age, with average ages. |
| `jira-infographic` | Render the stats as a PNG infographic. |
| `confluence-publish` | Upload an image to a Confluence page, replacing the old one. |
| `jira-report` | All four of the above in one go, for cron. |

## The Jira report pipeline

Four small tools that each do one step and hand JSON to the next, so any stage
can be swapped, inspected, or run on its own:

```sh
export JIRA_URL=https://jira.internal        # on-prem Jira (Server / Data Center)
export JIRA_TOKEN=...                        # personal access token
export CONFLUENCE_URL=https://wiki.internal
export CONFLUENCE_TOKEN=...

uv run handy jira-query 'project = FOO AND resolution = Unresolved' -o issues.json
uv run handy jira-stats issues.json -o stats.json
uv run handy jira-infographic stats.json -o report.png --title 'Open bugs'
uv run handy confluence-publish report.png --page-id 123456
```

Every stage reads stdin and writes stdout when you leave the paths off, so the
same thing works as a pipeline:

```sh
uv run handy jira-query 'project = FOO' | uv run handy jira-stats | \
  uv run handy jira-infographic -o report.png
```

Or run the whole thing at once — this is the form for a weekly cron entry:

```sh
uv run handy jira-report 'project = FOO AND resolution = Unresolved' \
  --out-dir ~/reports --name foo-weekly --page-id 123456
```

Notes on what the numbers mean:

- **Age** is days from issue creation to now, for every issue the query returns,
  resolved or not. Bands default to `0-7,8-30,31-90,90+` and are overridable
  with `--buckets`; the first matching band wins.
- **Components** are counted once per component, so an issue in two components
  adds to both and the component counts can exceed the issue total. Issues with
  no component are grouped under `Unassigned`.
- **Republishing** works because Confluence keys attachments by filename:
  uploading `report.png` again replaces the image in place, and the page markup
  never changes. The first run appends an image macro to the page if the page
  doesn't already reference the attachment (`--no-embed` to skip that).

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
