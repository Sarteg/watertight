"""Tier 3: strong repair with pymeshfix.

pymeshfix is GPL-3.0 (its PyPI metadata also lists AGPL-3.0), so it is an OPTIONAL extra:
`pip install "watertight[meshfix]"`. Never import it at module top level."""

from __future__ import annotations

import importlib.util

import numpy as np
import trimesh

from ..core import meshutil
from ..core.options import RepairOptions
from .base import TierOutput
from .trimesh_fixes import orient_shells

INSTALL_HINT = 'Stronger repair available: pip install "watertight[meshfix]"'


class MeshFixTier:
    key = "tier3"
    name = "Tier 3 (pymeshfix)"
    engine = "pymeshfix"

    def available(self) -> bool:
        return importlib.util.find_spec("pymeshfix") is not None

    def run(self, mesh: trimesh.Trimesh, opts: RepairOptions) -> TierOutput:
        import pymeshfix  # imported late on purpose (GPL-3.0, optional)

        fixer = pymeshfix.MeshFix(
            np.asarray(mesh.vertices, dtype=np.float64), np.asarray(mesh.faces, dtype=np.int32)
        )
        fixer.repair(joincomp=False, remove_smallest_components=False)
        out = meshutil.new_mesh(fixer.points, fixer.faces)
        out, flipped = orient_shells(out)
        actions = ["repaired with pymeshfix (holes, self-intersections, non-manifold parts)"]
        if flipped:
            actions.append(f"flipped {flipped} inside-out shells")
        return TierOutput(out, actions)
