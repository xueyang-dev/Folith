#!/usr/bin/env node
/**
 * D1: the task-removal path must not delete anything until the typed name matches.
 *
 * Drives the real confirmation modal in the frozen app and checks the filesystem
 * at every step, so "the guard held" is evidence rather than an assumption:
 *   1. opening the modal leaves the task on disk;
 *   2. the confirm button is disabled before typing;
 *   3. a wrong name keeps it disabled and the task on disk;
 *   4. closing the modal deletes nothing;
 *   5. the exact name enables the button, and confirming really removes the task.
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
const RESULT = args['result'] || path.join(OUT_DIR, 'history-delete.json');

const result = { job: JOB, steps: {}, ok: false };
const jobFile = path.join(DATA_DIR, JOB, 'state.json');
const present = () => fs.existsSync(jobFile);

// Streamlit puts the key class either on a wrapper or on the button itself, so
// match both shapes.
function button(page, key) {
  return page.locator(`button[class*="${key}"], [class*="${key}"] button`).first();
}

async function confirmDisabled(page) {
  return button(page, 'st-key-history_delete_confirm').isDisabled();
}

async function main() {
  const browser = await chromium.launch({ executablePath: process.env.FOLIO_CHROME });
  const context = await browser.newContext({ viewport: { width: 1440, height: 960 } });
  const page = await context.newPage();
  page.setDefaultTimeout(120000);

  await page.goto(`${APP_URL}/?view=history`, { waitUntil: 'domcontentloaded', timeout: 60000 });
  await page.waitForSelector('[data-testid="stAppViewContainer"]', { timeout: 60000 });
  await page.waitForFunction(() => document.body.innerText.trim().length > 200, null, { timeout: 300000 });
  await page.waitForSelector(`[class*="st-key-history_item_${JOB}"]`, { timeout: 300000 });
  result.steps.listed = { present: present() };

  // 1. open the modal
  await button(page, `st-key-history_del_${JOB}`).click();
  await page.waitForSelector('[class*="st-key-history_delete_confirm_name"]', { timeout: 120000 });
  await page.waitForTimeout(1500);
  result.steps.opened = { present: present(), confirm_disabled: await confirmDisabled(page) };

  // 2. the expected name is shown in the dialog itself
  const body = await page.locator('body').innerText();
  const match = body.match(/输入任务名称「(.+?)」以确认/);
  result.expected_name = match ? match[1] : null;

  // 3. a wrong name must not arm the button
  const input = page.locator('[class*="st-key-history_delete_confirm_name"] input').first();
  await input.fill('错误的名称-wrong-name');
  // Streamlit commits a text input on Enter/blur, not on programmatic fill.
  await input.press('Enter');
  await page.waitForTimeout(3000);
  result.steps.wrong_name = { present: present(), confirm_disabled: await confirmDisabled(page) };

  // 4. closing must delete nothing
  // 关闭 only exists for a *running* task; the dismiss control for a normal
  // task is 取消 (history_delete_cancel).
  await button(page, 'st-key-history_delete_cancel').click();
  await page.waitForTimeout(3000);
  result.steps.closed = { present: present() };

  // 5. the exact name arms the button and confirming removes the task
  await button(page, `st-key-history_del_${JOB}`).click();
  await page.waitForSelector('[class*="st-key-history_delete_confirm_name"]', { timeout: 120000 });
  await page.waitForTimeout(1500);
  if (result.expected_name) {
    const exact = page.locator('[class*="st-key-history_delete_confirm_name"] input').first();
    await exact.fill(result.expected_name);
    await exact.press('Enter');
    await page.waitForTimeout(4000);
    result.steps.correct_name = {
      present: present(),
      confirm_disabled: await confirmDisabled(page),
      typed_value: await exact.inputValue(),
    };
    const confirm = button(page, 'st-key-history_delete_confirm');
    if (!(await confirm.isDisabled())) {
      await confirm.click();
      for (let i = 0; i < 40 && present(); i += 1) {
        await page.waitForTimeout(1000);
      }
    }
  }
  result.steps.after_confirm = { present: present() };

  result.ok = Boolean(
    result.steps.opened.present === true
    && result.steps.opened.confirm_disabled === true
    && result.expected_name
    && result.steps.wrong_name.present === true
    && result.steps.wrong_name.confirm_disabled === true
    && result.steps.closed.present === true
    && result.steps.correct_name
    && result.steps.correct_name.confirm_disabled === false
    && result.steps.after_confirm.present === false
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
