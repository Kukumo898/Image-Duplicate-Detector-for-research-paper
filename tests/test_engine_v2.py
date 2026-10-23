"""Behavioral regression controls for multi-region and channel evidence.

Synthetic fixtures exercise mechanisms; they are not an independent biomedical
accuracy estimate and contain no coordinates/content from published case images.
"""
import json

import cv2
import numpy as np
import pytest
from PIL import Image

from app.engine import Detector, ENGINE_VERSION


@pytest.fixture
def detector():
    cv2.setNumThreads(2)
    return Detector()


def texture(seed, shape=(480, 640)):
    rng = np.random.default_rng(seed)
    gray = cv2.GaussianBlur(rng.normal(128, 60, shape).astype(np.float32), (0, 0), .85)
    for _ in range(85):
        x, y = int(rng.integers(15, shape[1] - 15)), int(rng.integers(15, shape[0] - 15))
        cv2.circle(gray, (x, y), int(rng.integers(3, 16)), float(rng.integers(20, 225)), 2)
    return np.clip(gray, 0, 255).astype(np.uint8)


def prepare(detector, tmp_path, image, name):
    path = tmp_path / name
    Image.fromarray(image).save(path)
    return detector.prepare(path)


def mapped(finding, point):
    p = np.asarray(finding["transform"]) @ np.array([*point, 1.])
    return p[:2] / p[2]


def test_three_local_regions_different_scales_and_mirror(detector, tmp_path):
    a, b = texture(100), texture(101)
    specifications = [((25, 25, 195, 170), (330, 25), .9, False),
                      ((300, 30, 230, 170), (35, 270), 1., False),
                      ((270, 260, 210, 180), (370, 270), .85, True)]
    expected = []
    for (x, y, w, h), (tx, ty), scale, mirror in specifications:
        patch = a[y:y + h, x:x + w]
        if mirror:
            patch = np.fliplr(patch)
        resized = cv2.resize(patch, (round(w * scale), round(h * scale)), interpolation=cv2.INTER_AREA)
        b[ty:ty + resized.shape[0], tx:tx + resized.shape[1]] = resized
        expected.append(((x + w / 2, y + h / 2), (tx + resized.shape[1] / 2, ty + resized.shape[0] / 2), mirror))
    aa, bb = prepare(detector, tmp_path, a, "multi_a.png"), prepare(detector, tmp_path, b, "multi_b.png")
    result = detector.compare_detailed(aa, bb)
    assert len(result["findings"]) == 3
    for source, target, mirrored in expected:
        corresponding = [f for f in result["findings"] if np.linalg.norm(mapped(f, source) - target) < 3]
        assert len(corresponding) == 1
        assert corresponding[0]["metrics"]["mirrored"] is mirrored
        assert corresponding[0]["metrics"]["pixel_correlation"] >= .92
    assert result["diagnostics"]["finding_count"] == 3
    assert detector.compare(aa, bb)["score"] == result["findings"][0]["score"]
    assert len(detector.compare_detailed(aa, bb, max_regions=1)["findings"]) == 1
    json.dumps(result, ensure_ascii=False, allow_nan=False)


def fluorescence(seed, shape=(360, 440)):
    rng = np.random.default_rng(seed)
    signal = np.zeros(shape, np.float32)
    for _ in range(180):
        center = (int(rng.integers(8, shape[1] - 8)), int(rng.integers(8, shape[0] - 8)))
        cv2.circle(signal, center, int(rng.integers(2, 8)), float(rng.integers(40, 225)), -1)
    return cv2.GaussianBlur(signal, (0, 0), .8).astype(np.uint8)


def test_single_green_channel_shared_with_different_merge(detector, tmp_path):
    green, blue = fluorescence(84), fluorescence(85)
    single = np.stack((np.zeros_like(green), green, np.zeros_like(green)), axis=2)
    merged = np.stack((fluorescence(86), green, blue), axis=2)
    aa, bb = prepare(detector, tmp_path, single, "green.png"), prepare(detector, tmp_path, merged, "merge.png")
    result = detector.compare_detailed(aa, bb)
    assert len(result["findings"]) == 1
    finding = result["findings"][0]
    assert finding["channel_a"] == finding["channel_b"] == "G"
    assert finding["review_context"] == "shared_channel_content"
    assert result["diagnostics"]["channel_pairs_considered"] == 1
    assert finding["metrics"]["pixel_correlation"] > .99


