"""The repair pipeline. Check first, then run tiers in order; stop at the first valid mesh."""

from __future__ import annotations

import os
import time
from dataclasses import replace
from pathlib import Path

import trimesh

from ..tiers import DEFAULT_TIERS
from ..tiers.manifold_rebuild import ManifoldRebuild
from ..tiers.meshfix import INSTALL_HINT
from ..tiers.trimesh_fixes import remove_dust_shells
from . import meshutil
from .check import CheckResult, check_mesh
from .errors import InputError, OutputExists, WatertightError
from .events import Emit, noop_emit
from .io import ensure_writable, load_stl, output_path_for, tmp_path_for, write_atomic
from .options import RepairOptions
from .refine import Attempt, hill_climb
from .report import DONE, FAILED, NEEDS_REVIEW, SKIPPED, RepairResult
from .validate import shape_guard


def _fault(step: str) -> None:
    """Test-only hook: WATERTIGHT_TEST_FAULT="sleep:<step>:<sec>" or "crash:<step>"."""
    spec = os.environ.get("WATERTIGHT_TEST_FAULT")
    if not spec:
        return
    parts = spec.split(":")
    if len(parts) >= 2 and parts[1] == step:
        if parts[0] == "sleep":
            time.sleep(float(parts[2]))
        elif parts[0] == "crash":
            os._exit(139)


class _Timer:
    """Records how long each step took, so a slow file can be diagnosed."""

    def __init__(self) -> None:
        self.marks: list[tuple[str, float]] = []

    def start(self, step: str) -> None:
        self.marks.append((step, time.monotonic()))

    def timings(self) -> list[tuple[str, float]]:
        now = time.monotonic()
        out = []
        for i, (step, t) in enumerate(self.marks):
            end = self.marks[i + 1][1] if i + 1 < len(self.marks) else now
            out.append((step, round(end - t, 2)))
        return out


def _step(emit: Emit, step: str, message: str = "", timer: _Timer | None = None) -> None:
    if timer is not None:
        timer.start(step)
    emit(step, message)
    _fault(step)


def repair(
    path: str | Path,
    options: RepairOptions | None = None,
    emit: Emit = noop_emit,
    job_id: int | str = 0,
    tiers=None,
) -> RepairResult:
    """Library API. Always returns a RepairResult (status 'failed' instead of raising)."""
    opts = options or RepairOptions()
    path = Path(path).expanduser()
    result = RepairResult(path=str(path))
    t0 = time.monotonic()
    try:
        _run(path, opts, emit, job_id, tiers or DEFAULT_TIERS, result)
    except WatertightError as exc:
        result.status = FAILED
        result.message = exc.message
        result.error_kind = exc.kind
    except MemoryError:
        result.status = FAILED
        result.message = "Out of memory"
        result.error_kind = "internal"
    except Exception as exc:  # never crash the worker: report it
        result.status = FAILED
        result.message = f"Internal error: {type(exc).__name__}: {exc}"
        result.error_kind = "internal"
    result.duration_s = time.monotonic() - t0
    timer = getattr(result, "_timer", None)
    if timer is not None:
        result.timings = timer.timings()
        del result._timer
    return result


def _shape_report(guard, baseline_check, opts: RepairOptions) -> dict:
    """The shape check as plain numbers with their limits, for the details pane."""
    return {
        "volume_before": guard.volume_before,
        "volume_after": guard.volume_after,
        "volume_change_pct": guard.volume_change_pct,
        "volume_limit_pct": opts.max_volume_change * 100,
        "size_change_pct": guard.bbox_change_pct,
        "size_limit_pct": opts.max_bbox_change * 100,
        "far_share_pct": guard.far_share_pct,
        "far_mm": guard.far_mm,
        "far_limit_pct": opts.max_far_share * 100,
    }


TIER2_NAME = "Tier 2 (manifold3d)"


def _refine(result, emit, timer, opts, tier2_input, baseline_mesh, baseline_check, current, notes):
    """Keep trying other cut sizes until no neighbouring size gives a better result."""
    best_mesh, best_check, guard, ok, tier_used, best_name = current
    _step(emit, "refine", "Refining", timer)
    if tier2_input is None:
        result.refine = {"tries": 0, "improved": False, "stopped": "nothing to vary", "log": []}
        return current

    def try_one(rings: int) -> Attempt | None:
        out = ManifoldRebuild().run(tier2_input, replace(opts, pinch_cut_rings=rings))
        if len(out.mesh.faces) == 0:
            return None
        chk = check_mesh(out.mesh)
        g = shape_guard(baseline_mesh, baseline_check, out.mesh, chk, opts)
        return Attempt(out.mesh, chk, g, [f"{TIER2_NAME}: {a}" for a in out.actions], out.notes)

    start = Attempt(best_mesh, best_check, guard)
    from_tier2 = (tier_used or best_name) == TIER2_NAME
    outcome = hill_climb(
        start, opts.pinch_cut_rings if from_tier2 else None, opts.pinch_cut_rings, try_one,
        opts.refine_max_tries, lambda msg: emit("refine", msg),
    )
    result.refine = outcome.summary()
    if not outcome.improved:
        return current
    won = outcome.best
    result.actions = [a for a in result.actions if not a.startswith(TIER2_NAME)] + won.actions
    notes += won.notes
    return won.mesh, won.check, won.guard, won.check.ok, (TIER2_NAME if won.check.ok else None), TIER2_NAME


