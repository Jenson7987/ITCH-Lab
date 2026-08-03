"""Command-line entry point for the ITCH-Lab research package."""

import sys
from collections.abc import Sequence

from itchlab_research import __version__

_PROGRAM_NAME = "itchlab-research"
_HELP = f"""Offline research package for ITCH-Lab

Usage: {_PROGRAM_NAME} [--help] [--version]

Options:
  --help       Show this help text.
  --version    Show the application version.

Research workflow commands are not yet implemented.
"""


def main(argv: Sequence[str] | None = None) -> int:
    """Run the foundation CLI and return a process-compatible exit code."""
    arguments = list(sys.argv[1:] if argv is None else argv)

    if not arguments or arguments in (["--help"], ["-h"]):
        print(_HELP, end="")
        return 0

    if arguments == ["--version"]:
        print(f"{_PROGRAM_NAME} {__version__}")
        return 0

    print(f"{_PROGRAM_NAME}: unrecognised argument(s).", file=sys.stderr)
    print(f"Try '{_PROGRAM_NAME} --help' for usage.", file=sys.stderr)
    return 2
