"""Split out of core.py.

Moved verbatim out of ``core.py``; ``core`` re-exports every public and private
name defined here.  Anything still owned by ``core`` is read late-bound through
``_c`` so that tests (and callers) that patch ``core.X`` keep working.
"""

import bisect
import difflib
import heapq
import math
import os
import unicodedata
from collections import namedtuple
from folith import project as _project
from folith import translation_target as _translation_target
from folith.textual import has_textual_content
from folith.textual import normalize_language
from folith.translation_memory import tm_context_status, tm_legacy_keys
from folith.engine._link import core as _c


def tm_path(project_id=None):
    """翻译记忆的存储路径；**按项目隔离**。

    翻译记忆是"已审校译文的受控记忆"，蓝图 §3.2 把它列在 Project 之下。
    条目键是「目标语言 + 原文」的作用域键（见 `tm_scope_key`）——因此同一个
    项目里同一段原文可以有多个目标语言的译法，而不会互相串用。

    系统工作区「未分类」沿用历史上的全局路径 `outputs/translation_memory.json`
    ——「未分类」就是旧默认项目的后继实体，因此既有任务的**行为与数据**完全
    不变（无迁移、无丢失）；命名项目各自使用
    `outputs/projects/<project_uuid>/translation_memory.json`。
    """
    project_id = _project.canonical_project_id(project_id) if project_id \
        else _project.SYSTEM_PROJECT_ID
    if _project.is_system_project_id(project_id):
        return _c.OUTPUT_DIR / "translation_memory.json"
    return _project.project_dir(_c.OUTPUT_DIR, project_id) / "translation_memory.json"


def translation_cache_path(project_id=None):
    """精确译文执行缓存的存储路径；与翻译记忆同级、同样**按项目隔离**。

    这个文件是派生缓存：删掉它只会多花 API 调用，不会影响翻译真值、TM 语义、
    顺序或交付。放在项目旁边是为了让「同一项目 + 同一原文 + 同一配置」在
    重跑、恢复和应用重启后仍然免费。
    """
    return tm_path(project_id).with_name("translation_cache.json")


def knowledge_cache_path(project_id=None):
    """知识反馈观测缓存的存储路径；与 TM / 精确译文缓存同级、同样按项目隔离。

    这个文件只保存「远程抽取计算」的规范化结果；合并、去重、优先级、事件与
    发现仍然每次重放，所以删掉它只会多花 API 调用，不会影响项目知识、翻译
    真值、顺序或交付。
    """
    return tm_path(project_id).with_name("knowledge_cache.json")


def _tm_eligible(source, target):
    """翻译记忆资格：源文必须有字母/数字（纯符号装饰行不入库），译文非空。"""
    return has_textual_content(source) \
        and bool((target or "").strip()) \
        and not _translation_target.is_translation_transport_wrapper(target)


def tm_normalize(text):
    """翻译记忆的规范化键：排版等价 → 同一键。

    处理的是真实来源差异，而不是"相似文本"：

    - PDF 与 DOCX 抽取出的换行、制表与连续空格；
    - 不间断空格（U+00A0）、全角空格（U+3000）；
    - 弯引号/直引号、en/em dash 与连字符、省略号、软连字符；
    - Unicode 兼容等价（NFKC，如全角字母数字）。

    不做大小写折叠：首字母大小写不同的段落是不同的段落。
    """
    if text is None:
        return ""
    value = unicodedata.normalize("NFKC", str(text))
    value = value.translate(_c._TM_CHAR_MAP)
    return _c._TM_SPACE_RE.sub(" ", value).strip()


def tm_index(tm, target_lang=None):
    """规范化原文 -> 作用域键的索引，**只收录目标语言可证明相同的条目**。

    没有目标语言上下文（`target_lang` 为空）时返回空索引：作用域不可证明
    就不建立任何可命中路径。
    """
    language = normalize_language(target_lang)
    index = {}
    if not language:
        return index
    for key, record in (tm or {}).items():
        if not _tm_hit(record, language, key):
            continue
        _, source = _c.tm_unscope_key(key)
        normalized = tm_normalize(source)
        if normalized and normalized not in index:
            index[normalized] = key
    return index


def _tm_hit(record, language, key):
    return isinstance(record, dict) and bool(record.get("reviewed")) \
        and bool(record.get("target")) \
        and _c.tm_record_language(key, record) == language


