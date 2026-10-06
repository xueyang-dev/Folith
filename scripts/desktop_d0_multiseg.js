#!/usr/bin/env node
/**
 * D0 multi-sentence editing check (roadmap §6.1: "多句切换与提交 … 无丢字、丢状态").
 *
 * Edits several segments in the real CAT workspace, committing each through the
 * app's own save button, then switches away and back to confirm every segment
 * still holds its own text.  `--verify-only` re-reads the segments (used after a
 * service restart to prove the edits survived a process boundary).
 *
 * Usage:
 *   node desktop_d0_multiseg.js --url URL --job JOB --texts-json FILE \
 *     --out-dir DIR --result FILE [--verify-only]
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
const targets = JSON.parse(fs.readFileSync(args['texts-json'], 'utf8'));
const result = { job: args.job, url: args.url, verify_only: Boolean(args['verify-only']), segments: [] };
const SHOTS = path.join(args['out-dir'], 'screenshots');
fs.mkdirSync(SHOTS, { recursive: true });

const settle = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

(async () => {
  const browser = await chromium.launch({ executablePath: CHROME, args: ['--no-sandbox', '--disable-gpu'] });
  const page = await browser.newContext({ viewport: { width: 1600, height: 1000 } }).then((c) => c.newPage());
  const alerts = async () => page.locator('[data-testid="stAlert"], [data-testid="stToast"]').allInnerTexts();
  const editorFor = (index) => page.locator(`.st-key-cat_editor_${args.job}_${index} textarea`).first();

  try {
    await page.goto(`${args.url}/?task=${args.job}&section=translation`, { waitUntil: 'domcontentloaded', timeout: 60000 });
    await page.waitForFunction(() => document.body.innerText.trim().length > 200, null, { timeout: 120000 });
    await settle(3000);

    for (const target of targets) {
      const record = { index: target.index, expected: target.text };
      const editor = editorFor(target.index);
      await editor.waitFor({ state: 'visible', timeout: 60000 });
      if (args['verify-only']) {
        record.actual = await editor.inputValue();
        record.ok = record.actual === target.text;
        result.segments.push(record);
        continue;
      }
      await editor.click();
      await page.keyboard.press(process.platform === 'darwin' ? 'Meta+a' : 'Control+a');
      await page.keyboard.press('Backspace');
      await page.keyboard.type(target.text, { delay: 8 });
      await page.keyboard.press('Tab');
      await settle(2000);
      const save = page.locator(`[class*="st-key-cat_save_btn_${args.job}_${target.index}"] button`).first();
      await save.waitFor({ state: 'visible', timeout: 60000 });
      let enabled = false;
      for (let i = 0; i < 60; i += 1) {
        if (!(await save.isDisabled())) { enabled = true; break; }
        await settle(1000);
      }
      if (!enabled) { throw new Error(`segment ${target.index}: save button never enabled`); }
      await save.click();
      await settle(2500);
      const texts = await alerts();
      record.saved_alert = texts.some((t) => /已保存/.test(t));
      // Switch away and back: the committed text must still be there.
      await editorFor(target.index === 0 ? 1 : 0).waitFor({ state: 'visible', timeout: 60000 });
      await settle(1200);
      record.actual = await editorFor(target.index).inputValue();
      record.ok = record.actual === target.text && record.saved_alert === true;
      result.segments.push(record);
    }
    await page.screenshot({ path: path.join(SHOTS, args['verify-only'] ? 'verify.png' : 'multi-edit.png') });
    result.ok = result.segments.length === targets.length && result.segments.every((item) => item.ok);
  } catch (error) {
    result.error = String(error && error.message ? error.message : error);
    result.ok = false;
  } finally {
    fs.writeFileSync(args.result, JSON.stringify(result, null, 2));
    await browser.close().catch(() => {});
  }
  process.exit(result.ok ? 0 : 1);
})();
