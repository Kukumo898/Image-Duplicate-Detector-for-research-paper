"""Conservative, local scientific-image similarity evidence.

Scores describe evidence strength, NOT calibrated probabilities. An absent finding
does not establish originality. Geometry and pixels are both checked before a
feature match is reported; semantic similarity alone is never a finding.
"""
from __future__ import annotations

import hashlib
import math
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from PIL import Image, ImageOps
from app.annotations import ANNOTATION_VERSION, analyze_annotations

ENGINE_VERSION = "3.0.0-annotations"
VERIFICATION_MASK_VERSION = "single-view-annotation-legacy-proposals-retained-v1"
_MAX_SIDE = 1280
_POLICIES = {
    "strict": dict(ratio=0.68, min_inliers=14, inlier_ratio=0.58,
                   corr=0.92, gradient=0.76, tile_fraction=0.70, global_corr=0.985),
    "balanced": dict(ratio=0.73, min_inliers=11, inlier_ratio=0.48,
                     corr=0.88, gradient=0.68, tile_fraction=0.60, global_corr=0.975),
    "sensitive": dict(ratio=0.77, min_inliers=9, inlier_ratio=0.40,
                      corr=0.84, gradient=0.60, tile_fraction=0.50, global_corr=0.965),
}

# Developed on mechanism controls, then locked before independent evaluation.
# These limits add safeguards; none of the v2 photometric thresholds is lowered.
ANNOTATION_VERIFICATION_POLICY = {
    "version": "bounded-independent-masks-v1",
    "max_excluded_overlap_fraction": 0.30,
    "min_effective_pixels": 700,
    "min_effective_overlap_fraction": 0.70,
    "min_data_independent_points": 8,
    "min_data_point_fraction": 0.35,
    "min_informative_tiles": 4,
    "min_occupied_cells": 4,
    "mask_mapping": "nearest_neighbor_homography_union_of_independent_single_image_masks",
    "pixel_grid": "coarser_supported_native_grid",
    "uncertain_regions": "retained_not_excluded",
    "legacy_ruler_rule": "unclassified_proposals_retained_not_excluded_from_candidates_or_verification",
}


@dataclass
class PreparedImage:
    path: str
    width: int
    height: int
    pixel_hash: str
    rgb: np.ndarray = field(repr=False)
    gray: np.ndarray = field(repr=False)
    scale_x: float
    scale_y: float
    information: dict[str, float]
    _features: dict[Any, tuple[np.ndarray, Any]] = field(default_factory=dict, repr=False)
    _channels: dict[str, Any] = field(default_factory=dict, repr=False)
    _data_mask: np.ndarray | None = field(default=None, repr=False)
    annotations: Any = field(default=None, repr=False)
    _verification_mask: np.ndarray | None = field(default=None, repr=False)
    _legacy_overlay_mask: np.ndarray | None = field(default=None, repr=False)


def _information(gray: np.ndarray) -> dict[str, float]:
    hist = np.bincount(gray.ravel(), minlength=256).astype(np.float64)
    hist /= max(1, gray.size)
    p = hist[hist > 0]
    gradient = cv2.Sobel(gray, cv2.CV_32F, 1, 0) ** 2 + cv2.Sobel(gray, cv2.CV_32F, 0, 1) ** 2
    return {"entropy": round(float(-(p * np.log2(p)).sum()), 4),
            "standard_deviation": round(float(gray.std()), 4),
            "edge_fraction": round(float((gradient > 400).mean()), 5)}


def _informative(image: PreparedImage) -> bool:
    i = image.information
    # Sparse plots and blots may have low entropy despite useful spatial detail.
    return min(image.gray.shape) >= 32 and i["entropy"] >= 0.20 and i["standard_deviation"] >= 5 and i["edge_fraction"] >= 0.006


def _correlation(x: np.ndarray, y: np.ndarray) -> float:
    x, y = x.astype(np.float32), y.astype(np.float32)
    x, y = x - x.mean(), y - y.mean()
    den = float(np.sqrt(np.dot(x.ravel(), x.ravel()) * np.dot(y.ravel(), y.ravel())))
    return float(np.dot(x.ravel(), y.ravel()) / den) if den > 1e-6 else 0.0


def _pixel_evidence(a: np.ndarray, b: np.ndarray, mask: np.ndarray) -> dict[str, Any] | None:
    """Photometric check with independent local tiles and gradient agreement."""
    if np.count_nonzero(mask) < 500:
        return None
    aa = cv2.GaussianBlur(a, (3, 3), 0.65).astype(np.float32)
    bb = cv2.GaussianBlur(b, (3, 3), 0.65).astype(np.float32)
    valid = mask.astype(bool)
    x, y = aa[valid], bb[valid]
    if min(float(x.std()), float(y.std())) < 4:
        return None
    corr = _correlation(x, y)
    # Brightness/contrast may change, but the signal must remain positively related.
    gain = float(np.dot(x - x.mean(), y - y.mean()) / max(float(np.dot(x - x.mean(), x - x.mean())), 1e-6))
    offset = float(y.mean() - gain * x.mean())
    residual = float(np.sqrt(np.mean((y - gain * x - offset) ** 2)) / max(float(y.std()), 4))
    ax, ay = cv2.Sobel(aa, cv2.CV_32F, 1, 0), cv2.Sobel(aa, cv2.CV_32F, 0, 1)
    bx, by = cv2.Sobel(bb, cv2.CV_32F, 1, 0), cv2.Sobel(bb, cv2.CV_32F, 0, 1)
    inner = cv2.erode(mask.astype(np.uint8), np.ones((5, 5), np.uint8)).astype(bool)
    numerator = float((ax[inner] * bx[inner] + ay[inner] * by[inner]).sum())
    denominator = math.sqrt(float((ax[inner] ** 2 + ay[inner] ** 2).sum()) * float((bx[inner] ** 2 + by[inner] ** 2).sum()))
    gradient = numerator / denominator if denominator > 1e-6 else 0.0
    ys, xs = np.where(valid)
    x0, x1, y0, y1 = int(xs.min()), int(xs.max()) + 1, int(ys.min()), int(ys.max()) + 1
    tile_scores: list[float] = []
    for yy in range(4):
        for xx in range(4):
            left, right = x0 + (x1 - x0) * xx // 4, x0 + (x1 - x0) * (xx + 1) // 4
            top, bottom = y0 + (y1 - y0) * yy // 4, y0 + (y1 - y0) * (yy + 1) // 4
            m = valid[top:bottom, left:right]
            if int(m.sum()) < 32:
                continue
            tx, ty = aa[top:bottom, left:right][m], bb[top:bottom, left:right][m]
            if min(float(tx.std()), float(ty.std())) >= 4:
                tile_scores.append(_correlation(tx, ty))
    return {"pixel_correlation": round(corr, 5), "gradient_agreement": round(gradient, 5),
            "normalized_residual": round(residual, 5), "brightness_gain": round(gain, 4),
            "brightness_offset": round(offset, 3), "verified_pixels": int(valid.sum()),
            "informative_tiles": len(tile_scores),
            "consistent_tile_fraction": round(sum(s >= 0.85 for s in tile_scores) / max(1, len(tile_scores)), 4)}


