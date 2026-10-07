"""Same-corpus before/after runner. Live mode spends bounded model requests.

Uses original cleanup prompts for baseline and the structured provider after.
Never saves credentials. Region coverage is measurable; region accuracy needs
human annotations and remains null. Run with --live to request model responses.
"""
from __future__ import annotations

import argparse
from dataclasses import replace
import json
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import core
from folith import pdf_preview, request_control
from folith.desktop.paths import resolve_paths
from folith.document_understanding import VLMDocumentProvider
from folith.ocr_online import OnlineOCRBackend


def run(output, live, reasoning_mode, max_tokens, cases, variants):
    core.OUTPUT_DIR = resolve_paths().data_dir
    config,error=core.resolve_ocr_online_config({'credential_source':'translation_profile',
        'model':'deepseek-v4.1-flash','max_tokens':max_tokens,'reasoning_mode':reasoning_mode,
        'timeout_s':45,'max_retries':0})
    if live and error:
        raise ValueError(error['message'])
    core.OUTPUT_DIR = output/'jobs'
    output.mkdir(parents=True,exist_ok=True)
    rows=[]
    original_tessdata=os.environ.get('TESSDATA_PREFIX')
    corpus=ROOT/'benchmark/vlm-migration'
    for name,language in [('native','eng'),('clean_scan','eng'),('historical','ara'),('rtl','ara')]:
        if name not in cases:
            continue
        if language=='ara':
            os.environ['TESSDATA_PREFIX']=str(ROOT/'benchmark/tessdata_fast')
        elif original_tessdata:
            os.environ['TESSDATA_PREFIX']=original_tessdata
        else:
            os.environ.pop('TESSDATA_PREFIX',None)
        data=(corpus/(name+'.pdf')).read_bytes()
        for variant in variants:
            job=f'{variant}-{name}'
            started=time.perf_counter()
            calls=[]
            provider=VLMDocumentProvider(config) if live and variant=='after' and name!='native' else None
            paras,warnings,report=core.extract_document_paragraphs_with_report(
                name+'.pdf',data,ocr_max_pages=None,ocr_mode='off' if name=='native' else 'force',
                ocr_language=language,ocr_dpi=150,ocr_backend="tesseract" if variant=="before" else "online",
                checkpoint_dir=core.job_dir(job),
                understanding_provider=provider)
            if live and (variant=='before' or name=='native'):
                transport=OnlineOCRBackend(config)
                def cleanup_call(_provider,_key,_model,system,user,**kwargs):
                    controller=request_control.get_request_controller(config.provider,config.model)
                    controller.acquire()
                    body=transport._request_body(system,[{'type':'text','text':user}])
                    response=transport._get_client().chat.completions.create(**body)
                    controller.note_success()
                    calls.append({'request_id':response.id,'usage':response.usage.model_dump() if response.usage else {},
                                  'finish_reason':response.choices[0].finish_reason})
                    if response.choices[0].finish_reason!='stop':
                        raise ValueError('cleanup truncated')
                    return response.choices[0].message.content
                paras,cleanup,cleanup_warnings=core.cleanup_source_paragraphs(
                    paras,config.provider,config.api_key,config.model,call_llm_fn=cleanup_call,
                    confidence_by_index=(report.get('ocr') or {}).get('paragraph_confidences'),
                    parallelism=1,max_retries=0,ocr_language=language,text_direction='rtl' if language=='ara' else 'ltr')
                warnings+=cleanup_warnings
            else:
                cleanup={'status':'skipped'}
            state=core.new_job_state(name+'.pdf'); state['extraction_report']=report
            segments,_=core._apply_source_segmentation(state,paras)
            state['paras']=segments
            core.save_source(job,data);core.save_job_state(job,state)
            locations=[pdf_preview.get_cached_segment_location(job,i,s) for i,s in enumerate(segments)]
            diagnostics=report.get('page_diagnostics') or []
            vlm_usage=[d.get('usage') or {} for d in diagnostics]
            tokens=lambda entries,key:sum(e.get(key) or 0 for e in entries)
            uses=[c['usage'] for c in calls]+vlm_usage
            row={'case':name,'variant':variant,'live':live,'semantic_extraction_success':bool(paras),
                 'paragraphs':len(paras),'characters':sum(map(len,paras)),'segments':len(segments),
                 'ocr_repair_calls':len(calls),'vlm_calls':sum(d.get('attempt_count',0) for d in diagnostics),
                 'input_tokens':tokens(uses,'prompt_tokens'),'output_tokens':tokens(uses,'completion_tokens'),
                 'wall_seconds':time.perf_counter()-started,
                 'navigation_accuracy':sum(p==0 for p,r in locations)/len(locations) if locations else None,
                 'grounding_coverage':sum(bool(r) for p,r in locations)/len(locations) if locations else None,
                 'grounding_accuracy':None,'warnings':warnings,'cleanup':cleanup,'repair_requests':calls,
                 'page_diagnostics':diagnostics,'semantic_paragraphs':paras}
            rows.append(row)
            (output/'comparison.json').write_text(json.dumps({'scope':'4 one-page fixtures; page navigation measured; no region gold annotations; single run latency',
                                                              'model':config.model if live else None,'cases':rows},indent=2,ensure_ascii=False))
            print({k:v for k,v in row.items() if k not in {'cleanup','repair_requests','page_diagnostics','semantic_paragraphs','warnings'}},flush=True)
    if original_tessdata:
        os.environ['TESSDATA_PREFIX']=original_tessdata
    else:
        os.environ.pop('TESSDATA_PREFIX',None)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--live',action='store_true')
    parser.add_argument('--output',type=Path,default=ROOT/'benchmark/vlm-migration/live')
    parser.add_argument('--reasoning-mode',default='provider_default',choices=['provider_default','thinking_disabled'])
    parser.add_argument('--max-tokens',type=int,default=4096)
    parser.add_argument('--cases',nargs='+',default=['native','clean_scan','historical','rtl'])
    parser.add_argument('--variants',nargs='+',choices=['before','after'],default=['before','after'])
    args=parser.parse_args();run(args.output,args.live,args.reasoning_mode,args.max_tokens,args.cases,args.variants)
