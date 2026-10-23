"""Synthetic mechanism contracts, not real-source performance validation."""
import json

import cv2
import numpy as np
from PIL import Image

from app.blot_candidates import BLOT_CANDIDATE_POLICY, generate_blot_candidates
from app.engine import (BLOT_TEXTURE_POLICY, Detector, _POLICIES,
                        FIXED_GRID_VERIFICATION_POLICY)


def strip(seed, shape=(128,512)):
    rng = np.random.default_rng(seed)
    y,x = np.indices(shape)
    base = np.full(shape,195.,np.float32)
    for index in range(6):
        center = 45+index*(shape[1]-80)/5
        base -= (95+index*5)*np.exp(-((x-center)/20.)**4-((y-shape[0]*.48)/12.)**2)
    base += 15*np.sin(x/70)+12*np.cos(y/25)
    # Distinct, reproducible native microtexture, including informative background.
    base += rng.normal(0,10,shape).astype(np.float32)
    return np.clip(base,0,255).astype(np.uint8)


def prepared(detector,tmp_path,gray,name):
    path = tmp_path/name
    Image.fromarray(gray).convert("RGB").save(path)
    return detector.prepare(path)


def new_only(tmp_path,a,b):
    detector = Detector()
    detector.blot_candidate_mode = "only"
    aa,bb = prepared(detector,tmp_path,a,"a.png"),prepared(detector,tmp_path,b,"b.png")
    originals = [i.gray.copy() for i in (aa,bb)]
    detail = detector.compare_detailed(aa,bb)
    assert all(np.array_equal(i.gray,g) for i,g in zip((aa,bb),originals))
    json.dumps(detail,allow_nan=False)
    return detail


def test_dense_path_is_active_on_transformed_native_texture(tmp_path):
    a = strip(601)
    b = cv2.warpAffine(a,np.float32([[1,0,2],[0,1,1]]),(a.shape[1],a.shape[0]),borderValue=195)
    b = np.clip(b.astype(np.float32)*1.015+2,0,255).astype(np.uint8)
    result = new_only(tmp_path,a,b)
    supported = [f for f in result["findings"] if f["evidence_class"]=="data_supported"]
    assert supported, result["diagnostics"]
    for f in supported:
        assert f["matches"]==[] and f["metrics"]["independent_keypoints"]==0
        av = f["metrics"]["annotation_verification"]
        assert av["fixed_grid_evidence"]["passed"]
        assert av["fixed_grid_evidence"]["informative_pixel_fraction_effective_area"] >= .5
        assert av["dense_texture_evidence"]["passed"]
        assert len(av["dense_texture_evidence"]["strata"])==2
        assert av["dense_texture_evidence"]["total_effective_pixels_unchanged"]==av["coverage_"+av["verification_grid"]]["effective_pixels"]


def test_same_layout_independent_native_texture_is_not_confirmed(tmp_path):
    result = new_only(tmp_path,strip(602),strip(603))
    assert not any(f["evidence_class"]=="data_supported" for f in result["findings"])
    assert result["diagnostics"]["four_stage"]["final_decision"]["semantic_status"]=="source_not_verified_from_pixels"


def test_shared_frame_without_data_is_not_confirmed(tmp_path):
    a = np.full((96,420),245,np.uint8)
    cv2.rectangle(a,(0,0),(419,95),0,3)
    b = np.where(a>0,250,0).astype(np.uint8)
    result = new_only(tmp_path,a,b)
    assert not any(f["evidence_class"]=="data_supported" for f in result["findings"])


def test_small_fixed_grid_not_exempted_by_whole_area_pass(tmp_path):
    a = strip(604,(32,200))
    b = np.clip(a.astype(np.int16)+2,0,255).astype(np.uint8)
    result = new_only(tmp_path,a,b)
    assert not any(f["evidence_class"]=="data_supported" for f in result["findings"])
    verified = [f["metrics"]["annotation_verification"] for f in result["findings"]]
    assert verified
    assert any(av["global_photometric_pass"] for av in verified)
    assert all("dense_path_fixed_grid_requirements_not_met" in av["reasons"] for av in verified)


def test_count_budgets_and_transforms_are_finite():
    a,b = strip(605),strip(606)
    proposals,stage = generate_blot_candidates(a,b)
    assert stage["template_tests"] <=120
    assert len(stage["attempts"]) <=8
    assert len(proposals) <=4
    assert all(np.isfinite(p["matrix"]).all() for p in proposals)
    assert BLOT_CANDIDATE_POLICY["windows_are_fixed_before_comparison"] is True
    assert BLOT_TEXTURE_POLICY["minimum_supporting_strata"]==2
    assert FIXED_GRID_VERIFICATION_POLICY["min_informative_pixel_fraction_effective_area"]==.5
    assert _POLICIES["strict"]==dict(ratio=.68,min_inliers=14,inlier_ratio=.58,corr=.92,
                                   gradient=.76,tile_fraction=.70,global_corr=.985)


def test_four_stage_not_run_on_early_information_exit(tmp_path):
    a = np.full((64,180),125,np.uint8)
    b = np.full((64,180),126,np.uint8)
    result = new_only(tmp_path,a,b)
    stages = result["diagnostics"]["four_stage"]
    assert stages["alignment"]["status"]=="not_run"
    assert stages["data_verification"]["status"]=="not_run"
    assert stages["alignment"]["candidates"][0]["reason"]
    assert stages["data_verification"]["candidates"][0]["reason"]
