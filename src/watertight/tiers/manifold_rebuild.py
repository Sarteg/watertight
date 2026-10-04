"""Tier 2: split non-manifold geometry into clean shells, close them, and (when safe) union
them with manifold3d (Apache-2.0)."""

from __future__ import annotations

import numpy as np
import trimesh

from ..core import meshutil
from ..core.options import RepairOptions
from .base import TierOutput
from .trimesh_fixes import cut_and_refill_pinches, fill_boundary_loops, orient_shells, shell_stats

MAX_UNION_SHELLS = 200  # a union of thousands of shells is slow and rarely wanted


def split_nonmanifold(m: trimesh.Trimesh) -> tuple[trimesh.Trimesh, int]:
    """Give every manifold-edge-connected patch its own copy of its vertices. Edges shared by
    3+ faces, and vertices pinched between patches, stop being shared. Returns (mesh, patches)."""
    n, labels = meshutil.face_components(m)
    nv = len(m.vertices)
    key = (labels[:, None] * nv + m.faces).ravel()
    uniq, inv = np.unique(key, return_inverse=True)
    new_v = m.vertices[uniq % nv]
    new_f = inv.reshape(-1, 3)
    return meshutil.new_mesh(new_v, new_f), n


class ManifoldRebuild:
    key = "tier2"
    name = "Tier 2 (manifold3d)"
    engine = "manifold3d"

    def available(self) -> bool:
        try:
            import manifold3d  # noqa: F401
        except Exception:
            return False
        return True

    def run(self, mesh: trimesh.Trimesh, opts: RepairOptions) -> TierOutput:
        actions: list[str] = []
        notes: list[str] = []
        m = meshutil.new_mesh(mesh.vertices.copy(), mesh.faces.copy())
        if len(m.faces) == 0:
            return TierOutput(m, actions)

        m.merge_vertices()
        m, cut = cut_and_refill_pinches(m, opts.pinch_cut_rings, opts.pinch_max_share)
        if cut:
            actions.append(f"cut out and refilled {cut} faces around pinch points")
        m, patches = split_nonmanifold(m)
        actions.append(f"split into {patches} manifold patches")

        # Whole-mesh steps (each already works per shell, in O(faces)).
        try:
            trimesh.repair.fix_winding(m)
        except Exception:
            pass
        m, _ = fill_boundary_loops(m)
        m, _ = orient_shells(m)

        # Drop flat sheets and tiny pieces.
        n, labels = meshutil.vertex_shell_labels(m)
        counts, bmin, bmax, vols = shell_stats(m, labels, n)
        diag = float(np.linalg.norm(bmax.max(axis=0) - bmin.min(axis=0)))
        shell_diag = np.linalg.norm(bmax - bmin, axis=1)
        drop = (np.abs(vols) <= 1e-9 * diag**3) | (shell_diag < opts.dust_diag_rel * diag)
        if drop.all():
            return TierOutput(m, actions, notes)
        if drop.any():
            actions.append(f"dropped {int(drop.sum())} flat or tiny sheets")
            m.update_faces(~drop[labels])
            m.remove_unreferenced_vertices()
        out = meshutil.new_mesh(m.vertices, m.faces)

        n, labels = meshutil.vertex_shell_labels(out)
        if 1 < n <= MAX_UNION_SHELLS:
            parts = []
            for s_ in range(n):
                sub = meshutil.new_mesh(out.vertices, out.faces[labels == s_])
                sub.remove_unreferenced_vertices()
                parts.append(sub)
            union = self._union(parts)
            if union is not None:
                out = union
                actions.append("merged overlapping shells with manifold3d")
        elif n > MAX_UNION_SHELLS:
            notes.append(f"{n} shells: overlap merge skipped (limit {MAX_UNION_SHELLS})")
        return TierOutput(out, actions, notes)

    @staticmethod
    def _union(parts: list[trimesh.Trimesh]) -> trimesh.Trimesh | None:
        """Union closed, outward-facing shells. Skipped if any shell is not a clean manifold or
        is a cavity (negative volume), because a union would change those."""
        import manifold3d as md

        mans = []
        for p in parts:
            if meshutil.signed_volume(p) <= 0:
                return None
            mesh = md.Mesh(
                vert_properties=np.asarray(p.vertices, dtype=np.float32),
                tri_verts=np.asarray(p.faces, dtype=np.uint32),
            )
            man = md.Manifold(mesh)
            if man.status() != md.Error.NoError or man.is_empty():
                return None
            mans.append(man)
        res = md.Manifold.batch_boolean(mans, md.OpType.Add).to_mesh()
        if len(res.tri_verts) == 0:
            return None
        return meshutil.new_mesh(res.vert_properties[:, :3], res.tri_verts)
