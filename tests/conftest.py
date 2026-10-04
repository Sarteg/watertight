"""Fixtures are generated in code, so the repo stays small and licence-clean."""

from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np
import pytest
import trimesh


def sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _box() -> trimesh.Trimesh:
    return trimesh.creation.box(extents=(10, 10, 10))


def write_stl(mesh: trimesh.Trimesh, path: Path, ascii_: bool = False) -> Path:
    """Write triangles with 3 separate vertices each, like real STL files."""
    data = mesh.export(file_type="stl_ascii" if ascii_ else "stl")
    mode = "w" if ascii_ else "wb"
    with open(path, mode) as fh:
        fh.write(data)
    return path


def drop_faces(mesh: trimesh.Trimesh, idx: list[int]) -> trimesh.Trimesh:
    keep = np.ones(len(mesh.faces), dtype=bool)
    keep[idx] = False
    m = mesh.copy()
    m.update_faces(keep)
    return m


class Fixtures:
    def __init__(self, folder: Path):
        self.folder = folder

    def path(self, name: str) -> Path:
        return self.folder / name

    def clean_cube(self, name="cube.stl", ascii_=False):
        return write_stl(_box(), self.path(name), ascii_)

    def hole_cube(self, name="hole.stl"):
        return write_stl(drop_faces(_box(), [3]), self.path(name))

    def big_hole_cube(self, name="bighole.stl"):
        # remove both triangles of one face -> a square (4-edge) hole
        m = _box()
        n = m.face_normals
        top = [i for i in range(len(m.faces)) if n[i][2] > 0.9]
        return write_stl(drop_faces(m, top), self.path(name))

    def flipped_cube(self, name="flipped.stl"):
        m = _box()
        f = m.faces.copy()
        f[[0, 5]] = f[[0, 5]][:, ::-1]
        return write_stl(trimesh.Trimesh(m.vertices, f, process=False), self.path(name))

    def inside_out_cube(self, name="inside.stl"):
        m = _box()
        m.invert()
        return write_stl(m, self.path(name))

    def dup_cube(self, name="dup.stl"):
        m = _box()
        f = np.vstack([m.faces, m.faces[:3]])
        return write_stl(trimesh.Trimesh(m.vertices, f, process=False), self.path(name))

    def degenerate_cube(self, name="degenerate.stl"):
        m = _box()
        v = np.vstack([m.vertices, [[0, 0, 0], [0, 0, 0], [0, 0, 0]]])
        f = np.vstack([m.faces, [[8, 9, 10]]])
        return write_stl(trimesh.Trimesh(v, f, process=False), self.path(name))

    def speck_cube(self, name="speck.stl"):
        m = _box()
        v = np.vstack([m.vertices, [[4, 0, 0], [4.01, 0, 0], [4, 0.01, 0]]])  # inside the cube's bbox
        f = np.vstack([m.faces, [[8, 9, 10]]])
        return write_stl(trimesh.Trimesh(v, f, process=False), self.path(name))

    def fin_cube(self, name="fin.stl"):
        # an extra internal triangle hanging off a cube edge: 3 faces on one edge
        m = _box()
        v = np.vstack([m.vertices, [[0, 0, 0]]])  # third corner at the centre: fin is inside
        e = m.edges_unique[0]
        f = np.vstack([m.faces, [[e[0], e[1], 8]]])
        return write_stl(trimesh.Trimesh(v, f, process=False), self.path(name))

    def two_cubes_far(self, name="two.stl"):
        a, b = _box(), _box()
        b.apply_translation([30, 0, 0])
        return write_stl(trimesh.util.concatenate([a, b]), self.path(name))

    def shared_edge_cubes(self, name="edge.stl"):
        a, b = _box(), _box()
        b.apply_translation([10, 10, 0])  # touch along one vertical edge
        return write_stl(trimesh.util.concatenate([a, b]), self.path(name))

    def figure8_sphere(self, name="figure8.stl"):
        # two holes that touch at one vertex: only pymeshfix (Tier 3) can close this
        s = trimesh.creation.icosphere(subdivisions=3)
        v0 = s.faces[0][0]
        fan = [i for i in range(len(s.faces)) if v0 in s.faces[i]]
        keep = np.ones(len(s.faces), dtype=bool)
        keep[[fan[0], fan[3]]] = False
        return write_stl(trimesh.Trimesh(s.vertices, s.faces[keep], process=False), self.path(name))

    def not_stl(self, name="bad.stl"):
        p = self.path(name)
        p.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 40)
        return p

    def empty(self, name="empty.stl"):
        p = self.path(name)
        p.write_bytes(b"")
        return p

    def truncated(self, name="trunc.stl"):
        data = _box().export(file_type="stl")
        p = self.path(name)
        p.write_bytes(data[: 84 + 50 * 8])  # header says 12, file holds 8
        return p


@pytest.fixture
def fx(tmp_path) -> Fixtures:
    return Fixtures(tmp_path)
