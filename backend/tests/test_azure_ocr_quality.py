"""Quality-path tests: resolution, native gate, crops, retry/fallback and spending guard."""
import base64
import io
import hashlib
import json
from pathlib import Path

import httpx
import pytest
from PIL import Image

from tests.test_azure_ocr import azure_env, complete, mock_api
from extractors.azure_ocr_extractor import AzureOCRExtractor, configuration, last_trace
from models.errors import AppError
from utils import deadline


def image_path(tmp_path):
    path = tmp_path / 'test.png'
    Image.new('RGB', (100, 100), 'white').save(path)
    return path


def test_render_uses_real_220_dpi_and_original_detail(monkeypatch, tmp_path):
    import pymupdf
    calls = mock_api(monkeypatch, complete())
    path = tmp_path / 'letter.pdf'
    with pymupdf.open() as doc:
        doc.new_page(width=612, height=792)
        doc.save(path)
    result = AzureOCRExtractor().extract(path)
    assert not result.errors
    content = calls[0][1]['json']['input'][0]['content']
    assert content[1]['detail'] == 'original'
    with Image.open(io.BytesIO(base64.b64decode(content[1]['image_url'].split(',', 1)[1]))) as rendered:
        assert rendered.height > 2300 and rendered.width > 1800
        assert rendered.width * rendered.height <= configuration()['pixels']


def test_huge_pdf_is_capped_before_raster_allocation(monkeypatch, tmp_path):
    import pymupdf
    calls = mock_api(monkeypatch, complete())
    path = tmp_path / 'large.pdf'
    with pymupdf.open() as doc:
        doc.new_page(width=10000, height=12000)
        doc.save(path)
    result = AzureOCRExtractor().extract(path)
    assert not result.errors
    encoded = calls[0][1]['json']['input'][0]['content'][1]['image_url'].split(',', 1)[1]
    with Image.open(io.BytesIO(base64.b64decode(encoded))) as image:
        assert max(image.size) <= configuration()['edge']
        assert image.width*image.height <= configuration()['pixels']


def test_clean_native_page_makes_no_cloud_request(monkeypatch, tmp_path):
    import pymupdf
    monkeypatch.setenv('AXTRACT_AZURE_MODE', 'hybrid')
    calls = mock_api(monkeypatch, complete())
    path = tmp_path / 'native.pdf'
    with pymupdf.open() as doc:
        p = doc.new_page()
        p.insert_textbox((72, 100, 500, 450), 'This is a clean native paragraph with faithful text extraction. ' * 4, fontsize=12)
        doc.save(path)
    result = AzureOCRExtractor().extract(path)
    assert not calls and not result.errors and result.blocks
    assert all(b.metadata['azure_native_reuse'] for b in result.blocks)
    assert all(b.extractor == 'pymupdf' for b in result.blocks)


def test_image_dominated_page_uses_cloud_in_hybrid(monkeypatch, tmp_path):
    import pymupdf
    monkeypatch.setenv('AXTRACT_AZURE_MODE', 'hybrid')
    calls = mock_api(monkeypatch, complete())
    image = image_path(tmp_path)
    path = tmp_path / 'scan.pdf'
    with pymupdf.open() as doc:
        p = doc.new_page(); p.insert_image(p.rect, filename=str(image))
        p.insert_text((72, 100), 'Hidden OCR is not reliable native evidence. '*5, fontsize=12)
        doc.save(path)
    assert AzureOCRExtractor().extract(path).blocks
    assert len(calls) == 1


def test_overview_and_detail_crops_share_one_request(monkeypatch, tmp_path):
    monkeypatch.setenv('AXTRACT_AZURE_CROPS', 'always')
    calls = mock_api(monkeypatch, complete())
    path = tmp_path / 'dense.png'; Image.new('RGB', (1000, 2000), 'white').save(path)
    result = AzureOCRExtractor().extract(path)
    assert len(calls) == 1 and len(result.blocks) == 1
    content = calls[0][1]['json']['input'][0]['content']
    assert len(content) == 4
    assert all(p['detail'] == 'original' for p in content[1:])


