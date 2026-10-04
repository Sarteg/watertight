"""List places the user can open in the explorer: home folders, local drives, and NETWORK drives
that the operating system has already mounted or mapped.

- macOS:   SMB/NFS/AFP shares appear in `mount` output, usually under /Volumes.
- Linux:   /proc/self/mounts (cifs, nfs, sshfs, ...) and GNOME gvfs shares under /run/user/<uid>/gvfs.
- Windows: drive letters (including mapped network drives) and their \\\\server\\share target.

watertight does NOT mount shares and never handles passwords. Mount or map the share with your
system tools first (Finder > Connect to Server, Windows "Map network drive", `mount -t cifs`).
This module never touches the mount points themselves (no stat, no listing), so a stale or
offline share cannot freeze the app while the list is built.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

NETWORK_FS = {
    "smbfs", "smb", "smb3", "cifs", "nfs", "nfs3", "nfs4", "afpfs", "webdav", "davfs", "davfs2",
    "fuse.sshfs", "sshfs", "fuse.rclone", "ceph", "glusterfs", "fuse.glusterfs", "afs", "lustre",
}

PLACE, NETWORK, DRIVE = "place", "network", "drive"

MOUNT_HELP = (
    "No network drives found. Mount or map the share first, then press l again:\n"
    "  macOS    Finder > Go > Connect to Server (cmd+K), e.g. smb://nas/stl\n"
    "  Windows  File Explorer > Map network drive   (or type \\\\server\\share with g)\n"
    "  Linux    file manager, or: sudo mount -t cifs //nas/stl /mnt/stl -o username=you"
)


@dataclass(frozen=True)
class Location:
    label: str
    path: Path
    kind: str  # place | network | drive
    detail: str = ""


def _clean_dev(dev: str) -> str:
    """Show host/share only: drop user names and passwords from //user:pw@host/share."""
    return re.sub(r"^//(?:[^@/]*@)?", "//", dev)


# ---- macOS ------------------------------------------------------------------------------------
_MAC_LINE = re.compile(r"^(?P<dev>.+?) on (?P<mp>/.*?) \((?P<opts>[^)]*)\)\s*$")


def parse_mac_mount(text: str) -> list[Location]:
    out: list[Location] = []
    for line in text.splitlines():
        m = _MAC_LINE.match(line.strip())
        if not m:
            continue
        mp, dev = m["mp"], m["dev"]
        fstype = m["opts"].split(",")[0].strip().lower()
        if fstype in NETWORK_FS:
            out.append(Location(Path(mp).name or mp, Path(mp), NETWORK, f"{fstype} {_clean_dev(dev)}"))
        elif mp.startswith("/Volumes/") and fstype not in {"autofs", "devfs"}:
            out.append(Location(Path(mp).name, Path(mp), DRIVE, fstype))
    return out


# ---- Linux ------------------------------------------------------------------------------------
def _unescape(s: str) -> str:
    return re.sub(r"\\([0-7]{3})", lambda m: chr(int(m.group(1), 8)), s)


def parse_linux_mounts(text: str) -> list[Location]:
    out: list[Location] = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 3:
            continue
        dev, mp, fstype = _unescape(parts[0]), _unescape(parts[1]), parts[2].lower()
        name = Path(mp).name or mp
        if fstype in NETWORK_FS:
            out.append(Location(name, Path(mp), NETWORK, f"{fstype} {_clean_dev(dev)}"))
        elif fstype in {"drvfs", "9p"} and mp.startswith("/mnt/"):  # Windows drives inside WSL
            out.append(Location(name, Path(mp), DRIVE, "windows drive"))
        elif dev.startswith("/dev/") and mp.startswith(("/media/", "/run/media/", "/mnt/")):
            out.append(Location(name, Path(mp), DRIVE, fstype))
    return out


_GVFS = re.compile(r"^(?P<proto>[a-z0-9]+)-share:(?P<rest>.*)$")


