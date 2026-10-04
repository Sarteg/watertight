"""Shape-safety guard: compare the repaired mesh to the cleaned input."""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import trimesh

from . import meshutil
from .check import CheckResult
from .options import RepairOptions


@dataclass
class GuardResult:
    volume_change_pct: float | None = None  # None when the input was open (volume meaningless)
    bbox_change_pct: float = 0.0
    far_share_pct: float | None = None  # % of the original surface that is now far from any output face
    warnings: list[str] = field(default_factory=list)
    volume_before: float | None = None  # signed volumes, set when they can be compared
    volume_after: float | None = None
    far_mm: float | None = None  # distance used for the surface check
    mean_dist_rel: float | None = None  # mean distance to the original surface, in units of far_mm


def shape_guard(
    base_mesh: trimesh.Trimesh,
    base_check: CheckResult,
    out_mesh: trimesh.Trimesh,
    out_check: CheckResult,
    opts: RepairOptions,
) -> GuardResult:
    g = GuardResult()
    # Volume is only comparable when the baseline was a closed, consistently wound surface.
    # Otherwise the signed volume is meaningless (a cube missing a face reads ~17 % too small).
    if base_check.closed_topology and base_check.winding_consistent and abs(base_check.volume) > 0:
        g.volume_before, g.volume_after = base_check.volume, out_check.volume
        g.volume_change_pct = 100.0 * (abs(out_check.volume) - abs(base_check.volume)) / abs(
            base_check.volume
        )
        if abs(g.volume_change_pct) > opts.max_volume_change * 100:
            g.warnings.append(
                f"WARNING: volume changed {g.volume_change_pct:+.2f}% "
                f"(limit {opts.max_volume_change * 100:.0f}%). Check the part before printing."
            )

    bv, ov = meshutil.merged(base_mesh).vertices, meshutil.merged(out_mesh).vertices
    if len(bv) and len(ov):
        bdim = bv.max(axis=0) - bv.min(axis=0)
        odim = ov.max(axis=0) - ov.min(axis=0)
        diag = float(np.linalg.norm(bdim)) or 1.0
        denom = np.where(bdim > 0, bdim, diag)
        g.bbox_change_pct = float(100.0 * np.max(np.abs(odim - bdim) / denom))
        if g.bbox_change_pct > opts.max_bbox_change * 100:
            g.warnings.append(
                f"WARNING: size changed {g.bbox_change_pct:.2f}% "
                f"(limit {opts.max_bbox_change * 100:.1f}%)."
            )
    far = surface_far_share(base_mesh, out_mesh, opts)
    g.far_share_pct = None if far is None else 100.0 * far[0]
    g.far_mm = None if far is None else far[1]
    g.mean_dist_rel = None if far is None else far[2]
    if far is not None and far[0] > opts.max_far_share:
        g.warnings.append(
            f"WARNING: {g.far_share_pct:.1f}% of the surface moved more than {far[1]:.2f} mm. "
            "Check the part before printing."
        )
    return g


SAMPLE_FACES = 200_000


def surface_far_share(
    base_mesh: trimesh.Trimesh, out_mesh: trimesh.Trimesh, opts: RepairOptions
) -> tuple[float, float, float] | None:
    """Catch lost detail that volume and size cannot see. Takes a sample of the original faces
    and asks how many have no output face nearby. Returns (share 0..1, the distance used in
    model units, mean distance as a share of that distance), or None when the mesh is too
    small to judge."""
    from scipy.spatial import cKDTree

    nb, no = len(base_mesh.faces), len(out_mesh.faces)
    if nb < 50 or no == 0:
        return None
    rng = np.random.default_rng(0)
    pick = rng.choice(nb, SAMPLE_FACES, replace=False) if nb > SAMPLE_FACES else slice(None)
    tri = base_mesh.vertices[base_mesh.faces[pick]]
    base_pts = tri.mean(axis=1)
    edge = np.median(np.linalg.norm(tri[:, 1] - tri[:, 0], axis=1))
    diag = meshutil.bbox_diagonal(base_mesh)
    # A rebuilt surface has different triangles, so allow a couple of edge lengths of slack.
    tol = max(opts.far_tol_rel * diag, 2.0 * float(edge))
    out_pts = out_mesh.vertices[out_mesh.faces].mean(axis=1)
    dist, _ = cKDTree(out_pts).query(base_pts, workers=-1)
    return float((dist > tol).mean()), float(tol), float(dist.mean() / tol)
