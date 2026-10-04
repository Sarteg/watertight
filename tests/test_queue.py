import os
import subprocess
import sys
import time
from pathlib import Path

import psutil
import pytest

from watertight.queue import QueueManager, State
from watertight.queue import manager as mgr_mod

from .conftest import sha256


def run_until(mgr: QueueManager, cond, timeout=30.0):
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        mgr.poll()
        if cond():
            return True
        time.sleep(0.03)
    return False


def wait_idle(mgr, timeout=40.0):
    assert run_until(mgr, lambda: mgr.idle, timeout), [(j.path.name, j.state) for j in mgr.jobs]


def children():
    """Live worker processes (not the multiprocessing resource tracker, not zombies)."""
    out = []
    for c in psutil.Process().children(recursive=True):
        try:
            if "spawn_main" in " ".join(c.cmdline()) and c.status() != psutil.STATUS_ZOMBIE:
                out.append(c)
        except psutil.Error:
            pass
    return out


def leftovers(folder: Path):
    return sorted(p.name for p in folder.iterdir() if "_repaired" in p.name or p.suffix == ".tmp")


def test_batch_states_and_outputs(fx):
    m = QueueManager(workers=3)
    paths = [fx.hole_cube(), fx.clean_cube(), fx.not_stl(), fx.flipped_cube()]
    m.add(paths)
    wait_idle(m)
    states = {j.path.name: j.state for j in m.jobs}
    assert states == {
        "hole.stl": State.DONE,
        "cube.stl": State.SKIPPED,
        "bad.stl": State.FAILED,
        "flipped.stl": State.DONE,
    }
    assert leftovers(fx.folder) == ["flipped_repaired.stl", "hole_repaired.stl"]
    assert all(j.pct == 100 for j in m.jobs)


def test_duplicates_ignored_and_bad_paths_fail_fast(fx, tmp_path):
    m = QueueManager(workers=1)
    p = fx.hole_cube()
    added, dups = m.add([p, p])
    assert len(added) == 1 and len(dups) == 1
    added, _ = m.add([tmp_path / "nope.stl", tmp_path])
    assert [j.state for j in added] == [State.FAILED, State.FAILED]
    assert "File not found" in added[0].message
    wait_idle(m)


def test_worker_limits(monkeypatch):
    for cores, default, top in ((10, 4, 9), (8, 4, 7), (4, 3, 3), (2, 1, 1), (1, 1, 1)):
        monkeypatch.setattr(os, "cpu_count", lambda c=cores: c)
        m = QueueManager()
        assert (m.workers, mgr_mod.max_workers()) == (default, top)
        assert m.set_workers(99) == top
        assert m.set_workers(0) == 1


def test_queue_runs_in_parallel(fx, monkeypatch):
    monkeypatch.setenv("WATERTIGHT_TEST_FAULT", "sleep:check:1.5")
    m = QueueManager(workers=3)
    m.add([fx.clean_cube(f"c{i}.stl") for i in range(3)])
    peak = 0
    t0 = time.monotonic()
    while not m.idle and time.monotonic() - t0 < 30:
        m.poll()
        peak = max(peak, len(m.running_jobs))
        time.sleep(0.02)
    assert peak == min(3, mgr_mod.max_workers()) or peak >= 2


def test_crash_fails_only_that_job(fx, monkeypatch):
    monkeypatch.setenv("WATERTIGHT_TEST_FAULT", "crash:tier1")
    m = QueueManager(workers=1)
    m.add([fx.hole_cube(), fx.clean_cube()])
    wait_idle(m)
    a, b = m.jobs
    assert a.state == State.FAILED and "worker crashed" in a.message
    assert b.state == State.SKIPPED  # next job still ran
    assert leftovers(fx.folder) == []


def test_timeout(fx, monkeypatch):
    monkeypatch.setenv("WATERTIGHT_TEST_FAULT", "sleep:check:30")
    m = QueueManager(workers=1, timeout=1.0)
    m.add([fx.clean_cube()])
    wait_idle(m)
    assert m.jobs[0].state == State.FAILED and "timed out" in m.jobs[0].message
    assert run_until(m, lambda: not children(), 5)


