const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// Target the current frame if inside one, else the page.
function target(engine) { return engine.currentFrame ?? engine.page; }

async function caption(engine, text) {
  await engine.page.evaluate(([p, t]) => window[p + 'caption']?.(t), [engine.prefix, text]).catch(() => {});
}

// Resolve a locator from {role,name} | {selector} (+ optional inArticle filter), within current target.
function locate(engine, spec) {
  const t = target(engine);
  let base = t;
  if (spec.inArticle) base = t.getByRole('article').filter({ hasText: spec.inArticle });
  if (spec.selector) return base.locator(spec.selector).first();
  return base.getByRole(spec.role, { name: spec.name, exact: false }).first();
}

async function moveTo(engine, x, y) {
  const steps = 22;
  const from = engine.cursor;
  for (let i = 1; i <= steps; i++) {
    const nx = from.x + (x - from.x) * (i / steps), ny = from.y + (y - from.y) * (i / steps);
    await engine.page.mouse.move(nx, ny);
    await engine.page.evaluate(([p, px, py]) => window[p + 'moveCursor']?.(px, py), [engine.prefix, nx, ny]).catch(() => {});
    await sleep(14);
  }
  engine.cursor = { x, y };
}

// Click with smooth cursor + ripple. Returns the resolved Playwright locator.
async function cursorClick(engine, loc) {
  await loc.scrollIntoViewIfNeeded();
  // clear the chrome bar if the target sits under it
  if (engine.chrome) {
    await loc.evaluate((el, bar) => { const top = el.getBoundingClientRect().top; if (top < bar) window.scrollBy(0, top - bar - 8); }, 52).catch(() => {});
  }
  const b = await loc.boundingBox();
  const x = b.x + b.width / 2, y = b.y + b.height / 2;
  await moveTo(engine, x, y);
  await engine.page.evaluate(([p, px, py]) => window[p + 'ripple']?.(px, py), [engine.prefix, x, y]).catch(() => {});
  await sleep(120);
  await loc.click();
}

async function doClick(engine, spec) {
  const loc = locate(engine, spec);
  const popupP = spec.followPopup ? engine.ctx.waitForEvent('page') : null;
  await cursorClick(engine, loc);
  if (popupP) { const np = await popupP; await np.waitForLoadState('domcontentloaded'); engine.page = np; }
  if (spec.expectUrl) {
    const pat = new RegExp(spec.expectUrl);
    try {
      await engine.page.waitForURL(pat, { waitUntil: 'commit', timeout: spec.timeout ?? 18000 });
    } catch {
      // absorb commit-vs-url race before deciding to retry
      let matched = false;
      try { await engine.page.waitForURL(pat, { timeout: 1500 }); matched = true; } catch {}
      if (!matched && spec.idempotent !== false) {
        await cursorClick(engine, loc);
        await engine.page.waitForURL(pat, { waitUntil: 'commit', timeout: spec.timeout ?? 18000 });
      } else if (!matched) {
        throw new Error(`expectUrl ${spec.expectUrl} not reached (idempotent:false, no retry)`);
      }
    }
  }
}

async function waitForSpec(engine, w) {
  const t = target(engine);
  if (w.heading) return t.getByRole('heading', { name: w.heading }).first().waitFor({ timeout: w.timeout ?? 20000 });
  if (w.text) return t.getByText(new RegExp(w.text)).first().waitFor({ timeout: w.timeout ?? 20000 });
  if (w.selector) return t.locator(w.selector).first().waitFor({ timeout: w.timeout ?? 20000 });
}

async function doType(engine, spec) {
  const loc = locate(engine, { selector: spec.selector });
  // env-sourced values into a visible field must be masked
  if (spec.value && !spec.mask) {
    const isPw = await loc.evaluate((el) => el.type === 'password').catch(() => false);
    if (!isPw) console.error('WARN: typing into a visible field without mask:true — set mask:true or use a password field');
  }
  await loc.fill(spec.value ?? '');
}