# ================= 模糊候选（#148：确定性相似度，只做候选，不做自动复用）=================
#
# 为什么单独一条读取路径而不是改 `tm_lookup`：`tm_lookup` 的结果会被**直接复用**
# 为已审校译文，而翻译记忆是错误放大器——一段"相似"的原文配上一段别的译文，
# 会把一个猜测复制到整篇文档。因此 `tm_lookup` 只认"可证明等价"（exact /
# normalized），这里新增的模糊候选只产出**建议**，必须经过审校或人工确认才能
# 落盘（#29/#92 的候选事务），并且 `_tm_hit` / `tm_index` / `tm_lookup` 的行为
# 一个字都不变。
#
# 为什么用标准库 `difflib.SequenceMatcher` 而不是 rapidfuzz：本项目虚拟环境里
# 没有 rapidfuzz，引入新依赖会让"零额外模型调用、零额外运行时依赖"的边界变宽，
# 而 difflib 的 ratio 是纯 Python 确定性实现，跨平台/跨版本结果稳定、可复现。
# 实测（50,000 条 TM，见 docs/tm-fuzzy-matching-0.8.2.md）在长度分桶 + 快速
# 上界剪枝下已经满足交互式查询的延迟上界，因此不值得为一个常数因子换依赖。
#
# 为什么输入先过 `tm_normalize`：排版差异（换行、全角、弯引号）不是"相似"，
# 而是"同一段文字"，不该被扣分。规范化后仍然不同，才是真正的模糊匹配。
#
# 为什么模糊分数封顶 99：100 是"可证明等价"的专用分数，任何非等价的候选都不
# 允许四舍五入到 100——否则调用方无法区分"同一句话"和"99.7% 像的另一句话"。

#: 部署级开关，关闭后所有未显式传参的调用回到 exact/normalized（rollback 路径）。
TM_FUZZY_ENV = "FOLITH_TM_FUZZY"
#: 部署级默认阈值（里程碑默认值的覆盖项，便于性能或精确率回退）。
TM_FUZZY_THRESHOLD_ENV = "FOLITH_TM_FUZZY_THRESHOLD"
#: 默认阈值：75%。这是**量出来的**，不是猜的——标定方法与数字见
#: docs/tm-fuzzy-matching-0.8.2.md「默认阈值」一节：同域不相关段落的最佳分数
#: 全部低于 50（噪声上界），"改一个数字"的分数下界 88（EN）/92（ZH），
#: "改一个专名"的下界 67（EN）/76（ZH）。75 落在噪声上界与有用改动之间，
#: 且与专业 CAT 的模糊区间（75–99）一致。
TM_FUZZY_DEFAULT_THRESHOLD = 75
#: 模糊候选的分数上限：100 只留给可证明等价的命中。
TM_FUZZY_MAX_SCORE = 99
#: 同一分数下的排序权重：可证明等价的候选永远排在猜测前面。
_TM_CANDIDATE_KIND_RANK = {"exact": 0, "normalized": 1, "fuzzy": 2}
#: 环境变量的"关闭"写法（其余任何值都按开启处理，非法值不抛错——降级不失败）。
_TM_FUZZY_OFF_VALUES = {"0", "false", "no", "off", "disabled"}
#: 每次查询最多做多少次**昂贵**的 ``ratio()`` 评估。
#:
#: 为什么需要它：`ratio()` 是匹配块构建（近似 O(len^2)），在"整篇同一句式"的
#: 样板段 TM 上，字符重集上界几乎等于 1，前几层剪枝完全不起作用，50,000 条
#: 穷举会把一次查询拖到数秒。默认值 400 的实测见
#: docs/tm-fuzzy-matching-0.8.2.md（§6：保留 50k 普通 TM p95 约 115 ms、
#: 样板段 p95 约 164 ms）。超预算时只保留"字符重集上界最高"的 400 条进
#: 精确计分，候选里会带 ``scan_truncated=True``（部分检索，绝不静默截断）。
TM_FUZZY_MAX_EVALS_ENV = "FOLITH_TM_FUZZY_MAX_EVALS"
TM_FUZZY_DEFAULT_MAX_EVALS = 400
#: 每次查询最多扫描多少条候选（廉价层：长度窗口 + real_quick_ratio +
#: quick_ratio）。它只限制最坏情况的常数，不参与分数判定。
TM_FUZZY_DEFAULT_MAX_SCAN = 6_000



def _tm_env_flag(raw):
    """解析开关型环境变量；无法判断时返回 None（调用方使用默认值）。"""
    value = str(raw or "").strip().lower()
    if not value:
        return None
    return value not in _TM_FUZZY_OFF_VALUES


