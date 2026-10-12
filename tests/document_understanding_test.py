"""Provider/geometry contracts: fake service responses, real PDFs and persistence."""
import json
from dataclasses import asdict
from types import SimpleNamespace

import fitz
import pytest

import core
from folith import pdf_preview
from folith.document_ir import PageEnvelope, PageUnderstanding, SemanticBlock, SourceSpan, normalized_bbox
from folith.document_understanding import VLMDocumentProvider, decode_semantics, understand_visual_pdf
from folith.ocr_online import OnlineOCRConfig
from folith.spatial_index import SpatialRegion, SpatialTextIndex, align_blocks


def page():
    return PageEnvelope('doc', 'p2', 1, 400, 600, image=b'png')


def index(lines):
    return SpatialTextIndex('p2', [SpatialRegion(str(i+1), [.1, .1+i*.1, .9, .15+i*.1], t, i)
                                  for i,t in enumerate(lines)])


def response(text, finish='stop'):
    return SimpleNamespace(id='req', model='vision', usage=SimpleNamespace(model_dump=lambda: {
        'prompt_tokens':100, 'completion_tokens':20, 'total_tokens':120}), choices=[
        SimpleNamespace(finish_reason=finish, message=SimpleNamespace(content=text))])


def fake_client(reply):
    calls = []
    def create(**body):
        calls.append(body)
        return reply
    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))), calls


def config():
    return OnlineOCRConfig(provider='fixture', model='vision', base_url='https://fixture.invalid/v1',
                           api_key='fixture-secret', max_retries=0)


def scanned_pdf(pages=2, rotation=0):
    native = fitz.open()
    native.new_page(width=400,height=600).insert_text((40,80),'The sensorium of the drone.')
    image = native[0].get_pixmap().tobytes('png')
    scan = fitz.open()
    for _ in range(pages):
        p = scan.new_page(width=400,height=600)
        p.insert_image(p.rect,stream=image)
        p.set_rotation(rotation)
    data = scan.tobytes()
    native.close(); scan.close()
    return data


@pytest.mark.parametrize('box', [[0,0,0,1],[-.1,0,1,1],[0,0,2,1],[0,0,float('nan'),1]])
def test_invalid_boxes_abstain(box):
    assert normalized_bbox(box) is None
    with pytest.raises(ValueError):
        SourceSpan('p1',bbox=box)


def test_fuzzy_multiple_lines_preserves_semantic_text():
    block = SemanticBlock('paragraph','The sensorium of the drone')
    spans = align_blocks(page(), [block], index(['The sensoriurn of','the drone']))[0]
    assert len(spans) == 2
    assert all(s['bbox'] and s['grounding_quality'] == 'approximate' for s in spans)
    assert block.text == 'The sensorium of the drone'


def test_rtl_normalization():
    block = SemanticBlock('paragraph','هذا نص عربي سليم',direction='rtl')
    spans = align_blocks(page(),[block],index(['ﻫﺬﺍ ﻧﺺ ﻋﺮﺑﻲ ﺳﻠﻴﻢ']))[0]
    assert spans[0]['grounding_quality'] == 'strong'


def test_ambiguous_and_poor_scan_are_page_only():
    for lines in [['unreadable'],['Repeated paragraph text','other','Repeated paragraph text']]:
        spans = align_blocks(page(),[SemanticBlock('paragraph','Repeated paragraph text')],index(lines))[0]
        assert spans == [asdict(SourceSpan('p2'))]


def test_vlm_identity_owned_by_envelope_and_no_coordinates_requested():
    client, calls = fake_client(response('{"b":[{"t":"heading","s":"Chapter 3"}]}'))
    result = VLMDocumentProvider(config(),client=client).understand_page(page())
    assert result.blocks[0].text == 'Chapter 3'
    user = calls[0]['messages'][1]['content']
    assert user[0]['type'] == 'image_url'
    assert 'p2' not in str(calls[0]) and 'document_id' not in str(calls[0])
    assert result.diagnostics['usage']['prompt_tokens'] == 100
    assert 'fixture-secret' not in json.dumps(result.diagnostics)


@pytest.mark.parametrize('raw,finish', [('bad','stop'),('{"b":[]}','length'),
 ('{"b":[{"t":"table","s":"flat markdown"}]}','stop'),
 ('{"b":[{"t":"paragraph","s":""}]}','stop')])
