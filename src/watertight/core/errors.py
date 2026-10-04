"""Typed errors. Each error maps to a short message and an exit-code kind."""

from __future__ import annotations


class WatertightError(Exception):
    """Base class. `kind` decides the exit code: input/output -> 2, internal -> 3."""

    kind = "internal"

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class InputError(WatertightError):
    kind = "input"


class OutputError(WatertightError):
    kind = "output"


class InvalidSTL(InputError):
    pass


class OutputExists(OutputError):
    pass


class TooLarge(InputError):
    pass
