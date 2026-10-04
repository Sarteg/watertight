"""Progress events. The core calls `emit(step, message)`; workers forward them to the UI."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

# Step name -> overall percent for one job. Steps that are skipped jump forward.
STEP_PCT = {
    "load": 10,
    "check": 20,
    "tier0": 35,
    "tier1": 55,
    "tier2": 75,
    "tier3": 90,
    "tier4": 92,
    "validate": 95,
    "refine": 96,
    "write": 100,
}

STEP_LABEL = {
    "load": "Loading",
    "check": "Checking",
    "tier0": "Repairing T0",
    "tier1": "Repairing T1",
    "tier2": "Repairing T2",
    "tier3": "Repairing T3",
    "tier4": "Repairing T4",
    "validate": "Validating",
    "refine": "Refining",
    "write": "Writing",
    "done": "Done",
}


@dataclass
class ProgressEvent:
    job_id: int
    step: str
    pct: int
    message: str = ""


Emit = Callable[[str, str], None]


def noop_emit(step: str, message: str = "") -> None:
    return None
