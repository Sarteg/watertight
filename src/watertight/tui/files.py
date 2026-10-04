"""Find STL files for selection. No UI code here."""

from __future__ import annotations

import os
from pathlib import Path

from ..core.io import is_repaired_name


def is_loop(path: Path) -> bool:
    """A symlinked folder that points at one of its own parents would loop forever."""
    try:
        if not path.is_symlink():
            return False
        target = path.resolve()
        return target == path.parent.resolve() or target in path.parent.resolve().parents
    except OSError:
        return True


def stl_files(folder: Path, recursive: bool = False, show_hidden: bool = False) -> list[Path]:
    """STL files in a folder. `_repaired` files are skipped. Never follows symlinked folders."""
    out: list[Path] = []
    stack = [folder]
    while stack:
        cur = stack.pop()
        try:
            entries = sorted(os.scandir(cur), key=lambda e: e.name.lower())
        except OSError:
            continue
        for e in entries:
            if e.name.startswith(".") and not show_hidden:
                continue
            try:
                if e.is_dir(follow_symlinks=False):
                    if recursive:
                        stack.append(Path(e.path))
                elif e.is_file() and e.name.lower().endswith(".stl"):
                    p = Path(e.path)
                    if not is_repaired_name(p):
                        out.append(p)
            except OSError:
                continue
    return sorted(out, key=lambda p: (str(p.parent).lower(), p.name.lower()))


def human_size(n: int) -> str:
    size = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{n} B"
