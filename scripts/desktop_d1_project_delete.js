#!/usr/bin/env node
/**
 * D1: the project-deletion modal (UI) and its typed-name gate.
 *
 * The core behaviour is unit-tested; this checks the *modal* in the frozen app:
 * the confirm button is disabled until the exact project name is typed, and the
 * project really disappears afterwards (the runner then checks that a
 * recoverable backup was written to disk).
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
const PROJECT_NAME = args['name'];
const MODE = args['mode'] || 'empty';
const OUT_DIR = args['out-dir'] || process.cwd();
const RESULT = args['result'] || path.join(OUT_DIR, 'project-delete.json');

const result = { project_name: PROJECT_NAME, steps: {}, ok: false };
const button = (page, key) =>
  page.locator(`button[class*="${key}"], [class*="${key}"] button`).first();
const anyKey = (page, key) =>
  page.locator(`[class*="${key}"]`).first();

async function main() {
  const browser = await chromium.launch({ executablePath: process.env.FOLIO_CHROME });
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
  const page = await context.newPage();
  page.setDefaultTimeout(120000);

  // 1. create an empty project through the real UI
  await page.goto(`${APP_URL}/?view=projects`, { waitUntil: 'domcontentloaded', timeout: 60000 });
  await page.waitForSelector('[data-testid="stAppViewContainer"]', { timeout: 60000 });
  await page.waitForFunction(() => document.body.innerText.trim().length > 200, null, { timeout: 300000 });
  await page.locator('button:has-text("新建项目"), [class*="st-key-project_header_action"] button').first().click();
  await page.waitForTimeout(1500);
  await button(page, 'st-key-project_new_blank').click();
  await page.waitForSelector('[class*="st-key-project_form_name"]', { timeout: 120000 });
  const nameInput = page.locator('[class*="st-key-project_form_name"] input').first();
  await nameInput.fill(PROJECT_NAME);
  await nameInput.press('Enter');
  await page.waitForTimeout(1500);
  await button(page, 'st-key-project_form_create').click();
  await page.waitForTimeout(6000);

  // Creating a project navigates into its detail view; the URL carries its id.
  const urlProject = (page.url().match(/[?&]project=([A-Za-z0-9_-]+)/) || [])[1] || null;
  result.url_project_id = urlProject;

  if (MODE === 'with-task' && urlProject) {
    // Put a task into the project so the deletion must be refused.
    await page.waitForSelector(`[class*="st-key-project_move_panel_${urlProject}"]`,
                               { timeout: 180000 });
    await page.locator(`[class*="st-key-project_move_panel_${urlProject}"] summary`).first().click();
    await page.waitForTimeout(2500);
    const pick = page.locator(`[class*="st-key-project_move_pick_${urlProject}"]`).first();
    await pick.click();
    await page.waitForTimeout(2000);
    const option = page.locator('[role="option"]').first();
    if (await option.count()) {
      result.option_text = (await option.innerText().catch(() => '')) || '';
      await option.click();
      await page.waitForTimeout(1500);
      await page.keyboard.press('Escape');
      await page.waitForTimeout(1500);
    }
    await button(page, `st-key-project_move_go_${urlProject}`).click();
    await page.waitForTimeout(6000);
    const bodyAfterMove = await page.locator('body').innerText();
    result.steps.moved = { flash: /已移入|移入本项目|已归属/.test(bodyAfterMove) };
  }

  // Come back to the hub to reach the card menu.
  await page.goto(`${APP_URL}/?view=projects`, { waitUntil: 'domcontentloaded', timeout: 60000 });
  await page.waitForSelector('[class*="st-key-project_row_"]', { timeout: 180000 });
  await page.waitForTimeout(2000);
  const rows = page.locator('[class*="st-key-project_row_"]');
  const total = await rows.count();
  let projectId = null;
  for (let i = 0; i < total; i += 1) {
    const row = rows.nth(i);
    const text = (await row.innerText().catch(() => '')) || '';
    if (text.includes(PROJECT_NAME)) {
      const classes = await row.getAttribute('class');
      const idMatch = (classes || '').match(/st-key-project_row_([A-Za-z0-9_-]+)/);
      projectId = idMatch ? idMatch[1] : null;
      break;
    }
  }
  result.project_id = projectId;
  result.steps.created = { project_id: projectId, rows: total };
  if (!projectId) {
    throw new Error('could not read the new project id from the DOM');
  }

  // 2. open the card menu and its delete entry
  await button(page, `st-key-project_menu_${projectId}`).click();
  await page.waitForTimeout(1500);
  await button(page, `st-key-pm_delete_${projectId}`).click();
  await page.waitForTimeout(6000);
  if (MODE === 'with-task') {
    const body = await page.locator('body').innerText();
    result.refusal_text = /不能删除|请先把这些任务/.test(body);
    result.steps.refused = {
      confirm_input_present:
        (await page.locator('[class*="st-key-project_form_confirm_name"]').count()) > 0,
      goto_tasks_present:
        (await page.locator('button[class*="st-key-project_form_goto_tasks"], [class*="st-key-project_form_goto_tasks"] button').count()) > 0,
    };
    result.ok = Boolean(result.refusal_text === true
      && result.steps.refused.confirm_input_present === false
      && result.steps.refused.goto_tasks_present === true);
    fs.mkdirSync(OUT_DIR, { recursive: true });
    fs.writeFileSync(RESULT, JSON.stringify(result, null, 2));
    await browser.close();
    console.log(JSON.stringify(result));
    return;
  }
  await page.waitForSelector('[class*="st-key-project_form_confirm_name"]', { timeout: 120000 });
  await page.waitForTimeout(2000);
  const confirm = button(page, 'st-key-project_form_confirm');
  result.steps.opened = { confirm_disabled: await confirm.isDisabled() };

  // 3. a wrong name must not arm it
  const confirmInput = page.locator('[class*="st-key-project_form_confirm_name"] input').first();
  await confirmInput.fill('错误名称');
  await confirmInput.press('Enter');
  await page.waitForTimeout(3000);
  result.steps.wrong_name = { confirm_disabled: await confirm.isDisabled() };

  // 4. the exact name arms it, and confirming removes the project
  await confirmInput.fill(PROJECT_NAME);
  await confirmInput.press('Enter');
  await page.waitForTimeout(4000);
  result.steps.correct_name = {
    confirm_disabled: await confirm.isDisabled(),
    typed_value: await confirmInput.inputValue(),
  };
  if (!(await confirm.isDisabled())) {
    await confirm.click();
    for (let i = 0; i < 40; i += 1) {
      const stillThere = await page.locator(`[class*="st-key-project_row_${projectId}"]`).count();
      if (stillThere === 0) break;
      await page.waitForTimeout(1000);
    }
  }
  result.steps.after_confirm = {
    row_present: (await page.locator(`[class*="st-key-project_row_${projectId}"]`).count()) > 0,
  };

  result.ok = Boolean(
    result.steps.opened.confirm_disabled === true
    && result.steps.wrong_name.confirm_disabled === true
    && result.steps.correct_name.confirm_disabled === false
    && result.steps.after_confirm.row_present === false
  );

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
