"""Tier protocol. A tier takes a mesh and returns a new mesh plus a list of actions it took.
Tiers never print and never touch the UI."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

import trimesh

from ..core.options import RepairOptions


@dataclass
class TierOutput:
    mesh: trimesh.Trimesh
    actions: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)  # hints for the user (e.g. install pymeshfix)


class Tier(Protocol):
    key: str  # "tier0" ... matches the progress step names
    name: str  # human name
    engine: str  # library it needs

    def available(self) -> bool: ...

    def run(self, mesh: trimesh.Trimesh, opts: RepairOptions) -> TierOutput: ...