def _annotation_like(gray: np.ndarray, mask: np.ndarray) -> bool:
    """Conservative heuristic for a white-background, multi-character label.

    This is not OCR and deliberately does not classify an entire sparse plot as
    text merely because its background is white. Most dark pixels must belong to
    small, character-sized connected components aligned along a text baseline.
    """
    selected = gray[mask.astype(bool)]
    if len(selected) < 500 or float((selected >= 235).mean()) < .68:
        return False
    dark = ((gray < 175) & mask.astype(bool)).astype(np.uint8)
    count, _, stats, centroids = cv2.connectedComponentsWithStats(dark, connectivity=8)
    chars = []
    for k in range(1, count):
        _, _, width, height, area = stats[k]
        if 3 <= height <= 40 and 2 <= width <= 40 and 6 <= area <= 650 and .12 <= width / height <= 2.5:
            chars.append(k)
    if len(chars) < 8 or int(stats[chars, cv2.CC_STAT_AREA].sum()) / max(1, int(dark.sum())) < .70:
        return False
    heights = stats[chars, cv2.CC_STAT_HEIGHT]
    centers = centroids[chars, 1]
    tolerance = max(3., float(np.median(heights)) * .6)
    return any(int((np.abs(centers - center) < tolerance).sum()) >= 6 for center in centers)


def _scientific_data_mask(gray: np.ndarray) -> np.ndarray:
    """Exclude only compact, solid ruler-like overlays, not biological bands.

    These masks are never positive evidence. Both images' masks are combined
    after alignment, so a moved/replaced scale bar cannot either create a match
    or veto an otherwise verified data region. Soft-edged blot bands are not
    removed unless they satisfy the same very restrictive rectangle criterion.
    """
    result = np.ones_like(gray, dtype=np.uint8)
    height, width = gray.shape
    for binary in ((gray < 20).astype(np.uint8), (gray < 50).astype(np.uint8), (gray > 245).astype(np.uint8)):
        count, _, stats, _ = cv2.connectedComponentsWithStats(binary, connectivity=8)
        for i in range(1, count):
            x, y, w, h, area = (int(v) for v in stats[i])
            long, short = max(w, h), min(w, h)
            if (long < max(22, .07 * max(width, height)) or short > 12
                    or short < 2 or long / short < 6 or area / (w * h) < .78):
                continue
            # A frame border is not a scale bar and must not remove whole edges.
            if long > .65 * max(width, height):
                continue
            result[max(0, y - 3):min(height, y + h + 3),
                   max(0, x - 3):min(width, x + w + 3)] = 0
    return result


def _mask_for(image: PreparedImage) -> np.ndarray:
    if image._data_mask is None:
        # v2's hard-rectangle proposal can also be a real thin band, even in a
        # corner or on noise. Preserve candidate descriptors until independent
        # annotation analysis and final data verification have been evaluated.
        image._data_mask = np.ones_like(image.gray, np.uint8)
    return image._data_mask


def _spatial_support(points: np.ndarray) -> bool:
    if len(points) < 4:
        return False
    eigen = np.linalg.eigvalsh(np.cov(points.T))
    if eigen[0] / max(eigen[1], 1e-6) < .015:
        return False
    # Four occupied cells across at least two rows AND columns prevent a ruler
    # or a single row of repeated symbols from becoming data-region evidence.
    extent = np.ptp(points, axis=0)
    cells = np.minimum(2, ((points - points.min(axis=0)) / np.maximum(extent, 1) * 3).astype(int))
    return len(set(map(tuple, cells))) >= 4 and len(set(cells[:, 0])) >= 2 and len(set(cells[:, 1])) >= 2


def _box_overlap(first: list[int], second: list[int]) -> float:
    x, y = max(first[0], second[0]), max(first[1], second[1])
    right = min(first[0] + first[2], second[0] + second[2])
    bottom = min(first[1] + first[3], second[1] + second[3])
    intersection = max(0, right - x) * max(0, bottom - y)
    return intersection / max(1, min(first[2] * first[3], second[2] * second[3]))


def _same_mapping(first: dict[str, Any], second: dict[str, Any]) -> bool:
    a, b = first["regions_a"][0], second["regions_a"][0]
    x0, y0 = max(a[0], b[0]), max(a[1], b[1])
    x1, y1 = min(a[0] + a[2], b[0] + b[2]), min(a[1] + a[3], b[1] + b[3])
    points = np.float32([[x0, y0], [x0, y1], [x1, y0], [x1, y1]]).reshape(-1, 1, 2)
    aa = cv2.perspectiveTransform(points, np.asarray(first["transform"], np.float64))
    bb = cv2.perspectiveTransform(points, np.asarray(second["transform"], np.float64))
    tolerance = max(4., .015 * max(first["regions_b"][0][2:]))
    return float(np.median(np.linalg.norm(aa - bb, axis=2))) < tolerance


def _orientation(image: np.ndarray, rotation: int, mirrored: bool) -> np.ndarray:
    rotated = np.rot90(image, k=-rotation)
    return np.ascontiguousarray(np.fliplr(rotated) if mirrored else rotated)


def _orientation_matrix(width: int, height: int, rotation: int, mirrored: bool) -> tuple[np.ndarray, int, int]:
    mats = [np.eye(3), np.array([[0., -1., height - 1], [1., 0., 0.], [0., 0., 1.]]),
            np.array([[-1., 0., width - 1], [0., -1., height - 1], [0., 0., 1.]]),
            np.array([[0., 1., 0.], [-1., 0., width - 1], [0., 0., 1.]])]
    rw, rh = (height, width) if rotation % 2 else (width, height)
    matrix = mats[rotation]
    if mirrored:
        matrix = np.array([[-1., 0., rw - 1], [0., 1., 0.], [0., 0., 1.]]) @ matrix
    return matrix, rw, rh


def _box(points: np.ndarray, image: PreparedImage, padding: float = 5) -> list[int]:
    lo = points.min(axis=0) - padding
    hi = points.max(axis=0) + padding
    x0 = max(0, min(image.width - 1, int(math.floor(lo[0] * image.scale_x))))
    y0 = max(0, min(image.height - 1, int(math.floor(lo[1] * image.scale_y))))
    x1 = max(x0 + 1, min(image.width, int(math.ceil(hi[0] * image.scale_x))))
    y1 = max(y0 + 1, min(image.height, int(math.ceil(hi[1] * image.scale_y))))
    return [x0, y0, x1 - x0, y1 - y0]


def _analysis_for(image: PreparedImage) -> Any:
    """Cache a single-image analysis, never a pair-conditioned exclusion."""
    if image.annotations is None:
        image.annotations = analyze_annotations(image.rgb)
    analysis = image.annotations
    for name in ("annotation_mask", "uncertain_mask", "data_mask", "labels"):
        value = getattr(analysis, name, None)
        if not isinstance(value, np.ndarray) or value.shape != image.gray.shape or value.dtype != np.uint8:
            raise ValueError(f"Invalid single-image annotation {name}: expected uint8 analysis-sized mask")
    return analysis


