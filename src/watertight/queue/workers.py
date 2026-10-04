"""Worker process entry point. One process per running job, so cancel = kill that process.
Each worker has its own pipe back to the manager: killing one worker can never block another
(a shared multiprocessing.Queue can deadlock if a writer is killed mid-write)."""

from __future__ import annotations

import multiprocessing as mp
import os
import signal
import threading
import time
import traceback
from pathlib import Path

CTX = mp.get_context("spawn")  # same behaviour on macOS, Linux and Windows


def prepare_spawn() -> None:
    """Start multiprocessing's helper process NOW. Textual replaces sys.stderr with an object
    that has no real file descriptor, and the helper cannot start after that."""
    try:
        from multiprocessing import resource_tracker

        resource_tracker.ensure_running()
    except Exception:
        pass


def _watch_parent(tmp_path: Path) -> None:
    parent = mp.parent_process()
    if parent is None:
        return
    while True:
        time.sleep(0.5)
        if not parent.is_alive():
            try:
                tmp_path.unlink(missing_ok=True)
            finally:
                os._exit(1)


def worker_main(job_id: int, path: str, options, conn) -> None:
    """Runs in a child process. Sends ('progress', step, message), then ('result', dict)."""
    from ..core.io import output_path_for, tmp_path_for
    from ..core.pipeline import repair

    try:
        signal.signal(signal.SIGINT, signal.SIG_IGN)  # Ctrl+C is handled by the manager
    except (ValueError, OSError):
        pass
    tmp = tmp_path_for(output_path_for(Path(path)), job_id)
    threading.Thread(target=_watch_parent, args=(tmp,), daemon=True).start()

    def emit(step: str, message: str = "") -> None:
        conn.send(("progress", step, message))

    try:
        result = repair(path, options, emit, job_id)
        conn.send(("result", result.to_dict()))
    except BaseException:  # never leave the manager waiting
        try:
            conn.send(("error", traceback.format_exc(limit=5)))
        except Exception:
            pass
    finally:
        try:
            conn.close()
        except Exception:
            pass
