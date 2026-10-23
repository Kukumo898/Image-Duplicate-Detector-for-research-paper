# Development from source

This is a reviewed source snapshot for the existing repository, not a replacement README or a new Windows binary release. Keep the existing root README, MIT LICENSE, screenshots, installation documents and Releases. Read `THIRD_PARTY_NOTICES.md` and `VALIDATION_SOURCE.md` before distributing a combined application. The release owner approved the AGPL combination route on 2026-10-23: original project components remain MIT, while the application combined with community PyMuPDF/MuPDF is distributed under AGPLv3. This source-only publication does not certify the compliance or reproducibility of earlier binary releases.

## Source layout and version selection

| Path | Actual role |
| --- | --- |
| `app/` | Current candidate: Python/FastAPI service, local SQLite persistence, OpenCV/NumPy detector, ingestion, annotations, panels and semantics |
| `app/static/` | Original HTML, CSS and browser JavaScript; served directly by FastAPI, no frontend build |
| `run.py` | Single-process Uvicorn launcher bound to `127.0.0.1`; protected baseline by default |
| `benchmarks/frozen_v3_before_closeout/app/` | Original frozen v3 code used by the unchanged default startup gate; no research dataset or database is included |
| `demo/` | Eleven program-generated synthetic PNGs, manifest, labels and explanatory README |
| `tests/` | Thirteen existing Python test modules and two optional original browser smoke scripts |
| `scripts/` | Synthetic demo generator, labelled-pair evaluation, panel inspection and local-server stop helper |
| `windows/` | Separately preserved final local Windows service, cache/lifecycle/backup/migration and original C# launcher sources; see its README |
| `SOURCE_PROVENANCE.json` | Original project-relative source paths and SHA-256 hashes for every unchanged copied input |

These source families are not interchangeable. Root candidate engine is `3.2.0-blot-low-texture`, baseline engine `3.0.0-annotations`, and final local Windows application `0.3.6-feature-cache-stable-trial`. Their relationship to the published GitHub `v1.0.0` EXE has not been independently established. No algorithm, threshold or business logic was changed for this snapshot.

## Python setup

Use a new clone or extracted source directory, not the original development directory or a used portable application. Windows x64 with CPython **3.14.0** was actually tested. NumPy in the existing lock requires Python >=3.12; other Python versions and operating systems were not tested.

From the repository root in PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-lock.txt
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt -c requirements-lock.txt
.\.venv\Scripts\python.exe -m pip check
```

`requirements.txt` lists bounded production dependencies. `requirements-lock.txt` pins 20 production distributions, but does not include pytest/httpx and has no artifact hashes. `requirements-dev.txt` extends the bounded production requirements with pytest/httpx; the constraint above preserves the production lock. `requirements-research.txt` is the original optional manifest for omitted acquisition/research work, extending dev with Beautiful Soup; it is not necessary for the included application or tests and was not installed/validated here. No separate `python-docx`, Word, OCR, model weights, API key or remote inference service is required by the inspected core implementation. PDF handling requires PyMuPDF and its separate licensing conditions.

The synthetic demo is already included. If needed, regenerate it **in this new source checkout**:

```powershell
.\.venv\Scripts\python.exe scripts\create_demo.py
```

Arial availability can change generated text pixels because the generator falls back to Pillow's default font. The synthetic images demonstrate functions; they are not independent scientific accuracy evidence.

## Start the original protected default

```powershell
.\.venv\Scripts\python.exe run.py --no-browser
```

Open `http://127.0.0.1:8765`. Omit `--no-browser` to open a browser automatically. The unchanged launcher retains the frozen baseline unless its existing local acceptance gate has passed; **no passed gate is supplied or fabricated**. On first default launch it creates its own ignored `output/v3_closeout/production_runtime`, copies the frozen app plus demo, verifies the frozen source hashes, and initializes local state under `data/` (or `LABIMAGE_DATA`). Do not remove the small included frozen `app/`: doing so would silently select a different engine.

The three original Chinese BAT files are retained unchanged. `安装依赖.bat` creates `.venv`, installs the production lock and regenerates demos, but does not install dev or Node dependencies. `启动系统.bat` uses the protected default. Stop the terminal server using Ctrl+C. `停止系统.bat` calls the audited local stop helper; use it only against a server launched from this checkout, not an unrelated or used user package.

