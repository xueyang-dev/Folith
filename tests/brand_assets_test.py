"""品牌资产一致性测试：Folith logo 只有一个真源，位图必须是它的派生物。

为什么需要它：Folith 的品牌资产在界面侧栏、README 和浏览器标签页各有一个
正式入口。这些位置各自独立，一旦只换了其中一个，品牌就会在界面上悄悄破功。
本模块把这件事变成可执行的约束：

1. 向量源只有一份，等于标准稿（viewBox、渐变、无外部字体依赖）；
2. 位图产物存在且尺寸正确，页面图标等于母版的派生（`render_brand_assets.py --check`）；
3. 设计系统色板 = 品牌源文件里的取色，而不是各表面各写一个蓝；
4. 界面/README 引用的路径真实存在；
5. 旧几何 / 旧产品名资源已删除。

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
STANDARD_SOURCE = ROOT / "docs" / "logo-exploration" / "folith-mark-final.svg"

# 品牌色板：唯一真源是 logo 源文件，界面 token 必须与之一致。
PALETTE = {
    "navy": "#000d2d",     # 字标 / App 图标底
    "cobalt": "#004cfd",   # 后页
    "azure": "#0088fd",    # 渐变中段
    "cyan": "#00e8fe",     # 渐变亮端
}
#: 日间界面主色：与 logo 钴蓝同色相、降低彩度（2026-10-16 色彩审校）。logo 本身不动。
UI_PRIMARY = "#2b5fd1"

# 标准标记（2026-10-16 修正几何）的位图产物与派生物；全部来自同一个向量源。
BRAND_PNG = (
    "folith-mark-hires.png",   # 1254×1254 母版，由 folith-mark-final.svg 渲染
    "folith-mark.png",         # 页面图标，scripts/render_brand_assets.py 从母版派生
    "folith-app-icon-white.png",  # App 图标，scripts/build_app_icon.py 从母版合成
    "folith-lockup.png",       # README 首屏（白底），scripts/extract_brand_png.py 派生
    "folith-lockup-transparent.png",
    "folith-lockup-transparent-dark.png",
)
# 旧几何或旧产品名的文件必须消失——全仓库只允许一个 logo。
RETIRED_ASSETS = (
    "folith-mark-mono.svg", "folith-mark-blue.svg", "folith-favicon.svg",
    "folith-app-icon.png", "folith-logo-dark.png", "folith-logo-stacked.png",
    "_mono-on-dark.png", "_mono-on-light.png",
    "folith-logo.png", "folith-logo-zh.png", "folith-favicon.png",
)
# 尺寸写死是为了让"有人手工换了一张别的图"立刻失败。
BRAND_PNG_SIZE = {
    "folith-lockup.png": (1187, 328),
    "folith-lockup-transparent.png": (1187, 328),
    "folith-lockup-transparent-dark.png": (1187, 328),
    "folith-mark.png": (256, 256),
    "folith-mark-hires.png": (1254, 1254),
    "folith-app-icon-white.png": (1024, 1024),
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
    assert 'viewBox="0 0 1254 1254"' in svg, "图标画布尺寸变了，位图必须重新生成"
    assert 'role="img"' in svg and "<title" in svg, "品牌图标需要无障碍标题"
    assert svg.count("<path") >= 2, "两页加白色字形层"
    assert svg.count("linearGradient") >= 2, "两页各有一条渐变"


def test_single_vector_source_is_the_standard_mark():
    """向量源只有一份：随包的 folith-mark.svg 逐字节等于 docs 里的标准稿。"""
    assert MARK.read_bytes() == STANDARD_SOURCE.read_bytes()
    assert sorted(p.name for p in STANDARD_SOURCE.parent.iterdir()
                  if p.suffix == ".svg") == ["folith-mark-final.svg"],         "docs/logo-exploration 里不得保留其他 logo 变体"


def test_accent_variants_only_change_gradient_stops():
    """翠绿 / 石板主题色版本与标准稿形状完全相同，只换渐变色标。"""
    def shape(svg: str) -> list[str]:
        return re.findall(r' d="([^"]+)"', svg)
    standard = shape(_read(MARK))
    for name in ("folith-mark-green.svg", "folith-mark-slate.svg"):
        assert shape(_read(BRAND / name)) == standard, f"{name} 的几何偏离了标准标记"


def test_mark_uses_palette():
    """渐变端点是品牌钴蓝 / 青（标准稿的色标与色板仅差 1 个色阶）。"""
    svg = _read(MARK).lower()
    for stop in ("#0044fc", "#01f6fc"):
        assert stop in svg, f"图标源里找不到渐变端点 {stop}"


def test_mark_has_no_remote_font_dependency():
    svg = _read(MARK)
    assert "@import" not in svg and "url(http" not in svg


def test_brand_bitmaps_exist_and_are_real():
    """所有位图存在且尺寸正确。不比较 mtime：CI 检出时间戳相同。"""
    missing = [n for n in BRAND_PNG if not (BRAND / n).is_file()]
    assert not missing, f"缺少品牌位图：{missing}"
    for name, (want_w, want_h) in BRAND_PNG_SIZE.items():
        path = BRAND / name
        assert path.stat().st_size > 4_000,             f"{name} 只有 {path.stat().st_size} 字节，像是占位文件"
        assert _png_size(path) == (want_w, want_h), f"{name} 尺寸不对"


def test_page_icon_is_derived_from_master():
    from scripts.render_brand_assets import main
    assert main(["--check"]) == 0


def test_retired_and_legacy_assets_are_gone():
    for name in RETIRED_ASSETS:
        assert not (BRAND / name).exists(), f"{name} 已退役，不应再存在"
    assert not list(BRAND.glob("foliothread-*")), "旧产品名素材不得随包保留"
    assert sorted(p.name for p in BRAND.glob("*.svg")) == [
        "folith-mark-green.svg", "folith-mark-slate.svg", "folith-mark.svg"]


def test_brand_standard_reference_is_preserved():
    """用户提供的品牌标准板是规范参考（docs/brand.md 引用），必须保留原图。"""
    assert BRAND_STANDARD.is_file(), "品牌标准 PNG 缺失"
    assert BRAND_STANDARD.stat().st_size > 100_000, "品牌标准 PNG 不是完整参考图"
    assert _png_size(BRAND_STANDARD) == (1448, 1086)


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
