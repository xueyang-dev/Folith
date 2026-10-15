"""macOS 文件打开集成：Finder / Dock / `open -a` / argv 交来的文档。

覆盖三件事，每件都能在无头环境里独立判定：

1. `QFileOpenEvent` → 应用对象 → `file_opened` 信号（路径提取正确、事件被消费）；
2. 窗口就绪之前的排队（`ExternalFileQueue`）不丢路径；
3. `MainWindow.open_external_files()` 只接受共享契约里的格式，其余**如实说明**
   （用户双击一个不支持的文件时，"什么都没发生"是最糟的反馈）；
4. 打包声明（`packaging/folith-qt.spec` 的 `CFBundleDocumentTypes`）与共享应用层的
   支持格式不漂移——没有这份声明，Finder 根本不会把文件交给本应用。
"""

from __future__ import annotations

import ast
import os
import pathlib

import pytest

# Each regression file runs in its own process. Do not inherit automation
# switches accidentally from another Qt test module.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["FOLITH_QT_NONINTERACTIVE"] = "1"
os.environ["FOLITH_EXIT_FORCE_PROMPT"] = "0"

ROOT = pathlib.Path(__file__).resolve().parents[1]

from folith.qt import app as app_module  # noqa: E402
from folith.qt import window as window_module  # noqa: E402
from folith.qt.app import ExternalFileQueue  # noqa: E402
from folith.qt.window import file_open_path  # noqa: E402

pytest.importorskip("PySide6")


@pytest.fixture(scope="module")
def qt_app():
    """本文件自己把应用建出来：只有这样才能验证"应用对象就是 FolithApplication"。"""
    from PySide6.QtWidgets import QApplication

    instance = QApplication.instance()
    if instance is None:
        instance = window_module.FolithApplication([])
    yield instance


def _run_in_fresh_process(code: str) -> None:
    """在**全新的进程**里跑一段 Qt 代码。

    "进程里的第一个 QApplication 是不是 FolithApplication"只能在没有别的应用对象
    的进程里判定：同一次 pytest 里只要有别的测试文件先建了普通 `QApplication`，
    `build_application` 就（正确地）直接复用它，这两条断言便与文件的执行顺序有关。
    """
    import os
    import subprocess
    import sys

    env = {**os.environ, "QT_QPA_PLATFORM": "offscreen", "PYTHONPATH": str(ROOT)}
    result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env,
                            capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr


def test_build_application_installs_the_file_open_aware_application():
    """入口建的应用必须能收到 `QFileOpenEvent`，否则 macOS 的"打开方式"永远没反应。"""
    _run_in_fresh_process("""
from PySide6.QtWidgets import QApplication
from folith.qt import window as window_module

app = window_module.build_application([])
assert isinstance(app, window_module.FolithApplication), type(app)
assert isinstance(QApplication.instance(), window_module.FolithApplication)
assert hasattr(app, "file_opened")
""")


def test_file_open_event_is_consumed_and_emits_the_local_path():
    """真事件走一遍：路径要取对，而且必须返回 True（否则 Qt 会忽略它）。"""
    _run_in_fresh_process("""
from PySide6.QtCore import QUrl
from PySide6.QtGui import QFileOpenEvent
from folith.qt import window as window_module

app = window_module.build_application([])
received = []
app.file_opened.connect(received.append)
event = QFileOpenEvent(QUrl.fromLocalFile("/tmp/中文 路径/论文.pdf"))
assert window_module.FolithApplication.event(app, event) is True
assert received == ["/tmp/中文 路径/论文.pdf"], received
""")


def test_file_open_path_falls_back_to_the_url_form():
    """有的平台/版本只给 `url()`：取不到 `file()` 时不能返回空串。"""

    class _UrlOnly:
        def file(self):
            raise AttributeError("no file() here")

        def url(self):
            from PySide6.QtCore import QUrl

            return QUrl.fromLocalFile("/tmp/only-url.docx")

    assert file_open_path(_UrlOnly()) == "/tmp/only-url.docx"


def test_file_open_path_is_empty_when_the_event_carries_nothing():
    class _Empty:
        def file(self):
            return ""

        def url(self):
            from PySide6.QtCore import QUrl

            return QUrl()

    assert file_open_path(_Empty()) == ""


