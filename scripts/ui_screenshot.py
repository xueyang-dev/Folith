#!/usr/bin/env python3
"""给运行中的译页界面截图（UI 改动的视觉验证工具）。

为什么需要它：界面改动的验收不能只看 `AppTest` 的元素树——纵向留白、文字截断、
列宽这类问题只有在真实渲染里才看得出来。这个脚本让"改完看一眼"变成一条命令。

依赖（本机已有的浏览器，不需要额外安装）：

    FOLIO_PLAYWRIGHT_CORE  playwright-core 的路径
    FOLIO_CHROME           Chrome/Chromium 可执行文件路径
    FOLIO_URL              应用地址，默认 http://127.0.0.1:8501

用法：

    # 首屏
    python scripts/ui_screenshot.py --out /tmp/shot.png

    # 打开某个任务的某个工作区页面（按最近更新取第一个任务）
    python scripts/ui_screenshot.py --out /tmp/translate.png --section 翻译

缺少依赖时会打印可操作的提示并返回非零，不会静默失败。
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

DEFAULT_PLAYWRIGHT_CANDIDATES = (
    "~/Dev/grok-workspace/node_modules/playwright-core",
    "~/.hermes/hermes-agent/node_modules/playwright-core",
)
CHROME_GLOB = "~/Library/Caches/ms-playwright/chromium-*/chrome-mac-arm64/*.app/Contents/MacOS/*"

_SCRIPT = r"""
const PW = process.env.FOLIO_PLAYWRIGHT_CORE;
const CHROME = process.env.FOLIO_CHROME;
const { chromium } = require(PW);
const [url, out, section, waitMs, view, widthsArg, metricsPath, heightArg] = process.argv.slice(2);
const viewportHeight = Number(heightArg || 900);

