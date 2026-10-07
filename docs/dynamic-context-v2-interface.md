# Dynamic Context v2 — frozen interface contract (Phase 1)

Status: **frozen for parallel implementation**. Anything in this file is a
contract; implementers must not change it without updating this file and
notifying the other writers.

Experimental results, the executive verdict and the production recommendation are
in [dynamic-context-v2-report.md](dynamic-context-v2-report.md); raw artifacts are
bundled in `docs/dynamic-context-v2-evidence/`.

**FROZEN — 2026-10-06.** The production default is `context_mode = "fixed"`;
`dynamic_v2` is `EXPERIMENTAL — FROZEN` (retained, reproducible, opt-in, not
expanded). The binding decision record is
[dynamic-context-v2-adr.md](dynamic-context-v2-adr.md); the boundary is enforced
by `tests/context_mode_freeze_test.py`. Do not extend this interface — further
context research is a separate, future task.

Production default stays `context_mode = "fixed"`. The fixed path is **not**
rewritten; `dynamic_v2` is additive.

---

## 1. Current context pipeline (audit result)

| Concern | Where it lives today |
|---|---|
| document summary | `folith/context.py::generate_document_synopsis` → `state["document_synopsis"]` |
| document_arc | same synopsis dict, field `document_arc` (persisted, **not** rendered per batch) |
| semantic units / digests | `folith/context.py::build_semantic_units`, `generate_section_digests` → `state["section_digests"]` |
| section lookup for a segment | `folith/context.py::digest_for_segment` |
| neighbour context | `core.py::_process_batch_payload` builds `ctx_prev` (2 before) / `ctx_next` (2 after) from `state["paras"]` |
| accepted-target continuity | `folith/context.py::recent_target_context(pairs, offset, 2, exclude…)` |
| TM matches | `core.py::tm_lookup` (exact + normalized) inside the batch loop; TM store `core.tm` dict, populated in-run by `tm_put` |
| glossary terms | `folith/terminology.py::select_glossary_for_segments` + `glossary_block`; locked/provisional/scope rules |
| entity hints | `folith/entity_registry.py::EntityRegistry.hints_for` |
| knowledge feedback | `folith/knowledge.py::provisional_hints` over `state["knowledge_candidates"]` |
| style/profile/unit rules | `document_profile` + `state["style_rules"]` + `section_digest.translation_notes` |
| final packet render | `folith/context.py::compile_context_packet` → `render_context_packet_with_stats` |
| prompt cost accounting | `core.py::_translation_prompt_cost` + `PROMPT_COST_CATEGORIES` |
| token estimator | `folith/usage.py` — `ceil(chars / 4)` |

`dynamic_v2` **reuses** every retrieval system above. It does not re-implement
terminology, TM, entity or knowledge semantics.

---

## 2. Module: `folith/dynamic_context.py`

### 2.1 Constants

```python
CONTEXT_MODES = ("fixed", "dynamic_v2")
SOURCE_TYPES = ("local_context", "distant_segment", "tm", "glossary",
                "entity", "project_knowledge", "summary", "rule")
DEFAULT_CONTEXT_BUDGET_TOKENS = 1800      # == production DEFAULT_CONTEXT_BUDGET_CHARS (7200) / 4
MANDATORY_TOKEN_RESERVE = 0               # mandatory is charged before optional
HEADER_TOKEN_RESERVE = 96                 # ~8 tokens x 12 possible section headers
TM_SIMILARITY_THRESHOLD = 0.60            # difflib ratio; documented, not learned
DISTANT_MAX_CANDIDATES = 8
DISTANT_TERM_WEIGHT = 3
DISTANT_ENTITY_WEIGHT = 3
DISTANT_KEYWORD_WEIGHT = 1
ABLATION_NAMES = ("distant", "distant_terms", "distant_entities", "tm",
                  "summary", "local", "entity", "knowledge", "rule",
                  "glossary_provisional")
```

`distant_terms` / `distant_entities` disable one inverted-index match type each,
so the required distant-overlap ablations (term only / entity only / both) are
expressible without new configuration surface.

### 2.2 Token estimate

```python
def estimate_tokens(text: str) -> int      # ceil(len(text)/4), matches folith.usage
```

### 2.3 `ContextCandidate`

```python
@dataclass
class ContextCandidate:
    candidate_id: str
    source_type: str
    text: str
    source_segment_ids: Tuple[int, ...] = ()
    score_features: Dict[str, float] = field(default_factory=dict)
    token_estimate: int = 0
    provenance: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)
    mandatory: bool = False
    authority: int = 0        # AUTHORITY_RAW..AUTHORITY_LOCKED
    dedupe_key: str = ""
    score: float = 0.0
    def to_dict(self) -> Dict[str, Any]
```

`text` is the **exact prompt text** this candidate contributes (without its
section header). `metadata` may carry `section_id`, `distance`, `similarity`,
`entities`, `terms`, `confidence`, `confirmed`/`locked`.

Authority levels (module constants):

