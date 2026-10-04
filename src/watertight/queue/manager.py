"""Queue manager: job states, FIFO order, parallel workers, pause, cancel, interrupt.
It has no UI code and no threads. The UI (or the headless runner) calls `poll()` often."""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from ..core.events import STEP_LABEL, STEP_PCT
from ..core.io import output_path_for, tmp_path_for
from ..core.options import RepairOptions
from ..core.report import DONE, FAILED, NEEDS_REVIEW, SKIPPED, RepairResult
from .memory import estimate_job_memory, memory_budget
from .workers import CTX, prepare_spawn, worker_main

LARGE_FILE_BYTES = 250_000_000  # ~5M binary faces: run alone to protect memory


class State(str, Enum):
    QUEUED = "Queued"
    WAITING = "Waiting (memory)"
    CHECKING = "Checking"
    REPAIRING = "Repairing"
    VALIDATING = "Validating"
    DONE = "Done"
    SKIPPED = "Skipped (already watertight)"
    NEEDS_REVIEW = "Needs review"
    FAILED = "Failed"
    CANCELLED = "Cancelled"


FINAL_STATES = {State.DONE, State.SKIPPED, State.NEEDS_REVIEW, State.FAILED, State.CANCELLED}
WAITING_STATES = {State.QUEUED, State.WAITING}
RUNNING_STATES = {State.CHECKING, State.REPAIRING, State.VALIDATING}

_STEP_STATE = {
    "load": State.CHECKING,
    "check": State.CHECKING,
    "validate": State.VALIDATING,
    "write": State.VALIDATING,
}
_RESULT_STATE = {DONE: State.DONE, SKIPPED: State.SKIPPED, NEEDS_REVIEW: State.NEEDS_REVIEW,
                 FAILED: State.FAILED}


def max_workers() -> int:
    """Leave one core free for the UI and the OS."""
    return max(1, (os.cpu_count() or 2) - 1)


def default_workers() -> int:
    return min(4, max_workers())


@dataclass
class Job:
    id: int
    path: Path
    state: State = State.QUEUED
    pct: int = 0
    label: str = "Queued"
    message: str = ""
    result: RepairResult | None = None
    size: int = 0
    started_at: float | None = None  # monotonic
    started_wall: float = 0.0
    ended_at: float | None = None
    proc: object | None = None
    conn: object | None = None
    out_path: Path | None = None
    tmp_path: Path | None = None
    large: bool = False
    mem: int = 0  # estimated peak memory, bytes
    _extra: dict = field(default_factory=dict)

    @property
    def final(self) -> bool:
        return self.state in FINAL_STATES

    @property
    def running(self) -> bool:
        return self.state in RUNNING_STATES

    @property
    def elapsed(self) -> float:
        if self.started_at is None:
            return 0.0
        return (self.ended_at or time.monotonic()) - self.started_at


def _key(path: Path) -> str:
    return os.path.normcase(os.path.abspath(path))


