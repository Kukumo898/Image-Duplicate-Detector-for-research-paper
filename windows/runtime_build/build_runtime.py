"""Build the portable runtime from CPython's official embed ZIP and exact PyPI wheels.

Run with the *build machine* CPython 3.14 x64.  No copied venv, installer, pip,
build-machine paths or network dependency is needed by the delivered application.
"""
from __future__ import annotations

import argparse
import csv
import email
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import urllib.request
import zipfile

HERE = Path(__file__).resolve().parent
DELIVERY = HERE.parent.parent
DEFAULT_PACKAGE = DELIVERY / "dist" / "图鉴"
EMBED_NAME = "python-3.14.0-embed-amd64.zip"
EMBED_URL = "https://www.python.org/ftp/python/3.14.0/" + EMBED_NAME
# Published by the python.org release page, independently checked before build.
EMBED_MD5 = "7c5d8d8e3213a11bd0e36f8b8eb03431"
RELEASE_PAGE = "https://www.python.org/downloads/release/python-3140/"


def digest(path: Path, algorithm="sha256"):
    return hashlib.file_digest(path.open("rb"), algorithm).hexdigest()


def fetch(url, dest):
    if not dest.exists():
        with urllib.request.urlopen(url, timeout=60) as response:
            dest.write_bytes(response.read())