def test_transient_retry_then_success(monkeypatch, tmp_path):
    monkeypatch.setenv('AXTRACT_AZURE_MAX_RETRIES', '1')
    monkeypatch.setattr('extractors.azure_ocr_extractor.time.sleep', lambda _: None)
    calls = []
    def post(client, url, **kwargs):
        calls.append(kwargs)
        return httpx.Response(429 if len(calls)==1 else 200,
            json={} if len(calls)==1 else complete(), headers={'retry-after-ms':'0'}, request=httpx.Request('POST', url))
    monkeypatch.setattr(httpx.Client, 'post', post)
    result = AzureOCRExtractor().extract(image_path(tmp_path))
    assert not result.errors and len(calls) == 2
    assert last_trace()[0]['requests'][0]['error_code'] == 'CLOUD_OCR_TRANSIENT'


def test_timeout_is_recorded_precisely(monkeypatch, tmp_path):
    def post(*a, **kw):
        raise httpx.ReadTimeout('test-secret-never-log')
    monkeypatch.setattr(httpx.Client, 'post', post)
    result = AzureOCRExtractor().extract(image_path(tmp_path))
    assert result.errors[0].code == 'CLOUD_OCR_TIMEOUT'
    assert 'test-secret-never-log' not in result.errors[0].message
    assert len(last_trace()[0]['requests']) == 1


def test_incomplete_primary_uses_configured_fallback(monkeypatch, tmp_path):
    monkeypatch.setenv('AZURE_OPENAI_FALLBACK_DEPLOYMENT', 'my-fallback')
    calls = []
    def post(client, url, **kwargs):
        calls.append(kwargs['json'])
        payload = {'status':'incomplete', 'usage':{'input_tokens':10,'output_tokens':5}} if len(calls)==1 else complete()
        return httpx.Response(200, json=payload, request=httpx.Request('POST', url))
    monkeypatch.setattr(httpx.Client, 'post', post)
    result = AzureOCRExtractor().extract(image_path(tmp_path))
    assert not result.errors
    assert [x['model'] for x in calls] == ['my-nano-deployment','my-fallback']
    assert result.blocks[0].metadata['fallback_reason'] == 'CLOUD_OCR_INCOMPLETE'
    assert len(last_trace()[0]['requests']) == 2


def test_request_cap_includes_retries(monkeypatch, tmp_path):
    monkeypatch.setenv('AXTRACT_AZURE_MAX_RETRIES', '3')
    monkeypatch.setenv('AXTRACT_AZURE_MAX_REQUESTS_PER_PAGE', '1')
    monkeypatch.setattr('extractors.azure_ocr_extractor.time.sleep', lambda _: None)
    calls = mock_api(monkeypatch, {}, 429)
    result = AzureOCRExtractor().extract(image_path(tmp_path))
    assert len(calls) == 1
    assert result.errors[0].code == 'CLOUD_OCR_REQUEST_LIMIT'


def test_usd_cap_stops_before_request_when_insufficient(monkeypatch, tmp_path):
    monkeypatch.setenv('AXTRACT_AZURE_MODEL', 'gpt-5.4-mini')
    monkeypatch.setenv('AXTRACT_AZURE_MAX_USD_PER_PAGE', '0.00001')
    calls = mock_api(monkeypatch, complete())
    result = AzureOCRExtractor().extract(image_path(tmp_path))
    assert not calls and result.errors[0].code == 'CLOUD_OCR_COST_LIMIT'


def test_usd_cap_reduces_output_limit_and_records_usage(monkeypatch, tmp_path):
    monkeypatch.setenv('AXTRACT_AZURE_MODEL', 'gpt-5.4-mini')
    monkeypatch.setenv('AXTRACT_AZURE_MAX_USD_PER_PAGE', '0.005')
    calls = mock_api(monkeypatch, complete())
    result = AzureOCRExtractor().extract(image_path(tmp_path))
    assert not result.errors
    assert calls[0][1]['json']['max_output_tokens'] < configuration()['tokens']
    assert result.blocks[0].metadata['reserved_or_reported_usd'] < .005
    profile = result.blocks[0].metadata['azure_profile']
    assert 'key' not in profile and 'url' not in profile


