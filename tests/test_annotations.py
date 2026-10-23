"""Single-view behavior controls, used for development (not scientific holdout)."""
import inspect
import json

import cv2
import numpy as np
import pytest

from app.annotations import ANNOTATION_POLICY, ANNOTATION_VERSION, analyze_annotations


def field(seed=21, shape=(280, 360)):
    rng = np.random.default_rng(seed)
    signal = cv2.GaussianBlur(rng.normal(0, 1, shape).astype(np.float32), (0, 0), .8)
    signal = np.clip(110 + signal*65, 0, 255).astype(np.uint8)
    return cv2.cvtColor(signal, cv2.COLOR_GRAY2RGB)


def dashed(image, color=(30, 160, 245), offset=100, slant=0):
    truth = np.zeros(image.shape[:2], np.uint8)
    h, _ = truth.shape
    for y in range(5, h-12, 32):
        start = (round(offset+slant*y), y)
        end = (round(offset+slant*(y+17)), y+17)
        cv2.line(image, start, end, color, 4, cv2.LINE_AA)
        cv2.line(truth, start, end, 1, 4, cv2.LINE_8)
    return truth


def blot(seed=2, label="Sample Alpha"):
    rng = np.random.default_rng(seed)
    h, w = 220, 540
    image = np.clip(238+rng.normal(0, 2, (h, w)), 0, 255).astype(np.uint8)
    for row in range(3):
        for col in range(5):
            cv2.ellipse(image, (180+col*73, 40+row*65), (int(rng.integers(17, 26)), 4),
                        0, 0, 360, int(rng.integers(50, 160)), -1)
    image = cv2.GaussianBlur(image, (0, 0), 1.1)
    rgb = cv2.cvtColor(image, cv2.COLOR_GRAY2RGB)
    for row in range(3):
        cv2.rectangle(rgb, (0, row*65), (155, row*65+60), (255, 255, 255), -1)
        cv2.putText(rgb, label, (2, row*65+35), cv2.FONT_HERSHEY_SIMPLEX, .55, (0, 0, 0), 1, cv2.LINE_AA)
    return rgb


def test_contract_is_single_rgb_view_and_json_safe():
    assert list(inspect.signature(analyze_annotations).parameters) == ["rgb"]
    rgb = field()
    original = rgb.copy()
    rgb.flags.writeable = False
    output = analyze_annotations(rgb)
    assert np.array_equal(rgb, original)
    for mask in (output.annotation_mask, output.uncertain_mask, output.data_mask):
        assert mask.dtype == np.uint8 and mask.shape == rgb.shape[:2]
        assert set(np.unique(mask)) <= {0, 1}
    assert np.all(output.data_mask[output.uncertain_mask > 0] == 1)
    assert not np.any(output.annotation_mask & output.uncertain_mask)
    assert output.labels.shape == rgb.shape[:2]
    assert output.metadata["version"] == ANNOTATION_VERSION
    assert output.metadata["coordinate_system"] == "analysis_view_xy_pixels"
    json.dumps(output.metadata, allow_nan=False)


@pytest.mark.parametrize("bad", [np.zeros((0, 10, 3), np.uint8), np.zeros((10, 10), np.uint8),
                                  np.zeros((10, 10, 4), np.uint8), np.zeros((10, 10, 3), np.float32), None])
def test_invalid_color_dtype_or_empty_input_has_explicit_error(bad):
    with pytest.raises(ValueError, match="RGB"):
        analyze_annotations(bad)


@pytest.mark.parametrize("shape", [(1, 1), (8, 17), (32, 32), (230, 400)])
def test_small_and_blank_images_are_not_fabricated_annotations(shape):
    rgb = np.full((*shape, 3), 255, np.uint8)
    output = analyze_annotations(rgb)
    assert output.annotation_mask.sum() == 0
    assert output.data_mask.sum() == shape[0]*shape[1]


@pytest.mark.parametrize("color,slant,scale", [((30, 160, 245), 0, 1), ((250, 240, 20), -.07, 1),
                                              ((240, 30, 60), .10, .65), ((20, 190, 180), -.10, 1.4)])
def test_dashed_guide_geometry_is_detected_but_retained_without_semantic_context(color, slant, scale):
    rgb = field()
    truth = dashed(rgb, color, 100 if slant >= 0 else 240, slant)
    if scale != 1:
        rgb = cv2.resize(rgb, None, fx=scale, fy=scale, interpolation=cv2.INTER_LINEAR)
        truth = cv2.resize(truth, (rgb.shape[1], rgb.shape[0]), interpolation=cv2.INTER_NEAREST)
    output = analyze_annotations(rgb)
    # This raster can equally represent real interrupted signal. The previous
    # test's >75% exclusion assertion encoded the known false-exclusion bug.
    assert output.annotation_mask[truth > 0].mean() == 0
    assert output.data_mask[truth > 0].mean() == 1
    assert output.uncertain_mask[truth > 0].mean() > .75
    assert output.metadata["areas"]["annotation_fraction"] < .15
    assert not (output.labels == 3).any()
    assert output.metadata["guide_candidates"]
    assert all(row["decision"] == "uncertain_retained" and row["excluded_pixels"] == 0
               for row in output.metadata["guide_candidates"])


