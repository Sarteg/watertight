"""The watertight TUI: file explorer on the left, queue on the right, progress at the bottom."""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.css.query import NoMatches
from textual.widget import Widget
from textual.widgets import Footer, ProgressBar, Static

from .. import __version__
from ..core.io import cleanup_stale_tmp
from ..core.options import RepairOptions
from ..core.report import details_lines, refine_message
from ..engines import engines
from ..locations import list_locations
from ..queue import Job, QueueManager, State
from ..queue.manager import max_workers
from .dialogs import ConfirmScreen, GoToScreen, HelpScreen, LocationsScreen
from .explorer import Explorer
from .files import stl_files
from .queue_view import QueueTable

OPEN_TIMEOUT = 8.0  # seconds to wait for a folder (slow network drive) before giving up
MIN_WIDTH, MIN_HEIGHT = 80, 24
NARROW_BELOW = 100
BULK_CONFIRM_ABOVE = 200


def job_details(job: Job) -> list[str]:
    if job.result is not None:
        lines = details_lines(job.result)
        if job.state == State.CANCELLED:
            lines.append("Cancelled. Nothing was saved for this file.")
        return lines
    lines = [f"File:    {job.path}"]
    if job.state == State.CANCELLED:
        lines.append("Cancelled. Nothing was saved for this file.")
    elif job.running:
        lines.append(f"Step:    {job.label}")
        lines.append(f"Elapsed: {job.elapsed:.0f} s")
    else:
        lines.append(job.state.value)
    return lines


def fmt_time(s: float) -> str:
    s = int(s)
    return f"{s // 60:02d}:{s % 60:02d}"


