"""Memory budget for the queue: do not start a job that would push the machine into swap."""

from __future__ import annotations

import os
import sys

# A repair peaks near 12 x the STL file size (measured: 147 MB file, 1.8 GB peak). Add slack.
BYTES_PER_FILE_BYTE = 14
BASE_BYTES = 150_000_000
BUDGET_SHARE = 0.6  # use at most 60 % of the installed memory
FALLBACK_TOTAL = 4 * 1024**3  # when the size cannot be read: assume a small machine


def total_memory() -> int:
    """Installed memory in bytes. Falls back to a cautious guess."""
    try:
        if sys.platform == "win32":
            import ctypes

            class _Status(ctypes.Structure):
                _fields_ = [
                    ("length", ctypes.c_ulong), ("load", ctypes.c_ulong),
                    ("total", ctypes.c_ulonglong), ("avail", ctypes.c_ulonglong),
                    ("tpage", ctypes.c_ulonglong), ("apage", ctypes.c_ulonglong),
                    ("tvirt", ctypes.c_ulonglong), ("avirt", ctypes.c_ulonglong),
                    ("ext", ctypes.c_ulonglong),
                ]

            st = _Status()
            st.length = ctypes.sizeof(_Status)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st)):  # type: ignore[attr-defined]
                return int(st.total)
        else:
            return int(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES"))
    except (OSError, ValueError, AttributeError):
        pass
    return FALLBACK_TOTAL


def memory_budget() -> int:
    return int(total_memory() * BUDGET_SHARE)


def estimate_job_memory(file_bytes: int, factor: float = 1.0) -> int:
    """Peak memory a repair of this file may need, in bytes."""
    return int((BASE_BYTES + BYTES_PER_FILE_BYTE * max(file_bytes, 0)) * factor)
