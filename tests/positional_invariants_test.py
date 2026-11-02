"""#233: position-indexed columns must never drift from the work list.

The task keeps several lists indexed by segment position that must stay the
same length as ``pairs`` / ``paras``: ``segment_source_spans``,
``segment_source_marks`` and ``segmentation.paragraph_ranges`` (+ its
``output_segment_count``).  Every structure edit used to update them by hand, so
one forgotten ``pop`` left the anchors/marks/range table running ahead of the
work list; nothing failed until recovered scan pages were re-ordered, hours
later, with no pointer to the cause.

These tests pin the three defences introduced for the issue:

* one choke point (``_splice_positional_columns``) — a static check that no
  structure edit mutates the work lists outside it;
* ``check_positional_invariants`` — a property test that fuzzes every structure
  edit and asserts the invariants after each step;
* ``repair_positional_columns`` — a one-off repair for tasks damaged by an older
  build.
"""
import ast
import random
import re
from pathlib import Path

import core
import pytest

ROOT = Path(__file__).resolve().parents[1]
POSITIONAL_COLUMNS = {
    "pairs", "paras", "segment_source_spans", "segment_source_marks",
}
# The single helper allowed to mutate the positional columns directly.
SPLICE_HELPER = "_splice_positional_columns"


# ---------------------------------------------------------------------------
# 1. One choke point: no direct mutation of the work lists outside the helper
# ---------------------------------------------------------------------------
class _DirectMutationFinder(ast.NodeVisitor):
    """Collect ``cols.pop`` / ``cols[i:j] =`` / ``del cols[...]`` on the columns."""

    _MUTATORS = {"pop", "insert", "append", "extend", "remove", "clear"}

    def __init__(self):
        self.function = None
        self.hits = []

    def visit_FunctionDef(self, node):  # noqa: N802 (ast API)
        previous = self.function
        self.function = node.name
        self.generic_visit(node)
        self.function = previous

    def _check_target(self, target, *, deleting=False):
        if isinstance(target, ast.Subscript) and isinstance(target.value, ast.Name) \
                and target.value.id in POSITIONAL_COLUMNS:
            # Any subscript write (element, slice or delete) on a work list must
            # go through the splice helper.
            self.hits.append((self.function, target.lineno, target.value.id, "subscript"))
        elif isinstance(target, (ast.Tuple, ast.List)):
            for item in target.elts:
                self._check_target(item, deleting=deleting)

    def visit_Assign(self, node):  # noqa: N802
        for target in node.targets:
            self._check_target(target)
        self.generic_visit(node)

    def visit_AugAssign(self, node):  # noqa: N802
        self._check_target(node.target)
        self.generic_visit(node)

    def visit_Delete(self, node):  # noqa: N802
        for target in node.targets:
            self._check_target(target, deleting=True)
        self.generic_visit(node)

    def visit_Call(self, node):  # noqa: N802
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr in self._MUTATORS \
                and isinstance(func.value, ast.Name) \
                and func.value.id in POSITIONAL_COLUMNS:
            self.hits.append((self.function, node.lineno, func.value.id, func.attr))
        self.generic_visit(node)


def test_no_structure_edit_mutates_positional_columns_outside_the_helper():
    """The grep test: only ``_splice_positional_columns`` touches the work lists.

    A direct ``pairs.pop`` / ``paras.insert`` / ``spans[i:j] =`` anywhere else is
    exactly how the lists drifted apart before, so it must not come back.
    """
    source = (ROOT / "folith" / "engine" / "segments.py").read_text(encoding="utf-8")
    finder = _DirectMutationFinder()
    finder.visit(ast.parse(source))
    offenders = [hit for hit in finder.hits if hit[0] != SPLICE_HELPER]
    assert not offenders, (
        "工作列表必须在 _splice_positional_columns 内修改，发现越界直接修改："
        + repr(offenders))

    # core.py owns ingestion/permutation, not structure edits; guard against it
    # growing its own direct mutation of the persisted work lists.
    core_source = (ROOT / "core.py").read_text(encoding="utf-8")
    qualified = r'state\[["\'](?:%s)["\']\]' % "|".join(sorted(POSITIONAL_COLUMNS))
    mutator = re.search(
        qualified + r"\s*\.\s*(pop|insert|append|extend|remove|clear)\s*\(", core_source)
    slice_assign = re.search(
        qualified + r"\s*\[[^\]]*:[^\]]*\]\s*=(?!=)", core_source)
    assert mutator is None, f"core.py 直接修改工作列表：{mutator.group(0) if mutator else ''}"
    assert slice_assign is None, "core.py 直接切片修改工作列表"


