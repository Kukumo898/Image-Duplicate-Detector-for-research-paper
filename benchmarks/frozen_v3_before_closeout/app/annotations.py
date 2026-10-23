"""Deterministic, single-view annotation candidates; never image-pair residuals.

Rules identify a narrow subset of raster overlays, not scientific semantics.
Ambiguous colored structures stay in the data candidate mask. Original RGB
pixels are neither edited nor inpainted, and no OCR/model/network is used.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import cv2
import numpy as np


ANNOTATION_VERSION = "1.0.0-single-view-rules"
ANNOTATION_POLICY = {
    "version": ANNOTATION_VERSION,
    "input": "uint8_HxWx3_RGB",
    "coordinate_system": "analysis_view_xy_pixels",
    "text_min_characters": 4,
    "text_white_ring_fraction": 0.60,
    "guide_min_span_fraction": 0.38,
    "guide_max_chromatic_background_fraction": 0.10,
    "guide_min_background_median": 28,
    "guide_max_width_fraction": 0.035,
    "guide_min_dashes": 3,
    "guide_requires_repeated_dashes": True,
    "max_annotation_fraction": 0.35,
    "uncertain_is_retained": True,
    "runtime_downloads": False,
    "ocr": False,
}


@dataclass
class AnnotationAnalysis:
    annotation_mask: np.ndarray
    uncertain_mask: np.ndarray
    data_mask: np.ndarray
    labels: np.ndarray
    metadata: dict[str, Any]


def _text_rows(gray: np.ndarray, chroma: np.ndarray) -> tuple[np.ndarray, list[dict[str, Any]]]:
    """Multiple crisp, character-like components on a white local background.

    Compactness/collinearity alone never excludes a band or fluorescent spot.
    White rings, dark cores, bounded height/width, baseline grouping and varied
    glyph shapes are all required. No recognized words or glyph whitelist.
    """
    h, w = gray.shape
    result = np.zeros_like(gray, np.uint8)
    dark = ((gray < 165) & (chroma < 55)).astype(np.uint8)
    count, components, stats, _ = cv2.connectedComponentsWithStats(dark, 8)
    max_h = max(12, min(64, int(min(h, w) * .10)))
    chars: list[tuple[int, int, int, int, int]] = []
    for k in range(1, count):
        x, y, cw, ch, area = (int(v) for v in stats[k])
        if not (4 <= ch <= max_h and 1 <= cw <= 2.6 * ch and 5 <= area <= 1600):
            continue
        if not (.10 <= area / (cw * ch) <= .90):
            continue
        pad = max(2, int(ch * .25))
        x0, y0, x1, y1 = max(0, x-pad), max(0, y-pad), min(w, x+cw+pad), min(h, y+ch+pad)
        local = gray[y0:y1, x0:x1]
        member = components[y0:y1, x0:x1] == k
        ring = cv2.dilate(member.astype(np.uint8), np.ones((3, 3), np.uint8)).astype(bool) & ~member
        # Soft-edged staining is not an opaque text glyph.
        if (not ring.any() or float((local[ring] >= 220).mean()) < ANNOTATION_POLICY["text_white_ring_fraction"]
                or float(np.median(local[member])) > 115):
            continue
        if float((local >= 230).mean()) < .43:
            continue
        chars.append((x, y, cw, ch, k))
    rows: list[dict[str, Any]] = []
    used: set[int] = set()
    # Shared baseline and limited inter-character gaps, not entire same-y rows.
    for start in sorted(range(len(chars)), key=lambda i: (chars[i][1], chars[i][0])):
        if start in used:
            continue
        reference = chars[start]
        baseline = reference[1] + reference[3]
        compatible = [i for i, c in enumerate(chars) if i not in used
                      and abs(c[1] + c[3] - baseline) <= max(2., reference[3] * .32)
                      and .50 <= c[3] / reference[3] <= 1.8]
        compatible.sort(key=lambda i: chars[i][0])
        groups: list[list[int]] = []
        for i in compatible:
            if not groups or chars[i][0] - (chars[groups[-1][-1]][0] + chars[groups[-1][-1]][2]) > 2.4 * reference[3]:
                groups.append([i])
            else:
                groups[-1].append(i)
        for group in groups:
            if len(group) < ANNOTATION_POLICY["text_min_characters"]:
                continue
            glyphs = [chars[i] for i in group]
            # Identical rows of disks/particles/band fragments remain ambiguous.
            shapes = {(round(c[2] / c[3] * 4), round(stats[c[4], 4] / (c[2]*c[3]) * 5)) for c in glyphs}
            if len(shapes) < 2:
                continue
            x0, y0 = min(c[0] for c in glyphs), min(c[1] for c in glyphs)
            x1, y1 = max(c[0]+c[2] for c in glyphs), max(c[1]+c[3] for c in glyphs)
            pad = max(2, round(float(np.median([c[3] for c in glyphs])) * .25))
            x0, y0, x1, y1 = max(0, x0-pad), max(0, y0-pad), min(w, x1+pad), min(h, y1+pad)
            # This includes whitespace only inside a verified text row, never
            # an entire label column or a hard-coded left margin.
            result[y0:y1, x0:x1] = 1
            rows.append({"bbox_xywh": [x0, y0, x1-x0, y1-y0], "characters": len(group),
                         "method": "white_ring_dark_core_baseline_varied_components"})
            used.update(group)
    return result, rows


def _guides(rgb: np.ndarray, gray: np.ndarray, chroma: np.ndarray) -> tuple[np.ndarray, np.ndarray, list[dict[str, Any]]]:
    """Detect flat-color long raster guides on achromatic textured fields.

    Hue/saturation is ONLY candidate generation. Required extra evidence is
    long-range collinearity, narrow support, achromatic non-black surrounding
    field, textured surrounding field, and repeated dash structure. Continuous
    strokes without independently recognizable arrow/ruler context are uncertain.
    White-background data plots and black-background
    fluorescence are specifically NOT accepted by this rule.
    """
    h, w = gray.shape
    accepted, uncertain = np.zeros_like(gray, np.uint8), np.zeros_like(gray, np.uint8)
    candidates: list[dict[str, Any]] = []
    colorful = (chroma >= 42).astype(np.uint8)
    if int(colorful.sum()) < 12:
        return accepted, uncertain, candidates
    # Every chromatic pixel remains data unless independently established below.
    uncertain[:] = colorful
    background = colorful == 0
    if (not background.any() or float(colorful.mean()) > .22
            or float(np.median(gray[background])) < ANNOTATION_POLICY["guide_min_background_median"]
            or float((gray[background] > 235).mean()) > .65):
        return accepted, uncertain, candidates
    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
    side = max(h, w)
    span_min = max(28, int(side * ANNOTATION_POLICY["guide_min_span_fraction"]))
    # Circular hue bins overlap to avoid a quantization boundary dropping a line.
    bins = sorted(set((hsv[:, :, 0][colorful > 0] // 15).tolist()))
    for hue_bin in bins:
        center = hue_bin * 15 + 7
        hue_delta = np.minimum(abs(hsv[:, :, 0].astype(np.int16)-center), 180-abs(hsv[:, :, 0].astype(np.int16)-center))
        hue_mask = ((hue_delta <= 17) & (colorful > 0)).astype(np.uint8)
        lines = cv2.HoughLinesP(hue_mask, 1, np.pi/360, threshold=max(12, span_min//6),
                                minLineLength=span_min, maxLineGap=max(8, int(side * .13)))
        if lines is None:
            continue
        for coords in lines[:40, 0]:
            x0, y0, x1, y1 = (int(v) for v in coords)
            length = float(np.hypot(x1-x0, y1-y0))
            radius = max(2, min(12, int(side * .016)))
            stripe = np.zeros_like(gray, np.uint8)
            cv2.line(stripe, (x0, y0), (x1, y1), 1, radius * 2 + 1)
            support = (stripe > 0) & (hue_mask > 0)
            yy, xx = np.nonzero(support)
            if len(xx) < 12:
                continue
            dx, dy = (x1-x0)/length, (y1-y0)/length
            along = (xx-x0)*dx + (yy-y0)*dy
            perpendicular = abs((xx-x0)*dy - (yy-y0)*dx)
            width = float(np.quantile(perpendicular, .95) * 2 + 1)
            if width > max(7., side * ANNOTATION_POLICY["guide_max_width_fraction"]):
                continue
            bins_along = np.zeros(int(length)+1, np.uint8)
            positions = np.clip(np.rint(along).astype(int), 0, len(bins_along)-1)
            bins_along[positions] = 1
            occupied = float(bins_along.mean())
            padded = np.r_[0, bins_along, 0].astype(np.int16)
            starts, ends = np.where(np.diff(padded)==1)[0], np.where(np.diff(padded)==-1)[0]
            lengths = ends-starts
            meaningful = lengths >= max(3, width * .7)
            runs = lengths[meaningful]
            regular_dashes = (len(runs) >= ANNOTATION_POLICY["guide_min_dashes"] and
                              float(np.std(runs)/max(1., np.mean(runs))) < .65 and occupied > .28)
            # Even an exactly flat, thin, straight color stroke can be a genuine
            # fiber/data line. Require the extra repeated-dash drawing syntax.
            if not regular_dashes:
                continue
            outer = cv2.dilate(stripe, np.ones((13, 13), np.uint8)).astype(bool)
            ring = outer & ~(stripe > 0)
            if int(ring.sum()) < 30 or float(colorful[ring].mean()) > ANNOTATION_POLICY["guide_max_chromatic_background_fraction"]:
                continue
            ring_values = gray[ring]
            texture = cv2.absdiff(gray, cv2.GaussianBlur(gray, (0, 0), 1.2))
            if (float(np.median(ring_values)) < 28 or float((ring_values > 235).mean()) > .65
                    or float(np.mean(texture[ring])) < 1.2):
                continue
            # Bounded halo includes antialiasing, but cannot expand in response
            # to a paired image. Only the independently selected color stroke.
            halo = max(1, min(3, int(round(side / 180))))
            selected = cv2.dilate(support.astype(np.uint8), np.ones((halo*2+1, halo*2+1), np.uint8))
            novel = int((selected & (1-accepted)).sum())
            accepted |= selected
            if novel > 0:
                candidates.append({"line_xyxy": [x0, y0, x1, y1], "stroke_width": round(width, 2),
                                   "dash_runs": int(len(runs)), "occupied_span_fraction": round(occupied, 4),
                                   "new_pixels": novel, "method": "collinear_flat_color_textured_achromatic_field",
                                   "repeated_dashes": bool(regular_dashes)})
    uncertain &= 1-accepted
    return accepted, uncertain, candidates


def _axes(gray: np.ndarray, text: np.ndarray, rows: list[dict[str, Any]]) -> np.ndarray:
    """Only perpendicular, long chart-frame lines with nearby verified text.

    Data curves and isolated thin bands do not satisfy this contextual rule.
    Grid lines are deliberately not excluded.
    """
    h, w = gray.shape
    result = np.zeros_like(gray, np.uint8)
    if len(rows) < 2 or float((gray > 235).mean()) < .65:
        return result
    dark = (gray < 110).astype(np.uint8) * (1-text)
    horizontal = cv2.morphologyEx(dark, cv2.MORPH_OPEN, np.ones((1, max(20, int(w*.42))), np.uint8))
    n_h, _, stats_h, _ = cv2.connectedComponentsWithStats(horizontal, 8)
    for i in range(1, n_h):
        x, y, cw, ch, _ = (int(v) for v in stats_h[i])
        # Gridlines may overwrite a few pixels of the vertical axis, so require
        # fixed-column occupancy rather than inventing continuity/inpainting.
        for vx in range(max(0, x-5), min(w, x+6)):
            ys = np.flatnonzero(dark[:min(h, y+2), vx])
            if len(ys) < h*.38:
                continue
            vy, bottom = int(ys[0]), int(ys[-1])
            if (bottom-vy >= h*.38 and len(ys)/max(1, bottom-vy+1) >= .85
                    and abs(y-bottom) <= 5 and ch <= 6):
                cv2.rectangle(result, (max(0,x-1), max(0,y-1)), (min(w-1,x+cw), min(h-1,y+ch)), 1, -1)
                cv2.rectangle(result, (max(0,vx-1), max(0,vy-1)), (min(w-1,vx+1), min(h-1,bottom+1)), 1, -1)
    return result


def analyze_annotations(rgb: np.ndarray) -> AnnotationAnalysis:
    """Analyze exactly one uint8 RGB view; all returned masks use its pixels.

    There is intentionally no pair, filename, score, residual, truth or mutable
    caller policy parameter. Metadata is JSON-safe and never embeds mask arrays.
    """
    if not isinstance(rgb, np.ndarray) or rgb.dtype != np.uint8 or rgb.ndim != 3 or rgb.shape[2] != 3:
        raise ValueError("Annotation analysis requires a uint8 H×W×3 RGB array.")
    h, w = rgb.shape[:2]
    if h < 1 or w < 1:
        raise ValueError("Annotation analysis requires non-empty RGB dimensions.")
    labels = np.zeros((h, w), np.uint8)
    uncertain = np.zeros((h, w), np.uint8)
    warnings = ["Morphological candidates are not OCR or scientific semantic labels.",
                "Ambiguous chromatic structures remain data candidates; isolated rulers/arrows may be missed."]
    rows: list[dict[str, Any]] = []
    guides: list[dict[str, Any]] = []
    if min(h, w) >= 24:
        contiguous = np.ascontiguousarray(rgb)
        gray = cv2.cvtColor(contiguous, cv2.COLOR_RGB2GRAY)
        chroma = rgb.max(axis=2).astype(np.int16) - rgb.min(axis=2).astype(np.int16)
        text, rows = _text_rows(gray, chroma)
        guide, uncertain, guides = _guides(contiguous, gray, chroma)
        axes = _axes(gray, text, rows)
        labels[text > 0] = 1
        labels[axes > 0] = 4
        labels[guide > 0] = 3
    else:
        warnings.append("View is too small for annotation context; no exclusion performed.")
    annotation = (labels > 0).astype(np.uint8)
    if float(annotation.mean()) > ANNOTATION_POLICY["max_annotation_fraction"]:
        uncertain |= annotation
        labels[:] = 0
        annotation[:] = 0
        warnings.append("Annotation area exceeds policy bound; all candidates retained as uncertain.")
    uncertain &= 1-annotation
    data = 1-annotation
    areas = {"total_pixels": int(h*w), "annotation_pixels": int(annotation.sum()),
             "uncertain_pixels": int(uncertain.sum()), "data_candidate_pixels": int(data.sum()),
             "annotation_fraction": round(float(annotation.mean()), 6),
             "uncertain_fraction": round(float(uncertain.mean()), 6),
             "data_candidate_fraction": round(float(data.mean()), 6)}
    type_areas = {name: int((labels == code).sum()) for code, name in
                  ((1, "text"), (2, "scale_bar"), (3, "guide_line"), (4, "axis_frame"))}
    return AnnotationAnalysis(annotation, uncertain, data, labels, {
        "version": ANNOTATION_VERSION, "analysis_width": w, "analysis_height": h,
        "coordinate_system": "analysis_view_xy_pixels", "policy": dict(ANNOTATION_POLICY),
        "areas": areas, "type_areas": type_areas, "text_rows": rows[:200], "guide_candidates": guides[:80],
        "methods": {"text": "white-ring/crisp-core/varied-glyph/baseline context",
                    "guide_line": "long collinear repeated dashes + achromatic textured background",
                    "axis_frame": "perpendicular long chart lines with independent text context",
                    "scale_bar": "unclassified without sufficient semantic context; retained"},
        "limitations": ["Not semantic segmentation; no biological identity inferred.",
                        "Regularly dashed scientific signals on gray texture can resemble raster guides.",
                        "Chromatic plot curves, soft stains and black-field fluorescence are retained.",
                        "White-on-dark text, short arrows and isolated scale bars are not reliably excluded."],
        "warnings": warnings, "dependencies": {"opencv": cv2.__version__, "numpy": np.__version__},
        "original_pixels_modified": False,
    })