def test_same_text_different_bands_and_same_bands_modified_text():
    first, second = blot(2), blot(89)
    changed = blot(2, "Control Delta")
    a, b, c = [analyze_annotations(x) for x in (first, second, changed)]
    # Independently identify identical templates, without seeing pair differences.
    assert np.array_equal(a.annotation_mask[:, :155], b.annotation_mask[:, :155])
    assert (a.labels[:, :155] == 1).sum() > 700
    for output in (a, b, c):
        assert output.data_mask[:, 160:].mean() > .995
    assert np.array_equal(first[:, 160:], changed[:, 160:])
    assert not np.array_equal(first[:, 160:], second[:, 160:])


def test_fluorescent_points_fibers_and_dashed_signals_are_retained():
    rgb = np.zeros((280, 360, 3), np.uint8)
    rng = np.random.default_rng(51)
    for _ in range(45):
        x, y = rng.integers(10, 270, 2)
        cv2.circle(rgb, (int(x), int(y)), 4, (0, 220, 30), -1, cv2.LINE_AA)
    cv2.line(rgb, (20, 10), (190, 265), (245, 30, 30), 3, cv2.LINE_AA)
    dashed(rgb, (20, 210, 30), 300)
    output = analyze_annotations(rgb)
    signal = rgb.max(axis=2) > 30
    assert output.data_mask[signal].mean() == 1
    assert output.uncertain_mask[signal].mean() > .80


def test_continuous_colored_fiber_on_gray_texture_is_not_a_guide_by_color_and_shape_alone():
    rgb = field()
    cv2.line(rgb, (100, 3), (117, 275), (20, 210, 30), 4, cv2.LINE_AA)
    output = analyze_annotations(rgb)
    assert output.annotation_mask.sum() == 0
    assert output.uncertain_mask.sum() > 500


def test_known_adverse_interrupted_true_signal_is_not_excluded_and_ambiguity_is_explicit():
    rng = np.random.default_rng(192718)
    noise = cv2.GaussianBlur(rng.normal(0, 1, (280, 360)).astype(np.float32), (0, 0), .8)
    rgb = np.repeat(np.clip(115 + noise*70, 0, 255).astype(np.uint8)[:, :, None], 3, 2)
    truth = np.zeros(rgb.shape[:2], np.uint8)
    for y in range(3, 260, 32):
        cv2.line(rgb, (175, y), (175, y+17), (170, 40, 140), 4, cv2.LINE_AA)
        cv2.line(truth, (175, y), (175, y+17), 1, 4)
    output = analyze_annotations(rgb)
    assert output.annotation_mask[truth > 0].mean() == 0
    assert output.data_mask[truth > 0].mean() == 1
    assert output.uncertain_mask[truth > 0].mean() == 1
    assert output.metadata["guide_candidates"]
    assert all(item["semantic_basis"] == "raster_geometry_only_no_independent_annotation_context"
               for item in output.metadata["guide_candidates"])
    # The public API cannot receive a semantic label and therefore must not
    # give a different answer for an identical raster called a drawn guide.
    drawn_guide_same_pixels = analyze_annotations(rgb.copy())
    assert np.array_equal(drawn_guide_same_pixels.data_mask, output.data_mask)


def test_uncertain_colored_signal_wins_over_an_overlapping_text_row_bbox():
    rgb = blot()
    before = analyze_annotations(rgb)
    row = before.metadata["text_rows"][0]["bbox_xywh"]
    x, y, w, h = row
    # Place chromatic scientific signal in the padded row. Single-view text
    # context must not silently override the explicit scientific uncertainty.
    cv2.line(rgb, (x+3, y+1), (x+w-4, y+1), (220, 30, 150), 1)
    output = analyze_annotations(rgb)
    signal = (rgb[:, :, 0].astype(int)-rgb[:, :, 1].astype(int)) > 100
    assert signal.any()
    assert output.uncertain_mask[signal].mean() == 1
    assert output.data_mask[signal].mean() == 1
    # Text masking is still operational in the other rows.
    assert (output.labels == 1).sum() > 500


def test_soft_stain_and_repeated_round_scientific_objects_are_preserved():
    rgb = np.full((220, 380, 3), 245, np.uint8)
    for x in range(25, 355, 30):
        cv2.circle(rgb, (x, 50), 5, (20, 20, 20), -1, cv2.LINE_AA)
    pts = np.array([(x, int(130+25*np.sin(x/40))) for x in range(10, 370)], np.int32)
    cv2.polylines(rgb, [pts], False, (170, 40, 90), 3, cv2.LINE_AA)
    rgb = cv2.GaussianBlur(rgb, (0, 0), 1.1)
    output = analyze_annotations(rgb)
    assert output.annotation_mask.mean() < .005


