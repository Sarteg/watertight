from pathlib import Path

import pytest
import trimesh

from watertight.core.errors import InputError, InvalidSTL
from watertight.core.io import (
    cleanup_stale_tmp,
    is_repaired_name,
    load_stl,
    output_path_for,
    tmp_path_for,
    write_atomic,
)


def test_output_name_keeps_stem_case_and_lowercases_ext():
    assert output_path_for(Path("/prints/Gear.STL")) == Path("/prints/Gear_repaired.stl")
    assert output_path_for(Path("/p/x.stl")) == Path("/p/x_repaired.stl")


def test_repaired_of_repaired_is_not_special_cased():
    assert output_path_for(Path("/p/x_repaired.stl")) == Path("/p/x_repaired_repaired.stl")
    assert is_repaired_name(Path("/p/x_repaired.stl"))
    assert not is_repaired_name(Path("/p/x.stl"))


def test_path_with_spaces(fx, tmp_path):
    folder = tmp_path / "a b"
    folder.mkdir()
    p = fx.clean_cube()
    q = folder / "my model.stl"
    q.write_bytes(p.read_bytes())
    assert output_path_for(q).name == "my model_repaired.stl"
    assert len(load_stl(q).mesh.faces) == 12


def test_load_binary_and_ascii_same_shape(fx):
    b = load_stl(fx.clean_cube("b.stl"))
    a = load_stl(fx.clean_cube("a.stl", ascii_=True))
    assert b.kind == "binary" and a.kind == "ascii"
    assert len(a.mesh.faces) == len(b.mesh.faces) == 12
    assert len(b.mesh.vertices) == 36  # raw: 3 per triangle, not merged


def test_bad_inputs(fx, tmp_path):
    with pytest.raises(InvalidSTL, match="Not a valid STL"):
        load_stl(fx.not_stl())
    with pytest.raises(InvalidSTL, match="empty"):
        load_stl(fx.empty())
    with pytest.raises(InputError, match="File not found"):
        load_stl(tmp_path / "nope.stl")
    with pytest.raises(InputError, match="directory"):
        load_stl(tmp_path)


def test_truncated_binary_reads_what_is_there(fx):
    loaded = load_stl(fx.truncated())
    assert len(loaded.mesh.faces) == 8
    assert any("Header says 12" in w for w in loaded.warnings)


def test_atomic_write_and_no_overwrite(tmp_path):
    from watertight.core.errors import OutputExists

    m = trimesh.creation.box()
    out = tmp_path / "x_repaired.stl"
    tmp = tmp_path / ".x.tmp"
    write_atomic(m, out, tmp, overwrite=False)
    assert out.exists() and not tmp.exists()
    before = out.read_bytes()
    with pytest.raises(OutputExists):
        write_atomic(m, out, tmp, overwrite=False)
    assert out.read_bytes() == before and not tmp.exists()
    write_atomic(m, out, tmp, overwrite=True)
    assert out.exists() and not tmp.exists()


def test_cleanup_stale_tmp_only_matches_our_pattern(tmp_path):
    import os
    import time

    ours = tmp_path / ".a_repaired.stl.watertight-3.tmp"
    other = tmp_path / "notes.tmp"
    ours.write_bytes(b"x")
    other.write_bytes(b"x")
    old = time.time() - 3600
    os.utime(ours, (old, old))
    os.utime(other, (old, old))
    assert cleanup_stale_tmp(tmp_path) == 1
    assert not ours.exists() and other.exists()
    assert tmp_path_for(Path("/p/a_repaired.stl"), 3).name == ".a_repaired.stl.watertight-3.tmp"


def test_write_works_when_hard_links_are_not_supported(tmp_path, monkeypatch):
    """Many SMB/NFS shares refuse os.link. The write must fall back and still be atomic."""
    import errno
    import os

    def no_link(*a, **k):
        raise OSError(errno.EPERM, "Operation not permitted")

    monkeypatch.setattr(os, "link", no_link)
    m = trimesh.creation.box()
    out, tmp = tmp_path / "n_repaired.stl", tmp_path / ".n.tmp"
    write_atomic(m, out, tmp, overwrite=False)
    assert out.exists() and not tmp.exists()
