"""Conservative, local panel proposals for white-gutter scientific figures.

This is a geometry/content heuristic, not a semantic figure parser. It looks for
rectangular connected content on a near-white background and for chart frames.
It deliberately does not infer boundaries through continuous image texture.
Coordinates refer to the decoded input raster (EXIF rotation is not applied).
No publication names, reference crops, layout templates, or OCR are used.
"""
from __future__ import annotations

import hashlib
import math
from pathlib import Path
import re

import cv2
import numpy as np
from PIL import Image


VERSION = "white-gutter-rectangles-v1.1"
PANEL_VERSION = VERSION
MAX_SOURCE_PIXELS = 40_000_000
MAX_ANALYSIS_PIXELS = 12_000_000
MAX_CANDIDATES = 512

_LIMITATION = (
    "自动面板仅为几何提议，不能保证全部拆分；不识别实验标签、通道语义或无白色沟槽的拼接边界。"
    "请保留整图并人工复核面板范围。"
)


def _bbox_area(box: list[int]) -> int:
    return box[2] * box[3]


def _intersection(a: list[int], b: list[int]) -> int:
    return max(0, min(a[0] + a[2], b[0] + b[2]) - max(a[0], b[0])) * max(
        0, min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1])
    )


def _edge_support(mask: np.ndarray, strip: int = 3) -> list[float]:
    strip = max(1, min(strip, mask.shape[0] // 4, mask.shape[1] // 4))
    return [
        float(mask[:strip].mean(axis=1).max()),
        float(mask[-strip:].mean(axis=1).max()),
        float(mask[:, :strip].mean(axis=0).max()),
        float(mask[:, -strip:].mean(axis=0).max()),
    ]


def _textured(rgb: np.ndarray) -> bool:
    """Reject solid bars/legend keys; a grid may still include a blank channel."""
    h, w = rgb.shape[:2]
    dy, dx = max(1, h // 10), max(1, w // 10)
    sample = rgb[dy:h - dy:max(1, h // 128), dx:w - dx:max(1, w // 128)]
    if min(sample.shape[:2]) < 2:
        return False
    bins = sample.astype(np.uint16) // 16
    unique = np.unique(bins[:, :, 0] * 256 + bins[:, :, 1] * 16 + bins[:, :, 2]).size
    changes = np.max(np.abs(np.diff(sample.astype(np.int16), axis=1)), axis=2) > 8
    return bool(unique >= 12 and changes.mean() >= 0.015)


def _similar_neighbor(a: dict, b: dict) -> bool:
    x, y, w, h = a["bbox"]
    xx, yy, ww, hh = b["bbox"]
    if min(w, ww) / max(w, ww) < 0.80 or min(h, hh) / max(h, hh) < 0.80:
        return False
    row = abs(y - yy) < max(h, hh) * 0.12 and abs(x - xx) < max(w, ww) * 2.3
    column = abs(x - xx) < max(w, ww) * 0.12 and abs(y - yy) < max(h, hh) * 2.3
    return bool((row or column) and _intersection(a["bbox"], b["bbox"]) == 0)


def _proposals(rgb: np.ndarray) -> tuple[list[dict], bool]:
    height, width = rgb.shape[:2]
    min_side = max(32, int(min(height, width) * 0.026))
    min_area = max(32 * 32, int(height * width * 0.0012))
    mask = (rgb.min(axis=2) < 242).astype(np.uint8)
    candidates: list[dict] = []

    def valid(w: int, h: int) -> bool:
        return w >= min_side and h >= min_side and w * h >= min_area and 0.08 <= w / h <= 12.5

    _, _, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    truncated = False
    # Large regions first keep the cost bounded on a very noisy page.
    for index in np.argsort(stats[1:, cv2.CC_STAT_AREA])[::-1] + 1:
        x, y, w, h, area = map(int, stats[index])
        if not valid(w, h):
            continue
        density = area / (w * h)
        if density < 0.64:
            continue
        region = mask[y:y + h, x:x + w]
        edge = float(np.mean(_edge_support(region)))
        if edge < 0.60 and density >= 0.85:
            # JPEG decoders can differ by a few channel values near sharp
            # white boundaries. Sparse 2--4 pixel ringing spurs then enlarge
            # the connected-component box even though the interior remains a
            # very dense rectangle. Check a bounded inner edge strip, without
            # trimming, altering or resampling the returned source pixels.
            fringe = max(4, min(12, math.ceil(min(w, h) * 0.035)))
            supported = _edge_support(region, strip=fringe)
            if min(supported) >= 0.82 and float(np.mean(supported)) >= 0.90:
                edge = float(np.mean(supported))
        if edge < 0.60:
            continue
        candidates.append({
            "bbox": [x, y, w, h],
            "confidence": round(min(0.98, 0.62 + 0.19 * density + 0.17 * edge), 3),
            "method": "white_gutter_dense_rectangle",
            "textured": _textured(rgb[y:y + h, x:x + w]),
        })
        if len(candidates) >= MAX_CANDIDATES:
            truncated = True
            break

    # Long, connected frame lines keep a chart intact: the bars or markers
    # inside an enclosing frame must not become separate image panels.
    dark = (rgb.min(axis=2) < 190).astype(np.uint8)
    horizontal = cv2.morphologyEx(dark, cv2.MORPH_OPEN, np.ones((1, max(32, min(180, width // 20))), np.uint8))
    vertical = cv2.morphologyEx(dark, cv2.MORPH_OPEN, np.ones((max(32, min(180, height // 25)), 1), np.uint8))
    lines = horizontal | vertical
    _, _, frame_stats, _ = cv2.connectedComponentsWithStats(lines, connectivity=8)
    for x, y, w, h, _ in frame_stats[1:]:
        x, y, w, h = map(int, (x, y, w, h))
        if not valid(w, h):
            continue
        # Dense pictures are already handled above. This pass is for sparse
        # white-background plots with three or four connected frame sides.
        if mask[y:y + h, x:x + w].mean() > 0.60:
            continue
        edges = _edge_support(lines[y:y + h, x:x + w], strip=max(3, min(18, int(min(w, h) * 0.045))))
        if sum(e >= 0.75 for e in edges) < 3:
            continue
        candidates.append({
            "bbox": [x, y, w, h], "confidence": 0.88,
            "method": "connected_chart_frame", "textured": True,
        })
        if len(candidates) >= MAX_CANDIDATES:
            truncated = True
            break

    # Do not retain chart columns, blank legend squares, or simple solid keys.
    # A blank fluorescence channel is meaningful when its same-size textured
    # neighbors establish a regular panel grid, so it is kept in that case.
    candidates = [c for c in candidates if c["textured"] or any(
        n["textured"] and n["method"] == "white_gutter_dense_rectangle" and _similar_neighbor(c, n)
        for n in candidates if n is not c
    )]

    # A frame around an entire montage should not hide its image panels.
    montage_frames: set[int] = set()
    for i, parent in enumerate(candidates):
        if parent["method"] != "connected_chart_frame":
            continue
        children = [c for c in candidates if c is not parent
                    and c["method"] == "white_gutter_dense_rectangle"
                    and c["textured"]
                    and _bbox_area(c["bbox"]) < 0.8 * _bbox_area(parent["bbox"])
                    and _intersection(parent["bbox"], c["bbox"]) >= 0.98 * _bbox_area(c["bbox"])]
        if len(children) >= 2 and sum(_bbox_area(c["bbox"]) for c in children) > 0.35 * _bbox_area(parent["bbox"]):
            montage_frames.add(i)
    candidates = [c for i, c in enumerate(candidates) if i not in montage_frames]

    # Largest first: attached text may extend a frame by a few pixels; near-
    # duplicate or contained candidates still represent only one visual panel.
    kept: list[dict] = []
    for candidate in sorted(candidates, key=lambda c: _bbox_area(c["bbox"]), reverse=True):
        if any(_intersection(candidate["bbox"], k["bbox"]) >= 0.94 * _bbox_area(candidate["bbox"]) for k in kept):
            continue
        kept.append(candidate)
    return kept, truncated


def segment_panels(path: Path | str, max_panels: int = 64) -> dict:
    """Return conservative multi-panel proposals in ORIGINAL raster coordinates.

    ``confidence`` describes geometric proposal quality, not duplicate
    probability. ``complete`` is intentionally false: this heuristic cannot
    establish exhaustive semantic coverage, even when all visible rectangles
    appear to have been found. A single continuous image returns no panels.
    """
    if isinstance(max_panels, bool) or not isinstance(max_panels, int) or max_panels < 1:
        raise ValueError("max_panels must be a positive integer")
    source = Path(path)
    warnings = [_LIMITATION]
    with Image.open(source) as original:
        width, height = original.size
        if width <= 0 or height <= 0 or width * height > MAX_SOURCE_PIXELS:
            raise ValueError(f"Panel input exceeds {MAX_SOURCE_PIXELS:,} pixels")
        if getattr(original, "n_frames", 1) > 1:
            warnings.append("输入含多帧；本次面板提议只处理第一帧。请先逐帧导入。")
        if original.getexif().get(274, 1) not in (None, 1):
            warnings.append("坐标基于原始像素方向，未应用 EXIF 旋转；建议先通过导入器规范化。")
        if original.mode in {"RGBA", "LA", "PA"} or "transparency" in original.info:
            rgba = original.convert("RGBA")
            image = Image.alpha_composite(Image.new("RGBA", rgba.size, "white"), rgba).convert("RGB")
        else:
            image = original.convert("RGB")
        scale = min(1.0, math.sqrt(MAX_ANALYSIS_PIXELS / (width * height)))
        if scale < 1:
            image = image.resize((max(1, round(width * scale)), max(1, round(height * scale))), Image.Resampling.LANCZOS)
            warnings.append("大图使用降采样预览定位，窄沟槽可能漏分；输出裁剪仍使用原图像素。")
        array = np.asarray(image)
    proposals, truncated = _proposals(array)
    if len(proposals) < 2:
        proposals = []
        warnings.append("未识别出至少两个可信的独立矩形面板；保持整图，不强制切分。")
    if truncated or len(proposals) > max_panels:
        warnings.append(f"面板数量触及上限 {max_panels}；结果不完整，需人工复核。")
    # Reading order with small row-alignment tolerances, not a fixed grid.
    proposals.sort(key=lambda c: (c["bbox"][1], c["bbox"][0]))
    rows: list[list[dict]] = []
    for proposal in proposals:
        if rows and abs(proposal["bbox"][1] - rows[-1][0]["bbox"][1]) < min(proposal["bbox"][3], rows[-1][0]["bbox"][3]) * 0.08:
            rows[-1].append(proposal)
        else:
            rows.append([proposal])
    ordered = [p for row in rows for p in sorted(row, key=lambda c: c["bbox"][0])][:max_panels]
    panels = []
    sx, sy = width / array.shape[1], height / array.shape[0]
    for p in ordered:
        x, y, w, h = p["bbox"]
        left, top = max(0, math.floor(x * sx)), max(0, math.floor(y * sy))
        right, bottom = min(width, math.ceil((x + w) * sx)), min(height, math.ceil((y + h) * sy))
        panels.append({"bbox": [left, top, right - left, bottom - top],
                       "confidence": p["confidence"], "method": p["method"]})
    return {"version": VERSION, "panels": panels, "warnings": warnings, "complete": False,
            "source_width": width, "source_height": height, "analysis_scale": scale,
            "proposal_count": len(proposals), "limit_reached": bool(truncated or len(proposals) > max_panels)}


def extract_panels(path: Path | str, output_dir: Path | str, max_panels: int = 64) -> tuple[list[dict], dict]:
    """Save unannotated, unscaled PNG crops; parent linkage is the caller's job."""
    source, destination = Path(path), Path(output_dir)
    report = segment_panels(source, max_panels=max_panels)
    records: list[dict] = []
    if not report["panels"]:
        return records, report
    destination.mkdir(parents=True, exist_ok=True)
    stem = re.sub(r"[^\w.-]", "_", source.stem)[:80] or "image"
    key = hashlib.sha256(str(source.resolve()).encode("utf-8")).hexdigest()[:10]
    with Image.open(source) as original:
        # Standard imported RGB/L/RGBA PNGs retain every decoded pixel exactly.
        # PNG cannot represent CMYK; make that exceptional conversion explicit.
        if original.mode not in {"1", "L", "LA", "I", "I;16", "P", "RGB", "RGBA"}:
            original = original.convert("RGB")
            report["warnings"].append("源色彩模式不支持 PNG；面板已转换为 RGB，未缩放或添加标注。")
        for index, panel in enumerate(report["panels"], start=1):
            x, y, width, height = panel["bbox"]
            name = f"{stem}_{key}_panel_{index:03d}.png"
            output = destination / name
            original.crop((x, y, x + width, y + height)).save(output, format="PNG")
            records.append({"path": str(output), "name": name, "bbox": panel["bbox"],
                            "width": width, "height": height, "panel_index": index,
                            "confidence": panel["confidence"], "method": panel["method"]})
    return records, report
