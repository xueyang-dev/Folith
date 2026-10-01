import io
import zipfile
import pytest
from core import (
    _domain_ok,
    _hard_ok,
    _clean_annotations,
    pairs_to_word,
    ANNOTATION_COLORS,
    _find_span,
    _is_person_name,
    _is_split_proper_noun,
)


def test_domain_stop_terms_and_length():
    # 常用国名/地名停用词被过滤
    assert not _domain_ok("Germany")
    assert not _domain_ok("China")
    assert not _domain_ok("United States")
    assert not _domain_ok("Berlin")

    # 过长标题/句子被过滤
    assert not _domain_ok("OECD The Space Economy at a Glance 2026")
    assert not _domain_ok("This is a very long sentence that cannot be a term")

    # 合法专业术语应保留
    assert _domain_ok("Urban Air Mobility")
    assert _domain_ok("propulsion systems")


def test_hard_segment_filtering():
    full_text = "Space has increasingly become an important component of economic development, technological innovation and strategic competitiveness."
    # 整段覆盖应被过滤
    assert not _hard_ok(full_text, full_text)

    # 超过 80% 覆盖的长段应被过滤
    almost_full = full_text[:int(len(full_text) * 0.85)]
    assert not _hard_ok(almost_full, full_text)

    # 精准局部短语应保留
    phrase = "combining a strong industrial foundation"
    assert _hard_ok(phrase, full_text)


def test_clean_annotations_term_frequency_cap():
    pairs = [
        {"source": f"Section {i} discusses Urban Air Mobility in Germany.",
         "target": f"第 {i} 节讨论了德国的城市空中交通。"}
        for i in range(5)
    ]
    # 每一段都有 Germany 和 Urban Air Mobility
    annotations = {}
    for i in range(5):
        src = pairs[i]["source"]
        tgt = pairs[i]["target"]
        g_s = src.find("Germany")
        g_t = tgt.find("德国")
        u_s = src.find("Urban Air Mobility")
        u_t = tgt.find("城市空中交通")

        annotations[i] = [
            {"type": "domain", "src_span": [g_s, g_s + len("Germany")], "tgt_span": [g_t, g_t + len("德国")], "note": "术语：Germany -> 德国"},
            {"type": "domain", "src_span": [u_s, u_s + len("Urban Air Mobility")], "tgt_span": [u_t, u_t + len("城市空中交通")], "note": "术语：Urban Air Mobility -> 城市空中交通"},
        ]

    cleaned = _clean_annotations(annotations, pairs)

    # Germany 属于停用词，应全部被过滤
    for items in cleaned.values():
        for item in items:
            assert "Germany" not in item["note"]

    # Urban Air Mobility 全篇最多出现 2 次
    uam_count = 0
    for items in cleaned.values():
        for item in items:
            if "Urban Air Mobility" in item["note"]:
                uam_count += 1
    assert uam_count == 2, f"Urban Air Mobility 预期全篇最多标注 2 次，实际 {uam_count} 次"


def test_pairs_to_word_cleans_full_segment_hard():
    full_source = "Space has increasingly become an important component of economic development."
    full_target = "航天日益成为经济发展的重要组成部分。"
    pairs = [{"source": full_source, "target": full_target}]
    # 传入滥标的整段 hard
    dirty_ann = {0: [{"type": "hard", "src_span": [0, len(full_source)], "tgt_span": [0, len(full_target)], "note": "整段滥标"}]}

    buf = pairs_to_word(pairs, annotations=dirty_ann)
    with zipfile.ZipFile(io.BytesIO(buf.getvalue())) as z:
        xml = z.read("word/document.xml").decode("utf-8")

    # 确认整段 hard 的颜色 #008080 没有被整段应用到正文中
    hard_color = ANNOTATION_COLORS["hard"]
    assert xml.count(f'w:val="{hard_color}"') <= 1  # 仅图例中出现一次


def test_find_span_latin_word_boundaries_and_full_words():
    # 1. 词干匹配词尾派生/屈折形式时，必须标全整个单词，绝不截断单词（如 Egyptian 绝不能只标 Egypt）
    src = "Neither the Syrian and Jordanian anti-aircraft guns nor the Egyptian MiGs could bring him down"
    span = _find_span(src, "Egypt")
    assert span is not None
    assert src[span[0]:span[1]] == "Egyptian"
    assert span == (60, 68)

    # 2. 命中复数形式时也标全
    src2 = "They deployed modern Skyhawks across the region."
    span2 = _find_span(src2, "Skyhawk")
    assert src2[span2[0]:span2[1]] == "Skyhawks"

    # 3. 避免在其他单词中间误命中（如 smart 中不应命中 art）
    src3 = "The smart art of design"
    span3 = _find_span(src3, "art")
    assert span3 == (10, 13)
    assert src3[span3[0]:span3[1]] == "art"


