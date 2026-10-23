"""Behavioral checks on synthetic controls; not a real-world accuracy claim."""
import json

import cv2
import numpy as np
import pytest
from PIL import Image, ImageEnhance

from app.engine import Detector, compare_paths


def textured(seed=13, size=(480, 640)):
    rng = np.random.default_rng(seed)
    noise = rng.normal(0, 1, size).astype(np.float32)
    signal = cv2.GaussianBlur(noise, (0, 0), 1.2)
    signal = np.clip(128 + signal * 115, 0, 255).astype(np.uint8)
    rgb = cv2.cvtColor(signal, cv2.COLOR_GRAY2RGB)
    for _ in range(70):
        center = (int(rng.integers(15, size[1] - 15)), int(rng.integers(15, size[0] - 15)))
        radius = int(rng.integers(3, 18))
        color = int(rng.integers(25, 220))
        cv2.circle(rgb, center, radius, (color, color, color), int(rng.choice([-1, 2])))
    return Image.fromarray(rgb)


@pytest.fixture
def images(tmp_path):
    def save(im, name):
        path = tmp_path / name
        im.save(path)
        return path
    return save


@pytest.fixture
def detector():
    cv2.setNumThreads(2)
    return Detector()


def assert_evidence(result):
    assert result is not None
    assert 0 <= result["score"] <= 100
    assert result["regions_a"] and result["regions_b"]
    assert len(result.get("matches", [])) <= 40
    json.dumps(result, ensure_ascii=False, allow_nan=False)


def test_decoded_pixels_across_file_formats(images, detector):
    original = textured()
    result = detector.compare_paths(images(original, "original.png"), images(original, "copy.bmp"))
    assert_evidence(result)
    assert result["kind"] == "exact_pixels"


def test_transparent_background_is_normalized_without_false_black_frame(images, detector):
    rgba = Image.new("RGBA", (180, 140), (12, 34, 56, 0))
    patch = textured(size=(100, 120))
    rgba.paste(patch, (30, 20))
    flattened = Image.new("RGB", rgba.size, "white")
    flattened.paste(rgba, mask=rgba.getchannel("A"))
    result = detector.compare_paths(images(rgba, "alpha.png"), images(flattened, "flat.png"))
    assert result["kind"] == "exact_pixels"


@pytest.mark.parametrize("variant", ["resize", "rotate90", "mirror", "brightness", "jpeg"])
def test_whole_image_transformations(images, detector, variant):
    original = textured()
    transformed = original
    if variant == "resize":
        transformed = original.resize((400, 300), Image.Resampling.LANCZOS)
    elif variant == "rotate90":
        transformed = original.transpose(Image.Transpose.ROTATE_90)
    elif variant == "mirror":
        transformed = original.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
    elif variant == "brightness":
        transformed = ImageEnhance.Brightness(original).enhance(0.72)
    suffix = "jpg" if variant == "jpeg" else "png"
    result = detector.compare_paths(images(original, "original.png"), images(transformed, f"variant.{suffix}"))
    assert_evidence(result)
    assert result["kind"] in {"near_duplicate", "local_reuse"}


@pytest.mark.parametrize("mirrored", [False, True])
def test_crop_rotation_brightness_and_optional_mirror(images, detector, mirrored):
    original = textured()
    crop = original.crop((120, 85, 505, 400))
    if mirrored:
        crop = crop.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
    transformed = ImageEnhance.Brightness(crop).enhance(0.8).rotate(17, resample=Image.Resampling.BICUBIC, expand=True)
    transformed = transformed.resize((int(transformed.width * .85), int(transformed.height * .85)), Image.Resampling.LANCZOS)
    result = detector.compare_paths(images(original, "original.png"), images(transformed, "transformed.png"))
    assert_evidence(result)
    assert result["kind"] == "local_reuse"
    assert result["metrics"]["inliers"] >= 14
    assert result["metrics"]["pixel_correlation"] >= .92
    assert result["metrics"]["mirrored"] is mirrored


def test_small_region_pasted_on_different_background(images, detector):
    original = textured()
    altered = textured(28)
    altered.paste(original.crop((190, 120, 420, 330)), (40, 205))
    result = detector.compare_paths(images(original, "original.png"), images(altered, "pasted.png"))
    assert_evidence(result)
    assert result["kind"] == "local_reuse"
    assert result["regions_a"][0][0] > 150
    assert result["regions_b"][0][0] < 100