def tm_fuzzy_enabled_default():
    """模糊候选是否默认开启；由 ``FOLITH_TM_FUZZY`` 覆盖。

    默认**开启**：这是一个新增的只读候选接口，不改变任何自动复用路径，
    调用方显式要候选就是为了拿模糊候选。要恢复 0.8.1 的状态有两条路：
    调用方传 ``fuzzy=False``（单参数、无数据迁移），或部署级设置
    ``FOLITH_TM_FUZZY=0``。
    """
    enabled = _tm_env_flag(os.environ.get(TM_FUZZY_ENV))
    return True if enabled is None else enabled


def tm_fuzzy_default_threshold():
    """默认相似度阈值（百分比整数）；无法解析时退回 ``TM_FUZZY_DEFAULT_THRESHOLD``。"""
    raw = str(os.environ.get(TM_FUZZY_THRESHOLD_ENV, "") or "").strip()
    if not raw:
        return TM_FUZZY_DEFAULT_THRESHOLD
    try:
        value = int(round(float(raw)))
    except (TypeError, ValueError):
        return TM_FUZZY_DEFAULT_THRESHOLD
    return min(100, max(0, value))


def tm_fuzzy_max_evals():
    """每次查询的昂贵评估预算；非法值退回默认（性能回退旋钮，不是正确性开关）。"""
    raw = str(os.environ.get(TM_FUZZY_MAX_EVALS_ENV, "") or "").strip()
    if not raw:
        return TM_FUZZY_DEFAULT_MAX_EVALS
    try:
        value = int(round(float(raw)))
    except (TypeError, ValueError):
        return TM_FUZZY_DEFAULT_MAX_EVALS
    return max(1, value)


class TmCandidateIndex:
    """候选索引：一次构建、多次查询的**只读快照**（按目标语言过滤）。

    与 `tm_index` 的关系：`tm_index` 是"规范化原文 -> 作用域键"的精确索引，
    服务于自动复用；本类只是同一个 TM 真值的另一份**派生**视图，包含精确
    索引没有的模糊查询所需的规范化文本与长度分桶。两者都不改变 TM 真值：
    删掉索引只是重建，不会影响任何落盘内容。

    只收录 `_tm_hit` 成立的条目（已审校、译文非空、目标语言可证明），因此
    旧版无作用域条目天然不在候选里（#108：保留数据，但永不参与复用）。
    """

    __slots__ = ("target_lang", "keys", "sources", "records", "normalized",
                 "lengths", "sorted_lengths", "order", "by_normalized",
                 "legacy_unscoped_count")

    def __init__(self, target_lang=""):
        self.target_lang = target_lang
        self.keys = []
        self.sources = []
        self.records = []
        self.normalized = []
        self.lengths = []
        self.sorted_lengths = []
        self.order = []
        self.by_normalized = {}
        self.legacy_unscoped_count = 0

    def __len__(self):
        return len(self.keys)


def tm_candidate_index(tm, target_lang=None):
    """构建候选索引；目标语言无法证明时返回空索引（fail closed，同 `tm_index`）。

    构建是 O(n)（每条做一次规范化），查询才是热路径；一次运行建一次即可，
    与 `tm_index` 的用法一致。长度分桶在这里排好序，查询时用 bisect 取窗口，
    避免为每个候选重算长度上界。
    """
    language = normalize_language(target_lang)
    index = TmCandidateIndex(language)
    if not language:
        return index
    for key, record in (tm or {}).items():
        if not _tm_hit(record, language, key):
            continue
        _, source = _c.tm_unscope_key(key)
        normalized = tm_normalize(source)
        if not normalized:
            # 纯排版/空白段没有可比较的内容；它也不会是"相似段落"。
            continue
        index.keys.append(key)
        index.sources.append(str(source))
        index.records.append(record)
        index.normalized.append(normalized)
        index.lengths.append(len(normalized))
        index.by_normalized.setdefault(normalized, []).append(len(index.keys) - 1)
    index.order = sorted(range(len(index.keys)), key=lambda pos: index.lengths[pos])
    index.sorted_lengths = [index.lengths[pos] for pos in index.order]
    index.legacy_unscoped_count = len(tm_legacy_keys(tm))
    return index


