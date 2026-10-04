"""Tier 0: clean up raw triangles. No geometry is invented here."""

from __future__ import annotations

import numpy as np
import trimesh

from ..core import meshutil
from ..core.options import RepairOptions
from .base import TierOutput


class Sanitize:
    key = "tier0"
    name = "Tier 0 (sanitize)"
    engine = "trimesh"

    def available(self) -> bool:
        return True

    def run(self, mesh: trimesh.Trimesh, opts: RepairOptions) -> TierOutput:
        actions: list[str] = []
        v = np.asarray(mesh.vertices, dtype=np.float64).copy()
        f = np.asarray(mesh.faces, dtype=np.int64).copy()

        finite_face = np.isfinite(v[f]).all(axis=(1, 2))
        if not finite_face.all():
            actions.append(f"removed {int((~finite_face).sum())} faces with NaN/inf coordinates")
            f = f[finite_face]
        m = meshutil.new_mesh(v, f)
        if len(m.faces) == 0:
            return TierOutput(m, actions)

        before = len(m.vertices)
        m.merge_vertices()
        diag = meshutil.bbox_diagonal(m)
        tol = opts.merge_tol_rel * diag
        if tol > 0:
            digits = int(np.ceil(-np.log10(tol)))
            if 0 <= digits < 8:  # coarser than the exact merge above
                m.merge_vertices(digits_vertex=digits)
        merged_n = before - len(m.vertices)
        if merged_n > 0:
            actions.append(f"merged {merged_n} duplicate vertices")

        f = m.faces
        same = (f[:, 0] == f[:, 1]) | (f[:, 1] == f[:, 2]) | (f[:, 0] == f[:, 2])
        tiny = m.area_faces <= 1e-12 * diag * diag
        bad = same | tiny
        if bad.any():
            actions.append(f"removed {int(bad.sum())} degenerate faces")
            m.update_faces(~bad)

        if len(m.faces):
            keep = m.unique_faces()
            if not keep.all():
                actions.append(f"removed {int((~keep).sum())} duplicate faces")
                m.update_faces(keep)

        m.remove_unreferenced_vertices()
        return TierOutput(m, actions)