def _retained_mask(image: PreparedImage) -> np.ndarray:
    analysis = _analysis_for(image)
    # Only high-confidence annotation pixels may be excluded. Unknown/data
    # proposals cannot silently delete uncertain scientific content.
    if image._verification_mask is None:
        layers = verification_mask_layers(image.rgb, analysis)
        image._verification_mask = layers["verification_data"]
        image._legacy_overlay_mask = layers["legacy_overlay"]
    return image._verification_mask


def verification_mask_layers(rgb: np.ndarray, analysis: Any = None) -> dict[str, np.ndarray]:
    """Public, pair-independent masks used by every verification path.

    Values are uint8 0/1 in the input RGB analysis-view coordinates. This helper
    is also used by import/export to persist exactly the masks used by the
    detector. The old hard-rectangle ruler heuristic is exposed as a proposal
    only: shape/position/noise context cannot establish ruler semantics, so it
    excludes neither candidate descriptors nor final data pixels. Uncertain
    scientific structures always override annotation exclusions.
    """
    analysis = analyze_annotations(rgb) if analysis is None else analysis
    if rgb.dtype != np.uint8 or rgb.ndim != 3 or rgb.shape[2] != 3:
        raise ValueError("verification masks require uint8 HxWx3 RGB")
    if any(getattr(analysis, key).shape != rgb.shape[:2] for key in ("annotation_mask", "uncertain_mask")):
        raise ValueError("verification mask analysis coordinates do not match RGB")
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    legacy = 1 - _scientific_data_mask(gray)
    excluded = (analysis.annotation_mask != 0) & (analysis.uncertain_mask == 0)
    return {"verification_excluded": excluded.astype(np.uint8),
            "verification_data": (~excluded).astype(np.uint8), "legacy_overlay": legacy}