class TmCandidates(list):
    """候选列表 + 本次检索的完备性状态；**list 行为完全不变**。

    为什么返回子类而不是纯 list：`scan_truncated` 是"集合可能不完整"的
    机器可读标志，而它必须能在**空结果**上被读到。只把标记挂在候选条目上时，
    "预算耗尽、一条都没过线"与"TM 里确实没有相似段"在返回值上完全不可区分，
    调用方就会把一次被截断的检索当成"没有记忆"——正是 #148 要避免的静默截断。

    属性（不影响 `len()` / 迭代 / 下标 / 切片 / 比较）：

    * ``scan_truncated``：本次检索是否因工作预算而**部分检索**；
    * ``reason``：``complete`` / ``eval_budget_exhausted`` /
      ``scan_budget_exhausted`` / ``eval_budget_exhausted+scan_budget_exhausted``
      / ``no_target_language`` / ``empty_source``（后两者是 fail-closed 的
      正常空结果，不是截断）；
    * ``evaluated``：实际做了多少次昂贵的 ``ratio()`` 评估；
    * ``scanned``：进入阶段二候选池（廉价上界过线）的条数；
    * ``window_size``：长度窗口内的条目总数（未按预算裁剪前）。

    注意：`list(...)` / 切片 / JSON 序列化得到的是普通 list，状态会丢——
    需要完备性判断的调用方应直接读**这个返回值**的属性。
    """

    __slots__ = ("scan_truncated", "reason", "evaluated", "scanned",
                 "window_size")

    def __init__(self, items=()):
        super().__init__(items)
        self.scan_truncated = False
        self.reason = "complete"
        self.evaluated = 0
        self.scanned = 0
        self.window_size = 0

    def __repr__(self):
        return (f"{type(self).__name__}({list.__repr__(self)}, "
                f"reason={self.reason!r}, scan_truncated={self.scan_truncated!r})")


def _tm_fuzzy_length_window(query_length, threshold):
    """由阈值反推候选长度的**无损**窗口。

    `SequenceMatcher.ratio() = 2M / (len_a + len_b)`，而匹配字符数不超过较短
    一段的长度，所以 ``ratio <= 2*min / (len_a + len_b)``。给定阈值 t、查询长度
    Lq，只有长度 Lc 落在 ``[Lq*t/(2-t), Lq*(2-t)/t]`` 内的条目才可能达到 t；
    窗口外的条目即使算 ratio 也一定低于阈值，跳过它们不改变任何分数结果。

    比较用的是"四舍五入后的整数百分比"，所以边界用 ``t - 0.5``（四舍五入的
    半格）放宽，保证不会把"能过线"的候选剪掉。
    """
    cutoff = max(0.0, (float(threshold) - 0.5) / 100.0)
    if cutoff <= 0.0:
        return 0, None
    low = int(math.floor(query_length * cutoff / (2.0 - cutoff)))
    high = int(math.ceil(query_length * (2.0 - cutoff) / cutoff))
    return max(0, low), high


def _tm_window_bounds(index, window):
    """长度窗口在「按长度排序」数组里的半开区间 ``[start, stop)``。"""
    lengths = index.sorted_lengths
    if window is None:
        return 0, len(lengths)
    low, high = window
    start = bisect.bisect_left(lengths, low)
    stop = len(lengths) if high is None else bisect.bisect_right(lengths, high)
    return start, max(start, stop)


def _tm_window_positions(index, window, query_length, max_scan):
    """按"长度窗口 + 长度最接近优先"产出候选位置（确定性顺序）。

    窗口内条目很多时（样板段 TM 最典型），从查询长度向两侧扩展地扫描：真正
    可能过线的候选几乎总是长度接近的，先扫它们，预算耗尽时才丢掉尾部。窗口
    不超过预算时顺序退化为长度升序（与全量扫描等价，见性能测试）。
    """
    order = index.order
    lengths = index.sorted_lengths
    start, stop = _tm_window_bounds(index, window)
    total = stop - start
    if total <= 0:
        return
    if max_scan is None or total <= max_scan:
        for bucket_pos in range(start, stop):
            yield order[bucket_pos]
        return
    center = bisect.bisect_left(lengths, query_length, start, stop)
    left, right = center - 1, center
    yielded = 0
    while yielded < max_scan and (left >= start or right < stop):
        if right < stop:
            yield order[right]
            right += 1
            yielded += 1
            if yielded >= max_scan:
                return
        if left >= start:
            yield order[left]
            left -= 1
            yielded += 1


#: `_tm_fuzzy_scores` 的结果：候选 + 完备性状态（内部使用，公开面是 TmCandidates）。
_FuzzyScan = namedtuple(
    "_FuzzyScan",
    "found truncated reasons evaluated scanned window_size")


