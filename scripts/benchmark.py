"""Benchmark: repair rate on a SYNTHETIC defect corpus, and parallel speed-up.

The corpus is generated in code (no downloads, no third-party meshes). For real-world numbers,
point `--folder` at a folder of imperfect STL files you have the right to use (for example a
sample of Thingi10K; check its licence first).

    python scripts/benchmark.py                 # synthetic corpus
    python scripts/benchmark.py --folder ~/stl  # your own files (copied to a temp dir first)
"""

from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
import time
from pathlib import Path

import numpy as np
import trimesh

from watertight.queue import QueueManager, State
from watertight.queue.manager import max_workers


def write(mesh: trimesh.Trimesh, path: Path) -> Path:
    path.write_bytes(mesh.export(file_type="stl"))
    return path


def drop(mesh, mask):
    m = mesh.copy()
    m.update_faces(mask)
    return m


def make_corpus(folder: Path, n_each: int = 4, subdivisions: int = 4) -> dict[str, list[Path]]:
    rng = np.random.default_rng(7)
    out: dict[str, list[Path]] = {k: [] for k in
        ("hole", "big hole", "flipped faces", "inside-out", "duplicate faces", "degenerate faces",
         "floating speck", "internal fin")}
    for i in range(n_each):
        shapes = [
            trimesh.creation.icosphere(subdivisions=subdivisions),
            trimesh.creation.cylinder(radius=1, height=2, sections=48),
            trimesh.creation.annulus(r_min=0.5, r_max=1, height=0.4, sections=48),
            trimesh.creation.box(extents=(1, 2, 3)),
        ]
        base = shapes[i % len(shapes)]
        nf = len(base.faces)
        k = int(rng.integers(0, nf))
        keep = np.ones(nf, bool)
        keep[k] = False
        out["hole"].append(write(drop(base, keep), folder / f"hole_{i}.stl"))

        c = base.vertices[base.faces].mean(axis=1)
        d = rng.normal(size=3)
        d /= np.linalg.norm(d)
        proj = c @ d
        keep = proj < np.quantile(proj, 0.95)
        out["big hole"].append(write(drop(base, keep), folder / f"bighole_{i}.stl"))

        f = base.faces.copy()
        idx = rng.choice(nf, size=max(2, nf // 40), replace=False)
        f[idx] = f[idx][:, ::-1]
        out["flipped faces"].append(
            write(trimesh.Trimesh(base.vertices, f, process=False), folder / f"flip_{i}.stl"))

        inv = base.copy()
        inv.invert()
        out["inside-out"].append(write(inv, folder / f"inside_{i}.stl"))

        f = np.vstack([base.faces, base.faces[: max(3, nf // 30)]])
        out["duplicate faces"].append(
            write(trimesh.Trimesh(base.vertices, f, process=False), folder / f"dup_{i}.stl"))

        v = np.vstack([base.vertices, np.zeros((3, 3))])
        f = np.vstack([base.faces, [[len(base.vertices), len(base.vertices) + 1,
                                     len(base.vertices) + 2]]])
        out["degenerate faces"].append(
            write(trimesh.Trimesh(v, f, process=False), folder / f"degen_{i}.stl"))

        n = len(base.vertices)
        v = np.vstack([base.vertices, base.vertices.mean(axis=0) + [0.01, 0, 0],
                       base.vertices.mean(axis=0) + [0.02, 0, 0],
                       base.vertices.mean(axis=0) + [0.01, 0.01, 0]])
        f = np.vstack([base.faces, [[n, n + 1, n + 2]]])
        out["floating speck"].append(
            write(trimesh.Trimesh(v, f, process=False), folder / f"speck_{i}.stl"))

        e = base.edges_unique[0]
        v = np.vstack([base.vertices, base.vertices.mean(axis=0)])
        f = np.vstack([base.faces, [[e[0], e[1], n]]])
        out["internal fin"].append(
            write(trimesh.Trimesh(v, f, process=False), folder / f"fin_{i}.stl"))
    return out


def run_queue(paths: list[Path], workers: int) -> tuple[float, QueueManager]:
    m = QueueManager(workers=workers, timeout=600)
    t0 = time.monotonic()
    m.add(paths)
    while not m.idle:
        m.poll()
        time.sleep(0.02)
    return time.monotonic() - t0, m


def copy_outputs_away(folder: Path) -> None:
    for p in folder.glob("*_repaired.stl"):
        p.unlink()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--folder", type=Path, help="folder with your own STL files")
    ap.add_argument("--n-each", type=int, default=4)
    ap.add_argument("--subdivisions", type=int, default=4, help="sphere detail (4 = 5k faces)")
    args = ap.parse_args()

    work = Path(tempfile.mkdtemp(prefix="watertight-bench-"))
    try:
        if args.folder:
            for p in args.folder.glob("*.stl"):
                shutil.copy(p, work / p.name)
            corpus = {"your files": sorted(work.glob("*.stl"))}
        else:
            corpus = make_corpus(work, args.n_each, args.subdivisions)
        all_paths = [p for v in corpus.values() for p in v]
        print(f"{len(all_paths)} files, workers available: {max_workers()}\n")

        secs, m = run_queue(all_paths, workers=min(4, max_workers()))
        by_path = {j.path: j for j in m.jobs}
        print(f"{'defect':<18}{'files':>6}{'repaired':>10}{'review':>8}{'failed':>8}{'skipped':>9}")
        total_ok = 0
        for name, paths in corpus.items():
            st = [by_path[p].state for p in paths]
            ok = st.count(State.DONE)
            total_ok += ok
            print(f"{name:<18}{len(paths):>6}{ok:>10}{st.count(State.NEEDS_REVIEW):>8}"
                  f"{st.count(State.FAILED):>8}{st.count(State.SKIPPED):>9}")
        print(f"\nrepair rate: {100 * total_ok / len(all_paths):.0f}% "
              f"({total_ok}/{len(all_paths)} ended Done)   wall time {secs:.1f} s")

        copy_outputs_away(work)
        heavy = make_corpus(Path(tempfile.mkdtemp(prefix="watertight-heavy-")), 1, 6)
        heavy_paths = [p for v in heavy.values() for p in v][:4]
        t1, _ = run_queue(heavy_paths, workers=1)
        n = min(4, max_workers())
        copy_outputs_away(heavy_paths[0].parent)
        tn, _ = run_queue(heavy_paths, workers=n)
        print(f"\nparallel: 4 heavy files  1 worker {t1:.1f} s  |  {n} workers {tn:.1f} s  "
              f"|  speed-up {t1 / tn:.2f}x  (time ratio {tn / t1:.2f})")
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
