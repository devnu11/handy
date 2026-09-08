"""Replace one named block of a Confluence page, leaving the rest untouched.

A page can carry several generated reports beside text people wrote. Each block
is delimited by HTML comments in the storage format:

    <!-- handy:perf -->  ...generated markup...  <!-- /handy:perf -->

Only what sits between the markers is rewritten, so a weekly job can refresh
its own block on a shared page without touching anyone else's. The markers must
already be on the page; `--init` appends an empty pair on the first run.

Note that some Confluence editors strip HTML comments when a page is edited in
the rich text editor. If a refresh reports missing markers, the block was
edited away and needs `--init` again.
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
from pathlib import Path

from handy.tools._atlassian import AtlassianError, Client, client_from_env, upload_attachment

HELP = "replace a named block of a Confluence page, leaving the rest alone"


class BlockMissingError(Exception):
    """The page has no marker pair with this name."""


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--page-id", required=True, help="numeric id of the target page")
    parser.add_argument("--name", required=True, help="block name, e.g. 'perf' or 'conformance'")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--image", type=Path, help="PNG to attach and show in the block")
    source.add_argument(
        "--markup", type=Path, help="file of Confluence storage format to place in the block"
    )
    parser.add_argument("--caption", default="", help="line of text under the image")
    parser.add_argument(
        "--init",
        action="store_true",
        help="append the marker pair if the page doesn't have it yet",
    )
    parser.add_argument("--url", help="Confluence base URL (default: $CONFLUENCE_URL)")
    parser.add_argument("--token", help="personal access token (default: $CONFLUENCE_TOKEN)")
    parser.add_argument(
        "--dry-run", action="store_true", help="print the markup instead of writing it"
    )
    parser.add_argument("--timeout", type=float, default=60.0, help="seconds per request")


def markers(name: str) -> tuple[str, str]:
    return f"<!-- handy:{name} -->", f"<!-- /handy:{name} -->"


def replace_block(body: str, name: str, content: str, init: bool = False) -> str:
    """Return `body` with the named block's contents swapped for `content`.

    Raises BlockMissingError when the markers aren't present and `init` is off,
    rather than appending a second copy of a block that may just have moved.
    """
    open_marker, close_marker = markers(name)
    start = body.find(open_marker)
    end = body.find(close_marker)
    if start == -1 or end == -1 or end < start:
        if not init:
            raise BlockMissingError(
                f"page has no {open_marker}...{close_marker} block; pass --init to add one"
            )
        return f"{body}\n{open_marker}\n{content}\n{close_marker}"
    return f"{body[: start + len(open_marker)]}\n{content}\n{body[end:]}"


def image_markup(filename: str, caption: str = "") -> str:
    """Storage-format markup showing an attachment, with an optional caption."""
    block = (
        '<p><ac:image ac:align="center" ac:layout="center">'
        f'<ri:attachment ri:filename="{filename}" />'
        "</ac:image></p>"
    )
    if caption:
        block += f'<p style="text-align: center;"><em>{caption}</em></p>'
    return block


def update_block(
    client: Client,
    page_id: str,
    name: str,
    content: str,
    init: bool = False,
) -> int:
    """Rewrite the named block and return the page's new version number."""
    page = client.get(f"/rest/api/content/{page_id}", {"expand": "body.storage,version"})
    body = ((page.get("body") or {}).get("storage") or {}).get("value", "")
    version = page["version"]["number"] + 1
    client.put(
        f"/rest/api/content/{page_id}",
        {
            "id": page_id,
            "type": page.get("type", "page"),
            "title": page["title"],
            "version": {"number": version, "minorEdit": True},
            "body": {
                "storage": {
                    "value": replace_block(body, name, content, init),
                    "representation": "storage",
                }
            },
        },
    )
    return version


def run(args: argparse.Namespace) -> int:
    source = args.image or args.markup
    if not source.is_file():
        print(f"handy confluence-block: no such file: {source}")
        return 1
    if args.markup:
        content = args.markup.read_text(encoding="utf-8").strip()
    else:
        content = image_markup(args.image.name, args.caption)

    if args.dry_run:
        open_marker, close_marker = markers(args.name)
        print(f"would write into {open_marker}...{close_marker} on page {args.page_id}:")
        print(content)
        return 0

    try:
        client = client_from_env("confluence", args.url, args.token, args.timeout)
        if args.image:
            attachment_id, replaced = upload_attachment(
                client,
                args.page_id,
                args.image.name,
                args.image.read_bytes(),
                comment=f"Updated by handy confluence-block on {datetime.now(UTC):%Y-%m-%d}",
            )
            verb = "replaced" if replaced else "attached"
            print(f"{verb} {args.image.name} (attachment {attachment_id})")
        version = update_block(client, args.page_id, args.name, content, args.init)
    except BlockMissingError as error:
        print(f"handy confluence-block: {error}")
        return 1
    except (AtlassianError, KeyError) as error:
        print(f"handy confluence-block: {error}")
        return 1
    print(f"updated block '{args.name}' on page {args.page_id} (now version {version})")
    return 0
