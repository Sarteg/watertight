import trimesh

import watertight
from watertight import RepairOptions, check_file, repair
from watertight.core.check import check_mesh

from .conftest import sha256

OPTS = RepairOptions()


def reload_ok(path):
    m = trimesh.load(path, force="mesh")
    return m.is_watertight and m.is_winding_consistent and m.volume > 0, m


# ---- check -------------------------------------------------------------------------------
def test_clean_cube_is_ok_binary_and_ascii(fx):
    assert check_file(fx.clean_cube("b.stl")).ok
    assert check_file(fx.clean_cube("a.stl", ascii_=True)).ok


def test_broken_cubes_are_not_ok(fx):
    assert not check_file(fx.hole_cube()).ok
    assert check_file(fx.flipped_cube()).watertight  # edges are closed...
    assert not check_file(fx.flipped_cube()).ok  # ...but winding is inconsistent
    c = check_file(fx.inside_out_cube())
    assert c.watertight and c.winding_consistent and not c.volume_positive and not c.ok


def test_check_counts(fx):
    c = check_file(fx.dup_cube())
    assert c.duplicate_faces == 3
    assert check_file(fx.degenerate_cube()).degenerate_faces == 1
    assert check_file(fx.two_cubes_far()).shells == 2


# ---- skip if watertight ---------------------------------------------------------------------
def test_clean_cube_is_skipped_and_nothing_written(fx):
    p = fx.clean_cube()
    r = repair(p)
    assert r.status == "skipped"
    assert not (p.parent / "cube_repaired.stl").exists()


# ---- repair ---------------------------------------------------------------------------------
def test_hole_is_filled_and_volume_close(fx):
    p = fx.hole_cube()
    r = repair(p)
    assert r.status == "repaired", r.message
    ok, m = reload_ok(p.parent / "hole_repaired.stl")
    assert ok
    assert abs(m.volume - 1000) / 1000 < 0.005


def test_square_hole(fx):
    r = repair(fx.big_hole_cube())
    assert r.status == "repaired", r.message
    ok, m = reload_ok(fx.path("bighole_repaired.stl"))
    assert ok and abs(m.volume - 1000) < 5


def test_flipped_faces_fixed(fx):
    r = repair(fx.flipped_cube())
    assert r.status == "repaired", r.message
    assert reload_ok(fx.path("flipped_repaired.stl"))[0]


def test_inside_out_fixed(fx):
    r = repair(fx.inside_out_cube())
    assert r.status == "repaired", r.message
    ok, m = reload_ok(fx.path("inside_repaired.stl"))
    assert ok and abs(m.volume - 1000) < 1e-3


def test_duplicates_and_degenerates_removed(fx):
    for make, name in ((fx.dup_cube, "dup"), (fx.degenerate_cube, "degenerate")):
        r = repair(make())
        assert r.status == "repaired", r.message
        c = check_mesh(trimesh.load(fx.path(f"{name}_repaired.stl"), process=False, force="mesh"))
        assert c.ok and c.duplicate_faces == 0 and c.degenerate_faces == 0


def test_speck_removed_and_reported(fx):
    r = repair(fx.speck_cube())
    assert r.status == "repaired", (r.message, r.warnings)
    assert any("tiny floating shells" in a for a in r.actions)
    assert check_file(fx.path("speck_repaired.stl")).shells == 1


def test_two_far_cubes_both_kept(fx):
    # already watertight -> skipped; make it need repair by punching a hole in one body
    import numpy as np

    from .conftest import drop_faces, write_stl

    m = trimesh.load(fx.two_cubes_far(), process=False, force="mesh")
    p = write_stl(drop_faces(m, [3]), fx.path("two_hole.stl"))
    r = repair(p)
    assert r.status == "repaired", r.message
    out = check_file(fx.path("two_hole_repaired.stl"))
    assert out.shells == 2 and out.ok
    assert any("2 separate bodies kept" in n for n in r.notes)
    assert np.isclose(out.volume, 2000, rtol=0.01)


def test_nonmanifold_fin_fixed_by_tier2(fx):
    r = repair(fx.fin_cube())
    assert r.status == "repaired", (r.message, r.warnings)
    assert r.tier_used == "Tier 2 (manifold3d)"
    assert reload_ok(fx.path("fin_repaired.stl"))[0]


def test_touching_edge_cubes_never_silently_pass(fx):
    """Two bodies touching along one edge cannot become manifold without moving geometry.
    The tool must say so (needs_review), not claim success."""
    r = repair(fx.shared_edge_cubes())
    assert r.status == "needs_review"
    assert r.message