class WatertightApp(App):
    CSS_PATH = "styles.tcss"
    TITLE = "watertight"
    ENABLE_COMMAND_PALETTE = False

    BINDINGS = [
        # Textual binds ctrl+c to a "press ctrl+q to quit" hint. We use it to interrupt.
        Binding("ctrl+c", "interrupt", "Stop all", priority=True, key_display="^C"),
        Binding("C", "interrupt", "Stop all", show=False),
        Binding("c", "cancel_one", "Cancel"),
        Binding("X", "cancel_queued", "Cancel queued", show=False),
        Binding("p", "pause", "Pause"),
        Binding("s", "resume", "Resume", show=False),
        Binding("x", "clear_finished", "Clear done", show=False),
        Binding("r", "retry", "Retry", show=False),
        Binding("plus", "more_workers", "+worker", show=False),
        Binding("equals_sign", "more_workers", "+worker", show=False),
        Binding("minus", "fewer_workers", "-worker", show=False),
        Binding("o", "toggle_overwrite", "Overwrite", show=False),
        Binding("f", "toggle_refine", "Keep trying", show=False),
        Binding("l", "locations", "Drives"),
        Binding("g", "goto", "Go to"),
        Binding("tab", "switch_pane", "Pane", priority=True),
        Binding("question_mark", "help", "Help"),
        Binding("q", "quit", "Quit"),
        Binding("ctrl+q", "quit", "Quit", show=False),
    ]

    def __init__(
        self,
        start_dir: Path,
        files: list[Path] | None = None,
        jobs: int | None = None,
        timeout: float | None = None,
        force: bool = False,
        bad_paths: list[Path] | None = None,
        refine: bool = False,
        refine_tries: int | None = None,
    ):
        super().__init__()
        self.start_dir = start_dir
        self.initial_files = files or []
        self.bad_paths = bad_paths or []
        self.mgr = QueueManager(workers=jobs, timeout=timeout,
                                options=RepairOptions(overwrite=force, refine=refine))
        if refine_tries:
            self.mgr.options.refine_max_tries = refine_tries
        self._notified: set[int] = set()
        self.selected: set[Path] = set()
        self._engines = engines()
        self._message = ""

    # ---- layout ------------------------------------------------------------------------------
    def compose(self) -> ComposeResult:
        yield Static(id="topbar")
        with Horizontal(id="panes"):
            with Vertical(id="explorer-pane"):
                yield Static("EXPLORER", classes="pane-title")
                yield Explorer(self.start_dir, id="explorer")
            with Vertical(id="queue-pane"):
                yield Static("QUEUE", classes="pane-title")
                yield QueueTable(id="queue")
        with Vertical(id="bottom"):
            with Horizontal(id="overall-row"):
                yield Static("Overall", id="overall-label")
                yield ProgressBar(total=1, show_eta=False, id="overall-bar")
                yield Static("", id="overall-text")
            with VerticalScroll(id="details-box"):
                yield Static("Select files, then press Enter.", id="details")
            yield Static("", id="message")
        yield Footer()
        yield Static(f"Please enlarge the window to at least {MIN_WIDTH}x{MIN_HEIGHT}.",
                     id="toosmall")

    def on_mount(self) -> None:
        self.set_interval(0.1, self._pump)
        self._apply_size()
        self._refresh_topbar()
        cleanup_stale_tmp(self.start_dir)
        self.query_one(Explorer).focus()
        if self.bad_paths:
            self.set_message("Not found: " + ", ".join(str(p) for p in self.bad_paths))
        if self.initial_files:
            self.run_paths(self.initial_files)

    def on_unmount(self) -> None:
        self.mgr.shutdown()

    def on_resize(self, event) -> None:
        self._apply_size()

    def _apply_size(self) -> None:
        w, h = self.size
        if (box := self._ui("#toosmall")) is not None:
            box.display = w < MIN_WIDTH or h < MIN_HEIGHT
        self.screen.set_class(w < NARROW_BELOW, "narrow")

    # ---- small UI helpers --------------------------------------------------------------------
    def _ui(self, selector: str, kind=Static):
        """A widget of the main screen, or None while the screen is starting or closing.
        The 0.1 s timer can fire in those moments, and a dialog may be on top."""
        try:
            return self.screen_stack[0].query_one(selector, kind)
        except (NoMatches, IndexError):
            return None

    def set_message(self, text: str) -> None:
        self._message = text
        if (w := self._ui("#message")) is not None:
            w.update(text)

    @property
    def table(self) -> QueueTable:
        return self.query_one(QueueTable)

    @property
    def explorer(self) -> Explorer:
        return self.query_one(Explorer)

    def _refresh_topbar(self) -> None:
        e = self._engines
        mark = lambda ok: "✔" if ok else "✘"  # noqa: E731
        txt = (
            f" watertight {__version__} │ workers {self.mgr.workers}/{max_workers()} │ "
            f"selected {len(self.selected)} │ overwrite {'on' if self.mgr.options.overwrite else 'off'}"
            f" │ keep trying {'on' if self.mgr.options.refine else 'off'}"
            f" │ trimesh{mark(e['trimesh'])} manifold3d{mark(e['manifold3d'])} "
            f"pymeshfix{mark(e['pymeshfix'])}"
        )
        if self.mgr.paused:
            txt += " │ PAUSED"
        if (bar := self._ui("#topbar")) is not None:
            bar.update(Text(txt, no_wrap=True, overflow="ellipsis"))

    def _refresh_overall(self) -> None:
        jobs = self.mgr.jobs
        total = len(jobs)
        finished = sum(1 for j in jobs if j.final)
        bar = self._ui("#overall-bar", ProgressBar)
        text = self._ui("#overall-text")
        if bar is None or text is None:
            return
        bar.update(total=max(1, total), progress=finished)
        c = self.mgr.counts()
        bits = [f"{finished}/{total} finished"]
        for st, label in ((State.DONE, "repaired"), (State.SKIPPED, "skipped"),
                          (State.NEEDS_REVIEW, "review"), (State.FAILED, "failed"),
                          (State.CANCELLED, "cancelled")):
            if c.get(st):
                bits.append(f"{c[st]} {label}")
        if self.mgr.batch_start is not None:
            end = self.mgr.batch_end or time.monotonic()
            bits.append(f"elapsed {fmt_time(end - self.mgr.batch_start)}")
            eta = self.mgr.eta_seconds()
            if eta is not None and not self.mgr.idle:
                bits.append(f"~{fmt_time(eta)} left")
        text.update(" · ".join(bits))

    def _show_details(self, job_id: int | None) -> None:
        job = self.mgr.get(job_id) if job_id is not None else None
        text = "\n".join(job_details(job)) if job else "Select files, then press Enter."
        if (box := self._ui("#details")) is not None:
            box.update(Text(text))

    # ---- selection ---------------------------------------------------------------------------
    def toggle_path(self, path: Path) -> None:
        if path.suffix.lower() == ".stl":  # no disk access: the explorer already listed it
            self.selected.symmetric_difference_update({path})
            self._selection_changed()
        else:  # a folder: scan it in a thread (a network share can be slow)
            self._scan(path, recursive=True, toggle=True)

    def select_folder(self, folder: Path, recursive: bool) -> None:
        self._scan(folder, recursive=recursive, toggle=False)

    def _scan(self, folder: Path, recursive: bool, toggle: bool) -> None:
        self.set_message(f"Scanning {folder} ...")
        show_hidden = self.explorer.show_all

        def work() -> None:
            files = stl_files(folder, recursive=recursive, show_hidden=show_hidden)
            self.call_from_thread(self._scan_done, files, recursive, toggle)

        self.run_worker(work, thread=True, group="scan", exit_on_error=False)

    def _scan_done(self, files: list[Path], recursive: bool, toggle: bool) -> None:
        if not files:
            self.set_message("No .stl files here." if not recursive else "No .stl files below.")
            return
        for f in files:  # remember sizes for the large-file rule (no disk access when queuing)
            try:
                self.explorer.sizes.setdefault(f, os.stat(f).st_size)
            except OSError:
                pass
        select = not (toggle and all(f in self.selected for f in files))
        self._bulk(files, select=select)

    def _bulk(self, files: list[Path], select: bool) -> None:
        def apply(ok: bool | None) -> None:
            if not ok:
                return
            if select:
                self.selected.update(files)
                self.set_message(f"Selected {len(files)} files ({len(self.selected)} total).")
            else:
                self.selected.difference_update(files)
            self._selection_changed()

        if select and len(files) > BULK_CONFIRM_ABOVE:
            self.push_screen(ConfirmScreen(f"Select {len(files)} files?"), apply)
        else:
            apply(True)

    def clear_selection(self) -> None:
        self.selected.clear()
        self._selection_changed()

    def _selection_changed(self) -> None:
        self.explorer.redraw()
        self._refresh_topbar()

    # ---- queue actions -----------------------------------------------------------------------
    def run_selected(self) -> None:
        paths = sorted(self.selected, key=lambda p: (str(p.parent).lower(), p.name.lower()))
        self.selected.clear()
        self._selection_changed()
        self.run_paths(paths)

    def run_paths(self, paths: list[Path]) -> None:
        added, dups = self.mgr.add(paths, probe=False, sizes=self.explorer.sizes)
        for job in added:
            self.table.add_job(job)
        msg = f"Added {len(added)} to the queue."
        if dups:
            msg += f" Ignored {len(dups)} already in the queue."
        self.set_message(msg)
        if self.table.row_count and self.table.highlighted_job_id() is None:
            pass
        self._refresh_topbar()

    def _refresh_all_rows(self) -> None:
        for job in self.mgr.jobs:
            self.table.refresh_job(job)

    def _pump(self) -> None:
        if self._ui("#queue", Widget) is None:  # the screen is starting or closing
            return
        changed = self.mgr.poll()
        for jid in changed:
            job = self.mgr.get(jid)
            if job is not None:
                self.table.refresh_job(job)
                self._notify_refine(job)
        self._ticks = getattr(self, "_ticks", 0) + 1
        if self._ticks % 10 == 0:  # once a second: keep the elapsed time of running files moving
            for job in self.mgr.running_jobs:
                self.table.refresh_job(job)
        self._refresh_overall()
        hid = self.table.highlighted_job_id()
        if hid is not None and (hid in changed or self.mgr.get(hid) and self.mgr.get(hid).running):
            self._show_details(hid)
        if changed:
            self._refresh_topbar()

    def _notify_refine(self, job) -> None:
        """Tell the user when keep-trying mode has stopped for a file."""
        r = job.result
        if job.final and r is not None and r.refine and job.id not in self._notified:
            self._notified.add(job.id)
            self.notify(f"{job.path.name}: {refine_message(r).replace('Keep trying: ', '')}",
                        title="Keep trying finished", timeout=15)

    def on_data_table_row_highlighted(self, event) -> None:
        try:
            self._show_details(int(event.row_key.value))
        except (TypeError, ValueError):
            pass

    def action_interrupt(self) -> None:
        if self.mgr.idle:
            self.set_message("Nothing to stop.")
            return
        s = self.mgr.interrupt_all()
        self._refresh_all_rows()
        self._refresh_overall()
        self.set_message(
            f"Stopped. {s['kept']} finished files kept · {s['stopped']} running stopped "
            f"(nothing saved) · {s['cancelled']} queued cancelled."
        )

    def action_cancel_one(self) -> None:
        jid = self.table.highlighted_job_id()
        if jid is None or not self.mgr.cancel(jid):
            self.set_message("Nothing to cancel on this row.")
            return
        job = self.mgr.get(jid)
        if job:
            self.table.refresh_job(job)
        self.set_message("Cancelled. Nothing was saved for that file.")

    def action_cancel_queued(self) -> None:
        n = self.mgr.cancel_queued()
        self._refresh_all_rows()
        self.set_message(f"Cancelled {n} queued files. Running files will finish.")

    def action_pause(self) -> None:
        self.mgr.pause()
        self._refresh_topbar()
        self.set_message("Paused. Running files finish; nothing new starts. Press s to resume.")

    def action_resume(self) -> None:
        self.mgr.resume()
        self._refresh_topbar()
        self.set_message("Resumed.")

    def action_clear_finished(self) -> None:
        ids = [j.id for j in self.mgr.jobs if j.final]
        for i in ids:
            self.table.remove_job(i)
        self.mgr.clear_finished()
        self._show_details(self.table.highlighted_job_id())
        self.set_message(f"Cleared {len(ids)} finished rows.")

    def action_retry(self) -> None:
        jid = self.table.highlighted_job_id()
        if jid is not None and self.mgr.retry(jid):
            self.table.refresh_job(self.mgr.get(jid))
            self.set_message("Queued again.")
        else:
            self.set_message("Only failed, cancelled or needs-review rows can be retried.")

    def action_more_workers(self) -> None:
        self.mgr.set_workers(self.mgr.workers + 1)
        self._refresh_topbar()
        self.set_message(f"Workers: {self.mgr.workers} (max {max_workers()}).")

    def action_fewer_workers(self) -> None:
        self.mgr.set_workers(self.mgr.workers - 1)
        self._refresh_topbar()
        self.set_message(f"Workers: {self.mgr.workers}.")

    def action_toggle_overwrite(self) -> None:
        self.mgr.options.overwrite = not self.mgr.options.overwrite
        self._refresh_topbar()
        self.set_message(
            "Overwrite ON: existing _repaired files are replaced."
            if self.mgr.options.overwrite else "Overwrite off."
        )

    def action_toggle_refine(self) -> None:
        self.mgr.options.refine = not self.mgr.options.refine
        self._refresh_topbar()
        self.set_message(
            "Keep trying ON: after a good repair, the app tries other settings until no better "
            "result exists. Applies to files you start next."
            if self.mgr.options.refine else "Keep trying off."
        )

    def action_switch_pane(self) -> None:
        narrow = self.screen.has_class("narrow")
        on_queue = self.table.has_focus or (narrow and self.screen.has_class("queue-active"))
        if on_queue:
            self.screen.remove_class("queue-active")
            self.explorer.focus()
        else:
            self.screen.add_class("queue-active")
            self.table.focus()

    def show_locations(self) -> None:
        def chosen(path: Path | None) -> None:
            if path is None:
                return
            if str(path) == "?goto":
                self.action_goto()
            else:
                self.open_location(path)

        self.push_screen(LocationsScreen(list_locations()), chosen)

    def action_locations(self) -> None:
        self.show_locations()

    def action_goto(self) -> None:
        def typed(text: str | None) -> None:
            if not text:
                return
            p = Path(text).expanduser()
            if not p.is_absolute() and not text.startswith("\\\\"):
                p = Path(self.explorer.path) / p
            self.open_location(p)

        self.push_screen(GoToScreen(), typed)

    def open_location(self, path: Path) -> None:
        """Open a folder in the explorer. The check runs in a thread with a time limit, so an
        offline or slow network drive cannot freeze the app."""
        self.set_message(f"Opening {path} ...")
        self.run_worker(self._open(path), exclusive=True, group="open")

    async def _open(self, path: Path) -> None:
        try:
            await asyncio.wait_for(asyncio.to_thread(os.listdir, path), timeout=OPEN_TIMEOUT)
        except asyncio.TimeoutError:
            self.set_message(f"No answer from {path} after {OPEN_TIMEOUT:.0f} s. Is the drive online?")
            return
        except OSError as exc:
            self.set_message(f"Cannot open {path}: {exc.strerror or exc}")
            return
        self.explorer.path = path
        self.set_message(f"Opened {path}")
        self.explorer.focus()

    def action_help(self) -> None:
        self.push_screen(HelpScreen())

    async def action_quit(self) -> None:
        if self.mgr.idle:
            self.exit(0)
            return

        def decide(ok: bool | None) -> None:
            if ok:
                self.mgr.interrupt_all()
                self.exit(0)

        self.push_screen(
            ConfirmScreen("Files are still being repaired or queued.\n"
                          "Quit now? Running files stop and nothing is saved for them."),
            decide,
        )


def run_tui(
    paths: list[Path],
    jobs: int | None = None,
    timeout: float | None = None,
    force: bool = False,
    debug: bool = False,
    refine: bool = False,
    refine_tries: int | None = None,
) -> int:
    start = Path.cwd()
    files: list[Path] = []
    bad: list[Path] = []
    found_dir = False
    for raw in paths:
        p = raw.expanduser()
        if p.is_dir():
            if not found_dir:
                start, found_dir = p.resolve(), True
        elif p.is_file():
            files.append(p)
            if not found_dir:
                start = p.resolve().parent
        else:
            bad.append(p)
    app = WatertightApp(start, files, jobs, timeout, force, bad, refine, refine_tries)
    app.run()
    return app.return_code or 0
