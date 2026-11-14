const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { pathToFileURL } = require('node:url');
const { chromium } = require('/root/playwright-runtime/node_modules/playwright');

const root = '/root/aidoku-uiux-baseline-20261114';
const delivery = path.join(root, 'review');
const manifest = JSON.parse(fs.readFileSync(path.join(delivery, 'manifest.json'), 'utf8'));
const report = { environment: 'test-env', pageErrors: [], scenes: [], layouts: [], inline: [] };

async function waitImages(page, ids) {
  await page.waitForFunction(names => names.every(id => {
    const image = document.getElementById(id);
    return image && image.complete && image.naturalWidth > 0;
  }), ids);
}

async function main() {
  const browser = await chromium.launch({
    headless: true,
    executablePath: '/root/kindle-ui-baseline-20261113-a7f3/browsers/chromium-1187/chrome-linux/chrome',
    args: ['--no-sandbox', '--disable-dev-shm-usage'],
  });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1200 } });
    page.on('pageerror', error => report.pageErrors.push(error.message));
    await page.goto(pathToFileURL(path.join(delivery, 'index.html')).href);
    assert.equal(await page.locator('#scene-select option').count(), 31);
    for (const scene of manifest.scenes) {
      await page.selectOption('#scene-select', scene.id);
      await waitImages(page, ['scribe-image', 'pw5-image']);
      for (const device of ['scribe', 'pw5']) {
        const actual = await page.locator('#' + device + '-image').evaluate(image => ({
          width: image.naturalWidth, height: image.naturalHeight, source: image.getAttribute('src'),
        }));
        assert.deepEqual(actual, { width: scene.devices[device].width, height: scene.devices[device].height, source: scene.devices[device].png });
      }
      report.scenes.push({ scene: scene.id, bothOriginalImagesLoaded: true });
    }
    await page.selectOption('#scene-select', 'library');
    await waitImages(page, ['scribe-image', 'pw5-image']);
    await page.click('#next-scene');
    assert.equal(await page.inputValue('#scene-select'), 'library-page-two');
    await page.click('#previous-scene');
    assert.equal(await page.inputValue('#scene-select'), 'library');
    for (const width of [1440, 1024, 736, 375, 320]) {
      await page.setViewportSize({ width, height: 1100 });
      await waitImages(page, ['scribe-image', 'pw5-image']);
      const geometry = await page.evaluate(() => {
        const a = document.getElementById('scribe-image');
        const b = document.getElementById('pw5-image');
        return {
          viewport: window.innerWidth, document: document.documentElement.scrollWidth,
          scribeScale: a.getBoundingClientRect().width / a.naturalWidth,
          paperwhiteScale: b.getBoundingClientRect().width / b.naturalWidth,
        };
      });
      assert.ok(geometry.document <= width, JSON.stringify(geometry));
      assert.ok(Math.abs(geometry.scribeScale - geometry.paperwhiteScale) < .002, JSON.stringify(geometry));
      report.layouts.push({ ...geometry, passed: true });
    }
    await page.setViewportSize({ width: 1440, height: 1200 });
    await page.selectOption('#scale-select', 'equal');
    let boxes = await page.locator('.shell img').evaluateAll(images => images.map(image => image.getBoundingClientRect().width));
    assert.ok(Math.abs(boxes[0] - boxes[1]) < 1);
    await page.selectOption('#frame-select', 'off');
    boxes = await page.locator('.shell').evaluateAll(shells => shells.map(shell => Number.parseFloat(getComputedStyle(shell).paddingLeft)));
    assert.deepEqual(boxes, [0, 0]);
    await page.click('[data-original="scribe"]');
    assert.ok(await page.locator('#original-dialog').evaluate(dialog => dialog.open));
    await waitImages(page, ['original-image']);
    assert.equal(await page.locator('#original-image').evaluate(image => image.getBoundingClientRect().width), 930);
    await page.selectOption('#zoom-select', '2');
    assert.equal(await page.locator('#original-image').evaluate(image => image.getBoundingClientRect().width), 1860);
    await page.keyboard.press('Escape');
    assert.equal(await page.locator('#original-dialog').evaluate(dialog => dialog.open), false);
    await page.selectOption('#scale-select', 'same');
    await page.selectOption('#frame-select', 'on');
    fs.mkdirSync(path.join(root, 'qa'), { recursive: true });
    await page.screenshot({ path: path.join(root, 'qa/viewer-desktop.png'), fullPage: true });
    await page.setViewportSize({ width: 375, height: 900 });
    await page.screenshot({ path: path.join(root, 'qa/viewer-mobile.png'), fullPage: true });
    const inline = await browser.newPage({ viewport: { width: 1024, height: 1000 } });
    inline.on('pageerror', error => report.pageErrors.push(error.message));
    await inline.goto(pathToFileURL(path.join(root, 'inline-preview.html')).href);
    await inline.frameLocator('iframe').locator('#kindle-baseline-scene').waitFor();
    const frame = inline.frames().find(item => item !== inline.mainFrame());
    for (const width of [1024, 736, 320]) {
      await inline.setViewportSize({ width, height: 1000 });
      for (const scene of ['library', 'detail', 'reader-controls', 'settings']) {
        await frame.selectOption('#kindle-baseline-scene', scene);
        await waitImages(frame, ['kindle-inline-scribe', 'kindle-inline-pw5']);
        const geometry = await frame.evaluate(() => ({ viewport: innerWidth, content: document.documentElement.scrollWidth }));
        assert.ok(geometry.content <= geometry.viewport, JSON.stringify(geometry));
        report.inline.push({ width, scene, passed: true });
      }
    }
    await inline.setViewportSize({ width: 1024, height: 1000 });
    await frame.selectOption('#kindle-baseline-scene', 'library');
    await waitImages(frame, ['kindle-inline-scribe', 'kindle-inline-pw5']);
    await inline.screenshot({ path: path.join(root, 'qa/inline-preview.png'), fullPage: true });
    assert.deepEqual(report.pageErrors, []);
    report.passed = true;
  } finally {
    fs.writeFileSync(path.join(delivery, 'evidence/viewer-checks.json'), JSON.stringify(report, null, 2));
    await browser.close();
  }
  console.log(JSON.stringify({ passed: report.passed, scenePairs: report.scenes.length, responsiveWidths: report.layouts.length, inlineChecks: report.inline.length, pageErrors: report.pageErrors.length }));
}

main().catch(error => { console.error(error); process.exitCode = 1; });
