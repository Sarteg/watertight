"""The queue table: one row per job, with a text progress bar."""

from __future__ import annotations

from rich.text import Text
from textual.widgets import DataTable

from ..queue import Job, State

_ICON = {
    State.QUEUED: ("o", "dim"),
    State.WAITING: ("o", "yellow"),
    State.CHECKING: (">", "cyan"),
    State.REPAIRING: (">", "cyan"),
    State.VALIDATING: (">", "cyan"),
    State.DONE: ("✔", "green"),
    State.SKIPPED: ("✔", "green"),
    State.NEEDS_REVIEW: ("!", "yellow"),
    State.FAILED: ("✘", "red"),
    State.CANCELLED: ("-", "dim"),
}


_SHORT = {State.SKIPPED: "Skipped (watertight)", State.WAITING: "Waiting (large)"}


def bar(pct: int, width: int = 10) -> str:
    pct = max(0, min(100, pct))
    cells = pct / 100 * width
    full = int(cells)
    half = "▌" if cells - full >= 0.5 and full < width else ""
    return ("█" * full + half).ljust(width, "░")


def status_cell(job: Job) -> Text:
    icon, style = _ICON[job.state]
    label = job.label if job.running else _SHORT.get(job.state, job.state.value)
    return Text(f"{icon} {label}", style=style)


def progress_cell(job: Job) -> Text:
    style = {State.FAILED: "red", State.CANCELLED: "dim", State.NEEDS_REVIEW: "yellow",
             State.SKIPPED: "green", State.DONE: "green"}.get(job.state, "cyan")
    if job.state == State.QUEUED:
        style = "dim"
    return Text(f"{bar(job.pct)} {job.pct:>3}%", style=style)


def info_cell(job: Job) -> str:
    if job.running:
        m, s = divmod(int(job.elapsed), 60)
        return f"{m:02d}:{s:02d} {job.message}".strip()
    msg = job.message if job.final else ""
    if job.state == State.DONE and job.result and job.result.tier_used:
        msg = job.result.tier_used
    return msg if len(msg) <= 60 else msg[:57] + "..."


class QueueTable(DataTable):
    def on_mount(self) -> None:
        self.cursor_type = "row"
        self.zebra_stripes = True
        self.add_column("#", key="id", width=3)
        self.add_column("File", key="file", width=16)
        self.add_column("Status", key="status", width=22)
        self.add_column("Progress", key="progress", width=15)
        self.add_column("Info", key="info")

    def add_job(self, job: Job) -> None:
        self.add_row(
            str(job.id), _short(job.path.name), status_cell(job), progress_cell(job),
            info_cell(job), key=str(job.id),
        )

    def refresh_job(self, job: Job) -> None:
        key = str(job.id)
        try:
            self.update_cell(key, "status", status_cell(job))
            self.update_cell(key, "progress", progress_cell(job))
            self.update_cell(key, "info", info_cell(job))
        except Exception:
            pass  # row was cleared

    def remove_job(self, job_id: int) -> None:
        try:
            self.remove_row(str(job_id))
        except Exception:
            pass

    def highlighted_job_id(self) -> int | None:
        try:
            if self.row_count == 0:
                return None
            row_key = self.coordinate_to_cell_key(self.cursor_coordinate).row_key
            return int(row_key.value)
        except Exception:
            return None


def _short(name: str, width: int = 16) -> str:
    return name if len(name) <= width else name[: width - 3] + "..."