def test_channel_local_crop_and_mirror(detector, tmp_path):
    green = fluorescence(94)
    single = np.stack((np.zeros_like(green), green, np.zeros_like(green)), axis=2)
    target = fluorescence(95)
    target[100:290, 200:410] = np.fliplr(green[30:220, 40:250])
    merged = np.stack((fluorescence(96), target, fluorescence(97)), axis=2)
    result = detector.compare_detailed(prepare(detector, tmp_path, single, "local_green.png"),
                                       prepare(detector, tmp_path, merged, "local_merge.png"))
    channels = [f for f in result["findings"] if f.get("channel_a") == "G"]
    assert len(channels) == 1
    assert channels[0]["metrics"]["mirrored"] is True
    assert np.linalg.norm(mapped(channels[0], (145, 125)) - (305, 195)) < 3


@pytest.mark.parametrize("seed", [102, 103, 104])
def test_independent_fluorescence_shared_annotation_is_not_match(detector, tmp_path, seed):
    images = []
    for sample in (101, seed):
        green = fluorescence(sample)
        rgb = np.stack((np.zeros_like(green), green, fluorescence(sample + 200)), axis=2)
        cv2.rectangle(rgb, (25, 15), (415, 85), (0, 0, 0), -1)
        cv2.putText(rgb, "GCN5 / Merge", (40, 43), cv2.FONT_HERSHEY_SIMPLEX, .8, (255,) * 3, 2)
        cv2.putText(rgb, "10 um - control", (40, 75), cv2.FONT_HERSHEY_SIMPLEX, .7, (255,) * 3, 2)
        cv2.rectangle(rgb, (300, 330), (410, 336), (255,) * 3, -1)
        images.append(prepare(detector, tmp_path, rgb, f"negative_{sample}.png"))
    result = detector.compare_detailed(*images)
    # v3 retains annotation-only candidates rather than erasing the comparison.
    assert not any(f["evidence_class"] == "data_supported" for f in result["findings"])
    assert all(f["evidence_class"] in {"annotation_only", "inconclusive"} for f in result["findings"])
    assert result["diagnostics"]["status"] in {"no_verified_match", "verified_findings"}
    assert result["diagnostics"]["rejections"]


def test_moved_ruler_does_not_hide_true_mirrored_tissue(detector, tmp_path):
    a = texture(174, (300, 420))
    b = np.fliplr(a).copy()
    cv2.rectangle(a, (270, 245), (375, 252), 0, -1)
    cv2.rectangle(b, (270, 245), (375, 252), 0, -1)
    result = detector.compare_detailed(prepare(detector, tmp_path, a, "ruler_a.png"),
                                       prepare(detector, tmp_path, b, "ruler_b.png"))
    assert len(result["findings"]) == 1
    assert result["findings"][0]["metrics"]["mirrored"] is True


def test_shared_rulers_without_shared_texture_do_not_match(detector, tmp_path):
    items = []
    for seed in (251, 252):
        gray = texture(seed, (300, 420))
        for x, y in ((20, 40), (250, 40), (20, 230), (250, 230)):
            cv2.rectangle(gray, (x, y), (x + 110, y + 7), 0, -1)
        items.append(prepare(detector, tmp_path, gray, f"rulers{seed}.png"))
    assert detector.compare_detailed(*items)["findings"] == []


def test_scale_bar_only_template_is_not_a_containment_alert(detector, tmp_path):
    # A ruler can otherwise satisfy several high-correlation tiles despite
    # containing no scientific image texture at all.
    template = np.full((90, 220), 245, np.uint8)
    cv2.rectangle(template, (35, 44), (170, 50), 5, -1)
    source = texture(600, (350, 460))
    source[170:260, 120:340] = template
    aa, bb = prepare(detector, tmp_path, source, "bar_source.png"), prepare(detector, tmp_path, template, "bar_template.png")
    assert detector.compare_detailed(aa, bb)["findings"] == []


def test_diagnostics_insufficient_information_and_exact_low_information(detector, tmp_path):
    aa = prepare(detector, tmp_path, np.full((100, 120), 20, np.uint8), "blank_a.png")
    bb = prepare(detector, tmp_path, np.full((100, 120), 21, np.uint8), "blank_b.png")
    detail = detector.compare_detailed(aa, bb)
    assert detail["diagnostics"]["status"] == "insufficient_information"
    assert detail["diagnostics"]["rejections"]["insufficient_information"] == 1
    exact = detector.compare_detailed(aa, aa)
    assert exact["findings"][0]["metrics"]["low_information"] is True
    assert exact["diagnostics"]["engine_version"] == ENGINE_VERSION == "3.2.0-blot-low-texture"
    assert exact["findings"][0]["evidence_class"] == "inconclusive"
    with pytest.raises(ValueError):
        detector.compare_detailed(aa, bb, max_regions=0)

