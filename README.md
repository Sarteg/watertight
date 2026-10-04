<p align="center">
  <img src="https://raw.githubusercontent.com/Sarteg/watertight/main/assets/logo.svg" alt="watertight" width="520">
</p>

<p align="center">
  <a href="https://github.com/Sarteg/watertight/actions/workflows/ci.yml"><img src="https://github.com/Sarteg/watertight/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue.svg" alt="MIT license"></a>
  <img src="https://img.shields.io/badge/python-3.10%2B-blue.svg" alt="Python 3.10+">
</p>

# watertight

Free, open-source terminal app that **repairs STL files for 3D printing**.

Pick files in a built-in file explorer, queue as many as you like, and watch them repair in
parallel. Each fixed file is saved **next to the original** as `<name>_repaired.stl`.
Files that are already watertight are **skipped**. Your originals are **never changed**.

```
 watertight 0.1.0 │ workers 4/7 │ selected 0 │ overwrite off │ trimesh✔ manifold3d✔ pymeshfix✔
╭──────────────────────────────────────╮╭──────────────────────────────────────────────────────────╮
│ EXPLORER                             ││ QUEUE                                                    │
│📂 prints                             ││ #    File              Status                  Progress  │
│├── [ ] 📄 bracket.stl  634 B         ││ 1    bracket.stl       ✔ Done                  ██████████│
│├── [ ] 📄 broken.stl  48 B           ││ 2    broken.stl        ✘ Failed                ██████████│
│├── [ ] 📄 edge.stl  1.3 KB           ││ 3    edge.stl          ! Needs review          ██████████│
│├── [ ] 📄 gear.stl  684 B            ││ 4    gear.stl          ✔ Done                  ██████████│
│├── [ ] 📄 hinge.stl  734 B           ││ 5    hinge.stl         ✔ Done                  ██████████│
│└── [ ] 📄 vase.stl  684 B            ││ 6    vase.stl          ✔ Skipped (watertight)  ██████████│
╰──────────────────────────────────────╯╰──────────────────────────────────────────────────────────╯
 Overall  ━━━━━━━━━━━━━ 6/6 finished · 3 repaired · 1 skipped · 1 review · 1 failed · elapsed 00:02
```

## Install

Needs Python 3.10 or newer.

```bash
pipx install watertight          # or: uv tool install watertight
```

From a clone of this repo:

```bash
pipx install .                   # or: uv tool install .   or: pip install .
```

### Stronger repair (optional)

```bash
pipx install "watertight[meshfix]"
```

This adds **pymeshfix**, which fixes harder problems (for example two holes that touch at one
point). It is installed **only if you ask for it**, because pymeshfix is **GPL-3.0** (its PyPI
metadata also lists AGPL-3.0), while watertight itself is MIT. The app detects it at run time
and uses it as the last repair step. The header line shows `pymeshfix✔` or `pymeshfix✘`.

## Use

```bash
watertight                       # open the TUI in the current folder
watertight ~/prints              # open the TUI in ~/prints
watertight a.stl b.stl           # open the TUI and queue these files right away
watertight --headless a.stl b.stl --jobs 2     # no UI, text report, exit codes
```

### Keys

| Key | Does |
|---|---|
| arrows, `space` | move, select / unselect a file or a whole folder |
| `a` / `A` | select all STL files here / in all sub-folders |
| `Enter` | add the selection to the queue **and start** |
| `Ctrl+C` (or `C`) | **stop everything**: running files stop and save nothing, queued files are cancelled. Finished files keep their output |
| `c` | cancel the highlighted file (running or queued) |
| `X` | cancel all queued files; running files finish |
| `p` / `s` | pause / resume |
| `+` / `-` | more / fewer workers (default 4, max = CPU cores − 1) |
| `o` | overwrite existing `_repaired` files on / off |
| `f` | **keep trying** on / off (see below) |
| `x`, `r` | clear finished rows, retry the highlighted row |
| `l` | **places and drives**: home folders, local disks, and **network drives** (see below) |
| `g` | type or paste a folder path (`/Volumes/nas/stl`, `Z:\stl`, `\\server\share\stl`) |
| `u`, `h`, `n` | go up one folder (at a drive root it opens the list), show all files, clear selection |
| `Tab`, `?`, `q` | switch pane, help, quit |

### Network drives

Press `l` to see what your computer already has mounted. watertight **does not mount shares and
never asks for passwords**: connect the drive with your system first, then press `l`.

