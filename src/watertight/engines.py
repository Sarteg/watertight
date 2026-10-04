"""Which repair engines are installed. Uses find_spec so nothing heavy is imported."""

from __future__ import annotations

import importlib.util


def engines() -> dict[str, bool]:
    def has(name: str) -> bool:
        return importlib.util.find_spec(name) is not None

    return {"trimesh": has("trimesh"), "manifold3d": has("manifold3d"), "pymeshfix": has("pymeshfix")}
