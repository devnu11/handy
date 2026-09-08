"""Find the largest files under a directory."""

from __future__ import annotations

import argparse
import os
import stat
from pathlib import Path

from handy.util import format_size

HELP = "list the largest files under a directory"


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "directory",
        nargs="?",
        default=".",
        type=Path,
        help="directory to scan (default: current directory)",
    )
    parser.add_argument(
        "-n",
        "--limit",
        type=int,
        default=20,
        help="how many files to show (default: 20)",
    )
    parser.add_argument(
        "-a",
        "--all",
        dest="include_hidden",
        action="store_true",
        help="include dotfiles and dot-directories",
    )


def largest_files(
    directory: Path, limit: int, include_hidden: bool = False
) -> list[tuple[int, Path]]:
    """Return up to `limit` (size, path) pairs, largest first."""
    sized: list[tuple[int, Path]] = []
    for root, dirnames, filenames in os.walk(directory, onerror=None):
        if not include_hidden:
            dirnames[:] = [d for d in dirnames if not d.startswith(".")]
            filenames = [f for f in filenames if not f.startswith(".")]
        for filename in filenames:
            path = Path(root, filename)
            try:
                info = path.lstat()
            except OSError:
                continue  # vanished or unreadable mid-walk
            if stat.S_ISLNK(info.st_mode):
                continue
            sized.append((info.st_size, path))
    sized.sort(key=lambda item: item[0], reverse=True)
    return sized[:limit]


def run(args: argparse.Namespace) -> int:
    if not args.directory.is_dir():
        print(f"handy bigfiles: not a directory: {args.directory}")
        return 1
    results = largest_files(args.directory, args.limit, args.include_hidden)
    if not results:
        print("no files found")
        return 0
    width = max(len(format_size(size)) for size, _ in results)
    for size, path in results:
        print(f"{format_size(size):>{width}}  {path}")
    return 0
