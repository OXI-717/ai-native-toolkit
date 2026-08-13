import { readFileSync } from 'node:fs';
export const overlaySource = readFileSync(new URL('../overlay.js', import.meta.url), 'utf8');

// Inject overlay into every document of the context, keyed by runId. Returns the global-name prefix.
// Note: addInitScript({ content, arg }) ignores `arg` when content is a string — Playwright only
// threads `arg` through when `fun` is a JS function. We prepend a `const arg = <runId>` declaration
// so overlay.js can still reference `arg` as specified, without any changes to overlay.js itself.
export async function injectOverlay(context, runId) {
  const content = 'const arg = ' + JSON.stringify(runId) + ';\n' + overlaySource;
  await context.addInitScript({ content });
  return '__sc_' + runId + '_';
}