def test_plot_axes_may_be_annotations_but_real_curve_is_uncertain_and_retained():
    rgb = np.full((300, 440, 3), 255, np.uint8)
    cv2.line(rgb, (60, 30), (60, 250), (0, 0, 0), 2)
    cv2.line(rgb, (60, 250), (420, 250), (0, 0, 0), 2)
    cv2.putText(rgb, "Measured response", (125, 22), cv2.FONT_HERSHEY_SIMPLEX, .5, (0, 0, 0), 1, cv2.LINE_AA)
    cv2.putText(rgb, "Treatment groups", (135, 285), cv2.FONT_HERSHEY_SIMPLEX, .5, (0, 0, 0), 1, cv2.LINE_AA)
    truth = np.zeros(rgb.shape[:2], np.uint8)
    points = np.array([(x, int(180+30*np.sin(x/45))) for x in range(80, 410)], np.int32)
    cv2.polylines(rgb, [points], False, (20, 70, 220), 2, cv2.LINE_AA)
    cv2.polylines(truth, [points], False, 1, 2)
    output = analyze_annotations(rgb)
    assert (output.labels == 4).sum() > 500
    assert output.data_mask[truth > 0].mean() > .99
    assert output.uncertain_mask[truth > 0].mean() > .90


def test_repeat_calls_view_strides_and_border_masks_remain_deterministic_and_bounded():
    rgb = field()
    dashed(rgb, offset=2)
    first = analyze_annotations(rgb)
    analyze_annotations(blot())
    second = analyze_annotations(rgb[:, ::-1][:, ::-1])
    assert np.array_equal(first.annotation_mask, second.annotation_mask)
    assert first.metadata == second.metadata
    assert first.annotation_mask.mean() <= ANNOTATION_POLICY["max_annotation_fraction"]
    assert first.annotation_mask.shape == rgb.shape[:2]


def test_dense_text_exclusion_limit_downgrades_to_retained_uncertainty():
    rgb = np.full((180, 360, 3), 255, np.uint8)
    for y in range(17, 180, 20):
        cv2.putText(rgb, "Dense Text Alpha Beta", (1, y), cv2.FONT_HERSHEY_SIMPLEX,
                    .6, (0, 0, 0), 1, cv2.LINE_AA)
    output = analyze_annotations(rgb)
    assert output.annotation_mask.sum() == 0
    assert output.uncertain_mask.mean() > ANNOTATION_POLICY["max_annotation_fraction"]
    assert output.data_mask.mean() == 1
    assert any("exceeds policy bound" in warning for warning in output.metadata["warnings"])


@pytest.mark.parametrize("color,scale", [((230, 30, 60), 1), ((20, 180, 210), .6), ((230, 220, 20), 1.4)])
def test_unsupported_short_arrows_report_uncertainty_instead_of_force_exclusion(color, scale):
    rgb = field()
    cv2.arrowedLine(rgb, (80, 40), (175, 160), color, 3, cv2.LINE_AA, tipLength=.18)
    if scale != 1:
        rgb = cv2.resize(rgb, None, fx=scale, fy=scale)
    output = analyze_annotations(rgb)
    assert output.annotation_mask.sum() == 0
    assert output.uncertain_mask.sum() > 100
    assert output.data_mask.mean() == 1


def test_isolated_gray_scale_bar_or_true_thin_band_is_retained_without_semantic_context():
    rgb = field()
    cv2.rectangle(rgb, (240, 250), (320, 254), (255, 255, 255), -1)
    output = analyze_annotations(rgb)
    assert output.data_mask[250:255, 240:321].mean() == 1
    assert output.metadata["methods"]["scale_bar"].startswith("unclassified")


@pytest.mark.parametrize("sigma,blur,layout", [(12,0,"three"),(20,.5,"three"),(12,0,"corner")])
def test_noisy_true_thin_bands_not_confused_with_rulers_by_analyzer(sigma,blur,layout):
    rng=np.random.default_rng(73821)
    gray=np.clip(233+rng.normal(0,sigma,(240,400)),0,255).astype(np.uint8)
    truth=np.zeros_like(gray)
    boxes=[(70,y,160,y+5) for y in (45,105,170)] if layout=="three" else [(280,195,370,200)]
    for x0,y0,x1,y1 in boxes:
        cv2.rectangle(gray,(x0,y0),(x1,y1),8,-1)
        cv2.rectangle(truth,(x0,y0),(x1,y1),1,-1)
    if blur:
        gray=cv2.GaussianBlur(gray,(0,0),blur)
    output=analyze_annotations(np.repeat(gray[:,:,None],3,2))
    assert output.data_mask[truth>0].mean()==1
