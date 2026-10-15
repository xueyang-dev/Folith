"""品牌资产一致性测试：Folith logo 只有一个真源，位图必须是它的派生物。

为什么需要它：Folith 的品牌资产在界面侧栏、README 和浏览器标签页各有一个
正式入口。这些位置各自独立，一旦只换了其中一个，品牌就会在界面上悄悄破功。
本模块把这件事变成可执行的约束：

1. 向量源存在且自洽（viewBox、渐变、没有外部字体依赖的关键字形）；
2. 位图产物存在，且不比向量源旧（`scripts/render_brand_assets.py --check` 的语义）；
3. 设计系统色板 = 品牌源文件里的取色，而不是各表面各写一个蓝；
4. 界面/README 引用的路径真实存在；
5. 旧裁切资源仍存在，但不再作为当前产品入口。

不做像素比对：那需要浏览器，属于 `scripts/render_brand_assets.py --check` 的职责，
CI 里不应依赖 Chromium。

运行：`python -m pytest tests/brand_assets_test.py -q`
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

ROOT = Path(__file__).resolve().parent.parent
BRAND = ROOT / "folith" / "resources" / "brand"
BRAND_STANDARD = ROOT / "docs" / "assets" / "folith-brand-standard.png"
MARK = BRAND / "folith-mark.svg"
MARK_MONO = BRAND / "folith-mark-mono.svg"
FAVICON = BRAND / "folith-favicon.svg"

# 品牌色板：唯一真源是 logo 源文件，界面 token 必须与之一致。
PALETTE = {
    "navy": "#000d2d",     # 字标 / App 图标底
    "cobalt": "#004cfd",   # 后页
    "azure": "#0088fd",    # 渐变中段
    "cyan": "#00e8fe",     # 渐变亮端
}

# 正式品牌素材：由用户提供，界面与 README 直接使用，不由渲染脚本生成。
OFFICIAL_PNG = (
    "folith-lockup.png",    # 主横向组合（README 首屏、侧栏品牌位）
    "folith-mark.png",      # 图标（透明底，页面图标）
    "folith-app-icon.png",  # App 图标
    "folith-mark-hires.png",  # 图标高清母版（用户提供，1254×1254）
)
# 补充变体：由 scripts/render_brand_assets.py 从 SVG 源渲染（kit 未覆盖的版位）。
DERIVED_PNG = (
    "folith-logo-dark.png",
    "folith-logo-stacked.png",
)
# 已被正式素材取代的派生文件必须消失——同一版位不允许两个真源。
RETIRED_PNG = (
    "folith-logo.png",
    "folith-logo-zh.png",
    "folith-favicon.png",
)
# 尺寸写死是为了让"有人手工换了一张别的图"立刻失败。
BRAND_PNG_SIZE = {
    "folith-lockup.png": (1187, 328),
    "folith-mark.png": (278, 248),
    "folith-app-icon.png": (273, 257),
    "folith-mark-hires.png": (1254, 1254),
    "folith-app-icon-white.png": (1024, 1024),
    "folith-logo-dark.png": (1498, 268),
    "folith-logo-stacked.png": (560, 372),
}


def _png_size(path: Path) -> tuple[int, int]:
    """从 PNG 头里读宽高（IHDR 固定在第 16..24 字节），不需要图像库。"""
    header = path.read_bytes()[:24]
    assert header[:8] == b"\x89PNG\r\n\x1a\n", f"{path.name} 不是 PNG"
    assert header[12:16] == b"IHDR", f"{path.name} 缺少 IHDR"
    return (int.from_bytes(header[16:20], "big"),
            int.from_bytes(header[20:24], "big"))


def _read(path: Path) -> str:
    assert path.is_file(), f"缺少品牌文件：{path.relative_to(ROOT)}"
    return path.read_text(encoding="utf-8")


def test_mark_source_shape():
    svg = _read(MARK)
    assert 'viewBox="0 0 274 268"' in svg, "图标画布尺寸变了，位图必须重新生成"
    assert 'role="img"' in svg and "<title" in svg and "<desc" in svg, \
        "品牌图标需要无障碍标题与描述"
    # 两页 + 光标 + 文/A：缺任何一个都不再是 Folith 的图标
    assert svg.count("<rect") == 3, \
        "图标应是两页圆角矩形 + 一层前页高光；新增/删除图层需同步更新本断言"
    assert svg.count("<path") == 1, "图标应只有一个光标路径"
    assert "文" in svg and ">A<" in svg, "图标必须保留 文 / A 两个字形"


def test_mark_uses_palette():
    """图标本体只用渐变三色；深海军蓝属于字标/App 图标底，不在图标里。"""
    svg = _read(MARK).lower()
    for name in ("cobalt", "cyan"):
        assert PALETTE[name] in svg, f"图标源里找不到品牌色 {name}={PALETTE[name]}"


def test_mark_has_no_remote_font_dependency():
    """图标要能离线渲染：可以挑字体家族，但不能 @import 远程字体。"""
    svg = _read(MARK)
    assert "@import" not in svg and "url(http" not in svg


def test_mono_mark_is_single_color():
    svg = _read(MARK_MONO)
    assert "currentColor" in svg, "单色版必须用 currentColor，才能跟随宿主前景色"
    colors = {c.lower() for c in re.findall(r'#[0-9A-Fa-f]{6}', svg)}
    # 只允许掩膜里的黑/白，其余一律 currentColor
    assert colors <= {"#ffffff", "#000000"}, \
        f"单色版混入了彩色：{sorted(colors - {'#ffffff', '#000000'})}"


def test_favicon_matches_mark_palette():
    svg = _read(FAVICON).lower()
    assert PALETTE["navy"] in svg, "标签页图标的底色必须是品牌深海军蓝"
    assert PALETTE["cobalt"] in svg and PALETTE["cyan"] in svg, \
        "标签页图标必须复用图标的渐变"


def test_brand_bitmaps_exist_and_are_real():
    """正式素材与补充变体都必须存在，且是尺寸正确的真实 PNG。

    刻意不比较 mtime：CI 从 git 检出时所有文件时间戳相同，按时间判"是否过期"
    会变成随机失败。真正的重生成检查是 `python scripts/render_brand_assets.py
    --check`（需要 Chromium，不进 CI）。
    """
    missing = [n for n in OFFICIAL_PNG if not (BRAND / n).is_file()]
    assert not missing, f"缺少用户提供的正式品牌素材：{missing}"
    missing = [n for n in DERIVED_PNG if not (BRAND / n).is_file()]
    assert not missing, (f"缺少品牌位图 {missing}；"
                         f"运行 python scripts/render_brand_assets.py")
    for name, (want_w, want_h) in BRAND_PNG_SIZE.items():
        path = BRAND / name
        assert path.stat().st_size > 4_000, \
            f"{name} 只有 {path.stat().st_size} 字节，像是占位文件"
        width, height = _png_size(path)
        assert (width, height) == (want_w, want_h), \
            f"{name} 尺寸为 {width}x{height}，期望 {want_w}x{want_h}"


def test_superseded_derived_assets_are_retired():
    """正式素材取代的派生文件不得留下，渲染脚本也不得再产出它们。"""
    for name in RETIRED_PNG:
        assert not (BRAND / name).exists(), \
            f"{name} 已被正式品牌素材取代，不应再存在（同一版位不能有两个真源）"
    from scripts.render_brand_assets import OUTPUTS
    assert set(OUTPUTS) == set(DERIVED_PNG), \
        f"渲染脚本产物 {sorted(OUTPUTS)} 与期望的补充变体 {sorted(DERIVED_PNG)} 不一致"


def test_brand_standard_reference_is_preserved():
    """用户提供的品牌标准板是规范参考（docs/brand.md 引用），必须保留原图。"""
    assert BRAND_STANDARD.is_file(), "品牌标准 PNG 缺失"
    assert BRAND_STANDARD.stat().st_size > 100_000, "品牌标准 PNG 不是完整参考图"
    assert _png_size(BRAND_STANDARD) == (1448, 1086)


def test_app_tokens_match_brand_palette():
    """app.py 的 :root 品牌 token 必须等于品牌源文件的取色。"""
    app = (ROOT / "app.py").read_text(encoding="utf-8")
    if ":root {" not in app:
        app = (ROOT / "folith" / "ui" / "styles.py").read_text(encoding="utf-8")
    block = app.split(":root {", 1)[1].split("}", 1)[0]

    def token(name: str) -> str:
        match = re.search(rf"--{name}:\s*(#[0-9A-Fa-f]{{6}})", block)
        assert match, f"app.py 设计系统缺少 --{name}"
        return match.group(1).lower()

    assert token("tp-logo-blue") == PALETTE["cobalt"]
    assert token("tp-primary") == PALETTE["cobalt"]
    assert token("tp-navy") == PALETTE["navy"]
    assert token("tp-azure") == PALETTE["azure"]
    assert token("tp-cyan") == PALETTE["cyan"]
    # hover / active 必须是同一色的更深阶，不能引入第二种蓝
    assert token("tp-primary-hover") < PALETTE["cobalt"], "hover 应比主色更深"
    assert token("tp-primary-active") < token("tp-primary-hover"), \
        "active 应比 hover 更深"


def test_streamlit_theme_matches_primary():
    gui = (ROOT / "gui.py").read_text(encoding="utf-8")
    assert f'"--theme.primaryColor", "{PALETTE["cobalt"]}"' in gui, \
        "Streamlit 主题主色必须与设计系统主色一致"

    app = (ROOT / "app.py").read_text(encoding="utf-8")
    assert 'page_icon=_BRAND_FAVICON' in app, "页面图标必须用品牌图标资产"


def test_sidebar_uses_formal_lockup():
    """侧栏与页面图标使用用户提供的正式素材，避免回退到派生图或旧裁切图。"""
    app = (ROOT / "app.py").read_text(encoding="utf-8")
    assert '"folith-lockup-transparent.png"' in app, "侧栏应使用正式原图去白底的 PNG"
    assert '"folith-mark.png"' in app, "页面图标应使用正式图标素材"
    assert '译页 智能体翻译工作台' in app, \
        "品牌位的替代文本要与新定位一致"


def test_legacy_source_crops_are_retained_but_not_current_entries():
    """旧裁切图供历史/兼容读取，当前 UI 不得继续引用它们。"""
    for name in ("foliothread-source-lockup.png", "foliothread-source-icon.png"):
        assert (BRAND / name).is_file(), f"兼容资源缺失：{name}"
    app = (ROOT / "app.py").read_text(encoding="utf-8")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    for legacy in ("foliothread-source-lockup.png", "foliothread-source-icon.png"):
        assert legacy not in app and legacy not in readme, \
            f"当前入口不应继续引用旧品牌裁切图：{legacy}"


def test_readme_references_existing_logo():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "folith/resources/brand/folith-lockup.png" in readme, \
        "README 首屏必须展示用户提供的正式主 lockup"
    for rel in re.findall(r'src="(folith/resources/brand/[^"]+)"', readme):
        assert (ROOT / rel).is_file(), f"README 引用了不存在的资产：{rel}"


def main() -> None:
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"  ✓ {name}")
    print("品牌资产一致性测试通过 ✅")


if __name__ == "__main__":
    main()
