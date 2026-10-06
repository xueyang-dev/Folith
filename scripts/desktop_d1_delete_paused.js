#!/usr/bin/env node
/**
 * D1: removing a *paused* task.
 *
 * The product treats "paused" as an active runtime status (the worker may be
 * mid-step), so the removal modal must take the same refusal branch as a running
 * task: explain why, offer no confirm control, and leave the task on disk.
 */
const fs = require('fs');
const path = require('path');
const { chromium } = require(process.env.FOLIO_PLAYWRIGHT_CORE);

function parseArgs(argv) {
  const out = {};
  for (let i = 0; i < argv.length; i += 1) {
    if (argv[i].startsWith('--')) {
      out[argv[i].slice(2)] = argv[i + 1];
      i += 1;
    }
  }
  return out;
}

const args = parseArgs(process.argv.slice(2));
const APP_URL = (args['url'] || '').replace(/\/$/, '');
const JOB = args['job'];
const DATA_DIR = args['data-dir'];
const OUT_DIR = args['out-dir'] || process.cwd();
const RESULT = args['result'] || path.join(OUT_DIR, 'delete-paused.json');

const result = { job: JOB, steps: {}, ok: false };
const present = () => fs.existsSync(path.join(DATA_DIR, JOB, 'state.json'));
const button = (page, key) =>
  page.locator(`button[class*="${key}"], [class*="${key}"] button`).first();

async function main() {
  const browser = await chromium.launch({ executablePath: process.env.FOLIO_CHROME });
  const context = await browser.newContext({ viewport: { width: 1440, height: 960 } });
  const page = await context.newPage();
  page.setDefaultTimeout(120000);

  // 1. open the task and pause it through the real UI
  await page.goto(`${APP_URL}/?task=${JOB}&section=translation`,
                  { waitUntil: 'domcontentloaded', timeout: 60000 });
  await page.waitForSelector('[data-testid="stAppViewContainer"]', { timeout: 60000 });
  await page.waitForFunction(() => document.body.innerText.trim().length > 200, null, { timeout: 300000 });
  await page.waitForSelector(`[class*="st-key-runtime_pause_${JOB}"]`, { timeout: 300000 });

  const pauseStarted = Date.now();
  let paused = false;
  for (let attempt = 0; attempt < 20 && !paused; attempt += 1) {
    try {
      await button(page, `st-key-runtime_pause_${JOB}`).click({ timeout: 15000 });
    } catch (error) {
      // the strip may have re-rendered; retry
    }
    try {
      await page.waitForSelector(`[class*="st-key-runtime_continue_${JOB}"]`, { timeout: 20000 });
      paused = true;
    } catch (error) {
      await page.waitForTimeout(2000);
    }
  }
  result.steps.paused = { paused, ms: Date.now() - pauseStarted, present: present() };

  // Hand control back to the runner so it can record the *on-disk* status while
  // the task is still paused, then continue with the removal attempt.
  fs.mkdirSync(OUT_DIR, { recursive: true });
  fs.writeFileSync(path.join(OUT_DIR, 'paused-ready'), '1');
  const goDelete = path.join(OUT_DIR, 'go-delete');
  const deadline = Date.now() + 600000;
  while (!fs.existsSync(goDelete) && Date.now() < deadline) {
    await page.waitForTimeout(2000);
  }
  result.runner_signalled = fs.existsSync(goDelete);

  // 2. the removal attempt must be refused
  await page.goto(`${APP_URL}/?view=history`, { waitUntil: 'domcontentloaded', timeout: 60000 });
  await page.waitForSelector(`[class*="st-key-history_item_${JOB}"]`, { timeout: 300000 });
  await button(page, `st-key-history_del_${JOB}`).click();
  await page.waitForTimeout(6000);
  const body = await page.locator('body').innerText();
  result.refusal_text = /无法移除|正在运行/.test(body);
  result.steps.opened = {
    present: present(),
    confirm_input_present: (await page.locator('[class*="st-key-history_delete_confirm_name"]').count()) > 0,
    confirm_button_present: (await page.locator('button[class*="st-key-history_delete_confirm"], [class*="st-key-history_delete_confirm"] button').count()) > 0,
    close_present: (await page.locator('button[class*="st-key-history_delete_close"], [class*="st-key-history_delete_close"] button').count()) > 0,
  };
  if (result.steps.opened.close_present) {
    await button(page, 'st-key-history_delete_close').click();
    await page.waitForTimeout(4000);
  }
  result.steps.closed = { present: present() };

  result.ok = Boolean(
    result.steps.paused.paused === true
    && result.refusal_text === true
    && result.steps.opened.present === true
    && result.steps.opened.confirm_input_present === false
    && result.steps.opened.confirm_button_present === false
    && result.steps.closed.present === true
  );

  fs.mkdirSync(OUT_DIR, { recursive: true });
  fs.writeFileSync(RESULT, JSON.stringify(result, null, 2));
  await browser.close();
  console.log(JSON.stringify(result));
}

main().catch(async (error) => {
  result.error = String(error).slice(0, 300);
  result.steps.failure_present = present();
  fs.mkdirSync(OUT_DIR, { recursive: true });
  fs.writeFileSync(RESULT, JSON.stringify(result, null, 2));
  console.log(JSON.stringify(result));
  process.exit(1);
});