def test_pause_starts_nothing(fx):
    m = QueueManager(workers=2)
    m.pause()
    m.add([fx.clean_cube()])
    for _ in range(5):
        m.poll()
        time.sleep(0.02)
    assert m.jobs[0].state == State.QUEUED
    m.resume()
    wait_idle(m)
    assert m.jobs[0].state == State.SKIPPED


def test_cancel_running_job_in_write_step_leaves_nothing(fx, monkeypatch):
    monkeypatch.setenv("WATERTIGHT_TEST_FAULT", "sleep:rename:30")  # temp file exists, no rename
    m = QueueManager(workers=1)
    p = fx.hole_cube()
    before = sha256(p)
    m.add([p])
    tmp_seen = run_until(m, lambda: any(".tmp" in f.name for f in fx.folder.iterdir()), 20)
    assert tmp_seen, "worker never reached the write step"
    t0 = time.monotonic()
    m.cancel(m.jobs[0].id)
    assert time.monotonic() - t0 < 2.5
    assert m.jobs[0].state == State.CANCELLED
    assert leftovers(fx.folder) == []
    assert sha256(p) == before
    assert run_until(m, lambda: not children(), 3)


def test_cancel_during_check_writes_nothing(fx, monkeypatch):
    monkeypatch.setenv("WATERTIGHT_TEST_FAULT", "sleep:check:30")
    m = QueueManager(workers=1)
    m.add([fx.hole_cube()])
    run_until(m, lambda: m.jobs[0].running, 5)
    m.cancel(m.jobs[0].id)
    assert m.jobs[0].state == State.CANCELLED and leftovers(fx.folder) == []


def test_cancel_one_queued_row(fx):
    m = QueueManager(workers=1)
    m.pause()
    added, _ = m.add([fx.clean_cube("a.stl"), fx.clean_cube("b.stl")])
    assert m.cancel(added[0].id)
    assert [j.state for j in m.jobs] == [State.CANCELLED, State.QUEUED]


def test_interrupt_all_stops_running_and_cancels_queued(fx, monkeypatch):
    m = QueueManager(workers=2)
    m.add([fx.hole_cube("done1.stl"), fx.hole_cube("done2.stl")])
    wait_idle(m)
    kept = leftovers(fx.folder)
    assert kept == ["done1_repaired.stl", "done2_repaired.stl"]

    monkeypatch.setenv("WATERTIGHT_TEST_FAULT", "sleep:check:30")
    files = [fx.hole_cube(f"slow{i}.stl") for i in range(6)]
    hashes = {f: sha256(f) for f in files}
    m.add(files)
    assert run_until(m, lambda: len(m.running_jobs) == 2, 20)
    t0 = time.monotonic()
    summary = m.interrupt_all()
    assert time.monotonic() - t0 < 2.5
    assert summary == {"kept": 2, "stopped": 2, "cancelled": 4}
    new = m.jobs[2:]
    assert all(j.state == State.CANCELLED for j in new)
    assert leftovers(fx.folder) == kept  # finished outputs stay, nothing else appears
    assert all(sha256(f) == h for f, h in hashes.items())
    assert run_until(m, lambda: not children(), 3)

    # the pool is still healthy
    monkeypatch.delenv("WATERTIGHT_TEST_FAULT")
    m.add([fx.clean_cube("after.stl")])
    wait_idle(m)
    assert m.jobs[-1].state == State.SKIPPED


def test_cancel_queued_only_lets_running_finish(fx, monkeypatch):
    monkeypatch.setenv("WATERTIGHT_TEST_FAULT", "sleep:check:1.0")
    m = QueueManager(workers=1)
    m.add([fx.hole_cube(f"q{i}.stl") for i in range(3)])
    assert run_until(m, lambda: len(m.running_jobs) == 1, 20)
    assert m.cancel_queued() == 2
    wait_idle(m)
    states = [j.state for j in m.jobs]
    assert states[0] == State.DONE and states[1:] == [State.CANCELLED, State.CANCELLED]
    assert leftovers(fx.folder) == ["q0_repaired.stl"]


