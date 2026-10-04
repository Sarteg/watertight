# Contributing

Thanks for helping. Keep it small and tested.

## Set up

```bash
uv venv && uv pip install -e ".[dev,meshfix]"
.venv/bin/pytest                 # about a minute; it starts real worker processes
.venv/bin/ruff check src tests
```

Tests that need `pymeshfix` skip themselves when it is not installed. CI runs both ways.

## Layout

- `src/watertight/core/` mesh check, pipeline, validation, I/O. **No UI code here.**
- `src/watertight/tiers/` one file per repair tier. A tier takes a mesh, returns a mesh and a list
  of actions. Add a tier by following `tiers/base.py` and registering it in `tiers/__init__.py`.
- `src/watertight/queue/` job states, parallel worker processes, cancel and interrupt.
- `src/watertight/tui/` the Textual app. It only reads the queue manager and calls it.
- `src/watertight/headless.py`, `cli.py` the text mode and the entry point.

## Rules

1. **Original files are never written.** A test hashes inputs before and after.
2. **Output appears only by one atomic rename.** Cancel must leave no output and no temp file.
3. **Core never prints.** Tiers return data. The CLI and the TUI format it.
4. **Optional GPL code stays optional.** Never import `pymeshfix` at module top level. A CI step
   fails if a required dependency has a GPL or AGPL license.
5. **Test fixtures are generated in code.** Do not commit third-party meshes.
6. Explain a non-obvious design choice in a short code comment or in the pull request.

## Pull requests

Describe the defect or feature, add a test, run `pytest` and `ruff`.
