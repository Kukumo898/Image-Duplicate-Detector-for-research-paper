"""Additive visual/semantic interpretation; never infer experiment identity from pixels.

Historical JSON is projected, not updated. A review confirming similarity is not
an experimental-identity annotation and is deliberately not used here.
"""
from copy import deepcopy

SEMANTIC_SCHEMA_VERSION = '1.0-visual-semantic-split'
CONTENT_ROLES = {
    'experimental_image_candidate': '实验图像候选（未核实实验身份）',
    'schematic_icon': '示意图/图标',
    'annotation_template': '标注/模板',
    'unknown': '内容角色未知',
}
COUNT_LABELS = {
    'total_records': '全部记录', 'visual_reuse_clues': '视觉复用线索',
    'experimental_image_candidates': '实验图像候选', 'schematic_icons': '示意图/图标',
    'annotation_records': '标注类', 'semantic_unknown': '语义无来源核实',
    'content_role_unknown': '内容角色未知',
    'inconclusive': '视觉证据不确定',
    'established_experimental_reuse': '有来源依据的实验图像复用',
    'semantic_based_records': '有来源/复核依据的内容角色',
}

def enrich_finding(finding, content_role=None, semantic_basis=None):
    """Return a copy with additive fields, preserving all old evidence/versions."""
    result = deepcopy(finding)
    status = result.get('evidence_class', 'legacy_unclassified')
    role = content_role or result.get('content_role')
    if role not in CONTENT_ROLES:
        role = 'annotation_template' if status == 'annotation_only' else 'unknown'
    basis = deepcopy(semantic_basis or result.get('semantic_basis') or {})
    basis.setdefault('status', 'image_heuristic' if role == 'annotation_template' else 'unknown')
    basis.setdefault('source', None)
    basis.setdefault('basis', '标注类视觉证据；不核实实验身份。' if role == 'annotation_template'
                     else '缺少可追溯的实验身份或内容语义依据，不能从像素对应推断实验属性。')
    sourced = basis.get('status') in ('source_context', 'human_review') and bool(basis.get('source'))
    # A heuristic (including microscopy appearance) cannot certify experiment identity.
    verified = bool(basis.get('experimental_identity_verified')) and sourced and role == 'experimental_image_candidate'
    basis['experimental_identity_verified'] = verified
    visual = status == 'data_supported'
    result.update(semantic_schema_version=SEMANTIC_SCHEMA_VERSION,
                  visual_evidence={'status': status, 'raster_correspondence': visual,
                                   'basis': '原几何/像素及共同有效区证据见 metrics.annotation_verification；不是实验属性证明。'},
                  content_role=role, semantic_basis=basis,
                  review_scope={'visual_reuse_supported': visual,
                                'established_experimental_reuse': visual and verified,
                                'misconduct_assessed': False,
                                'limitations': ['像素复用不等于实验重复或不当行为；共享通道、公共对照、图标可能合法。',
                                                '内容角色候选不等于来源已核实；未知不能计作语义真阳性。']})
    if status == 'legacy_unclassified' or 'semantic_schema_version' not in finding:
        result['historical_interpretation'] = ('加法只读解释，原结果/版本/复核保持；旧 data_supported 仅指遮罩外视觉对应，未追认为已验证实验数据。')
    return result

def semantic_counts(findings):
    counts = dict.fromkeys(COUNT_LABELS, 0)
    for original in findings:
        f = enrich_finding(original)
        counts['total_records'] += 1
        counts['visual_reuse_clues'] += int(f['visual_evidence']['raster_correspondence'])
        counts['experimental_image_candidates'] += int(f['content_role'] == 'experimental_image_candidate')
        counts['schematic_icons'] += int(f['content_role'] == 'schematic_icon')
        counts['annotation_records'] += int(f['content_role'] == 'annotation_template')
        counts['content_role_unknown'] += int(f['content_role'] == 'unknown')
        counts['inconclusive'] += int(f.get('evidence_class') == 'inconclusive')
        sourced = f['semantic_basis']['status'] in ('source_context','human_review') and bool(f['semantic_basis']['source'])
        counts['semantic_based_records'] += int(sourced)
        counts['semantic_unknown'] += int(not sourced)
        counts['established_experimental_reuse'] += int(f['review_scope']['established_experimental_reuse'])
    return counts

def semantic_prediction(findings):
    """Semantic abstention is separate from visual detection or no-match."""
    rows = [enrich_finding(f) for f in findings]
    if any(f['review_scope']['established_experimental_reuse'] for f in rows):
        return 'source_supported_experimental_reuse'
    return 'unknown_not_semantically_verified'