def test_bad_or_truncated_response_is_failure(raw,finish):
    client,_ = fake_client(response(raw,finish))
    with pytest.raises(ValueError):
        VLMDocumentProvider(config(),client=client).understand_page(page())


def test_explicit_blank_page_accepted():
    client,_ = fake_client(response('{"b":[]}'))
    assert VLMDocumentProvider(config(),client=client).understand_page(page()).blocks == []


class Provider:
    def __init__(self, blocks=None):
        self.blocks = blocks or [SemanticBlock('paragraph','The sensorium of the drone. A second sentence.')]
    def understand_page(self, envelope):
        assert envelope.image.startswith(b'\x89PNG')
        return PageUnderstanding(self.blocks,'fixture-vlm')


def test_geometry_failure_never_blocks_semantics_or_page_navigation(tmp_path,monkeypatch):
    monkeypatch.setattr(core,'OUTPUT_DIR',tmp_path)
    def broken(_):
        raise RuntimeError('no OCR language pack')
    data = scanned_pdf()
    paragraphs, warnings, report = core.extract_document_paragraphs_with_report(
        'scan.pdf',data,ocr_max_pages=None,understanding_provider=Provider(),spatial_engine=broken)
    assert len(paragraphs) == 2 and report['semantic_source'] == 'vlm'
    state = core.new_job_state('scan.pdf')
    state['extraction_report'] = report
    segments,_ = core._apply_source_segmentation(state,paragraphs)
    state['paras'] = segments
    core.save_source('grounding',data)
    core.save_job_state('grounding',state)
    assert pdf_preview.get_cached_segment_location('grounding',len(segments)-1,segments[-1]) == (1,[])
    assert report['page_diagnostics'][0]['spatial_error'] == 'no OCR language pack'
    assert report['document_ir']['pages'][1]['page_id'] == 'p2'


def test_table_cells_survive_and_translate_as_nodes():
    blocks=decode_semantics('{"b":[{"t":"table","s":"","c":[{"r":0,"c":0,"s":"Name"},{"r":0,"c":1,"s":"Value"}]}]}')
    paragraphs,document=understand_visual_pdf('table.pdf',scanned_pdf(1),Provider(blocks))
    assert paragraphs == ['Name','Value']
    table=next(b for b in document['blocks'] if b['type']=='table')
    assert [c['col'] for c in table['cells']] == [0,1]
    assert all(next(b for b in document['blocks'] if b['block_id']==bid)['parent_id']==table['block_id']
               for bid in document['translation_block_ids'])


def test_persisted_multi_page_spans_merge_split_undo(tmp_path,monkeypatch):
    monkeypatch.setattr(core,'OUTPUT_DIR',tmp_path)
    state=core.new_job_state('scan.pdf')
    data=scanned_pdf()
    paragraphs,document=understand_visual_pdf('scan.pdf',data,Provider())
    state['extraction_report']={'document_ir':document}
    state['paras'],_=core._apply_source_segmentation(state,paragraphs,mode='paragraph')
    state['pairs']=[{'source':s,'target':'译文','reviewed':False} for s in state['paras']]
    core.assign_missing_segment_uids(state,job_id='spans')
    core.save_source('spans',data);core.save_job_state('spans',state)
    core.mutate_translation_segments('spans',0,'merge')
    loaded=core.load_job_state('spans')
    assert [s['page_id'] for s in loaded['pairs'][0]['source_spans']] == ['p1','p2']
    assert pdf_preview.get_segment_source_locations('spans',0) == [(0,[]),(1,[])]
    core.undo_translation_segment_structure('spans')
    assert len(core.load_job_state('spans')['pairs']) == 2
    core.mutate_translation_segments('spans',0,'split',source_a='The sensorium.',source_b='The drone.')
    loaded=core.load_job_state('spans')
    assert loaded['pairs'][0]['source_spans'] == loaded['pairs'][1]['source_spans']
    assert pdf_preview.get_segment_source_locations('spans',2) == [(1,[])]


def test_rotated_scan_geometry_is_unrotated():
    paragraphs,document=understand_visual_pdf('rotated.pdf',scanned_pdf(1,90),Provider())
    assert document['pages'][0]['width']==400
    assert document['pages'][0]['height']==600
    assert document['pages'][0]['rotation']==90


