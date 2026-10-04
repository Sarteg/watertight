"""Result objects and text formatting."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path

from .check import CheckResult

DONE = "repaired"
SKIPPED = "skipped"
NEEDS_REVIEW = "needs_review"
FAILED = "failed"


@dataclass
class RepairResult:
    path: str
    status: str = FAILED  # repaired | skipped | needs_review | failed
    output_path: str | None = None
    file_kind: str = ""  # binary / ascii
    tier_used: str | None = None
    check_before: CheckResult | None = None
    check_after: CheckResult | None = None
    actions: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    message: str = ""  # short reason (shown in the queue)
    error_kind: str = ""  # "", "input", "output", "internal"
    volume_change_pct: float | None = None
    bbox_change_pct: float = 0.0
    refine: dict | None = None  # what the keep-trying mode did, when it was on
    shape: dict | None = None  # numbers and limits of the shape check, for the report
    duration_s: float = 0.0
    timings: list = field(default_factory=list)  # [(step, seconds)] to see what was slow

    def to_dict(self) -> dict:
        d = asdict(self)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> RepairResult:
        d = dict(d)
        for k in ("check_before", "check_after"):
            if d.get(k):
                d[k] = CheckResult.from_dict(d[k])
        return cls(**d)

    @property
    def exit_code(self) -> int:
        if self.status in (DONE, SKIPPED):
            return 0
        if self.status == NEEDS_REVIEW:
            return 1
        return 3 if self.error_kind == "internal" else 2


def problems(c: CheckResult) -> list[str]:
    """What is wrong with a mesh, in plain words."""
    out = []
    if c.open_edges:
        out.append(f"{c.open_edges:,} open edges (holes in the surface)")
    if c.nonmanifold_edges:
        out.append(f"{c.nonmanifold_edges:,} pinched edges (3 or more faces meet on one edge)")
    if not c.winding_consistent:
        out.append("faces point in mixed directions")
    if c.volume < 0:
        out.append("the model is inside-out")
    if c.degenerate_faces:
        out.append(f"{c.degenerate_faces:,} empty faces (no area)")
    if c.duplicate_faces:
        out.append(f"{c.duplicate_faces:,} duplicate faces")
    return out


TIER_LABELS = {
    "Tier 0": "Clean up",
    "Tier 1": "Fix face directions and fill holes",
    "Tier 2": "Cut out pinch points and rebuild",
    "Tier 3": "Strong repair (pymeshfix)",
}


def _steps(actions: list[str]) -> list[str]:
    """Group the action list by tier: '1. Clean up: merged ...; removed ...'."""
    groups: dict[str, list[str]] = {}
    for a in actions:
        head, _, text = a.partition(": ")
        groups.setdefault(head.split(" (")[0], []).append(text or head)
    return [
        f"{i}. {TIER_LABELS.get(tier, tier)}: " + "; ".join(texts)
        for i, (tier, texts) in enumerate(groups.items(), 1)
    ]


def _fmt_pct(x: float) -> str:
    return f"{x:+.2f}%" if abs(x) >= 0.005 else "no change"


def shape_lines(r: RepairResult) -> list[str]:
    """The shape check: what changed, and the limit it must stay inside."""
    sh = r.shape
    if not sh:
        return []
    out = []
    v = sh.get("volume_change_pct")
    if v is None:
        out.append("  --    Volume   not compared (the original had holes, so its volume means nothing)")
    else:
        ok = abs(v) <= sh["volume_limit_pct"]
        out.append(
            f"  {'OK  ' if ok else 'FAIL'}  Volume   {sh['volume_before']:.5g} -> {sh['volume_after']:.5g}  "
            f"({_fmt_pct(v)}, limit {sh['volume_limit_pct']:g}%)"
        )
    z = sh["size_change_pct"]
    out.append(
        f"  {'OK  ' if z <= sh['size_limit_pct'] else 'FAIL'}  Size     "
        f"{'no change' if z < 0.005 else f'changed {z:.2f}%'}  (limit {sh['size_limit_pct']:g}%)"
    )
    f = sh.get("far_share_pct")
    if f is None:
        out.append("  --    Surface  not compared (model too small)")
    else:
        out.append(
            f"  {'OK  ' if f <= sh['far_limit_pct'] else 'FAIL'}  Surface  "
            f"{f:.2f}% of it moved more than {sh['far_mm']:.2g} mm  (limit {sh['far_limit_pct']:g}%)"
        )
    return out


def refine_message(r: RepairResult) -> str:
    """One plain sentence on what keep-trying mode did. Empty when it was off."""
    f = r.refine
    if not f:
        return ""
    n = f["tries"]
    if f["stopped"] == "nothing to vary":
        return "Keep trying: there is nothing else to try for this file."
    if f["stopped"] == "try limit":
        return f"Keep trying: stopped at the limit of {n} tries. A better result may still exist."
    if f["improved"]:
        return f"Keep trying: found a better result after {n} tries. It cannot get any better."
    return f"Keep trying: {n} other settings tried. It cannot get any better."


def details_lines(r: RepairResult) -> list[str]:
    """Plain lines for the TUI details pane and the headless report."""
    lines = [f"File:     {r.path}"]
    if r.file_kind and r.check_before:
        lines.append(f"Input:    {r.file_kind} STL, {r.check_before.faces:,} faces")
    if r.check_before and r.status != FAILED:
        lines.append("")
        found = problems(r.check_before)
        lines.append("Problems found:" + ("" if found else " none"))
        lines += [f"  - {p}" for p in found]
    if r.status == SKIPPED:
        lines.append("")
        lines.append("Result:   already watertight, skipped (nothing written)")
    elif r.status in (DONE, NEEDS_REVIEW):
        lines.append("")
        lines.append("What the app did:")
        steps = _steps(r.actions)
        lines += [f"  {x}" for x in steps] if steps else ["  nothing it could change"]
        shape = shape_lines(r)
        if shape:
            lines.append("")
            lines.append("Shape check (original vs repaired):")
            lines += shape
        lines.append("")
        if r.output_path:
            tag = "Repaired" if r.status == DONE else "Saved, but please review"
            lines.append(f"Result:   {tag} ({r.tier_used or 'best effort'})")
            lines.append(f"Output:   {r.output_path}")
        else:
            lines.append(f"Result:   NOT saved. {r.message}")
        c = r.check_after
        if c:
            left = problems(c)
            if r.output_path or left:
                lines.append("Left over:" + (" nothing, the file is watertight" if not left else ""))
                lines += [f"  - {p}" for p in left]
    if r.status == FAILED:
        lines.append(f"Failed:   {r.message}")
    if r.refine:
        lines.append("")
        lines.append(refine_message(r))
        lines += [f"  - {x}" for x in r.refine.get("log", [])]
        lines.append("")
    if r.timings:
        lines.append("Time:     " + ", ".join(f"{s} {t:.1f}s" for s, t in r.timings))
    for w in r.warnings:
        lines.append(w)
    for n in r.notes:
        lines.append(f"Note:     {n}")
    return lines


def headless_line(r: RepairResult) -> str:
    name = Path(r.path).name
    if r.status == SKIPPED:
        return f"{name}: already watertight, skipped"
    if r.status == FAILED:
        return f"{name}: FAILED  {r.message}"
    tag = "repaired" if r.status == DONE else "NEEDS REVIEW"
    extra = ""
    if r.volume_change_pct is not None:
        extra = f"  vol {r.volume_change_pct:+.2f}%"
    out = Path(r.output_path).name if r.output_path else "-"
    if r.refine:
        extra += "  " + refine_message(r).replace("Keep trying: ", "[keep trying] ")
    msg = f"  ({r.message})" if r.message and r.status == NEEDS_REVIEW else ""
    return f"{name}: {tag}  {r.tier_used or ''}{extra}  -> {out}{msg}"