def json_write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", type=Path, default=DEFAULT_PACKAGE)
    parser.add_argument("--vc-redist", type=Path, default=Path("C:/Program Files/Microsoft Visual Studio/2022/BuildTools/VC/Redist/MSVC/14.44.35112/x64/Microsoft.VC143.CRT"))
    args = parser.parse_args()
    package = args.package.resolve()
    target = package / "runtime" / "python"
    target.mkdir(parents=True, exist_ok=True)
    thirdparty = target / "Lib" / "site-packages"
    thirdparty.mkdir(parents=True, exist_ok=True)
    licenses = package / "LICENSES"
    licenses.mkdir(parents=True, exist_ok=True)
    wheels = HERE / "wheels"
    wheels.mkdir(exist_ok=True)
    lock = package / "release_source" / "requirements-lock.txt"

    embed = HERE / EMBED_NAME
    fetch(EMBED_URL, embed)
    if digest(embed, "md5") != EMBED_MD5:
        raise RuntimeError("CPython archive differs from the official release checksum")
    sbom = HERE / (EMBED_NAME + ".spdx.json")
    fetch(EMBED_URL + ".spdx.json", sbom)
    # Only the build host invokes pip. Its caches and interpreter are NOT copied.
    subprocess.run([
        sys.executable, "-m", "pip", "download", "--only-binary=:all:",
        "--no-deps", "--disable-pip-version-check", "--index-url", "https://pypi.org/simple",
        "-r", str(lock), "--dest", str(wheels),
    ], check=True)

    with zipfile.ZipFile(embed) as archive:
        archive.extractall(target)
    # _pth enforces isolated path initialization: ignore PYTHONHOME/PYTHONPATH,
    # registry installations, global/user site-packages, and the current directory.
    # No 'import site': all required wheels have been checked to contain no .pth.
    (target / "python314._pth").write_text(
        "python314.zip\n.\nLib/site-packages\n../../tools\n../../release_source\n",
        encoding="utf-8",
    )
    shutil.copyfile(target / "LICENSE.txt", licenses / "Python-3.14.0-LICENSE.txt")
    shutil.copyfile(sbom, licenses / sbom.name)
    wanted = dict(line.strip().lower().replace("_", "-").split("==", 1)
                  for line in lock.read_text(encoding="utf-8-sig").splitlines()
                  if line.strip() and not line.startswith("#"))
    distributions = []
    for wheel in sorted(wheels.glob("*.whl")):
        with zipfile.ZipFile(wheel) as archive:
            metadata_name = next(n for n in archive.namelist() if n.endswith(".dist-info/METADATA"))
            metadata = email.message_from_bytes(archive.read(metadata_name))
            name, version = metadata["Name"], metadata["Version"]
            canonical = name.lower().replace("_", "-")
            if wanted.get(canonical) != version:
                continue
            provenance_path = HERE / (wheel.name + ".pypi.json")
            fetch(f"https://pypi.org/pypi/{name}/{version}/json", provenance_path)
            upstream = json.loads(provenance_path.read_text(encoding="utf-8"))
            published = next(x for x in upstream["urls"] if x["filename"] == wheel.name)
            actual = digest(wheel)
            if published["digests"]["sha256"] != actual:
                raise RuntimeError("Wheel SHA256 differs from PyPI: " + wheel.name)
            if any(n.endswith(".pth") for n in archive.namelist()):
                raise RuntimeError("Review wheel .pth before allowing it: " + wheel.name)
            # Wheel installation is deterministic unpacking. There are no wheels
            # with .data/purelib or .data/platlib in the exact locked dependency set.
            if any(".data/purelib/" in n or ".data/platlib/" in n for n in archive.namelist()):
                raise RuntimeError("Wheel layout needs explicit install mapping: " + wheel.name)
            archive.extractall(thirdparty)
            license_files = []
            for filename in archive.namelist():
                base = Path(filename).name.lower()
                if filename.endswith("/"):
                    continue
                if base.startswith(("license", "licence", "copying", "notice")):
                    dest = licenses / (canonical + "-" + version) / filename
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_bytes(archive.read(filename))
                    license_files.append(dest.relative_to(package).as_posix())
            expressions = metadata.get("License-Expression") or metadata.get("License")
            if not expressions:
                expressions = "; ".join(x.removeprefix("License :: ") for x in metadata.get_all("Classifier", []) if x.startswith("License :: "))
            distributions.append({
                "name": name, "version": version, "license": expressions,
                "wheel": wheel.name, "wheel_sha256": actual,
                "url": published["url"], "pypi_metadata_url": f"https://pypi.org/pypi/{name}/{version}/json",
                "installed_relative": "runtime/python/Lib/site-packages",
                "license_files": license_files,
            })
    if {d["name"].lower().replace("_", "-"): d["version"] for d in distributions} != wanted:
        raise RuntimeError("Installed distribution list does not match the lock")
    # The official Python embed only supplies vcruntime140[_1].  The locked
    # PyMuPDF/OpenCV native chain additionally imports msvcp140.dll; shipping just
    # Python would silently borrow an installed VC++ Redistributable on this PC.
    # Use the official installed Visual Studio REDIST folder, never System32.
    vc_source = args.vc_redist.resolve()
    if "redist" not in str(vc_source).lower() or not (vc_source / "msvcp140.dll").is_file():
        raise RuntimeError("Need an official x64 Visual Studio CRT REDIST directory")
    vc_files = []
    for source in sorted(vc_source.glob("*.dll")):
        shutil.copyfile(source, target / source.name)
        vc_files.append({"filename": source.name, "source": str(source), "sha256": digest(source),
                         "destination": (target / source.name).relative_to(package).as_posix()})
    vc_license = licenses / "Microsoft-VC143-CRT"
    vc_license.mkdir(exist_ok=True)
    redist_txt = vc_source.parents[5] / "Licenses" / "2052" / "Redist.txt"
    if redist_txt.is_file():
        shutil.copyfile(redist_txt, vc_license / "Redist.txt")
    # Archive Microsoft's source/redistribution documentation for offline review.
    microsoft_urls = {
        "Redistribution.html": "https://learn.microsoft.com/en-us/visualstudio/releases/2022/redistribution",
        "Redistribute-Visual-CPP.html": "https://learn.microsoft.com/en-us/cpp/windows/redistributing-visual-cpp-files?view=msvc-170",
    }
    for name, url in microsoft_urls.items():
        fetch(url, HERE / name)
        shutil.copyfile(HERE / name, vc_license / name)
    manifest = {
        "schema": "labimage-portable-runtime-v1",
        "target": "Windows x64; CPython 3.14.0 (not free-threaded)",
        "python": {"version": "3.14.0", "url": EMBED_URL, "release_page": RELEASE_PAGE,
                   "sha256": digest(embed), "md5_official_release_page": EMBED_MD5,
                   "sbom_url": EMBED_URL + ".spdx.json", "sbom_sha256": digest(sbom)},
        "requirements_lock_sha256": digest(lock),
        "distributions": distributions,
        "visual_cpp_runtime": {"version_directory": vc_source.parents[1].name,
                               "deployment": "Unmodified official Visual Studio x64 CRT REDIST DLLs, application-local",
                               "files": vc_files, "documentation": microsoft_urls},
        "runtime_isolation": {
            "layout": "Official CPython embeddable ZIP + exact binary wheels",
            "venv_copied": False, "pip_shipped": False, "runtime_downloads": False,
            "external_python_required": False, "site_imported": False,
            "python_environment_ignored": True,
        },
    }
    json_write(package / "dependencies.json", manifest)
    json_write(DELIVERY / "runtime_provenance.json", manifest)
    with (package / "dependencies.csv").open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["name", "version", "license", "wheel", "wheel_sha256", "url"])
        writer.writeheader()
        for row in distributions:
            writer.writerow({k: row[k] for k in writer.fieldnames})
    native = [{"path": p.relative_to(package).as_posix(), "bytes": p.stat().st_size, "sha256": digest(p)}
              for p in sorted(target.rglob("*")) if p.suffix.lower() in (".dll", ".pyd", ".exe")]
    json_write(package / "native_binaries.json", native)
    print(json.dumps({"runtime": str(target), "distributions": len(distributions), "native_binaries": len(native)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