```python
AUTHORITY_LOCKED = 4        # human locked / confirmed
AUTHORITY_PROJECT = 3       # project-confirmed knowledge
AUTHORITY_TM = 2            # high-confidence TM
AUTHORITY_OBSERVATION = 1   # automatic observation
AUTHORITY_RAW = 0           # raw distant context
```

### 2.4 `QueryProfile`

```python
@dataclass
class QueryProfile:
    segment_ids: List[int]
    section_id: str
    position: int
    total_segments: int
    terms: List[str]        # glossary source forms occurring in the batch
    entities: List[str]     # entity registry source forms occurring in the batch
    keywords: List[str]     # deterministic CJK bigrams + latin words
    def to_dict(self) -> Dict[str, Any]

def build_query_profile(batch, *, offset, total_segments, section_profile=None,
                        glossary_entries=None, entity_records=None) -> QueryProfile
```

No LLM call. Deterministic.

### 2.5 `DistantIndex`

```python
@dataclass
class DistantHit:
    segment_id: int
    matched_terms: List[str]
    matched_entities: List[str]
    matched_keywords: List[str]
    same_section: bool
    distance: int
    score: float
    target: str = ""          # accepted target when available & visible
    level: str = ""

class DistantIndex:
    @classmethod
    def build(cls, segments, pairs=None, *, before_index=None,
              document_profile=None, glossary_entries=None,
              entity_records=None, max_segments=20000) -> "DistantIndex"
    def lookup(self, profile, *, before_index=<build default>,
               limit=DISTANT_MAX_CANDIDATES, exclude_segment_ids=(),
               pairs=<build default>, offline=False) -> List[DistantHit]
    def stats(self) -> Dict[str, int]
```

* Online (default, `before_index=offset`): only segments with index `< offset`
  are eligible. No future segment, no future translation.
* Offline (`before_index=None` + `offline=True`): whole document indexed; must be
  labelled `offline=True` in every record and never compared with online as
  equivalent.
* `pairs` / `before_index` passed to `build` are **defaults for later lookups**,
  not index content: the index stores source text only, and accepted targets are
  attached at lookup time from the live pair list, so a future translation can
  never be baked into the index.
* Inverted structures: `term -> ids`, `entity -> ids`, `keyword -> ids`,
  `section -> ids`.
* Never returns the current batch, blocked/excluded segments, local-neighbour
  segments, or empty segments.

### 2.6 Retrieval context + pipeline

```python
@dataclass
class RetrievalContext:
    profile: QueryProfile
    current_batch: List[str]
    previous_source: List[str] = ()
    next_source: List[str] = ()
    previous_target: List[Dict[str, Any]] = ()
    glossary_entries: List[Dict[str, Any]] = ()
    glossary_text: str = ""
    entity_hints: List[Dict[str, Any]] = ()
    knowledge_hints: List[Dict[str, Any]] = ()
    tm: Mapping[str, Any] = field(default_factory=dict)
    tm_lang: str = ""
    document_profile: Dict[str, Any] = field(default_factory=dict)
    document_synopsis: Dict[str, Any] = field(default_factory=dict)
    section_digest: Dict[str, Any] = field(default_factory=dict)
    style_rules: str = ""
    distant_index: Optional[DistantIndex] = None
    blocked_segment_ids: Sequence[int] = ()
    local_segment_ids: Sequence[int] = ()   # segments already carried as neighbours
    ablations: Sequence[str] = ()
    token_budget: int = DEFAULT_CONTEXT_BUDGET_TOKENS
    offline: bool = False

`RetrievalContext.exclude_ids()` returns the current batch ids, blocked ids,
local-neighbour ids and accepted-target ids; distant retrieval must never return
any of them, so one paragraph is never injected twice through two sources.

def collect_context_candidates(ctx: RetrievalContext) -> List[ContextCandidate]
def deduplicate_candidates(cands) -> Tuple[List[ContextCandidate], int]
def rank_candidates(cands) -> List[ContextCandidate]
def select_context(cands, token_budget) -> "ContextSelection"
```

`ContextSelection`:

```python
@dataclass
class ContextSelection:
    mandatory: List[ContextCandidate]
    selected: List[ContextCandidate]      # includes mandatory
    rejected: List[ContextCandidate]
    token_total: int
    tokens_by_source: Dict[str, int]
    candidates_by_source: Dict[str, int]
    mandatory_over_budget: bool
    budget: int
```

Selection rule, three tiers inside one budget:

1. **mandatory** — locked terminology, human-confirmed decisions, explicit rules.
   Never silently dropped; if it alone exceeds the budget set
   `mandatory_over_budget=True` and record it.
2. **priority** — batch-matched knowledge constraints: glossary, rule, entity and
   project-knowledge candidates that the *current batch* actually triggered
   (`PRIORITY_SOURCES`).  Tiny and directly actionable; raw distant text must
   never crowd them out of a tight budget.
3. **optional** — ranked local / distant / TM / summary context.

Within each tier candidates are sorted by
`(score desc, token_estimate asc, candidate_id asc)` and added while
`used + HEADER_TOKEN_RESERVE <= budget`.

### 2.7 Ranking (deterministic, documented)

