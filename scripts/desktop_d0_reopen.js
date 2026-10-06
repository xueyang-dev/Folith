#!/usr/bin/env node
/**
 * D0 reopen verification: restart the service on the same data directory and
 * confirm the job, its committed edit and its delivery are still readable
 * through the UI (roadmap §6.1 "重开后任务、编辑和交付可读").
 *
 * Usage:
 *   node desktop_d0_reopen.js --url URL --job JOB --expect TEXT \
 *     --out-dir DIR --result FILE
 */
'use strict';

const fs = require('fs');
const path = require('path');

const PW = process.env.FOLIO_PLAYWRIGHT_CORE;
const CHROME = process.env.FOLIO_CHROME;
if (!PW || !CHROME) {
  console.error('[fatal] FOLIO_PLAYWRIGHT_CORE and FOLIO_CHROME must be set');
  process.exit(2);
}
const { chromium } = require(PW);

function parseArgs(argv) {
  const out = {};
  for (let i = 0; i < argv.length; i += 1) {
    if (argv[i].startsWith('--')) {
      const name = argv[i].slice(2);
      const next = argv[i + 1];
      if (next === undefined || next.startsWith('--')) { out[name] = true; }
      else { out[name] = next; i += 1; }
    }
  }
  return out;
}

const args = parseArgs(process.argv.slice(2));
const result = { url: args.url, job: args.job, steps: [], ok: false };
const SHOTS = path.join(args['out-dir'], 'screenshots');
fs.mkdirSync(SHOTS, { recursive: true });

(async () => {
  const browser = await chromium.launch({ executablePath: CHROME, args: ['--no-sandbox', '--disable-gpu'] });
  const context = await browser.newContext({ viewport: { width: 1600, height: 1000 } });
  const page = await context.newPage();
  try {
    await page.goto(args.url, { waitUntil: 'domcontentloaded', timeout: 60000 });
    await page.waitForFunction(() => document.body.innerText.trim().length > 200, null, { timeout: 180000 });
    await page.waitForTimeout(3000);
    // The job must appear in the sidebar history after a restart.
    const history = await page.locator('body').innerText();
    result.job_visible = history.includes(args.job) || /历史任务/.test(history);
    await page.goto(`${args.url}/?task=${args.job}&section=translation`, { waitUntil: 'domcontentloaded' });
    await page.waitForFunction(() => document.body.innerText.trim().length > 200, null, { timeout: 180000 });
    await page.waitForTimeout(4000);
    const editor = page.locator(`.st-key-cat_editor_${args.job}_0 textarea`).first();
    await editor.waitFor({ state: 'visible', timeout: 90000 });
    const value = await editor.inputValue();
    result.editor_excerpt = (value || '').slice(0, 200);
    result.editor_contains_edit = Boolean(args.expect && value && value.includes(args.expect));
    await page.screenshot({ path: path.join(SHOTS, 'reopen-translation.png') });
    await page.goto(`${args.url}/?task=${args.job}&section=delivery`, { waitUntil: 'domcontentloaded' });
    await page.waitForFunction(() => document.body.innerText.trim().length > 200, null, { timeout: 120000 });
    await page.waitForTimeout(4000);
    const delivery = await page.locator('body').innerText();
    result.delivery_visible = /交付版本 v\d+|下载 最终译文/.test(delivery);
    result.delivery_excerpt = delivery.slice(0, 300);
    await page.screenshot({ path: path.join(SHOTS, 'reopen-delivery.png') });
    result.ok = Boolean(result.editor_contains_edit && result.delivery_visible);
  } catch (error) {
    result.error = String(error && error.message ? error.message : error);
  } finally {
    fs.writeFileSync(args.result, JSON.stringify(result, null, 2));
    await browser.close().catch(() => {});
  }
  process.exit(result.ok ? 0 : 1);
})();
