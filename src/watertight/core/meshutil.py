"""Small mesh helpers shared by the check, the tiers and the validation."""

from __future__ import annotations

import numpy as np
import trimesh
from scipy import sparse
from scipy.sparse import csgraph


def new_mesh(vertices: np.ndarray, faces: np.ndarray) -> trimesh.Trimesh:
    return trimesh.Trimesh(
        vertices=np.asarray(vertices, dtype=np.float64),
        faces=np.asarray(faces, dtype=np.int64),
        process=False,
    )


def merged(mesh: trimesh.Trimesh) -> trimesh.Trimesh:
    """A copy with identical vertices merged. STL stores 3 vertices per triangle, so every
    edge looks open until vertices are merged. Always merge before testing topology."""
    m = new_mesh(mesh.vertices.copy(), mesh.faces.copy())
    if len(m.faces):
        m.merge_vertices()
    return m


def bbox_diagonal(mesh: trimesh.Trimesh) -> float:
    if len(mesh.vertices) == 0:
        return 0.0
    ext = mesh.vertices.max(axis=0) - mesh.vertices.min(axis=0)
    return float(np.linalg.norm(ext))


def signed_volume(mesh: trimesh.Trimesh) -> float:
    if len(mesh.faces) == 0:
        return 0.0
    tri = mesh.vertices[mesh.faces]
    tri = tri - tri.reshape(-1, 3).mean(axis=0)  # keep numbers small for big offsets
    vol = np.einsum("ij,ij->i", tri[:, 0], np.cross(tri[:, 1], tri[:, 2])).sum() / 6.0
    return float(vol)


def edge_counts(mesh: trimesh.Trimesh) -> tuple[int, int]:
    """(open edges, non-manifold edges). Needs merged vertices to be meaningful."""
    if len(mesh.faces) == 0:
        return 0, 0
    counts = np.bincount(mesh.edges_unique_inverse)
    return int((counts == 1).sum()), int((counts > 2).sum())


def vertex_shell_labels(mesh: trimesh.Trimesh) -> tuple[int, np.ndarray]:
    """Shells = groups of faces joined through shared vertices. Returns (count, label per face)."""
    nf = len(mesh.faces)
    if nf == 0:
        return 0, np.zeros(0, dtype=np.int64)
    nv = len(mesh.vertices)
    f = mesh.faces
    rows = np.concatenate([f[:, 0], f[:, 1], f[:, 2]])
    cols = np.concatenate([f[:, 1], f[:, 2], f[:, 0]])
    graph = sparse.coo_matrix((np.ones(len(rows), dtype=np.int8), (rows, cols)), shape=(nv, nv))
    _, vlabels = csgraph.connected_components(graph, directed=False)
    flabels = vlabels[f[:, 0]]
    uniq, inv = np.unique(flabels, return_inverse=True)
    return len(uniq), inv.astype(np.int64)


def face_components(mesh: trimesh.Trimesh) -> tuple[int, np.ndarray]:
    """Components joined through edges shared by exactly two faces (manifold edges only)."""
    nf = len(mesh.faces)
    adj = mesh.face_adjacency
    if len(adj) == 0:
        return nf, np.arange(nf, dtype=np.int64)
    graph = sparse.coo_matrix(
        (np.ones(len(adj), dtype=np.int8), (adj[:, 0], adj[:, 1])), shape=(nf, nf)
    )
    n, labels = csgraph.connected_components(graph, directed=False)
    return int(n), labels.astype(np.int64)
