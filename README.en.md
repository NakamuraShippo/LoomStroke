<h1>
  <img src="img/favicon.png" alt="" width="64" height="64" align="middle">&nbsp;&nbsp;
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="img/textlogo.png">
    <img src="img/textlogo_br.png" alt="LoomStroke" width="400" align="middle">
  </picture>
</h1>

[日本語](README.md) | [English](README.en.md) | [简体中文](README.zh-CN.md) | [繁體中文（台灣）](README.zh-TW.md)

### [patreon](https://www.patreon.com/c/LoomStroke)

LoomStroke is a desktop painting application built with PySide6 and wgpu.
It supports raster and vector editing, multipage documents, text, comic panels and speech balloons,
and integration with ComfyUI, LLMs, and image generation services.

## Download the Windows version as a ZIP

### [Download the latest LoomStroke (ZIP)](https://github.com/NakamuraShippo/LoomStroke/archive/refs/heads/main.zip)

**You do not need to install Python beforehand.** You need 64-bit Windows and an internet connection
for the initial setup. If Python is not found, the installer automatically downloads a private copy for the app.

1. Download the ZIP using the link above.
2. Right-click the ZIP, select “Extract All,” and open the extracted `LoomStroke-main` folder.
3. Double-click `install.bat` and wait until “Installation completed successfully.” appears.
4. Double-click `boot_LoomStroke.bat` to launch the app.

For subsequent launches, just use `boot_LoomStroke.bat`. You do not need Git or any command-line input.
To launch without AI features, double-click `boot_LoomStroke_noAI.bat`.
Always extract the ZIP before running anything; do not run files from inside the ZIP.
Keep using the extracted folder after setup.

This link downloads the latest distribution from `main`. The ZIP includes the app, documentation, and licenses.
Python and the virtual environment are prepared during installation; model weights and user data are not included.
See the [installation and launch guide](docs/user/INSTALL_AND_RUN.md) for details.

To update an installed ZIP version, save your work, close LoomStroke, and double-click `update.bat`.
The previous app files are kept in `.update-backups/`, while your user data and runtime environment are preserved.
If you are adding this update feature for the first time, copy both `update.bat` and `update.ps1`
from the latest ZIP into your current app folder.
The public Git version can also be updated with the same `update.bat`. It requires Git for Windows and the `main` branch.
The updater stops if it finds local changes or your own commits, without discarding them.
See also [updating the Git version](docs/user/INSTALL_AND_RUN.md#git版のアップデートwindows). Development folders are outside the updater's scope.

On October 20, 2026, we verified extracting the distribution ZIP, automatic setup and launch with the two BAT files,
drawing in a new document, Undo/Redo, saving, and normal shutdown on 64-bit Windows 11 without Python preinstalled.
This verification used Windows Sandbox. Windows Sandbox is not required for normal use.

This repository provides source code under the LoomStroke License v1.0.
The authoritative terms are in [LICENSE](LICENSE); a Japanese translation is available in [LICENSE_JA](LICENSE_JA).
Use, including commercial use, is permitted. Redistribution, sharing, modification for distribution,
sale, transfer, and similar activities require prior written permission from the copyright holder.
The app's license does not restrict how you use the works you create with it.
Third-party code and resources are governed by their respective licenses.

## Requirements

- Python 3.11 or later (`install.bat` prepares it automatically on Windows when needed)
- A wgpu-compatible GPU and driver (D3D12 on Windows, Vulkan on Linux, Metal on macOS)
- An OS credential store (required to save and retrieve API keys for paid gateways)

Actual operation on each OS depends on your environment. Verification on physical macOS hardware is not included in this snapshot's validation.

## Installation and launch

On 64-bit Windows, you can double-click `install.bat` without installing Python first.
When needed, it downloads official CPython 3.13.16, verifies it against a fixed SHA-256 hash, and prepares it in `.runtime/`.
Administrator privileges and changes to the system Python or PATH are not required.
It reuses an existing `.venv` / `venv`, or creates `.venv` if neither exists, then installs the app and its required dependencies.
After setup, launch with `boot_LoomStroke.bat`. On failure, the installer leaves the error visible and exits
without deleting or replacing an existing virtual environment.

Use `install.bat --nanobanana` to enable NanoBanana's additional dependencies.
For automated execution, add `--no-pause`. To choose the Python used to create a new virtual environment,
add `--python "C:\path\python.exe"`.
Use `--no-python-download` to disable automatic Python downloads.
Keep `.runtime/` and `.venv/` in the same location after setup, as they are used to launch the app.

For manual setup, run the following from the repository root:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e .
python -m loomstroke
```

On macOS / Linux, activate the virtual environment with `source .venv/bin/activate`.

The required rendering dependency `wgpu==0.31.0` is declared in the package dependencies and installed automatically with `rendercanvas`.
It is also listed in `requirements.txt`, so a separate installation is unnecessary.
That file pins runtime dependency versions and includes the additional SDK for NanoBanana.
It does not include testing, formatting, type-checking, or build tools.

To install NanoBanana's additional dependencies, run this in the same virtual environment:

```powershell
python -m pip install -e ".[nanobanana]"
```

`keyring` is a required dependency. API keys use the OS credential store through `SecretProvider`.
If that store is unavailable, saving and retrieving API keys for paid gateways is refused.
Connections to external services require permission through the app setting `privacy/allow_external_services`.
The SAM3.1 runtime and model weights for AI Selection are not bundled; prepare them separately when using the corresponding feature.

## Documentation

- [User guide entry point](docs/user/index.html)
- [Japanese manual](docs/user/index_ja.html)
- [Installation and launch](docs/user/INSTALL_AND_RUN.md)
- [Launch instructions and troubleshooting](docs/user/RUN_INSTRUCTIONS.md)

The documentation reflects the development version as of October 22, 2026, including CMYK painting, layer styles,
RGB/CMYK color panels, and vector stroke width editing.
It also covers text along paths, automatic text layer naming, launch without AI, ZIP updates, ComfyUI parameter display
and shutdown handling, Undo/Redo immediately after comic panel changes, and recovery of off-screen floating panels.

From the main menu's “Filter” menu, you can select the same 11 filters available as layer filters and apply them directly to the target layer.
For vector and text layers, a rasterization confirmation appears. Clicking OK in the filter settings applies rasterization and the filter together.
Canceling either the confirmation or the settings preserves the original layer. After applying, a single Undo restores it.

## Source layout

- `src/loomstroke/`: App, built-in plugins, rendering, and runtime resources
- `img/`: App logos and icons
- `docs/user/`: User guides
- `LICENSES/` and `THIRD_PARTY_LICENSES`: Third-party license notices

This distribution excludes the development repository's history, private test assets, test suite, internal development documents,
user data, API keys, model weights, virtual environments, and prebuilt distribution packages.
The publication scope is documented in [PUBLICATION.md](PUBLICATION.md).

## Third-party components

PySide6 / Qt is used under the terms of LGPL-3.0.
LoomStroke's own license does not restrict rights granted by third-party licenses,
such as replacing dynamically linked libraries.

The Mapbox earcut port is licensed under the ISC License. Bundled ICC profiles are subject to their respective permissions.
See [THIRD_PARTY_LICENSES](THIRD_PARTY_LICENSES), [LICENSES](LICENSES/),
and the notices in `src/loomstroke/io/resources/icc/` for details.

Activities on the hosting service, such as viewing and forking, are also subject to that service's terms.
Contact information for permission requests is provided in LICENSE.
