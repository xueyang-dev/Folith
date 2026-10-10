#!/usr/bin/env python3
"""Read-only local-log audit; only writes reports to --out. No Folith imports/API calls.

python3 docs/performance-real-tasks-2026-10-08/audit.py
python3 docs/performance-real-tasks-2026-10-08/audit.py --evidence evidence.json --out /tmp/folith-audit
"""
import argparse
import collections
import datetime as dt
import json
import math
from pathlib import Path
import re


def load(p):
    return json.loads(p.read_text(encoding='utf-8'))


def lines(p):
    return [json.loads(x) for x in p.read_text(encoding='utf-8').splitlines() if x.strip()] if p.exists() else []


def checkpoint_metrics(p):
    records=[]
    for raw in lines(p):
        record={k:v for k,v in raw.items() if k != 'entries'}
        if 'entries' in raw:
            record['entries_count']=len(raw['entries'])
        records.append(record)
    return records


def stamp(x):
    return dt.datetime.fromisoformat(x).timestamp()


def distribution(xs):
    if not xs:
        return None
    xs = sorted(xs)
    return dict(count=len(xs), sum=sum(xs), avg=sum(xs)/len(xs), min=xs[0], max=xs[-1],
                **{f'p{p}': xs[round(p/100*(len(xs)-1))] for p in [50, 90, 95, 99]})


def collect(roots):
    jobs = {}
    inventory = []
    for root in roots:
        for f in sorted(root.glob('*/state.json')):
            state = load(f)
            runs = [load(q) for q in sorted(f.parent.glob('.performance/runs/*/summary.json'))]
            api_runs = [r for r in runs if r.get('model_metrics', {}).get('total_calls', 0)]
            if not api_runs:
                continue
            jid = f.parent.name
            inventory.append(dict(job_id=jid, directory=str(f.parent), filename=state.get('filename'),
                                  latest_started_at=max(r['started_at'] for r in api_runs)))
            if jid in jobs:  # first root (desktop) is authoritative for duplicate job IDs
                continue
            stages = state.get('performance', {}).get('stages', {})
            cfg = state.get('pipeline_config', {})
            trace_files = {}
            for q in sorted(f.parent.glob('.performance/runs/*/summary.json')):
                trace_files[q.parent.name] = dict(events=len(lines(q.parent/'events.jsonl')),
                    resources=len(lines(q.parent/'resources.jsonl')), stages=list(load(q.parent/'stages.json')) if (q.parent/'stages.json').exists() else [])
            jobs[jid] = dict(job_id=jid, directory=str(f.parent), filename=state.get('filename'), trace_files=trace_files,
                runs=runs, runtime_events=lines(f.parent/'runtime_events.jsonl'),
                checkpoint_events=checkpoint_metrics(f.parent/'events.jsonl'),
                technical_log=(f.parent/'runtime_technical.log').read_text() if (f.parent/'runtime_technical.log').exists() else '',
                state=dict(stage=state.get('stage'), delivery_status=state.get('delivery_status'),
                    p2_done=state.get('p2_done'), source_segments=len(state.get('paras', [])),
                    pairs=len(state.get('pairs', [])), extraction=state.get('extraction_report', {}).get('extracted', {}),
                    usage=state.get('llm_usage', {}), context_packets=state.get('context_packet_log', []),
                    batch_plan=state.get('batch_plan', {}), review_stats=state.get('review_stats', {}),
                    understanding_done=state.get('understanding_done'), annotations_done=state.get('annotations_done'),
                    exported_assets=state.get('exported_assets', []), delivery_snapshots=len(state.get('delivery_snapshots', [])),
                    config={k:cfg.get(k) for k in ['enable_understanding','enable_source_cleanup','enable_review','enable_annotate','enable_report','batch_size','max_batch_chars','translation_concurrency']},
                    provider=state.get('provider'), model=state.get('model'),
                    profiler_run_id=state.get('performance', {}).get('run_id'), profiler_stages=stages))
    sample = [j for j in jobs.values() if 'marker-sample' in (j['filename'] or '')]
    business = [j for j in jobs.values() if j not in sample]
    business.sort(key=lambda j:max(r['started_at'] for r in j['runs']), reverse=True)
    return dict(schema=1, roots=[str(r) for r in roots], selection='latest run started_at; deduplicate job_id; exclude marker sample',
                inventory=inventory, business=business[:5], samples=sample)


