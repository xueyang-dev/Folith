"""重新生成 Folith·译页的**补充**品牌变体（SVG 源 → PNG）。

正式品牌素材由用户提供、**不由本脚本管理，也不要在这里覆盖**：

    folith-lockup.png     主横向组合（README 首屏、侧栏品牌位）
    folith-mark.png       图标（透明底，页面图标）
    folith-app-icon.png   App 图标（海军蓝圆角方块）

本脚本只负责 kit 没覆盖的补充变体——源文件是
`folith/resources/brand/` 下的两个 SVG（`folith-mark.svg` 彩色图标、
`folith-mark-mono.svg` 单色图标）。改了 SVG 之后必须能一条命令重新生成：
手工导出的位图会漂移，很快就不再等于向量源，而深色底材料、窄栏版位、
单色场景又都在引用这些 PNG。

渲染走本机已有的 Chromium（与 `scripts/ui_screenshot.py` 同一套依赖），
不引入额外的图像库：

    FOLIO_PLAYWRIGHT_CORE  playwright-core 的路径
    FOLIO_CHROME           Chrome/Chromium 可执行文件路径

用法：

    python scripts/render_brand_assets.py            # 重新生成补充变体
    python scripts/render_brand_assets.py --extra    # 附带单色版预览
    python scripts/render_brand_assets.py --check    # 只校验产物是否比向量源新

产物（全部是向量派生）：

    folith-logo-dark.png    深色底横向组合
    folith-logo-stacked.png 竖向组合（窄栏 / 方形版位）
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BRAND = ROOT / "folith" / "resources" / "brand"

DEFAULT_PLAYWRIGHT_CANDIDATES = (
    "~/Dev/grok-workspace/node_modules/playwright-core",
    "~/.hermes/hermes-agent/node_modules/playwright-core",
)
CHROME_GLOB = ("~/Library/Caches/ms-playwright/chromium-*/chrome-mac-arm64/"
               "*.app/Contents/MacOS/*")

# ---- 唯一调色板 ----------------------------------------------------------
# 取色自品牌源文件；app.py 里对应的 --tp-* 变量必须与本表一致。
PALETTE = {
    "navy": "#000D2D",      # 字标 / App 图标底
    "cobalt": "#004CFD",    # 后页（深蓝端）
    "azure": "#0088FD",     # 渐变中段
    "cyan": "#00E8FE",      # 渐变亮端
    "sub": "#3E495D",       # 浅色底副标题
    "sub_dark": "#A9B6C9",  # 深色底副标题
    "on_dark": "#FFFFFF",
}

FONT_STACK = ("Manrope, 'Helvetica Neue', Helvetica, Arial, "
              "'PingFang SC', 'Noto Sans SC', sans-serif")
FONT_CSS = ("https://fonts.googleapis.com/css2?family=Manrope:wght@400;500;600;700;800"
            "&display=swap")

WORDMARK_EN = "Folith·译页"
TAGLINE_EN = "Agentic Translation Workspace"
TAGLINE_ZH = "智能体翻译工作台"

# 横向组合的版式，单位是「图标高度 = 268」，可无级缩放。
LOCKUP = {"gap": 44, "size": 76, "tag_en": 40, "tag_zh": 38,
          "line_gap": 16, "tag_gap": 12, "text_w": 1180}

_SCRIPT = r"""
const PW = process.env.FOLIO_PLAYWRIGHT_CORE;
const CHROME = process.env.FOLIO_CHROME;
const { chromium } = require(PW);

(async () => {
  const jobs = JSON.parse(process.env.FOLIO_BRAND_JOBS);
  const browser = await chromium.launch({
    executablePath: CHROME, args: ['--no-sandbox', '--disable-gpu'],
  });
  const page = await browser.newPage({ viewport: { width: 1600, height: 900 },
                                       deviceScaleFactor: 1 });
  const errors = [];
  page.on('pageerror', (e) => errors.push(e.message));
  for (const job of jobs) {
    await page.setViewportSize({ width: job.w, height: job.h });
    await page.setContent(job.html, { waitUntil: 'load' });
    try { await page.evaluate(() => document.fonts.ready); } catch (e) {}
    await page.waitForTimeout(160);
    const el = await page.$(job.selector);
    if (!el) throw new Error('missing selector: ' + job.selector);
    await el.screenshot({ path: job.out, omitBackground: true });
    console.log('rendered', job.out);
  }
  if (errors.length) { console.error('[pageerror]', errors.join(' | ')); process.exit(1); }
  await browser.close();
})().catch((e) => { console.error('FAILED', e.message); process.exit(1); });
"""


def _resolve_playwright() -> str | None:
    explicit = os.environ.get("FOLIO_PLAYWRIGHT_CORE")
    if explicit and Path(explicit).is_dir():
        return explicit
    for candidate in DEFAULT_PLAYWRIGHT_CANDIDATES:
        path = Path(candidate).expanduser()
        if (path / "package.json").is_file():
            return str(path)
    return shutil.which("playwright-core")


def _resolve_chrome() -> str | None:
    explicit = os.environ.get("FOLIO_CHROME")
    if explicit and Path(explicit).is_file():
        return explicit
    for match in sorted(Path("~").expanduser().glob(CHROME_GLOB.replace("~/", ""))):
        if match.is_file():
            return str(match)
    for name in ("Google Chrome", "Chromium"):
        found = shutil.which(name)
        if found:
            return found
    return None


def _page(body: str, width: int, height: int) -> str:
    return f"""<!doctype html><html><head><meta charset="utf-8">
