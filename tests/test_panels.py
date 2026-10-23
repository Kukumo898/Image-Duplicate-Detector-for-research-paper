"""Behavioral tests for generic panel proposals, not a publication template.

The Nature/Figcheck figures are development regression examples and are not
used as an independent accuracy estimate. These tests use randomized content
and layouts to avoid baking those figures' coordinates into the detector.
"""
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw
import pytest

from app.panels import extract_panels, segment_panels


def _noise(rng, width, height, mode="RGB"):
    shape = (height, width, 3) if mode == "RGB" else (height, width)
    return Image.fromarray(rng.integers(10, 225, shape, dtype=np.uint8))


def _iou(a, b):
    x = max(0, min(a[0] + a[2], b[0] + b[2]) - max(a[0], b[0]))
    y = max(0, min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1]))
    return x * y / (a[2] * a[3] + b[2] * b[3] - x * y)


def _grid(tmp_path, seed=1, rows=3, cols=4, gutter=8, mode="RGB"):
    rng = np.random.default_rng(seed)
    w, h = map(int, rng.integers(65, 150, 2))
    margin = int(rng.integers(8, 28))
    image = Image.new(mode, (cols * w + (cols - 1) * gutter + margin * 2,
                            rows * h + (rows - 1) * gutter + margin * 2), "white")
    boxes = []
    for r in range(rows):
        for c in range(cols):
            x, y = margin + c * (w + gutter), margin + r * (h + gutter)
            image.paste(_noise(rng, w, h, mode=mode), (x, y))
            boxes.append([x, y, w, h])
    path = tmp_path / f"grid_{seed}.png"
    image.save(path)
    return path, boxes


@pytest.mark.parametrize("seed,rows,cols,gutter", [
    (7, 2, 3, 1), (12, 4, 5, 2), (30, 2, 2, 12), (81, 3, 5, 22),
    (92, 6, 3, 3), (109, 3, 4, 7), (217, 2, 6, 15), (501, 4, 4, 1),
])
def test_randomized_grids_and_one_pixel_gutters(tmp_path, seed, rows, cols, gutter):
    path, expected = _grid(tmp_path, seed=seed, rows=rows, cols=cols, gutter=gutter)
    report = segment_panels(path)
    actual = [p["bbox"] for p in report["panels"]]
    assert len(actual) == rows * cols
    assert all(max(_iou(box, got) for got in actual) > 0.98 for box in expected)
    assert report["complete"] is False
    assert report["warnings"]  # automatic suggestions never claim completeness


def test_black_fluorescence_panels_include_blank_sibling(tmp_path):
    rng = np.random.default_rng(32)
    canvas = Image.new("RGB", (510, 350), "white")
    expected = []
    for row in range(2):
        for col in range(3):
            x, y = 12 + col * 166, 12 + row * 168
            pixels = np.zeros((156, 154, 3), dtype=np.uint8)
            if (row, col) != (1, 1):
                pixels[:, :, col] = rng.integers(0, 200, (156, 154), dtype=np.uint8)
            # A scale bar remains within, not a separate candidate.
            pixels[-12:-8, -45:-9] = 255
            canvas.paste(Image.fromarray(pixels), (x, y))
            expected.append([x, y, 154, 156])
    path = tmp_path / "channels.png"
    canvas.save(path)
    actual = [p["bbox"] for p in segment_panels(path)["panels"]]
    assert len(actual) == 6
    assert all(max(_iou(box, got) for got in actual) > 0.98 for box in expected)


