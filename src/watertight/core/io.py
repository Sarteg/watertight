"""Read STL files and write repaired STL files safely."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import trimesh

from .errors import InputError, InvalidSTL, OutputError, OutputExists, TooLarge

MAX_FILE_BYTES = 1_000_000_000
MAX_FACES = 20_000_000
SUFFIX = "_repaired"

_BIN_DTYPE = np.dtype([("n", "<f4", (3,)), ("v", "<f4", (3, 3)), ("a", "<u2")])


@dataclass
class Loaded:
    mesh: trimesh.Trimesh
    kind: str  # "binary" or "ascii"
    warnings: list[str] = field(default_factory=list)


def output_path_for(path: Path) -> Path:
    """`/a/Gear.STL` -> `/a/Gear_repaired.stl`. Same folder, stem keeps its case."""
    return path.with_name(f"{path.stem}{SUFFIX}.stl")


def tmp_path_for(out_path: Path, job_id: int | str) -> Path:
    return out_path.with_name(f".{out_path.name}.watertight-{job_id}.tmp")


def is_repaired_name(path: Path) -> bool:
    return path.stem.lower().endswith(SUFFIX) and path.suffix.lower() == ".stl"


def _mesh_from_triangles(tris: np.ndarray) -> trimesh.Trimesh:
    n = len(tris)
    vertices = tris.reshape(-1, 3).astype(np.float64)
    faces = np.arange(n * 3, dtype=np.int64).reshape(-1, 3)
    return trimesh.Trimesh(vertices=vertices, faces=faces, process=False)


def check_input_file(path: Path, max_bytes: int = MAX_FILE_BYTES) -> int:
    """Return the file size, or raise a clear error."""
    if not path.exists():
        raise InputError(f"File not found: {path}")
    if path.is_dir():
        raise InputError("Expected a file, got a directory")
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise InputError(f"Cannot read {path}: {exc.strerror or exc}") from exc
    if size == 0:
        raise InvalidSTL("Not a valid STL file (file is empty)")
    if size > max_bytes:
        raise TooLarge("File too large")
    return size


def load_stl(path: Path, max_bytes: int = MAX_FILE_BYTES, max_faces: int = MAX_FACES) -> Loaded:
    """Load a binary or ASCII STL. Vertices are NOT merged: STL stores 3 per triangle."""
    size = check_input_file(path, max_bytes)
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise InputError(f"Cannot read {path}: {exc.strerror or exc}") from exc

    warnings: list[str] = []
    count = int.from_bytes(data[80:84], "little") if len(data) >= 84 else -1
    if len(data) >= 84 and size == 84 + 50 * count:
        kind = "binary"
    elif _looks_ascii(data):
        kind = "ascii"
    elif len(data) >= 84 + 50 and count > 0:
        kind = "binary"
    else:
        raise InvalidSTL("Not a valid STL file")

    if kind == "binary":
        available = (len(data) - 84) // 50
        n = min(count, available)
        if n != count:
            warnings.append(f"Header says {count} triangles, file holds {n}; read {n}")
        elif len(data) != 84 + 50 * count:
            warnings.append("Extra bytes after the last triangle were ignored")
        if n == 0:
            raise InvalidSTL("STL has no triangles")
        if n > max_faces:
            raise TooLarge("File too large")
        arr = np.frombuffer(data, dtype=_BIN_DTYPE, count=n, offset=84)
        mesh = _mesh_from_triangles(arr["v"])
    else:
        mesh = _load_ascii(data)
        if len(mesh.faces) > max_faces:
            raise TooLarge("File too large")
    return Loaded(mesh=mesh, kind=kind, warnings=warnings)


def _looks_ascii(data: bytes) -> bool:
    head = data[:1024].lstrip().lower()
    return head.startswith(b"solid") and b"facet" in data[:4096].lower()


def _load_ascii(data: bytes) -> trimesh.Trimesh:
    # Parse "vertex x y z" lines ourselves: fast, and tolerant of odd whitespace.
    verts: list[list[float]] = []
    try:
        for line in data.decode("utf-8", errors="replace").splitlines():
            parts = line.split()
            if len(parts) == 4 and parts[0].lower() == "vertex":
                verts.append([float(parts[1]), float(parts[2]), float(parts[3])])
    except ValueError as exc:
        raise InvalidSTL("Not a valid STL file (bad number in ASCII STL)") from exc
    if not verts:
        raise InvalidSTL("STL has no triangles")
    if len(verts) % 3:
        raise InvalidSTL("Not a valid STL file (vertex count is not a multiple of 3)")
    tris = np.asarray(verts, dtype=np.float64).reshape(-1, 3, 3)
    return _mesh_from_triangles(tris)


def stl_bytes(mesh: trimesh.Trimesh) -> bytes:
    """Binary STL bytes. Deterministic: trimesh writes a zero header."""
    data = mesh.export(file_type="stl")
    return data if isinstance(data, bytes) else bytes(data)


def write_atomic(
    mesh: trimesh.Trimesh, out_path: Path, tmp_path: Path, overwrite: bool, before_rename=None
) -> None:
    """Write `tmp_path`, then rename onto `out_path`. The output appears only by that rename."""
    if out_path.exists() and not overwrite:
        raise OutputExists(f"Output exists: {out_path.name} (use --force to overwrite)")
    payload = stl_bytes(mesh)
    try:
        with open(tmp_path, "wb") as fh:
            fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())
        if before_rename is not None:
            before_rename()
        if overwrite:
            os.replace(tmp_path, out_path)
        else:
            try:
                os.link(tmp_path, out_path)  # fails if out_path appeared meanwhile
            except FileExistsError as exc:
                raise OutputExists(f"Output exists: {out_path.name}") from exc
            except OSError:
                if out_path.exists():
                    raise OutputExists(f"Output exists: {out_path.name}") from None
                os.replace(tmp_path, out_path)
                return
            tmp_path.unlink(missing_ok=True)
    except OutputExists:
        tmp_path.unlink(missing_ok=True)
        raise
    except OSError as exc:
        tmp_path.unlink(missing_ok=True)
        raise OutputError(f"Cannot write to {out_path.parent}: {exc.strerror or exc}") from exc


def ensure_writable(folder: Path) -> None:
    """Really try to create and delete a tiny file. `os.access` lies on many network shares."""
    probe = folder / f".watertight-probe-{os.getpid()}.tmp"
    try:
        with open(probe, "xb") as fh:
            fh.write(b"x")
        probe.unlink()
    except OSError as exc:
        try:
            probe.unlink(missing_ok=True)
        except OSError:
            pass
        raise OutputError(f"Cannot write to {folder}: {exc.strerror or exc}") from exc


def cleanup_stale_tmp(folder: Path, older_than_s: float = 600.0) -> int:
    """Remove leftover `.<name>.watertight-<id>.tmp` files in `folder`. Returns count."""
    import time

    removed = 0
    try:
        entries = list(folder.iterdir())
    except OSError:
        return 0
    now = time.time()
    for p in entries:
        n = p.name
        if n.startswith(".") and ".watertight-" in n and n.endswith(".tmp"):
            try:
                if now - p.stat().st_mtime > older_than_s:
                    p.unlink()
                    removed += 1
            except OSError:
                pass
    return removed
