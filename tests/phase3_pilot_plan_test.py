"""Pilot preparation must preserve source scope and cannot imply live authority."""
import json
import subprocess
import sys
import pytest
from scripts.phase3_pilot_plan import build_plan


def sample():
    return {'dataset_id':'synthetic-wiring-only',
            'segments':[{'id':f's{i}','source':f'Synthetic source {i}. '+ 'Local example. '*20,
                         'paragraph_id':i//3} for i in range(24)]}


def test_actual_core_plans_preserve_same_order_and_scope_without_exposing_text():
    data=sample()
    plan=build_plan(data)
    for variant in plan['batching']['variants']:
        assert [i for b in variant['batches'] for i in b['segment_ids']]==[r['id'] for r in data['segments']]
        assert sum(b['source_chars'] for b in variant['batches'])==plan['source_chars']
    encoded=json.dumps(plan)
    assert 'Synthetic source' not in encoded and 'Local example.' not in encoded
    assert plan['status']=='NOT_AUTHORIZED_NOT_EXECUTED'
    assert plan['no_performance_conclusion_from_plan_counts']
    assert plan['execution_order'][0]=='paragraph_batching'
    assert plan['reasoning']['roles']==['annotation','knowledge']


def test_source_mapping_is_unambiguous():
    data=sample();data['segments'][1]['id']='s0'
    with pytest.raises(ValueError,match='unique'):build_plan(data)
    data=sample();data['segments'][-1]['paragraph_id']=0
    with pytest.raises(ValueError,match='contiguous'):build_plan(data)


def test_cli_has_no_live_mode(tmp_path):
    source=tmp_path/'manifest.json';source.write_text(json.dumps(sample()))
    result=subprocess.run([sys.executable,'scripts/phase3_pilot_plan.py','--manifest',str(source),
                           '--out',str(tmp_path/'plan.json'),'--live'],text=True,capture_output=True)
    assert result.returncode==2
    assert not (tmp_path/'plan.json').exists()
