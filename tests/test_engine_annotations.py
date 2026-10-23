"""Mechanism/development controls, not an independent accuracy validation."""
import copy
import hashlib
import json
from types import SimpleNamespace

import cv2
import numpy as np
import pytest
from PIL import Image

from app.engine import (ANNOTATION_VERIFICATION_POLICY, Detector, ENGINE_VERSION,
                        FIXED_GRID_VERIFICATION_POLICY, _fixed_grid_evidence,
                        _POLICIES, _analysis_for, _retained_mask, verification_mask_layers)


def texture(seed=123, shape=(300, 420)):
    rng = np.random.default_rng(seed)
    signal = cv2.GaussianBlur(rng.normal(128, 70, shape).astype(np.float32), (0, 0), .8)
    for _ in range(90):
        cv2.circle(signal, tuple(map(int, (rng.integers(10, shape[1]-10), rng.integers(10, shape[0]-10)))),
                   int(rng.integers(2, 11)), float(rng.integers(20, 230)), 2)
    return np.repeat(np.clip(signal, 0, 255).astype(np.uint8)[:, :, None], 3, axis=2)


@pytest.fixture
def detector():
    cv2.setNumThreads(2)
    return Detector()


def prepare(detector, tmp_path, rgb, name):
    path = tmp_path / name
    Image.fromarray(rgb).save(path)
    return detector.prepare(path)


def supported(detail):
    return [f for f in detail["findings"] if f["evidence_class"] == "data_supported"]


def test_policy_is_additive_not_reduced():
    assert ENGINE_VERSION == "3.2.0-blot-low-texture"
    assert _POLICIES["strict"] == dict(ratio=.68, min_inliers=14, inlier_ratio=.58,
        corr=.92, gradient=.76, tile_fraction=.70, global_corr=.985)
    assert ANNOTATION_VERIFICATION_POLICY["max_excluded_overlap_fraction"] == .30
    json.dumps(ANNOTATION_VERIFICATION_POLICY, allow_nan=False)


@pytest.mark.parametrize("variant", ["exact", "mirror", "crop"])
def test_unannotated_data_copy_all_paths_have_evidence(detector, tmp_path, variant):
    a = texture()
    b = a if variant == "exact" else np.fliplr(a).copy() if variant == "mirror" else a[50:260, 70:350]
    aa, bb = prepare(detector, tmp_path, a, "a.png"), prepare(detector, tmp_path, b, "b.png")
    result = detector.compare_detailed(aa, bb)
    assert supported(result)
    for finding in result["findings"]:
        evidence = finding["metrics"]["annotation_verification"]
        assert evidence["raw_evidence"] and evidence["data_evidence"]
        for side in ("a", "b"):
            coverage = evidence["coverage_" + side]
            assert coverage["overlap_pixels"] == coverage["effective_pixels"] + coverage["excluded_pixels"]
            assert coverage["full_image_pixels"] >= coverage["overlap_pixels"]
        assert evidence["mask_provenance"].endswith("no_residual_selection")
    json.dumps(result, ensure_ascii=False, allow_nan=False)


def test_same_text_different_texture_is_not_data_alarm_but_is_auditable(detector, tmp_path):
    items = []
    for seed in (151, 152):
        rgb = texture(seed)
        rgb[:110] = 255
        cv2.putText(rgb, "Treatment / Control", (15, 38), cv2.FONT_HERSHEY_SIMPLEX, .75, (0, 0, 0), 2)
        cv2.putText(rgb, "Expression level", (15, 83), cv2.FONT_HERSHEY_SIMPLEX, .75, (0, 0, 0), 2)
        items.append(prepare(detector, tmp_path, rgb, f"{seed}.png"))
    detail = detector.compare_detailed(*items)
    assert not supported(detail)
    assert detail["findings"]
    assert all(f["evidence_class"] in {"annotation_only", "inconclusive"} for f in detail["findings"])
    assert detail["diagnostics"]["annotation_verification_rejections"]


def test_same_data_different_text_and_guides_retains_original_pixels(detector, tmp_path):
    original = texture(170)
    variants = []
    for i, message in enumerate(("Control", "Treatment")):
        rgb = original.copy()
        rgb[:38] = 255
        cv2.putText(rgb, message, (12, 28), cv2.FONT_HERSHEY_SIMPLEX, .7, (0, 0, 0), 2)
        cv2.line(rgb, (100 + i * 60, 40), (150 + i * 60, 290), (255, 0, 0), 4)
        variants.append(prepare(detector, tmp_path, rgb, f"guide{i}.png"))
    hashes = [hashlib.sha256(p.rgb.tobytes()).hexdigest() for p in variants]
    analyses = [copy.deepcopy(_analysis_for(p).annotation_mask) for p in variants]
    detail = detector.compare_detailed(*variants)
    assert supported(detail)
    assert hashes == [hashlib.sha256(p.rgb.tobytes()).hexdigest() for p in variants]
    assert all(np.array_equal(mask, _analysis_for(p).annotation_mask) for mask, p in zip(analyses, variants))