# ---------------------------------------------------------------------------
# Synthetic state shared by the property / repair / acceptance tests
# ---------------------------------------------------------------------------
def _state(n=6):
    state = core.new_job_state("positional.pdf")
    state.update(
        p1_done=True, p2_done=True,
        paras=[f"source {i}" for i in range(n)],
        pairs=[{"source": f"source {i}", "target": f"译文 {i}",
                "initial_target": f"译文 {i}", "reviewed": True,
                "from_tm": False, "glossary_entry_ids": []}
               for i in range(n)],
        segment_source_spans=[[{"page_id": f"p{i}"}] for i in range(n)],
        segment_source_marks=[[{"id": chr(97 + i)}] for i in range(n)],
        segmentation={
            "output_segment_count": n,
            "paragraph_ranges": [
                {"paragraph_index": i, "start": i, "end": i + 1}
                for i in range(n)],
        },
        review_stats={"reviewed_segments": n},
        delivery_status="draft",
    )
    return state


def _ranges(state):
    return [((row.get("start"), row.get("end")))
            for row in (state.get("segmentation") or {}).get("paragraph_ranges") or []]


# ---------------------------------------------------------------------------
# 2. Property test: random exclude/include/insert/split/merge/delete/undo
# ---------------------------------------------------------------------------
def _apply_random_edit(rng, state, step):
    pairs = state.get("pairs") or []
    paras = state.get("paras") or []
    count = len(pairs) if pairs else len(paras)
    operation = rng.choice(
        ["exclude", "include", "insert", "split", "merge", "delete", "undo"])
    if operation == "exclude":
        if not count:
            return "exclude", "empty"
        index = rng.randrange(count)
        if not (paras[index]
                or (pairs and str(pairs[index].get("target") or "").strip())):
            return operation, "empty segment"
        core.mutate_translation_segments(
            _JOB, index, "exclude", exclude_reason="property")
    elif operation == "include":
        records = state.get("excluded_segments") or []
        if not records:
            return operation, "nothing excluded"
        record = rng.choice(records)
        core.mutate_translation_segments(
            _JOB, 0, "include", excluded_id=record["excluded_id"])
    elif operation == "insert":
        if not count:
            return operation, "empty"
        core.mutate_translation_segments(_JOB, rng.randrange(count), "insert")
    elif operation == "split":
        if not count:
            return operation, "empty"
        index = rng.randrange(count)
        if not paras[index]:
            return operation, "empty segment"
        core.mutate_translation_segments(
            _JOB, index, "split",
            source_a=paras[index] + " A", source_b=paras[index] + " B",
            target_a="甲", target_b="乙")
    elif operation == "merge":
        if count < 2:
            return operation, "too short"
        core.mutate_translation_segments(_JOB, rng.randrange(count - 1), "merge")
    elif operation == "delete":
        candidates = [i for i, pair in enumerate(pairs)
                      if isinstance(pair, dict) and pair.get("manual_segment")
                      and not str(pair.get("source") or "").strip()
                      and not str(pair.get("target") or "").strip()]
        if not candidates:
            return operation, "no empty manual segment"
        core.mutate_translation_segments(_JOB, rng.choice(candidates), "delete")
    else:  # undo
        if not core.can_undo_translation_segment_structure(state)["available"]:
            return operation, "nothing to undo"
        core.undo_translation_segment_structure(_JOB)
    return operation, "ok"


