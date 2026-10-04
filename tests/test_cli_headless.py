import os
import signal
import subprocess
import sys
import time

import pytest

from .conftest import sha256

PY = [sys.executable, "-m", "watertight"]


def run(args, env=None, **kw):
    e = dict(os.environ)
    e.pop("WATERTIGHT_TEST_FAULT", None)
    e.update(env or {})
    return subprocess.run(PY + args, capture_output=True, text=True, env=e, timeout=120, **kw)


def test_version_and_help():
    r = run(["--version"])
    assert r.returncode == 0 and r.stdout.startswith("watertight ")
    h = run(["--help"])
    assert h.returncode == 0 and "--headless" in h.stdout and "examples:" in h.stdout


def test_headless_success_and_skip(fx):
    hole, clean = fx.hole_cube(), fx.clean_cube()
    before = sha256(hole), sha256(clean)
    r = run(["--headless", str(hole), str(clean)])
    assert r.returncode == 0, r.stdout + r.stderr
    assert "hole.stl: repaired" in r.stdout
    assert "cube.stl: already watertight, skipped" in r.stdout
    assert "Summary: 1 repaired, 1 skipped" in r.stdout
    assert (fx.folder / "hole_repaired.stl").exists()
    assert not (fx.folder / "cube_repaired.stl").exists()
    assert (sha256(hole), sha256(clean)) == before
    assert "Traceback" not in r.stderr


def test_non_terminal_falls_back_to_headless(fx):
    r = run([str(fx.clean_cube())])  # stdout is a pipe here
    assert r.returncode == 0
    assert "running headless" in r.stderr
    assert "already watertight" in r.stdout


def test_exit_codes(fx, tmp_path):
    assert run(["--headless", str(tmp_path / "missing.stl")]).returncode == 2
    assert run(["--headless", str(fx.not_stl())]).returncode == 2
    assert run(["--headless", str(tmp_path)]).returncode == 2
    assert run(["--headless", str(fx.shared_edge_cubes())]).returncode == 1
    assert run(["--headless"]).returncode == 2
    r = run(["--headless", str(fx.not_stl())])
    assert "Not a valid STL file" in r.stdout and "Traceback" not in r.stderr


def test_output_exists_needs_force(fx):
    p = fx.hole_cube()
    out = fx.folder / "hole_repaired.stl"
    out.write_bytes(b"keep")
    r = run(["--headless", str(p)])
    assert r.returncode == 2 and "Output exists" in r.stdout
    assert out.read_bytes() == b"keep"
    assert run(["--headless", "--force", str(p)]).returncode == 0
    assert out.read_bytes() != b"keep"


def test_quiet(fx):
    r = run(["--headless", "--quiet", str(fx.hole_cube())])
    assert r.returncode == 0 and r.stdout == "" and r.stderr == ""
    bad = run(["--headless", "-q", str(fx.not_stl())])
    assert bad.returncode == 2 and "Not a valid STL file" in bad.stderr


def test_jobs_clamped_with_note(fx):
    r = run(["--headless", "--jobs", "999", str(fx.clean_cube())])
    assert r.returncode == 0 and "using" in r.stderr


def test_no_network_calls_in_source():
    import pathlib

    src = pathlib.Path(__file__).parents[1] / "src" / "watertight"
    banned = ("import requests", "import urllib", "import socket", "import http.client")
    for f in src.rglob("*.py"):
        text = f.read_text()
        assert not any(b in text for b in banned), f


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signals")
def test_ctrl_c_in_headless_exits_130_and_saves_nothing(fx):
    files = [fx.hole_cube(f"s{i}.stl") for i in range(4)]
    e = dict(os.environ, WATERTIGHT_TEST_FAULT="sleep:check:60")
    proc = subprocess.Popen(
        PY + ["--headless", "--jobs", "2"] + [str(f) for f in files],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=e,
    )
    time.sleep(3)  # let workers start
    t0 = time.monotonic()
    proc.send_signal(signal.SIGINT)
    out, err = proc.communicate(timeout=15)
    assert time.monotonic() - t0 < 5
    assert proc.returncode == 130, (out, err)
    assert "Interrupted: 0 finished" in out and "cancelled" in out
    assert [p.name for p in fx.folder.iterdir() if "_repaired" in p.name or p.suffix == ".tmp"] == []


def test_timeout_default_is_no_limit():
    from watertight.cli import build_parser

    assert build_parser().parse_args([]).timeout is None
    assert build_parser().parse_args(["--timeout", "30"]).timeout == 30