| System | Connect it | Shows up as |
|---|---|---|
| macOS (Samba/SMB, NFS, AFP) | Finder > Go > Connect to Server, `smb://nas/stl` | `/Volumes/stl` |
| Windows | File Explorer > Map network drive (or just type `\\nas\stl` with `g`) | `Z:` with its `\\nas\stl` target |
| Linux | file manager, or `sudo mount -t cifs //nas/stl /mnt/stl -o username=you` | `/mnt/stl`, or a GNOME share under `/run/user/<uid>/gvfs` |

A slow or offline drive cannot freeze the app: folders open with an 8 second limit, and listing
files runs in the background. Repairing over the network reads the whole file and writes the
result next to it, so a slow link means slower jobs (there is **no time limit** by default; add `--timeout SEC` if you want one). Only the macOS and Linux lists were tested on real mount output
formats; the Windows drive list follows the Win32 API and has **not** been run on Windows.

### Headless exit codes

| Code | Meaning |
|---|---|
| 0 | every file is repaired or was already watertight |
| 1 | at least one file was written but needs review |
| 2 | bad input, path problem, cannot write, or output already exists |
| 3 | unexpected internal error |
| 130 | you pressed `Ctrl+C`; unfinished files were not written |

## How it works

A file is **OK** (skipped) when it is watertight, has consistent winding, and has positive volume.
Otherwise the repair runs in steps and **stops at the first step that gives an OK mesh**:

| Step | What it does | Needs |
|---|---|---|
| Tier 0 | drop broken triangles, merge duplicate vertices, remove duplicate and zero-area faces | trimesh |
| Tier 1 | remove tiny floating pieces, fix winding, close holes, flip inside-out shells | trimesh |
| Tier 2 | cut out and refill pinch points, split non-manifold geometry into clean pieces, close them, union overlapping bodies | manifold3d |
| Tier 3 | pymeshfix: complex holes, self-intersections | pymeshfix (optional) |

The result is checked against three limits. If any one is broken, the result is **not saved**
(the next-best result that stays inside the limits is used instead, or nothing is written):

| Check | Limit |
|---|---|
| Volume | changes by more than 2 % |
| Size | changes by more than 0.1 % |
| Surface | more than 1 % of it moved more than 0.5 % of the model width |

The tool never rescales, moves or rotates your part. Select a row to see what was wrong, what
each step did, and each limit with its measured value.

### Keep trying (`--refine`, key `f`)

After a good repair, the tool can try other settings and keep the best result. It stops when no
nearby setting is better (or after `--refine-tries N`, default 8) and tells you so. It takes
longer, so it is off by default. The file is written once, at the end, so `Ctrl+C` still saves
nothing.

### Memory

Big files need memory (about 14 times the file size). The queue starts a job only if the running
jobs plus this one fit in 60 % of your installed memory. At least one job always runs.

### Limits (please read)

- Some meshes cannot be fixed automatically. Those end as **Needs review** with the reason.
- Two bodies that only touch along an edge cannot become manifold without moving geometry.
  They end as **Needs review**. pymeshfix may drop one of the bodies; the size check flags it.
- Big, curved holes are closed with a simple fan. Check the patch before you print.
- STL only (binary and ASCII in, binary out). No 3D preview.

## Benchmark

`python scripts/benchmark.py` generates a **synthetic** defect set (holes, flipped faces,
inside-out, duplicates, degenerate faces, floating specks, internal fins) and runs it.
Result on one 8-core Mac, with pymeshfix installed:

| What | Result |
|---|---|
| Synthetic repair rate | 31 of 32 files ended **Done**; 1 ended **Needs review** (a ring with a large hole) |
| 4 heavy files, 1 worker vs 4 workers | 8.2 s vs 3.5 s (2.35× faster) |
| 78k-face file | check 0.14 s, repair 2.2 s |
| 313k-face file | check 0.6 s, repair 9.2 s |

These are **not** real-world numbers. For those, run the script on your own folder of imperfect
files: `python scripts/benchmark.py --folder ~/stl`.

## Develop

```bash
uv venv && uv pip install -e ".[dev,meshfix]"
.venv/bin/pytest
.venv/bin/ruff check src tests
```

See [CONTRIBUTING.md](CONTRIBUTING.md).
Library use: `from watertight import check_file, repair`.

## Privacy

No network calls. No telemetry. The app only reads STL files you point it at.

## License

MIT. See [LICENSE](LICENSE) and [THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md).
