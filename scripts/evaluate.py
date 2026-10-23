"""Evaluate an independently labelled pair set; never invent benchmark accuracy.

CSV fields: image_a,image_b,duplicate,category (category is optional).
Image paths are relative to the CSV file unless --root is supplied.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import sys
import time
from collections import OrderedDict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.engine import Detector, ENGINE_VERSION
from app.semantics import semantic_counts, semantic_prediction


def wilson(successes, count):
    if count == 0:
        return None
    z = 1.95996398454
    p = successes / count
    denominator = 1 + z*z/count
    center = (p + z*z/(2*count)) / denominator
    half = z * math.sqrt(p*(1-p)/count + z*z/(4*count*count)) / denominator
    return [round(max(0, center-half), 6), round(min(1, center+half), 6)]


def metrics(rows):
    tp = sum(row['label'] and row['detected'] for row in rows)
    fp = sum(not row['label'] and row['detected'] for row in rows)
    fn = sum(row['label'] and not row['detected'] for row in rows)
    tn = sum(not row['label'] and not row['detected'] and not row.get('abstained',False) for row in rows)
    negative_abstentions = sum(not row['label'] and row.get('abstained',False) for row in rows)
    positive_abstentions = sum(row['label'] and row.get('abstained',False) for row in rows)
    negatives = sum(not row['label'] for row in rows)
    return {'pairs': len(rows), 'tp': tp, 'fp': fp, 'fn': fn, 'tn': tn,
            'positive_abstentions':positive_abstentions, 'negative_abstentions':negative_abstentions,
            'coverage':(len(rows)-positive_abstentions-negative_abstentions)/len(rows) if rows else None,
            'precision': tp/(tp+fp) if tp+fp else None,
            'precision_wilson_95': wilson(tp, tp+fp),
            'recall': tp/(tp+fn) if tp+fn else None,
            'recall_wilson_95': wilson(tp, tp+fn),
            'false_positive_rate': fp/negatives if negatives and not negative_abstentions else None,
            'false_alarm_fraction_full_negative_denominator':fp/negatives if negatives else None,
            'false_positive_rate_wilson_95': wilson(fp, negatives) if not negative_abstentions else None}


def pair_prediction(detail):
    classes = {finding.get('evidence_class') for finding in detail['findings']}
    if classes - {'data_supported','annotation_only','inconclusive'}:
        return 'inconclusive'
    if 'data_supported' in classes:
        return 'data_supported'
    if 'inconclusive' in classes:
        return 'inconclusive'
    if 'annotation_only' in classes:
        return 'annotation_only'
    return 'no_match' if detail['diagnostics'].get('status')=='no_verified_match' else 'inconclusive'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('labels', type=Path)
    parser.add_argument('--root', type=Path)
    parser.add_argument('--sensitivity', choices=['strict', 'balanced'], default='strict')
    parser.add_argument('--dataset-role', choices=['development', 'heldout'], default='development',
                        help='Default is development; only use heldout for genuinely independent, untouched labels.')
    parser.add_argument('--output', type=Path, default=Path('test-results/benchmark'))
    args = parser.parse_args()
    if args.output.exists() and any(args.output.iterdir()):
        raise SystemExit('输出目录非空；拒绝覆盖历史结果。请使用新的 --output。')
    root = (args.root or args.labels.parent).resolve()
    with args.labels.open(encoding='utf-8-sig', newline='') as stream:
        labelled = list(csv.DictReader(stream))
    if not labelled or not {'image_a', 'image_b', 'duplicate'} <= set(labelled[0]):
        raise SystemExit('CSV 必须包含 image_a,image_b,duplicate 三列且至少一条记录')
    detector = Detector()
    cache = OrderedDict()

    def prepare(path):
        if path not in cache:
            cache[path] = detector.prepare(path)
        cache.move_to_end(path)
        while len(cache) > 24:
            cache.popitem(last=False)
        return cache[path]

    results = []
    start = time.perf_counter()
    for index, row in enumerate(labelled, 1):
        label = row['duplicate'].strip().lower()
        if label not in ('1', '0', 'true', 'false'):
            raise SystemExit(f'第 {index+1} 行 duplicate 必须为 1/0 或 true/false')
        a, b = (root / row['image_a']).resolve(), (root / row['image_b']).resolve()
        if a == b:
            raise SystemExit(f'第 {index+1} 行比较了同一路径，不应作为准确率样本')
        detail = detector.compare_detailed(prepare(a), prepare(b), sensitivity=args.sensitivity)
        prediction = pair_prediction(detail)
        result = detail['findings'][0] if detail['findings'] else None
        results.append({'image_a': row['image_a'], 'image_b': row['image_b'],
                        'category': row.get('category', 'unspecified'), 'label': label in ('1', 'true'),
                        'origin':row.get('origin','unspecified'),'source_group':row.get('source_group','unspecified'),
                        'detected': prediction=='data_supported','prediction':prediction,'abstained':prediction=='inconclusive',
                        'kind': result['kind'] if result else '',
                        'score': result['score'] if result else '',
                        'semantic_prediction': semantic_prediction(detail['findings']),
                        'semantic_counts': semantic_counts(detail['findings']),
                        'evidence': detail})
        print(f'[{index}/{len(labelled)}] label={label} prediction={prediction}', flush=True)
    summary = {'engine_version': ENGINE_VERSION, 'sensitivity': args.sensitivity,
               'dataset_role': args.dataset_role,
               'label_file': str(args.labels.resolve()), 'seconds': round(time.perf_counter()-start, 3),
               'overall': metrics(results),
               'task_layers': {'visual_reuse': {'metrics': metrics(results), 'positive_rule':'data_supported only; not an experimental identity claim'},
                               'experimental_semantics': {'status':'not_evaluated_no_independent_semantic_labels', 'metrics':None,
                                  'prediction_counts':{key:sum(r['semantic_prediction']==key for r in results) for key in ('source_supported_experimental_reuse','unknown_not_semantically_verified')},
                                  'note':'Visual labels are not silently reused as experimental-identity truth. Unknown is not a semantic TP.'}},
               'record_semantic_counts': semantic_counts([f for row in results for f in row['evidence']['findings']]),
               'by_category': {category: metrics([r for r in results if r['category'] == category])
                               for category in sorted({r['category'] for r in results})},
               'by_origin_category': {origin+':'+category:metrics([r for r in results if r['origin']==origin and r['category']==category])
                                      for origin,category in sorted({(r['origin'],r['category']) for r in results})},
               'evidence_class_counts':{classification:sum(r['prediction']==classification for r in results)
                                       for classification in ('data_supported','annotation_only','inconclusive','no_match')},
               'abstention_rule':'Positive abstention remains FN; negative abstention is not TN. FPR is unavailable with negative abstentions; full-negative-denominator alarm fraction is separate.',
               'interpretation': '仅适用于这份标注集。内置合成示例已用于开发调试，不是留出验证，也不能代表真实科研性能。Wilson区间假设样本近似独立；共享来源的图片对相关，真实验证应按论文/实验分组划分并使用分组统计。'}
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    (args.output / 'evidence.json').write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
    with (args.output / 'pairs.csv').open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=['image_a','image_b','category','origin','source_group','label','detected','prediction','abstained','kind','score','semantic_prediction','semantic_counts'], extrasaction='ignore')
        writer.writeheader()
        writer.writerows([{**row, 'semantic_counts':json.dumps(row['semantic_counts'],ensure_ascii=False)} for row in results])
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