def test_checkpoint_reuses_semantics_but_rebuilds_spatial_grounding(tmp_path):
    class CountingProvider(Provider):
        cache_identity = {'model':'fixture','prompt':'v1'}
        calls = 0
        def understand_page(self,envelope):
            self.calls += 1
            return super().understand_page(envelope)
    provider=CountingProvider()
    data=scanned_pdf(1)
    paragraphs,first=understand_visual_pdf('scan.pdf',data,provider,checkpoint_dir=tmp_path)
    assert provider.calls==1
    def spatial(envelope):
        return SpatialTextIndex(envelope.page_id,[SpatialRegion('1',[.1,.1,.9,.2],paragraphs[0],0)])
    _,second=understand_visual_pdf('scan.pdf',data,provider,checkpoint_dir=tmp_path,spatial_engine=spatial)
    assert provider.calls==1
    assert second['provenance']['page_diagnostics'][0]['cache_hit']
    assert second['provenance']['page_diagnostics'][0]['usage'] is None
    assert second['blocks'][0]['source_anchor']['source_spans'][0]['bbox']
    provider.cache_identity={'model':'fixture','prompt':'v2'}
    understand_visual_pdf('scan.pdf',data,provider,checkpoint_dir=tmp_path)
    assert provider.calls==2


def test_failed_page_preserves_request_accounting(tmp_path):
    client,_=fake_client(response('{"b":[]}',finish='length'))
    provider=VLMDocumentProvider(config(),client=client)
    paras,_,report=core.extract_document_paragraphs_with_report(
        'scan.pdf',scanned_pdf(1),understanding_provider=provider,
        spatial_engine=lambda p:SpatialTextIndex(p.page_id),checkpoint_dir=tmp_path)
    assert not paras and report['status']=='failed'
    assert report['page_diagnostics'][0]['usage']['prompt_tokens']==100
    assert report['page_diagnostics'][0]['attempt_count']==1
    assert (tmp_path/'document_understanding'/'0-failure.json').exists()
    assert not (tmp_path/'document_understanding'/'0.json').exists()


def test_real_relay_fenced_payload_is_normalized():
    from pathlib import Path
    # Freeze the real failure shape without depending on benchmark files.
    result=decode_semantics('```json\n{"b":[{"t":"paragraph","s":"هذا نص عربي واضح","l":"ar","d":"rtl"}]}\n```')
    assert result[0].direction=='rtl'


def test_default_scanned_runtime_uses_current_vision_connection_without_repair(tmp_path,monkeypatch):
    from offline_provider import OfflineProvider
    from folith.ocr_online import OnlineOCRBackend
    monkeypatch.setattr(core,'OUTPUT_DIR',tmp_path)
    monkeypatch.setattr(core,'call_llm',OfflineProvider())
    monkeypatch.setattr(core._LLM_CTX,'base_url',None,raising=False)
    client,_=fake_client(response('{"b":[{"t":"paragraph","s":"The sensorium of the drone."}]}'))
    monkeypatch.setattr(OnlineOCRBackend,'_get_client',lambda _:client)
    state=core.run_job_pipeline('default-vlm','scan.pdf',scanned_pdf(1),
        provider='OpenCode Go',api_key='fixture-secret',model='deepseek-v4.1-flash',
        translator_base_url='https://fixture.invalid/v1',target_lang='简体中文',
        auto_term=False,enable_report=False,translation_theory='',enable_review=False,
        enable_annotate=False,use_tm=False,enable_understanding=False)
    assert state['pipeline_config']['ocr_backend']=='online'
    assert state['extraction_report']['semantic_source']=='vlm'
    assert state['source_cleanup']['reason']=='vlm_semantics_no_ocr_repair'
    assert state['p2_done'] and state['pairs'][0]['source_spans'][0]['page_id']=='p1'
    assert 'fixture-secret' not in core.job_state_path('default-vlm').read_text()


