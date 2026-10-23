# Third-party notices and release-licensing status

Audit date: 2026-10-23.

## Scope and project license

The existing GitHub repository uses the MIT License for the project's independently owned code. This source-upload overlay intentionally does **not** include a replacement root LICENSE or README: retain the repository's existing files. This notice does not relicense any third-party component, certify original authorship, grant a commercial license, or extend MIT to dependencies, fonts, artwork or system runtimes.

This delivery contains project source, synthetic demonstration data, tests, documentation and original maintenance/build scripts. It does **not** bundle third-party library source trees, wheels, node_modules, browser binaries, Python/MSVC runtimes, DLL/PYD files or application EXEs. The AGPL license document under LICENSES is a reference license text, not a bundled copy of PyMuPDF's implementation. Dependencies are installed separately from their upstream distributions; their own terms still apply.

## Authorized source-publication route: MIT originals; AGPLv3 combined application

On 2026-10-23 the release owner explicitly selected the AGPL open-source combination route. This clears the previously pending owner licensing decision for this **source-only publication**, not the separate audit of previously distributed binaries.

**Original project components remain available under the existing MIT License. The existing root LICENSE is preserved unchanged. When these components are combined with the community edition of PyMuPDF/MuPDF to form the application, the combined work as a whole is distributed under the GNU Affero General Public License, version 3 (AGPLv3).** The original components' MIT permissions and copyright notices remain intact; they do not relicense PyMuPDF/MuPDF or waive obligations for the combination. Other third-party components retain their own applicable terms. The project does not claim that the installed or bundled combination is MIT-only.

For the combined work, you may redistribute and modify it under GNU AGPL version 3, subject to that license's conditions. It is provided WITHOUT ANY WARRANTY, including the implied warranties of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. The full license is included at [LICENSES/AGPL-3.0-PyMuPDF-COPYING.txt](LICENSES/AGPL-3.0-PyMuPDF-COPYING.txt). Preserve the applicable copyright, license and warranty notices when conveying copies or modified versions. Source and object-code distributions have different corresponding-source duties under sections 4–6; modified network deployments must also satisfy applicable section 13 source-access requirements. Do not treat a link or a license-text copy alone as proof that every duty for a future distribution is fulfilled.

