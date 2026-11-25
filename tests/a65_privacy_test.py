"""A-65: reviewer role for manual review; project-local cache clearing."""
from __future__ import annotations

from folith.qt import adapter, support


def test_manual_review_uses_reviewer_connection(monkeypatch):
    cfg = adapter.EngineConfig(provider='TranslateCo', model='t', api_key='TRANS-KEY',
                               base_url='http://translate.local/v1')
    cfg.reviewer_mode = 'separate'
    cfg.reviewer_provider = 'ReviewCo'
    cfg.reviewer_model = 'r'
    cfg.reviewer_api_key = 'REVIEW-KEY'
    cfg.reviewer_base_url = 'http://review.local/v1'
    seen = {}

    def fake_review(job_id, indexes, provider, api_key, model, target_lang, **kw):
        seen.update(provider=provider, api_key=api_key, model=model, base_url=kw.get('base_url'))
        return ({}, {})

    monkeypatch.setattr(adapter.core, 'review_translation_segments', fake_review)
    monkeypatch.setattr(adapter.core, 'load_job_state', lambda _j: {
        'pairs': [{'source': 's', 'target': 't'}]})
    adapter.review_segments('job', [0], cfg)
    assert seen == {'provider': 'ReviewCo', 'api_key': 'REVIEW-KEY',
                    'model': 'r', 'base_url': 'http://review.local/v1'}


def test_clear_caches_includes_projects(tmp_path, monkeypatch):
    monkeypatch.setattr(adapter.core, 'OUTPUT_DIR', tmp_path)
    from folith import project as project_paths
    project = project_paths.project_dir(tmp_path, 'alpha')
    project.mkdir(parents=True)
    (tmp_path / 'translation_cache.json').write_text('{}')
    (project / 'translation_cache.json').write_text('{}')
    (project / 'knowledge_cache.json').write_text('{}')
    (project / 'tm.json').write_text('{}')
    (project / 'body.txt').write_text('keep')
    support.clear_caches()
    assert not (tmp_path / 'translation_cache.json').exists()
    assert not (project / 'translation_cache.json').exists()
    assert not (project / 'knowledge_cache.json').exists()
    assert (project / 'tm.json').read_text() == '{}'
    assert (project / 'body.txt').read_text() == 'keep'


def test_local_gateway_reviewer_keeps_explicit_url(monkeypatch):
    cfg = adapter.EngineConfig(provider='TranslateCo', model='t', api_key='K',
                               base_url='http://translate.local/v1')
    cfg.reviewer_mode = 'separate'
    cfg.reviewer_provider = 'Local Gateway'
    cfg.reviewer_model = 'local-r'
    cfg.reviewer_api_key = 'LOCAL-KEY'
    cfg.reviewer_base_url = 'http://gateway.local/v1'
    seen = {}

    def fake_review(job_id, indexes, provider, api_key, model, target_lang, **kw):
        seen.update(provider=provider, api_key=api_key, base_url=kw.get('base_url'))
        return ({}, {})

    monkeypatch.setattr(adapter.core, 'review_translation_segments', fake_review)
    monkeypatch.setattr(adapter.core, 'load_job_state', lambda _j: {
        'pairs': [{'source': 's', 'target': 't'}]})
    adapter.review_segments('job', [0], cfg)
    assert seen['provider'] == 'Local Gateway'
    assert seen['api_key'] == 'LOCAL-KEY'
    assert seen['base_url'] == 'http://gateway.local/v1'
