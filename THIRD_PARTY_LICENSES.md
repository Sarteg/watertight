# Third-party licenses

watertight is MIT. These are the libraries it uses. Check each project for the full text.
Licenses were read from each package's installed metadata on 2026-10-03.

## Required (installed with `pip install watertight`)

| Package | License |
|---|---|
| trimesh | MIT |
| numpy | BSD-3-Clause (plus bundled permissive notices) |
| scipy | BSD-3-Clause |
| networkx | BSD-3-Clause |
| manifold3d | Apache-2.0 |
| textual | MIT |
| rich (via textual) | MIT |

## Optional (installed only with `pip install "watertight[meshfix]"`)

| Package | License | Note |
|---|---|---|
| pymeshfix | GPL-3.0 (bundled LICENSE file); PyPI classifier says AGPL-3.0 | Not part of the default install. Never imported at module top level. If you redistribute a bundle that includes it, read its license first. This is not legal advice. |

## Development only

pytest, pytest-asyncio, ruff, psutil, pip-licenses, pyte (used in manual terminal checks).