def test_mixed_plot_text_and_photographs_not_bar_fragments(tmp_path):
    rng = np.random.default_rng(18)
    canvas = Image.new("RGB", (940, 660), "white")
    draw = ImageDraw.Draw(canvas)
    # Several disconnected bars live inside one connected plot frame.
    draw.rectangle((500, 40, 900, 290), outline="black", width=3)
    for x, y, color in [(530, 150, "red"), (640, 100, "blue"), (760, 180, "green")]:
        draw.rectangle((x, y, x + 60, 287), fill=color)
    for index in range(20):
        draw.text((30, 20 + index * 13), "Methods: independent cells, dose, mean and s.d.", fill="black")
    canvas.paste(_noise(rng, 340, 220), (40, 360))
    canvas.paste(_noise(rng, 340, 220), (500, 360))
    draw.text((15, 338), "a", fill="black")
    draw.text((475, 338), "b", fill="black")
    draw.rectangle((275, 550, 350, 555), fill="white")
    path = tmp_path / "mixed.png"
    canvas.save(path)
    panels = segment_panels(path)["panels"]
    assert len(panels) == 3
    for expected in ([500, 40, 401, 251], [40, 360, 340, 220], [500, 360, 340, 220]):
        assert max(_iou(expected, p["bbox"]) for p in panels) > 0.97


@pytest.mark.parametrize("framed", [False, True])
def test_single_chart_not_split_into_bars_or_markers(tmp_path, framed):
    image = Image.new("RGB", (640, 440), "white")
    draw = ImageDraw.Draw(image)
    if framed:
        draw.rectangle((50, 40, 600, 370), outline="black", width=3)
    else:
        draw.line((50, 40, 50, 370, 600, 370), fill="black", width=3)
    for i, color in enumerate(["red", "green", "blue", "purple"]):
        x = 100 + i * 115
        draw.rectangle((x, 100 + i * 40, x + 60, 369), fill=color)
        draw.text((x, 385), "Group", fill="black")
    path = tmp_path / f"chart_{framed}.png"
    image.save(path)
    assert segment_panels(path)["panels"] == []


def test_frame_around_a_montage_does_not_hide_children(tmp_path):
    path, expected = _grid(tmp_path, seed=14, rows=2, cols=2, gutter=16)
    image = Image.open(path)
    draw = ImageDraw.Draw(image)
    draw.rectangle((1, 1, image.width - 2, image.height - 2), outline="black", width=2)
    image.save(path)
    panels = segment_panels(path)["panels"]
    assert len(panels) == 4
    assert all(max(_iou(b, p["bbox"]) for p in panels) > 0.98 for b in expected)


@pytest.mark.parametrize("mode", ["RGB", "L"])
def test_continuous_texture_is_not_cut_on_content_or_scale_bar(tmp_path, mode):
    image = _noise(np.random.default_rng(431), 580, 450, mode=mode)
    draw = ImageDraw.Draw(image)
    draw.ellipse((240, 100, 370, 230), fill="white")
    draw.rectangle((420, 405, 540, 412), fill="white")
    path = tmp_path / f"single_{mode}.png"
    image.save(path)
    assert segment_panels(path)["panels"] == []


def test_whitespace_letters_and_small_keys_are_not_panels(tmp_path):
    image = Image.new("RGB", (600, 400), "white")
    draw = ImageDraw.Draw(image)
    for i in range(6):
        draw.text((20, 40 + i * 40), "abcdef Ghijk scientific labels 123456", fill="black")
        draw.rectangle((450, 40 + i * 40, 478, 68 + i * 40), fill="blue")
    path = tmp_path / "text.png"
    image.save(path)
    assert segment_panels(path)["panels"] == []


def test_export_has_exact_original_pixels_and_legal_coordinates(tmp_path):
    path, expected = _grid(tmp_path, seed=15, rows=3, cols=3, gutter=5)
    records, report = extract_panels(path, tmp_path / "out")
    original = np.asarray(Image.open(path))
    assert len(records) == len(expected)
    for index, record in enumerate(records, start=1):
        x, y, w, h = record["bbox"]
        assert 0 <= x < x + w <= original.shape[1]
        assert 0 <= y < y + h <= original.shape[0]
        assert record["width"] == w and record["height"] == h
        assert record["panel_index"] == index
        saved = np.asarray(Image.open(record["path"]))
        assert saved.shape == (h, w, 3)
        assert np.array_equal(saved, original[y:y + h, x:x + w])
    assert [r["bbox"] for r in records] == [p["bbox"] for p in report["panels"]]


