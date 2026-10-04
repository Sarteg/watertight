"""Keep trying. After the first good repair, try other settings and keep the best result.
Stop when no neighbouring setting is better, or at the try limit."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

import trimesh

from .check import CheckResult
from .validate import GuardResult

MIN_RINGS, MAX_RINGS = 0, 6  # the setting that is varied: faces cut around each pinch point

NO_BETTER = "no better result"
TRY_LIMIT = "try limit"


@dataclass
class Attempt:
    mesh: trimesh.Trimesh
    check: CheckResult
    guard: GuardResult
    actions: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def quality(a: Attempt) -> tuple:
    """Lower is better. Order matters: stay inside the limits, be watertight, leave nothing
    wrong, keep the surface where it was. Small differences are rounded away so noise does
    not count as progress."""
    g, c = a.guard, a.check
    return (
        bool(g.warnings),
        not c.ok,
        c.open_edges + c.nonmanifold_edges,
        round((g.far_share_pct or 0.0) / 0.05),
        round((g.mean_dist_rel or 0.0) / 0.02),
    )


@dataclass
class RefineOutcome:
    best: Attempt
    best_rings: int | None
    tries: int = 0
    improved: bool = False
    stopped: str = NO_BETTER
    log: list[str] = field(default_factory=list)

    def summary(self) -> dict:
        return {
            "tries": self.tries,
            "improved": self.improved,
            "stopped": self.stopped,
            "log": self.log,
            "rings": self.best_rings,
        }


def hill_climb(
    start: Attempt,
    start_rings: int | None,
    default_rings: int,
    try_one: Callable[[int], Attempt | None],
    max_tries: int,
    notify: Callable[[str], None] = lambda msg: None,
) -> RefineOutcome:
    """Try the settings next to the best one. Move to a better one and repeat. Stop when
    every neighbour of the best setting has been tried and none is better."""
    out = RefineOutcome(best=start, best_rings=start_rings)
    tried: set[int] = set() if start_rings is None else {start_rings}
    while True:
        if out.best_rings is None:
            todo = [default_rings] if default_rings not in tried else []
        else:
            todo = [r for r in (out.best_rings - 1, out.best_rings + 1)
                    if MIN_RINGS <= r <= MAX_RINGS and r not in tried]
        if not todo:
            return out
        moved = False
        for r in todo:
            if out.tries >= max_tries:
                out.stopped = TRY_LIMIT
                return out
            out.tries += 1
            tried.add(r)
            notify(f"try {out.tries}: cut size {r}")
            try:
                att = try_one(r)
            except Exception as exc:  # a failed try is not a failed file
                out.log.append(f"cut size {r}: failed ({type(exc).__name__})")
                continue
            if att is None:
                out.log.append(f"cut size {r}: nothing to compare")
                continue
            better = quality(att) < quality(out.best)
            out.log.append(f"cut size {r}: {'better, kept' if better else 'not better'}")
            if better:
                out.best, out.best_rings, out.improved, moved = att, r, True, True
        if not moved:
            return out