def test_large_files_run_alone(fx):
    m = QueueManager(workers=3, large_bytes=1)  # everything counts as large
    m.add([fx.clean_cube(f"l{i}.stl") for i in range(3)])
    peak = 0
    t0 = time.monotonic()
    while not m.idle and time.monotonic() - t0 < 40:
        m.poll()
        peak = max(peak, len(m.running_jobs))
        time.sleep(0.01)
    assert peak == 1
    assert all(j.state == State.SKIPPED for j in m.jobs)


def test_memory_budget_limits_how_many_run_together(fx):
    m = QueueManager(workers=3, memory_limit=200_000_000)  # one job's estimate is about 150 MB
    m.add([fx.clean_cube(f"m{i}.stl") for i in range(3)])
    peak = 0
    t0 = time.monotonic()
    while not m.idle and time.monotonic() - t0 < 40:
        m.poll()
        peak = max(peak, len(m.running_jobs))
        time.sleep(0.01)
    assert peak == 1
    assert all(j.state == State.SKIPPED for j in m.jobs)


def test_big_files_get_a_bigger_memory_estimate():
    from watertight.queue.memory import estimate_job_memory, memory_budget

    assert estimate_job_memory(147_000_000) > 1_800_000_000  # the Torso peaked near 1.8 GB
    assert estimate_job_memory(1_000_000, factor=1.3) > estimate_job_memory(1_000_000)
    assert memory_budget() > 0


def test_retry_cancelled_job(fx):
    m = QueueManager(workers=1)
    m.pause()
    (job,), _ = m.add([fx.hole_cube()])
    m.cancel(job.id)
    assert m.retry(job.id)
    m.resume()
    wait_idle(m)
    assert job.state == State.DONE


def test_clear_finished(fx):
    m = QueueManager(workers=1)
    m.add([fx.clean_cube()])
    wait_idle(m)
    assert m.clear_finished() == 1 and m.jobs == []


@pytest.mark.skipif(sys.platform == "win32", reason="SIGKILL semantics differ on Windows")
def test_workers_exit_when_main_process_is_killed(fx):
    script = f"""
import os, sys, time
os.environ["WATERTIGHT_TEST_FAULT"] = "sleep:rename:60"
from watertight.queue import QueueManager
m = QueueManager(workers=1)
m.add([r"{fx.hole_cube()}"])
print("started", flush=True)
while True:
    m.poll(); time.sleep(0.05)
"""
    proc = subprocess.Popen([sys.executable, "-c", script], stdout=subprocess.PIPE, text=True)
    parent = psutil.Process(proc.pid)
    t0 = time.monotonic()
    while time.monotonic() - t0 < 30:
        if any(".tmp" in f.name for f in fx.folder.iterdir()):
            break
        time.sleep(0.05)
    kids = [k for k in parent.children(recursive=True) if "spawn_main" in " ".join(k.cmdline())]
    assert kids, "no worker started"
    proc.kill()
    proc.wait()
    t0 = time.monotonic()
    while time.monotonic() - t0 < 8 and any(k.is_running() and k.status() != "zombie" for k in kids):
        time.sleep(0.1)
    assert not any(k.is_running() and k.status() != "zombie" for k in kids)
    assert leftovers(fx.folder) == []


def test_no_time_limit_by_default():
    assert QueueManager().timeout is None
    assert QueueManager(timeout=0).timeout is None  # 0 also means "no limit"


def test_timeout_message_says_which_step(fx, monkeypatch):
    monkeypatch.setenv("WATERTIGHT_TEST_FAULT", "sleep:check:30")
    m = QueueManager(workers=1, timeout=6.0)  # a loaded machine needs seconds to start a worker
    m.add([fx.clean_cube()])
    wait_idle(m)
    assert "timed out after 6 s during checking" in m.jobs[0].message
