# Source snapshot validation — 2026-10-23

## Release verdict

| Check | Verdict | Scope |
| --- | --- | --- |
| Source completeness | PARTIAL | Runnable root candidate + original protected baseline + final Windows service and launcher sources retained; standalone complete desktop binary build and v1.0.0 correspondence not established |
| Secret/private-data audit | PASS | Reviewed upload set; no unresolved credential or private research-data finding; not a guarantee against all possible secrets |
| Dependency reproducibility | PARTIAL | Clean Windows x64/Python 3.14 install and pip check pass; npm ci passes; other platforms, optional research dependencies and complete desktop build unverified |
| Runtime/test aggregate | FAIL | Root suite and real-process HTTP/browser smoke pass; additional historical-suite reuse against final Windows branch fails lifecycle contract |
| GitHub source publication | READY | Owner explicitly approved AGPLv3 for the PyMuPDF/MuPDF combination; original MIT and existing root LICENSE retained. Source-only scope; earlier binary compliance remains separately unverified |

## Executed checks

- Original project recursively inventoried without copying environments or runtime data. Release inputs were byte-copied and SHA-256 checked; no detector algorithm, threshold or business logic was changed.
- Existing Windows x64/Python 3.14.0 environment: runtime/dev imports and `pip check` passed.
- Fresh separate Python virtual environment: installed original production lock plus dev requirements, then `pip check` passed. No packages were installed into the original development environment. Python versions/platforms beyond this one were not tested.
- Root suite in a copied source checkout and fresh environment: **162 passed, 1 skipped, 1 deprecation warning, 36.71 seconds**. Earlier separate existing-environment run produced the same test counts. The skip is the intentionally excluded optional publication PDF; no genuine research input was used in the isolated runs.
- `npm ci --ignore-scripts --no-audit --no-fund` succeeded in an isolated checkout. Node 24.19.0 / npm 11.17.0, Playwright 1.64.1; package/lock metadata agree.
- Unchanged default launcher starts frozen engine `3.0.0-annotations`; root `--candidate` starts engine `3.2.0-blot-low-texture`; separately preserved Windows source starts application `0.3.6-feature-cache-stable-trial`. Each served its index, CSS, JavaScript and 11 synthetic demos using new local state.
- Existing `npm run test:ui` browser smoke passed against the isolated root candidate with Google Chrome. It exercised synthetic upload, result/evidence review, saved review note, source links and responsive-layout assertions.
- Final Windows direct-source process completed a synthetic identical-image pair job and exported non-empty HTTP-200 JSON, CSV and HTML reports. This was cache-off development mode, not the complete portable controller.
- All 57 Python files parsed successfully; Node syntax checks passed for all 5 JavaScript/CJS files; all 3 PowerShell scripts parsed without errors and were not executed. Both original C# launchers compiled successfully in a temporary directory without icon/PNG resources; the temporary EXEs were not executed or shipped. This is compile-only evidence, not a reconstructed desktop release.

## Explicit final Windows cross-version suite failure

Reusing the root 13-module suite unchanged against a separate byte-identical copy of final Windows app produced **150 passed, 12 failed, 1 skipped, 2 teardown errors, 1 warning** (37.10 seconds). All 12 failures and both errors were `RuntimeError: cannot schedule new futures after shutdown`.

The final branch closes its global thread pool when its lifespan exits. These historical tests repeatedly construct/close TestClient lifespans within one interpreter, without recreating that pool. The separate real-process service smoke above passed. No detector assertion failure was observed in this cross-version run, but this does not turn the full suite into PASS or establish scientific accuracy. No production code/test was changed to conceal the failure. A version-appropriate isolated-process/lifecycle test harness is future reviewed work, not part of this unchanged source preparation.

## Safety and provenance

All original user databases/uploads/thumbnails, research PDFs/images, downloaded paper pages, caches, historical reports, EXE/DLL/PYD, runtime environments and unknown-rights icon binaries were excluded. The included demo PNGs have no EXIF or auxiliary text/GPS metadata and are reproducible from the original generator. Node is test-only; the shipped UI has no external CDN/font/model dependency.

Public Git history checked read-only: 12 commits, 18 unique blobs including 13 text blobs, no credential-pattern candidate in those texts. Five pre-existing public binary images were not OCR-scanned and are neither reuploaded nor replaced. No history rewrite or remote mutation was performed. Pattern scanning cannot prove absence of every secret or authorship of every line.

## Not verified / not claimed

No full desktop EXE rebuild, exact equivalence to released v1.0.0, C# interaction, full migration/backup restore, cached-mode stress/NTFS boundary behavior, other operating system, other Python version, independent scientific sensitivity/specificity, comprehensive dependency vulnerability database audit, or commercial license authorization was established. The owner selected the AGPL combination route; a commercial license is not claimed or needed as the chosen route for this source publication. Historical scripts retain their original relative paths and may require omitted older ZIPs/resources. The optional browser_v2 script was syntax-checked, not executed. The retained optional research requirements were not installed.

The original online README and MIT LICENSE are deliberately absent from this additive source payload so they cannot be overwritten. They remain in the existing repository. See `THIRD_PARTY_NOTICES.md` and `windows/README.md` for separate dependency/build boundaries.
