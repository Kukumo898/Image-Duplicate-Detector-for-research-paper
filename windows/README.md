# Windows desktop and portable source archive

This directory preserves original Python, frontend, launcher, lifecycle, backup/migration and C# source located inside the local Windows delivery workspace. These are actual source files, not decompiled executables. Files were copied without changing detection algorithms, thresholds or business logic.

## Three source families, not three claims about v1.0.0

| Location in this repository | Source-reported application / engine | Purpose |
| --- | --- | --- |
| Root `app/` | application 0.3.1; engine 3.2.0-blot-low-texture | Current development/candidate implementation |
| `benchmarks/frozen_v3_before_closeout/app/` | application 0.3.0; engine 3.0.0-annotations | Original protected default selected by root `run.py` when the acceptance gate is absent/not passed |
| `windows/portable/release_source/app/` | application 0.3.6-feature-cache-stable-trial; cache 1.1.3; engine 3.2.0-blot-low-texture | Additional Windows service/UI, optional feature-cache and lifecycle implementation |

These internal versions are not the GitHub release tag `v1.0.0`. This audit has not established a byte-for-byte correspondence between that published tag/EXE and every local source family. Do not replace the existing release, README, LICENSE or screenshots on that assumption. The root detector, annotations, blot candidates, semantics, ingestion and panels modules were confirmed byte-identical to the corresponding final Windows source modules; wrappers and UI differ.

## Layout

- `portable/release_source/app/`: complete packaged Python and HTML/CSS/JS source, including feature_cache, cache_root, daily_lifecycle and report_assets.
- `portable/release_source/requirements-lock.txt`: original exact production dependency lock.
- `portable/release_source/demo/`: program-generated synthetic examples, not research or experimental images.
- `portable/tools/`: original local service controller, launcher ownership checks, backup and migration code.
- `portable/使用说明.md`: original resource read by `/api/help`. It describes the assembled portable binary, not the source-only development command below. Its historical names/defaults are retained unchanged.
- `build_archive/`: original C# launcher and historical assembly/renaming/maintenance scripts. These are archival sources with historical relative paths, not a new supported one-command build system.
- `runtime_build/`: original official-CPython/PyPI runtime construction code and original build notes.

## Run the Windows Python service from source

The normal root developer setup is documented in `../DEVELOPMENT.md`. No Node build is needed for this service. The existing lock requires Python 3.12 or newer through NumPy's metadata; the locally available verification environment uses Windows x64/Python 3.14. Other interpreters, operating systems and clean machines are not thereby verified.

From the **repository root**, first prepare its virtual environment and install its production lock:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-lock.txt
```

Then use a new isolated data location and explicitly disable the optional persistent cache. These commands do not use the root project's original data directory or an existing portable package:

```powershell
$sourcePython = (Resolve-Path '.\.venv\Scripts\python.exe').Path
$env:LABIMAGE_DATA = Join-Path (Get-Location) 'test-results\windows-source-data'
$env:LABIMAGE_FEATURE_CACHE = '0'
Push-Location '.\windows\portable\release_source'
& $sourcePython -m uvicorn app.main:app --host 127.0.0.1 --port 18766 --workers 1
# After stopping the development server with Ctrl+C:
Pop-Location
```

Open `http://127.0.0.1:18766`. On startup the service creates a new local SQLite database and data directories under the configured location. Do not start multiple workers. It does not require a pre-existing task database, experimental input, model download, OCR, Word installation, remote API key or frontend CDN.

This direct HTTP development launch does **not** start the full portable lifecycle controller. Portable acceleration-mode switching, authenticated shutdown, backup and migration UI operations require their assembled controller/launcher context; their absence is not authorization to remove identity or manifest checks. Use Ctrl+C to stop the direct developer launch. The ordinary upload, local comparison and static UI path can be reviewed independently.

The checked-in synthetic demo files are sufficient. To regenerate them, run from the repository root (do not run inside the original development project):

```powershell
.\.venv\Scripts\python.exe scripts\create_demo.py --output windows\portable\release_source\demo
```

Font availability can affect exact demonstration pixels: the original generator tries Arial and falls back to Pillow's default font. These examples are functionality demonstrations, not independent evidence of scientific sensitivity or specificity.

## Environment configuration