def test_original_never_touched(fx):
    for make in (fx.hole_cube, fx.flipped_cube, fx.clean_cube, fx.not_stl):
        p = make()
        before = sha256(p)
        repair(p)
        assert sha256(p) == before


def test_output_exists_without_overwrite(fx):
    p = fx.hole_cube()
    out = fx.path("hole_repaired.stl")
    out.write_bytes(b"keep me")
    r = repair(p)
    assert r.status == "failed" and r.error_kind == "output"
    assert "Output exists" in r.message
    assert out.read_bytes() == b"keep me"
    r2 = repair(p, RepairOptions(overwrite=True))
    assert r2.status == "repaired"
    assert out.read_bytes() != b"keep me"


def test_failed_inputs_report_clean_errors(fx, tmp_path):
    assert repair(fx.not_stl()).message == "Not a valid STL file"
    assert repair(fx.empty()).status == "failed"
    r = repair(tmp_path / "missing.stl")
    assert r.status == "failed" and r.message.startswith("File not found")
    assert r.exit_code == 2


def test_truncated_is_needs_review(fx):
    r = repair(fx.truncated())
    assert r.status == "needs_review"
    assert "truncated" in r.message


def test_volume_guard_warns(fx, monkeypatch):
    # a mesh whose repair changes volume a lot: force a tiny limit
    r = repair(fx.flipped_cube(), RepairOptions(max_bbox_change=0.0))
    assert r.status in ("repaired", "needs_review")


def test_result_roundtrip_dict(fx):
    r = repair(fx.hole_cube())
    r2 = type(r).from_dict(r.to_dict())
    assert r2.status == r.status and r2.check_after.ok == r.check_after.ok


def test_progress_events_in_order(fx):
    seen = []
    repair(fx.hole_cube(), emit=lambda s, m="": seen.append(s))
    assert seen[:2] == ["load", "check"]
    assert seen[-2:] == ["validate", "write"]
    assert "tier0" in seen and "tier1" in seen


def test_public_api():
    assert watertight.__version__


# ---- Tier 3 (optional pymeshfix) -------------------------------------------------------------
import importlib.util  # noqa: E402

import pytest  # noqa: E402

HAS_MESHFIX = importlib.util.find_spec("pymeshfix") is not None


def test_pinched_hole_is_fixed_before_tier3(fx):
    """Two holes touching at one vertex used to need pymeshfix. The hole filler now cuts the
    pinched boundary into simple loops, so the small tiers handle it."""
    r = repair(fx.figure8_sphere())
    assert r.status == "repaired", (r.message, r.warnings)
    assert r.tier_used in ("Tier 1 (trimesh)", "Tier 2 (manifold3d)")
    assert reload_ok(fx.path("figure8_repaired.stl"))[0]


@pytest.mark.skipif(not HAS_MESHFIX, reason="pymeshfix not installed")
def test_tier3_runs_when_the_small_tiers_cannot(fx, monkeypatch):
    from watertight.tiers.manifold_rebuild import ManifoldRebuild
    from watertight.tiers.trimesh_fixes import TrimeshFixes

    monkeypatch.setattr(TrimeshFixes, "available", lambda self: False)
    monkeypatch.setattr(ManifoldRebuild, "available", lambda self: False)
    r = repair(fx.hole_cube())
    assert r.status == "repaired", (r.message, r.warnings)
    assert r.tier_used == "Tier 3 (pymeshfix)"
    assert reload_ok(fx.path("hole_repaired.stl"))[0]


def test_result_that_ruins_the_shape_is_not_saved(fx, monkeypatch):
    """A repair that throws away much of the model must not replace it with a bad file."""
    from watertight.tiers import DEFAULT_TIERS
    from watertight.tiers.base import TierOutput

    class Ruiner:
        key, name, engine = "tier3", "Tier 3 (ruin)", "test"

        def available(self):
            return True

        def run(self, mesh, opts):
            small = trimesh.creation.box(extents=(0.3, 0.3, 0.3))
            return TierOutput(small, ["shrank it"])

    p = fx.hole_cube()
    monkeypatch.setattr("watertight.core.pipeline.DEFAULT_TIERS", DEFAULT_TIERS[:1] + [Ruiner()])
    r = repair(p)
    assert r.status == "needs_review"
    assert "nothing saved" in r.message
    assert r.output_path is None
    assert not fx.path("hole_repaired.stl").exists()


def test_without_pymeshfix_gives_install_hint(fx, monkeypatch):
    from watertight.tiers.manifold_rebuild import ManifoldRebuild
    from watertight.tiers.meshfix import MeshFixTier
    from watertight.tiers.trimesh_fixes import TrimeshFixes

    monkeypatch.setattr(TrimeshFixes, "available", lambda self: False)
    monkeypatch.setattr(ManifoldRebuild, "available", lambda self: False)
    monkeypatch.setattr(MeshFixTier, "available", lambda self: False)
    r = repair(fx.hole_cube())
    assert r.status == "needs_review"
    assert any('pip install "watertight[meshfix]"' in n for n in r.notes)