def test_new_default_preserves_legacy_selection_and_native_no_credentials(monkeypatch):
    monkeypatch.setattr(core,'load_provider_config',lambda:None)
    monkeypatch.setattr(core,'load_ocr_online_config',lambda:None)
    assert core._effective_ocr_source(None)[0]=='online'
    assert core._effective_ocr_source(None,{'ocr_backend':'tesseract'},prefer_saved=True)[0]=='tesseract'
    pdf=fitz.open();p=pdf.new_page();p.insert_text((40,80),
        'This is a native structured paragraph with enough text to pass the quality gate.')
    data=pdf.tobytes();pdf.close()
    paras,_,report=core.extract_document_paragraphs_with_report('native.pdf',data)
    assert paras and report['semantic_source']=='native_pdf'
    assert not report.get('page_diagnostics')


def test_mixed_pdf_keeps_native_and_scanned_pages_in_order():
    native=fitz.open()
    for i in range(3):
        p=native.new_page(width=400,height=600)
        if i!=1:
            p.insert_text((40,80),('First native paragraph has ordinary text for extraction.' if i==0 else 'Final native paragraph keeps its original position.'))
        else:
            with fitz.open(stream=scanned_pdf(1),filetype='pdf') as scan:
                p.insert_image(p.rect,stream=scan[0].get_pixmap().tobytes('png'))
    data=native.tobytes();native.close()
    paras,_,report=core.extract_document_paragraphs_with_report('mixed.pdf',data,
        ocr_max_pages=None,understanding_provider=Provider(),spatial_engine=lambda p:SpatialTextIndex(p.page_id))
    assert report['semantic_source']=='mixed'
    assert len(paras)==3 and paras[0].startswith('First native paragraph')
    assert paras[1].startswith('The sensorium') and paras[2].startswith('Final native paragraph')
    document=report['document_ir'];blocks={b['block_id']:b for b in document['blocks']}
    assert len(blocks)==len(document['blocks'])
    assert [blocks[bid]['source_anchor']['source_spans'][0]['page_id']
            for bid in document['translation_block_ids']]==['p1','p2','p3']


def _with_invisible_ocr_layer(data, text):
    """Stamp the hidden text layer a scanner / Internet Archive puts on each page."""
    doc=fitz.open(stream=data,filetype='pdf')
    for page in doc:
        for row in range(6):
            page.insert_text((40,80+row*16),text,fontsize=11,render_mode=3)
    out=doc.tobytes();doc.close()
    return out


def test_scan_with_invisible_ocr_layer_goes_to_vlm_not_the_text_layer():
    # 实测：扫描小说自带的隐形 OCR 层通过了文本层质量检测，303/308 页按原生文本
    # 走了老的拼行规则，VLM 只接手了 5 张空白图片页。
    data=_with_invisible_ocr_layer(scanned_pdf(2),'Tbe sensorlum of tbe drone, garbled by someone else OCR.')
    paras,warnings,report=core.extract_document_paragraphs_with_report('scan.pdf',data,
        ocr_max_pages=None,understanding_provider=Provider(),spatial_engine=lambda p:SpatialTextIndex(p.page_id))
    assert report['semantic_source']=='vlm' and report['ocr_trigger']=='scanned_ocr_overlay'
    assert paras and all(p.startswith('The sensorium') for p in paras)
    assert any('隐形 OCR 层' in w for w in warnings)


def test_ocr_overlay_page_inside_a_native_pdf_is_routed_as_a_scanned_page():
    native=fitz.open()
    p=native.new_page(width=400,height=600)
    p.insert_text((40,80),'First native paragraph has ordinary text for extraction.')
    with fitz.open(stream=_with_invisible_ocr_layer(scanned_pdf(1),'Tbe sensorlum.'),filetype='pdf') as scan:
        native.insert_pdf(scan)
    data=native.tobytes();native.close()
    paras,_,report=core.extract_document_paragraphs_with_report('mixed.pdf',data,
        ocr_max_pages=None,understanding_provider=Provider(),spatial_engine=lambda p:SpatialTextIndex(p.page_id))
    assert report['semantic_source']=='mixed'
    assert paras[0].startswith('First native paragraph') and paras[1].startswith('The sensorium')


def test_page_only_never_claims_a_rectangle():
    with pytest.raises(ValueError):
        SourceSpan('p1',bbox=[.1,.1,.9,.2],grounding_quality='page_only')


def test_optional_language_direction_may_be_null():
    block=decode_semantics('{"b":[{"t":"paragraph","s":"Visible content","l":null,"d":null}]}')[0]
    assert block.language is None and block.direction is None
