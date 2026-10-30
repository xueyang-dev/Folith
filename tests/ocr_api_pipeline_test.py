"""End-to-end OCR API page warnings survive extraction and cache reuse."""
import io
import fitz
from PIL import Image
import core
from folith.ocr_api_backend import OcrApiBackend
from folith.ocr_online import OnlineOCRConfig


def scanned_pdf():
    doc = fitz.open()
    for shade in (0.2, 0.4, 0.6, 1.0):
        page = doc.new_page(width=80, height=100)
        page.draw_rect(page.rect, color=None, fill=(shade, shade, shade))
    data = doc.tobytes()
    doc.close()
    return data


def test_ocr_warnings_reach_extraction_report_and_cached_pages(tmp_path, monkeypatch):
    backend = OcrApiBackend(OnlineOCRConfig(provider='test', model='PaddleOCR',
                                           api_key='test', base_url='https://example.test'))
    monkeypatch.setattr(core._ocr_backends, 'resolve_backend', lambda *a, **k: (backend, []))
    def read_image(png, **kwargs):
        pixel = Image.open(io.BytesIO(png)).convert('L').getpixel((30, 30))
        context = kwargs.get('context')
        if context is not None:
            context['request'] = {'status': 'completed', 'attempt_count': 1}
        if pixel > 240:
            return [], 'empty_response: blank'
        text = ('本文その一。\n本文その二。\n' + '以下余白\n' * 8
                if pixel < 80 else f'独立した本文 {pixel}。')
        return kwargs['decoder'](text), None
    monkeypatch.setattr(backend, 'read_image', read_image)
    source = scanned_pdf()
    text, warnings, details = core._ocr_pdf_text_with_warnings(
        source, backend='ocr_api', workers=4, checkpoint_dir=tmp_path,
        ocr_language='jpn', return_details=True)
    assert '本文その一。' in text and '本文その二。' in text
    assert details['review_pages'] == [1]
    assert details['blank_pages'] == [4]
    assert any('第 1 页' in warning and '循环' in warning for warning in warnings)
    assert any('第 4 页' in warning and '空白' in warning for warning in warnings)
    paragraphs, _, report = core.extract_document_paragraphs_with_report(
        'scan.pdf', source, ocr_mode='force', ocr_backend='ocr_api',
        ocr_language='jpn', source_language='日本語', checkpoint_dir=tmp_path,
        ocr_workers=4)
    assert paragraphs
    assert any('第 1 页' in warning and '循环' in warning for warning in report['warnings'])
    assert any('第 4 页' in warning and '空白' in warning for warning in report['warnings'])


def test_table_with_repeated_cells_is_complete_and_not_flagged(tmp_path, monkeypatch):
    backend = OcrApiBackend(OnlineOCRConfig(provider='test', model='PaddleOCR',
                                           api_key='test', base_url='https://example.test'))
    monkeypatch.setattr(core._ocr_backends, 'resolve_backend', lambda *a, **k: (backend, []))
    table = '第一表\n' + '同上\n' * 6 + '合計 三十石'
    def read_image(png, **kwargs):
        pixel = Image.open(io.BytesIO(png)).convert('L').getpixel((30, 30))
        context = kwargs.get('context')
        if context is not None:
            context['request'] = {'status': 'completed', 'attempt_count': 1}
        if pixel < 80:
            return kwargs['decoder'](table), None
        if pixel < 120:
            return kwargs['decoder']('本文\n' + '以下余白\n' * 8), None
        return kwargs['decoder'](f'独立した本文 {pixel}。'), None
    monkeypatch.setattr(backend, 'read_image', read_image)
    text, warnings, details = core._ocr_pdf_text_with_warnings(
        scanned_pdf(), backend='ocr_api', workers=4, checkpoint_dir=tmp_path,
        ocr_language='jpn', return_details=True)
    assert '合計 三十石' in text and text.count('同上') == 6
    assert details['review_pages'] == [2]
    assert any('第 2 页' in warning and '循环' in warning for warning in warnings)
    assert not any('第 1 页' in warning for warning in warnings)


def test_a_failed_page_gets_its_own_warning_line(tmp_path, monkeypatch):
    from folith import runtime_events_view as view
    backend = OcrApiBackend(OnlineOCRConfig(provider='test', model='PaddleOCR',
                                           api_key='test', base_url='https://example.test'))
    monkeypatch.setattr(core._ocr_backends, 'resolve_backend', lambda *a, **k: (backend, []))
    def read_image(png, **kwargs):
        pixel = Image.open(io.BytesIO(png)).convert('L').getpixel((30, 30))
        context = kwargs.get('context')
        if context is not None:
            context['request'] = {'status': 'completed', 'attempt_count': 1}
            context['response_text'] = '一、目次\\n二、目次\\n三、目次'
        if pixel < 80:
            return [], 'truncated: cut'
        return kwargs['decoder'](f'独立した本文 {pixel}。'), None
    monkeypatch.setattr(backend, 'read_image', read_image)
    messages = []
    core._ocr_pdf_text_with_warnings(
        scanned_pdf(), backend='ocr_api', workers=4, checkpoint_dir=tmp_path,
        ocr_language='jpn', return_details=True, on_progress=messages.append)
    failure_lines = [m for m in messages if '未能识别' in m]
    assert failure_lines == ['【阶段一】第 1 页未能识别，已跳过：OCR 输出过长被截断']
    assert view.message_status('ocr', failure_lines[0]) == 'warning'
    tallies = [m for m in messages if '失败 1 页' in m]
    assert tallies and all(view.message_status('ocr', m) == 'running' for m in tallies)
