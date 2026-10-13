#!/usr/bin/env node
/**
 * D0 vertical slice, driven through the real Streamlit UI in a real browser.
 *
 * Roadmap §6.1: launch -> import a real document -> small task -> multi-segment
 * workspace -> human edit -> commit edit -> complete review -> official delivery
 * -> save the delivered file -> verify the edit is inside it.  Nothing is
 * injected into session state or state.json; every step goes through the UI
 * (plus the app's own `?task=&section=` routing, which is product behaviour).
 *
 * Usage:
 *   FOLIO_PLAYWRIGHT_CORE=<path> FOLIO_CHROME=<chrome> node desktop_d0_slice.js \
 *     --url http://127.0.0.1:PORT --source /abs/source.docx \
 *     --out-dir /abs/out --stub-url http://127.0.0.1:PORT2/v1 \
 *     --edit-text "D0-EDIT-<token>" --result /abs/out/slice.json [--review]
 */
'use strict';

const fs = require('fs');
const path = require('path');
const crypto = require('crypto');

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
    const key = argv[i];
    if (key.startsWith('--')) {
      const name = key.slice(2);
      const next = argv[i + 1];
      if (next === undefined || next.startsWith('--')) { out[name] = true; }
      else { out[name] = next; i += 1; }
    }
  }
  return out;
}

const args = parseArgs(process.argv.slice(2));
const APP_URL = args.url;
const SOURCE = args.source;
const OUT_DIR = args['out-dir'];
const STUB_URL = args['stub-url'];
const EDIT_TEXT = args['edit-text'] || `D0-EDIT-${Date.now()}`;
const RESULT = args.result || path.join(OUT_DIR, 'slice.json');
const WANT_REVIEW = Boolean(args.review);
const TARGET_LANG = args['target-lang'] || '简体中文';
const OCR_LANGUAGE = args['ocr-language'] || null;
const OCR_SOURCE = args['ocr-source'] || null;
const SHOTS = path.join(OUT_DIR, 'screenshots');

fs.mkdirSync(SHOTS, { recursive: true });
fs.mkdirSync(path.join(OUT_DIR, 'downloads'), { recursive: true });

const result = {
  started_at: new Date().toISOString(),
  url: APP_URL,
  source: SOURCE,
  edit_text: EDIT_TEXT,
  target_lang: TARGET_LANG,
  review_requested: WANT_REVIEW,
  steps: [],
  alerts: [],
  page_errors: [],
  job_id: null,
  download: null,
  ok: false,
};

function record(step, ok, detail) {
  result.steps.push({ step, ok, detail: detail === undefined ? null : detail });
  console.log(`${ok ? '[ok]  ' : '[FAIL]'} ${step}${detail ? ' :: ' + JSON.stringify(detail).slice(0, 400) : ''}`);
}

function sha256(file) {
  return crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex');
}

class SliceFailure extends Error {}

