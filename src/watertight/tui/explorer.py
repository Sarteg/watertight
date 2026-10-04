"""File explorer: a DirectoryTree that shows only folders and .stl files, with checkboxes."""

from __future__ import annotations

import os
from collections.abc import Iterable
from pathlib import Path

from rich.style import Style
from rich.text import Text
from textual.binding import Binding
from textual.widgets import DirectoryTree
from textual.widgets._tree import TreeNode

from ..core.io import is_repaired_name
from .files import human_size, is_loop


class Explorer(DirectoryTree):
    BINDINGS = [
        Binding("space", "toggle", "Select"),
        Binding("enter", "run", "Run"),
        Binding("a", "select_folder", "All here"),
        Binding("A", "select_folder_recursive", "All below", show=False),
        Binding("n", "clear_selection", "None"),
        Binding("u", "parent_folder", "Up", show=False),
        Binding("h", "toggle_hidden", "Hidden/all", show=False),
    ]

    def __init__(self, path: Path, **kwargs):
        self.show_all = False
        # Filled by filter_paths, which Textual runs in a worker thread. render_label only reads
        # these, so a slow network share never blocks drawing.
        self.sizes: dict[Path, int] = {}
        self._locked: set[Path] = set()
        super().__init__(path, **kwargs)

    # ---- what is listed ----------------------------------------------------------------------
    def filter_paths(self, paths: Iterable[Path]) -> list[Path]:
        out = []
        for p in paths:
            if p.name.startswith(".") and not self.show_all:
                continue
            try:
                if p.is_dir():
                    if is_loop(p):
                        continue
                    if not os.access(p, os.R_OK | os.X_OK):
                        self._locked.add(p)
                    out.append(p)
                elif p.suffix.lower() == ".stl" or self.show_all:
                    if p.suffix.lower() == ".stl":
                        self.sizes[p] = p.stat().st_size
                    out.append(p)
            except OSError:
                continue
        return out

    # ---- how a row looks ---------------------------------------------------------------------
    def render_label(self, node: TreeNode, base_style: Style, style: Style) -> Text:
        label = super().render_label(node, base_style, style)
        if node.data is None:
            return label
        path = node.data.path
        selected = getattr(self.app, "selected", set())
        if node._allow_expand:  # a folder
            n = sum(1 for s in selected if _is_under(s, path))
            tail = Text(f"  ({n} selected)", style="bold") if n else Text("")
            if path in self._locked:
                tail = Text("  [locked: no permission]", style="red")
            return Text.assemble(label, tail)
        is_stl = path.suffix.lower() == ".stl"
        if not is_stl:
            return Text.assemble(Text("    "), label, style="dim")
        mark = "[x] " if path in selected else "[ ] "
        parts: list = [Text(mark, style="bold green" if path in selected else "")]
        parts.append(label)
        if path in self.sizes:
            parts.append(Text(f"  {human_size(self.sizes[path])}", style="dim"))
        if is_repaired_name(path):
            parts.append(Text("  (repaired)", style="dim italic"))
            return Text.assemble(*parts, style="dim")
        return Text.assemble(*parts)

    def redraw(self) -> None:
        self._invalidate()

    # ---- helpers -----------------------------------------------------------------------------
    def cursor_path(self) -> Path | None:
        node = self.cursor_node
        if node is None or node.data is None:
            return None
        return node.data.path

    def current_folder(self) -> Path | None:
        p = self.cursor_path()
        if p is None:
            return None
        return p if p.is_dir() else p.parent

    # ---- actions (selection logic lives in the app) ------------------------------------------
    def action_toggle(self) -> None:
        p = self.cursor_path()
        if p is not None:
            self.app.toggle_path(p)

    def action_run(self) -> None:
        p = self.cursor_path()
        if getattr(self.app, "selected", None):
            self.app.run_selected()
        elif p is not None and p.is_file():
            self.app.run_paths([p])
        else:
            self.action_select_cursor()  # a folder: open/close it

    def action_select_folder(self) -> None:
        f = self.current_folder()
        if f is not None:
            self.app.select_folder(f, recursive=False)

    def action_select_folder_recursive(self) -> None:
        f = self.current_folder()
        if f is not None:
            self.app.select_folder(f, recursive=True)

    def action_clear_selection(self) -> None:
        self.app.clear_selection()

    def action_parent_folder(self) -> None:
        here = Path(self.path)
        parent = here.parent
        if parent != here:
            self.app.open_location(parent)
        else:  # already at a drive or share root: show the list of places
            self.app.show_locations()

    def action_toggle_hidden(self) -> None:
        self.show_all = not self.show_all
        self.reload()
        self.app.set_message("Showing all files and hidden folders" if self.show_all
                             else "Showing folders and .stl files only")

    def on_directory_tree_file_selected(self, event: DirectoryTree.FileSelected) -> None:
        """A mouse click on a file toggles its checkbox (Enter is bound to `run`)."""
        event.stop()
        if event.path.suffix.lower() == ".stl":
            self.app.toggle_path(event.path)


def _is_under(path: Path, folder: Path) -> bool:
    try:
        path.relative_to(folder)
        return True
    except ValueError:
        return False