_JOB = "positional-property"


def test_random_structure_edits_keep_positional_columns_in_step(tmp_path, monkeypatch):
    """Fixed seed, a few hundred random structure edits, invariants after each.

    This is the test that would have caught the six-exclusion desync at once.
    An edit the layer deliberately refuses (``ValueError``, e.g. an inclusion
    whose original paragraph range has since been restructured away) is skipped;
    every edit that *succeeds* must leave the columns in step.
    """
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    global _JOB
    _JOB = "positional-property"
    core.save_job_state(_JOB, _state())
    rng = random.Random(20261027)
    applied = 0
    for step in range(300):
        state = core.load_job_state(_JOB)
        try:
            operation, outcome = _apply_random_edit(rng, state, step)
        except ValueError:
            # The layer deliberately refused the edit (e.g. an inclusion whose
            # original paragraph range was restructured away).  Nothing was
            # saved; the invariants must still hold.
            operation, outcome = "edit", "rejected"
        state = core.load_job_state(_JOB)
        report = core.check_positional_invariants(state)
        assert report["ok"], (
            f"第 {step} 步（{operation}，{outcome}）后位置数据脱节：{report['summary']}")
        # Explicit full-coverage assertion (the invariant now requires it too): a
        # non-empty range table must run contiguously from 0 to len(paras).
        rows = (state.get("segmentation") or {}).get("paragraph_ranges") or []
        if rows:
            assert rows[0]["start"] == 0, (step, operation, rows)
            for previous, current in zip(rows, rows[1:]):
                assert previous["end"] == current["start"], (step, operation, rows)
            assert rows[-1]["end"] == len(state["paras"]), (
                step, operation, rows[-1]["end"], len(state["paras"]))
        if outcome == "ok":
            applied += 1
    assert applied >= 100, "property test barely exercised any edit"
    # And the final state is still structurally sound.
    assert core.check_positional_invariants(core.load_job_state(_JOB))["ok"]


# ---------------------------------------------------------------------------
# 3. A deliberately shifted list is rejected at edit time, naming the column
# ---------------------------------------------------------------------------
def test_a_shifted_anchor_list_is_rejected_at_edit_time_naming_the_column(
        tmp_path, monkeypatch):
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    job = "positional-shifted"
    state = _state(4)
    # Six manual exclusions in the field left the anchors running *ahead* of the
    # work list.  Reproduce that here and prove the next edit refuses to save it.
    state["segment_source_spans"] = state["segment_source_spans"] + [[{"page_id": "px"}]]
    core.save_job_state(job, state)
    with pytest.raises(ValueError, match="segment_source_spans"):
        core.mutate_translation_segments(job, 0, "insert")
    # Nothing was persisted: the rejected edit left the task untouched.
    assert len(core.load_job_state(job)["paras"]) == 4


def test_a_shifted_range_table_is_rejected_naming_the_range_column(
        tmp_path, monkeypatch):
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    job = "positional-shifted-ranges"
    state = _state(3)
    # Ranges running one past the work list (an exclusion that forgot to move
    # them) is the "ahead" direction; the edit must be refused, not saved.
    state["segmentation"]["paragraph_ranges"] = [
        {"paragraph_index": 0, "start": 0, "end": 2},
        {"paragraph_index": 1, "start": 2, "end": 4},
    ]
    core.save_job_state(job, state)
    # A same-length content edit does not change len(paras), so the over-long
    # range table cannot be masked by the edit's own length change.
    with pytest.raises(ValueError, match="paragraph_ranges"):
        core.mutate_translation_segments(job, 0, "source_edit", source_text="修改")


