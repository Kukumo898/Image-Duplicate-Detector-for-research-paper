"""Bounded, raster-only proposals for feature-poor elongated grayscale views.

This module proposes geometry; it never confirms reuse. No file identities,
labels, annotations, truth regions or pair-dependent exclusion masks are inputs.
"""
from __future__ import annotations

import cv2
import numpy as np


BLOT_CANDIDATE_POLICY = {
    "version": "bounded-dense-strip-v1",
    "route": "both_views_min_aspect_1.5_min_side_32_chromatic_fraction_le_.02_max_sift_features_100",
    "search_long_side": 512,
    "directions": ["a_template_in_b", "b_template_in_a"],
    "orientations": ["identity", "horizontal_mirror", "rotate180", "vertical_mirror"],
    "quarter_turns": False,
    "relative_scales": [.75, .875, 1., 1.125, 1.25],
    "initial_axis_scaling": "whole_view_extent_ratio_with_anisotropy_at_most_1.10",
    "fixed_template_windows_xyxy_fraction": [[0., 0., 1., 1.], [0., 0., .75, 1.], [.25, 0., 1., 1.]],
    "windows_are_fixed_before_comparison": True,
    "source_search_padding_fraction": .15,
    "minimum_real_overlap_template_fraction": .70,
    "minimum_template_side": 16,
    "minimum_template_area_source_fraction": .15,
    "maximum_template_tests": 120,
    "candidate_ncc_floor": .88,
    "peaks_per_template": 1,
    "peak_suppression_radius_template_fraction": .10,
    "maximum_refinement_candidates": 8,
    "maximum_verification_candidates": 4,
    "refinement": "single_global_affine_ecc_on_entire_fixed_template_no_lane_motion",
    "ecc_iterations": 35,
    "ecc_epsilon": .00001,
    "ecc_axis_scale_deviation_max": .05,
    "ecc_off_diagonal_abs_max": .025,
    "ecc_translation_template_fraction_max": .08,
    "deduplication": "corner_median_distance_at_most_3_search_pixels_and_same_template_fraction",
    "truncation": "descending_ncc_then_fixed_iteration_order_before_refinement_and_verification",
    "budget_kind": "deterministic_count_budget_not_wall_clock_timeout",
    "final_verifier": "unchanged_original_raster_post_verifier_plus_mandatory_original_16x16_grid",
    "evidence_dependency": "proposal_refinement_and_verification_share_pixels_not_independent_replications",
}


def _orient(gray, index):
    h, w = gray.shape
    matrix = np.eye(3)
    if index >= 2:
        gray = np.rot90(gray, 2)
        matrix = np.array([[-1., 0., w-1], [0., -1., h-1], [0., 0., 1.]])
    if index % 2:
        gray = np.fliplr(gray)
        matrix = np.array([[-1., 0., w-1], [0., 1., 0.], [0., 0., 1.]]) @ matrix
    return np.ascontiguousarray(gray), matrix


def _corners(box):
    x, y, w, h = box
    return np.float32([[x, y], [x+w-1, y], [x+w-1, y+h-1], [x, y+h-1]])


def _mapped_box(points, shape):
    h, w = shape
    low = np.maximum([0, 0], np.floor(points.min(axis=0))).astype(int)
    high = np.minimum([w, h], np.ceil(points.max(axis=0))+1).astype(int)
    return [int(low[0]), int(low[1]), int(max(0, high[0]-low[0])), int(max(0, high[1]-low[1]))]


