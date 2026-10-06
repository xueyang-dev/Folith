#!/usr/bin/env node
/**
 * D2 file-integration check: the delivery page must offer native "reveal in
 * file manager" for a delivered snapshot file, and report the outcome.
 *
 * Usage: node desktop_d2_file_actions.js --url URL --job JOB --out-dir DIR --result FILE
 */
'use strict';

const fs = require('fs');
const path = require('path');

const PW = process.env.FOLIO_PLAYWRIGHT_CORE;
const CHROME = process.env.FOLIO_CHROME;
if (!PW || !CHROME) { console.error('[fatal] FOLIO_PLAYWRIGHT_CORE/FOLIO_CHROME required'); process.exit(2); }
const { chromium } = require(PW);

function parseArgs(argv) {
  const out = {};
  for (let i = 0; i < argv.length; i += 1) {
    if (argv[i].startsWith('--')) {
      const name = argv[i].slice(2);
      const next = argv[i + 1];
      if (next === undefined || next.startsWith('--')) { out[name] = true; } else { out[name] = next; i += 1; }
    }
  }
  return out;
}

const args = parseArgs(process.argv.slice(2));
const result = { job: args.job, url: args.url };
const SHOTS = path.join(args['out-dir'], 'screenshots');
fs.mkdirSync(SHOTS, { recursive: true });

(async () => {
  const browser = await chromium.launch({ executablePath: CHROME, args: ['--no-sandbox', '--disable-gpu'] });
  const page = await browser.newContext({ viewport: { width: 1600, height: 1000 } }).then((c) => c.newPage());
  try {
    await page.goto(`${args.url}/?task=${args.job}&section=delivery`, { waitUntil: 'domcontentloaded', timeout: 60000 });
    await page.waitForFunction(() => document.body.innerText.trim().length > 200, null, { timeout: 180000 });
    await page.waitForTimeout(6000);
    result.reveal_buttons = await page.locator('[class*="st-key-reveal_prim_"] button').count();
    result.open_buttons = await page.locator('[class*="st-key-open_prim_"] button').count();
    const reveal = page.locator(`[class*="st-key-reveal_prim_${args.job}_translation_docx"] button`).first();
    result.translation_docx_button = await reveal.count() > 0;
    if (result.translation_docx_button) {
      await reveal.click();
      await page.waitForTimeout(4000);
    }
    const body = await page.locator('body').innerText();
    const match = /(已在文件管理器中显示：[^\n]*|已用默认应用打开：[^\n]*|文件不存在[^\n]*|无法完成[^\n]*)/.exec(body);
    result.notice = match ? match[1].trim() : null;
    result.ok = Boolean(result.translation_docx_button && result.notice && result.notice.startsWith('已在文件管理器中显示'));
    await page.screenshot({ path: path.join(SHOTS, 'delivery-file-actions.png') });
  } catch (error) {
    result.error = String(error && error.message ? error.message : error);
  } finally {
    fs.writeFileSync(args.result, JSON.stringify(result, null, 2));
    await browser.close().catch(() => {});
  }
  process.exit(result.ok ? 0 : 1);
})();
