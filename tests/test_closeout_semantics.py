"""Semantic boundaries are contracts, not evidence of detector precision."""
from copy import deepcopy
from app.semantics import enrich_finding, semantic_counts, semantic_prediction

def test_raster_support_is_not_experiment_identity_and_preserves_history():
    old={'evidence_class':'data_supported','metrics':{'engine_version':'3.0.0'},'review_status':'confirmed'}
    before=deepcopy(old); f=enrich_finding(old)
    assert old==before
    assert f['visual_evidence']['raster_correspondence']
    assert f['content_role']=='unknown'
    assert not f['review_scope']['established_experimental_reuse']
    assert f['metrics']==old['metrics']

def test_schematic_visual_reuse_and_candidate_differ_from_semantic_verification():
    visual={'evidence_class':'data_supported'}
    icon=enrich_finding(visual,'schematic_icon',{'status':'source_context','source':'figure-caption','basis':'diagram'})
    candidate=enrich_finding(visual,'experimental_image_candidate',{'status':'image_heuristic','experimental_identity_verified':True})
    assert icon['visual_evidence']['raster_correspondence']
    assert not icon['review_scope']['established_experimental_reuse']
    assert not candidate['semantic_basis']['experimental_identity_verified']
    counts=semantic_counts([icon,candidate,enrich_finding({'evidence_class':'annotation_only'})])
    assert counts['total_records']==3 and counts['visual_reuse_clues']==2
    assert counts['schematic_icons']==counts['experimental_image_candidates']==counts['annotation_records']==1
    assert counts['established_experimental_reuse']==0
    assert semantic_prediction([icon,candidate])=='unknown_not_semantically_verified'

def test_source_evidence_required_even_for_explicit_identity_claim():
    source=enrich_finding({'evidence_class':'data_supported'},'experimental_image_candidate',
        {'status':'source_context','source':'publisher-correction','basis':'experiment identity disclosed','experimental_identity_verified':True})
    assert source['review_scope']['established_experimental_reuse']
    assert semantic_counts([source])['semantic_based_records']==1
    assert semantic_prediction([source])=='source_supported_experimental_reuse'
    legacy=enrich_finding({'metrics':{}})
    assert 'evidence_class' not in legacy
    assert legacy['visual_evidence']['status']=='legacy_unclassified'