def test_unrelated_microscopy_like_textures_do_not_match(images, detector):
    original = detector.prepare(images(textured(6), "a.png"))
    for seed in (16, 52, 94):
        unrelated = detector.prepare(images(textured(seed), f"b{seed}.png"))
        assert detector.compare(original, unrelated) is None


def test_blank_and_smooth_low_information_are_not_near_duplicates(images, detector):
    blank_a = images(Image.new("RGB", (640, 480), (240, 240, 240)), "a.png")
    blank_b = images(Image.new("RGB", (640, 480), (235, 235, 235)), "b.png")
    assert detector.compare_paths(blank_a, blank_b) is None
    exact = detector.compare_paths(blank_a, blank_a)
    assert exact["kind"] == "exact_pixels"
    assert exact["metrics"]["low_information"] is True


def test_sixteen_bit_images_are_not_clipped_to_identical_white(images, detector):
    rng = np.random.default_rng(33)
    a = rng.integers(1000, 60000, (200, 240), dtype=np.uint16)
    b = rng.integers(1000, 60000, (200, 240), dtype=np.uint16)
    path_a = images(Image.fromarray(a), "a16.tiff")
    path_b = images(Image.fromarray(b), "b16.tiff")
    assert detector.compare_paths(path_a, path_b) is None
    assert detector.compare_paths(path_a, path_a)["kind"] == "exact_pixels"


def test_similar_repeated_structure_different_content(images, detector):
    # Same regular lattice, but independently generated spots. A few shared motif
    # edges are not enough to establish a consistent, pixel-verified reused area.
    fixtures = []
    for seed in (51, 98):
        rng = np.random.default_rng(seed)
        image = np.full((480, 640, 3), 230, np.uint8)
        for y in range(30, 470, 45):
            for x in range(30, 630, 45):
                radius = int(rng.integers(5, 17))
                color = int(rng.integers(20, 180))
                cv2.circle(image, (x, y), radius, (color,) * 3, -1)
        fixtures.append(images(Image.fromarray(image), f"lattice{seed}.png"))
    assert detector.compare_paths(*fixtures) is None


def blot(seed=17):
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:480, 0:640]
    signal = np.full((480, 640), 233., np.float32)
    signal += cv2.GaussianBlur(rng.normal(0, 4, signal.shape).astype(np.float32), (0, 0), .6)
    for y in (80, 175, 290, 390):
        for x in (90, 200, 310, 420, 530):
            sx = float(rng.uniform(14, 32))
            sy = float(rng.uniform(3, 9))
            xj, yj = x + rng.uniform(-6, 6), y + rng.uniform(-15, 15)
            signal -= rng.uniform(50, 150) * np.exp(-.5 * (((xx - xj) / sx) ** 2 + ((yy - yj) / sy) ** 2))
    return Image.fromarray(np.clip(signal, 0, 255).astype(np.uint8)).convert("RGB")


@pytest.mark.parametrize("variant", ["crop", "mirror", "rotate90"])
def test_blot_like_image_crop(images, detector, variant):
    original = blot()
    cropped = original.crop((45, 35, 570, 350))
    if variant == "mirror":
        cropped = cropped.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
    elif variant == "rotate90":
        cropped = cropped.transpose(Image.Transpose.ROTATE_90)
    path_a, path_b = images(original, "blot.png"), images(cropped, "blot_crop.png")
    result = detector.compare_paths(path_a, path_b)
    assert_evidence(result)
    matrix = np.asarray(result["transform"])
    mapped = matrix @ np.array([307.5, 192.5, 1.])
    mapped = mapped[:2] / mapped[2]
    assert np.linalg.norm(mapped - np.array(cropped.size) / 2) < 3
    reverse = detector.compare_paths(path_b, path_a)
    assert_evidence(reverse)
    reverse_point = np.asarray(reverse["transform"]) @ np.array([*mapped, 1.])
    reverse_point = reverse_point[:2] / reverse_point[2]
    assert np.linalg.norm(reverse_point - [307.5, 192.5]) < 3


def test_independent_blot_bands_in_same_lane_layout_do_not_match(images, detector):
    assert detector.compare_paths(images(blot(17), "blot_a.png"), images(blot(51), "blot_b.png")) is None


