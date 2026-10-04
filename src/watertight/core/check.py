"""The check: is this mesh already printable? (watertight + consistent winding + volume > 0)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np
import trimesh

from . import meshutil
from .io import load_stl


@dataclass
class CheckResult:
    faces: int = 0
    watertight: bool = False
    winding_consistent: bool = False
    volume: float = 0.0
    volume_positive: bool = False
    shells: int = 0
    open_edges: int = 0
    nonmanifold_edges: int = 0
    degenerate_faces: int = 0
    duplicate_faces: int = 0
    bbox_min: list[float] = field(default_factory=lambda: [0.0, 0.0, 0.0])
    bbox_max: list[float] = field(default_factory=lambda: [0.0, 0.0, 0.0])

    @property
    def ok(self) -> bool:
        """True = no repair needed."""
        return self.watertight and self.winding_consistent and self.volume_positive

    @property
    def closed_topology(self) -> bool:
        return self.open_edges == 0 and self.nonmanifold_edges == 0

    def score(self) -> tuple:
        """Lower is better. Used to pick the best mesh when nothing reaches OK."""
        return (
            not self.ok,
            self.open_edges + self.nonmanifold_edges,
            not self.winding_consistent,
            not self.volume_positive,
        )

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> CheckResult:
        return cls(**d)


def check_mesh(mesh: trimesh.Trimesh) -> CheckResult:
    """Check a mesh. Works on a vertex-merged copy, so raw STL loads give true answers."""
    m = meshutil.merged(mesh)
    res = CheckResult(faces=len(m.faces))
    if len(m.faces) == 0:
        return res
    open_e, nonman_e = meshutil.edge_counts(m)
    res.open_edges, res.nonmanifold_edges = open_e, nonman_e
    res.watertight = open_e == 0 and nonman_e == 0
    res.winding_consistent = bool(m.is_winding_consistent)
    res.volume = meshutil.signed_volume(m)
    res.volume_positive = bool(np.isfinite(res.volume) and res.volume > 0)
    res.shells = meshutil.vertex_shell_labels(m)[0]
    diag = meshutil.bbox_diagonal(m)
    same_index = (
        (m.faces[:, 0] == m.faces[:, 1])
        | (m.faces[:, 1] == m.faces[:, 2])
        | (m.faces[:, 0] == m.faces[:, 2])
    )
    res.degenerate_faces = int((same_index | (m.area_faces <= 1e-12 * diag * diag)).sum())
    res.duplicate_faces = int(
        len(m.faces) - np.unique(np.sort(m.faces, axis=1), axis=0).shape[0]
    )
    res.bbox_min = [float(x) for x in m.vertices.min(axis=0)]
    res.bbox_max = [float(x) for x in m.vertices.max(axis=0)]
    return res


def check_file(path: str | Path) -> CheckResult:
    """Library API: check one STL file. Raises WatertightError for bad input."""
    return check_mesh(load_stl(Path(path)).mesh)