def analyze(j):
    s=j['state']; es=j['runtime_events']; runs=j['runs']; stages=s['profiler_stages']; ctx=s['context_packets']
    starts=[stamp(r['started_at']) for r in runs]; ends=[stamp(r['finished_at']) for r in runs]
    intervals=sorted(zip(starts, ends)); union=0; lo=hi=None
    for a,b in intervals:
        if lo is None: lo,hi=a,b
        elif a <= hi: hi=max(hi,b)
        else: union+=hi-lo;lo,hi=a,b
    if lo is not None: union+=hi-lo
    active=[int(m.group(1)) for e in es if (m:=re.search(r'(\d+) 个请求进行中', e.get('message','')))]
    packet_totals={k:sum(x.get(k,0) or 0 for x in ctx) for k in ['prompt_chars','context_prefix_chars','current_batch_chars','dedupe_removed_count']}
    phase_metrics={}
    for k,v in stages.items():
        items=v.get('items', [])
        phase_metrics[k]=dict(container_wall_s=v.get('wall_time_s'), active_sum_s=sum(x['wall_time_s'] for x in items) if items else None,
            item_latency=distribution([x['wall_time_s'] for x in items]), items=len(items),
            metadata=v.get('metadata', {}), started_at=v.get('started_at'), finished_at=v.get('finished_at'))
    boundary=[]
    for e in es:
        msg=e.get('message','')
        if e.get('event') in ['worker_started','worker_released','job_failed','job_cancelled','job_completed'] or (
            ('【阶段' in msg and '批次 ' not in msg) or '全文理解完成' in msg or '自动标注完成' in msg):
            boundary.append(e)
    milestones=[]
    for start_text,end_text,label in [('【阶段1.3】','全文理解完成','document_understanding'),
                                     ('【阶段二终检】','【阶段 2.5】','targeted_final_review'),
                                     ('【阶段 2.5】','自动标注完成','annotation_elapsed')]:
        start=next((e for e in es if start_text in e.get('message','')),None)
        end=next((e for e in es if end_text in e.get('message','') and (not start or e['timestamp']>=start['timestamp'])),None)
        if start and end:
            milestones.append(dict(label=label,started_at=start['timestamp'],finished_at=end['timestamp'],
                elapsed_s=stamp(end['timestamp'])-stamp(start['timestamp']), includes_interruptions=label=='annotation_elapsed' and len(runs)>1))
    gen=stages.get('translation_generation', {}).get('items', [])
    return dict(job_id=j['job_id'], elapsed_first_to_last_s=max(ends)-min(starts), summary_wall_sum_s=sum(r['wall_time_s'] for r in runs),
        summary_interval_union_s=union, outside_summary_intervals_s=max(ends)-min(starts)-union,
        summary_calls=sum(r['model_metrics']['total_calls'] for r in runs),
        summary_failed_calls=sum(r['model_metrics']['failed_calls'] for r in runs),
        summary_usage={k:sum(r['model_metrics'].get(k,0) for r in runs) for k in ['total_prompt_tokens','total_completion_tokens','total_cached_tokens','total_tokens']},
        observed_remaining_active_max=max(active,default=None), retry_event_count=sum(e.get('event')=='translation_retry' for e in es),
        runtime_event_counts=dict(collections.Counter(e.get('event') for e in es)),
        checkpoint_phase_counts=dict(collections.Counter(e.get('phase') for e in j['checkpoint_events'])),
        packet_totals=packet_totals, packet_count=len(ctx),
        source_chars_per_packet=distribution([x.get('current_batch_chars',0) for x in ctx]),
        current_api_candidate_counts=dict(collections.Counter(x.get('current_batch_count',0) for x in ctx)),
        planned_segment_counts=dict(collections.Counter(x.get('metadata',{}).get('segment_count') for x in gen)),
        phase_metrics=phase_metrics, boundaries=boundary, milestone_intervals=milestones)


