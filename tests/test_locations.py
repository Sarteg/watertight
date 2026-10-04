from pathlib import Path

from watertight.locations import (
    DRIVE,
    NETWORK,
    gvfs_locations,
    list_locations,
    parse_linux_mounts,
    parse_mac_mount,
    windows_drives,
)

MAC = """\
/dev/disk3s1s1 on / (apfs, sealed, local, read-only, journaled)
//me:secret@nas.local/stl on /Volumes/stl (smbfs, nodev, nosuid, mounted by me)
nas:/export/models on /Volumes/models (nfs, nodev, nosuid, mounted by me)
/dev/disk4s1 on /Volumes/USB STICK (msdos, local, nodev, nosuid, noowners)
map auto_home on /System/Volumes/Data/home (autofs, automounted, nobrowse)
"""

LINUX = """\
proc /proc proc rw,nosuid 0 0
/dev/nvme0n1p2 / ext4 rw,relatime 0 0
//nas.local/stl /mnt/nas\\040stl cifs rw,relatime,vers=3.1.1 0 0
nas:/export /mnt/nfs nfs4 rw,relatime 0 0
/dev/sdb1 /media/me/USB vfat rw 0 0
C:\\134 /mnt/c 9p rw 0 0
tmpfs /run tmpfs rw 0 0
"""


def test_mac_network_and_usb_but_not_system_volumes():
    locs = parse_mac_mount(MAC)
    by = {loc.label: loc for loc in locs}
    assert by["stl"].kind == NETWORK and by["stl"].path == Path("/Volumes/stl")
    assert by["models"].kind == NETWORK
    assert by["USB STICK"].kind == DRIVE
    assert len(locs) == 3


def test_credentials_are_never_shown():
    text = " ".join(f"{loc.label} {loc.detail}" for loc in parse_mac_mount(MAC))
    assert "secret" not in text and "me:" not in text and "//nas.local/stl" in text


def test_linux_mounts_with_escaped_space():
    by = {loc.label: loc for loc in parse_linux_mounts(LINUX)}
    assert by["nas stl"].kind == NETWORK and by["nas stl"].path == Path("/mnt/nas stl")
    assert by["nfs"].kind == NETWORK
    assert by["USB"].kind == DRIVE
    assert by["c"].detail == "windows drive"
    assert "run" not in by and "proc" not in by


def test_gvfs_shares_and_user_name_hidden():
    base = Path("/run/user/1000/gvfs")
    locs = gvfs_locations(["smb-share:server=nas,share=stl,user=me", "other"], base)
    assert len(locs) == 1 and locs[0].kind == NETWORK
    assert locs[0].label == "stl on nas" and "me" not in locs[0].detail.split("//")[0]
    assert locs[0].path == base / "smb-share:server=nas,share=stl,user=me"


class FakeKernel32:
    def GetLogicalDrives(self):  # noqa: N802 - Win32 name
        return 0b11101  # A, C, D, E

    def GetDriveTypeW(self, root):  # noqa: N802
        return {"A:\\": 2, "C:\\": 3, "D:\\": 5, "E:\\": 4}[root]


def test_windows_drive_letters_and_mapped_drive():
    locs = windows_drives(FakeKernel32(), lambda local: "\\\\nas\\stl" if local == "E:" else None)
    by = {loc.label: loc for loc in locs}
    assert set(by) == {"A:", "C:", "E:"}  # CD-ROM skipped
    assert by["E:"].kind == NETWORK and "nas" in by["E:"].detail
    assert by["C:"].kind == DRIVE and by["C:"].path == Path("C:\\")


def test_list_locations_never_raises_and_sorts_network_first(tmp_path):
    def boom(*a, **k):
        raise OSError("no mount command")

    assert any(loc.label == "Home" for loc in list_locations("darwin", run=boom))

    locs = list_locations("linux", read_text=lambda p: LINUX)
    kinds = [loc.kind for loc in locs if loc.kind != "place"]
    assert kinds.index(NETWORK) < kinds.index(DRIVE)