## Run the current root candidate in isolation

```powershell
$env:LABIMAGE_DATA = Join-Path (Get-Location) 'test-results\candidate-data'
.\.venv\Scripts\python.exe run.py --candidate --no-browser --port 18765
```

Open `http://127.0.0.1:18765`. The candidate flag refuses the formal default `data/` directory. Use a new writable directory and stop using Ctrl+C. Never point it at a private development database. `LABIMAGE_DATA` is read at process import/startup; changing it after import does not reconfigure a running service. The code does not load `.env` files. Run a single worker and keep the loopback binding.

On a clean start the service creates its SQLite schema plus uploads, images, thumbnails and annotations directories. An existing database, detection history or research input is not required. User-added images and review records stay in the local state folder and must not be committed.

## Python tests

```powershell
.\.venv\Scripts\python.exe -B -m pytest -q -ra -p no:cacheprovider
```

The root suite tests the current root `app/`, not the default-launch baseline. Its fixtures create synthetic images, in-memory PDF/DOCX documents and temporary SQLite data. A single optional publication-PDF panel regression skips when the deliberately excluded `benchmarks/figurecheck` fixture is absent. Do not copy a private paper or experimental image into Git just to remove that skip.

The actual clean-environment result was **162 passed, 1 skipped**. There is a FastAPI/Starlette httpx deprecation warning; no dependency or algorithm was changed to suppress it. The separate final Windows branch passed real-process HTTP/source checks but failed a cross-version reuse of this entire suite because its shutdown lifecycle differs. See `VALIDATION_SOURCE.md`; do not describe every Windows test as passed.

## Node.js and optional browser tests

Node is development-only. There is no npm production server, build pipeline or downloaded production frontend dependency. Use Node **>=20** (tested Node 24.19.0, npm 11.17.0); the lock resolves Playwright 1.64.1. Install Google Chrome: the existing smoke explicitly uses Playwright's `channel: 'chrome'`, not bundled Chromium.

```powershell
npm ci --ignore-scripts --no-audit --no-fund
```

Keep the isolated candidate above running. In a second terminal at the same checkout root:

```powershell
$env:LABIMAGE_URL = 'http://127.0.0.1:18765'
npm run test:ui
```

The test submits only bundled synthetic examples, saves local review data and screenshots under ignored `test-results/`, and needs a live test service. It must not target a production instance. This existing smoke passed against the current candidate. `browser_v2_smoke.cjs` is retained as another optional original Windows test; it expects repo-local `.venv/Scripts/python.exe` for synthetic document generation and was syntax-checked but not executed here. Neither old smoke is the final Windows UI contract, whose readiness label has changed.

## Final Windows source and binary build limitations

See `windows/README.md` for a separate, isolated direct-source Uvicorn command. Full portable controller, authenticated shutdown, cache switching, backups and migrations require correctly assembled runtime/session/manifests. Do not remove checks to make this source-only tree look like an assembled package.

Original C# and historical build/runtime scripts are included, but the previous ZIP input, unconfirmed-rights PNG/ICO artwork, runtime binaries and complete standalone source-to-EXE build recipe are not. Desktop binary reproducibility remains **PARTIAL / not validated**. Both original C# launchers compiled in a temporary compile-only check without artwork/resources; those test binaries were not executed or included. No released EXE was decompiled, replaced or uploaded.

## Common problems

- Missing imports: use `.venv` from this checkout and install the lock plus constrained dev requirements. `npm ci` cannot install Python modules.
- Different engine than expected: default launcher uses frozen v3; pytest imports the root candidate; final Windows source is separate. Check `/api/status` on the intended isolated port.
- Port already occupied: choose a fresh `--port`; the launcher may reuse an existing recognized local app at that port.
- Browser smoke times out: check its `LABIMAGE_URL`, Chrome availability and root-candidate UI version. Do not alter detector thresholds to satisfy a browser assertion.
- Private files appear in a checkout: stop before committing and inspect the actual file set. `.gitignore` is prevention, not a substitute for auditing.
- Full Windows tests report `cannot schedule new futures after shutdown`: the historical root suite repeatedly starts/stops one in-process app while the final Windows branch shuts down its global executor on exit. This is an unresolved test/lifecycle contract difference, not a passing Windows suite.
