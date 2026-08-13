// Entry point: load scenario → (pre-pass measure) → record context → run steps → webm. Prints VIDEO=<path>.
import { chromium } from 'playwright';
import { randomUUID } from 'node:crypto';
import { dirname, join, resolve } from 'node:path';
import { loadScenario, resolverRules } from './lib/scenario.mjs';
import { injectOverlay } from './lib/overlay-inject.mjs';
import { runSteps, measureHeights } from './lib/steps.mjs';

function parseArgs(argv) {
  const a = { allowJs: false, keep: false, overwrite: false, scenario: null };
  for (const x of argv) {
    if (x === '--allow-js') a.allowJs = true;
    else if (x === '--keep') a.keep = true;
    else if (x === '--overwrite') a.overwrite = true;
    else if (!x.startsWith('--')) a.scenario = x;
  }
  if (!a.scenario) throw new Error('usage: record.mjs <scenario.yaml> [--allow-js] [--keep] [--overwrite]');
  return a;
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const scenario = loadScenario(args.scenario, process.env);
  const runId = randomUUID().slice(0, 8);
  const prefix = '__sc_' + runId + '_';
  const chrome = scenario.chrome !== false;
  const width = scenario.viewport?.width ?? 1280;
  const launchArgs = [];
  const rr = resolverRules(scenario);
  if (rr) launchArgs.push(rr);
  launchArgs.push('--disable-features=AutofillServerCommunication,AutofillKeyMetricsLogging');

  const browser = await chromium.launch({ headless: true, args: launchArgs });

  // Resolve height: explicit number, or auto-fit pre-pass.
  let height = scenario.viewport?.height;
  if (height === 'auto' || height == null) {
    const measured = await measureHeights(browser, scenario, width);
    height = measured + (chrome ? 52 : 0);
    height = Math.min(height, 2000);
  }

  const outMp4 = resolve(process.cwd(), scenario.output || `out/${scenario.name}.mp4`);
  const outDir = dirname(outMp4);
  const ctx = await browser.newContext({
    viewport: { width, height },
    deviceScaleFactor: 2,
    recordVideo: { dir: join(outDir, 'raw'), size: { width, height } },
    httpCredentials: scenario.httpAuth ? { username: scenario.httpAuth.username, password: scenario.httpAuth.password } : undefined,
  });
  // IMPORTANT ORDERING: the chrome=false flag init-script MUST be registered BEFORE the overlay
  // init-script, because addInitScript scripts run in registration order on every new document, and
  // the overlay's ensure() reads window[prefix+'chrome'] to decide whether to build the bar.
  if (!chrome) await ctx.addInitScript((p) => { window[p + 'chrome'] = false; }, prefix);
  await injectOverlay(ctx, runId);

  const page = await ctx.newPage();
  page.on('dialog', (d) => { console.error('dialog dismissed:', d.type(), d.message()); d.dismiss().catch(() => {}); });
  page.on('download', (d) => { console.error('download cancelled:', d.url()); d.cancel().catch(() => {}); });
  const engine = { page, ctx, prefix, chrome, currentFrame: null, allowJs: args.allowJs, cursor: { x: width / 2, y: height / 2 }, caption: '' };
  const replayOverlay = async (targetPage) => {
    try { await targetPage.evaluate(([p, u]) => window[p + 'seturl']?.(u, true), [prefix, targetPage.url()]); } catch {}
    try { await targetPage.evaluate(([p, c]) => window[p + 'moveCursor']?.(c.x, c.y), [prefix, engine.cursor]); } catch {}
    if (engine.caption) {
      try { await targetPage.evaluate(([p, t]) => window[p + 'caption']?.(t), [prefix, engine.caption]); } catch {}
    }
  };
  // auto-reinit overlay url pill + cursor + active caption on top-frame navigation
  const attachNavigationReplay = (targetPage) => {
    targetPage.on('framenavigated', async (fr) => {
      if (fr !== targetPage.mainFrame()) return;
      await replayOverlay(targetPage);
    });
  };
  engine.selectPage = async (targetPage) => {
    attachNavigationReplay(targetPage);
    engine.page = targetPage;
    engine.currentFrame = null;
    // A popup/tab may have completed its first navigation before it is selected.
    await replayOverlay(targetPage);
  };
  attachNavigationReplay(page);

  await runSteps(engine, scenario.steps);

  const video = page.video();
  await ctx.close();
  await browser.close();
  const path = await video?.path();
  if (!path) { console.error('ERROR: no video produced'); process.exit(1); }
  console.log('OUT=' + outMp4);
  console.log('VIDEO=' + path);
}

main().catch((e) => { console.error(e?.stack || e); process.exit(1); });