import os  # noqa: E402
import sys  # noqa: E402


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permissions")
def test_read_only_folder_fails_early_and_leaves_nothing(fx):
    p = fx.hole_cube()
    os.chmod(fx.folder, 0o555)
    try:
        if os.access(fx.folder, os.W_OK):
            pytest.skip("running as a user that ignores permissions")
        r = repair(p)
        assert r.status == "failed" and r.error_kind == "output"
        assert r.message.startswith("Cannot write to")
    finally:
        os.chmod(fx.folder, 0o755)
    assert [f.name for f in fx.folder.iterdir() if "_repaired" in f.name or f.suffix == ".tmp"] == []


def test_result_records_time_per_step(fx):
    r = repair(fx.hole_cube())
    steps = [s for s, _ in r.timings]
    assert steps[:2] == ["load", "check"] and steps[-1] == "write"
    assert all(t >= 0 for _, t in r.timings)
    from watertight.core.report import details_lines

    assert any(line.startswith("Time:") for line in details_lines(r))


def test_many_tiny_shells_scale_linearly(tmp_path):
    """Scans often have 100k+ loose specks. A per-shell loop is O(shells x faces) and takes
    hours; this must stay in seconds."""
    import time

    import numpy as np

    from .conftest import write_stl

    rng = np.random.default_rng(3)
    base = trimesh.creation.icosphere(subdivisions=3, radius=50)
    t = trimesh.creation.box(extents=(0.3, 0.3, 0.3))
    f = t.faces[1:]  # an open little box: needs a hole filled
    n = 40_000
    V = np.vstack([base.vertices] + [t.vertices + rng.normal(size=3) * 80 for _ in range(n)])
    F = np.vstack([base.faces] + [f + len(base.vertices) + 8 * i for i in range(n)])
    p = write_stl(trimesh.Trimesh(V, F, process=False), tmp_path / "specks.stl")
    t0 = time.monotonic()
    r = repair(p)
    elapsed = time.monotonic() - t0
    assert r.status == "repaired", (r.status, r.message)
    assert elapsed < 20, f"too slow: {elapsed:.1f}s (quadratic loop is back?)"


def test_pinched_edge_is_cut_and_refilled_without_moving_the_rest():
    """Two spheres that meet along one shared edge: 4 faces on that edge. The fix cuts a
    small patch and refills it. Everything else must stay where it was."""
    import numpy as np

    from watertight.core import meshutil
    from watertight.tiers.trimesh_fixes import cut_and_refill_pinches, orient_shells

    a = trimesh.creation.icosphere(subdivisions=3)
    mid = a.vertices[a.edges_unique[0]].mean(axis=0)
    b = trimesh.Trimesh(2 * mid - a.vertices, a.faces[:, ::-1], process=False)  # shares that edge
    m = trimesh.util.concatenate([a, b])
    m.merge_vertices()
    assert meshutil.edge_counts(m) == (0, 1)

    out, cut = cut_and_refill_pinches(m)
    out, _ = orient_shells(out)
    chk = check_mesh(out)
    assert 0 < cut < 0.05 * len(m.faces)
    assert chk.ok and chk.nonmanifold_edges == 0
    assert np.allclose(out.bounds, m.bounds)


def test_pinch_cut_gives_up_when_it_would_remove_too_much(fx):
    from watertight.core import meshutil
    from watertight.tiers.trimesh_fixes import cut_and_refill_pinches

    m = meshutil.merged(trimesh.load(str(fx.shared_edge_cubes()), force="mesh", process=False))
    out, cut = cut_and_refill_pinches(m)
    assert cut == 0 and len(out.faces) == len(m.faces)


def test_guard_sees_lost_surface_that_volume_and_size_miss():
    """Cut away a cap of a sphere, close it flat, keep the volume close: the sphere's surface
    is gone there. Volume and bounding box barely move, but the surface check must object."""
    import numpy as np

    from watertight.core.options import RepairOptions
    from watertight.core.validate import shape_guard

    base = trimesh.creation.icosphere(subdivisions=4)
    keep = base.triangles_center[:, 2] < 0.2  # drop the whole top cap
    out = trimesh.Trimesh(base.vertices.copy(), base.faces[keep], process=False)
    out.update_faces(np.ones(len(out.faces), dtype=bool))
    g = shape_guard(base, check_mesh(base), out, check_mesh(out), RepairOptions())
    assert g.far_share_pct is not None and g.far_share_pct > 5
    assert g.warnings
    assert any("surface moved" in w for w in g.warnings)

    same = shape_guard(base, check_mesh(base), base.copy(), check_mesh(base), RepairOptions())
    assert same.far_share_pct == 0 and not same.warnings


