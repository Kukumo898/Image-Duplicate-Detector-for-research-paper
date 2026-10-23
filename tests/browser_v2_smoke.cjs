/* Isolated UI integration checks. Uses only synthetic demo images, never holdout. */
const { chromium } = require('playwright');
const fs = require('node:fs');
const path = require('node:path');
const { spawnSync } = require('node:child_process');
const assert = require('node:assert/strict');

const root = process.env.LABIMAGE_URL || 'http://127.0.0.1:8767';
const output = path.resolve('output/qa_v2_ui');
fs.mkdirSync(output, { recursive: true });
const montage = path.join(output, 'ui_synthetic_montage.png');
const generated = spawnSync(path.resolve('.venv/Scripts/python.exe'), ['-X', 'utf8', '-c', `from PIL import Image,ImageDraw
import sys
a=Image.open('demo/01_microscopy_original.png').convert('RGB').resize((300,220))
canvas=Image.new('RGB',(680,270),'white')
canvas.paste(a,(20,30));canvas.paste(a,(360,30))
d=ImageDraw.Draw(canvas);d.text((20,10),'A',fill='black');d.text((360,10),'B',fill='black')
canvas.save(sys.argv[1])`, montage], { encoding: 'utf8' });
if (generated.status !== 0) throw new Error(generated.stderr);