def gvfs_locations(names: list[str], base: Path) -> list[Location]:
    """GNOME/KDE-style shares: /run/user/<uid>/gvfs/smb-share:server=nas,share=stl,user=me"""
    out: list[Location] = []
    for n in sorted(names):
        m = _GVFS.match(n)
        if not m:
            continue
        kv = dict(p.split("=", 1) for p in m["rest"].split(",") if "=" in p)
        server, share = kv.get("server", "?"), kv.get("share", "?")
        out.append(Location(f"{share} on {server}", base / n, NETWORK, f"{m['proto']} //{server}/{share}"))
    return out


# ---- Windows ----------------------------------------------------------------------------------
DRIVE_REMOVABLE, DRIVE_FIXED, DRIVE_REMOTE, DRIVE_CDROM, DRIVE_RAMDISK = 2, 3, 4, 5, 6


def windows_drives(kernel32, get_unc: Callable[[str], str | None]) -> list[Location]:
    """Drive letters from the Win32 API. `get_unc("Z:")` returns \\\\server\\share for mapped drives.
    Never touches the drive itself, so an offline mapped drive cannot hang this call."""
    out: list[Location] = []
    mask = kernel32.GetLogicalDrives()
    for i in range(26):
        if not mask & (1 << i):
            continue
        letter = chr(ord("A") + i)
        kind = kernel32.GetDriveTypeW(f"{letter}:\\")
        if kind == DRIVE_REMOTE:
            unc = get_unc(f"{letter}:") or ""
            out.append(Location(f"{letter}:", Path(f"{letter}:\\"), NETWORK, f"mapped drive {unc}".strip()))
        elif kind in (DRIVE_FIXED, DRIVE_REMOVABLE, DRIVE_RAMDISK):
            label = "removable" if kind == DRIVE_REMOVABLE else "local disk"
            out.append(Location(f"{letter}:", Path(f"{letter}:\\"), DRIVE, label))
    return out


def _win_api() -> tuple[object, Callable[[str], str | None]]:
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    mpr = ctypes.windll.mpr  # type: ignore[attr-defined]

    def get_unc(local: str) -> str | None:
        size = wintypes.DWORD(1024)
        buf = ctypes.create_unicode_buffer(size.value)
        if mpr.WNetGetConnectionW(local, buf, ctypes.byref(size)) == 0:
            return buf.value
        return None

    return kernel32, get_unc


# ---- public -----------------------------------------------------------------------------------
def _places() -> list[Location]:
    home = Path.home()
    out = [Location("Home", home, PLACE, str(home))]
    for name in ("Desktop", "Documents", "Downloads"):
        p = home / name
        if p.is_dir():
            out.append(Location(name, p, PLACE, str(p)))
    return out


def list_locations(
    platform: str | None = None,
    run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
    read_text: Callable[[str], str] | None = None,
) -> list[Location]:
    """Places, network drives, and local drives for this computer. Never raises."""
    platform = platform or sys.platform
    found: list[Location] = []
    try:
        if platform == "darwin":
            res = run(["mount"], capture_output=True, text=True, timeout=3)
            found = parse_mac_mount(res.stdout)
            if not any(loc.path == Path("/") for loc in found):
                found.append(Location("Computer (/)", Path("/"), DRIVE, "startup disk"))
        elif platform.startswith("linux"):
            reader = read_text or (lambda p: Path(p).read_text(errors="replace"))
            found = parse_linux_mounts(reader("/proc/self/mounts"))
            uid = os.getuid() if hasattr(os, "getuid") else 0
            gvfs = Path(f"/run/user/{uid}/gvfs")
            try:
                found += gvfs_locations(os.listdir(gvfs), gvfs)
            except OSError:
                pass
            found.append(Location("Computer (/)", Path("/"), DRIVE, "root"))
        elif platform == "win32":
            kernel32, get_unc = _win_api()
            found = windows_drives(kernel32, get_unc)
    except Exception:
        pass
    seen: set[Path] = set()
    out: list[Location] = []
    for loc in _places() + sorted(found, key=lambda x: (x.kind != NETWORK, x.label.lower())):
        if loc.path not in seen:
            seen.add(loc.path)
            out.append(loc)
    return out