# ---------------------------------------------------------------------------
# 4. Include (restore) re-inserts its anchor, mark and range entry
# ---------------------------------------------------------------------------
def test_include_reinserts_anchor_mark_and_range_at_the_original_position(
        tmp_path, monkeypatch):
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    job = "positional-restore-acceptance"
    core.save_job_state(job, _state(3))
    excluded = core.mutate_translation_segments(
        job, 1, "exclude", exclude_reason="noise")
    excluded_id = excluded["excluded_segments"][0]["excluded_id"]

    restored = core.mutate_translation_segments(job, 0, "include",
                                                excluded_id=excluded_id)
    assert [span[0]["page_id"] for span in restored["segment_source_spans"]] == \
        ["p0", "p1", "p2"]
    assert [mark[0]["id"] for mark in restored["segment_source_marks"]] == \
        ["a", "b", "c"]
    assert _ranges(restored) == [(0, 1), (1, 2), (2, 3)]
    assert core.check_positional_invariants(restored)["ok"]


# ---------------------------------------------------------------------------
# 4b. Forward split/insert widen the range table; undo is the exact inverse
# ---------------------------------------------------------------------------
def test_forward_split_and_insert_widen_ranges_and_undo_is_their_inverse(
        tmp_path, monkeypatch):
    """A forward edit and its undo must move paragraph_ranges symmetrically."""
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    for operation, kwargs in (
            ("split", {"source_a": "source 1 A", "source_b": "source 1 B",
                       "target_a": "甲", "target_b": "乙"}),
            ("insert", {})):
        job = f"positional-symmetry-{operation}"
        monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
        core.save_job_state(job, _state(4))
        before = _ranges(core.load_job_state(job))
        edited = core.mutate_translation_segments(job, 1, operation, **kwargs)
        after = _ranges(edited)
        # The table must still be a complete cover, one slot wider than before.
        assert after[-1][1] == len(edited["paras"]) == 5, (operation, after)
        assert core.check_positional_invariants(edited)["ok"]
        undone = core.undo_translation_segment_structure(job)
        assert _ranges(undone) == before, (operation, _ranges(undone), before)
        assert len(undone["paras"]) == 4
        assert core.check_positional_invariants(undone)["ok"]


# ---------------------------------------------------------------------------
# 5. Repair for tasks damaged by an older build
# ---------------------------------------------------------------------------
def test_repair_rebuilds_anchors_from_pairs_and_reclaims_the_range_table():
    """A damaged state (anchors and ranges six ahead) becomes usable again."""
    state = _state(4)
    # Damage: older build left anchors and ranges ahead of the four real pairs.
    state["segment_source_spans"] = state["segment_source_spans"] + [
        [{"page_id": "pz"}], [{"page_id": "pz"}]]
    state["segment_source_marks"] = state["segment_source_marks"] + [
        [{"id": "z"}], [{"id": "z"}]]
    state["segmentation"]["paragraph_ranges"] = [
        {"paragraph_index": i, "start": i, "end": i + 1} for i in range(6)]
    state["segmentation"]["output_segment_count"] = 6
    assert not core.check_positional_invariants(state)["ok"]

    report = core.repair_positional_columns(state)
    assert report["ok"], report["summary"]
    # Anchors are rebuilt from each pair's own ledger (which the pairs kept).
    assert [span[0]["page_id"] for span in state["segment_source_spans"]] == \
        ["p0", "p1", "p2", "p3"]
    assert [mark[0]["id"] for mark in state["segment_source_marks"]] == \
        ["a", "b", "c", "d"]
    assert _ranges(state)[-1][1] == len(state["paras"])
    assert state["segmentation"]["output_segment_count"] == len(state["paras"])


def test_repair_is_a_no_op_on_a_healthy_state(tmp_path):
    state = _state(3)
    before = (list(state["segment_source_spans"]),
              list(state["segment_source_marks"]), _ranges(state))
    report = core.repair_positional_columns(state)
    assert report["ok"]
    assert (state["segment_source_spans"], state["segment_source_marks"],
            _ranges(state)) == before
