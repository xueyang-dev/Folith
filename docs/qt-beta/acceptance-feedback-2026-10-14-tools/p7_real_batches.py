"""Opt-in live-model smoke probe: six requests, synthetic text, no retries.
Usage (only with user authorization): venv/bin/python <script> <report.json>
Reads the saved desktop connection without printing or copying credentials.
Tests the real translation prompt and response parser, not the full pipeline.
"""
import json
import os
import sys
import tempfile
import time
from pathlib import Path
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from folith.desktop.paths import resolve_paths
os.environ['FOLITH_OUTPUT_DIR'] = str(resolve_paths().data_dir)
from folith.qt.adapter import EngineConfig
import core
from folith import llm_transport, translation_protocol
from folith.application.task_setup import STRATEGY_PRESETS, batch_params
config = EngineConfig.load()
assert config.provider and config.model and config.api_key, 'No configured desktop model'
scratch = Path(tempfile.mkdtemp(prefix='folith-live-batches-'))
core.OUTPUT_DIR = scratch
os.environ.update(FOLITH_OUTPUT_DIR=str(scratch), FOLITH_DESKTOP_LOG_DIR=str(scratch / 'logs'))
llm_transport.OPENAI_MAX_RETRIES = 0
llm_transport.OPENAI_TIMEOUT = (10.0, 45.0)
core._LLM_CTX.base_url = config.base_url
core._LLM_CTX.reasoning_effort = config.reasoning_effort
records = []
class OneRequestOnly(BaseException):
    pass
for sample, sentences in enumerate((1, 3, 6)):
    texts = []
    for i in range(6):
        sentence = (f'Reader {i+1} visited the city library in {2020+i}. '
                    'The archivist explained how careful reading connects a familiar word to its context. ')
        texts.append((sentence * sentences).strip())
    for name, count in [('标准',4), ('快速',6)]:
        batch = texts[:count]
        record = {'mode':name, 'sample':sample+1, 'segments':count,
                  'source_chars':sum(map(len,batch)), 'requests':0}
        items_limit, chars_limit = batch_params(STRATEGY_PRESETS[name])
        record['within_profile'] = count <= items_limit and record['source_chars'] <= chars_limit
        def request(*args, **kwargs):
            if record['requests']:
                raise OneRequestOnly()
            record['requests'] += 1
            start = time.perf_counter()
            try:
                response = core.call_llm(*args, **kwargs)
                try:
                    parsed = translation_protocol.parse_translation_response(response, count)
                    record.update(outcome='ok', returned_items=len(parsed))
                except translation_protocol.TranslationProtocolError as error:
                    record.update(outcome='protocol_error', code=error.code)
                return response
            except Exception as error:
                record.update(outcome='transport_error', error_type=type(error).__name__)
                raise OneRequestOnly() from None
            finally:
                record['seconds'] = round(time.perf_counter()-start, 2)
        try:
            core.translate_batch(batch, [], [], '', '', '简体中文',
                                 config.provider, config.api_key, config.model,
                                 call_llm_fn=request)
        except OneRequestOnly:
            pass
        records.append(record)
report = {'model':config.model, 'request_limit':6, 'requests':sum(r['requests'] for r in records),
          'synthetic_only':True, 'sdk_retries':0, 'records':records}
path = Path(sys.argv[1])
path.parent.mkdir(parents=True, exist_ok=True)
path.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
print(json.dumps(report,ensure_ascii=False))
