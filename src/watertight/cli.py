"""Command line entry point. Opens the TUI, or runs headless."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__

EPILOG = """\
examples:
  watertight                        open the TUI in the current folder
  watertight ~/prints               open the TUI in ~/prints
  watertight --headless a.stl b.stl --jobs 2     repair files with no UI

Repaired files are saved next to the original as <name>_repaired.stl.
Files that are already watertight are skipped. Originals are never changed.
Stronger repair: pip install "watertight[meshfix]"
"""


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="watertight",
        description="Repair STL files for 3D printing.",
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("paths", nargs="*", help="folder to open, or STL files to queue")
    p.add_argument("--headless", "--no-tui", action="store_true", help="no UI; print a text report")
    p.add_argument("--jobs", "-j", type=int, metavar="N",
                   help="parallel workers (default 4 in the TUI, 1 headless; max is cores - 1)")
    p.add_argument("--timeout", type=float, default=None, metavar="SEC",
                   help="fail a file that takes longer than this (default: no limit)")
    p.add_argument("--force", action="store_true", help="overwrite an existing _repaired file")
    p.add_argument("--refine", action="store_true",
                   help="keep trying other settings after a good repair, until no better result exists")
    p.add_argument("--refine-tries", type=int, metavar="N",
                   help="most extra tries per file in --refine mode (default 8)")
    p.add_argument("--quiet", "-q", action="store_true", help="print only errors")
    p.add_argument("--debug", action="store_true", help="show tracebacks and internal details")
    p.add_argument("--version", action="version", version=f"watertight {__version__}")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    interactive = sys.stdout.isatty() and sys.stdin.isatty()
    headless = args.headless or not interactive
    if headless:
        if not args.headless and not args.quiet:
            print("Not a terminal: running headless.", file=sys.stderr)
        from .headless import run_headless

        return run_headless(
            args.paths, args.jobs, args.timeout, args.force, args.quiet, args.debug,
            args.refine, args.refine_tries,
        )
    try:
        from .tui.app import run_tui
    except ImportError as exc:  # pragma: no cover
        print(f"Cannot start the TUI ({exc}). Use --headless.", file=sys.stderr)
        return 3
    return run_tui([Path(p) for p in args.paths], args.jobs, args.timeout, args.force, args.debug,
                   args.refine, args.refine_tries)
