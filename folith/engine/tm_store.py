"""Split out of core.py.

Moved verbatim out of ``core.py``; ``core`` re-exports every public and private
name defined here.  Anything still owned by ``core`` is read late-bound through
``_c`` so that tests (and callers) that patch ``core.X`` keep working.
"""

import unicodedata
from folith import project as _project
from folith import translation_target as _translation_target
from folith.textual import has_textual_content
from folith.textual import normalize_language
from folith.translation_memory import tm_context_status
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