<link rel="stylesheet" href="{FONT_CSS}">
<style>
  html, body {{ margin: 0; padding: 0; background: transparent; }}
  body {{ font-family: {FONT_STACK}; -webkit-font-smoothing: antialiased; }}
  #stage {{ width: {width}px; height: {height}px; display: flex;
            align-items: center; justify-content: center; }}
  .lockup {{ display: flex; align-items: center; }}
  .lockup .text {{ display: flex; flex-direction: column; }}
  .wordmark {{ font-weight: 800; letter-spacing: -.022em; line-height: .9; }}
  .tag {{ font-weight: 600; }}
</style></head><body><div id="stage">{body}</div></body></html>"""


def _data_uri(path: Path) -> str:
    return ("data:image/svg+xml;base64,"
            + base64.b64encode(path.read_bytes()).decode("ascii"))


def _inline_svg(path: Path, color: str | None = None) -> str:
    """把 SVG 内联进 HTML；color 会覆盖 currentColor 的单色版颜色。"""
    svg = path.read_text(encoding="utf-8")
    svg = svg.replace("<svg ", '<svg style="display:block;width:100%;height:100%" ', 1)
    if color:
        svg = svg.replace("<svg ", f'<svg color="{color}" ', 1)
    return svg


def _lockup(mark_href: str, on_dark: bool, *, wordmark: str,
            taglines: tuple[tuple[str, int, str], ...]) -> tuple[str, int, int]:
    icon_h, icon_w = 268, 274
    width = icon_w + LOCKUP["gap"] + LOCKUP["text_w"]
    height = icon_h
    word = PALETTE["on_dark"] if on_dark else PALETTE["navy"]
    sub = PALETTE["sub_dark"] if on_dark else PALETTE["sub"]
    tags = "".join(
        f'<div class="tag" style="font-size:{size}px;color:{sub};'
        f'margin-top:{LOCKUP["line_gap"] if index == 0 else LOCKUP["tag_gap"]}px;'
        f'letter-spacing:{spacing}">{text}</div>'
        for index, (text, size, spacing) in enumerate(taglines))
    body = (
        f'<div class="lockup">'
        f'<img src="{mark_href}" width="{icon_w}" height="{icon_h}" alt="">'
        f'<div class="text" style="margin-left:{LOCKUP["gap"]}px">'
        f'<div class="wordmark" style="font-size:{LOCKUP["size"]}px;color:{word}">'
        f'{wordmark}</div>{tags}'
        f'</div></div>')
    return _page(body, width, height), width, height


def _stacked(mark_href: str, on_dark: bool) -> tuple[str, int, int]:
    """竖向组合：图标在上、字标与中英副标题居中在下（侧栏品牌位用）。"""
    width, icon = 560, 168
    word = PALETTE["on_dark"] if on_dark else PALETTE["navy"]
    sub = PALETTE["sub_dark"] if on_dark else PALETTE["sub"]
    body = (
        f'<div style="display:flex;flex-direction:column;align-items:center;'
        f'width:{width}px">'
        f'<img src="{mark_href}" width="{icon}" height="{int(icon * 268 / 274)}" alt="">'
        f'<div class="wordmark" style="font-size:50px;color:{word};margin-top:18px">'
        f'{WORDMARK_EN}</div>'
        f'<div class="tag" style="font-size:25px;color:{sub};margin-top:10px;'
        f'letter-spacing:-.01em">{TAGLINE_EN}</div>'
        f'<div class="tag" style="font-size:24px;color:{sub};margin-top:8px;'
        f'letter-spacing:.14em">{TAGLINE_ZH}</div>'
        f'</div>')
    return _page(body, width, 372), width, 372


def _mark_block(svg: str, size: int, tile: str | None = None,
                radius: int = 0, inner: float = 1.0) -> tuple[str, int, int]:
    box = size
    if tile:
        body = (f'<div style="width:{box}px;height:{box}px;border-radius:{radius}px;'
                f'background:{tile};display:flex;align-items:center;'
                f'justify-content:center">'
                f'<div style="width:{box * inner:.1f}px;height:{box * inner:.1f}px">'
                f'{svg}</div></div>')
    else:
        body = f'<div style="width:{box}px;height:{box}px">{svg}</div>'
    return _page(body, box, box), box, box


def _jobs(extra: bool) -> list[dict]:
    color_mark = BRAND / "folith-mark.svg"
    mono_mark = BRAND / "folith-mark-mono.svg"
    for path in (color_mark, mono_mark):
        if not path.is_file():
            raise SystemExit(f"[错误] 缺少品牌源文件：{path}")
    href = _data_uri(color_mark)
    jobs: list[dict] = []

    english_tags = ((TAGLINE_EN, LOCKUP["tag_en"], "-.01em"),
                    (TAGLINE_ZH, LOCKUP["tag_zh"], ".14em"))
    html, w, h = _lockup(href, on_dark=True, wordmark=WORDMARK_EN,
                          taglines=english_tags)
    jobs.append({"html": html, "w": w, "h": h, "selector": "#stage",
                 "out": str(BRAND / "folith-logo-dark.png")})

    html, w, h = _stacked(href, on_dark=False)
    jobs.append({"html": html, "w": w, "h": h, "selector": "#stage",
                 "out": str(BRAND / "folith-logo-stacked.png")})

    if extra:
        # 单色版：深色底用白色、浅色底用品牌深蓝（字形是背景色镂空）
        for name, color, tile in (("_mono-on-dark.png", PALETTE["on_dark"], PALETTE["navy"]),
                                  ("_mono-on-light.png", PALETTE["navy"], None)):
            html, w, h = _mark_block(_inline_svg(mono_mark, color), 512, tile=tile,
                                     radius=115 if tile else 0)
            jobs.append({"html": html, "w": w, "h": h, "selector": "#stage",
                         "out": str(BRAND / name)})
    return jobs


def _render(jobs: list[dict]) -> int:
    if shutil.which("node") is None:
        print("[错误] 未找到 node。品牌位图渲染需要 node + playwright-core。",
              file=sys.stderr)
        return 2
    playwright, chrome = _resolve_playwright(), _resolve_chrome()
    if playwright is None or chrome is None:
        print("[错误] 未找到 playwright-core 或 Chrome；请设置 "
              "FOLIO_PLAYWRIGHT_CORE / FOLIO_CHROME。", file=sys.stderr)
        return 2
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as handle:
        handle.write(_SCRIPT)
        script = handle.name
    env = {**os.environ, "FOLIO_PLAYWRIGHT_CORE": playwright,
           "FOLIO_CHROME": chrome, "FOLIO_BRAND_JOBS": json.dumps(jobs)}
    try:
        result = subprocess.run(["node", script], env=env, capture_output=True,
                                text=True)
    finally:
        Path(script).unlink(missing_ok=True)
    if result.stdout.strip():
        print(result.stdout.strip())
    if result.returncode != 0:
        if result.stderr.strip():
            print(result.stderr.strip(), file=sys.stderr)
        return result.returncode
    return 0


OUTPUTS = ("folith-logo-dark.png", "folith-logo-stacked.png")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--extra", action="store_true",
                        help="额外输出单色图标预览（_mono-*.png）")
    parser.add_argument("--check", action="store_true",
                        help="只校验产物是否存在且比向量源新")
    args = parser.parse_args(argv)

    if args.check:
        sources = [BRAND / "folith-mark.svg",
                   BRAND / "folith-mark-mono.svg"]
        newest = max(p.stat().st_mtime for p in sources if p.is_file())
        missing = [n for n in OUTPUTS if not (BRAND / n).is_file()]
        stale = [n for n in OUTPUTS
                 if (BRAND / n).is_file() and (BRAND / n).stat().st_mtime < newest]
        if missing or stale:
            print(f"[失败] 品牌位图缺失 {missing} / 落后于向量源 {stale}；"
                  f"请运行 python scripts/render_brand_assets.py", file=sys.stderr)
            return 1
        print(f"品牌位图与向量源一致 ✅（{len(OUTPUTS)} 个产物）")
        return 0

    code = _render(_jobs(args.extra))
    if code:
        return code
    for name in OUTPUTS:
        path = BRAND / name
        print(f"  {path.relative_to(ROOT)}  {path.stat().st_size / 1024:.0f} KB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