def table(headers, rows):
    def esc(x): return str(x).replace('|','\\|').replace('\n',' ')
    return '\n'.join(['| '+' | '.join(headers)+' |','|'+'|'.join(['---']*len(headers))+'|']+
        ['| '+' | '.join(esc(x) for x in row)+' |' for row in rows])


def number(x):
    return '缺失' if x is None else f'{x:,.3f}'


def render(evidence, analysis):
    jobs=evidence['business']+evidence['samples']; by={a['job_id']:a for a in analysis}
    short={j['job_id']: ('Eco' if 'Eco-' in j['filename'] else 'Attention-2p' if '2p' in j['filename'] else '样本' if 'marker' in j['filename'] else 'Attention-8p' if 'arxiv' in j['filename'] else 'Sensorium-53p' if 'Sensorium' in j['filename'] else 'Sky-224p') for j in jobs}
    out=['# Folith 最近真实翻译任务性能报告','', '审计日期：2026-10-08，展示时区 Asia/Riyadh（UTC+3）。只读原任务记录；生产代码与质量策略未修改。', '',
         '## 样本与端到端口径','',
         '桌面数据目录与旧版 outputs 合并，按同一 job_id 去重（桌面优先），按最近 run.started_at 排序，选取 5 个有 API 调用的业务文档任务；验证样本单列。不是最近 5 个成功任务。真实文档及 API 调用可由记录支持，操作者用途没有独立 provenance 字段。benchmark、损坏文件测试和无 API 任务不纳入。', '',
         '端到端实测范围是 run 启动到 worker 结束，不包括此前上传/点击耗时；没有已导出交付物记录，因此所有业务任务的“到最终交付”耗时均未知。多次运行报告自然历时、已保存 run 区间并集和区间外时间；区间外时间可能包含缺失运行，不能一律称为人为等待。', '']
    rows=[]
    for j in jobs:
        a=by[j['job_id']];s=j['state']
        rows.append([short[j['job_id']],j['job_id'],s['extraction'].get('pages','缺失'),s['extraction'].get('characters','缺失'),s['source_segments'],s['pairs'],s['stage']+'/'+str(s['delivery_status']),number(a['elapsed_first_to_last_s']),number(a['summary_interval_union_s'])])
    out += [table(['任务','job_id','页','提取字符','段数','已存 pairs','业务状态/交付状态','首末自然历时 s','已存 run 并集 s'],rows),'', '业务文档完整名称及数据路径：','']
    out += [f"- {short[j['job_id']]}：{j['filename']}；`{j['directory']}`。" for j in jobs]
    out += ['', '## 各次运行、请求分布与 usage','',
            '下表的 API 次数实际是 call_llm 层的逻辑调用数；SDK 内部 HTTP 重试不单列。延迟包含 SDK 内部重试/传输等待；P 分位来自已保存汇总，不能从几个运行的分位数重新计算总体分位。失败调用也纳入分布。Tokens 是运行追踪从提供商 usage 汇总的记录值；失败请求未返回 usage 的成本不在其中，不能保证等于账单。', '']
    rows=[]
    for j in jobs:
        for r in j['runs']:
            p=r['latency_percentiles']['model_call_seconds'];m=r['model_metrics']
            rows.append([short[j['job_id']],r['run_id'],r['status'],number(r['wall_time_s']),m['total_calls'],m['failed_calls'],number(p['avg']),number(p['p50']),number(p['p95']),number(p['max']),m['total_prompt_tokens'],m['total_completion_tokens'],m['total_cached_tokens']])
    out += [table(['任务','run_id','状态','运行 s','调用','失败','均值 s','P50 s','P95 s','最大 s','输入 usage','输出 usage','缓存 usage'],rows),'',
            '## 状态账本：按角色的字符估算','',
            '所有任务 exact_token_calls=0；下表 token 数遵循逐调用 ceil(chars/4)，属于估算，不能与上表实测 usage 混合。角色 latency 是累计调用耗时，不是角色独占端到端耗时；辅助角色也可能涵盖多个步骤。', '']
    rows=[]
    for j in jobs:
        for role,v in j['state']['usage'].get('by_role',{}).items():
            rows.append([short[j['job_id']],role,v['calls'],v['failures'],number(v['latency_seconds']),v['prompt_tokens'],v['completion_tokens']])
    out += [table(['任务','角色','调用','失败','累计请求 s','输入估算 token','输出估算 token'],rows),'',
            '## 翻译、审校、知识反馈和持久化的已存批次计时','',
            '下表重算 state.performance.stages[*].items 的 wall_time_s 之和；这是并发工作量，不能相加当端到端时间。阶段 started_at/finished_at 是整个循环的容器边界，不能作为每个子阶段独占耗时。无 items 用“缺失”，不采用 active_wall_time_s 自动回填的容器时长。Eco 失败/取消没有关闭保存翻译 profiler，所以细分缺失。Sky 仅保留最后 82 批翻译的计时，其余批次不完整。', '']
    rows=[]
    phases=['translation_generation','translation_deterministic_qa','translation_repair','translation_semantic_review','knowledge_feedback','state_commit','tm_commit']
    for j in jobs:
        a=by[j['job_id']]
        rows.append([short[j['job_id']]]+[number(a['phase_metrics'].get(k,{}).get('active_sum_s')) for k in phases])
    out += [table(['任务','生成 Σs','确定性 QA Σs','修复 Σs','语义审校 Σs','知识反馈 Σs','状态提交 Σs','TM 提交 Σs'],rows),'',
            '已存逐批次生成计时的分布（包括复用/无需调用的短批次，不等于纯网络请求分布）：','']
    rows=[]
    for j in jobs:
        p=by[j['job_id']]['phase_metrics'].get('translation_generation',{}).get('item_latency')
        if p: rows.append([short[j['job_id']],p['count'],number(p['avg']),number(p['p50']),number(p['p95']),number(p['max'])])
    out += [table(['任务','已存批数','均值 s','P50 s','P95 s','最大 s'],rows),'',
            '## 批次、并发与重复上下文','',
            '各任务配置 batch_size=4、max_batch_chars=2400。计划批次不等于调用次数；每批还可能生成、修复、审校、知识反馈。current_batch_count 是排除可复用内容后的模型候选段数，context_packet_log 每个已提交批次只记一次，不覆盖全部重试与未提交批次。前缀包含相邻源文、已有译文、语义摘要等，前缀总量是“多次携带的上下文量”，不能视为全部可删除的重复文本。', '']
    rows=[]
    for j in jobs:
        a=by[j['job_id']];b=j['state']['batch_plan'];t=a['packet_totals'];n=a['packet_count'];p=a['source_chars_per_packet']
        rows.append([short[j['job_id']],b.get('batch_count'),b.get('concurrency'),a['observed_remaining_active_max'],n,number(p['avg']) if p else '缺失',t['context_prefix_chars'],t['current_batch_chars'],number(t['context_prefix_chars']/t['current_batch_chars']) if t['current_batch_chars'] else '缺失',f"{100*t['context_prefix_chars']/t['prompt_chars']:.1f}%" if t['prompt_chars'] else '缺失',json.dumps(a['current_api_candidate_counts'],ensure_ascii=False)])
    out += [table(['任务','计划批数','批 worker 并发','日志剩余在途最大','已存上下文批数','源文均值 字符/批','前缀累计字符','当前源文累计字符','前缀/源文','前缀/packet','模型候选段数:批数'],rows),'',
            '“日志剩余在途最大”是合并遥测在响应后输出的逻辑 call_llm 在途数，是观测值而非完整峰值；不能用 request_started/response_received 条数相减重建并发。逐请求起止没有落盘，实际 HTTP API 并发峰值/均值均未知。知识反馈在主提交路径串行运行，同时 worker 可继续请求，因此配置 4 不代表进程严格至多 4 个 API 调用。', '',
            '## 正常写入成本与资源','']
    rows=[]
    for j in jobs:
        a=by[j['job_id']];m=a['phase_metrics'].get('runtime_telemetry',{}).get('metadata',{})
        rows.append([short[j['job_id']],m.get('state_writes','缺失'),m.get('event_appends','缺失'),m.get('fsyncs','缺失'),number(m.get('state_write_seconds')),number(m.get('event_append_seconds')),a['retry_event_count'],a['summary_failed_calls']])
    out += [table(['任务','runtime 状态写次数','事件 append 次数','fsync 次数','状态写 Σs','事件写 Σs','可见翻译 retry 事件','trace 失败调用'],rows),'',
            '状态提交、TM 提交、runtime 写入是各自计时口径，可能重叠且不涵盖所有最终快照/交付写入，不能相加声称总 IO。runtime_telemetry.wall_time_s 是循环容器，不是磁盘时间。进程资源采样也不能直接归因到一个并行任务。', '']
    rows=[]
    for j in jobs:
        for r in j['runs']:
            z=r['resources'];rows.append([short[j['job_id']],r['run_id'],z.get('process_cpu_avg_pct'),z.get('process_rss_peak_mb'),z.get('thread_peak')])
    out += [table(['任务','run_id','进程平均 CPU %','RSS 峰值 MB','线程峰值'],rows),'',
            '## 复现与边界索引','',
            '运行 `python3 docs/performance-real-tasks-2026-10-08/audit.py` 重读两处原数据，生成 evidence.json、analysis.json、metrics.md。仅输出目录发生写入；不导入 Folith、不执行流水线、不发 API 请求。用 `--evidence <保存的 evidence.json> --out /tmp/folith-audit` 可在原日志变化后重现同一组数字。证据快照只保留审计需要的状态字段、计时、事件和技术日志，不复制源文/译文或密钥配置。', '',
            '每个任务的原始运行目录为 `<directory>/.performance/runs/<run_id>/`，读取 summary.json、events.jsonl、stages.json、resources.jsonl（后两者用于结构/资源核对，数值汇总取 summary 和嵌入 profiler）；事件时间线见 analysis.json.boundaries。逐批次原始数据保留在 evidence.json.state.profiler_stages。分位数重算采用排序后的 round(p*(n-1))（与项目 trace 相同）。', '']
    return '\n'.join(out)


def main():
    p=argparse.ArgumentParser();p.add_argument('--desktop',type=Path,default=Path.home()/'Library/Application Support/Folith/data')
    p.add_argument('--legacy',type=Path,default=Path(__file__).resolve().parents[2]/'outputs')
    p.add_argument('--evidence',type=Path);p.add_argument('--out',type=Path,default=Path(__file__).resolve().parent);a=p.parse_args()
    evidence=load(a.evidence) if a.evidence else collect([a.desktop,a.legacy]);analysis=[analyze(j) for j in evidence['business']+evidence['samples']]
    a.out.mkdir(parents=True,exist_ok=True)
    for name,payload in [('evidence.json',evidence),('analysis.json',analysis)]:
        (a.out/name).write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    (a.out/'metrics.md').write_text(render(evidence,analysis)+'\n',encoding='utf-8')
    print(json.dumps(dict(business_jobs=len(evidence['business']),samples=len(evidence['samples']),out=str(a.out)),ensure_ascii=False))


if __name__=='__main__':
    main()