The PDF ingestion path in `app/ingest.py` and the separately preserved baseline/Windows source imports PyMuPDF. The pinned dependency is PyMuPDF 1.28.2, offered under AGPLv3 or an Artifex commercial agreement. This publication selects the AGPL route; **no commercial grant is asserted**. [Versioned upstream source](https://github.com/pymupdf/PyMuPDF/tree/1.28.2), [official licensing](https://pymupdf.io/licensing), [official documentation](https://pymupdf.readthedocs.io/en/latest/about.html).

The project source is provided in this repository, with project-relative input provenance in `SOURCE_PROVENANCE.json` and setup/build limitations in `DEVELOPMENT.md` and `windows/README.md`. PyMuPDF/MuPDF implementations, wheels and native runtimes are **not** conveyed by this source-only commit; dependencies are acquired separately from their upstream distributions. Anyone creating or conveying an installed/bundled application must meet the AGPL requirements for that actual combination and obtain/provide its required corresponding source. Anyone operating a modified network version must supply required source access to its users. Do not substitute this snapshot for the exact source of a different binary or deployment.

MIT originals and an AGPL-governed combination are compatible scopes, not a claim that every source file has lost its original license. [GNU module-license guidance](https://www.gnu.org/licenses/gpl-faq.en.html#GPLModuleLicense), [GNU license-compatibility guidance](https://www.gnu.org/licenses/license-compatibility.en.html).

### Existing Windows releases are unchanged and separately unverified

This is an additive source publication, not a new binary release or a certification of an earlier EXE. The exact correspondence, complete corresponding-source provision and native-component redistribution conditions of previously published Windows packages have not been established by this audit. Their GitHub Release assets are not replaced, repackaged or reuploaded here. The historical local assembly scripts depend on earlier artifacts and excluded artwork, so independent desktop binary reproduction remains PARTIAL. The remaining binary-specific issues are not represented as resolved merely because the source-publication route was selected.

The included AGPL text was checked against official [PyMuPDF 1.28.2 COPYING](https://github.com/pymupdf/PyMuPDF/blob/1.28.2/COPYING). Adding it does not itself constitute an Artifex commercial license, a source offer for a previously distributed EXE, or proof of compliance of all future distributions.

## Exact production dependency inventory

The following 20 versions are the entries in `requirements-lock.txt` and the retained Windows production lock. License identifiers below were read from the actual installed distribution metadata; Colorama's bundled license text was checked because its metadata only classifies it as BSD. The upstream links are project-maintainer source locations or the project's exact-version PyPI distribution page. These are inventories, not substitute license texts and not a claim that every optional/bundled subcomponent has the same license.

| Distribution | Locked version | Reported/checkable license | Primary upstream |
| --- | --- | --- | --- |
| annotated-doc | 0.0.5 | MIT | [Project](https://github.com/fastapi/annotated-doc) |
| annotated-types | 0.8.0 | MIT | [Project](https://github.com/annotated-types/annotated-types) |
| anyio | 4.15.1 | MIT | [Project](https://github.com/agronholm/anyio) |
| click | 8.5.0 | BSD-3-Clause | [Project](https://github.com/pallets/click) |
| colorama | 0.4.6 | BSD-3-Clause; verified from its LICENSE.txt | [Project](https://github.com/tartley/colorama) |
| fastapi | 0.157.0 | MIT | [Project](https://github.com/fastapi/fastapi) |
| h11 | 0.16.0 | MIT | [Versioned distribution](https://pypi.org/project/h11/0.16.0/) |
| idna | 3.20 | BSD-3-Clause | [Project](https://github.com/kjd/idna) |
| numpy | 2.5.4 | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0; additional wheel notices may apply | [Project](https://github.com/numpy/numpy) |
| opencv-python-headless | 4.14.0.94 | Apache-2.0; wheel includes separate third-party notices | [Project](https://github.com/opencv/opencv-python) |
| opentelemetry-api | 1.45.1 | Apache-2.0 | [Project](https://github.com/open-telemetry/opentelemetry-python) |
| pillow | 12.3.0 | MIT-CMU; bundled codec/library terms can be separate | [Project](https://github.com/python-pillow/Pillow) |
| pydantic | 2.14.1 | MIT | [Project](https://github.com/pydantic/pydantic) |
| pydantic_core | 2.50.1 | MIT | [Project](https://github.com/pydantic/pydantic/tree/main/pydantic-core) |
| pymupdf | 1.28.2 | AGPL v3 selected for the combination; commercial alternative not asserted | [Versioned license](https://github.com/pymupdf/PyMuPDF/blob/1.28.2/COPYING) |
| python-multipart | 0.0.32 | Apache-2.0 | [Project](https://github.com/Kludex/python-multipart) |
| starlette | 1.9.0 | BSD-3-Clause | [Project](https://github.com/Kludex/starlette) |
| typing-inspection | 0.4.4 | MIT | [Project](https://github.com/pydantic/typing-inspection) |
| typing_extensions | 4.16.0 | PSF-2.0 | [Project](https://github.com/python/typing_extensions) |
| uvicorn | 0.54.0 | BSD-3-Clause | [Project](https://github.com/Kludex/uvicorn) |

NumPy, OpenCV, Pillow and PyMuPDF wheels can contain native libraries or other incorporated components under additional terms. This source-only overlay does not redistribute those wheels. Anyone constructing a new binary/runtime distribution must examine the **actual selected platform artifacts**, preserve all required notices/licenses and establish any applicable source/redistribution obligations. The table is not a complete binary software bill of materials. Upgrading or resolving different dependencies requires renewed checks; broad requirements ranges do not inherit this exact-version audit.

## Developer, browser and optional research dependencies

| Component | Version/scope in this project | License/status | Primary upstream |
| --- | --- | --- | --- |
| pytest | requirements-dev range >=8.3,<10; inspected environment 9.1.1 | MIT | [Project](https://github.com/pytest-dev/pytest) |
| httpx | requirements-dev range >=0.28,<1; inspected environment 0.28.1 | BSD-3-Clause | [Project](https://github.com/encode/httpx) |
| playwright | package.json ^1.56.0; package-lock 1.64.1 | Apache-2.0 | [Project](https://github.com/microsoft/playwright) |
| playwright-core | package-lock 1.64.1, transitive from Playwright | Apache-2.0 | [Project](https://github.com/microsoft/playwright) |
| beautifulsoup4 | original optional research tier >=4.13,<5; not a core runtime requirement; not installed in the inspected environment | MIT as reported by upstream; no exact optional resolved version certified here | [Maintainer distribution](https://pypi.org/project/beautifulsoup4/), [project](https://www.crummy.com/software/BeautifulSoup/bs4/) |
| Node.js | optional developer interpreter; package-lock requires Node >=20 through Playwright; no interpreter version locked/bundled | Node.js MIT plus included third-party terms; not just the Playwright license | [Node.js license](https://github.com/nodejs/node/blob/main/LICENSE) |
| Google Chrome | used by the retained smoke test's explicit `channel: 'chrome'`; not bundled | Separate browser terms; not covered by Playwright's Apache license | [Chrome terms](https://www.google.com/chrome/terms/) |

Development/research installations can resolve additional transitive packages. Their complete resolved graph is not fixed by the production lock and is not represented as fully enumerated above. Beautiful Soup is noted for transparency about the original optional research manifest, not because public demo generation or normal image/PDF/DOCX processing requires it. No browser or research dataset is included in this source tree.

## Windows build/runtime prerequisites, not bundled dependencies

The archived Windows build scripts refer to official CPython 3.14.0 embeddable AMD64 Python, .NET Framework C# tooling, and Visual Studio VC143 x64 redistributable components. They are not supplied by this source upload and are not licensed as project MIT code. Anyone rebuilding/redistributing a portable application must separately check the exact interpreter, native libraries and Microsoft redistribution terms and retain their required notices. [CPython release](https://www.python.org/downloads/release/python-3140/), [Python licensing](https://docs.python.org/3/license.html), [Microsoft redistribution reference](https://learn.microsoft.com/en-us/visualstudio/releases/2022/redistribution).

Original bitmap/icon resources without independently established redistribution clearance were conservatively excluded from the desktop-source archive. Their omission is recorded in `windows/README.md`; no replacement artwork or executable was invented to mask this build limitation. The retained demonstrations are identified by their original generator as synthetic. They do not contain experimental source imagery; no external font file is included.

## Interpretation of this notice

This file records the evidence available during source preparation. It does not make a blanket claim that the repository, all future dependency resolutions, existing Releases or any new portable binary have completed legal review. Preserve third-party attribution and original license files whenever third-party material is actually redistributed, follow the owner-selected AGPL route for the combination, and use the audit report's separate security/test/completeness results without interpreting the earlier licensing decision hold as a credential finding.