def _tm_fuzzy_scores(index, normalized_query, threshold, *, window=None,
                     best_count=None, max_scan=None, max_evals=None):
    """对索引里的候选算确定性相似度，返回 `_FuzzyScan`。

    两阶段，所有剪枝都是**无损**的（不改变任何入选分数）：

    阶段一（廉价上界）：在长度窗口内逐个候选算 ``real_quick_ratio``（长度上界，
    常数时间）和 ``quick_ratio``（字符重集上界，O(len)），只留下上界最高的
    ``max_evals`` 条；被挤掉的数量说明集合不再完整（``truncated``）。

    阶段二（昂贵精确分）：只对留下的候选算 ``ratio()``（O(len^2) 的匹配块
    构建）。``real_quick_ratio`` / ``quick_ratio`` 都是 ``ratio`` 的上界，
    所以"上界低于阈值"的候选本来就不可能入选；把它们挡在阶段二之外不改变
    任何分数。

    为什么要两阶段而不是边扫边算：样板段 TM 里字符重集上界几乎等于 1，前两层
    剪不掉东西，50,000 条穷举会把一次查询拖到数秒。此时"先按上界排序、再对
    最有可能的一批算精确分"既把最坏情况压进预算，也避免"扫描顺序决定质量"
    ——顺序按上界降序（同分按位置升序），是确定性的。

    **两条截断路径都要如实上报**（这是 #148 审查的必修项）：

    * ``max_evals`` 预算：上界过线的候选多于预算，挤掉的可能是真实候选；
    * ``max_scan`` 预算：长度窗口本身超过廉价扫描上限，窗口尾部根本没看。

    两者都会体现在 ``truncated`` 与 ``reasons`` 里；即使最终一条候选都没过
    阈值，``truncated`` 仍然为真——由调用方（`tm_candidates`）挂到返回列表上。

    ``best_count``（调用方只要前 K 名）在阶段二把剪枝线抬到"当前第 K 名分数"：
    上界都够不到第 K 名的候选不必算精确分，不影响前 K 名。

    复用一个 `SequenceMatcher` 并把查询固定为 seq2：seq2 的字符索引只构建一次，
    逐候选 `set_seq1` 换掉被比较的一段。这是 50k 规模下最省的做法。
    """
    cutoff = max(0.0, (float(threshold) - 0.5) / 100.0)
    if max_evals is None:
        max_evals = tm_fuzzy_max_evals()
    if max_scan is None:
        max_scan = TM_FUZZY_DEFAULT_MAX_SCAN
    normalized = index.normalized
    start, stop = _tm_window_bounds(index, window)
    window_size = stop - start
    truncated = window_size > max_scan

    # ---- 阶段一：廉价上界，保留上界最高的 max_evals 条 ----
    matcher = difflib.SequenceMatcher()
    matcher.set_seq2(normalized_query)
    candidates = []          # 小顶堆：(上界, 位置)
    scanned = 0
    dropped = False
    for pos in _tm_window_positions(index, window, len(normalized_query), max_scan):
        candidate = normalized[pos]
        if candidate == normalized_query:
            # 可证明等价：由 exact/normalized 分支以 100 分返回，不参与模糊计分。
            continue
        matcher.set_seq1(candidate)
        if matcher.real_quick_ratio() < cutoff:
            continue
        bound = matcher.quick_ratio()
        if bound < cutoff:
            continue
        scanned += 1
        if len(candidates) < max_evals:
            heapq.heappush(candidates, (bound, pos))
        elif bound > candidates[0][0]:
            heapq.heapreplace(candidates, (bound, pos))
            dropped = True
        else:
            dropped = True
    truncated = truncated or dropped

    # ---- 阶段二：只对上界最高的那批算精确分（上界降序 = 最有可能的先算）----
    found = []
    best_scores = []         # 已算出的分数（升序），用于 Top-K 剪枝线
    evaluated = 0
    for bound, pos in sorted(candidates, key=lambda item: (-item[0], item[1])):
        required = threshold
        if best_count is not None and len(best_scores) >= best_count:
            required = max(required, best_scores[-best_count])
        # 分数是"四舍五入后的整数百分比"，所以剪枝线对应的最小 ratio 是
        # （required - 0.5）/100，正好是四舍五入的半格。
        if bound < max(cutoff, (required - 0.5) / 100.0):
            continue
        matcher.set_seq1(normalized[pos])
        evaluated += 1
        score = int(round(matcher.ratio() * 100))
        if score > TM_FUZZY_MAX_SCORE:
            score = TM_FUZZY_MAX_SCORE
        if score < threshold:
            continue
        found.append((score, pos))
        if best_count is not None:
            bisect.insort(best_scores, score)
            if len(best_scores) > best_count * 4:
                del best_scores[:-best_count * 2]

    reasons = []
    if window_size > max_scan:
        reasons.append("scan_budget_exhausted")
    if dropped:
        reasons.append("eval_budget_exhausted")
    return _FuzzyScan(found=found, truncated=truncated,
                      reasons=tuple(reasons), evaluated=evaluated,
                      scanned=scanned, window_size=window_size)


