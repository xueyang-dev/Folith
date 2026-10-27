#!/usr/bin/env python3
"""Prepare a no-network Paragraph 2k/4k and reasoning pilot plan.

Input source text stays local. Output contains IDs, sizes and actual core batch
boundaries, never the source, target, credentials, or a latency prediction.
This command deliberately has no live execution mode.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def build_plan(manifest):
    import core
    rows = manifest.get('segments') or []
    if not rows or not manifest.get('dataset_id'):
        raise ValueError('dataset_id and nonempty segments are required')
    ids = [r['id'] for r in rows]
    if any(not isinstance(i, str) or not i for i in ids) or len(set(ids)) != len(ids):
        raise ValueError('segment IDs must be unique nonempty strings')
    sources = [r['source'] for r in rows]
    if any(not isinstance(s, str) or not s.strip() for s in sources):
        raise ValueError('every source segment must contain text')
    ranges, seen = [], set()
    for index, row in enumerate(rows):
        pid = row['paragraph_id']
        if index and pid == rows[index - 1]['paragraph_id']:
            ranges[-1]['end'] = index + 1
        else:
            if pid in seen:
                raise ValueError('each paragraph must occupy one contiguous range')
            seen.add(pid)
            ranges.append({'paragraph_index': len(ranges), 'start': index, 'end': index + 1})
    variants = []
    for name, cap in [('sentence_baseline', 4800), ('paragraph_2k', 2000), ('paragraph_4k', 4000)]:
        batches = (core.make_batches(sources, batch_size=6, max_chars=cap)
                   if name == 'sentence_baseline' else
                   core.make_paragraph_batches(sources, ranges, max_chars=cap, max_items=20))
        assert [s for b in batches for s in b] == sources
        cursor, plan = 0, []
        for batch in batches:
            end = cursor + len(batch)
            plan.append({'segment_ids': ids[cursor:end], 'source_chars': sum(map(len, batch)),
                         'over_char_budget_indivisible_unit': sum(map(len, batch)) > cap})
            cursor = end
        variants.append({'id': name, 'strategy': 'sentence' if name == 'sentence_baseline' else 'paragraph',
                         'max_chars': cap, 'max_items': 6 if name == 'sentence_baseline' else 20,
                         'planned_batches': len(plan), 'batches': plan})
    return {
        'status': 'NOT_AUTHORIZED_NOT_EXECUTED', 'mode': 'offline_plan_only',
        'dataset_id': manifest['dataset_id'], 'segment_count': len(rows),
        'source_chars': sum(map(len, sources)),
        'language_direction': manifest.get('language_direction', 'English -> Simplified Chinese'),
        'execution_order': ['paragraph_batching', 'annotation_knowledge_reasoning'],
        'batching': {'variants': variants, 'paired_repetitions': 2,
                     'same_source_scope_context_model_endpoint_quality_policy': True,
                     'proposed_concurrency': 4, 'max_all_role_logical_requests': 160,
                     'max_http_attempts': 320, 'proposed_provider_hard_budget_usd': 5},
        'reasoning': {'roles': ['annotation', 'knowledge'],
                      'modes': ['provider_default', 'thinking_disabled'],
                      'paired_payloads_per_role': 4, 'max_capability_probes': 2,
                      'max_all_role_logical_requests': 18, 'max_http_attempts': 36,
                      'proposed_provider_hard_budget_usd': 3},
        'required_authorization': ['reviewed exact source/context/target/glossary sample bundle',
                                   'provider, model and final endpoint',
                                   'both budgets and provider-side hard quotas',
                                   'supported output ceiling and actual billing rules'],
        'primary_metrics': ['actual time to current-policy translation_ready',
                            'whole experimental run wall time including required QA/review/repair/knowledge',
                            'blinded bilingual quality and hard-invariant failures'],
        'secondary_metrics': ['logical requests by operation', 'HTTP attempts',
                              'reported/unknown input/output/reasoning/cached usage',
                              'recovery and retry work', 'provider-confirmed cost'],
        'no_performance_conclusion_from_plan_counts': True,
        'production_promotion': 'blocked pending measured completion time and quality acceptance',
    }


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--manifest', type=Path, required=True)
    p.add_argument('--out', type=Path, required=True)
    args = p.parse_args()
    result = build_plan(json.loads(args.manifest.read_text(encoding='utf-8')))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print('Offline plan written; no data sent and no model requests made.')


if __name__ == '__main__':
    main()