def test_domain_person_names_and_places_filtered():
    # 亲属称谓+人名严禁作为专业术语
    assert not _domain_ok("Grandpa Yaakov", "雅科夫爷爷")
    assert not _domain_ok("Grandma Sarah", "萨拉奶奶")
    # 音译人名严禁作为专业术语
    assert not _domain_ok("Sarah Mageni", "莎拉·马格尼")
    assert not _domain_ok("Evan Gordon", "埃文·戈登")
    assert not _domain_ok("ZE’EV RAZ", "泽埃夫·拉兹")
    # 单独人名严禁作为专业术语
    assert not _domain_ok("Nadav", "纳达夫")
    assert not _domain_ok("Moran", "莫兰")
    assert not _domain_ok("Avsha", "阿夫沙")
    # 通识国家与地区严禁作为专业术语
    assert not _domain_ok("Egypt", "埃及")
    assert not _domain_ok("Sinai", "西奈")
    assert not _domain_ok("California", "加利福尼亚")

    # 真正的核心专业术语必须保留
    assert _domain_ok("Urban Air Mobility", "城市空中交通")
    assert _domain_ok("War of Attrition", "消耗战")
    assert _domain_ok("Six-Day War", "六日战争")
    assert _domain_ok("Skyhawk", "天鹰攻击机")
    assert _domain_ok("anti-aircraft guns", "高射炮")


def test_split_proper_noun_filtered():
    # Kibbutz Geva 中仅标了 Kibbutz 属于复合专名截断，应被检测并过滤
    text = "It was the year that Kibbutz Geva marked thirty years since its founding"
    span = (21, 28)  # "Kibbutz"
    assert text[span[0]:span[1]] == "Kibbutz"
    assert _is_split_proper_noun(span, text)

    # 独立通用的 kibbutz 不应被当成截断
    standalone_text = "The members of the kibbutz gathered for lunch."
    s_idx = standalone_text.find("kibbutz")
    standalone_span = (s_idx, s_idx + len("kibbutz"))
    assert not _is_split_proper_noun(standalone_span, standalone_text)


def test_glossary_overlay_cannot_bypass_filters():
    # 术语表确定性覆盖严禁绕过人名、地名与专名截断过滤
    pairs = [
        {"source": "Grandpa Yaakov and Grandma Sarah owned the book.", "target": "雅科夫爷爷和萨拉奶奶拥有这本书。"},
        {"source": "Egyptian MiGs were in Egypt.", "target": "埃及的米格机在埃及。"},
        {"source": "It was the year that Kibbutz Geva was founded.", "target": "那一年盖瓦基布兹成立了。"},
        {"source": "The Six-Day War broke out.", "target": "六日战争爆发了。"},
    ]
    raw_annotations = {
        0: [
            {"type": "domain", "src_span": [0, 14], "tgt_span": [0, 5], "note": "术语：Grandpa Yaakov -> 雅科夫爷爷"},
            {"type": "domain", "src_span": [19, 32], "tgt_span": [6, 10], "note": "术语：Grandma Sarah -> 萨拉奶奶"},
        ],
        1: [
            {"type": "domain", "src_span": [21, 26], "tgt_span": [7, 9], "note": "术语：Egypt -> 埃及"},
        ],
        2: [
            {"type": "domain", "src_span": [21, 28], "tgt_span": [3, 6], "note": "采用该以色列集体农庄形式的通行音译专名。"},
        ],
        3: [
            {"type": "domain", "src_span": [4, 15], "tgt_span": [0, 4], "note": "术语：Six-Day War -> 六日战争"},
        ]
    }
    cleaned = _clean_annotations(raw_annotations, pairs)

    # 人名、国家名、专名截断全被过滤
    assert 0 not in cleaned
    assert 1 not in cleaned
    assert 2 not in cleaned

    # 合法专业术语保留
    assert 3 in cleaned
    assert len(cleaned[3]) == 1
    assert "Six-Day War" in cleaned[3][0]["note"]