def test_independent_masks_do_not_depend_on_pair_or_order(detector, tmp_path):
    a = prepare(detector, tmp_path, texture(200), "a.png")
    b = prepare(detector, tmp_path, texture(201), "b.png")
    original = _analysis_for(a).annotation_mask.copy()
    detector.compare_detailed(a, b)
    detector.compare_detailed(b, a)
    assert np.array_equal(original, _analysis_for(a).annotation_mask)
    assert not supported(detector.compare_detailed(a, b))


def fake_analysis(image, excluded, uncertain=None):
    zero = np.zeros(image.gray.shape, np.uint8)
    return SimpleNamespace(annotation_mask=excluded, uncertain_mask=zero if uncertain is None else uncertain,
        data_mask=1-excluded, labels=excluded.copy(), metadata={"test_fixture": True})


def test_extreme_exclusion_is_inconclusive_not_high_score(detector, tmp_path):
    a = prepare(detector, tmp_path, texture(203), "a.png")
    a.annotations = fake_analysis(a, np.ones(a.gray.shape, np.uint8))
    result = detector.compare_detailed(a, a)["findings"][0]
    assert result["kind"] == "exact_pixels"
    assert result["evidence_class"] == "inconclusive"
    evidence = result["metrics"]["annotation_verification"]
    assert evidence["coverage_a"]["effective_pixels"] == 0
    assert "excessive_annotation_exclusion_a" in evidence["reasons"]


def test_uncertain_pixels_are_never_silently_excluded(detector, tmp_path):
    a = prepare(detector, tmp_path, texture(204), "a.png")
    ones = np.ones(a.gray.shape, np.uint8)
    a.annotations = fake_analysis(a, ones, uncertain=ones)
    finding = detector.compare_detailed(a, a)["findings"][0]
    assert finding["evidence_class"] == "data_supported"
    assert finding["metrics"]["annotation_verification"]["coverage_a"]["excluded_pixels"] == 0


def test_missing_analysis_recomputes_but_bad_coordinate_mask_fails(detector, tmp_path):
    a = prepare(detector, tmp_path, texture(205), "a.png")
    a.annotations = None
    assert supported(detector.compare_detailed(a, a))
    a.annotations = fake_analysis(a, np.zeros((10, 10), np.uint8))
    with pytest.raises(ValueError, match="analysis-sized"):
        detector.compare_detailed(a, a)


def test_low_information_exact_view_is_not_data_reuse(detector, tmp_path):
    a = prepare(detector, tmp_path, np.full((100, 130, 3), 230, np.uint8), "blank.png")
    finding = detector.compare_detailed(a, a)["findings"][0]
    assert finding["metrics"]["exact_pixels"]
    assert finding["evidence_class"] == "inconclusive"
    assert "low_information_exact_view" in finding["metrics"]["annotation_verification"]["reasons"]


def test_color_channel_view_reuses_single_rgb_analysis(detector, tmp_path):
    rng = np.random.default_rng(210)
    def fluorescence():
        field = np.zeros((260, 340), np.uint8)
        for _ in range(130):
            cv2.circle(field, (int(rng.integers(8, 332)), int(rng.integers(8, 252))), 4, int(rng.integers(60, 230)), -1)
        return cv2.GaussianBlur(field, (0, 0), .7)
    green = fluorescence()
    a = prepare(detector, tmp_path, np.stack((np.zeros_like(green), green, np.zeros_like(green)), 2), "single.png")
    b = prepare(detector, tmp_path, np.stack((fluorescence(), green, fluorescence()), 2), "merge.png")
    result = detector.compare_detailed(a, b)
    assert supported(result)
    finding = supported(result)[0]
    assert finding["channel_a"] == finding["channel_b"] == "G"
    assert finding["metrics"]["annotation_verification"]["data_evidence"]["pixel_correlation"] > .99
    assert detector._channel_view(a, "G").annotations is a.annotations


