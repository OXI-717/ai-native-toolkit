import { test } from 'node:test';
import assert from 'node:assert/strict';
import { chromium } from 'playwright';
import { injectOverlay } from '../lib/overlay-inject.mjs';

test('overlay injects suffixed globals and caption element', async () => {
  const browser = await chromium.launch({ headless: true });
  const ctx = await browser.newContext();
  const prefix = await injectOverlay(ctx, 'abc123');
  const page = await ctx.newPage();
  await page.goto('about:blank');
  await page.evaluate((p) => window[p + 'caption']('hello'), prefix);
  const text = await page.evaluate((p) => document.getElementById(p + 'caption').textContent, prefix);
  assert.equal(text, 'hello');
  await browser.close();
});

import { execFileSync } from 'node:child_process';
import { writeFileSync, mkdirSync, existsSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

test('end-to-end: scenario → mp4 against local fixtures', () => {
  const dir = fileURLToPath(new URL('./.out/e2e/', import.meta.url));
  mkdirSync(dir, { recursive: true });
  const shop = fileURLToPath(new URL('./fixtures/shop.html', import.meta.url));
  const scenario = `schemaVersion: "1"
name: smoke
viewport: { width: 800, height: 600 }
output: ${dir}smoke.mp4
steps:
  - caption: "shop"
  - goto: { url: "file://${shop}", waitFor: { heading: "Item B" } }
  - click: { role: button, name: "Оплатить", expectUrl: "pay.html" }
  - waitFor: { heading: "Имитация оплаты" }
  - click: { role: button, name: "Подтвердить" }
  - waitFor: { text: "Готово" }
  - waitMs: 300
`;
  const sp = dir + 'smoke.yaml';
  writeFileSync(sp, scenario);
  const engine = fileURLToPath(new URL('../', import.meta.url));
  execFileSync('bash', [engine + 'make-video.sh', sp], { stdio: 'inherit', cwd: dir });
  assert.ok(existsSync(dir + 'smoke.mp4'), 'mp4 produced');
});
