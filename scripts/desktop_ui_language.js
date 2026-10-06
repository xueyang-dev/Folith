#!/usr/bin/env node
/**
 * D2 resource check: switch the UI language in the real app.
 *
 * The locale catalogs ship inside the bundle; this proves they are actually
 * reachable at runtime (a missing/renamed catalog would silently fall back to
 * Chinese while every static check still passed).
 *
 * Usage: node desktop_ui_language.js --url URL --out-dir DIR --result FILE
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
const result = { url: args.url, steps: [] };
const SHOTS = path.join(args['out-dir'], 'screenshots');
fs.mkdirSync(SHOTS, { recursive: true });
const settle = (ms) => new Promise((r) => setTimeout(r, ms));

async function pickLanguage(page, label) {
  // The popover keeps its previous DOM around, so the option must be matched as
  // *visible*; the trigger may also need a second click after a locale switch.
  const option = () => page.locator(`label:visible:has-text("${label}")`).first();
  const trigger = () => page.locator('[class*="st-key-utility_action_language"] button').first();
  for (let attempt = 0; attempt < 4; attempt += 1) {
    if (await option().count()) break;
    await trigger().click();
    await settle(2000);
  }
  await option().waitFor({ state: 'visible', timeout: 30000 });
  await option().click();
  await settle(4000);
}

(async () => {
  const browser = await chromium.launch({ executablePath: CHROME, args: ['--no-sandbox', '--disable-gpu'] });
  const page = await browser.newContext({ viewport: { width: 1600, height: 1000 } }).then((c) => c.newPage());
  const body = () => page.locator('body').innerText();
  try {
    await page.goto(args.url, { waitUntil: 'domcontentloaded', timeout: 60000 });
    await page.waitForFunction(() => document.body.innerText.trim().length > 200, null, { timeout: 120000 });
    await settle(4000);
    const before = await body();
    result.before = { chinese_present: before.includes('新建任务'), english_present: before.includes('New task') };
    await page.screenshot({ path: path.join(SHOTS, 'language-before.png') });

    if (args['read-only']) {
      // Report the active locale without touching it (used after a restart to
      // prove the preference persisted).
      result.ok = result.before.chinese_present || result.before.english_present;
      return;
    }

    await pickLanguage(page, 'English');
    const after = await body();
    result.after_english = {
      english_present: after.includes('New task') || after.includes('Task history'),
      chinese_present: after.includes('新建任务'),
      sample: after.slice(0, 300),
    };
    await page.screenshot({ path: path.join(SHOTS, 'language-en.png') });

    if (args['keep']) {
      // Leave the UI in the switched language: used to prove the preference is
      // persisted and restored on the next launch.
      result.restored = null;
      result.ok = result.after_english.english_present && !result.after_english.chinese_present;
      return;
    }
    await pickLanguage(page, '简体中文');
    const restored = await body();
    result.restored = { chinese_present: restored.includes('新建任务') };
    result.ok = result.before.chinese_present && result.after_english.english_present
      && !result.after_english.chinese_present && result.restored.chinese_present;
  } catch (error) {
    result.error = String(error && error.message ? error.message : error);
    result.ok = false;
  } finally {
    fs.writeFileSync(args.result, JSON.stringify(result, null, 2));
    await browser.close().catch(() => {});
  }
  process.exit(result.ok ? 0 : 1);
})();