def test_realistic_soft_blot_copy_and_crisp_thin_bands_are_not_masked(detector, tmp_path):
    # Smooth, finite-width biological-like bands and even crisp rectangular
    # bands on a flat substrate must remain; slenderness is not an annotation.
    yy,xx = np.mgrid[:320,:440]
    signal = np.full((320,440),233.,np.float32)
    rng = np.random.default_rng(215)
    signal += cv2.GaussianBlur(rng.normal(0,3,signal.shape).astype(np.float32),(0,0),.6)
    for y in (65,150,245):
        for x in (60,145,230,315,400):
            signal -= float(rng.uniform(65,150))*np.exp(-.5*(((xx-x)/rng.uniform(12,22))**2+((yy-y)/rng.uniform(3,7))**2))
    rgb = np.repeat(np.clip(signal,0,255).astype(np.uint8)[:,:,None],3,2)
    a = prepare(detector,tmp_path,rgb,"blot.png")
    b = prepare(detector,tmp_path,rgb[25:285,25:420],"blot_copy.png")
    assert supported(detector.compare_detailed(a,b))
    clean = np.full((240,400,3),245,np.uint8)
    for y in (45,105,170):
        cv2.rectangle(clean,(70,y),(160,y+5),(8,8,8),-1)
        cv2.rectangle(clean,(240,y),(330,y+5),(8,8,8),-1)
    assert verification_mask_layers(clean)["verification_excluded"].sum() == 0


def test_public_verification_masks_reproduce_prepared_and_roi_geometry(detector,tmp_path):
    a = prepare(detector,tmp_path,texture(219),"a.png")
    b = prepare(detector,tmp_path,np.fliplr(a.rgb).copy(),"b.png")
    assert np.array_equal(_retained_mask(a),verification_mask_layers(a.rgb,a.annotations)["verification_data"])
    finding = supported(detector.compare_detailed(a,b))[0]
    evidence = finding["metrics"]["annotation_verification"]
    for side,image in (("a",a),("b",b)):
        roi = np.zeros(image.gray.shape,np.uint8)
        cv2.fillPoly(roi,[np.asarray(c,np.int32) for c in evidence["roi_"+side]["contours"]],1)
        assert roi.sum() >= evidence["coverage_"+side]["overlap_pixels"]
    assert evidence["verification_mask_version"]


@pytest.mark.parametrize("noise,blur", [(12,0),(20,.5)])
def test_noisy_experimental_thin_bands_are_not_legacy_rulers(noise,blur):
    rng = np.random.default_rng(73821)
    gray = np.clip(233+rng.normal(0,noise,(240,400)),0,255).astype(np.uint8)
    if blur:
        gray=cv2.GaussianBlur(gray,(0,0),blur)
    for y in (45,105,170):
        cv2.rectangle(gray,(70,y),(160,y+5),8,-1)
    rgb=np.repeat(gray[:,:,None],3,2)
    assert verification_mask_layers(rgb)["verification_excluded"].sum() == 0


def test_single_corner_band_is_a_retained_proposal_not_a_scale_bar(detector,tmp_path):
    rng=np.random.default_rng(73821)
    gray=np.clip(233+rng.normal(0,12,(240,400)),0,255).astype(np.uint8)
    cv2.rectangle(gray,(280,195),(370,200),8,-1)
    rgb=np.repeat(gray[:,:,None],3,2)
    layers=verification_mask_layers(rgb)
    assert layers["legacy_overlay"].sum() > 0  # expose the failed v2 hypothesis
    assert layers["verification_excluded"].sum() == 0
    a=prepare(detector,tmp_path,rgb,"corner_band.png")
    from app.engine import _mask_for
    assert _mask_for(a).all()  # not silently removed in candidate generation


def test_nondata_channel_explanation_does_not_claim_verification():
    finding={"evidence_class":"inconclusive","metrics":{},"method":"sift","explanation":"raw"}
    Detector._describe_channel(finding,"G","G")
    assert "不是数据复用报警" in finding["explanation"]
    assert "具有经过几何与像素验证" not in finding["explanation"]


def test_fixed_grid_partial_evidence_reports_all_disagreement_without_excluding_it():
    a = cv2.cvtColor(texture(901, (480,640)), cv2.COLOR_RGB2GRAY)
    b = cv2.cvtColor(texture(902, (480,640)), cv2.COLOR_RGB2GRAY)
    mask = np.ones(a.shape,np.uint8)
    # A small but spatially distributed set of real corresponding patches.
    # This is deliberately partial reuse, not a claim that the rest agrees.
    for row,col in ((1,1),(1,5),(1,12),(5,1),(5,12),(10,1),(10,5),(10,12),(14,14)):
        b[row*30:(row+1)*30,col*40:(col+1)*40] = a[row*30:(row+1)*30,col*40:(col+1)*40]
    before = mask.copy()
    result = _fixed_grid_evidence(a,b,mask,.92,.76,.70)
    assert result["passed"]
    assert result["supporting_cells"] >= 8
    assert result["counterevidence_cells"] > result["supporting_cells"]
    assert len(result["cells"]) == 256
    assert sum(c["effective_pixels"] for c in result["cells"]) == mask.sum()
    assert 0 < result["supporting_pixel_fraction_effective_area"] < .1
    assert result["all_effective_pixels_retained"]
    assert np.array_equal(mask,before)
    assert all(c["evidence"]["pixel_correlation"] >= .92 and c["evidence"]["gradient_agreement"] >= .76
               for c in result["cells"] if c["supporting"])


