"""Validate the retained kill snapshot and successful frozen flow independently."""
import json
from pathlib import Path
import sys
sys.path[:0]=['scripts']
from qt_crash_recovery_probe import _durable_state
from docx import Document
q=Path(sys.argv[1]).resolve()
failed=json.loads((q/'recovery-report.json').read_text(encoding='utf-8'))
job=next((q/'data').glob('*/state.json')).parent.name
before,replay=_durable_state(q/'after-kill',job)
after,final_replay=_durable_state(q/'data',job)
flow=json.loads((q/'flow.json').read_text(encoding='utf-8'))
assert flow['ok'] and flow['identity']['frozen']
assert len(after['pairs'])==len(after['paras'])==4000
added_uids=0
for old,new in zip(before['pairs'],after['pairs']):
    for key in ['source','target','initial_target']:
        assert old.get(key)==new.get(key), key
    if old.get('segment_uid'):
        assert old['segment_uid']==new.get('segment_uid')
    elif new.get('segment_uid'):
        added_uids+=1
events=q/'data'/job/'events.jsonl'
offset=(q/'after-kill'/job/'events.jsonl').stat().st_size
with events.open('rb') as handle:
    handle.seek(offset)
    offsets=[e['offset'] for line in handle if line.strip()
             for e in [json.loads(line)] if e.get('phase')=='generation_started']
assert offsets and min(offsets)>=len(before['pairs'])
exported=next(Path(f) for f in flow['saved_files'] if f.endswith('_translation.docx'))
text='\n'.join(p.text for p in Document(exported).paragraphs)
position=0
for pair in after['pairs']:
    target=pair['target']
    assert target
    found=text.find(target,position)
    assert found>=0
    position=found+len(target)
report={'ok':True,'scope':'post-run validation of retained original artifacts; no new translation run',
        'fixture':'4000 generated Chinese paragraphs; deterministic localhost provider',
        'killed_exit':failed['killed']['exit_code'],'committed_pairs_at_kill':len(before['pairs']),
        'replay_after_kill':replay,'replay_after_resume':final_replay,
        'recovered_pairs':len(after['pairs']),'committed_text_fields_preserved':True,
        'canonical_uids_added':added_uids,'existing_canonical_uids_changed':0,
        'minimum_regenerated_offset':min(offsets),'committed_batches_not_regenerated':True,
        'reviewed':flow['review']['reviewed'],'final_delivery':flow['delivery'],
        'all_4000_export_targets_in_source_order':True,'identity':flow['identity'],
        'original_driver_assertion':'incorrectly required a previously absent segment_uid to stay absent; corrected validator allows canonical UID addition',
        'duration_s':failed['duration_s']}
(q/'verified-recovery-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(report,ensure_ascii=False,indent=2))