class QueueManager:
    def __init__(
        self,
        workers: int | None = None,
        timeout: float | None = None,
        options: RepairOptions | None = None,
        large_bytes: int = LARGE_FILE_BYTES,
        memory_limit: int | None = None,
    ):
        prepare_spawn()
        self.options = options or RepairOptions()
        self.timeout = timeout or None  # None / 0 = no limit: complex meshes can take long
        self.large_bytes = large_bytes
        self.memory_limit = memory_limit if memory_limit is not None else memory_budget()
        self.workers = self._clamp(workers if workers is not None else default_workers())
        self.paused = False
        self.jobs: list[Job] = []
        self._next_id = 1
        self.batch_start: float | None = None
        self.batch_end: float | None = None

    # ---- settings ---------------------------------------------------------------------------
    @staticmethod
    def _clamp(n: int) -> int:
        return max(1, min(int(n), max_workers()))

    def set_workers(self, n: int) -> int:
        self.workers = self._clamp(n)
        return self.workers

    # ---- adding jobs ------------------------------------------------------------------------
    def add(self, paths, probe: bool = True, sizes: dict | None = None) -> tuple[list[Job], list[Path]]:
        """Queue files. Returns (new jobs, duplicate paths that were ignored).

        probe=True checks the file now (fail fast; used by the headless runner). probe=False does
        no disk access at all, so a slow network share cannot freeze the TUI: the worker reports
        a missing file itself. `sizes` (path -> bytes) feeds the large-file rule in that case."""
        added: list[Job] = []
        dups: list[Path] = []
        active = {_key(j.path) for j in self.jobs if not j.final}
        for raw in paths:
            p = Path(raw).expanduser()
            p = Path(os.path.abspath(p))
            k = _key(p)
            if k in active:
                dups.append(p)
                continue
            job = Job(id=self._next_id, path=p)
            self._next_id += 1
            self.jobs.append(job)
            added.append(job)
            if self.idle_before_add():
                self.batch_start, self.batch_end = time.monotonic(), None
            if probe and not p.exists():
                self._fail_now(job, f"File not found: {p}", "input")
            elif probe and p.is_dir():
                self._fail_now(job, "Expected a file, got a directory", "input")
            else:
                if probe:
                    try:
                        job.size = p.stat().st_size
                    except OSError:
                        job.size = 0
                else:
                    job.size = int((sizes or {}).get(p, 0))
                job.large = job.size >= self.large_bytes
                job.mem = self._estimate(job.size)
                active.add(k)
        return added, dups

    def idle_before_add(self) -> bool:
        return self.batch_start is None or self.batch_end is not None

    def _fail_now(self, job: Job, message: str, kind: str) -> None:
        job.result = RepairResult(path=str(job.path), status=FAILED, message=message,
                                  error_kind=kind)
        self._finish(job, State.FAILED, message)

    # ---- state helpers ----------------------------------------------------------------------
    def _finish(self, job: Job, state: State, message: str = "") -> None:
        job.state = state
        job.pct = 100
        job.label = state.value
        job.message = message
        job.ended_at = time.monotonic()
        job.proc = None
        if job.conn is not None:
            try:
                job.conn.close()
            except Exception:
                pass
            job.conn = None
        if self.idle and self.batch_start is not None and self.batch_end is None:
            self.batch_end = time.monotonic()

    @property
    def running_jobs(self) -> list[Job]:
        return [j for j in self.jobs if j.running]

    @property
    def idle(self) -> bool:
        return not any(not j.final for j in self.jobs)

    def counts(self) -> dict[State, int]:
        out: dict[State, int] = {}
        for j in self.jobs:
            out[j.state] = out.get(j.state, 0) + 1
        return out

    def eta_seconds(self) -> float | None:
        """Only after at least 3 jobs finished by real work."""
        worked = [j for j in self.jobs if j.final and j.started_at and j.state != State.CANCELLED]
        if len(worked) < 3:
            return None
        avg = sum(j.elapsed for j in worked) / len(worked)
        remaining = sum(1 for j in self.jobs if not j.final)
        return avg * remaining / max(1, self.workers)

    # ---- polling ----------------------------------------------------------------------------
    def poll(self) -> set[int]:
        """Read worker messages, reap finished workers, enforce timeouts, start new jobs.
        Returns the ids of jobs that changed."""
        changed: set[int] = set()
        now = time.monotonic()
        for job in list(self.running_jobs):
            if self._drain(job):
                changed.add(job.id)
            if job.final:
                continue
            proc = job.proc
            if proc is not None and not proc.is_alive():
                if self._drain(job):  # last words
                    changed.add(job.id)
                if not job.final:
                    code = getattr(proc, "exitcode", None)
                    self._cleanup_tmp(job)
                    self._fail_now_running(job, f"worker crashed (exit code {code})", "internal")
                    changed.add(job.id)
            elif (self.timeout and job.started_at is not None
                  and now - job.started_at > self.timeout):
                self._kill([job])
                self._cleanup_tmp(job)
                self._fail_now_running(
                    job, f"timed out after {self.timeout:.0f} s during {job.label.lower()}", "internal")
                changed.add(job.id)
        if not self.paused:
            changed |= self._start_jobs()
        return changed

    def _fail_now_running(self, job: Job, message: str, kind: str) -> None:
        job.result = RepairResult(path=str(job.path), status=FAILED, message=message,
                                  error_kind=kind)
        self._finish(job, State.FAILED, message)

    def _drain(self, job: Job) -> bool:
        conn = job.conn
        changed = False
        if conn is None:
            return False
        while True:
            try:
                if not conn.poll(0):
                    break
                msg = conn.recv()
            except (EOFError, OSError):
                break
            changed = True
            kind = msg[0]
            if kind == "progress":
                step, message = msg[1], msg[2]
                job.pct = max(job.pct, STEP_PCT.get(step, job.pct))
                job.label = STEP_LABEL.get(step, step)
                job.state = _STEP_STATE.get(step, State.REPAIRING)
                job.message = message
            elif kind == "result":
                res = RepairResult.from_dict(msg[1])
                job.result = res
                self._finish(job, _RESULT_STATE[res.status], res.message)
                break
            elif kind == "error":
                job.result = RepairResult(path=str(job.path), status=FAILED,
                                          message="Internal error in worker", error_kind="internal",
                                          notes=[msg[1]])
                self._finish(job, State.FAILED, "Internal error in worker")
                break
        return changed

    def _estimate(self, size: int) -> int:
        return estimate_job_memory(size, 1.3 if self.options.refine else 1.0)

    @staticmethod
    def _used(jobs: list) -> int:
        return sum(j.mem for j in jobs)

    def _start_jobs(self) -> set[int]:
        changed: set[int] = set()
        running = self.running_jobs
        if any(j.large for j in running):
            return changed
        active_out = {_key(j.out_path) for j in running if j.out_path}
        for job in self.jobs:
            if len(running) >= self.workers:
                break
            if job.state not in WAITING_STATES:
                continue
            if running and (job.large or self._used(running) + job.mem > self.memory_limit):
                if job.state != State.WAITING:
                    job.state, job.label = State.WAITING, State.WAITING.value
                    changed.add(job.id)
                break  # keep FIFO: nothing starts behind a job that must wait for memory
            out = output_path_for(job.path)
            if _key(out) in active_out:
                continue  # same output path as a running job: wait for it
            self._spawn(job, out)
            running.append(job)
            active_out.add(_key(out))
            changed.add(job.id)
            if job.large:
                break
        return changed

    def _spawn(self, job: Job, out: Path) -> None:
        recv_conn, send_conn = CTX.Pipe(duplex=False)
        proc = CTX.Process(
            target=worker_main, args=(job.id, str(job.path), self.options, send_conn), daemon=True
        )
        job.out_path = out
        job.tmp_path = tmp_path_for(out, job.id)
        job.started_at = time.monotonic()
        job.started_wall = time.time()
        job.state, job.label, job.pct = State.CHECKING, "Starting", 2
        proc.start()
        send_conn.close()  # parent keeps only the read end, so EOF shows when the worker dies
        job.proc, job.conn = proc, recv_conn

    # ---- killing ----------------------------------------------------------------------------
    @staticmethod
    def _kill(jobs: list[Job]) -> None:
        procs = [j.proc for j in jobs if j.proc is not None]
        for p in procs:
            try:
                p.terminate()
            except Exception:
                pass
        deadline = time.monotonic() + 1.0
        for p in procs:
            p.join(max(0.0, deadline - time.monotonic()))
        for p in procs:
            if p.is_alive():
                try:
                    p.kill()
                except Exception:
                    pass
                p.join(1.0)

    @staticmethod
    def _cleanup_tmp(job: Job) -> None:
        if job.tmp_path is not None:
            try:
                job.tmp_path.unlink(missing_ok=True)
            except OSError:
                pass

    def _stop_running(self, jobs: list[Job]) -> None:
        """Kill workers, delete temp files, mark cancelled. Save nothing."""
        for j in jobs:
            self._drain(j)  # a result may have arrived just now: then the job is simply done
        todo = [j for j in jobs if not j.final]
        self._kill(todo)
        for j in todo:
            self._drain(j)
            if j.final:
                continue
            self._cleanup_tmp(j)
            # Rare race: the final rename happened just before the kill. Then it is Done.
            if j.out_path is not None and j.out_path.exists():
                try:
                    fresh = j.out_path.stat().st_mtime >= j.started_wall - 1
                except OSError:
                    fresh = False
                if fresh:
                    j.result = RepairResult(path=str(j.path), status=DONE,
                                            output_path=str(j.out_path),
                                            message="finished just before cancel")
                    self._finish(j, State.DONE, "finished just before cancel")
                    continue
            self._finish(j, State.CANCELLED, "cancelled")

    # ---- user actions -----------------------------------------------------------------------
    def get(self, job_id: int) -> Job | None:
        return next((j for j in self.jobs if j.id == job_id), None)

    def cancel(self, job_id: int) -> bool:
        job = self.get(job_id)
        if job is None or job.final:
            return False
        if job.state in WAITING_STATES:
            self._finish(job, State.CANCELLED, "cancelled")
        else:
            self._stop_running([job])
        return True

    def cancel_queued(self) -> int:
        n = 0
        for j in self.jobs:
            if j.state in WAITING_STATES:
                self._finish(j, State.CANCELLED, "cancelled")
                n += 1
        return n

    def interrupt_all(self) -> dict[str, int]:
        """Ctrl+C: stop every running job (save nothing), cancel every queued job.
        Finished jobs keep their outputs."""
        kept = sum(1 for j in self.jobs if j.state in (State.DONE, State.NEEDS_REVIEW))
        running = self.running_jobs
        self._stop_running(running)
        stopped = sum(1 for j in running if j.state == State.CANCELLED)
        queued = self.cancel_queued()
        return {"kept": kept, "stopped": stopped, "cancelled": queued}

    def pause(self) -> None:
        self.paused = True

    def resume(self) -> None:
        self.paused = False

    def retry(self, job_id: int) -> bool:
        job = self.get(job_id)
        if job is None or job.state not in (State.FAILED, State.CANCELLED, State.NEEDS_REVIEW):
            return False
        if any(not j.final and _key(j.path) == _key(job.path) for j in self.jobs if j is not job):
            return False
        job.state, job.label, job.pct, job.message = State.QUEUED, "Queued", 0, ""
        job.result, job.started_at, job.ended_at = None, None, None
        job.proc = job.conn = None
        if self.batch_end is not None:
            self.batch_start, self.batch_end = time.monotonic(), None
        return True

    def clear_finished(self) -> int:
        n = len([j for j in self.jobs if j.final])
        self.jobs = [j for j in self.jobs if not j.final]
        if not self.jobs:
            self.batch_start = self.batch_end = None
        return n

    def shutdown(self) -> None:
        self.interrupt_all()