def _run(path, opts, emit, job_id, tiers, result: RepairResult) -> None:
    out_path = output_path_for(path)
    timer = _Timer()
    result._timer = timer  # read by repair() so timings survive an early failure

    _step(emit, "load", "Loading", timer)
    loaded = load_stl(path, opts.max_file_bytes, opts.max_faces)
    result.file_kind = loaded.kind
    result.warnings += loaded.warnings
    lossy = any(w.startswith("Header says") for w in loaded.warnings)

    _step(emit, "check", "Checking", timer)
    before = check_mesh(loaded.mesh)
    result.check_before = before
    if before.faces == 0:
        raise InputError("STL has no triangles")

    if before.ok:
        result.status = NEEDS_REVIEW if lossy else SKIPPED
        result.message = (
            "file was truncated; read what was there" if lossy else "already watertight"
        )
        return

    # Fail early (before heavy work) if we cannot write the result.
    if out_path.exists() and not opts.overwrite:
        raise OutputExists(f"Output exists: {out_path.name} (use --force to overwrite)")
    ensure_writable(out_path.parent)

    best_mesh: trimesh.Trimesh = loaded.mesh
    best_check: CheckResult = before
    best_name = None
    baseline_mesh = None
    baseline_check = None
    ok = False
    tier_used = None
    notes: list[str] = []
    candidates: list[tuple[str, trimesh.Trimesh, CheckResult]] = []
    tier2_input: trimesh.Trimesh | None = None  # what Tier 2 started from: refine retries from here

    for tier in tiers:
        if not tier.available():
            if tier.key == "tier3":
                notes.append(INSTALL_HINT)
            continue
        _step(emit, tier.key, tier.name, timer)
        if tier.key == "tier2":
            tier2_input = best_mesh
        try:
            out = tier.run(best_mesh, opts)
        except Exception as exc:
            notes.append(f"{tier.name} failed: {type(exc).__name__}: {exc}")
            continue
        result.actions += [f"{tier.name}: {a}" for a in out.actions]
        notes += out.notes
        if len(out.mesh.faces) == 0:
            notes.append(f"{tier.name} left no faces; ignored")
            continue
        chk = check_mesh(out.mesh)
        candidates.append((tier.name, out.mesh, chk))
        if tier.key == "tier0":
            baseline_mesh, baseline_check = out.mesh, chk
        if chk.score() <= best_check.score() or tier.key == "tier0":
            best_mesh, best_check, best_name = out.mesh, chk, tier.name
        if chk.ok:
            ok = True
            tier_used = tier.name
            best_mesh, best_check = out.mesh, chk
            break

    if baseline_mesh is None:
        baseline_mesh, baseline_check = best_mesh, best_check
    else:
        # Dust shells are removed on purpose, so they must not count as a size change.
        base, removed, _ = remove_dust_shells(
            meshutil.new_mesh(baseline_mesh.vertices.copy(), baseline_mesh.faces.copy()), opts
        )
        if removed:
            baseline_mesh, baseline_check = base, check_mesh(base)

    _step(emit, "validate", "Validating", timer)
    guard = shape_guard(baseline_mesh, baseline_check, best_mesh, best_check, opts)
    if guard.warnings:
        # The best-scoring result changed the shape. Try the others before giving up on it.
        for name, cand_mesh, cand_check in sorted(candidates, key=lambda c: c[2].score()):
            if cand_check.score() >= before.score() or cand_mesh is best_mesh:
                continue
            cand_guard = shape_guard(baseline_mesh, baseline_check, cand_mesh, cand_check, opts)
            if not cand_guard.warnings:
                notes.append(f"{best_name or tier_used} changed the shape, so {name} was used instead")
                best_mesh, best_check, best_name, guard = cand_mesh, cand_check, name, cand_guard
                ok = cand_check.ok
                tier_used = name if ok else None
                break
    if opts.refine:
        best_mesh, best_check, guard, ok, tier_used, best_name = _refine(
            result, emit, timer, opts, tier2_input, baseline_mesh, baseline_check,
            (best_mesh, best_check, guard, ok, tier_used, best_name), notes,
        )
    result.shape = _shape_report(guard, baseline_check, opts)
    result.warnings += guard.warnings
    result.volume_change_pct = guard.volume_change_pct
    result.bbox_change_pct = guard.bbox_change_pct
    result.check_after = best_check
    result.tier_used = tier_used or best_name
    n_shells = best_check.shells
    if n_shells > 1:
        notes.append(f"{n_shells} separate bodies kept")
    result.notes += [n for n in dict.fromkeys(notes)]

    if guard.warnings:
        # Outside the limits and no other result stays inside them. A damaged copy is worse than none.
        result.status = NEEDS_REVIEW
        result.message = "repair would change the shape too much; nothing saved"
        return

    _step(emit, "write", "Writing", timer)
    tmp = tmp_path_for(out_path, job_id)
    write_atomic(best_mesh, out_path, tmp, opts.overwrite, lambda: _fault("rename"))
    result.output_path = str(out_path)

    if ok and not guard.warnings and not lossy:
        result.status = DONE
        result.message = f"repaired with {tier_used}"
    else:
        result.status = NEEDS_REVIEW
        if lossy:
            result.message = "file was truncated; some triangles may be missing"
        elif not ok:
            why = []
            if best_check.open_edges:
                why.append(f"{best_check.open_edges} open edges left")
            if best_check.nonmanifold_edges:
                why.append(f"{best_check.nonmanifold_edges} non-manifold edges left")
            if not best_check.winding_consistent:
                why.append("winding still inconsistent")
            if not best_check.volume_positive:
                why.append("volume not positive")
            result.message = "could not fully repair: " + (", ".join(why) or "unknown")
        else:
            result.message = guard.warnings[0].replace("WARNING: ", "")