def test_report_says_what_was_wrong_what_was_done_and_the_shape_check(fx):
    from watertight.core.report import details_lines

    text = "\n".join(details_lines(repair(fx.hole_cube())))
    for part in ("Problems found:", "open edges", "What the app did:", "1. Clean up",
                 "Shape check", "Size", "Left over: nothing"):
        assert part in text, part


def test_report_for_a_refused_result_shows_which_limit_failed(fx, monkeypatch):
    from watertight.core.report import details_lines
    from watertight.tiers import DEFAULT_TIERS
    from watertight.tiers.base import TierOutput

    class Ruiner:
        key, name, engine = "tier3", "Tier 3 (ruin)", "test"

        def available(self):
            return True

        def run(self, mesh, opts):
            return TierOutput(trimesh.creation.box(extents=(0.3, 0.3, 0.3)), ["shrank it"])

    monkeypatch.setattr("watertight.core.pipeline.DEFAULT_TIERS", DEFAULT_TIERS[:1] + [Ruiner()])
    text = "\n".join(details_lines(repair(fx.hole_cube())))
    assert "NOT saved" in text and "FAIL" in text


# ---- keep-trying (refine) mode ---------------------------------------------------------------
def _attempt(far=0.0, mean=0.0, ok=True, warn=False):
    from types import SimpleNamespace

    from watertight.core.refine import Attempt
    from watertight.core.validate import GuardResult

    chk = SimpleNamespace(ok=ok, open_edges=0, nonmanifold_edges=0)
    g = GuardResult(far_share_pct=far, mean_dist_rel=mean, warnings=["w"] if warn else [])
    return Attempt(mesh=None, check=chk, guard=g)


def test_hill_climb_walks_downhill_and_stops_when_neighbours_are_not_better():
    from watertight.core.refine import hill_climb

    far = {0: 3.0, 1: 2.0, 2: 1.0, 3: 0.5, 4: 0.5, 5: 2.0, 6: 4.0}  # best at 3 (4 ties it)
    seen = []

    def try_one(r):
        seen.append(r)
        return _attempt(far=far[r])

    out = hill_climb(_attempt(far=far[2]), 2, 2, try_one, max_tries=20)
    assert out.best_rings == 3 and out.improved and out.stopped == "no better result"
    assert seen == [1, 3, 4]  # tried both neighbours, moved up, tried the next one, stopped
    assert out.tries == 3


def test_hill_climb_respects_the_try_limit_and_survives_a_failed_try():
    from watertight.core.refine import hill_climb

    def try_one(r):
        if r == 1:
            raise RuntimeError("boom")
        return _attempt(far=10 - r)  # always better going up

    out = hill_climb(_attempt(far=8), 2, 2, try_one, max_tries=3)
    assert out.tries == 3 and out.stopped == "try limit"
    assert any("failed" in line for line in out.log)


def test_quality_puts_staying_inside_the_limits_first():
    from watertight.core.refine import quality

    assert quality(_attempt(far=5.0)) < quality(_attempt(far=0.0, warn=True))
    assert quality(_attempt(far=0.0)) < quality(_attempt(far=0.0, ok=False))


def test_refine_mode_reports_and_keeps_a_result_inside_the_limits(tmp_path):
    import numpy as np

    a = trimesh.creation.icosphere(subdivisions=3)
    mid = a.vertices[a.edges_unique[0]].mean(axis=0)
    b = trimesh.Trimesh(2 * mid - a.vertices, a.faces[:, ::-1], process=False)  # shares one edge
    p = tmp_path / "pinched.stl"
    trimesh.util.concatenate([a, b]).export(p)
    plain = repair(p, RepairOptions(overwrite=True))
    fine = repair(p, RepairOptions(overwrite=True, refine=True))
    assert plain.refine is None
    assert fine.refine and fine.refine["tries"] >= 1
    assert fine.status == "repaired", (fine.message, fine.warnings)
    assert fine.refine["stopped"] in ("no better result", "try limit")
    assert np.isfinite(fine.shape["far_share_pct"])
    from watertight.core.report import details_lines

    assert any(line.startswith("Keep trying:") for line in details_lines(fine))


def test_refine_with_nothing_to_vary_says_so(fx):
    r = repair(fx.hole_cube(), RepairOptions(overwrite=True, refine=True))
    assert r.refine["stopped"] == "nothing to vary"
