from __future__ import annotations

from pathlib import Path

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, OptionList, Static
from textual.widgets.option_list import Option

from ..engines import engines
from ..locations import MOUNT_HELP, NETWORK, Location

HELP_TEXT = """\
[b]Explorer[/b]
  arrows        move            space   select / unselect a file or folder
  a / A         select all STL files here / below    n   clear selection
  enter         add the selection to the queue and start (auto-start)
  l             places and drives (home, disks, NETWORK drives)   g   type a path
  u             go up one folder (at a drive root: opens the list)  h   show / hide other files
[b]Queue[/b]
  c             cancel the highlighted file (running or queued)
  X             cancel every queued file; running files finish
  ctrl+c  or  C interrupt EVERYTHING: stop running files, save nothing for them,
                cancel all queued files. Finished files keep their output.
  p / s         pause / resume         x   clear finished rows      r   retry
  + / -         more / fewer workers (max = CPU cores - 1)          o   overwrite on/off
  f             keep trying on/off: after a good repair, try other settings until
                no better result exists, then tell you
[b]App[/b]
  tab           switch pane            ?   this help        q   quit

Repaired files are saved next to the original as NAME_repaired.stl.
Files that are already watertight are skipped. Originals are never changed.
"""


class ConfirmScreen(ModalScreen[bool]):
    BINDINGS = [
        Binding("y", "yes", "Yes"),
        Binding("enter", "yes", "Yes", show=False),
        Binding("n", "no", "No"),
        Binding("escape", "no", "No", show=False),
    ]

    def __init__(self, message: str, yes: str = "Yes", no: str = "No"):
        super().__init__()
        self.message, self.yes_label, self.no_label = message, yes, no

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Static(self.message, id="dialog-text")
            with Horizontal(id="dialog-buttons"):
                yield Button(f"{self.yes_label} (y)", id="yes", variant="primary")
                yield Button(f"{self.no_label} (n)", id="no")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "yes")

    def action_yes(self) -> None:
        self.dismiss(True)

    def action_no(self) -> None:
        self.dismiss(False)


class HelpScreen(ModalScreen[None]):
    BINDINGS = [Binding("escape", "close", "Close"), Binding("q", "close", "Close", show=False),
                Binding("question_mark", "close", "Close", show=False),
                Binding("enter", "close", "Close", show=False)]

    def compose(self) -> ComposeResult:
        eng = engines()
        extra = (
            "" if eng["pymeshfix"] else
            '\n[b]Stronger repair is off.[/b] Install it with:\n  pip install "watertight[meshfix]"\n'
            "(pymeshfix is GPL-3.0/AGPL-3.0; it is installed only if you ask for it.)\n"
        )
        with Vertical(id="dialog"):
            yield Static(HELP_TEXT + extra, id="dialog-text")
            yield Static("[dim]Press esc to close[/dim]")

    def action_close(self) -> None:
        self.dismiss(None)


class LocationsScreen(ModalScreen[Path | None]):
    """Pick a home folder, a local drive, or a network drive."""

    BINDINGS = [Binding("escape", "close", "Close"), Binding("g", "goto", "Type a path"),
                Binding("l", "close", "Close", show=False)]

    def __init__(self, locations: list[Location]):
        super().__init__()
        self.locations = locations

    def compose(self) -> ComposeResult:
        has_net = any(loc.kind == NETWORK for loc in self.locations)
        with Vertical(id="dialog"):
            yield Static("[b]Places and drives[/b]   (enter = open, g = type a path, esc = close)")
            options = []
            for loc in self.locations:
                tag = {"network": "NET ", "drive": "disk", "place": "    "}[loc.kind]
                options.append(Option(f"{tag}  {loc.label:<24} {loc.detail}"))
            yield OptionList(*options, id="locations")
            if not has_net:
                yield Static(f"[dim]{MOUNT_HELP}[/dim]")

    def on_mount(self) -> None:
        self.query_one(OptionList).focus()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(self.locations[event.option_index].path)

    def action_close(self) -> None:
        self.dismiss(None)

    def action_goto(self) -> None:
        self.dismiss(Path("?goto"))


class GoToScreen(ModalScreen[str | None]):
    """Type or paste a folder path: /Volumes/nas/stl, Z:\\stl, \\\\server\\share\\stl, ~/prints"""

    BINDINGS = [Binding("escape", "close", "Close")]

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Static("[b]Go to folder[/b]   (enter = open, esc = cancel)\n"
                         "[dim]Examples: ~/prints   /Volumes/nas/stl   Z:\\stl\n"
                         "          \\\\server\\share\\stl[/dim]")
            yield Input(placeholder="folder path", id="goto-input")

    def on_mount(self) -> None:
        self.query_one(Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value.strip().strip('"').strip("'") or None)

    def action_close(self) -> None:
        self.dismiss(None)
