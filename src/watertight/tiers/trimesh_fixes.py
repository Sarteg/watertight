"""Tier 1: pure trimesh / numpy fixes (permissive licence)."""

from __future__ import annotations

import numpy as np
import trimesh

from ..core import meshutil
from ..core.options import RepairOptions
from .base import TierOutput


def shell_stats(m: trimesh.Trimesh, labels: np.ndarray, n: int):
    """Per-shell face counts, bounding boxes and signed volumes, all in O(faces).
    (Loops like `faces[labels == s]` per shell are O(shells x faces): hours on a noisy scan.)"""
    tri = m.vertices[m.faces]
    order = np.argsort(labels, kind="stable")
    counts = np.bincount(labels, minlength=n)
    starts = np.concatenate([[0], np.cumsum(counts)[:-1]])
    bmin = np.minimum.reduceat(tri.min(axis=1)[order], starts, axis=0)
    bmax = np.maximum.reduceat(tri.max(axis=1)[order], starts, axis=0)
    t = tri - tri.reshape(-1, 3).mean(axis=0)
    contrib = np.einsum("ij,ij->i", t[:, 0], np.cross(t[:, 1], t[:, 2])) / 6.0
    vols = np.bincount(labels, weights=contrib, minlength=n)
    return counts, bmin, bmax, vols


def remove_dust_shells(m: trimesh.Trimesh, opts: RepairOptions) -> tuple[trimesh.Trimesh, int, int]:
    """Drop tiny shells (< dust_face_count faces, or tiny bbox). Never drops everything.
    Returns (mesh, removed, kept_big_shells)."""
    n, labels = meshutil.vertex_shell_labels(m)
    if n <= 1:
        return m, 0, n
    counts, bmin, bmax, _ = shell_stats(m, labels, n)
    diag = float(np.linalg.norm(bmax.max(axis=0) - bmin.min(axis=0)))
    shell_diag = np.linalg.norm(bmax - bmin, axis=1)
    dust = (counts < opts.dust_face_count) | (shell_diag < opts.dust_diag_rel * diag)
    if dust.all() or not dust.any():
        return m, 0, n
    m.update_faces(~dust[labels])
    m.remove_unreferenced_vertices()
    return m, int(dust.sum()), int((~dust).sum())


def _boundary_components(m: trimesh.Trimesh):
    """Directed boundary edges (a->b) grouped into loops joined by shared vertices."""
    counts = np.bincount(m.edges_unique_inverse)
    boundary = counts[m.edges_unique_inverse] == 1
    be = m.edges[boundary]
    if len(be) == 0:
        return []
    verts, inv = np.unique(be.ravel(), return_inverse=True)
    local = inv.reshape(-1, 2)
    from scipy import sparse
    from scipy.sparse import csgraph

    g = sparse.coo_matrix(
        (np.ones(len(local), dtype=np.int8), (local[:, 0], local[:, 1])),
        shape=(len(verts), len(verts)),
    )
    _, lab = csgraph.connected_components(g, directed=False)
    edge_lab = lab[local[:, 0]]
    order = np.argsort(edge_lab, kind="stable")
    counts = np.bincount(edge_lab)
    return np.split(be[order], np.cumsum(counts)[:-1])


def _split_cycles(edges: np.ndarray) -> tuple[list[list[int]], np.ndarray]:
    """Chain directed edges a->b into simple loops (no vertex twice). A pinched boundary, where
    one vertex has 2+ outgoing edges, is cut into several loops. Returns (loops, leftover
    edges that never closed, as an (n, 2) array)."""
    out: dict[int, list[int]] = {}
    for a, b in edges:
        out.setdefault(int(a), []).append(int(b))
    loops: list[list[int]] = []
    for start in list(out):
        while out[start]:
            path = [start]
            where = {start: 0}
            while out.get(path[-1]):
                b = out[path[-1]].pop()
                if b not in where:
                    where[b] = len(path)
                    path.append(b)
                    continue
                i = where[b]  # the walk came back to a vertex it already saw: a loop closed
                loops.append(path[i:])
                for v in path[i + 1:]:
                    del where[v]
                del path[i + 1:]
                if len(path) == 1:
                    break
            if len(path) > 1:  # dead end: the chain does not close, so its edges stay open
                for u, v in zip(path[:-1], path[1:], strict=True):
                    out[u].append(v)
                break
    left = np.asarray([(a, b) for a, bs in out.items() for b in bs], dtype=np.int64).reshape(-1, 2)
    return loops, left