def generate_blot_candidates(gray_a, gray_b):
    """Return full fixed-window proposals in analysis-grid a->b coordinates."""
    policy = BLOT_CANDIDATE_POLICY
    stage = {"stage": "blot_dense_candidate", "triggered": True, "status": "completed",
             "search_policy": dict(policy), "template_tests": 0, "candidate_count": 0,
             "truncated": False, "searches": [], "attempts": [], "reason": None}
    proposals = []
    for reverse in (False, True):
        template, source = (gray_b, gray_a) if reverse else (gray_a, gray_b)
        sh, sw = source.shape
        q = min(1., policy["search_long_side"]/max(sh, sw))
        small = cv2.resize(source, (max(1, round(sw*q)), max(1, round(sh*q))), interpolation=cv2.INTER_AREA)
        qx, qy = small.shape[1]/sw, small.shape[0]/sh
        pad_x, pad_y = max(2, round(small.shape[1]*.15)), max(2, round(small.shape[0]*.15))
        padded = cv2.copyMakeBorder(small, pad_y, pad_y, pad_x, pad_x, cv2.BORDER_CONSTANT,
                                   value=float(np.median(small)))
        for orient in range(4):
            oriented, orientation = _orient(template, orient)
            th0, tw0 = oriented.shape
            sx0, sy0 = small.shape[1]/tw0, small.shape[0]/th0
            anisotropy = max(sx0/sy0, sy0/sx0)
            if anisotropy > 1.10:
                stage["searches"].append({"direction": int(reverse), "orientation": orient,
                                          "status": "not_run", "reason": "whole_view_aspect_ratio_outside_bound"})
                continue
            for scale in policy["relative_scales"]:
                for window_id, (l, t, r, b) in enumerate(policy["fixed_template_windows_xyxy_fraction"]):
                    if stage["template_tests"] >= policy["maximum_template_tests"]:
                        stage.update(status="budget_truncated", truncated=True, reason="template_test_budget")
                        break
                    x0, y0, x1, y1 = round(l*tw0), round(t*th0), round(r*tw0), round(b*th0)
                    crop = oriented[y0:y1, x0:x1]
                    tw, th = round((x1-x0)*sx0*scale), round((y1-y0)*sy0*scale)
                    record = {"direction": int(reverse), "orientation": orient, "scale": scale,
                              "window_id": window_id, "template_box_oriented_xywh": [x0, y0, x1-x0, y1-y0],
                              "search_shape": list(small.shape[::-1]), "padding_xy": [pad_x, pad_y]}
                    stage["template_tests"] += 1
                    stage["searches"].append(record)
                    if (min(tw, th) < policy["minimum_template_side"] or tw > padded.shape[1]
                            or th > padded.shape[0] or tw*th < small.size*.15):
                        record.update(status="rejected", reason="template_size_outside_budget")
                        continue
                    query = cv2.resize(crop, (tw, th), interpolation=cv2.INTER_AREA if scale*q <= 1 else cv2.INTER_LINEAR)
                    if float(query.std()) < 5:
                        record.update(status="rejected", reason="uninformative_template")
                        continue
                    response = cv2.matchTemplate(padded, query, cv2.TM_CCOEFF_NORMED)
                    _, peak, _, location = cv2.minMaxLoc(response)
                    x, y = location
                    second = response.copy()
                    dx, dy = max(2, round(tw*.10)), max(2, round(th*.10))
                    second[max(0, y-dy):y+dy+1, max(0, x-dx):x+dx+1] = -1
                    peak2 = float(second.max())
                    real_area = max(0, min(x+tw, pad_x+small.shape[1])-max(x, pad_x))*max(0, min(y+th, pad_y+small.shape[0])-max(y, pad_y))
                    record.update(peak_ncc=float(peak), second_peak_ncc=peak2, peak_margin=float(peak-peak2),
                                  peak_location_xy=[x-pad_x, y-pad_y], real_overlap_fraction=real_area/(tw*th))
                    if peak < policy["candidate_ncc_floor"] or real_area/(tw*th) < .70:
                        record.update(status="rejected", reason="ncc_floor_or_real_overlap")
                        continue
                    resize = np.diag([tw/(x1-x0), th/(y1-y0), 1.])
                    origin = np.array([[1., 0., -x0], [0., 1., -y0], [0., 0., 1.]])
                    base = resize @ origin @ orientation
                    record.update(status="candidate", reason=None)
                    proposals.append({"record": record, "reverse": reverse, "query": query,
                                      "patch": padded[y:y+th, x:x+tw], "base": base,
                                      "position": [x-pad_x, y-pad_y], "qxy": [qx, qy],
                                      "box_template": _mapped_box(cv2.perspectiveTransform(_corners([x0,y0,x1-x0,y1-y0]).reshape(-1,1,2), np.linalg.inv(orientation)).reshape(-1,2), template.shape),
                                      "source_shape": source.shape, "index": len(stage["searches"])-1})
    proposals.sort(key=lambda p: (-p["record"]["peak_ncc"], p["index"]))
    stage["raw_candidate_count"] = len(proposals)
    stage["refinement_truncated"] = len(proposals) > policy["maximum_refinement_candidates"]
    stage["truncated"] |= stage["refinement_truncated"]
    output = []
    for proposal in proposals[:policy["maximum_refinement_candidates"]]:
        rec = proposal["record"]
        attempt = {"candidate_id": len(stage["attempts"]), "search_index": proposal["index"],
                   "proposal_evidence": dict(rec), "evidence_dependency": policy["evidence_dependency"]}
        stage["attempts"].append(attempt)
        query, patch = proposal["query"], proposal["patch"]
        affine = np.eye(2, 3, dtype=np.float32)
        try:
            ecc, refined = cv2.findTransformECC(patch, query, affine.copy(), cv2.MOTION_AFFINE,
                         (cv2.TERM_CRITERIA_COUNT | cv2.TERM_CRITERIA_EPS, policy["ecc_iterations"], policy["ecc_epsilon"]),
                         None, 3)
            good = (np.isfinite(refined).all() and max(abs(refined[0,0]-1), abs(refined[1,1]-1)) <= .05
                    and max(abs(refined[0,1]), abs(refined[1,0])) <= .025
                    and abs(refined[0,2]) <= max(2,query.shape[1]*.08)
                    and abs(refined[1,2]) <= max(2,query.shape[0]*.08))
            attempt.update(ecc_score=float(ecc), ecc_transform=refined.tolist(), refinement_accepted=bool(good))
            if good:
                affine = refined
            else:
                attempt["refinement_reason"] = "bounded_affine_refinement_rejected_using_initial_mapping"
        except cv2.error:
            attempt.update(refinement_accepted=False, refinement_reason="ecc_did_not_converge_using_initial_mapping")
        refinement = np.eye(3)
        refinement[:2] = affine
        x, y = proposal["position"]
        qx, qy = proposal["qxy"]
        mapping = (np.diag([1/qx, 1/qy, 1.]) @ np.array([[1.,0.,x],[0.,1.,y],[0.,0.,1.]])
                   @ np.linalg.inv(refinement) @ proposal["base"])
        template_box = proposal["box_template"]
        mapped = cv2.perspectiveTransform(_corners(template_box).reshape(-1,1,2), mapping).reshape(-1,2)
        source_box = _mapped_box(mapped, proposal["source_shape"])
        matrix = np.linalg.inv(mapping) if proposal["reverse"] else mapping
        boxes = [source_box, template_box] if proposal["reverse"] else [template_box, source_box]
        attempt.update(status="aligned", analysis_transform_a_to_b=matrix.tolist(),
                       region_a_analysis_xywh=boxes[0], region_b_analysis_xywh=boxes[1],
                       independent_keypoints=0, alignment_basis="bounded_dense_ncc_and_global_affine_ecc")
        corners_a = _corners(boxes[0])
        duplicate = False
        for old in output:
            if old["window_id"] != rec["window_id"]:
                continue
            first = cv2.perspectiveTransform(corners_a.reshape(-1,1,2), matrix).reshape(-1,2)
            second = cv2.perspectiveTransform(corners_a.reshape(-1,1,2), np.array(old["matrix"])).reshape(-1,2)
            if float(np.median(np.linalg.norm(first-second,axis=1)))*min(qx,qy) <= 3:
                duplicate = True
                break
        if duplicate:
            attempt.update(status="deduplicated", reason="same_fixed_window_and_mapping")
            continue
        if len(output) >= policy["maximum_verification_candidates"]:
            attempt.update(status="not_run", reason="verification_candidate_budget", alignment_status="aligned")
            stage["truncated"] = True
            continue
        output.append({"matrix": matrix.tolist(), "box_a": boxes[0], "box_b": boxes[1],
                       "window_id": rec["window_id"], "attempt": attempt})
    stage["candidate_count"] = len(output)
    stage["omitted_before_refinement"] = max(0, len(proposals)-policy["maximum_refinement_candidates"])
    if not output:
        stage["reason"] = "no_dense_candidate_above_fixed_floor"
    return output, stage
