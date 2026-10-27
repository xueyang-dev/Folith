"""Independent full baseline import; deterministic translate_stage plans, pairs and prompt comparison.
Synthetic text exists in temporary subprocess output only. Persistent artifact contains booleans.
"""
import copy, json, os, re, subprocess, sys, tarfile, tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3]
BASE='397adb22d8de46edb0781dc75e53661318a5cb11'

def worker(root):
    sys.path.insert(0,str(root))
    import core
    core._call_llm_transport=lambda *a, **kw: (_ for _ in ()).throw(AssertionError('NETWORK FORBIDDEN'))
    from folith import context
    from unittest.mock import patch
    from contextlib import ExitStack
    data=[]
    sources=[f'Synthetic section {i} discusses Aerodynamics in Germany.' for i in range(24)]
    reference={s: f'Synthetic target {i} Aerodynamics Germany.' for i,s in enumerate(sources)}
    for strategy in ['legacy','sentence','paragraph']:
        seed=None
        for resume in [False,True]:
            prompts=[]
            def call(provider,key,model,system,user,**kw):
                if '问题清单' in user:
                    ss=re.findall(r'^\d+\. 原文：(.*)$',user,re.M)
                    return json.dumps([reference[s] for s in ss])
                if '你是一位专业翻译人员' in system:
                    tail=user.split(context.CURRENT_BATCH_TITLE,1)[-1]
                    ss=re.findall(r'^\d+\. (.*)$',tail,re.M)
                    prompts.append([system,user])
                    return json.dumps([reference[s] for s in ss])
                if '翻译流知识抽取器' in system: return '[]'
                return '{"findings":[],"evidence_requests":[]}'
            with tempfile.TemporaryDirectory() as td, ExitStack() as stack:
                for n,v in [('OUTPUT_DIR',Path(td)),('call_llm',call)]: stack.enter_context(patch.object(core,n,v))
                s=core.new_job_state('synthetic.docx')
                s['paras']=list(sources)
                s['segmentation']={'paragraph_ranges':[{'paragraph_index':i//3,'start':i,'end':i+3} for i in range(0,24,3)]}
                s['pipeline_config']={} if strategy=='legacy' else {'batch_strategy':strategy}
                if resume: s['pairs']=copy.deepcopy(seed[:6])
                core.save_job_state('batch-audit',s)
                result=core.translate_stage(s,'batch-audit',[],'DeepSeek','synthetic','model','en','',enable_review=False,use_tm=False,
                    batch_size=6,max_batch_chars=4800,translation_concurrency=1,knowledge_feedback=False,use_translation_cache=False)
                seed=copy.deepcopy(result['pairs'])
                plan={k:v for k,v in result['batch_plan'].items() if k!='snapshot_cadence'}
                pairs=[{k:p.get(k) for k in ['source','target','id','segment_id','from_tm']} for p in result['pairs']]
                data.append({'strategy':strategy,'resume':resume,'plan':plan,'pairs':pairs,'prompts':prompts,'calls':len(prompts)})
    print(json.dumps(data))

if len(sys.argv)>1:
    worker(Path(sys.argv[1]))
else:
    with tempfile.TemporaryDirectory(prefix='folith-baseline-') as td:
        root=Path(td)
        p=subprocess.Popen(['git','archive',BASE],cwd=ROOT,stdout=subprocess.PIPE)
        with tarfile.open(fileobj=p.stdout,mode='r|') as tar: tar.extractall(root)
        assert p.wait()==0
        rows=[]
        for runtime in [root,ROOT]:
            proc=subprocess.run([sys.executable,str(Path(__file__).resolve()),str(runtime)],cwd=runtime,text=True,capture_output=True)
            if proc.returncode: raise RuntimeError(proc.stderr)
            rows.append(json.loads(proc.stdout.splitlines()[-1]))
        checks=[]
        for old,new in zip(*rows):
            checks.append({'strategy':new['strategy'],'resume':new['resume'], 'plan_equal':old['plan']==new['plan'],
                'pairs_and_ids_equal':old['pairs']==new['pairs'], 'prompt_context_equal':old['prompts']==new['prompts'],
                'baseline_calls':old['calls'],'candidate_calls':new['calls']})
        (ROOT/'docs/performance/phase3-independent-evidence/batch-results.json').write_text(json.dumps(checks,indent=2)+'\n')
        print(json.dumps(checks,indent=2))