async function doHighlight(engine, spec) {
  const t = target(engine);
  let loc;
  if (spec.selector) loc = t.locator(spec.selector).first();
  else {
    let base = t;
    if (spec.in) base = t.getByRole(spec.in);
    loc = base.getByRole(spec.role, { name: spec.name }).nth(spec.index ?? 0);
  }
  await loc.evaluate((n) => { n.style.outline = '3px solid #2ec26b'; n.style.borderRadius = '12px'; n.style.transition = 'outline .3s'; n.scrollIntoView({ block: 'center' }); }).catch(() => {});
}

export async function runSteps(engine, steps) {
  for (const step of steps) {
    if ('caption' in step) await caption(engine, step.caption);
    else if ('goto' in step) { await engine.page.goto(step.goto.url, { waitUntil: 'domcontentloaded' }); if (step.goto.waitFor) await waitForSpec(engine, step.goto.waitFor); }
    else if ('click' in step) await doClick(engine, step.click);
    else if ('type' in step) await doType(engine, step.type);
    else if ('waitFor' in step) await waitForSpec(engine, step.waitFor);
    else if ('expectUrl' in step) await engine.page.waitForURL(new RegExp(step.expectUrl.pattern), { waitUntil: 'commit', timeout: step.expectUrl.timeout ?? 18000 });
    else if ('waitMs' in step) await sleep(step.waitMs);
    else if ('highlight' in step) await doHighlight(engine, step.highlight);
    else if ('resize' in step) throw new Error('resize is unsupported (fixed recordVideo.size)');
    else if ('switchToNewTab' in step || 'switchToFrame' in step || 'switchToParent' in step || 'uploadFile' in step) await runSwitchStep(engine, step);
    else if ('js' in step) await runJsStep(engine, step.js);
    else throw new Error('unknown step: ' + JSON.stringify(Object.keys(step)));
  }
}

async function runSwitchStep(engine, step) {
  if ('switchToNewTab' in step) {
    const s = step.switchToNewTab || {};
    const existing = engine.ctx.pages().filter((p) => p !== engine.page);
    const np = existing.length
      ? existing[existing.length - 1]
      : await engine.ctx.waitForEvent('page', { timeout: s.timeout ?? 15000 });
    await np.waitForLoadState('domcontentloaded');
    if (s.expectUrl) await np.waitForURL(new RegExp(s.expectUrl), { waitUntil: 'commit', timeout: s.timeout ?? 15000 });
    engine.page = np; engine.currentFrame = null;
    return;
  }
  if ('switchToFrame' in step) {
    engine.currentFrame = engine.page.frameLocator(step.switchToFrame.selector);
    return;
  }
  if ('switchToParent' in step) { engine.currentFrame = null; return; }
  if ('uploadFile' in step) {
    const loc = (engine.currentFrame ?? engine.page).locator(step.uploadFile.selector).first();
    await loc.setInputFiles(step.uploadFile.path);
    return;
  }
}
async function runJsStep(engine, code) {
  if (!engine.allowJs) { console.error('WARN: js step skipped (pass --allow-js to enable)'); return; }
  const fn = new Function('page', 'context', `return (async () => { ${code} })();`);
  await fn(engine.page, engine.ctx);
}

// Second context in the SAME browser, no recording/overlay. Measures the tallest goto page.
export async function measureHeights(browser, scenario, width) {
  const gotos = scenario.steps.filter((s) => 'goto' in s).map((s) => s.goto.url);
  if (!gotos.length) return 800;
  const ctx = await browser.newContext({ viewport: { width, height: 800 } });
  const page = await ctx.newPage();
  let max = 600;
  for (const url of gotos) {
    try {
      await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 20000 });
      await page.evaluate(() => document.fonts && document.fonts.ready).catch(() => {});
      await new Promise((r) => setTimeout(r, 400));
      await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight)); // trigger lazy-load
      await new Promise((r) => setTimeout(r, 400));
      await page.evaluate(() => window.scrollTo(0, 0));
      const h = await page.evaluate(() => Math.max(document.documentElement.scrollHeight, document.documentElement.clientHeight));
      if (h > max) max = h;
    } catch (e) { console.error('measure: skip', url, e.message); }
  }
  await ctx.close();
  return max;
}
