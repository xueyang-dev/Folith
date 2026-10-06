#!/usr/bin/env node
/**
 * D1: is the app usable when the data directory holds many tasks?
 *
 * Loads the task history view, counts the rendered entries, opens the first task
 * and waits for its CAT workspace, then reports timings.
 *
 *   node desktop_d1_many_jobs.js --url http://127.0.0.1:PORT --out-dir DIR \
 *        --result result.json --expect 50
 */
const fs = require('fs');
const path = require('path');
const { chromium } = require(process.env.FOLIO_PLAYWRIGHT_CORE);

function parseArgs(argv) {
  const out = {};
  for (let i = 0; i < argv.length; i += 1) {
    const key = argv[i];
    if (key.startsWith('--')) {
      out[key.slice(2)] = argv[i + 1];
      i += 1;
    }
  }
  return out;
}

const args = parseArgs(process.argv.slice(2));
const APP_URL = (args['url'] || '').replace(/\/$/, '');
const OUT_DIR = args['out-dir'] || process.cwd();
const RESULT = args['result'] || path.join(OUT_DIR, 'many-jobs.json');
const EXPECT = Number(args['expect'] || 0);

const result = { url: APP_URL, expect: EXPECT, steps: {}, ok: false };

async function main() {
  const browser = await chromium.launch({ executablePath: process.env.FOLIO_CHROME });
  const context = await browser.newContext({ viewport: { width: 1440, height: 960 } });
  const page = await context.newPage();
  page.setDefaultTimeout(120000);

  const started = Date.now();
  await page.goto(`${APP_URL}/?view=history`, { waitUntil: 'domcontentloaded', timeout: 60000 });
  await page.waitForSelector('[data-testid="stAppViewContainer"]', { timeout: 60000 });
  await page.waitForFunction(() => document.body.innerText.trim().length > 200, null, { timeout: 300000 });
  result.steps.load = { ms: Date.now() - started };

  // Wait for the task list to render.
  const listStarted = Date.now();
  await page.waitForSelector('[class*="st-key-history_item_"]', { timeout: 300000 });
  await page.waitForTimeout(3000);
  const count = await page.locator('[class*="st-key-history_item_"]').count();
  result.steps.list = { ms: Date.now() - listStarted, rendered_items: count };

  // The app must still be interactive: open the first task and see the CAT grid.
  const openStarted = Date.now();
  const openButton = page.locator('[class*="st-key-history_card_"] button').first();
  if (await openButton.count()) {
    await openButton.click();
  } else {
    await page.locator('[class*="st-key-history_item_"] button').first().click();
  }
  let workspace = false;
  try {
    await page.waitForSelector('[class*="st-key-cat_row_"]', { timeout: 240000 });
    workspace = true;
  } catch (error) {
    result.steps.workspace_error = String(error).slice(0, 200);
  }
  result.steps.workspace = { ms: Date.now() - openStarted, rendered: workspace };
  result.title = await page.title();
  result.ok = count > 0 && workspace && (!EXPECT || count === EXPECT);
  result.total_ms = Date.now() - started;

  fs.mkdirSync(OUT_DIR, { recursive: true });
  fs.writeFileSync(RESULT, JSON.stringify(result, null, 2));
  await browser.close();
  console.log(JSON.stringify(result));
}

main().catch(async (error) => {
  result.error = String(error).slice(0, 300);
  fs.mkdirSync(OUT_DIR, { recursive: true });
  fs.writeFileSync(RESULT, JSON.stringify(result, null, 2));
  console.log(JSON.stringify(result));
  process.exit(1);
});