def tm_candidates(tm, source, target_lang, *, previous_source=None,
                  next_source=None, threshold=None, limit=None, fuzzy=None,
                  index=None):
    """返回按相似度**降序**的 TM 候选（含分数、来源与上下文冲突标记）。

    #148：给「原文稍有改动（一个数字、一个名字、一句话）」的段落一个高置信
    建议，而不是让译者从零重译。这是**只读候选**：调用方可以把候选填进编辑器
    供人确认，但不得把它当作已审校译文直接复用——保护路径与 #29/#92 的候选
    事务一致，`tm_lookup` / `tm_index` / `_tm_hit` 的行为不变。

    参数
    ----
    ``tm``
        翻译记忆映射（同一个 `tmus` / `translation_memory.json` 真值，不另建存储）。
    ``source``
        当前原文；先用 `tm_normalize` 规范化再比较（排版差异不扣分）。
    ``target_lang``
        必须显式给出；无法证明目标语言时返回空列表（与 `tm_index` 同样 fail closed）。
    ``previous_source`` / ``next_source``
        当前上下文（#107）。记录里写有前后文指纹且不一致时，该候选的
        ``context_status`` 是 ``conflict``，``context_conflict`` 为真——候选照常
        给出，但必须降级为"建议并确认"。
    ``threshold``
        模糊候选的最低整数百分比（0-100）。``None`` 时取
        `tm_fuzzy_default_threshold()`。精确/规范化候选不受它影响（恒为 100）。
    ``limit``
        返回条数上限；``None`` 表示不截断（在全部候选上排序）。给出 ``limit``
        时会启用 Top-K 上界剪枝。**当且仅当本次检索没有被工作预算截断
        （``result.scan_truncated is False``）时**，返回结果与"全量排序后取
        前 N 名"逐条相同；一旦截断，阶段一按"上界降序"淘汰，上界排序不等于
        精确 ``ratio`` 排序，前 N 名可能不同。负值按 0 处理。
    ``fuzzy``
        是否返回模糊候选。``False`` 时只返回 exact/normalized，也就是 0.8.1 的
        可证明等价行为——这是回滚路径（单参数、无数据迁移）。``None`` 时取
        `tm_fuzzy_enabled_default()`（可被 ``FOLITH_TM_FUZZY=0`` 关闭）。
    ``index``
        由 `tm_candidate_index` 预构建的索引，用于一次运行内多次查询；语言必须
        与 ``target_lang`` 一致（不一致直接抛 ``ValueError``，不跨语言猜）。

        **索引是快照**：它不跟随 ``tm`` 的变化自动更新。``tm`` 里新增、删除、
        改审校状态或改目标语言之后，必须重新 `tm_candidate_index` —— 复用旧
        索引只会按旧快照返回候选（不会读出错数据，但会漏掉新条目）。一次运行
        内 TM 不变时复用它是安全的，也是 50k 规模下的推荐用法。

    返回的每一项::

        {
            "key": 作用域键（"目标语言 + 原文"，TM 的身份）,
            "source": 条目原文（未规范化）,
            "target": 条目译文,
            "score": 整数百分比（exact/normalized = 100，模糊 <= 99）,
            "kind": "exact" | "normalized" | "fuzzy",
            "context_status": "match" | "conflict" | "unknown",
            "context_conflict": bool（context_status == "conflict" 的便捷位）,
            "legacy_unscoped": False（#108 语义下旧条目不参与，恒为 False）,
            "reviewed": True,
            "provenance": 记录自带来源（如 "tmx_import"），没有则空串,
            "target_lang": 该条目的目标语言原样写法,
            "scan_truncated": 该条目所属检索是否"部分检索"（便捷位；空结果时
                              只能从返回对象上读，见下）,
        }

    排序键：分数降序 → 等价程度（exact > normalized > fuzzy）→ 原文 → 键。
    全部是确定性比较，没有哈希顺序或时间抖动的参与。

    返回类型是 `TmCandidates`（``list`` 的子类，列表行为完全不变），额外带
    本次检索的完备性状态：``scan_truncated`` / ``reason`` / ``evaluated`` /
    ``scanned`` / ``window_size``。**空结果也必须能读出状态**：

    * ``tm_candidates(...) == []`` 且 ``result.scan_truncated is True``
      → 这次检索被预算截断、一条都没找到，不等于"TM 里没有相似段"；
    * ``result.scan_truncated is False`` → 窗口内是穷举的，集合完整。

    性能边界（实测数字见 docs/tm-fuzzy-matching-0.8.2.md）：长度窗口 / 上界
    剪枝在预算内是**穷举**的；样板段极多的 TM 上昂贵评估会超过预算
    （``FOLITH_TM_FUZZY_MAX_EVALS``，默认 400），或长度窗口超过廉价扫描
    上限（``TM_FUZZY_DEFAULT_MAX_SCAN``，6,000），此时 ``scan_truncated`` 为
    真、``reason`` 说明是哪一路预算（可能两者都有）。分数仍然精确，但集合可能
    不完整，调用方必须如实展示这一点（不要静默省略）。
    """
    language = normalize_language(target_lang)
    if not language:
        return _tm_candidates_result([], reason="no_target_language")
    raw_query = str(source or "").replace("\n", " ")
    normalized_query = tm_normalize(raw_query)
    if not normalized_query:
        return _tm_candidates_result([], reason="empty_source")
    resolved_threshold = tm_fuzzy_default_threshold() if threshold is None \
        else int(threshold)
    resolved_threshold = min(100, max(0, resolved_threshold))
    if fuzzy is None:
        fuzzy = tm_fuzzy_enabled_default()
    resolved_limit = None if limit is None else max(0, int(limit))
    if index is None:
        index = tm_candidate_index(tm, target_lang)
    elif index.target_lang != language:
        raise ValueError(
            "候选索引与目标语言不一致：索引是 "
            f"{index.target_lang!r}，查询是 {language!r}（不能跨语言复用索引）")
    if resolved_limit == 0:
        return _tm_candidates_result([], reason="complete")

    items = []
    scan = None
    # 1) 可证明等价：精确（原文逐字相同）与规范化等价（只差排版），都是 100。
    for pos in index.by_normalized.get(normalized_query, ()):
        source_text = index.sources[pos]
        items.append(_tm_candidate_item(
            index, pos, 100,
            "exact" if source_text == raw_query else "normalized",
            previous_source, next_source))
    # 2) 模糊候选：确定性相似度，分数四舍五入到整数且封顶 99。
    if fuzzy and resolved_threshold <= TM_FUZZY_MAX_SCORE:
        # 调用方要前 N 名时，把 N 减掉已经占位的 100 分条目，作为 Top-K 剪枝
        # 的目标；结果与"全量排序后再截断"完全一致（上界剪枝不改变前 N 名）。
        best_count = None if resolved_limit is None \
            else max(0, resolved_limit - len(items))
        if best_count != 0:
            window = _tm_fuzzy_length_window(len(normalized_query),
                                             resolved_threshold)
            scan = _tm_fuzzy_scores(
                index, normalized_query, resolved_threshold, window=window,
                best_count=best_count)
            for score, pos in scan.found:
                items.append(_tm_candidate_item(
                    index, pos, score, "fuzzy", previous_source, next_source,
                    scan_truncated=scan.truncated))

    items.sort(key=lambda item: (
        -item["score"], _TM_CANDIDATE_KIND_RANK[item["kind"]],
        item["source"], item["key"]))
    if resolved_limit is not None:
        items = items[:resolved_limit]
    result = _tm_candidates_result(items)
    if scan is not None:
        result.scan_truncated = scan.truncated
        result.reason = "+".join(scan.reasons) if scan.reasons else "complete"
        result.evaluated = scan.evaluated
        result.scanned = scan.scanned
        result.window_size = scan.window_size
    return result


