"""Upload an image to an on-prem Confluence page, replacing the previous version.

Confluence keys attachments by filename, so re-uploading under the same name
replaces the image in place and the page markup never has to change. That is
what makes a weekly refresh a one-line cron job.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from handy.tools._atlassian import (
    AtlassianError,
    Client,
    client_from_env,
    upload_attachment,
)

HELP = "upload an image to a Confluence page, replacing the existing attachment"


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("image", type=Path, help="file to attach")
    parser.add_argument("--page-id", required=True, help="numeric id of the target page")
    parser.add_argument(
        "--name",
        help="attachment filename to write to (default: the image's own name)",
    )
    parser.add_argument("--url", help="Confluence base URL (default: $CONFLUENCE_URL)")
    parser.add_argument("--token", help="personal access token (default: $CONFLUENCE_TOKEN)")
    parser.add_argument(
        "--comment",
        default="Updated by handy confluence-publish",
        help="attachment version comment",
    )
    parser.add_argument(
        "--no-embed",
        dest="embed",
        action="store_false",
        help="don't append an image macro when the page doesn't reference the attachment yet",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="report what would be uploaded without writing to Confluence",
    )
    parser.add_argument("--timeout", type=float, default=60.0, help="seconds per request")


def embed_markup(filename: str) -> str:
    """Confluence storage-format markup that renders the attachment inline."""
    return (
        '<p><ac:image ac:align="center" ac:layout="center">'
        f'<ri:attachment ri:filename="{filename}" />'
        "</ac:image></p>"
    )


def ensure_embedded(client: Client, page_id: str, filename: str) -> bool:
    """Append the image macro if the page body doesn't reference the file yet.

    Returns True when the page was edited. Subsequent runs are no-ops, so the
    weekly job only ever swaps the attachment.
    """
    page = client.get(f"/rest/api/content/{page_id}", {"expand": "body.storage,version"})
    body = ((page.get("body") or {}).get("storage") or {}).get("value", "")
    if f'ri:filename="{filename}"' in body:
        return False
    client.put(
        f"/rest/api/content/{page_id}",
        {
            "id": page_id,
            "type": page.get("type", "page"),
            "title": page["title"],
            "version": {"number": page["version"]["number"] + 1, "minorEdit": True},
            "body": {
                "storage": {"value": body + embed_markup(filename), "representation": "storage"}
            },
        },
    )
    return True


def run(args: argparse.Namespace) -> int:
    if not args.image.is_file():
        print(f"handy confluence-publish: no such file: {args.image}")
        return 1
    filename = args.name or args.image.name
    if args.dry_run:
        size = args.image.stat().st_size
        print(f"would upload {args.image} ({size:,} bytes) as {filename} to page {args.page_id}")
        return 0
    try:
        client = client_from_env("confluence", args.url, args.token, args.timeout)
        attachment_id, replaced = upload_attachment(
            client, args.page_id, filename, args.image.read_bytes(), args.comment
        )
        edited = ensure_embedded(client, args.page_id, filename) if args.embed else False
    except (AtlassianError, KeyError) as error:
        print(f"handy confluence-publish: {error}")
        return 1
    verb = "replaced" if replaced else "attached"
    print(f"{verb} {filename} on page {args.page_id} (attachment {attachment_id})")
    if edited:
        print("added an image macro to the page body")
    return 0