async function main() {
  const browser = await chromium.launch({
    executablePath: CHROME,
    args: ['--no-sandbox', '--disable-gpu'],
  });
  const context = await browser.newContext({
    viewport: { width: 1600, height: 1000 },
    acceptDownloads: true,
  });
  const page = await context.newPage();
  page.on('pageerror', (err) => result.page_errors.push(String(err.message)));
  page.on('dialog', (dialog) => dialog.dismiss().catch(() => {}));

  const shot = async (name) => {
    try { await page.screenshot({ path: path.join(SHOTS, `${name}.png`) }); } catch (_) {}
  };
  const alerts = async () => {
    const texts = await page.locator('[data-testid="stAlert"], .tp-utility-feedback[role="status"]').allInnerTexts().catch(() => []);
    texts.forEach((text) => { if (text && !result.alerts.includes(text.trim())) result.alerts.push(text.trim()); });
    return texts;
  };
  const settle = async (ms = 1200) => {
    await page.waitForTimeout(ms);
    for (let i = 0; i < 120; i += 1) {
      const running = await page.locator('[data-testid="stStatusWidget"]').count().catch(() => 0);
      if (!running) break;
      await page.waitForTimeout(500);
    }
    await page.waitForTimeout(600);
  };
  const keyButton = (key) => page.locator(`[class*="st-key-${key}"] button:visible`).first();
  const keyVisible = async (key) => (await page.locator(`[class*="st-key-${key}"]:visible`).count()) > 0;
  const clickKey = async (key, timeout = 30000) => {
    const target = keyButton(key);
    await target.waitFor({ state: 'visible', timeout });
    await target.click();
    await settle();
  };
  const step = async (name, fn) => {
    try {
      const detail = await fn();
      record(name, true, detail);
      await shot(name.replace(/[^a-zA-Z0-9_-]/g, '_'));
      return detail;
    } catch (error) {
      record(name, false, { error: String(error && error.message ? error.message : error) });
      await alerts();
      await shot(`FAIL-${name.replace(/[^a-zA-Z0-9_-]/g, '_')}`);
      throw new SliceFailure(`${name}: ${error && error.message ? error.message : error}`);
    }
  };

  try {
    await step('open-app', async () => {
      await page.goto(APP_URL, { waitUntil: 'domcontentloaded', timeout: 60000 });
      await page.waitForSelector('[data-testid="stAppViewContainer"]', { timeout: 60000 });
      await page.waitForFunction(() => document.body.innerText.trim().length > 200, null, { timeout: 180000 });
      await settle(2500);
      return { title: await page.title() };
    });

    await step('configure-provider', async () => {
      await page.goto(`${APP_URL}/?view=appearance`, { waitUntil: 'domcontentloaded' }).catch(() => {});
      await settle(1500);
      if (await keyVisible('utility_cell_settings')) {
        await clickKey('utility_cell_settings');
      } else {
        await page.getByRole('button', { name: '设置' }).first().click();
        await settle();
      }
      await page.locator('[class*="st-key-provider_choice"]').first().waitFor({ timeout: 30000 });
      // Select the OpenAI-compatible relay provider.
      const combo = page.locator('[class*="st-key-provider_choice"] [role="combobox"], [class*="st-key-provider_choice"] input').first();
      await combo.click();
      await page.waitForTimeout(600);
      const option = page.locator('[role="option"]', { hasText: '自定义中转站' }).first();
      if (await option.count()) { await option.click(); }
      else { await page.keyboard.type('自定义中转站'); await page.keyboard.press('Enter'); }
      await settle(2000);
      await page.locator('[class*="st-key-custom_base_url"] input').first().waitFor({ timeout: 30000 });
      await page.locator('[class*="st-key-model_choice_"] input').first().fill('d0-stub-model');
      await page.locator('[class*="st-key-api_key_"] input').first().fill('d0-local');
      await page.locator('[class*="st-key-custom_base_url"] input').first().fill(STUB_URL);
      await page.keyboard.press('Tab');
      await settle(1500);
      await page.getByRole('button', { name: '保存配置' }).first().click();
      await settle(2500);
      await alerts();
      const saved = (await page.getByText('已保存').count()) > 0
        || (await page.locator('[data-testid="stAlert"]').allInnerTexts()).some((t) => /保存/.test(t));
      return { saved, stub_url: STUB_URL };
    });

    if (await keyVisible('utility_cell_ai')) {
      await step('test-connection', async () => {
        await page.getByRole('button', { name: '测试连接' }).first().click();
        await page.waitForTimeout(1500);
        for (let i = 0; i < 60; i += 1) {
          const texts = await page.locator('[data-testid="stAlert"], .tp-utility-feedback[role="status"]').allInnerTexts();
          const hit = texts.find((t) => /响应「|连接成功|连接正常/.test(t));
          if (hit) { await alerts(); return { alert: hit.trim() }; }
          const bad = texts.find((t) => /失败|错误|无法/.test(t));
          if (bad) { await alerts(); throw new Error(`connection test failed: ${bad.trim()}`); }
          await page.waitForTimeout(1000);
        }
        await alerts();
        throw new Error('connection test produced no result alert');
      });
    }

    await step('upload-source', async () => {
      await page.goto(APP_URL, { waitUntil: 'domcontentloaded' });
      await page.waitForFunction(() => document.body.innerText.trim().length > 200, null, { timeout: 120000 });
      await settle(2000);
      if (await keyVisible('dismiss_onboarding')) { await clickKey('dismiss_onboarding'); }
      if (await keyVisible('new_task_action')) { await clickKey('new_task_action'); }
      const input = page.locator('[data-testid="stFileUploaderDropzoneInput"]').first();
      await input.waitFor({ state: 'attached', timeout: 60000 });
      await input.setInputFiles(SOURCE);
      await page.waitForTimeout(1500);
      for (let i = 0; i < 120; i += 1) {
        const body = await page.locator('body').innerText();
        if (/更换文件|删除/.test(body)) break;
        await page.waitForTimeout(1000);
      }
      await settle(1500);
      // Target language.
      const lang = page.locator('[class*="st-key-target_lang"] [role="combobox"], [class*="st-key-target_lang"] input').first();
      if (await lang.count()) {
        const current = await lang.inputValue().catch(() => '');
        if (current !== TARGET_LANG) {
          await lang.click();
          await page.waitForTimeout(500);
          const langOption = page.locator('[role="option"]', { hasText: TARGET_LANG }).first();
          if (await langOption.count()) { await langOption.click(); }
          else { await page.keyboard.type(TARGET_LANG); await page.keyboard.press('Enter'); }
          await settle(1500);
        }
      }
      if (OCR_SOURCE) {
        const source = page.locator('[class*="st-key-ocr_backend"] [role="combobox"]').first();
        await source.waitFor({ state: 'visible', timeout: 60000 });
        await source.click();
        await page.getByRole('option', { name: OCR_SOURCE, exact: true }).click();
        await settle(1500);
      }
      if (OCR_LANGUAGE && await keyVisible('ocr_language')) {
        const ocr = page.locator('[class*="st-key-ocr_language"] [role="combobox"], [class*="st-key-ocr_language"] input').first();
        await ocr.click();
        await page.waitForTimeout(500);
        const ocrOption = page.locator('[role="option"]', { hasText: OCR_LANGUAGE }).first();
        if (await ocrOption.count()) { await ocrOption.click(); }
        else { await page.keyboard.type(OCR_LANGUAGE); await page.keyboard.press('Enter'); }
        await settle(1500);
      }
      const next = keyButton('next_to_2');
      await next.waitFor({ state: 'visible', timeout: 60000 });
      if (await next.isDisabled()) { throw new Error('下一步 disabled after upload'); }
      await next.click();
      await settle(3000);
      return { target_lang: TARGET_LANG, ocr_language: OCR_LANGUAGE, ocr_source: OCR_SOURCE };
    });

    await step('strategy', async () => {
      // The preset cards render as visible buttons; the default selection is 标准.
      await page.getByRole('button', { name: /标准/ }).first().waitFor({ timeout: 60000 });
      const presetText = await page.locator('body').innerText();
      if (!WANT_REVIEW) { return { preset: '标准', review_enabled: false }; }
      // 独立审校 is an advanced toggle; the 标准 preset leaves it off.
      await page.getByRole('button', { name: /高级设置/ }).first().click();
      await page.waitForTimeout(2500);
      const reviewToggle = page.locator('[class*="st-key-strategy_review"]:visible').first();
      await reviewToggle.waitFor({ state: 'visible', timeout: 30000 });
      const input = reviewToggle.locator('input').first();
      const alreadyOn = (await input.count()) ? await input.isChecked().catch(() => false) : false;
      if (!alreadyOn) {
        await reviewToggle.locator('label, [role="switch"], input').first().click();
        await page.waitForTimeout(3000);
      }
      const nowOn = (await input.count()) ? await input.isChecked().catch(() => true) : true;
      return { preset: '标准', review_enabled: nowOn, preset_text_has_review: /独立审校/.test(presetText) };
    });

    await step('delivery-options', async () => {
      await clickKey('next_to_3');
      await page.locator('[class*="st-key-delivery_preset"]').first().waitFor({ timeout: 60000 });
      await clickKey('next_to_4');
      await page.locator('[class*="st-key-run_task"]').first().waitFor({ timeout: 60000 });
      return true;
    });

    const jobId = await step('run-task', async () => {
      const run = keyButton('run_task');
      if (await run.isDisabled()) {
        await alerts();
        throw new Error('开始任务 disabled (AI engine not ready)');
      }
      await run.click();
      for (let i = 0; i < 180; i += 1) {
        const current = new URL(page.url());
        const job = current.searchParams.get('task');
        if (job) { return job; }
        await page.waitForTimeout(1000);
      }
      throw new Error('job id never appeared in the URL');
    });
    result.job_id = jobId;

    if (args['fragment-probe']) {
      // Fragment-isolated polling: mark a node outside the runtime fragment and
      // require it to survive while the runtime HUD text keeps changing.
      const observation = { samples: [], marker_survived: null, hud_changed: false };
      await page.evaluate(() => {
        const anchor = document.querySelector('[data-testid="stAppViewContainer"] > section')
          || document.querySelector('[data-testid="stAppViewContainer"]');
        if (anchor) { anchor.__d0Marker = 'kept'; }
        window.__d0Hud = () => {
          const hud = document.querySelector('[class*="st-key-workspace_runtime_actions_"]')
            || document.querySelector('[data-testid="stMarkdownContainer"]');
          return hud ? hud.innerText : '';
        };
      });
      const first = await page.evaluate(() => (window.__d0Hud ? window.__d0Hud() : ''));
      const firstSection = await page.evaluate(() => document.body.innerText.slice(0, 4000));
      for (let i = 0; i < 12; i += 1) {
        await page.waitForTimeout(1000);
        const sample = await page.evaluate(() => {
          const hudNode = document.querySelector('[class*="st-key-workspace_runtime_actions_"]');
          const anchor = document.querySelector('[data-testid="stAppViewContainer"] > section')
            || document.querySelector('[data-testid="stAppViewContainer"]');
          return {
            hud: hudNode ? hudNode.innerText : '',
            section: document.body.innerText.slice(0, 4000),
            marker: Boolean(anchor && anchor.__d0Marker),
          };
        });
        observation.samples.push({
          i,
          hud: sample.hud.slice(0, 160),
          section_len: sample.section.length,
          section: sample.section.slice(0, 220),
          marker: sample.marker,
        });
        if (sample.hud && sample.hud !== first) { observation.hud_changed = true; }
        if (sample.section && sample.section !== firstSection) { observation.section_changed = true; }
      }
      observation.marker_survived = observation.samples.every((item) => item.marker);
      observation.section_changed = Boolean(observation.section_changed);
      observation.first_hud_excerpt = first.slice(0, 200);
      result.fragment = observation;
      result.ok = observation.marker_survived && observation.hud_changed;
      result.finished_at = new Date().toISOString();
      fs.writeFileSync(RESULT, JSON.stringify(result, null, 2));
      await browser.close().catch(() => {});
      return result.ok ? 0 : 1;
    }

    if (args['stop-after-start']) {
      // D0 exit matrix: hand the running job back to the caller so it can stop
      // the service mid-task and check recovery semantics.
      result.ok = true;
      result.stopped_after_start = true;
      result.finished_at = new Date().toISOString();
      fs.writeFileSync(RESULT, JSON.stringify(result, null, 2));
      await browser.close().catch(() => {});
      return 0;
    }

    await step('wait-translation', async () => {
      await page.locator(`[class*="st-key-cat_grid_${jobId}"]`).first().waitFor({ timeout: 120000 });
      for (let i = 0; i < 600; i += 1) {
        const body = await page.locator('body').innerText();
        if (/已完成|翻译完成|交付|审校/.test(body) && !/正在翻译|进行中/.test(body)) break;
        await page.waitForTimeout(2000);
      }
      await settle(2000);
      return { body_excerpt: (await page.locator('body').innerText()).slice(0, 300) };
    });

    await step('edit-and-commit', async () => {
      await page.goto(`${APP_URL}/?task=${jobId}&section=translation`, { waitUntil: 'domcontentloaded' });
      await page.waitForFunction(() => document.body.innerText.trim().length > 200, null, { timeout: 120000 });
      await settle(2500);
      const editor = page.locator(`.st-key-cat_editor_${jobId}_0 textarea`).first();
      await editor.waitFor({ state: 'visible', timeout: 60000 });
      await editor.click();
      await page.keyboard.press(process.platform === 'darwin' ? 'Meta+a' : 'Control+a');
      await page.keyboard.press('Backspace');
      await page.keyboard.type(EDIT_TEXT, { delay: 12 });
      await page.keyboard.press('Tab');
      await page.waitForTimeout(2500);
      const save = page.locator(`[class*="st-key-cat_save_btn_${jobId}_0"] button`).first();
      await save.waitFor({ state: 'visible', timeout: 60000 });
      let enabled = false;
      for (let i = 0; i < 60; i += 1) {
        if (!(await save.isDisabled())) { enabled = true; break; }
        await page.waitForTimeout(1000);
      }
      if (!enabled) { throw new Error('保存当前段 never enabled after typing (draft not persisted)'); }
      await save.click();
      await page.waitForTimeout(2000);
      await settle(1500);
      const texts = await alerts();
      const saved = texts.some((t) => /已保存/.test(t));
      if (!saved) { throw new Error(`no 已保存 confirmation; alerts=${JSON.stringify(texts)}`); }
      return { edit_text: EDIT_TEXT, alerts: texts };
    });

    if (WANT_REVIEW) {
      await step('review', async () => {
        await page.goto(`${APP_URL}/?task=${jobId}&section=review`, { waitUntil: 'domcontentloaded' });
        await page.waitForFunction(() => document.body.innerText.trim().length > 200, null, { timeout: 120000 });
        await settle(3000);
        // Re-review every segment whose edit invalidated its review result.
        let reruns = 0;
        for (let round = 0; round < 30; round += 1) {
          const runButtons = page.locator('[class*="st-key-review_task_run_"] button:visible');
          const count = await runButtons.count();
          let clicked = false;
          for (let i = 0; i < count; i += 1) {
            const button = runButtons.nth(i);
            const visible = await button.isVisible().catch(() => false);
            const disabled = await button.isDisabled().catch(() => true);
            if (visible && !disabled) {
              await button.click();
              clicked = true;
              reruns += 1;
              break;
            }
          }
          if (!clicked) break;
          await settle(3500);
          for (let i = 0; i < 90; i += 1) {
            const body = await page.locator('body').innerText();
            if (!/正在审校|审校中/.test(body)) break;
            await page.waitForTimeout(2000);
          }
          await settle(1500);
        }
        // Decide any findings that are still open.
        let decided = 0;
        for (let round = 0; round < 12; round += 1) {
          const resolve = page.locator('[class*="st-key-review_resolve_"] button:visible').first();
          if (await resolve.count()) {
            await resolve.click();
            await settle(3000);
            decided += 1;
            continue;
          }
          const preserve = page.locator('[class*="st-key-review_preserve_"] button').first();
          if (await preserve.count()) {
            await preserve.click();
            await settle(3000);
            decided += 1;
            continue;
          }
          break;
        }
        await alerts();
        const body = await page.locator('body').innerText();
        const pending = /需复审\s*(\d+)/.exec(body);
        return {
          reruns,
          decided,
          pending_rereview: pending ? Number(pending[1]) : null,
          body_excerpt: body.slice(0, 500),
        };
      });
    }

    await step('freeze-delivery', async () => {
      await page.goto(`${APP_URL}/?task=${jobId}&section=delivery`, { waitUntil: 'domcontentloaded' });
      await page.waitForFunction(() => document.body.innerText.trim().length > 200, null, { timeout: 120000 });
      await settle(3000);
      if (await keyVisible('delivery_save_drafts')) {
        await clickKey('delivery_save_drafts');
        await settle(2500);
      }
      const freeze = page.locator(`[class*="st-key-workspace_delivery_final_${jobId}"] button`).first();
      await freeze.waitFor({ state: 'visible', timeout: 60000 });
      if (await freeze.isDisabled()) {
        await alerts();
        const body = await page.locator('body').innerText();
        throw new Error(`freeze disabled; gate text: ${body.slice(0, 800)}`);
      }
      await freeze.click();
      await settle(4000);
      for (let i = 0; i < 180; i += 1) {
        const body = await page.locator('body').innerText();
        if (/交付版本 v\d+ 已创建|已创建交付版本/.test(body)) break;
        await page.waitForTimeout(2000);
      }
      await settle(2000);
      await alerts();
      return { body_excerpt: (await page.locator('body').innerText()).slice(0, 400) };
    });

    await step('download-delivery', async () => {
      const selector = `[class*="st-key-dl_prim_${jobId}_translation_docx"] button`;
      const button = page.locator(selector).first();
      await button.waitFor({ state: 'visible', timeout: 60000 });
      const [download] = await Promise.all([
        page.waitForEvent('download', { timeout: 120000 }),
        button.click(),
      ]);
      const target = path.join(OUT_DIR, 'downloads', download.suggestedFilename() || 'translation.docx');
      await download.saveAs(target);
      const stat = fs.statSync(target);
      result.download = { path: target, bytes: stat.size, sha256: sha256(target), suggested: download.suggestedFilename() };
      return result.download;
    });

    result.ok = result.steps.every((item) => item.ok);
  } catch (error) {
    result.error = String(error && error.message ? error.message : error);
    result.ok = false;
  } finally {
    result.finished_at = new Date().toISOString();
    result.alerts = result.alerts.slice(0, 40);
    fs.writeFileSync(RESULT, JSON.stringify(result, null, 2));
    await browser.close().catch(() => {});
  }
  return result.ok ? 0 : 1;
}

main().then((code) => process.exit(code)).catch((error) => {
  console.error('[fatal]', error);
  process.exit(3);
});