def test_external_file_queue_holds_paths_until_the_window_exists():
    """窗口没建好时先排队——启动即打开文件是最常见的时序，丢在这里最隐蔽。"""
    opened: list[tuple[object, list[str]]] = []
    queue = ExternalFileQueue(lambda window, files: opened.append((window, files)))

    queue.add("/tmp/a.pdf")
    queue.add("")
    queue.add("   ")
    assert queue.pending == ["/tmp/a.pdf"]
    assert opened == [], "窗口还不存在时不允许直接调用"

    window = object()
    queue.attach(window)
    assert opened == [(window, ["/tmp/a.pdf"])], opened
    assert queue.pending == []

    # 窗口就绪之后到达的事件：立刻交给窗口，不再排队
    queue.add("/tmp/b.docx")
    assert opened[-1] == (window, ["/tmp/b.docx"])
    assert queue.pending == []


def test_attach_flushes_nothing_when_there_is_nothing_pending():
    opened: list[object] = []
    queue = ExternalFileQueue(lambda window, files: opened.append(files))
    queue.attach(object())
    assert opened == []


# ---------------------------------------------------------------- 窗口行为

@pytest.fixture()
def client(tmp_path, monkeypatch, qt_app):
    """最小窗口夹具：只要能把 `open_external_files` 跑到真实分发路径上。"""
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("FOLITH_DESKTOP_DATA_DIR", str(data_dir))
    monkeypatch.setenv("FOLITH_DESKTOP_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("FOLITH_OUTPUT_DIR", str(data_dir))

    from PySide6.QtCore import QSettings

    import core

    monkeypatch.setattr(core, "OUTPUT_DIR", data_dir, raising=False)

    from folith.desktop import paths as desktop_paths
    from folith.qt import adapter, i18n as i18n_module

    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    monkeypatch.setattr(window_module, "QSettings", lambda *_args: settings)
    i18n_module.set_language("zh-CN")

    paths = desktop_paths.resolve_paths()
    config = adapter.EngineConfig(
        provider="自定义中转站", model="stub-model", api_key="stub-key",
        base_url="", target_lang="简体中文", enable_review=True,
        enable_report=False, enable_understanding=False,
        enable_source_cleanup=False)
    window = window_module.MainWindow(config, paths, None)
    window.show()
    qt_app.processEvents()
    try:
        yield window
    finally:
        window.close()
        qt_app.processEvents()
        i18n_module.set_language("zh-CN")


def test_supported_documents_are_imported_and_land_on_the_home_page(client, tmp_path, monkeypatch):
    """支持的类型必须真的走导入路径（而不是被静默忽略）。"""
    from folith.qt import adapter

    imported: list[list[str]] = []
    monkeypatch.setattr(client, "import_paths", lambda paths, **kw: imported.append(list(paths)))

    source = tmp_path / "论文.docx"
    source.write_bytes(b"stub")
    client.open_external_files([str(source)])

    assert imported == [[str(source)]], imported
    assert client.stack.currentWidget() is client.home, "导入结果要落在任务列表上"


def test_every_shared_source_suffix_is_accepted(client, tmp_path, monkeypatch):
    """契约里列出的格式一个都不能漏——否则 macOS 交来的文件会被当成"不支持"。"""
    from folith.qt import adapter

    imported: list[list[str]] = []
    monkeypatch.setattr(client, "import_paths", lambda paths, **kw: imported.append(list(paths)))

    paths = []
    for suffix in adapter.SUPPORTED_SUFFIXES:
        item = tmp_path / f"文档{suffix}"
        item.write_bytes(b"stub")
        paths.append(str(item))
    client.open_external_files(paths)

    assert imported == [paths], imported


def test_unsupported_document_says_so_instead_of_doing_nothing(client, tmp_path, monkeypatch):
    """不支持的类型：不导入、但要**说明**，不能静默。"""
    imported: list[list[str]] = []
    monkeypatch.setattr(client, "import_paths", lambda paths, **kw: imported.append(list(paths)))

    other = tmp_path / "表格.xlsx"
    other.write_bytes(b"stub")
    client.open_external_files([str(other)])

    assert imported == [], imported
    message = client.last_notice()
    assert "xlsx" in message and "不支持" in message, message


def test_mixed_batch_imports_the_supported_ones_only(client, tmp_path, monkeypatch):
    imported: list[list[str]] = []
    monkeypatch.setattr(client, "import_paths", lambda paths, **kw: imported.append(list(paths)))

    good = tmp_path / "好.pdf"
    bad = tmp_path / "坏.zip"
    good.write_bytes(b"stub")
    bad.write_bytes(b"stub")
    client.open_external_files([str(good), str(bad)])

    assert imported == [[str(good)]], imported
    assert "zip" in client.last_notice()


def test_empty_input_is_a_no_op(client, monkeypatch):
    """空路径不能触发导入（macOS 有时会送来空字符串）。"""
    imported: list[list[str]] = []
    monkeypatch.setattr(client, "import_paths", lambda paths, **kw: imported.append(list(paths)))
    client.open_external_files([])
    client.open_external_files(["", "   "])
    assert imported == []


# ------------------------------------------------------- 命令行与打包声明

def test_parser_accepts_documents_as_positional_arguments():
    """`Folith.app/Contents/MacOS/Folith 论文.pdf` 这种用法要能进导入流程。"""
    args = app_module._parser().parse_args(["a.pdf", "b.docx"])
    assert args.files == ["a.pdf", "b.docx"]


def test_parser_defaults_to_no_documents():
    assert app_module._parser().parse_args([]).files == []


def test_bundle_declares_every_supported_document_type():
    """没有 `CFBundleDocumentTypes`，Finder 的「打开方式」不会列出本应用。"""
    from folith.qt import adapter

    spec = (ROOT / "packaging" / "folith-qt.spec").read_text(encoding="utf-8")
    tree = ast.parse(spec)
    # spec 是 PyInstaller 脚本（不是可导入模块），只能读 AST：先收下模块级常量，
    # 再把 `CFBundleDocumentTypes` 的值解析成字面量（可能引用常量名）。
    constants: dict[str, object] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    try:
                        constants[target.id] = ast.literal_eval(node.value)
                    except ValueError:
                        pass
    declared = None
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
                getattr(target, "id", None) == "info_plist" for target in node.targets):
            for key, value in zip(node.value.keys, node.value.values):
                if getattr(key, "value", None) == "CFBundleDocumentTypes":
                    declared = (constants.get(value.id) if isinstance(value, ast.Name)
                                else ast.literal_eval(value))
    assert declared, "spec 里必须有 CFBundleDocumentTypes"
    assert isinstance(declared, list) and declared

    utis = {uti for entry in declared for uti in entry["LSItemContentTypes"]}
    # 每一项都必须是"候选"处理程序：不抢用户的系统默认应用。
    assert all(entry["LSHandlerRank"] == "Alternate" for entry in declared), declared
    assert all(entry["CFBundleTypeRole"] in {"Viewer", "Editor"} for entry in declared)
    assert "com.adobe.pdf" in utis, utis
    assert "org.openxmlformats.wordprocessingml.document" in utis, utis
    # 声明必须**恰好等于**契约：多声明一种 UTI，Finder 就会把本应用列进那种文件的
    # 候选程序，而导入层会拒绝它——用户双击后只得到"不支持的文件类型"。
    # 本轮真机验收就是这样发现 `.txt` / `.md` / `.doc` 被声明却打不开的。
    ingestible = {
        ".pdf": "com.adobe.pdf",
        ".docx": "org.openxmlformats.wordprocessingml.document",
        ".epub": "org.idpf.epub-container",
        ".txt": "public.plain-text",
        ".md": "net.daringfireball.markdown",
        ".markdown": "net.daringfireball.markdown",
    }
    assert set(adapter.SUPPORTED_SUFFIXES) == set(ingestible), adapter.SUPPORTED_SUFFIXES
    assert utis == set(ingestible.values()), utis
    for unsupported in ("com.microsoft.word.doc",):
        assert unsupported not in utis, f"{unsupported} 会被声明成可打开，但导入层拒绝它"