def _tm_candidates_result(items, *, reason="complete"):
    """把候选装进带完备性状态的列表子类（状态字段的语义见 `TmCandidates`）。"""
    result = TmCandidates(items)
    result.reason = reason
    result.scan_truncated = reason not in ("complete", "no_target_language",
                                           "empty_source")
    return result


def _tm_candidate_item(index, pos, score, kind, previous_source, next_source,
                       scan_truncated=False):
    """把索引里的一个位置组装成候选条目（上下文状态在这里统一标注）。"""
    record = index.records[pos]
    status = tm_context_status(record, previous_source, next_source)
    # 条目声明语言优先（写入时落盘），键前缀兜底；两者都没有时用索引的
    # 比较键——但那种条目根本不会进索引（`_tm_hit` 已经要求语言可证明）。
    declared = record.get("target_lang") or _c.tm_unscope_key(
        index.keys[pos])[0] or index.target_lang
    return {
        "key": index.keys[pos],
        "source": index.sources[pos],
        "target": record.get("target", ""),
        "score": score,
        "kind": kind,
        "context_status": status,
        "context_conflict": status == "conflict",
        # #108：旧版无作用域条目保留在文件里，但永远不进入候选；这里恒为
        # False 是为了让调用方可以显式断言"这条候选的作用域是可证明的"。
        "legacy_unscoped": False,
        "reviewed": bool(record.get("reviewed")),
        "provenance": str(record.get("source") or ""),
        "target_lang": str(declared),
        # 工作预算耗尽时是"部分检索"：分数仍然精确，但集合可能不完整。
        # 不做静默截断——调用方有权知道这次查询没有扫完。
        "scan_truncated": bool(scan_truncated),
    }