def test_unknown_model_cannot_use_unverified_cost_cap(monkeypatch):
    monkeypatch.setenv('AXTRACT_AZURE_MODEL', 'unknown-model')
    monkeypatch.setenv('AXTRACT_AZURE_MAX_USD_PER_PAGE', '0.01')
    with pytest.raises(AppError, match='USD cap'):
        configuration()


def test_explicit_deadline_is_respected(monkeypatch, tmp_path):
    calls = mock_api(monkeypatch, complete())
    deadline.start(-1)
    result = AzureOCRExtractor().extract(image_path(tmp_path))
    assert not calls and result.errors[0].code == 'TIMEOUT'


def test_cloud_deadline_does_not_modify_local_or_vlm(monkeypatch):
    from services.parse_service import _cloud_deadline
    calls = []
    monkeypatch.setattr(deadline, 'start', lambda value: calls.append(value))
    monkeypatch.setenv('AXTRACT_AZURE_DOCUMENT_SECONDS', '180')
    _cloud_deadline('pdf')
    assert calls == [180]
    monkeypatch.setenv('AXTRACT_OCR_PROVIDER', 'local')
    monkeypatch.setenv('AXTRACT_VLM_OCR', 'all')
    _cloud_deadline('pdf')
    assert calls == [180]


@pytest.mark.parametrize('name,value', [('AXTRACT_AZURE_RENDER_DPI','nan'), ('AXTRACT_AZURE_MAX_RETRIES','4'), ('AXTRACT_AZURE_DETAIL','bad')])
def test_invalid_quality_settings_rejected(monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    with pytest.raises(AppError):
        configuration()


def test_diagnostic_selection_is_fixed_and_balanced(tmp_path, monkeypatch):
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]/'scripts'))
    import azure_benchmark as ab
    import frozen70_benchmark as frozen
    cases, pdfs = [], {}
    for cat in ['arxiv_math','headers_footers','long_tiny_text','multi_column','old_scans','old_scans_math','tables']:
        for i in range(3):
            rel = f'{cat}/{i}.pdf'
            p = tmp_path/'pdfs'/rel; p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(f'page {i}'.encode())
            pdfs[rel] = hashlib.sha256(p.read_bytes()).hexdigest()
            cases.append(dict(id=f'{cat}-{i}', pdf=rel, page=1, type='present', text='page'))
    assertion = tmp_path/'cases.jsonl'
    assertion.write_text('\n'.join(json.dumps(c) for c in cases), encoding='utf8')
    monkeypatch.setattr(frozen, 'manifest', lambda: {'pdfs':pdfs, 'dataset':{'case_source_sha256':{'cases.jsonl':hashlib.sha256(assertion.read_bytes()).hexdigest()}}})
    selected, subset = ab.selection(tmp_path, '14')
    assert len(selected) == len(subset) == 14
    assert all(sum(p.startswith(cat+'/') for p in selected)==2 for cat in {c['pdf'].split('/')[0] for c in cases})
    assert ab.selection(tmp_path, '14') == (selected, subset)


def test_quality_checks_detect_omissions_repetition_and_broken_math():
    from extractors.azure_ocr_extractor import _quality_flags
    reference = ' '.join(['faithful original document transcription preserves important scientific equations']*15)
    assert 'native_text_coverage_low' in _quality_flags('A short summary.', {'native_reference':reference})
    paragraph = 'This entire long passage should be transcribed once and must not be repeated repeatedly in the generated document.'
    assert 'repeated_long_passage' in _quality_flags('\n\n'.join([paragraph]*3), {})
    assert 'unbalanced_latex_environment' in _quality_flags(r'$$\begin{aligned}x=1$$', {})
    assert 'unbalanced_display_math' in _quality_flags('$$x=1', {})
    assert not _quality_flags(r'$$\begin{aligned}x&=1\end{aligned}$$', {})


