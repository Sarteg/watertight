# Changelog

## 0.1.0 (unreleased)

First version.

- Pinch points (edges shared by 3+ faces) are cut out and refilled in place. Pinched holes are split into simple loops. This keeps the shape on scanned models that pymeshfix used to shrink.
- The shape check now also compares the surface, so lost detail is caught even when volume and size match.
- Keep trying mode (`--refine`, `--refine-tries N`, key `f` in the TUI): tries other settings after a good repair, keeps the best, and tells you when it cannot get better.
- The queue limits parallel jobs by memory, not only by worker count.
- The details pane now lists the problems found, what each step did, and the shape check against its limits.
- A repair that changes the shape by more than 25 % volume or 5 % size is not saved.

- Terminal UI (Textual): file explorer, multi-select, queue, per-file and overall progress.
- Auto-start, parallel workers (default 4, up to CPU cores − 1), pause, cancel, retry.
- `Ctrl+C` interrupt: stops running files, saves nothing for them, cancels queued files.
- Check first: files that are already watertight are skipped and nothing is written.
- Repair tiers: sanitize, trimesh fixes, manifold3d rebuild, optional pymeshfix.
- Output `<name>_repaired.stl` next to the original; atomic write; originals never touched.
- Headless mode with exit codes (`--headless`), also used automatically when not on a terminal.
