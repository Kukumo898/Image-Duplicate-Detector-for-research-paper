/* Isolated headless browser validation of the local application under development. */
const { chromium } = require('playwright');
const fs = require('node:fs');
const path = require('node:path');

(async () => {
  const browser = await chromium.launch({ channel: 'chrome', headless: true });
  const page = await browser.newPage({ viewport: { width: 1440, height: 1080 } });
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  const root = process.env.LABIMAGE_URL || 'http://127.0.0.1:8765';
  const output = path.resolve('test-results/ui');
  fs.mkdirSync(output, { recursive: true });
  await page.goto(root);
  await page.getByText('本地引擎已就绪', { exact: true }).waitFor();
  await page.screenshot({ path: path.join(output, '01-workspace.png'), fullPage: true, animations: 'disabled' });
  await page.locator('[data-mode="pair"]').click();
  await page.locator('#include-history').uncheck();
  await page.locator('#add-library').uncheck();
  await page.locator('#task-name').fill('界面验收 · 合成样本双图');
  await page.locator('#file-input').setInputFiles([
    path.resolve('demo/01_microscopy_original.png'),
    path.resolve('demo/03_microscopy_mirrored.png'),
  ]);
  await page.locator('#start-button').click();
  await page.locator('#result-title').filter({ hasText: '界面验收' }).waitFor();
  await page.waitForFunction(() => document.getElementById('result-findings').textContent.trim() === '1', { timeout: 120000 });
  await page.screenshot({ path: path.join(output, '02-results.png'), fullPage: true, animations: 'disabled' });
  // Read the app's rendered button names, not internal JavaScript application state.
  const buttons = await page.locator('#findings-grid button').allTextContents();
  const open = page.locator('#findings-grid button').filter({ hasText: /查看|证据|对照/ }).first();
  if (!(await open.count())) throw new Error('No evidence button: ' + buttons.join(','));
  await open.click();
  await page.locator('#evidence-dialog').waitFor({ state: 'visible' });
  if (await page.locator('.source-download').count() !== 2) throw new Error('Missing original source download links');
  await page.locator('#review-note').fill('合成镜像样本：界面端到端验收记录，不代表真实科研性能。');
  await page.locator('[data-review="confirmed"]').click();
  await page.waitForFunction(() => document.getElementById('review-message').textContent.includes('保存'));
  await page.screenshot({ path: path.join(output, '03-evidence.png'), fullPage: true, animations: 'disabled' });
  await page.locator('#view-overlay').click();
  await page.locator('#overlay-caution').waitFor({ state: 'visible' });
  await page.locator('#close-dialog').click();
  await page.locator('[data-view="workspace"]').click();
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({ path: path.join(output, '04-mobile.png'), fullPage: true, animations: 'disabled' });
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 3);
  if (overflow) throw new Error('Mobile layout overflows viewport');
  if (errors.length) throw new Error('Browser errors: ' + errors.join('\n'));
  console.log(JSON.stringify({ status: 'passed', screenshot_directory: output, browser_errors: errors }, null, 2));
  await browser.close();
})().catch(error => { console.error(error); process.exit(1); });