@pytest.mark.parametrize("shared_cells", [0,1,4])
def test_fixed_grid_unrelated_or_too_few_shared_cells_does_not_pass(shared_cells):
    a = cv2.cvtColor(texture(910,(480,640)),cv2.COLOR_RGB2GRAY)
    b = cv2.cvtColor(texture(911,(480,640)),cv2.COLOR_RGB2GRAY)
    for row,col in ((1,1),(1,12),(12,1),(12,12))[:shared_cells]:
        b[row*30:(row+1)*30,col*40:(col+1)*40] = a[row*30:(row+1)*30,col*40:(col+1)*40]
    evidence = _fixed_grid_evidence(a,b,np.ones(a.shape,np.uint8),.92,.76,.70)
    assert not evidence["passed"]
    assert "insufficient_supporting_cells" in evidence["reasons"]


def test_new_findings_separate_candidate_morphology_from_experimental_identity(detector,tmp_path):
    a = prepare(detector,tmp_path,texture(920),"a.png")
    finding = supported(detector.compare_detailed(a,a))[0]
    assert finding["visual_evidence"]["raster_correspondence"]
    assert finding["content_role"] == "experimental_image_candidate"
    assert finding["semantic_basis"]["status"] == "image_heuristic"
    assert finding["semantic_basis"]["source"] is None
    assert not finding["semantic_basis"]["experimental_identity_verified"]
    assert not finding["review_scope"]["established_experimental_reuse"]
    assert not finding["review_scope"]["misconduct_assessed"]


def test_fixed_policy_is_serializable_and_unchanged_original_thresholds():
    assert FIXED_GRID_VERIFICATION_POLICY["cell_correlation"] == "unchanged_sensitivity_correlation_threshold"
    assert FIXED_GRID_VERIFICATION_POLICY["cell_gradient"] == "unchanged_sensitivity_gradient_threshold"
    json.dumps(FIXED_GRID_VERIFICATION_POLICY,allow_nan=False)


def test_fixed_grid_sparse_shared_texture_on_flat_background_is_not_dense_data_support():
    a=np.full((480,640),230,np.uint8)
    b=a.copy()
    texture_gray=cv2.cvtColor(texture(930,(480,640)),cv2.COLOR_RGB2GRAY)
    for row,col in ((1,1),(1,5),(1,12),(5,1),(5,12),(10,1),(10,5),(10,12)):
        a[row*30:(row+1)*30,col*40:(col+1)*40]=texture_gray[row*30:(row+1)*30,col*40:(col+1)*40]
        b[row*30:(row+1)*30,col*40:(col+1)*40]=a[row*30:(row+1)*30,col*40:(col+1)*40]
    evidence=_fixed_grid_evidence(a,b,np.ones(a.shape,np.uint8),.92,.76,.70)
    assert not evidence["passed"]
    assert evidence["informative_pixel_fraction_effective_area"] < .5
    assert "insufficient_dense_informative_area" in evidence["reasons"]
    assert len(evidence["cells"]) == 256


@pytest.mark.parametrize("reuse_lane",[False,True])
def test_independent_demo_blots_with_common_printing_do_not_pass_partial_grid(detector,tmp_path,reuse_lane):
    from scripts.create_demo import blot
    original=np.asarray(blot(20261023)).copy()
    if reuse_lane:
        original[65:300,440:504]=original[65:300,120:184]
    independent=np.asarray(blot(20261024))
    aa=prepare(detector,tmp_path,original,"original.png")
    bb=prepare(detector,tmp_path,independent,"independent.png")
    result=detector.compare_detailed(aa,bb)
    assert not supported(result)
    assert result["findings"]  # keep the candidate/abstention; do not erase it
    assert any("insufficient_dense_informative_area" in f["metrics"]["annotation_verification"]["fixed_grid_evidence"]["reasons"]
               for f in result["findings"] if f["metrics"]["annotation_verification"]["fixed_grid_evidence"])