(async () => {
  const browser = await chromium.launch({
    executablePath: CHROME, args: ['--no-sandbox', '--disable-gpu'],
  });
  const page = await browser.newPage({
    viewport: { width: 1440, height: viewportHeight }, deviceScaleFactor: Number(process.env.DEVICE_SCALE_FACTOR || 2),
  });
  page.on('pageerror', (e) => console.error('[pageerror]', e.message));
  await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 60000 });
  await page.waitForSelector('[data-testid="stAppViewContainer"]', { timeout: 30000 });
  await page.waitForTimeout(3500);

  const sidebar = page.locator('[data-testid="stSidebar"]');

  if (view) {
    if (view === 'projects') {
      const btn = sidebar.locator('[class*="st-key-project_section_header_button"] button, button:has-text("项目")').first();
      if (await btn.count()) { await btn.click(); await page.waitForTimeout(3000); }
      else console.error('[warn] 未找到项目中心按钮');
    } else if (view === 'new') {
      const btn = sidebar.locator('[class*="st-key-new_task_action"] button, button:has-text("新建任务")').first();
      if (await btn.count()) { await btn.click(); await page.waitForTimeout(3000); }
      else console.error('[warn] 未找到新建任务按钮');
    } else if (view === 'history') {
      const btn = sidebar.locator('button:has-text("历史任务")').first();
      if (await btn.count()) { await btn.click(); await page.waitForTimeout(3000); }
      else console.error('[warn] 未找到历史任务按钮');
    } else if (view === 'library') {
      const btn = sidebar.locator('button:has-text("术语与翻译记忆")').first();
      if (await btn.count()) { await btn.click(); await page.waitForTimeout(3000); }
      else console.error('[warn] 未找到语言资产按钮');
    } else if (view === 'settings') {
      const btn = sidebar.locator('[class*="st-key-manage_provider"] button, button:has-text("管理")').first();
      if (await btn.count()) { await btn.click(); await page.waitForTimeout(3000); }
      else console.error('[warn] 未找到设置管理按钮');
    }
  } else if (section) {
    const history = sidebar.locator('button:has-text("历史任务")').first();
    if (await history.count()) { await history.click(); await page.waitForTimeout(2500); }
    // The full-card hit target always enters Translation.  A task's secondary
    // CTA may instead jump to Review or Delivery, which is the wrong smoke-test
    // starting point.
    const openTaskBtn = page.locator('[class*="st-key-history_card_"] button').first();
    const action = /打开任务|继续审校|查看交付|准备交付|继续处理|继续翻译|更新报告|查看进度|打开/;
    const actionBtn = (await openTaskBtn.count())
      ? openTaskBtn
      : page.locator('button').filter({ hasText: action }).first();
    if (await actionBtn.count()) { await actionBtn.click(); await page.waitForTimeout(3500); }
    if (!(section === '翻译' && await page.locator('.tp-cat-head').count())) {
      const navRoot = page.locator('.st-key-workspace_nav').first();
      const nav = (await navRoot.count())
        ? navRoot.locator('button').filter({ hasText: new RegExp(`^${section}$`) }).first()
        : page.locator('button').filter({ hasText: new RegExp(`^${section}$`) }).first();
      if (await nav.count() && await nav.isVisible()) {
        await nav.click(); await page.waitForTimeout(3000);
      } else console.error('[warn] 未找到可见导航项:', section);
    }
  }
  await page.waitForTimeout(Number(waitMs || 2500));

  const measure = async () => page.evaluate(() => {
    const box = (selector) => {
      const el = document.querySelector(selector);
      if (!el) return null;
      const r = el.getBoundingClientRect();
      return { x: Math.round(r.x), y: Math.round(r.y),
        width: Math.round(r.width), height: Math.round(r.height),
        bottom: Math.round(r.bottom) };
    };
    const main = box('[data-testid="stMainBlockContainer"]');
    const mainEl = document.querySelector('[data-testid="stMainBlockContainer"]');
    const firstSegment = document.querySelector('.tp-cat-rowcell[data-segment="0"]')
      ?.closest('[class*="st-key-cat_row_"]');
    const firstRect = firstSegment?.getBoundingClientRect();
    const row = firstSegment || document.querySelector('[class*="st-key-cat_row_"]');
    const columns = row ? [...row.querySelectorAll('[data-testid="stColumn"]')] : [];
    const rect = (el) => {
      if (!el) return null;
      const r = el.getBoundingClientRect();
      return { x: Math.round(r.x), y: Math.round(r.y),
        width: Math.round(r.width), height: Math.round(r.height) };
    };
    const topbar = document.querySelector('.st-key-workspace_topbar');
    const topbarColumns = topbar ? [...topbar.querySelectorAll('[data-testid="stColumn"]')]
      .map(el => rect(el)) : [];
    const source = row?.querySelector('.tp-cat-source')?.closest('[data-testid="stColumn"]');
    const target = row?.querySelector('textarea')?.closest('[data-testid="stColumn"]');
    const visibleRows = [...document.querySelectorAll('[class*="st-key-cat_row_"]')]
      .filter(el => { const r = el.getBoundingClientRect();
        return r.top < innerHeight && r.bottom > 0; }).length;
    const rowRects = [...document.querySelectorAll('[class*="st-key-cat_row_"]')]
      .slice(0, 8).map(el => {
        const r = el.getBoundingClientRect();
        const marker = el.querySelector('[data-segment]');
        return { segment: marker ? Number(marker.dataset.segment) : null,
          active: marker?.classList.contains('is-active') || false,
          density: marker?.classList.contains('is-row-two') ? 'two' :
            marker?.classList.contains('is-row-long') ? 'long' : 'one',
          y: Math.round(r.y), height: Math.round(r.height),
          textareas: [...el.querySelectorAll('textarea')].map(area => {
            const a = area.getBoundingClientRect();
            const cs = getComputedStyle(area);
            const root = area.closest('[data-testid="stTextAreaRootElement"]');
            const rr = root ? root.getBoundingClientRect() : null;
            return { height: Math.round(a.height), scrollHeight: area.scrollHeight,
              valueLength: area.value.length, cssHeight: cs.height,
              cssMinHeight: cs.minHeight, cssMaxHeight: cs.maxHeight,
              rootHeight: rr ? Math.round(rr.height) : null,
              inlineStyle: area.getAttribute('style') || '' };
          }) };
      });
    const rowAncestry = firstSegment ? (() => {
      const items = [];
      let node = firstSegment;
      for (let depth = 0; node && depth < 6; depth++, node = node.parentElement) {
        const r = node.getBoundingClientRect();
        const cs = getComputedStyle(node);
        items.push({ tag: node.tagName, testid: node.getAttribute('data-testid'),
          className: String(node.className || '').slice(0, 180),
          y: Math.round(r.y), height: Math.round(r.height),
          marginTop: cs.marginTop, marginBottom: cs.marginBottom,
          paddingTop: cs.paddingTop, paddingBottom: cs.paddingBottom,
          gap: cs.gap });
      }
      return items;
    })() : [];
    return {
      viewport: { width: innerWidth, height: innerHeight },
      main, taskHeader: box('.st-key-workspace_exit_actions'),
      mainStyle: mainEl ? { width: getComputedStyle(mainEl).width,
        maxWidth: getComputedStyle(mainEl).maxWidth } : null,
      topbar: rect(topbar),
      topbarCopy: box('.tp-workspace-topbar-copy'),
      taskMore: box('[class*="st-key-workspace_more_"]'),
      topbarColumns,
      workflowNav: box('.st-key-workspace_nav'),
      toolbar: box('.st-key-translation_toolbar'),
      issueSummary: box('.st-key-translation_issue_filters'),
      sourceWarning: box('.tp-source-quality-alert'),
      activeActions: box('.st-key-translation_editor_actions'),
      gridHeader: box('.tp-cat-head'),
      firstSegment: rect(firstSegment),
      firstSegmentY: firstRect ? Math.round(firstRect.top) : null,
      headerToSegment: main && firstRect ? Math.round(firstRect.top - main.y) : null,
      sourceWidth: rect(source)?.width ?? null,
      targetWidth: rect(target)?.width ?? null,
      rowColumnCount: columns.length,
      inspector: box('.st-key-workspace_context_col'),
      visibleSegments: visibleRows,
      rowRects,
      rowAncestry,
    };
  });
  const widths = (widthsArg || '').split(',').map(Number).filter(x => x > 0);
  const results = [];
  const path = require('path');
  const fs = require('fs');
  for (const width of (widths.length ? widths : [1440])) {
    await page.setViewportSize({ width, height: viewportHeight });
    await page.waitForTimeout(700);
    const measured = await measure();
    results.push({ state: 'inspector_closed', ...measured });
    const targetPath = widths.length
      ? path.join(path.dirname(out), `${path.parse(out).name}-${width}${path.extname(out) || '.png'}`)
      : out;
    await page.screenshot({ path: targetPath, fullPage: false });
    console.log('saved', targetPath);
  }

  // Browser-level persistence check: open the contextual Inspector, then close it
  // through the single toolbar control and verify the main editor regains its width.
  const toggle = page.locator('[class*="st-key-workspace_inspector_toggle_"] button').first();
  if (await toggle.count()) {
    await toggle.click();
    await page.waitForSelector('.st-key-workspace_context_col', { timeout: 10000 });
    await page.waitForTimeout(500);
    results.push({ state: 'inspector_open', ...(await measure()) });
    const collapse = page.locator('[class*="st-key-workspace_inspector_toggle_"] button').first();
    if (await collapse.count()) {
      await collapse.click();
      // Streamlit may retain an empty keyed wrapper after fragment replacement;
      // geometry (rather than node removal) tells us whether width was released.
      await page.waitForFunction(() => {
        const button = document.querySelector('[class*="st-key-workspace_inspector_toggle_"] button');
        return !button || (
          button.textContent.includes('检查器') &&
          !button.textContent.includes('收起')
        );
      }, null, { timeout: 10000 });
      results.push({ state: 'inspector_restored_closed', ...(await measure()) });
    }
  }
  if (metricsPath) fs.writeFileSync(metricsPath, JSON.stringify(results, null, 2));
  console.log(JSON.stringify(results));
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
    found = shutil.which("playwright-core")
    return found


