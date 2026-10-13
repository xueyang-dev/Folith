"""人工验收清单必须与代码、与候选产物一致。

验收清单是交付物的一部分：如果它引用了不存在的按钮、写错了快捷键，或者指向一份
旧产物，测试者会被误导——而这一类错误自动化最容易发现。
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["FOLITH_QT_NONINTERACTIVE"] = "1"

pytest.importorskip("PySide6")

KIT = ROOT / "docs" / "qt-beta" / "manual-acceptance.md"

#: 清单里用「」或代码格式引用的界面文字，必须真的出现在 Qt 源码里。
REFERENCED_LABELS = (
    "翻译任务", "还没有任务", "模型连接", "测试连接", "外观", "跟随系统",
    "日间", "夜间", "纸面", "保存并提交", "复制原文到译文", "保存并进入下一段",
    "冻结交付", "保存冻结交付文件", "用默认应用打开", "在 Finder 中显示",
    "重译本段", "排除本段", "已排除段落", "审校全部段落", "在后台运行",
    "关于 Folith", "诊断信息", "保存诊断包", "任务与交付", "导入文档",
)


def _qt_sources() -> str:
    # `prompts.py` 是应用内对话框（确认/通知/输入/等待）唯一的文案出处：
    # 像「在后台运行」这样的按钮文字随对话框一起从 window.py 搬了过去，
    # 扫描范围必须跟着走，否则检查会因为"文件搬家"误报缺文字。
    return "\n".join(
        (ROOT / "folith" / "qt" / name).read_text(encoding="utf-8")
        for name in ("views.py", "window.py", "dialogs.py", "theme.py",
                     "adapter.py", "prompts.py"))


def test_every_label_the_kit_references_exists():
    kit = KIT.read_text(encoding="utf-8")
    sources = _qt_sources()
    missing = [label for label in REFERENCED_LABELS
               if label in kit and label not in sources]
    assert not missing, f"验收清单引用了不存在的界面文字：{missing}"


def test_kit_shortcuts_match_the_registered_actions():
    """清单里列的快捷键必须与真实登记的菜单动作一致。"""
    from PySide6.QtGui import QKeySequence
    from PySide6.QtWidgets import QApplication

    from folith.qt import adapter, window as window_module

    app = QApplication.instance() or QApplication([])
    main = window_module.MainWindow(adapter.EngineConfig(),
                                   adapter.desktop_paths.resolve_paths(), None)
    try:
        actions = main.menu_actions
        resolved = {
            key: actions[key].shortcut().toString(
                QKeySequence.SequenceFormat.PortableText)
            for key in ("import", "submit", "close", "settings", "start",
                        "reload")
        }
        assert resolved == {
            "import": "Ctrl+O", "submit": "Ctrl+S", "close": "Ctrl+W",
            "settings": "Ctrl+,", "start": "Ctrl+R", "reload": "Ctrl+Shift+R",
        }, resolved
        kit = KIT.read_text(encoding="utf-8")
        for text in ("`⌘O`", "`⌘S`", "`⌘W`", "`⌘,`", "`⌘R`", "`⌘⇧R`"):
            assert text in kit, f"清单里缺少快捷键 {text}"
    finally:
        main.timer.stop()
        main._closing_accepted = True
        main.close()


def test_kit_candidate_identity_is_not_stale():
    """清单里的候选身份必须与构建记录一致（否则测试者核对的是旧哈希）。"""
    import qt_acceptance_kit as kit_script

    record = ROOT / "build" / "qt-desktop" / "dmg_record.json"
    if not record.is_file():
        pytest.skip("no build record on disk")
    text = KIT.read_text(encoding="utf-8")
    head, current, tail = kit_script._split(text)
    expected = kit_script.render(kit_script._facts())
    assert current.strip() == expected.strip(), (
        "验收清单的候选身份已过期：运行 "
        "`venv/bin/python scripts/qt_acceptance_kit.py --write`")
    # 记录里的 sha 必须真的出现在清单里（而不是只与生成结果一致），
    # 而且清单里**只能有这一个** sha——回传模板里再写一个就会过期。
    sha = json.loads(record.read_text(encoding="utf-8"))["sha256"]
    assert sha in text
    hashes = set(re.findall(r"\b[0-9a-f]{64}\b", text))
    assert hashes == {sha}, (
        f"验收清单里出现了其它 sha256（会过期）：{sorted(hashes - {sha})}")


def test_kit_names_the_manual_items_the_document_requires():
    """§6 的五类人工项必须在清单里有对应步骤，不能漏项。"""
    kit = KIT.read_text(encoding="utf-8")
    for heading in ("A. 干净机器安装", "B. 模型连接", "C. 真实中文输入法",
                    "D. 系统文件面板", "E. 睡眠 / 唤醒", "F. 真实模型服务",
                    "G. 生命周期与交互"):
        assert heading in kit, heading
    # 回传模板要让测试者能确认自己验收的就是这一份。
    assert "候选 sha256" in kit
    assert "诊断包" in kit


def test_docs_match_the_artifacts_and_the_suite():
    """状态/矩阵/验收清单/证据日志的说法必须与产物、实测、用例数一致。

    这些文档本身就是交付物：测试者按验收清单操作、按状态清单判断能不能发。历史上
    我在这里犯过几次错（用例数写成过时数字、候选 sha 手写后过期、结论字符串缺失），
    所以把核对变成用例。这里跳过"重新收集用例数"（那会递归调用 pytest）。
    """
    import qt_docs_audit as docs_audit

    if not (ROOT / "build" / "qt-desktop" / "dmg_record.json").is_file():
        pytest.skip("Qt document/artifact audit requires a locally built DMG record")

    report = docs_audit.audit(check_tests=False)
    assert report["ok"] is True, report["problems"]
    assert report["has_verdict"] is True
    assert report["evidence_unique"] == report["evidence_count"]
    assert report["unconditional_ready_claims"] == []


def test_docs_audit_catches_a_stale_claim(tmp_path, monkeypatch):
    """审计本身也要能被证伪：把状态清单改坏必须报出来。"""
    import qt_docs_audit as docs_audit

    if not (ROOT / "build" / "qt-desktop" / "dmg_record.json").is_file():
        pytest.skip("Qt document/artifact audit requires a locally built DMG record")

    original = docs_audit.STATUS.read_text(encoding="utf-8")
    broken = original.replace("ENGINEERING_COMPLETE — REQUIRED_ACCEPTANCE_PENDING",
                              "全部完成")
    monkeypatch.setattr(docs_audit, "STATUS", tmp_path / "status.md")
    (tmp_path / "status.md").write_text(broken, encoding="utf-8")
    report = docs_audit.audit(check_tests=False)
    assert report["ok"] is False
    assert any("结论字符串" in item for item in report["problems"]), report["problems"]


def test_quickstart_is_a_usable_one_page_guide():
    """验收速览必须自足：结论、候选身份、四件优先事项、回传模板、已知限制都在。"""
    import qt_docs_audit as docs_audit

    text = (ROOT / "docs" / "qt-beta" / "acceptance-quickstart.md").read_text(
        encoding="utf-8")
    assert "ENGINEERING_COMPLETE — REQUIRED_ACCEPTANCE_PENDING" in text
    assert "PRIVATE_BETA_READY" in text          # 说明"通过前不得声明"
    assert "不得" in text
    # 候选身份由生成器写入（与构建记录一致）
    record = ROOT / "build" / "qt-desktop" / "dmg_record.json"
    if record.is_file():
        assert docs_audit._sha_matches(
            text, json.loads(record.read_text(encoding="utf-8"))["sha256"])
    for needle in ("真实中文输入法", "系统文件面板", "干净机器", "真实模型服务",
                   "候选 sha256：", "诊断包", "已知限制",
                   "scripts/qt_beta_chain.sh", "已由机器验证",
                   "不要挂载 AX 代理", "QTBUG-149612"):
        assert needle in text, needle
    # 指向详细步骤，避免两份清单分叉
    assert "manual-acceptance.md" in text


def test_no_module_defines_the_same_function_twice():
    """同名函数重复定义会**静默**让后一个胜出——本项目已因此踩过两次。

    （`window._automation` 与 `adapter.unresolved_blocking`。）这里把整类钉死。
    """
    import qt_duplicate_defs

    problems = []
    for path in qt_duplicate_defs.TARGETS:
        for item in qt_duplicate_defs.duplicates_in(path):
            problems.append(f"{path.name}: {item}")
    assert not problems, f"存在重复定义（后者静默胜出）：{problems}"


def test_duplicate_checker_can_fail(tmp_path):
    """检查器自身必须能被证伪（否则"永远通过"就不是证据）。"""
    import qt_duplicate_defs

    sample = tmp_path / "sample.py"
    sample.write_text(
        "def thing():\n    return 1\n\n\n"
        "def thing():\n    return 2\n\n\n"
        "class Box:\n    def render(self):\n        return 'a'\n\n"
        "    def render(self):\n        return 'b'\n", encoding="utf-8")
    found = qt_duplicate_defs.duplicates_in(sample)
    assert any("thing" in item for item in found), found
    assert any("Box.render" in item for item in found), found