```python
RANK_LOCKED_TERM = 100.0
RANK_HUMAN_CONFIRMED = 60.0
RANK_SHARED_TERM = 40.0
RANK_SHARED_ENTITY = 30.0
RANK_TM_SIMILARITY_MAX = 40.0     # scaled by similarity
RANK_DIRECT_NEIGHBOUR = 25.0
RANK_SAME_SECTION = 10.0
RANK_LEXICAL_OVERLAP_MAX = 20.0   # scaled by overlap ratio
RANK_DISTANCE_PENALTY_MAX = 30.0  # min(max, distance / 8)
```

`score_features` records each contributing feature by name so every score is
explainable after the fact. No learned model, no hidden weights.

### 2.8 Packet

```python
def build_dynamic_packet(*, current_batch, offset, total_segments, ...,
                         token_budget=..., ablations=(), offline=False,
                         experiment_id="") -> Dict[str, Any]
```

Returns a packet that is **shape-compatible with `compile_context_packet`**
plus these extra keys (only present in dynamic packets):

```text
context_mode="dynamic_v2"
dynamic_diagnostics: {
    experiment_id, context_mode, context_budget_tokens, offline,
    ablations, candidate_counts, selected_ids, selected_by_source,
    selected_tokens_by_source, rejected_count, mandatory_over_budget,
    fallback, fallback_reason, distant_stats,
    profile: {terms, entities, keywords, section_id}
}
tm_context:       List[{source, target, similarity, provenance, segment_id}]
distant_context:  List[{segment_id, distance, source, target, level,
                        matched_terms, matched_entities, matched_keywords,
                        retrieval, score}]
project_knowledge: List[str]
```

`build_dynamic_packet` **never raises**: any internal error is caught, recorded
in `dynamic_diagnostics.fallback_reason`, and the function falls back to
mandatory context + minimal local context (or the fixed packet when even that
fails). An experimental retrieval error must never destroy a translation job.

### 2.9 Rendering

New optional packet keys render through the **existing**
`folith/context.py::_render_context_packet`, additively, after the current
sections and before the current batch:

```text
【翻译记忆参考（近似匹配，仅供一致译法参考）】   key: tm_context
【远距离相关段落（历史上下文，仅供一致性参考）】 key: distant_context
【项目知识反馈（人工确认优先）】                 key: project_knowledge
```

Fixed packets never carry those keys, so fixed rendering is byte-identical.
`PROMPT_COST_CATEGORIES` gains `tm`, `distant_context`, `project_knowledge`
entries only for those titles.

`folith/context.py::context_metadata` adds a `dynamic_context` key **only** when
`packet.get("context_mode") == "dynamic_v2"`; fixed metadata is unchanged.

---

## 3. `core.translate_stage` wiring (additive)

```python
translate_stage(..., context_mode=None, context_budget_tokens=None,
                context_ablations=None, context_offline=None)
```

Resolution order:

```text
argument  >  state["context_mode"]  >  "fixed"
```

* `"fixed"` (default) → unchanged `compile_context_packet` call, unchanged
  prompt, unchanged behaviour.
* `"dynamic_v2"` → `folith.dynamic_context.build_dynamic_packet(...)`, then the
  same `translate_batch(..., context_packet=packet)` call.
* Unknown mode → `"fixed"` (fail safe, never experimental-by-accident).

Per-batch records appended to `state["dynamic_context_records"]` (bounded to
600 entries), each:

```text
batch, offset, segment_count, experiment_id, context_mode, context_budget_tokens,
offline, ablations, candidate_counts, selected_ids, selected_by_source,
priority_ids, selected_tokens_by_source, total_selected_tokens, rejected_count,
mandatory_over_budget, dedupe_removed_count, fallback, fallback_reason,
batch_source_tokens, source_tokens_estimate, context_chars, prompt_chars,
prompt_tokens_estimate, wall_s, distant_stats, distant_retrieved,
distant_selected, selected_scores, profile, category_chars
```

Records contain counts, ids and provenance only — never prompt text, never
secrets.

---

## 4. Benchmark arms (`scripts/dynamic_context_ab.py`)

| Arm | Meaning |
|---|---|
| `A_nocontext` | `dynamic_v2` with every optional source ablated → source batch + mandatory terminology only. In this workload the glossary has no locked entries, so A is a pure source-batch prompt. |
| `B_fixed` | `context_mode="fixed"` — production baseline. |
| `C_dynamic` | `dynamic_v2`, ablations `("distant",)`. |
| `D_dynamic_distant` | `dynamic_v2`, no ablation — primary experimental condition. |

Extra arms (ablations / budget study) are the same runner with different flags:

```text
D_term_only        distant retrieval restricted to term overlap
D_entity_only      distant retrieval restricted to entity overlap
D_both             == D_dynamic_distant
D_no_tm / D_no_summary / D_no_local
D_budget_small (900) / D_budget_medium (1800) / D_budget_large (3600)
```

Fixed across arms: provider, model, temperature, prompt, batching, concurrency,
review mode, knowledge-feedback policy, segmentation, glossary, starting state,
caches off.