def _mask_contours(mask: np.ndarray) -> list[list[list[int]]]:
    # CHAIN_APPROX_SIMPLE preserves the exact raster boundary, unlike an epsilon
    # approximation. These compact geometry vectors are not full pixel arrays.
    contours, _ = cv2.findContours(mask.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    return [contour.reshape(-1, 2).astype(int).tolist() for contour in contours]


def _roi_mask(image: PreparedImage, boxes: list[list[int]]) -> np.ndarray:
    mask = np.zeros_like(image.gray, np.uint8)
    for x, y, w, h in boxes:
        x0, y0 = max(0, int(x / image.scale_x)), max(0, int(y / image.scale_y))
        x1 = min(mask.shape[1], int(math.ceil((x + w) / image.scale_x)))
        y1 = min(mask.shape[0], int(math.ceil((y + h) / image.scale_y)))
        mask[y0:y1, x0:x1] = 1
    return mask


def _point_support(points: np.ndarray, retained: np.ndarray) -> tuple[np.ndarray, dict[str, Any]]:
    if not len(points):
        return points, {"total_independent_points": 0, "retained_independent_points": 0,
                        "retained_fraction": None, "occupied_cells": 0, "two_dimensional": False}
    # A keypoint descriptor sees a neighborhood, not only its exact center.
    # Requiring an annotation-free 7x7 neighborhood prevents glyph edges just
    # outside the exclusion contour from counting as independent data support.
    clean = cv2.erode(retained, np.ones((7, 7), np.uint8))
    xy = np.rint(points).astype(int)
    inside = (xy[:, 0] >= 0) & (xy[:, 0] < retained.shape[1]) & (xy[:, 1] >= 0) & (xy[:, 1] < retained.shape[0])
    keep = np.zeros(len(points), bool)
    keep[inside] = clean[xy[inside, 1], xy[inside, 0]] != 0
    selected = points[keep]
    cells = set()
    if len(selected):
        extent = np.ptp(points, axis=0)
        cells = set(map(tuple, np.minimum(2, ((selected - points.min(axis=0)) / np.maximum(extent, 1) * 3).astype(int))))
    return selected, {"total_independent_points": len(points), "retained_independent_points": int(keep.sum()),
                     "retained_fraction": round(float(keep.mean()), 5), "occupied_cells": len(cells),
                     "two_dimensional": _spatial_support(selected)}


def _coverage(image: PreparedImage, overlap: np.ndarray, effective: np.ndarray, own: np.ndarray) -> dict[str, Any]:
    full, total, kept = image.gray.size, int(overlap.sum()), int(effective.sum())
    excluded = total - kept
    own_excluded = int((own == 0).sum())
    return {"full_image_pixels": full, "overlap_pixels": total, "excluded_pixels": excluded,
            "effective_pixels": kept, "own_excluded_full_image_pixels": own_excluded,
            "overlap_fraction_full_image": round(total / max(1, full), 6),
            "excluded_fraction_full_image": round(excluded / max(1, full), 6),
            "effective_fraction_full_image": round(kept / max(1, full), 6),
            "own_excluded_fraction_full_image": round(own_excluded / max(1, full), 6),
            "excluded_fraction_overlap": round(excluded / max(1, total), 6),
            "effective_fraction_overlap": round(kept / max(1, total), 6),
            "analysis_width": image.gray.shape[1], "analysis_height": image.gray.shape[0],
            "analysis_to_original_scale_x": image.scale_x, "analysis_to_original_scale_y": image.scale_y}


class Detector:
    """Reusable detector. Prepare each image once; features are calculated lazily.

    No model downloads, remote services, or image uploads are performed.
    """

    def __init__(self, max_side: int = _MAX_SIDE):
        self.max_side = max(512, min(int(max_side), 2000))
        self._sift = cv2.SIFT_create(nfeatures=2200, contrastThreshold=0.025, edgeThreshold=12)
        self._matcher = cv2.BFMatcher(cv2.NORM_L2)

    def prepare(self, path: str | Path) -> PreparedImage:
        path = Path(path)
        with Image.open(path) as opened:
            # EXIF orientation and alpha compositing are part of pixel normalization.
            if opened.width * opened.height > 80_000_000:
                raise ValueError("图片超过 8000 万像素，请先导出较小的无损图片。")
            im = ImageOps.exif_transpose(opened)
            high_depth = im.mode in {"I", "F", "I;16", "I;16L", "I;16B", "I;16N"}
            if high_depth:
                # Never silently clamp scientific 16-bit/float images to 255 for
                # the exact-pixel hash. The analysis preview is tone-mapped only.
                raw = np.asarray(im)
                hasher = hashlib.sha256(f"RAW:{im.mode}:{im.width}:{im.height}:{raw.dtype}:".encode())
                hasher.update(raw.tobytes())
                digest = hasher.hexdigest()
                values = raw.astype(np.float32)
                finite = np.isfinite(values)
                if not finite.any():
                    raise ValueError("图片不含有限数值像素。")
                low, high = float(values[finite].min()), float(values[finite].max())
                preview = np.zeros(values.shape, np.uint8)
                if high > low:
                    preview[finite] = np.clip((values[finite] - low) * (255 / (high - low)), 0, 255).astype(np.uint8)
                im = Image.fromarray(preview).convert("RGB")
            elif "A" in im.getbands() or "transparency" in im.info:
                rgba = im.convert("RGBA")
                base = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
                im = Image.alpha_composite(base, rgba).convert("RGB")
            else:
                im = im.convert("RGB")
            width, height = im.size
            if not high_depth:
                hasher = hashlib.sha256(f"RGB:{width}:{height}:".encode())
                hasher.update(im.tobytes())
                digest = hasher.hexdigest()
            if max(im.size) > self.max_side:
                im.thumbnail((self.max_side, self.max_side), Image.Resampling.LANCZOS)
            rgb = np.asarray(im).copy()
        gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
        return PreparedImage(str(path.resolve()), width, height, digest, rgb, gray,
                             width / rgb.shape[1], height / rgb.shape[0], _information(gray),
                             annotations=analyze_annotations(rgb))

    def _classify_evidence(self, a: PreparedImage, b: PreparedImage, result: dict[str, Any],
                           policy: dict[str, float], *, points_a: np.ndarray | None = None,
                           points_b: np.ndarray | None = None, roi_a: np.ndarray | None = None,
                           roi_b: np.ndarray | None = None, verification_grid: str | None = None) -> dict[str, Any]:
        """One mandatory post-verifier for exact/global/local/containment/channel.

        Neither mask depends on the other image, residuals, or correlation. The
        only pair-conditioned operation is their fixed homographic intersection.
        Original photometric evidence remains separately inspectable.
        """
        if "evidence_class" in result:
            return result
        aa, ab = _analysis_for(a), _analysis_for(b)
        ma, mb = _retained_mask(a), _retained_mask(b)
        ra = _roi_mask(a, result["regions_a"]) if roi_a is None else roi_a
        rb = _roi_mask(b, result["regions_b"]) if roi_b is None else roi_b
        matrix = (np.diag([1 / b.scale_x, 1 / b.scale_y, 1.])
                  @ np.asarray(result["transform"], np.float64)
                  @ np.diag([a.scale_x, a.scale_y, 1.]))
        inverse = np.linalg.inv(matrix)
        def warp(mask: np.ndarray, transform: np.ndarray, target: PreparedImage) -> np.ndarray:
            return cv2.warpPerspective(mask, transform, (target.gray.shape[1], target.gray.shape[0]),
                                       flags=cv2.INTER_NEAREST, borderMode=cv2.BORDER_CONSTANT, borderValue=0)
        overlap_a, overlap_b = ra & warp(rb, inverse, a), rb & warp(ra, matrix, b)
        valid_a = overlap_a & ma & warp(mb, inverse, a)
        valid_b = overlap_b & mb & warp(ma, matrix, b)
        coverage_a, coverage_b = _coverage(a, overlap_a, valid_a, ma), _coverage(b, overlap_b, valid_b, mb)
        # Match on the coarser native grid, exactly as v2 local verification.
        area_a, area_b = int(overlap_a.sum()), int(overlap_b.sum())
        choose_b = (b.gray.size < a.gray.size if abs(area_b-area_a) <= .01 * max(area_a, area_b)
                    else area_b < area_a)
        if verification_grid == "b" or (verification_grid is None and choose_b):
            target, source, transform, raw_mask, data_mask, grid = b, a, matrix, overlap_b, valid_b, "b"
        else:
            target, source, transform, raw_mask, data_mask, grid = a, b, inverse, overlap_a, valid_a, "a"
        warped = cv2.warpPerspective(source.gray, transform,
                    (target.gray.shape[1], target.gray.shape[0]), flags=cv2.INTER_LINEAR)
        raw = _pixel_evidence(target.gray, warped, raw_mask)
        data = _pixel_evidence(target.gray, warped, data_mask)
        points_a = np.empty((0, 2), np.float32) if points_a is None else points_a
        points_b = np.empty((0, 2), np.float32) if points_b is None else points_b
        pa, sa = _point_support(points_a, ma & warp(mb, inverse, a))
        pb, sb = _point_support(points_b, mb & warp(ma, matrix, b))
        spatial = {"a": sa, "b": sb, "basis": "independent_sift_points" if len(points_a) else "photometric_tiles"}
        reasons: list[str] = []
        limits = ANNOTATION_VERIFICATION_POLICY
        for side, coverage in (("a", coverage_a), ("b", coverage_b)):
            if coverage["excluded_fraction_overlap"] > limits["max_excluded_overlap_fraction"]:
                reasons.append("excessive_annotation_exclusion_" + side)
            if coverage["effective_pixels"] < limits["min_effective_pixels"]:
                reasons.append("insufficient_effective_data_pixels_" + side)
        if len(points_a):
            for side, support in (("a", sa), ("b", sb)):
                if support["retained_independent_points"] < limits["min_data_independent_points"]:
                    reasons.append("insufficient_independent_data_points_" + side)
                if support["retained_fraction"] < limits["min_data_point_fraction"]:
                    reasons.append("annotation_dominated_correspondences_" + side)
                if not support["two_dimensional"] or support["occupied_cells"] < limits["min_occupied_cells"]:
                    reasons.append("spatially_degenerate_data_support_" + side)
        if result["kind"] == "exact_pixels" or result["kind"] == "near_duplicate":
            corr, gradient, tile = policy["global_corr"], .90, .85
        elif "containment" in result["method"]:
            corr, gradient, tile = .990, .92, .85
        else:
            corr, gradient, tile = policy["corr"], policy["gradient"], policy["tile_fraction"]
        if not data:
            reasons.append("insufficient_informative_data_pixels")
        elif (data["pixel_correlation"] < corr or data["gradient_agreement"] < gradient
              or data["consistent_tile_fraction"] < tile or data["informative_tiles"] < 4
              or data["brightness_gain"] <= .1):
            reasons.append("data_pixel_inconsistency")
        raw_annotation = ((_annotation_like(target.gray, raw_mask) and _annotation_like(warped, raw_mask))
                          or (_annotation_like(255 - target.gray, raw_mask) and _annotation_like(255 - warped, raw_mask)))
        # Channel-intensity spots can accidentally resemble glyph components in
        # an inverted gray view. The legacy glyph-only fallback is applicable
        # only to an achromatic ROI in the original single RGB image.
        if raw_annotation and raw_mask.any():
            rgb_range = np.ptp(target.rgb.astype(np.int16), axis=2)
            raw_annotation = float((rgb_range[raw_mask.astype(bool)] > 25).mean()) < .01
        if raw_annotation:
            reasons.append("annotation_like_support_region")
        annotation_points = (len(points_a) and min(sa["retained_fraction"], sb["retained_fraction"]) < .35)
        # Flat interiors of filled vector primitives and their shared bottom
        # edges are not numerical data evidence. Require the actual non-flat
        # foreground on white-background plots to agree as well as the region.
        foreground = None
        if (float((a.gray >= 235).mean()) > .55 and float((b.gray >= 235).mean()) > .55):
            structure = ((target.gray < 225) | (warped < 225)).astype(np.uint8) & data_mask
            foreground = _pixel_evidence(target.gray, warped, structure)
            if not foreground or foreground["pixel_correlation"] < corr or foreground["gradient_agreement"] < gradient:
                reasons.append("foreground_data_not_verified")
        if not reasons:
            classification = "data_supported"
        elif raw_annotation or annotation_points:
            classification = "annotation_only"
            reasons.append("matched_annotation_without_independent_data_support")
        else:
            classification = "inconclusive"
        if result["kind"] == "exact_pixels" and not _informative(a):
            classification = "inconclusive"
            if "low_information_exact_view" not in reasons:
                reasons.append("low_information_exact_view")
        verification = {"raw_evidence": raw, "data_evidence": data,
            "foreground_data_evidence": foreground, "coverage_a": coverage_a, "coverage_b": coverage_b,
            "spatial_support": spatial, "annotation_version": ANNOTATION_VERSION,
            "coordinate_system": "analysis_view_xy_pixels", "verification_grid": grid,
            "analysis_transform_a_to_b": matrix.tolist(),
            "roi_a": {"encoding": "opencv_external_contours_xy_integer_fillpoly", "contours": _mask_contours(ra)},
            "roi_b": {"encoding": "opencv_external_contours_xy_integer_fillpoly", "contours": _mask_contours(rb)},
            "verification_mask_version": VERIFICATION_MASK_VERSION,
            "legacy_overlay_proposals_a": _mask_contours(a._legacy_overlay_mask),
            "legacy_overlay_proposals_b": _mask_contours(b._legacy_overlay_mask),
            "legacy_overlay_proposals_are_excluded": False,
            "reasons": reasons, "policy": dict(limits),
            "photometric_thresholds": {"correlation": corr, "gradient": gradient, "tile_fraction": tile},
            "mask_provenance": "two_independent_single_image_analyses_no_residual_selection"}
        result["metrics"]["annotation_verification"] = verification
        result["evidence_class"] = classification
        if classification != "data_supported":
            result["metrics"]["raw_candidate_explanation"] = result["explanation"]
            result["explanation"] = ("标注/模板相似：未建立独立实验数据复用支持。" if classification == "annotation_only"
                                     else "证据不确定：已执行候选比较，但数据区证据不足或未通过；这不是数据复用报警。")
        return result

    def _features(self, image: PreparedImage, mirrored: bool = False) -> tuple[np.ndarray, Any]:
        if mirrored not in image._features:
            gray = np.ascontiguousarray(np.fliplr(image.gray)) if mirrored else image.gray
            mask = np.ascontiguousarray(np.fliplr(_mask_for(image))) if mirrored else _mask_for(image)
            keypoints, descriptors = self._sift.detectAndCompute(gray, mask * 255)
            pts = np.asarray([k.pt for k in keypoints], dtype=np.float32).reshape(-1, 2)
            if mirrored and len(pts):
                pts[:, 0] = image.gray.shape[1] - 1 - pts[:, 0]
            image._features[mirrored] = (pts, descriptors)
        return image._features[mirrored]

    def compare(self, a: PreparedImage, b: PreparedImage, sensitivity: str = "strict") -> dict[str, Any] | None:
        findings = self.compare_detailed(a, b, sensitivity)["findings"]
        return findings[0] if findings else None

    def compare_detailed(self, a: PreparedImage, b: PreparedImage, sensitivity: str = "strict",
                         max_regions: int = 4) -> dict[str, Any]:
        """Return verified regions plus an audit trail, including on no-match.

        The region budget is a bounded search, not an exhaustive enumeration.
        Diagnostic rejection counts describe candidate hypotheses, not negatives.
        """
        if sensitivity not in _POLICIES:
            raise ValueError("sensitivity 必须是 strict、balanced 或 sensitive。")
        if isinstance(max_regions, bool) or not isinstance(max_regions, int) or not 1 <= max_regions <= 12:
            raise ValueError("max_regions 必须是 1 到 12 的整数。")
        started = time.perf_counter()
        diagnostics: dict[str, Any] = {"engine_version": ENGINE_VERSION, "sensitivity": sensitivity,
            "stages": [], "rejections": {}, "region_budget": max_regions,
            "caveat": "没有已验证匹配不等于没有重复；结果需结合面板、通道及实验标签人工复核。"}
        findings: list[dict[str, Any]] = []
        policy = _POLICIES[sensitivity]

        def finish(status: str | None = None) -> dict[str, Any]:
            for result in findings:
                self._classify_evidence(a, b, result, policy)
            findings.sort(key=lambda result: (result["evidence_class"] == "data_supported", result["score"]), reverse=True)
            counts = {name: sum(f["evidence_class"] == name for f in findings[:max_regions])
                      for name in ("data_supported", "annotation_only", "inconclusive")}
            diagnostics["evidence_class_counts"] = counts
            diagnostics["single_image_analysis"] = {"a": _analysis_for(a).metadata, "b": _analysis_for(b).metadata}
            diagnostics["annotation_verification_rejections"] = [
                {"evidence_class": f["evidence_class"], "reasons": f["metrics"]["annotation_verification"]["reasons"]}
                for f in findings[:max_regions] if f["evidence_class"] != "data_supported"]
            diagnostics.update(status=status or ("verified_findings" if findings else "no_verified_match"),
                               finding_count=len(findings[:max_regions]), elapsed_ms=round((time.perf_counter() - started) * 1000, 2))
            return {"findings": findings[:max_regions], "diagnostics": diagnostics}

        def append(result: dict[str, Any], view_a: PreparedImage = a, view_b: PreparedImage = b) -> None:
            self._classify_evidence(view_a, view_b, result, policy)
            # Same region rediscovered in gray and RGB, or by multiple fits, is
            # one piece of evidence. Different maps/target regions remain distinct.
            for index, previous in enumerate(findings):
                if (min(_box_overlap(previous["regions_a"][0], result["regions_a"][0]),
                        _box_overlap(previous["regions_b"][0], result["regions_b"][0])) > .72
                        and _same_mapping(previous, result)):
                    diagnostics["rejections"]["redundant_region"] = diagnostics["rejections"].get("redundant_region", 0) + 1
                    if result["evidence_class"] == "data_supported" and previous["evidence_class"] != "data_supported":
                        findings[index] = result
                    return
            if sum(f["evidence_class"] == "data_supported" for f in findings) >= max_regions:
                diagnostics["region_budget_reached"] = True
                return
            findings.append(result)

        if a.pixel_hash == b.pixel_hash:
            findings.append({"kind": "exact_pixels", "score": 100.0, "method": "normalized_pixel_sha256",
                    "explanation": "归一化查重视图的尺寸和像素一致；此结论不等于原始文件字节一致，也不代表科研不当行为。",
                    "metrics": {"exact_pixels": True, "width": a.width, "height": a.height,
                                "low_information": not _informative(a), "engine_version": ENGINE_VERSION},
                    "regions_a": [[0, 0, a.width, a.height]], "regions_b": [[0, 0, b.width, b.height]],
                    "matches": [], "transform": np.eye(3).tolist()})
            diagnostics["stages"].append({"stage": "exact_pixels", "accepted": 1})
            return finish()
        if not _informative(a) or not _informative(b):
            diagnostics["rejections"]["insufficient_information"] = 1
            diagnostics["information_a"], diagnostics["information_b"] = a.information, b.information
            return finish("insufficient_information")
        global_stage: dict[str, Any] = {"stage": "global_gray"}
        global_result = self._global(a, b, policy, global_stage)
        diagnostics["stages"].append(global_stage)
        if global_result:
            append(global_result)
            if global_result["evidence_class"] == "data_supported":
                return finish()
        for mirrored in (False, True):
            for result in self._local_regions(a, b, mirrored, policy, diagnostics, max_regions):
                append(result)
            if sum(f["evidence_class"] == "data_supported" for f in findings) >= max_regions:
                return finish()
        # At most three biologically plausible color-channel comparisons. No
        # RGB Cartesian explosion, and brightfield/ordinary plots never enter.
        channel_pairs = self._channel_pairs(a, b)
        diagnostics["channel_pairs_considered"] = len(channel_pairs)
        for channel_a, channel_b in channel_pairs:
            ca, cb = self._channel_view(a, channel_a), self._channel_view(b, channel_b)
            channel_stage: dict[str, Any] = {"stage": "global_channel", "channel_a": channel_a, "channel_b": channel_b}
            channel_result = self._global(ca, cb, policy, channel_stage)
            diagnostics["stages"].append(channel_stage)
            if channel_result:
                self._describe_channel(channel_result, channel_a, channel_b)
                append(channel_result, ca, cb)
            else:
                for mirrored in (False, True):
                    for result in self._local_regions(ca, cb, mirrored, policy, diagnostics, max_regions,
                                                      (channel_a, channel_b)):
                        self._describe_channel(result, channel_a, channel_b)
                        append(result, ca, cb)
            if sum(f["evidence_class"] == "data_supported" for f in findings) >= max_regions:
                return finish()
        # Smooth gels/blots can lack enough independent SIFT features. Use an
        # entire-image containment check, not a relaxed feature-count threshold.
        if not any(f["evidence_class"] == "data_supported" for f in findings) and max(len(self._features(a)[0]), len(self._features(b)[0])) < 100:
            result = self._containment(a, b, policy)
            diagnostics["stages"].append({"stage": "low_feature_containment", "accepted": int(result is not None)})
            if result:
                append(result)
        return finish()

    @staticmethod
    def _describe_channel(result: dict[str, Any], channel_a: str, channel_b: str) -> None:
        result.update(channel_a=channel_a, channel_b=channel_b, review_context="shared_channel_content")
        result["metrics"].update(channel_a=channel_a, channel_b=channel_b)
        result["method"] = "channel_" + result["method"]
        if result.get("evidence_class") in {"annotation_only", "inconclusive"}:
            result["explanation"] = (f"{channel_a} / {channel_b} 色彩分量候选："
                + ("仅建立标注/模板相似，未建立独立数据支持。" if result["evidence_class"] == "annotation_only"
                   else "数据区证据不足或未通过，不是数据复用报警。"))
        else:
            result["explanation"] = (f"{channel_a} / {channel_b} 色彩分量具有经过几何与像素验证的共同内容。"
                                     "单通道与 Merge 共享内容可能是正常实验呈现；须核对实验组、视野和图注，不能据此认定不当行为。")

    @staticmethod
    def _channel_view(image: PreparedImage, name: str) -> PreparedImage:
        if name not in image._channels:
            gray = np.ascontiguousarray(image.rgb[:, :, "RGB".index(name)])
            image._channels[name] = PreparedImage(image.path, image.width, image.height,
                image.pixel_hash + ":" + name, image.rgb, gray, image.scale_x, image.scale_y, _information(gray),
                annotations=_analysis_for(image))
        return image._channels[name]

    def _channel_pairs(self, a: PreparedImage, b: PreparedImage) -> list[tuple[str, str]]:
        def active(image: PreparedImage) -> dict[str, float]:
            if "profile" not in image._channels:
                rgb = image.rgb.astype(np.int16)
                vivid = (rgb.max(axis=2) - rgb.min(axis=2) > 25) & (rgb.max(axis=2) > 30)
                # Fluorescence-like is only a routing heuristic, never a label.
                if float(vivid.mean()) < .015 or float((image.gray < 65).mean()) < .35:
                    image._channels["profile"] = {}
                else:
                    image._channels["profile"] = {name: float(image.rgb[:, :, i].std())
                        for i, name in enumerate("RGB")
                        if float(((rgb[:, :, i] - rgb.min(axis=2) > 15) & (rgb[:, :, i] > 30)).mean()) >= .0075
                        and _informative(self._channel_view(image, name))}
            return image._channels["profile"]
        aa, bb = active(a), active(b)
        shared = [(name, name) for name in "RGB" if name in aa and name in bb]
        shared.sort(key=lambda pair: min(aa[pair[0]], bb[pair[1]]), reverse=True)
        if shared:
            return shared[:3]
        # Recolored single-channel signals are supported without searching every
        # possible color combination in genuine multichannel composites.
        if len(aa) == len(bb) == 1:
            return [(next(iter(aa)), next(iter(bb)))]
        return []

    def compare_paths(self, path_a: str | Path, path_b: str | Path, sensitivity: str = "strict") -> dict[str, Any] | None:
        return self.compare(self.prepare(path_a), self.prepare(path_b), sensitivity)

    def _global(self, a: PreparedImage, b: PreparedImage, policy: dict[str, float],
                stage: dict[str, Any] | None = None) -> dict[str, Any] | None:
        stage = stage if stage is not None else {}
        stage.update(orientation_candidates=0, correlation_candidates=0, accepted=0)
        if any(float(image.gray[_mask_for(image).astype(bool)].std()) < 4
               for image in (a, b) if np.count_nonzero(_mask_for(image))):
            stage["reason"] = "overlay_only_information"
            return None
        bh, bw = b.gray.shape
        thumbnail_scale = min(1, 384 / max(bw, bh))
        target = (max(32, int(bw * thumbnail_scale)), max(32, int(bh * thumbnail_scale)))
        bsmall = cv2.resize(b.gray, target, interpolation=cv2.INTER_AREA)
        for rotation in range(4):
            for mirrored in (False, True):
                transformed = _orientation(a.gray, rotation, mirrored)
                ah, aw = transformed.shape
                if abs((aw / ah) / (bw / bh) - 1) > 0.025:
                    continue
                stage["orientation_candidates"] += 1
                asmall = cv2.resize(transformed, target, interpolation=cv2.INTER_AREA)
                # A cheap first pass avoids detailed checks on unrelated orientations.
                if _correlation(asmall, bsmall) < policy["global_corr"]:
                    continue
                stage["correlation_candidates"] += 1
                evidence = _pixel_evidence(asmall, bsmall, np.ones_like(asmall, dtype=np.uint8))
                if not evidence or evidence["gradient_agreement"] < 0.90 or evidence["consistent_tile_fraction"] < 0.85 or evidence["informative_tiles"] < 4:
                    continue
                matrix, rw, rh = _orientation_matrix(a.width, a.height, rotation, mirrored)
                matrix = np.diag([b.width / rw, b.height / rh, 1.]) @ matrix
                stage["accepted"] = 1
                return {"kind": "near_duplicate", "score": round(min(99.5, 95 + 4.5 * evidence["pixel_correlation"]), 2),
                        "method": "global_orientation_photometric_verification",
                        "explanation": "整图在方向、尺寸及线性亮度变化归一化后，像素结构和多个局部区域高度一致；需结合实验上下文人工复核。",
                        "metrics": {**evidence, "rotation_degrees": rotation * 90, "mirrored": mirrored,
                                    "engine_version": ENGINE_VERSION},
                        "regions_a": [[0, 0, a.width, a.height]], "regions_b": [[0, 0, b.width, b.height]],
                        "matches": [], "transform": matrix.tolist()}
        return None

    def _containment(self, a: PreparedImage, b: PreparedImage,
                     policy: dict[str, float]) -> dict[str, Any] | None:
        threshold = .995 if policy["ratio"] <= .68 else .990
        for source, template, reversed_pair in ((a, b, False), (b, a, True)):
            data_pixels = template.gray[_mask_for(template).astype(bool)]
            if len(data_pixels) < 500 or float(data_pixels.std()) < 4:
                continue
            sh, sw = source.gray.shape
            q = min(1., 640 / max(sh, sw))
            small = cv2.resize(source.gray, (round(sw * q), round(sh * q)), interpolation=cv2.INTER_AREA)
            qx, qy = small.shape[1] / sw, small.shape[0] / sh
            for factor in (1., .75, 1.25, .5, 1.5, 2.):
                for rotation in range(4):
                    for mirrored in (False, True):
                        oriented = _orientation(template.gray, rotation, mirrored)
                        oh, ow = oriented.shape
                        tw, th = round(ow * q * factor), round(oh * q * factor)
                        if min(tw, th) < 48 or tw > small.shape[1] or th > small.shape[0]:
                            continue
                        area_fraction = tw * th / small.size
                        if not .035 <= area_fraction <= .90:
                            continue
                        crop = cv2.resize(oriented, (tw, th), interpolation=cv2.INTER_AREA if factor * q <= 1 else cv2.INTER_LINEAR)
                        if float(crop.std()) < 5:
                            continue
                        response = cv2.matchTemplate(small, crop, cv2.TM_CCOEFF_NORMED)
                        _, maximum, _, location = cv2.minMaxLoc(response)
                        if maximum < threshold:
                            continue
                        x, y = location
                        patch = small[y:y + th, x:x + tw]
                        mask = np.ones_like(patch, dtype=np.uint8)
                        if _annotation_like(patch, mask) or _annotation_like(255 - patch, mask):
                            continue
                        evidence = _pixel_evidence(patch, crop, mask)
                        if (not evidence or evidence["pixel_correlation"] < .990
                                or evidence["gradient_agreement"] < .92
                                or evidence["consistent_tile_fraction"] < .85
                                or evidence["informative_tiles"] < 4):
                            continue
                        orient_matrix, _, _ = _orientation_matrix(template.gray.shape[1], template.gray.shape[0], rotation, mirrored)
                        template_resize = np.diag([tw / ow, th / oh, 1.])
                        translation = np.array([[1., 0., -x], [0., 1., -y], [0., 0., 1.]])
                        matrix = (np.diag([template.scale_x, template.scale_y, 1.])
                                  @ np.linalg.inv(template_resize @ orient_matrix) @ translation
                                  @ np.diag([qx / source.scale_x, qy / source.scale_y, 1.]))
                        source_box = _box(np.float32([[x / qx, y / qy], [(x + tw) / qx, (y + th) / qy]]), source, padding=0)
                        template_box = [0, 0, template.width, template.height]
                        if reversed_pair:
                            matrix = np.linalg.inv(matrix)
                        return {"kind": "local_reuse", "score": round(96 + 2 * maximum, 2),
                                "method": "low_feature_full_template_containment_pixel_verification",
                                "explanation": "低纹理图片的一幅完整查重视图，在另一幅图的局部区域内获得极高像素相关，并通过梯度和分区一致性验证；不依赖降低特征点门槛，仍需人工复核实验上下文。",
                                "metrics": {**evidence, "template_correlation": round(float(maximum), 6),
                                            "template_scale": factor, "rotation_degrees": rotation * 90,
                                            "mirrored": mirrored, "engine_version": ENGINE_VERSION},
                                "regions_a": [template_box if reversed_pair else source_box],
                                "regions_b": [source_box if reversed_pair else template_box],
                                "matches": [], "transform": matrix.tolist()}
        return None

    def _local(self, a: PreparedImage, b: PreparedImage, mirrored: bool,
               policy: dict[str, float]) -> dict[str, Any] | None:
        diagnostics: dict[str, Any] = {"stages": [], "rejections": {}}
        results = self._local_regions(a, b, mirrored, policy, diagnostics, 1)
        return results[0] if results else None

    def _local_regions(self, a: PreparedImage, b: PreparedImage, mirrored: bool,
                       policy: dict[str, float], diagnostics: dict[str, Any], max_regions: int,
                       channels: tuple[str, str] = ("gray", "gray")) -> list[dict[str, Any]]:
        stage: dict[str, Any] = {"stage": "local_geometry", "channel_a": channels[0],
            "channel_b": channels[1], "mirrored": mirrored, "models": 0, "accepted": 0}
        diagnostics["stages"].append(stage)

        def reject(reason: str) -> None:
            diagnostics["rejections"][reason] = diagnostics["rejections"].get(reason, 0) + 1

        ka, da = self._features(a)
        kb, db = self._features(b, mirrored)
        stage.update(features_a=len(ka), features_b=len(kb))
        if da is None or db is None or min(len(da), len(db)) < policy["min_inliers"]:
            reject("insufficient_features")
            return []

        def ratio_matches(first: np.ndarray, second: np.ndarray) -> dict[int, int]:
            good = {}
            for pair in self._matcher.knnMatch(first, second, k=2):
                if len(pair) == 2 and pair[0].distance < policy["ratio"] * pair[1].distance:
                    good[pair[0].queryIdx] = pair[0].trainIdx
            return good

        forward, reverse = ratio_matches(da, db), ratio_matches(db, da)
        pairs = [(i, j) for i, j in forward.items() if reverse.get(j) == i]
        # SIFT can emit multiple orientations at the same point. Do not count these
        # as independent geometric evidence.
        used_a, used_b, unique = set(), set(), []
        for i, j in pairs:
            ca, cb = tuple(np.rint(ka[i] / 3).astype(int)), tuple(np.rint(kb[j] / 3).astype(int))
            if ca not in used_a and cb not in used_b:
                used_a.add(ca)
                used_b.add(cb)
                unique.append((i, j))
        stage.update(forward_matches=len(forward), mutual_matches=len(pairs), independent_matches=len(unique))
        if len(unique) < policy["min_inliers"]:
            reject("insufficient_independent_matches")
            return []
        pa = np.float32([ka[i] for i, _ in unique])
        pb = np.float32([kb[j] for _, j in unique])
        results = []
        # A coherent second model must not be penalized merely because another
        # region underwent a different transformation. Sequential RANSAC removes
        # explained correspondences, including rejected overlay hypotheses.
        for model_index in range(min(8, max_regions + 2)):
            if len(pa) < policy["min_inliers"]:
                break
            matrix, inlier_mask = cv2.findHomography(pa, pb, cv2.RANSAC, 3.0, maxIters=3000, confidence=0.999)
            stage["models"] += 1
            if matrix is None or inlier_mask is None or not np.isfinite(matrix).all():
                reject("geometry_estimation_failed")
                break
            keep = inlier_mask.ravel().astype(bool)
            count, model_ratio = int(keep.sum()), float(keep.mean())
            if count < policy["min_inliers"]:
                reject("insufficient_geometric_inliers")
                break
            ia, ib = pa[keep], pb[keep]
            # Denominator is still genuine competing descriptor evidence, but
            # only in this model's spatial support, not an unrelated second ROI.
            hull_a, hull_b = cv2.convexHull(ia), cv2.convexHull(ib)
            competing = np.array([cv2.pointPolygonTest(hull_a, tuple(map(float, p)), False) >= 0
                                  or cv2.pointPolygonTest(hull_b, tuple(map(float, q)), False) >= 0
                                  for p, q in zip(pa, pb)])
            inlier_ratio = count / max(count, int(competing.sum()))
            result, reason, evidence = self._verify_local(a, b, ia, ib, matrix, mirrored,
                                                          inlier_ratio, len(unique), policy)
            attempt = {"model": model_index, "inliers": count, "model_inlier_ratio": round(model_ratio, 4),
                       "local_inlier_ratio": round(inlier_ratio, 4), "reason": reason}
            if evidence:
                attempt["pixel_evidence"] = evidence
            stage.setdefault("hypotheses", []).append(attempt)
            if result:
                result["metrics"]["model_inlier_ratio"] = round(model_ratio, 4)
                results.append(result)
                stage["accepted"] += 1
                if sum(r["evidence_class"] == "data_supported" for r in results) >= max_regions:
                    break
            else:
                reject(reason)
            pa, pb = pa[~keep], pb[~keep]
        return results

    def _verify_local(self, a: PreparedImage, b: PreparedImage, ia: np.ndarray, ib: np.ndarray,
                      matrix: np.ndarray, mirrored: bool, inlier_ratio: float, mutual_count: int,
                      policy: dict[str, float]) -> tuple[dict[str, Any] | None, str, dict[str, Any] | None]:
        count = len(ia)
        if inlier_ratio < policy["inlier_ratio"]:
            return None, "insufficient_local_consensus", None
        hull_a, hull_b = cv2.convexHull(ia), cv2.convexHull(ib)
        area_a, area_b = float(cv2.contourArea(hull_a)), float(cv2.contourArea(hull_b))
        coverage_a, coverage_b = area_a / a.gray.size, area_b / b.gray.size
        if min(coverage_a, coverage_b) < 0.006 or min(area_a, area_b) < 700:
            return None, "insufficient_region_area", None
        # Collinear annotations, thin line traces, and implausible transforms are
        # insufficient evidence for a scientific image-reuse alert.
        if not _spatial_support(ia) or not _spatial_support(ib):
            return None, "insufficient_two_dimensional_support", None
        predicted = cv2.perspectiveTransform(ia.reshape(-1, 1, 2), matrix).reshape(-1, 2)
        error = np.linalg.norm(predicted - ib, axis=1)
        if float(np.median(error)) > 1.8 or abs(float(np.linalg.det(matrix))) < 1e-8:
            return None, "geometry_reprojection_failed", None
        center = ia.mean(axis=0)
        probes = np.float32([center, center + [10, 0], center + [0, 10]]).reshape(-1, 1, 2)
        out = cv2.perspectiveTransform(probes, matrix).reshape(-1, 2)
        singular = np.linalg.svd(np.column_stack((out[1] - out[0], out[2] - out[0])) / 10, compute_uv=False)
        if singular[-1] < 0.035 or singular[0] > 30 or singular[0] / max(singular[-1], 1e-9) > 5:
            return None, "implausible_geometry", None
        # Verify on the coarser native grid. Magnifying the lower-resolution
        # image falsely penalizes true scale changes through interpolation blur.
        if area_b < area_a:
            target, source, hull, warp_matrix, verification_grid = b, a, hull_b, matrix, "b"
        else:
            target, source, hull, warp_matrix, verification_grid = a, b, hull_a, np.linalg.inv(matrix), "a"
        height, width = target.gray.shape
        warped = cv2.warpPerspective(source.gray, warp_matrix, (width, height), flags=cv2.INTER_LINEAR)
        valid = cv2.warpPerspective(_mask_for(source), warp_matrix, (width, height), flags=cv2.INTER_NEAREST)
        valid &= _mask_for(target)
        region = np.zeros_like(target.gray, dtype=np.uint8)
        cv2.fillConvexPoly(region, np.rint(hull).astype(np.int32), 1)
        # Only test the geometrically supported hull, without expanding into
        # unrelated tissue outside a crop. Annotation exclusions are recorded.
        raw_pixels = int(region.sum())
        valid &= region
        evidence = _pixel_evidence(target.gray, warped, valid)
        if evidence:
            evidence.update(verification_grid=verification_grid, masked_overlay_or_border_pixels=raw_pixels - int(valid.sum()))
        scale_a = np.diag([1 / a.scale_x, 1 / a.scale_y, 1.])
        scale_b = np.diag([b.scale_x, b.scale_y, 1.])
        original_matrix = scale_b @ matrix @ scale_a
        order = np.argsort(error)[:40]
        matched = [[[round(float(ia[k, 0] * a.scale_x), 2), round(float(ia[k, 1] * a.scale_y), 2)],
                    [round(float(ib[k, 0] * b.scale_x), 2), round(float(ib[k, 1] * b.scale_y), 2)]] for k in order]
        strength = min(98.5, 70 + min(12, count * 0.15) + 8 * inlier_ratio + 8 * (evidence or {}).get("pixel_correlation", 0))
        result = {"kind": "local_reuse", "score": round(strength, 2),
                "method": "sift_mutual_ratio_multimodel_ransac_scale_aware_pixel_verification",
                "explanation": "发现多个双向独特特征点在统一几何变换下对应，且对齐后的区域通过像素与梯度一致性验证。框线表示匹配证据范围，不是精确分割，也不直接判定不当复用。",
                "metrics": {**(evidence or {}), "mutual_matches": mutual_count, "inliers": count,
                            "inlier_ratio": round(inlier_ratio, 4), "median_reprojection_error": round(float(np.median(error)), 4),
                            "coverage_a": round(coverage_a, 4), "coverage_b": round(coverage_b, 4),
                            "mirrored": mirrored, "engine_version": ENGINE_VERSION},
                "regions_a": [_box(ia, a)], "regions_b": [_box(ib, b)],
                "matches": matched, "transform": original_matrix.tolist()}
        region_a, region_b = np.zeros_like(a.gray, np.uint8), np.zeros_like(b.gray, np.uint8)
        cv2.fillConvexPoly(region_a, np.rint(hull_a).astype(np.int32), 1)
        cv2.fillConvexPoly(region_b, np.rint(hull_b).astype(np.int32), 1)
        self._classify_evidence(a, b, result, policy, points_a=ia, points_b=ib, roi_a=region_a, roi_b=region_b,
                               verification_grid=verification_grid)
        if result["evidence_class"] == "data_supported":
            # Retain the historical convenient metrics on the accepted data
            # region; the unchanged raw values live in annotation_verification.
            result["metrics"].update(result["metrics"]["annotation_verification"]["data_evidence"] or {})
        return result, result["evidence_class"], evidence


def compare_paths(path_a: str | Path, path_b: str | Path, sensitivity: str = "strict") -> dict[str, Any] | None:
    return Detector().compare_paths(path_a, path_b, sensitivity)
