"""Generate auditable panel-proposal previews without altering detection crops.

Example:
  python scripts/inspect_panels.py figure.png --output output/panel_inspection
The preview contains boxes and numeric IDs; extract_panels outputs do not.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
from time import perf_counter

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.panels import extract_panels  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("images", type=Path, nargs="+")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-panels", type=int, default=64)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    reports = []
    for source in args.images:
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        destination = args.output / f"{source.stem}_{digest[:8]}"
        started = perf_counter()
        records, report = extract_panels(source, destination, args.max_panels)
        elapsed = perf_counter() - started
        preview = Image.open(source).convert("RGB")
        draw = ImageDraw.Draw(preview)
        thickness = max(2, round(min(preview.size) / 500))
        for record in records:
            x, y, w, h = record["bbox"]
            color = "#ff7b00" if record["method"] == "connected_chart_frame" else "#00dfcf"
            draw.rectangle((x, y, x + w - 1, y + h - 1), outline=color, width=thickness)
            label = str(record["panel_index"])
            font_size = max(14, round(min(preview.size) / 70))
            text_box = draw.textbbox((x + 3, y + 2), label, font_size=font_size)
            draw.rectangle(text_box, fill="black")
            draw.text((x + 3, y + 2), label, fill="white", font_size=font_size)
        destination.mkdir(parents=True, exist_ok=True)
        preview_path = destination / "panel_proposals_preview.jpg"
        preview.save(preview_path, quality=92)
        entry = {"source": str(source), "source_sha256": digest,
                 "elapsed_seconds_with_png_export": round(elapsed, 4),
                 "preview": str(preview_path), "report": report, "records": records,
                 "evaluation_note": "Automatic proposals, not a manually adjudicated accuracy estimate."}
        (destination / "report.json").write_text(json.dumps(entry, ensure_ascii=False, indent=2), encoding="utf-8")
        reports.append(entry)
        print(f"{source.name}: {len(records)} proposals; {elapsed:.3f} s with PNG export")
    (args.output / "summary.json").write_text(json.dumps(reports, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
