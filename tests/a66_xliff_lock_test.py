"""A-66/CX-03: excluded segments must not shift sub-segment lock attribution."""
from __future__ import annotations

import core

NS = 'urn:oasis:names:tc:xliff:document:1.2'


def _fixture():
    segment_sources = ('<seg-source><mrk mtype="seg" mid="1">one</mrk> '
                       '<mrk mtype="seg" mid="2">two</mrk></seg-source>')
    segment_targets = ('<target><mrk mtype="seg" mid="1">un</mrk> '
                       '<mrk mtype="seg" mid="2">deux</mrk></target>')
    decl = '<v:seg-defs><v:seg id="1" locked="true"/><v:seg id="2"/></v:seg-defs>'
    skip = '<trans-unit id="skip" translate="no"><source>not for translation</source><target>ignore</target></trans-unit>'
    partial = (f'<trans-unit id="partial"><source>one two</source>{segment_sources}'
               f'{segment_targets}{decl}</trans-unit>')
    ordinary = '<trans-unit id="ordinary"><source>ordinary</source><target>ordinaire</target></trans-unit>'
    return (f'<xliff version="1.2" xmlns="{NS}" xmlns:v="urn:cx03:extension">'
            f'<file original="cx03" datatype="plaintext" source-language="en" '
            f'target-language="fr"><body>{skip}{partial}{ordinary}</body></file></xliff>').encode()


def test_partial_lock_follows_external_id():
    dialect, document, projection, ledger, pairs = core._xliff_parse_projection(
        'cx03.xlf', _fixture())
    by_id = {(p.get('external_segment') or {}).get('external_id'): p for p in pairs}
    ids = list(by_id)
    partial_id = next(i for i in ids if i and 'partial' in i)
    ordinary_id = next(i for i in ids if i and 'ordinary' in i)
    assert by_id[partial_id].get('readonly_reason') == 'partial_subsegment_lock'
    assert by_id[ordinary_id].get('readonly_reason') != 'partial_subsegment_lock'
