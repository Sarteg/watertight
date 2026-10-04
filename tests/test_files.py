import os
import sys

import pytest

from watertight.tui.files import human_size, is_loop, stl_files


def test_stl_files_skips_repaired_hidden_and_other_files(fx):
    fx.clean_cube("a.stl")
    fx.clean_cube("B.STL")
    fx.clean_cube("a_repaired.stl")
    fx.clean_cube(".hidden.stl")
    (fx.folder / "c.txt").write_text("x")
    sub = fx.folder / "sub"
    sub.mkdir()
    fx.clean_cube("sub/deep.stl")
    assert [p.name for p in stl_files(fx.folder)] == ["a.stl", "B.STL"]
    assert sorted(p.name for p in stl_files(fx.folder, recursive=True)) == ["B.STL", "a.stl", "deep.stl"]
    assert ".hidden.stl" in [p.name for p in stl_files(fx.folder, show_hidden=True)]


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need privileges on Windows")
def test_symlink_loop_is_not_followed(fx):
    sub = fx.folder / "sub"
    sub.mkdir()
    fx.clean_cube("sub/deep.stl")
    os.symlink(fx.folder, sub / "up", target_is_directory=True)  # points at a parent
    assert is_loop(sub / "up")
    assert not is_loop(sub)
    found = stl_files(fx.folder, recursive=True)  # must terminate
    assert [p.name for p in found] == ["deep.stl"]


def test_human_size():
    assert human_size(12) == "12 B" and human_size(2048) == "2.0 KB"