def test_transparency_is_used_for_proposals_but_preserved_in_crops(tmp_path):
    path, expected = _grid(tmp_path, seed=20, rows=2, cols=2)
    image = Image.open(path).convert("RGBA")
    pixels = np.asarray(image).copy()
    pixels[np.all(pixels[:, :, :3] == 255, axis=2)] = (0, 0, 0, 0)
    Image.fromarray(pixels).save(path)
    records, _ = extract_panels(path, tmp_path / "alpha")
    assert len(records) == 4
    for record in records:
        x, y, w, h = record["bbox"]
        assert Image.open(record["path"]).mode == "RGBA"
        assert np.array_equal(np.asarray(Image.open(record["path"])), pixels[y:y + h, x:x + w])


def test_limit_is_explicit_and_does_not_exceed_budget(tmp_path):
    path, _ = _grid(tmp_path, seed=42, rows=4, cols=4)
    records, report = extract_panels(path, tmp_path / "limited", max_panels=5)
    assert len(records) == 5
    assert report["limit_reached"] is True
    assert report["proposal_count"] == 16
    assert report["complete"] is False
    assert any("5" in warning for warning in report["warnings"])


def test_sparse_jpeg_boundary_spurs_do_not_hide_dense_rectangles(tmp_path):
    rng = np.random.default_rng(1008)
    image = Image.new("RGB", (620, 320), "white")
    expected = []
    for x in (30, 330):
        y, w, h = 40, 240, 220
        image.paste(_noise(rng, w, h), (x, y))
        draw = ImageDraw.Draw(image)
        # Sparse decoder ringing protrudes four pixels past every edge; the
        # returned rectangle must still retain the ORIGINAL pixels, not repair
        # the boundary or erase genuine signal.
        for k in range(20, 190, 31):
            draw.line((x + k, y - 4, x + k, y + 1), fill=(241, 240, 241))
            draw.line((x + k, y + h - 2, x + k, y + h + 3), fill=(240, 241, 240))
            draw.line((x - 4, y + k, x + 1, y + k), fill=(241, 240, 240))
            draw.line((x + w - 2, y + k, x + w + 3, y + k), fill=(240, 241, 241))
        expected.append([x, y, w, h])
    path = tmp_path / "ringing.png"
    image.save(path)
    panels = segment_panels(path)["panels"]
    assert len(panels) == 2
    assert all(max(_iou(box, p["bbox"]) for p in panels) > 0.92 for box in expected)


def test_sparse_irregular_components_are_not_rescued_as_rectangles(tmp_path):
    image = Image.new("RGB", (620, 320), "white")
    draw = ImageDraw.Draw(image)
    for x in (30, 330):
        draw.ellipse((x, 40, x + 220, 260), fill="navy")
        draw.line((x + 110, 30, x + 110, 270), fill="navy", width=2)
        draw.line((x - 10, 150, x + 230, 150), fill="navy", width=2)
    path = tmp_path / "irregular.png"
    image.save(path)
    assert segment_panels(path)["panels"] == []


def test_actual_pdf_import_jpeg_decode_preserves_s4_labeled_panel_coverage(tmp_path):
    # Development regression, explicitly NOT a held-out generalization test.
    import json
    from app.ingest import ingest_file
    base = Path(__file__).resolve().parents[1] / "benchmarks/figurecheck"
    pdf = base / "discovery_agent/nc_supplementary.pdf"
    if not pdf.exists():
        pytest.skip("Optional public development PDF fixture is unavailable")
    images, _ = ingest_file(pdf, pdf.name, tmp_path / "pdf_import", max_images=100)
    imported = next(i for i in images if i["page"] == 6 and i["representation"] == "embedded" and i["width"] == 1154)
    panels = segment_panels(imported["path"])["panels"]
    manifest = json.loads((base / "cases_nc_s4.json").read_text(encoding="utf-8"))
    for event in manifest["events"]:
        for side in ("a", "b"):
            x0, y0, x1, y1 = event["panel_" + side]["xyxy"]
            assert max(_iou([x0, y0, x1 - x0, y1 - y0], p["bbox"]) for p in panels) > 0.90


@pytest.mark.parametrize("limit", [0, -1, True, 2.5])
def test_invalid_limits_are_rejected(tmp_path, limit):
    with pytest.raises(ValueError):
        segment_panels(tmp_path / "does_not_exist.png", max_panels=limit)