| Variable | Source behavior / developer use |
| --- | --- |
| `LABIMAGE_DATA` | Writable local state root; set it to an isolated directory before import/startup. Default is this source family's `release_source/data`. |
| `LABIMAGE_FEATURE_CACHE` | Default `0`/off for direct source launch; `1`, `true`, `yes` or `on` selects the optional cached implementation at process startup. Changing mode requires a new process. No performance qualification is implied. |
| `LABIMAGE_FEATURE_CACHE_DIR` | Cache root only when explicitly enabled. Use a new owned local NTFS location on Windows; do not point at a private or shared existing cache. |
| `LABIMAGE_FEATURE_CACHE_LIMIT_BYTES`, `LABIMAGE_FEATURE_CACHE_TTL_SECONDS`, `LABIMAGE_FEATURE_CACHE_SNAPSHOT_LIMIT_BYTES` | Existing optional resource controls. Leave unchanged for ordinary reproduction; this release preparation does not tune them. |
| `LABIMAGE_RUNTIME` | Full portable service/controller runtime context; not required for the direct cache-off source command. |
| `LABIMAGE_SESSION_ID`, `LABIMAGE_CONTROL_TOKEN`, `LABIMAGE_SOURCE_MANIFEST_SHA256` | Internal per-session values generated/validated by portable tools. They are not developer credentials to put in an `.env` file or repository. |

## Tests and verification scope

The root `tests/test_*.py` suite targets root app imports and uses synthetic temporary fixtures; it is not automatically a complete Windows-controller test suite. Run it exactly as documented in `../DEVELOPMENT.md`. The original optional scientific PDF fixture is deliberately excluded and the corresponding existing test skips when it is missing. Historical browser/report smoke scripts dependent on omitted research reports are not evidence of a fresh source verification.

The source-release audit report records actual executed tests and HTTP checks. Syntax/import checks or a running HTTP service do not prove every C# UI, backup/restore operation, cache failure path, operating system or clean-machine deployment works. No such unexecuted operation is marked passed here.

## Desktop build status: PARTIAL

The original outer launcher uses `DeliveryEntry.cs`; startup versus migration behavior is selected by the output executable's filename. The inner launcher uses `TujianLauncher.cs`. They require Windows .NET Framework's `csc.exe`, Windows Forms, System.Drawing and System.Web.Extensions, plus PNG/ICO resource inputs. Original bitmap/icon artwork is conservatively excluded because its redistribution rights were not independently established during this source preparation. Use only resource assets whose rights the release owner has confirmed; omission is documented, not hidden by modifying launcher code.

The historical `build_layout.py` reads an earlier liquid-glass release ZIP, earlier source/review folders and manifests. Its original path assumptions are preserved for traceability and are not satisfied by this source-only layout. `rename_product.py` similarly operates on a prior clean assembled distribution. Do not execute either against a used user package. No new source-to-EXE packaging pipeline was invented during this task, and a complete independently reproducible Windows binary build is **not** claimed.

The runtime builder acquires official CPython 3.14.0 AMD64 embeddable Python and exact PyPI wheels and requires an official Visual Studio VC143 x64 REDIST directory supplied through `--vc-redist`. Windows/MSVC redistribution conditions remain applicable. Downloaded runtimes, wheels, DLLs, PYDs, EXEs and node_modules are intentionally absent from this Git source upload. A complete portable assembly must generate fresh matching source/runtime/file manifests; never bypass `verify_manifest()` or reuse stale manifests to make a partial package run.

## Licensing and private-data boundaries

The existing repository MIT LICENSE is retained online and is not replaced by this archive. It does not convert dependencies or artwork to MIT. PyMuPDF is required by PDF ingestion and is offered under AGPL v3 or Artifex commercial terms. On 2026-10-23 the owner selected AGPLv3 for the PyMuPDF/MuPDF combined application, while preserving MIT for the original project components and the unchanged root LICENSE. No Artifex commercial grant is claimed. This source-publication decision does not establish the corresponding-source or redistribution compliance of previously distributed Windows binaries. See the root third-party notices and `../VALIDATION_SOURCE.md` before publication.

No runtime user_data, SQLite database, private scientific input, generated detection evidence, session records, control tokens, logs, locks, credentials, embedded interpreter or binary executable is part of this source archive. Paths in original generic help/build notes describe expected layout or system component locations, not bundled personal data.
