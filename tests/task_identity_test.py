"""任务身份 = 文档身份 + 本地化上下文，而**不是**文档身份本身。

发布阻断级缺陷（本次修复）：任务 ID 只由文件内容哈希推导。于是
「同一份文档 + 另一个项目 + 另一种目标语言」会**静默打开旧任务**——
用户以为在新建，实际在续做一个语义完全不同的活：注入的项目记忆与术语
不同、译文语言也不同。任务数不增加，旧任务的进度被当成本次任务的进度。

修法：让项目身份与目标语言身份参与任务身份，同时把内容哈希保留为
**文档去重**手段（`file_job_id`），两者在概念上分开：

- 文档身份 `file_job_id(bytes)`      —— 同一份文件重传得到同一个值；
- 任务身份 `task_job_id(bytes, ctx)` —— 文档身份 + 项目 + 目标语言 + 源语言；
- 打开规则 `resolve_task_id(...)`    —— 同上下文续做；旧任务只有在上下文
  **可证明一致**时才被认领，否则宁可新建，也不把别的语言的旧任务当成本活。

命令行入口（`scripts/translate_pdf.py`，README 里的用户路径）用同一套规则
（`translate_pdf.resolve_job_id`）：它原来只看文档身份，于是"同一个 PDF 换
`--target-lang` 再跑一次"会静默续做另一种语言的旧任务。

运行：`python -m pytest tests/task_identity_test.py -q`
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import core  # noqa: E402

PROJECT_A = "proj-alpha"
PROJECT_B = "proj-beta"
LANG_ZH = "简体中文"
LANG_FR = "Français"

DOC_BYTES = b"docx-content-that-two-tasks-share"


@contextmanager
def job_env():
    """隔离的输出目录；应用与核心层共用同一个 `core.OUTPUT_DIR`。"""
    tmp = Path(tempfile.mkdtemp(prefix="task-identity-"))
    old_output = core.OUTPUT_DIR
    core.OUTPUT_DIR = tmp
    try:
        yield tmp
    finally:
        core.OUTPUT_DIR = old_output
        shutil.rmtree(tmp, ignore_errors=True)


def _create_task(job_id, *, project_id, target_lang, filename="paper.docx",
                 pairs=None):
    """按给定本地化上下文落盘一个任务，模拟"用户真的把它跑起来了"。"""
    state = core.new_job_state(filename)
    state["project_id"] = project_id
    state["target_lang"] = target_lang
    state["paras"] = ["Заседание начнётся завтра."]
    state["pairs"] = list(pairs or [])
    core.save_job_state(job_id, state)
    return state


# ================= 同一个上下文：仍然是同一个任务（续做不被破坏）=================


def test_same_document_same_context_resumes_the_same_task():
    """同文件 + 同项目 + 同目标语言 = 同一个任务，重传即续传。"""
    with job_env():
        first = core.resolve_task_id(DOC_BYTES, project_id=PROJECT_A,
                                     target_lang=LANG_ZH)
        _create_task(first, project_id=PROJECT_A, target_lang=LANG_ZH,
                     pairs=[{"source": "a", "target": "甲", "reviewed": True}])

        again = core.resolve_task_id(DOC_BYTES, project_id=PROJECT_A,
                                     target_lang=LANG_ZH)

        assert again == first, "同一份文档在同一上下文下必须续做同一个任务"
        resumed = core.load_job_state(again)
        assert resumed is not None
        assert len(resumed["pairs"]) == 1, "续做必须能看到既有进度"


def test_identical_content_still_maps_to_one_document_identity():
    """内容哈希仍然是文档身份：同一份字节永远得到同一个文档身份。"""
    with job_env():
        assert core.file_job_id(DOC_BYTES) == core.file_job_id(bytes(DOC_BYTES))
        assert core.file_job_id(DOC_BYTES) != core.file_job_id(DOC_BYTES + b"!")


# ================= 换项目 / 换目标语言：必须是独立任务 =================


def test_same_document_in_another_project_is_an_independent_task():
    """同文件、同目标语言、**另一个项目** -> 独立任务（注入的项目记忆不同）。"""
    with job_env():
        job_a = core.resolve_task_id(DOC_BYTES, project_id=PROJECT_A,
                                     target_lang=LANG_ZH)
        _create_task(job_a, project_id=PROJECT_A, target_lang=LANG_ZH)

        job_b = core.resolve_task_id(DOC_BYTES, project_id=PROJECT_B,
                                     target_lang=LANG_ZH)

        assert job_b != job_a, "换项目不得复用另一个项目的任务"
        assert core.load_job_state(job_b) is None, \
            "新上下文的任务必须是全新的，而不是指向旧任务的目录"


def test_same_document_in_another_target_language_is_an_independent_task():
    """同文件、同项目、**另一种目标语言** -> 独立任务（译文语言不同）。"""
    with job_env():
        job_zh = core.resolve_task_id(DOC_BYTES, project_id=PROJECT_A,
                                      target_lang=LANG_ZH)
        _create_task(job_zh, project_id=PROJECT_A, target_lang=LANG_ZH)

        job_fr = core.resolve_task_id(DOC_BYTES, project_id=PROJECT_A,
                                      target_lang=LANG_FR)

        assert job_fr != job_zh, "换目标语言不得复用另一种语言的旧任务"
        assert core.load_job_state(job_fr) is None


def test_other_project_and_language_tasks_do_not_share_state():
    """两个上下文的任务各自独立：状态互不泄漏，各自都能被再次续做。"""
    with job_env():
        job_a = core.resolve_task_id(DOC_BYTES, project_id=PROJECT_A,
                                     target_lang=LANG_ZH)
        _create_task(job_a, project_id=PROJECT_A, target_lang=LANG_ZH,
                     filename="paper-a.docx",
                     pairs=[{"source": "a", "target": "甲", "reviewed": True}])

        job_b = core.resolve_task_id(DOC_BYTES, project_id=PROJECT_B,
                                     target_lang=LANG_FR)
        assert job_b != job_a
        _create_task(job_b, project_id=PROJECT_B, target_lang=LANG_FR,
                     filename="paper-b.docx", pairs=[])

        state_a = core.load_job_state(job_a)
        state_b = core.load_job_state(job_b)

        # 各自记录的是自己的本地化上下文，没有互相顶替。
        assert state_a["project_id"] == PROJECT_A
        assert state_a["target_lang"] == LANG_ZH
        assert state_b["project_id"] == PROJECT_B
        assert state_b["target_lang"] == LANG_FR
        assert len(state_a["pairs"]) == 1 and state_b["pairs"] == [], \
            "一个任务的进度不得出现在另一个任务里"

        # 两个任务都能被各自的上下文再次打开（不是二选一）。
        assert core.resolve_task_id(DOC_BYTES, project_id=PROJECT_A,
                                    target_lang=LANG_ZH) == job_a
        assert core.resolve_task_id(DOC_BYTES, project_id=PROJECT_B,
                                    target_lang=LANG_FR) == job_b


def test_task_count_grows_when_the_localization_context_changes():
    """旧缺陷的可见症状：换了上下文任务数却不增加（静默复用）。"""
    with job_env():
        first = core.resolve_task_id(DOC_BYTES, project_id=PROJECT_A,
                                     target_lang=LANG_ZH)
        _create_task(first, project_id=PROJECT_A, target_lang=LANG_ZH)
        assert len(core.list_jobs()) == 1

        second = core.resolve_task_id(DOC_BYTES, project_id=PROJECT_A,
                                      target_lang=LANG_FR)
        _create_task(second, project_id=PROJECT_A, target_lang=LANG_FR)

        assert len(core.list_jobs()) == 2, \
            "同一份文档在另一种目标语言下必须是一个新任务"


# ================= 语言规范化：同一语言的写法差异不算新任务 =================


def test_language_normalization_keeps_the_same_task_resumable():
    """大小写 / 首尾空白 / 全半角不是另一种语言，仍应续做同一个任务。"""
    with job_env():
        job = core.resolve_task_id(DOC_BYTES, project_id=PROJECT_A,
                                   target_lang=LANG_FR)
        _create_task(job, project_id=PROJECT_A, target_lang=LANG_FR)

        assert core.resolve_task_id(DOC_BYTES, project_id=PROJECT_A,
                                    target_lang="  français  ") == job
        assert core.resolve_task_id(DOC_BYTES, project_id=PROJECT_A,
                                    target_lang="FRANÇAIS") == job
        assert core.resolve_task_id(DOC_BYTES, project_id=PROJECT_A,
                                    target_lang=LANG_ZH) != job


# ================= 旧数据：只在"可证明一致"时被认领 =================


def test_legacy_content_hash_task_is_adopted_when_context_provably_matches():
    """历史版本留下的、以内容哈希命名的任务，上下文一致时不得失联。"""
    with job_env():
        legacy_id = core.file_job_id(DOC_BYTES)
        _create_task(legacy_id, project_id=PROJECT_A, target_lang=LANG_ZH,
                     pairs=[{"source": "a", "target": "甲", "reviewed": True}])

        resolved = core.resolve_task_id(DOC_BYTES, project_id=PROJECT_A,
                                        target_lang=LANG_ZH)

        assert resolved == legacy_id, "可证明一致时必须沿用旧任务（否则进度失联）"
        assert len(core.load_job_state(resolved)["pairs"]) == 1


def test_legacy_content_hash_task_is_not_adopted_when_context_differs():
    """旧任务的上下文与请求不同 -> 绝不认领，宁可新建。"""
    with job_env():
        legacy_id = core.file_job_id(DOC_BYTES)
        _create_task(legacy_id, project_id=PROJECT_A, target_lang=LANG_ZH)

        resolved = core.resolve_task_id(DOC_BYTES, project_id=PROJECT_B,
                                        target_lang=LANG_FR)

        assert resolved != legacy_id, "上下文不同的旧任务不得被当成这个活"
        assert resolved == core.task_job_id(DOC_BYTES, project_id=PROJECT_B,
                                            target_lang=LANG_FR)
        assert core.load_job_state(resolved) is None, "必须是全新任务"


def test_legacy_task_without_a_target_language_is_never_adopted():
    """旧任务没有目标语言记录 -> 不可证明一致 -> 不认领（宁可让用户重建）。

    这是"宁可保守"的一侧：把一种语言的旧任务当成另一种语言的新任务，
    比让用户重新跑一次要危险得多。
    """
    with job_env():
        legacy_id = core.file_job_id(DOC_BYTES)
        state = core.new_job_state("paper.docx")
        state["project_id"] = PROJECT_A
        state.pop("target_lang", None)
        core.save_job_state(legacy_id, state)

        for language in (LANG_ZH, LANG_FR):
            resolved = core.resolve_task_id(DOC_BYTES, project_id=PROJECT_A,
                                            target_lang=language)
            assert resolved != legacy_id, \
                f"无目标语言的旧任务不得被认领（{language}）"
            assert core.load_job_state(resolved) is None


def test_task_identity_is_derived_deterministically():
    """任务身份是纯函数推导，不读磁盘：同样的输入永远得到同样的 ID。"""
    with job_env():
        for project_id, language in ((PROJECT_A, LANG_ZH), (PROJECT_B, LANG_FR)):
            first = core.task_job_id(DOC_BYTES, project_id=project_id,
                                     target_lang=language)
            second = core.task_job_id(DOC_BYTES, project_id=project_id,
                                      target_lang=language)
            assert first == second
            assert first != core.file_job_id(DOC_BYTES), \
                "任务身份必须区别于文档身份"


def test_task_identity_covers_project_and_language_together():
    """四种组合两两不同：项目与目标语言都参与身份，缺一不可。"""
    with job_env():
        combos = [(PROJECT_A, LANG_ZH), (PROJECT_A, LANG_FR),
                  (PROJECT_B, LANG_ZH), (PROJECT_B, LANG_FR)]
        ids = {core.task_job_id(DOC_BYTES, project_id=project_id,
                                target_lang=language)
               for project_id, language in combos}
        assert len(ids) == len(combos), \
            "项目或目标语言任一不同，任务身份就必须不同"


def test_document_identity_is_not_task_identity():
    """`file_job_id` 的文档字符串必须在概念上与任务身份区分开。"""
    assert "不是任务身份" in (core.file_job_id.__doc__ or "")
    assert "文档身份" in (core.task_job_id.__doc__ or "")


# ================= 命令行入口用的是同一套任务身份 =================
# `scripts/translate_pdf.py` 是 README 里的入口。它原来只看文档身份，于是
# 「同一个 PDF 换 --target-lang 再跑一次」会**静默续做**另一种语言的旧任务。

sys.path.insert(0, str(ROOT / "scripts"))
import translate_pdf  # noqa: E402


def test_cli_job_id_separates_target_languages():
    with job_env():
        document_id = core.file_job_id(b"pdf-bytes")
        zh = translate_pdf.resolve_job_id(document_id, LANG_ZH)
        fr = translate_pdf.resolve_job_id(document_id, LANG_FR)

        assert zh != fr, "命令行里同一个 PDF 换目标语言必须是另一个任务"
        assert translate_pdf.resolve_job_id(document_id, LANG_ZH) == zh, \
            "同一份文档 + 同一个目标语言必须仍然可续传"


def test_cli_job_id_adopts_a_legacy_task_only_when_provably_equal():
    with job_env():
        document_id = core.file_job_id(b"pdf-bytes")
        # 旧版本按裸文档身份命名，但**记录了**目标语言 -> 可证明一致 -> 沿用。
        _create_task(document_id, project_id=None, target_lang=LANG_ZH)

        assert translate_pdf.resolve_job_id(document_id, LANG_ZH) == document_id, \
            "上下文一致的旧任务不得失联（否则进度丢失）"
        assert translate_pdf.resolve_job_id(document_id, LANG_FR) != document_id, \
            "另一种目标语言不得沿用这个旧任务"


def test_cli_job_id_never_adopts_a_legacy_task_without_a_language():
    with job_env():
        document_id = core.file_job_id(b"pdf-bytes")
        state = core.new_job_state("book.pdf")
        state.pop("target_lang", None)
        core.save_job_state(document_id, state)

        for language in (LANG_ZH, LANG_FR):
            assert translate_pdf.resolve_job_id(document_id, language) != document_id, \
                f"无语言记录的旧任务不得被认领（{language}）"


def test_cli_explicit_job_id_still_wins():
    """`--job-id` 仍是显式续跑任意任务的逃生口。"""
    with job_env():
        document_id = core.file_job_id(b"pdf-bytes")
        assert translate_pdf.resolve_job_id(
            document_id, LANG_ZH, explicit="manual-job") == "manual-job"


def test_cli_document_identity_is_stable_and_content_based(tmp_path):
    """文档身份仍是纯内容派生：同一份文件永远同一个值（任务身份在它之上）。"""
    path = tmp_path / "book.docx"
    path.write_bytes(b"docx-bytes")

    first = translate_pdf.load_document(str(path))
    second = translate_pdf.load_document(str(path))

    assert first[0] == "book.docx" and first[1] == b"docx-bytes"
    assert first[2] == second[2], "同一份文档必须得到同一个文档身份"
    assert first[2] == core.file_job_id(b"docx-bytes")


# ================= 译前改了项目 / 目标语言：ID 不再证明上下文 =================
# 译前准备可以改目标语言和项目，但任务 ID 是按创建时的上下文推导的、不会跟着变。
# 只看"ID 是否存在"就会：先按英文导入，译前改成阿拉伯语，再按英文导入——打开的
# 却是那个阿拉伯语任务。


def _import(data, target_lang, *, project_id=None):
    from folith.application import commands
    from folith.application.settings import SettingsSources

    return commands.import_document(
        "paper.txt", data, SettingsSources(explicit={"target_lang": target_lang}),
        project_id=project_id)


def _setup(job_id, target_lang):
    from folith.application import commands
    from folith.application.settings import SettingsSources

    commands.apply_task_setup(
        job_id, SettingsSources(explicit={"target_lang": target_lang}),
        target_lang=target_lang)


def test_reimport_after_the_language_was_changed_does_not_open_the_other_language():
    with job_env():
        first = _import(DOC_BYTES, "English")
        _setup(first.job_id, "العربية")

        again = _import(DOC_BYTES, "English")

        assert again.job_id != first.job_id
        assert again.created is True
        assert core.load_job_state(again.job_id)["target_lang"] == "English"
        assert core.load_job_state(first.job_id)["target_lang"] == "العربية", \
            "改过语言的任务本身不受影响"


def test_moved_away_identity_resolves_to_the_same_new_task_every_time():
    """备选 ID 是确定的：同样的导入每次都落回同一个任务，不会越建越多。"""
    with job_env():
        first = _import(DOC_BYTES, "English")
        _setup(first.job_id, "العربية")

        second = _import(DOC_BYTES, "English")
        third = _import(DOC_BYTES, "English")

        assert third.job_id == second.job_id
        assert third.created is False


def test_changed_language_task_is_still_found_by_its_original_id_owner():
    """改语言之后再改回来：原 ID 上的任务又是这个上下文的活，照常续做。"""
    with job_env():
        first = _import(DOC_BYTES, "English")
        _setup(first.job_id, "العربية")
        _setup(first.job_id, "English")

        again = _import(DOC_BYTES, "English")

        assert again.job_id == first.job_id
        assert again.created is False


def test_source_language_chosen_in_setup_does_not_fork_the_task():
    """源语言由准备页写进任务；导入从不带源语言，不能因此认不出原任务。"""
    with job_env():
        first = _import(DOC_BYTES, "English")
        state = core.load_job_state(first.job_id)
        state["source_lang"] = "Русский"
        core.save_job_state(first.job_id, state)

        again = _import(DOC_BYTES, "English")

        assert again.job_id == first.job_id


def test_cli_does_not_resume_a_task_whose_language_was_changed():
    with job_env():
        document_id = core.file_job_id(b"pdf-bytes")
        zh = translate_pdf.resolve_job_id(document_id, LANG_ZH)
        _create_task(zh, project_id=None, target_lang=LANG_FR)

        again = translate_pdf.resolve_job_id(document_id, LANG_ZH)

        assert again != zh
        assert core.load_job_state(again) is None
