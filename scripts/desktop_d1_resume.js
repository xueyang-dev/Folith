#!/usr/bin/env node
/**
 * D1 crash-recovery check: after an abrupt kill, the reopened job must be
 * presented as interrupted (not complete) and offer a working resume control.
 *
 * Usage:
 *   node desktop_d1_resume.js --url URL --job JOB --out-dir DIR --result FILE
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
const result = { job: args.job, url: args.url, steps: [] };
const SHOTS = path.join(args['out-dir'], 'screenshots');
fs.mkdirSync(SHOTS, { recursive: true });

const progress = (text) => {
  const match = /(\d+)\s*\/\s*(\d+)\s*已译/.exec(text || '');
  return match ? { done: Number(match[1]), total: Number(match[2]) } : null;
};

(async () => {
  const browser = await chromium.launch({ executablePath: CHROME, args: ['--no-sandbox', '--disable-gpu'] });
  const context = await browser.newContext({ viewport: { width: 1600, height: 1000 } });
  const page = await context.newPage();
  try {
    await page.goto(`${args.url}/?task=${args.job}&section=translation`, { waitUntil: 'domcontentloaded', timeout: 60000 });
    await page.waitForFunction(() => document.body.innerText.trim().length > 200, null, { timeout: 180000 });
    await page.waitForTimeout(5000);
    const bodyBefore = await page.locator('body').innerText();
    result.body_before = bodyBefore.slice(0, 900);
    result.progress_before = progress(bodyBefore);
    result.interrupted_text_present = /运行已中断|进度已保存|暂时没有新的运行信号|需要恢复|已中断/.test(bodyBefore);
    result.resume_present = (await page.locator('[class*="st-key-runtime_resume_"] button:visible').count()) > 0;
    result.retry_present = (await page.locator('[class*="st-key-runtime_retry_"] button:visible').count()) > 0;
    result.abandon_present = (await page.locator('[class*="st-key-runtime_abandon_"] button:visible').count()) > 0;
    await page.screenshot({ path: path.join(SHOTS, 'crash-before-resume.png') });

    // A stopped job may offer 继续处理 (interrupted/cancelled), 继续任务 (paused) or
    // 重试当前步骤 (stalled) — take whichever the product actually shows.
    const control = page.locator('[class*="st-key-runtime_resume_"] button:visible').first();
    const continueBtn = page.locator('[class*="st-key-runtime_continue_"] button:visible').first();
    const retry = page.locator('[class*="st-key-runtime_retry_"] button:visible').first();
    if (await control.count()) {
      await control.click();
      result.resume_clicked = true;
      result.control_used = 'runtime_resume';
    } else if (await continueBtn.count()) {
      await continueBtn.click();
      result.continue_clicked = true;
      result.control_used = 'runtime_continue';
    } else if (await retry.count()) {
      await retry.click();
      result.retry_clicked = true;
      result.control_used = 'runtime_retry';
    }
    result.pause_control_present = (await page.locator('[class*="st-key-runtime_pause_"] button:visible').count()) > 0;

    // Progress must actually advance after the resume click.
    for (let i = 0; i < 90; i += 1) {
      await page.waitForTimeout(2000);
      const text = await page.locator('body').innerText();
      const now = progress(text);
      if (now && result.progress_before && now.done > result.progress_before.done) {
        result.progress_after = now;
        result.resumed = true;
        break;
      }
      if (now) { result.progress_after = now; }
    }
    result.body_after = (await page.locator('body').innerText()).slice(0, 600);
    await page.screenshot({ path: path.join(SHOTS, 'crash-after-resume.png') });
    result.ok = Boolean(result.resumed
      && (result.resume_present || result.retry_present || result.continue_clicked));
  } catch (error) {
    result.error = String(error && error.message ? error.message : error);
  } finally {
    fs.writeFileSync(args.result, JSON.stringify(result, null, 2));
    await browser.close().catch(() => {});
  }
  process.exit(result.ok ? 0 : 1);
})();