(async () => {
  const browser = await chromium.launch({ channel: 'chrome', headless: true });
  const page = await browser.newPage({ viewport: { width: 1440, height: 1080 } });
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.goto(root);
  await page.getByText('本地引擎已就绪', { exact: true }).waitFor();
  assert(await page.locator('.validation-banner').isVisible());
  assert(await page.locator('#auto-panels').isChecked());
  await page.locator('[data-mode="pair"]').click();
  assert(!(await page.locator('#auto-panels').isChecked()));
  await page.locator('[data-mode="documents"]').click();
  assert(await page.locator('#auto-panels').isChecked());
  await page.locator('[data-mode="pair"]').click();
  await page.locator('#add-library').uncheck();
  await page.locator('#task-name').fill('UI v2 验收 · 合成镜像双图');
  await page.locator('#file-input').setInputFiles([path.resolve('demo/01_microscopy_original.png'), path.resolve('demo/03_microscopy_mirrored.png')]);
  const pairResponse = page.waitForResponse(r => r.url().endsWith('/api/jobs') && r.request().method() === 'POST');
  await page.locator('#start-button').click();
  const pairId = (await (await pairResponse).json()).job_id;
  await page.waitForFunction(() => document.getElementById('result-title').textContent.includes('合成镜像双图') && document.getElementById('result-subtitle').textContent.includes('检测完成'), { timeout: 120000 });
  const pairDetail = await (await page.request.get(root + '/api/jobs/' + pairId)).json();
  assert.equal(pairDetail.job.settings.auto_panels, false);
  assert(pairDetail.findings.length >= 1);
  await page.locator('#findings-grid .text-button').first().click();
  await page.locator('#evidence-dialog').waitFor({ state: 'visible' });
  assert.equal(await page.locator('.source-download').count(), 2);
  assert(!(await page.locator('#view-context').isVisible()));
  await page.locator('#review-note').fill('UI v2 synthetic smoke only; not a scientific accuracy claim.');
  await page.locator('[data-review="confirmed"]').click();
  await page.waitForFunction(() => document.getElementById('review-message').textContent.includes('已保存'));
  await page.locator('#view-overlay').click();
  assert(await page.locator('#overlay-caution').isVisible());
  await page.locator('#close-dialog').click();
  await page.locator('[data-view="workspace"]').click();
  await page.locator('[data-mode="batch"]').click();
  await page.locator('#include-history').uncheck();
  await page.locator('#add-library').uncheck();
  await page.locator('#task-name').fill('UI v2 验收 · 单图版跨面板');
  await page.locator('#file-input').setInputFiles(montage);
  const montageResponse = page.waitForResponse(r => r.url().endsWith('/api/jobs') && r.request().method() === 'POST');
  await page.locator('#start-button').click();
  const montageId = (await (await montageResponse).json()).job_id;
  await page.waitForFunction(() => document.getElementById('result-title').textContent.includes('单图版跨面板') && document.getElementById('result-subtitle').textContent.includes('检测完成'), { timeout: 120000 });
  const montageDetail = await (await page.request.get(root + '/api/jobs/' + montageId)).json();
  assert.equal(montageDetail.job.settings.auto_panels, true);
  assert(montageDetail.job.diagnostics.segmentation.panels >= 2);
  assert(montageDetail.findings.some(f => f.image_a.parent_id && f.image_b.parent_id));
  assert(await page.locator('#job-diagnostics').isVisible());
  assert((await page.locator('#job-diagnostics').textContent()).includes('不保证覆盖每个面板'));
  await page.screenshot({ path: path.join(output, '01-montage-results.png'), fullPage: true, animations: 'disabled' });
  await page.locator('#findings-grid .text-button').first().click();
  await page.locator('#view-context').waitFor({ state: 'visible' });
  await page.waitForFunction(() => !document.getElementById('view-context').disabled);
  await page.locator('#view-context').click();
  assert(await page.locator('#parent-context-note').isVisible());
  assert.equal(await page.locator('.parent-source-link').count(), 2);
  await page.screenshot({ path: path.join(output, '02-parent-context.png'), fullPage: true, animations: 'disabled' });
  await page.locator('#close-dialog').click();

  // UI-only contract fixture, explicitly not an algorithm result: channel hints
  // and multiple independent region rows must render without conflating them.
  const fixture = JSON.parse(JSON.stringify(montageDetail));
  fixture.job.id = 'ui-contract'; fixture.job.name = 'UI contract fixture — not detector output';
  fixture.findings = [1, 2].map(region => ({ ...fixture.findings[0], id: 'ui-contract-' + region, region_index: region, channel_a: 'G', channel_b: 'G', review_context: 'shared_channel_content', review_status: 'pending' }));
  await page.route('**/api/jobs/ui-contract', route => route.fulfill({ json: fixture }));
  await page.route('**/api/jobs', route => route.request().method() === 'GET' ? route.fulfill({ json: { jobs: [{ ...fixture.job, findings_count: 2 }] } }) : route.continue());
  await page.locator('[data-view="tasks"]').click();
  await page.locator('#all-tasks .task-row').filter({ hasText: 'UI contract fixture' }).click();
  await page.waitForFunction(() => document.getElementById('result-title').textContent.includes('UI contract fixture'));
  assert.equal(await page.locator('#findings-grid .finding-card').count(), 2);
  assert((await page.locator('#findings-grid').textContent()).includes('区域 2'));
  await page.locator('#findings-grid .text-button').first().click();
  assert(await page.locator('#channel-context').isVisible());
  assert((await page.locator('#channel-context').textContent()).includes('不代表整张 Merge 图相同'));
  await page.waitForFunction(() => !document.getElementById('view-context').disabled);
  await page.screenshot({ path: path.join(output, '03-channel-contract.png'), fullPage: true, animations: 'disabled' });
  await page.locator('#close-dialog').click();
  await page.locator('[data-view="workspace"]').click();
  await page.setViewportSize({ width: 390, height: 844 });
  await page.locator('#toast').waitFor({ state: 'hidden', timeout: 10000 });
  await page.screenshot({ path: path.join(output, '04-mobile.png'), fullPage: true, animations: 'disabled' });
  assert(!(await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 3)), 'Mobile horizontal overflow');
  assert.deepEqual(errors, []);
  const result = { status: 'passed', only_synthetic_ui_data: true, pair_job: pairId, montage_job: montageId, montage_panels: montageDetail.job.diagnostics.segmentation.panels, montage_findings: montageDetail.findings.length, channel_fixture_is_not_detection_result: true, browser_errors: errors, output };
  fs.writeFileSync(path.join(output, 'ui_validation.json'), JSON.stringify(result, null, 2));
  console.log(JSON.stringify(result, null, 2));
  await browser.close();
})().catch(error => { console.error(error); process.exit(1); });