def fill_boundary_loops(m: trimesh.Trimesh) -> tuple[trimesh.Trimesh, int]:
    """Close every hole: 3 edges -> 1 triangle, 4 edges -> 2 triangles, more -> centroid fan.
    A pinched boundary is cut into simple loops first. Needs consistent winding.
    Returns (mesh, holes_filled)."""
    comps = _boundary_components(m)
    if not comps:
        return m, 0
    verts = m.vertices
    new_verts: list[np.ndarray] = []
    new_faces: list[list[int]] = []
    nv = len(verts)
    filled = 0
    for edges in comps:
        loops, left = _split_cycles(edges)
        fans: list[tuple[np.ndarray, np.ndarray]] = []
        for lp in loops:
            if len(lp) == 3:
                a, b, c = lp
                new_faces.append([a, c, b])
            elif len(lp) == 4:
                r = [lp[0], lp[3], lp[2], lp[1]]  # reversed loop
                d0 = np.linalg.norm(verts[r[0]] - verts[r[2]])
                d1 = np.linalg.norm(verts[r[1]] - verts[r[3]])
                if d1 < d0:
                    r = r[1:] + r[:1]
                new_faces += [[r[0], r[1], r[2]], [r[0], r[2], r[3]]]
            elif len(lp) > 4:
                fans.append((np.asarray(lp), np.asarray(list(zip(lp, lp[1:] + lp[:1], strict=True)))))
        filled += len(loops)
        if len(left):  # chains that did not close (bad winding): fan them as one hole
            fans.append((np.unique(left.ravel()), left))
            filled += 1
        for ids, es in fans:
            new_verts.append(verts[ids].mean(axis=0))
            c = nv
            nv += 1
            for a, b in es:
                new_faces.append([int(b), int(a), c])
    if not new_faces:
        return m, 0
    allv = np.vstack([verts] + [v[None, :] for v in new_verts]) if new_verts else verts
    allf = np.vstack([m.faces, np.asarray(new_faces, dtype=np.int64)])
    return meshutil.new_mesh(allv, allf), filled


def cut_and_refill_pinches(
    m: trimesh.Trimesh, rings: int = 2, max_share: float = 0.05, max_rounds: int = 5
) -> tuple[trimesh.Trimesh, int]:
    """Fix edges shared by 3+ faces (two surfaces touching along a line) without moving
    anything else: cut out the faces around them, then fill the small holes that leave.
    Gives up (returns the input) if the cut would touch more than `max_share` of the faces.
    Needs merged vertices. Returns (mesh, faces_cut)."""
    total_cut = 0
    start = m
    for rnd in range(max_rounds):
        counts = np.bincount(m.edges_unique_inverse)
        bad = np.where(counts > 2)[0]
        if len(bad) == 0:
            break
        sel = np.isin(m.edges_unique_inverse, bad).reshape(-1, 3).any(axis=1)
        for _ in range(rings if rnd == 0 else 1):
            used = np.zeros(len(m.vertices), dtype=bool)
            used[m.faces[sel].ravel()] = True
            sel = used[m.faces].any(axis=1)
        total_cut += int(sel.sum())
        if total_cut > max_share * len(start.faces):
            return start, 0
        m = meshutil.new_mesh(m.vertices, m.faces[~sel])
        m.remove_unreferenced_vertices()
        if len(m.faces) == 0:
            return start, 0
        try:
            trimesh.repair.fix_winding(m)
        except Exception:
            return start, 0
        m, _ = fill_boundary_loops(m)
    return m, total_cut


def orient_shells(m: trimesh.Trimesh) -> tuple[trimesh.Trimesh, int]:
    """Make each closed shell point outward. A negative shell that sits inside another shell's
    bounding box is treated as a cavity and left alone. Returns (mesh, shells_flipped)."""
    n, labels = meshutil.vertex_shell_labels(m)
    if n == 0:
        return m, 0
    _, bmin, bmax, vols = shell_stats(m, labels, n)
    if n == 1:
        if vols[0] < 0:
            m.invert()
            return m, 1
        return m, 0
    neg = np.where(vols < 0)[0]
    if len(neg) == 0:
        return m, 0
    # Only the biggest positive shells can contain a cavity: keep the test cheap on noisy scans.
    pos = np.where(vols > 0)[0]
    size = np.linalg.norm(bmax[pos] - bmin[pos], axis=1)
    pos = pos[np.argsort(size)[::-1][:64]]
    flip = np.zeros(n, dtype=bool)
    for s_ in neg:
        inside = (
            ((bmin[pos] <= bmin[s_]).all(axis=1) & (bmax[s_] <= bmax[pos]).all(axis=1)).any()
            if len(pos) else False
        )
        if not inside:
            flip[s_] = True
    if flip.any():
        faces = m.faces.copy()
        sel = flip[labels]
        faces[sel] = faces[sel][:, ::-1]
        m.faces = faces
    return m, int(flip.sum())


class TrimeshFixes:
    key = "tier1"
    name = "Tier 1 (trimesh)"
    engine = "trimesh"

    def available(self) -> bool:
        return True

    def run(self, mesh: trimesh.Trimesh, opts: RepairOptions) -> TierOutput:
        actions: list[str] = []
        notes: list[str] = []
        m = meshutil.new_mesh(mesh.vertices.copy(), mesh.faces.copy())
        if len(m.faces) == 0:
            return TierOutput(m, actions)

        # Dust first: a lone speck triangle would otherwise be "filled" into a flat double face.
        m, removed, big = remove_dust_shells(m, opts)
        if removed:
            actions.append(f"removed {removed} tiny floating shells")
        if big > 1:
            notes.append(f"{big} separate bodies kept")

        try:
            trimesh.repair.fix_winding(m)
            actions.append("made winding consistent")
        except Exception as exc:  # fail soft, report loud
            notes.append(f"winding fix failed: {exc}")

        m, holes = fill_boundary_loops(m)
        if holes:
            actions.append(f"filled {holes} hole{'s' if holes != 1 else ''}")

        m, flipped = orient_shells(m)
        if flipped:
            actions.append(f"flipped {flipped} inside-out shells")
        return TierOutput(m, actions, notes)
