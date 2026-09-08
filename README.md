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
| `conformance-report` | Report conformance pass rates against a minimum. |
| `perf-report` | Compare performance measurements against expected values. |
| `confluence-block` | Replace one named block of a Confluence page. |

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

## Conformance and performance reports

Two generators that read a JSON file of results and write a PNG. Both also take
`--summary` to dump the numbers they computed, and both exit non-zero when
something is below its floor, so a CI step can fail on the same data the graphic
is drawn from.

```sh
uv run handy conformance-report results.json -o conformance.png --minimum 0.999
uv run handy perf-report metrics.json -o perf.png --tolerance 0.05
```

### Why these don't chart pass rates

A pass rate is a bad primary scale. Suites here run from twenty tests to two
million, so a 99.9% floor means "no failures allowed" in one suite and "two
thousand" in another, and a 0.1% failure on a 100%-wide bar is under a pixel —
clean and broken look identical. So:

- The chart plots **error budget consumed** — `failed / ((1 - minimum) x tests)`
  — which is comparable across every suite size. 100% means the floor is exactly
  met.
- Percentages carry **as many decimals as the suite size needs**, worked out
  from `log10(tests)`. A single failure in 2.1M tests reads `99.99995%`, not
  `100%`.
- Any nonzero value is drawn at a **minimum visible length**, so one failure and
  none never look the same. Exact counts sit in the table beside the chart.
- Performance metrics are plotted as **deviation from their own expectation**,
  signed so worse is always to the right whether the metric wants to go up or
  down, with each metric's own tolerance marked on its row.

### Input shapes

Conformance — `failed` is derived when absent, and a bare list of suites works too:

```json
{
  "source": "nightly conformance",
  "minimum_pass_rate": 0.999,
  "suites": [
    {"name": "core", "total": 2100000, "passed": 2099878, "skipped": 0,
     "minimum_pass_rate": 0.9999}
  ]
}
```

Performance — a metric compares against `target` when it has one and `baseline`
otherwise, so fixed goals and last-run comparisons mix in one report:

```json
{
  "source": "perf run 4821",
  "tolerance": 0.05,
  "metrics": [
    {"name": "p99 latency", "value": 262, "unit": "ms", "target": 250,
     "tolerance": 0.1, "lower_is_better": true},
    {"name": "throughput", "value": 18400, "unit": " req/s", "baseline": 19000,
     "lower_is_better": false}
  ]
}
```

## Publishing several reports on one page

`confluence-publish` swaps a whole attachment; `confluence-block` rewrites one
delimited region of a page, so several reports can share a page with text people
wrote. Put markers in the page's storage format:

```html
<!-- handy:perf --><!-- /handy:perf -->
```

Then each job refreshes only its own block:

```sh
uv run handy confluence-block --page-id 123456 --name perf --image perf.png \
  --caption 'Run 4821'
```

`--init` appends the marker pair on the first run. If a later run reports the
markers missing, someone's rich-text edit stripped the HTML comments and the
block needs `--init` again.

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