def test_invisible_native_text_never_used_as_hint_or_direct_output(tmp_path):
    import pymupdf
    from extractors.azure_ocr_extractor import _visible_native_text
    path = tmp_path/'hidden.pdf'
    with pymupdf.open() as doc:
        page = doc.new_page()
        page.insert_text((72,100), 'This hidden source must not be copied. '*5, render_mode=3)
        doc.save(path)
    with pymupdf.open(path) as doc:
        assert _visible_native_text(doc[0]) == ''
        assert AzureOCRExtractor()._native_page(doc[0], 1) is None


def test_quality_reread_keeps_first_output_when_budget_exhausted(monkeypatch, tmp_path):
    monkeypatch.setenv('AXTRACT_AZURE_MAX_REQUESTS_PER_PAGE', '1')
    calls = mock_api(monkeypatch, complete('$$ x = 1'))
    result = AzureOCRExtractor().extract(image_path(tmp_path))
    assert not result.errors and result.blocks[0].content == '$$ x = 1'
    assert len(calls) == 1
    assert result.blocks[0].metadata['fallback_reason'] == 'quality_reread_unavailable:CLOUD_OCR_REQUEST_LIMIT'


def test_auto_crops_only_when_detail_would_otherwise_be_lost(monkeypatch):
    from extractors.azure_ocr_extractor import _views
    config = dict(configuration(), crops='auto', dense=True)
    with Image.new('RGB', (1000,2000)) as image:
        views = _views(image,config)
        assert len(views) == 1
        for view in views: view.close()
    with Image.new('RGB',(4000,4000)) as image:
        views = _views(image,config)
        assert len(views) == 3
        for view in views: view.close()


def test_scorer_preflight_rejects_missing_interpreter_before_paid_run(tmp_path):
    import sys
    from types import SimpleNamespace
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]/'scripts'))
    import azure_benchmark as ab
    args = SimpleNamespace(scorer_python=tmp_path/'missing.exe', scorer=tmp_path/'scorer')
    with pytest.raises(SystemExit, match='interpreter not found'):
        ab.verify_scorer(args)


def test_recovery_budget_guard_runs_before_output_or_network(tmp_path, monkeypatch):
    import sys,os
    from types import SimpleNamespace
    from unittest.mock import patch
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]/'scripts'))
    import azure_benchmark as ab
    source = tmp_path/'first'; source.mkdir()
    profile = {'scope':'70-page diagnostic','azure':{'deployment':'my-nano-deployment'},
        'code_sha256':{name:hashlib.sha256((ab.ROOT/name).read_bytes()).hexdigest() for name in
            ['backend/extractors/azure_ocr_extractor.py','backend/extractors/azure_ocr_options.py']}}
    (source/'azure_profile.json').write_text(json.dumps(profile),encoding='utf8')
    (source/'performance.json').write_text(json.dumps({'reserved_or_reported_usd':1.45,'documents':[{'pdf':'a.pdf','success':False},{'pdf':'b.pdf','success':False}]}),encoding='utf8')
    monkeypatch.setattr(ab,'selection',lambda *a:None)
    args = SimpleNamespace(source=source,out=tmp_path/'recovered',scorer=None,scorer_python=None,
                           page_cap_usd=.075,max_usd=1.50,data_root=tmp_path)
    with patch.dict(os.environ, {}, clear=False):
        with pytest.raises(SystemExit, match='reservation exceeds'):
            ab.recover(args)
    assert not args.out.exists()


def test_table_marker_normalization_preserves_math_and_prose():
    from extractors.azure_ocr_extractor import _normalize_table_markers
    source = "Prose $^{*}$\n| 0.31$^{*}$ | $x^{2}$ |\n"
    assert _normalize_table_markers(source) == "Prose $^{*}$\n| 0.31* | $x^{2}$ |\n"


def test_layout_prompt_and_explicit_reasoning(monkeypatch, tmp_path):
    monkeypatch.setenv('AXTRACT_AZURE_PROMPT', 'layout')
    monkeypatch.setenv('AXTRACT_AZURE_REASONING', 'low')
    calls = mock_api(monkeypatch, complete())
    result = AzureOCRExtractor().extract(image_path(tmp_path))
    assert not result.errors
    payload = calls[0][1]['json']
    assert payload['reasoning'] == {'effort': 'low'}
    assert 'rectangular Markdown grids' in payload['input'][0]['content'][0]['text']
