"""Headless runner: no UI, short text report, exit codes."""

from __future__ import annotations

import signal
import sys
import time
from pathlib import Path

from . import __version__
from .core.options import RepairOptions
from .core.report import details_lines, headless_line
from .queue import QueueManager, State
from .queue.manager import max_workers

EXIT_INTERRUPTED = 130


def _options(force: bool, refine: bool, tries: int | None) -> RepairOptions:
    opts = RepairOptions(overwrite=force, refine=refine)
    if tries:
        opts.refine_max_tries = tries
    return opts


def run_headless(
    paths: list[str],
    jobs: int | None = None,
    timeout: float | None = None,
    force: bool = False,
    quiet: bool = False,
    debug: bool = False,
    refine: bool = False,
    refine_tries: int | None = None,
    out=None,
    err=None,
) -> int:
    out = out or sys.stdout
    err = err or sys.stderr
    if not paths:
        print("No files given. Usage: watertight --headless FILE [FILE ...]", file=err)
        return 2

    requested = 1 if jobs is None else jobs
    mgr = QueueManager(workers=requested, timeout=timeout, options=_options(force, refine, refine_tries))
    if jobs is not None and jobs > max_workers() and not quiet:
        print(f"Note: --jobs {jobs} is more than the limit; using {mgr.workers}.", file=err)

    interrupted = {"flag": False}

    def on_sigint(signum, frame):  # noqa: ARG001
        interrupted["flag"] = True

    old = None
    try:
        old = signal.signal(signal.SIGINT, on_sigint)
    except ValueError:  # not in the main thread (tests)
        pass

    t0 = time.monotonic()
    try:
        if not quiet:
            print(f"watertight {__version__}", file=out)
        mgr.add([Path(p) for p in paths])
        reported: set[int] = set()
        summary = None
        while not mgr.idle:
            if interrupted["flag"]:
                summary = mgr.interrupt_all()
                break
            mgr.poll()
            _report_new(mgr, reported, quiet, debug, out, err)
            time.sleep(0.05)
        _report_new(mgr, reported, quiet, debug, out, err)
    finally:
        if old is not None:
            signal.signal(signal.SIGINT, old)

    if summary is not None:
        print(
            f"Interrupted: {summary['kept']} finished, {summary['stopped']} stopped "
            f"(nothing saved), {summary['cancelled']} cancelled",
            file=out,
        )
        return EXIT_INTERRUPTED

    counts = mgr.counts()
    n = lambda *s: sum(counts.get(x, 0) for x in s)  # noqa: E731
    bits = [
        (n(State.DONE), "repaired"),
        (n(State.SKIPPED), "skipped"),
        (n(State.NEEDS_REVIEW), "needs review"),
        (n(State.FAILED), "failed"),
    ]
    text = ", ".join(f"{c} {label}" for c, label in bits if c) or "nothing to do"
    if not quiet:
        print(f"Summary: {text} · {time.monotonic() - t0:.1f} s", file=out)
    code = 0
    for j in mgr.jobs:
        if j.result is not None:
            code = max(code, j.result.exit_code)
    return code


def _report_new(mgr, reported, quiet, debug, out, err) -> None:
    for j in mgr.jobs:
        if not j.final or j.id in reported:
            continue
        reported.add(j.id)
        r = j.result
        if r is None:
            continue
        failed = r.status in ("failed",)
        if quiet and not failed:
            continue
        stream = err if failed and quiet else out
        print(headless_line(r), file=stream)
        for w in r.warnings:
            if w.startswith("WARNING"):
                print(f"  {w}", file=stream)
        for note in r.notes:
            if note.startswith("Stronger repair"):
                print(f"  {note}", file=stream)
        if debug and r.error_kind == "internal":
            for line in details_lines(r):
                print(f"  {line}", file=err)