def graph(seed):
    image = np.full((480, 640, 3), 255, np.uint8)
    cv2.line(image, (65, 30), (65, 410), (0, 0, 0), 2)
    cv2.line(image, (65, 410), (600, 410), (0, 0, 0), 2)
    for i in range(6):
        cv2.putText(image, str(i), (55 + 100 * i, 440), cv2.FONT_HERSHEY_SIMPLEX, .55, (0, 0, 0), 1)
    rng = np.random.default_rng(seed)
    for color in ((30, 80, 170), (170, 70, 30)):
        points = np.array([(80 + 24 * i, int(rng.integers(70, 380))) for i in range(21)], np.int32)
        cv2.polylines(image, [points], False, color, 2, cv2.LINE_AA)
        for point in points:
            cv2.circle(image, tuple(point), 4, color, -1)
    return Image.fromarray(image)


def test_plot_copy_with_rotation_and_recompression(images, detector):
    original = graph(17)
    transformed = original.transpose(Image.Transpose.ROTATE_180)
    result = detector.compare_paths(images(original, "graph.png"), images(transformed, "graph.jpg"))
    assert_evidence(result)


def test_sparse_plot_same_data_recolored(images, detector):
    paths = []
    for index, color in enumerate(((40, 80, 170), (160, 60, 90))):
        image = np.full((480, 640, 3), 255, np.uint8)
        cv2.line(image, (65, 70), (65, 410), (0, 0, 0), 2)
        cv2.line(image, (65, 410), (600, 410), (0, 0, 0), 2)
        cv2.putText(image, "Synthetic response assay", (65, 35), cv2.FONT_HERSHEY_SIMPLEX, .75, (30, 30, 30), 2)
        points = np.array([(100, 360), (190, 310), (280, 320), (370, 230), (460, 180), (550, 110)], np.int32)
        cv2.polylines(image, [points], False, color, 2, cv2.LINE_AA)
        for k, point in enumerate(points):
            cv2.circle(image, tuple(point), 4, color, -1)
            cv2.putText(image, str(k), (int(point[0]), 440), cv2.FONT_HERSHEY_SIMPLEX, .6, (0, 0, 0), 1)
        paths.append(images(Image.fromarray(image), f"sparse{index}.png"))
    assert_evidence(detector.compare_paths(*paths))


def test_different_plot_data_with_same_axes_is_not_reuse(images, detector):
    assert detector.compare_paths(images(graph(17), "graph_a.png"), images(graph(76), "graph_b.png")) is None


def test_common_multiline_annotation_is_not_a_data_reuse_alert(images, detector):
    fixtures = []
    for seed in (17, 76):
        image = np.asarray(graph(seed)).copy()
        cv2.rectangle(image, (130, 40), (510, 135), (255, 255, 255), -1)
        cv2.putText(image, "Expression level", (145, 75), cv2.FONT_HERSHEY_SIMPLEX, .9, (0, 0, 0), 2)
        cv2.putText(image, "Treatment versus control", (145, 115), cv2.FONT_HERSHEY_SIMPLEX, .7, (0, 0, 0), 2)
        fixtures.append(images(Image.fromarray(image), f"label{seed}.png"))
    # v3 preserves the shared-annotation candidate for audit; it is not a
    # data-reuse alert. The detailed API exposes the classification explicitly.
    result = detector.compare_detailed(*(detector.prepare(path) for path in fixtures))
    assert not any(f["evidence_class"] == "data_supported" for f in result["findings"])


def test_large_images_regions_use_original_coordinates(images, detector):
    original = textured().resize((1920, 1440), Image.Resampling.LANCZOS)
    cropped = original.crop((540, 360, 1350, 1080))
    result = detector.compare_paths(images(original, "large.png"), images(cropped, "crop.png"))
    assert_evidence(result)
    box = result["regions_a"][0]
    assert box[0] > 490 and box[1] > 310
    assert 600 < box[2] < 900 and 500 < box[3] < 800


def test_convenience_function_and_invalid_policy(images):
    path = images(textured(), "single.png")
    assert compare_paths(path, path)["kind"] == "exact_pixels"
    with pytest.raises(ValueError):
        compare_paths(path, path, "unknown")