class _TranslationMemory(dict):
    """Remember the read baseline so a save applies only this writer's changes."""
    def __init__(self, values=()):
        super().__init__(values)
        self.baseline = {key: dict(value) for key, value in self.items()}


def _ensure_tm_dir(path):
    """Create the TM parent directory at most once per process."""
    parent = str(path.parent)
    with _c._TM_DIR_LOCK:
        if parent in _c._TM_DIR_READY:
            return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        # The writer below retries; do not poison the cache with a failure.
        return
    with _c._TM_DIR_LOCK:
        _c._TM_DIR_READY.add(parent)


def _write_tm_bytes(path, payload):
    """Write the encoded payload, rebuilding the directory once if it vanished."""
    try:
        path.write_bytes(payload)
    except OSError:
        with _c._TM_DIR_LOCK:
            _c._TM_DIR_READY.discard(str(path.parent))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)


def tm_project_id(state):
    """任务使用的翻译记忆所属项目（与任务的项目归属一致）。"""
    return _c.resolved_project_id(state or {})


def state_target_lang(state, fallback=None):
    """任务的目标语言（TM 作用域用），返回**原样写法**（用于落盘与展示）。

    只认 state 里真正记下来的目标语言；没有就退回调用方显式给出的 fallback，
    两者都没有则返回空串。这里**不补默认值**——补一个默认语言等于猜语言，
    而猜错的代价是把一条已审校的错译文复用到另一种语言的任务里。
    """
    raw = (state or {}).get("target_lang")
    if normalize_language(raw):
        return str(raw)
    return str(fallback or "")


def copy_system_tm_to_project(project_id):
    """把系统工作区「未分类」的已审校记忆并入指定项目（人工动作）。

    新建项目从空白记忆开始（隔离的代价）。如果用户希望把既有积累带过去，
    这是一个显式的、可解释的动作，而不是静默共享或静默复制。
    返回本次并入的条目数。
    """
    target_id = _project.canonical_project_id(project_id)
    if _project.is_system_project_id(target_id):
        return 0
    if _c.load_project(target_id) is None:
        raise ValueError(f"项目不存在：{project_id}")
    source_tm = _c.load_tm(_project.SYSTEM_PROJECT_ID)
    if not source_tm:
        return 0
    target_tm = _c.load_tm(target_id)
    added = 0
    for source, record in source_tm.items():
        if source in target_tm:
            continue
        target_tm[source] = record
        added += 1
    if added:
        _c.save_tm(target_tm, target_id)
    return added


def _migrate_legacy_system_tm():
    """把历史遗留的 `projects/default/translation_memory.json` 补到根路径。

    系统工作区的 TM 只有一个权威位置：`outputs/translation_memory.json`
    （旧默认项目的全局记忆路径）。旧版本里某些路径曾经把 TM 写进
    `projects/<id>/translation_memory.json`；迁移时**只在权威文件不存在时**
    搬过来，绝不合并两份——那会造成"同一实体两份 TM 真值"。
    """
    authoritative = _c.OUTPUT_DIR / "translation_memory.json"
    if authoritative.is_file():
        return False
    legacy = _project.projects_root(_c.OUTPUT_DIR) \
        / _project.SYSTEM_PROJECT_ID / "translation_memory.json"
    if not legacy.is_file():
        return False
    try:
        authoritative.parent.mkdir(parents=True, exist_ok=True)
        legacy.replace(authoritative)
    except OSError:
        return False
    return True