def _resolve_chrome() -> str | None:
    explicit = os.environ.get("FOLIO_CHROME")
    if explicit and Path(explicit).is_file():
        return explicit
    for match in sorted(Path("~").expanduser().glob(
            CHROME_GLOB.replace("~/", ""))):
        if match.is_file():
            return str(match)
    for name in ("Google Chrome", "Chromium"):
        found = shutil.which(name)
        if found:
            return found
    mac_app = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
    if mac_app.is_file():
        return str(mac_app)
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, required=True, help="输出 PNG 路径")
    parser.add_argument("--view", default="",
                        help="系统主页面视图 (projects/new/history/library/settings)")
    parser.add_argument("--section", default="",
                        help="工作区页面（概览/翻译/术语/审校/交付）；留空则截首屏")
    parser.add_argument("--url", default=os.environ.get("FOLIO_URL",
                                                        "http://127.0.0.1:8501"))
    parser.add_argument("--wait-ms", default="2500", help="渲染后额外等待毫秒数")
    parser.add_argument("--widths", default="",
                        help="按逗号分隔的桌面 viewport 宽度；截图文件会追加宽度")
    parser.add_argument("--metrics", type=Path, default=None,
                        help="将 DOM 几何与可见段落数写入 JSON")
    parser.add_argument("--height", type=int, default=900,
                        help="viewport 高度（默认 900）")
    args = parser.parse_args(argv)

    if shutil.which("node") is None:
        print("[错误] 未找到 node。截图需要 node + playwright-core。", file=sys.stderr)
        return 2
    playwright = _resolve_playwright()
    if playwright is None:
        print("[错误] 未找到 playwright-core。请设置 FOLIO_PLAYWRIGHT_CORE 指向"
              "包含 package.json 的 playwright-core 目录。", file=sys.stderr)
        return 2
    chrome = _resolve_chrome()
    if chrome is None:
        print("[错误] 未找到 Chrome/Chromium。请设置 FOLIO_CHROME 指向可执行文件。",
              file=sys.stderr)
        return 2

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as handle:
        handle.write(_SCRIPT)
        script_path = handle.name
    env = {**os.environ, "FOLIO_PLAYWRIGHT_CORE": playwright, "FOLIO_CHROME": chrome}
    try:
        result = subprocess.run(
            ["node", script_path, args.url, str(args.out), args.section,
             str(args.wait_ms), args.view, args.widths,
             str(args.metrics) if args.metrics else "", str(args.height)],
            env=env, capture_output=True, text=True)
    finally:
        Path(script_path).unlink(missing_ok=True)

    if result.stdout.strip():
        print(result.stdout.strip())
    if result.returncode != 0:
        if result.stderr.strip():
            print(result.stderr.strip(), file=sys.stderr)
        return result.returncode
    screenshot_paths = ([args.out] if not args.widths else [
        args.out.with_name(f"{args.out.stem}-{width}{args.out.suffix or '.png'}")
        for width in args.widths.split(",") if width.strip().isdigit()
    ])
    missing = [path for path in screenshot_paths if not path.is_file()]
    if missing:
        print(f"[错误] 未生成截图：{missing[0]}", file=sys.stderr)
        return 1
    for path in screenshot_paths:
        print(f"尺寸 {path.stat().st_size / 1024:.0f} KB -> {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
