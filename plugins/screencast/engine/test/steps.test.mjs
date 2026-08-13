import { test } from 'node:test';
import assert from 'node:assert/strict';
import { runSteps } from '../lib/steps.mjs';

// Minimal page stub: records evaluate() calls, no browser involved.
const stubEngine = () => {
  const calls = [];
  return {
    calls,
    page: {
      evaluate: async (fn, arg) => { calls.push(arg); },
      goto: async () => {},
      url: () => 'about:blank',
    },
    prefix: 'p_',
    currentFrame: null,
    cursor: { x: 0, y: 0 },
    caption: '',
  };
};

test('caption step remembers the active text on the engine', async () => {
  const engine = stubEngine();
  await runSteps(engine, [{ caption: 'первый шаг' }]);
  // record.mjs replays engine.caption after every navigation — the overlay is
  // rebuilt from scratch there, so losing this field means invisible captions.
  assert.equal(engine.caption, 'первый шаг');
});

test('a later caption replaces the remembered one', async () => {
  const engine = stubEngine();
  await runSteps(engine, [{ caption: 'первый' }, { caption: 'второй' }]);
  assert.equal(engine.caption, 'второй');
});

test('empty caption clears the remembered text', async () => {
  const engine = stubEngine();
  await runSteps(engine, [{ caption: 'виден' }, { caption: '' }]);
  assert.equal(engine.caption, '');
});

test('followPopup selects the popup through the page-selection hook', async () => {
  const engine = stubEngine();
  const popup = { waitForLoadState: async () => {} };
  engine.ctx = { waitForEvent: async () => popup };
  engine.selectPage = async (selected) => { engine.selectedPage = selected; engine.page = selected; };
  engine.page.getByRole = () => ({ first: () => ({ scrollIntoViewIfNeeded: async () => {}, boundingBox: async () => ({ x: 0, y: 0, width: 1, height: 1 }), click: async () => {} }) });
  engine.page.mouse = { move: async () => {} };

  await runSteps(engine, [{ click: { role: 'button', name: 'Open', followPopup: true } }]);

  assert.equal(engine.selectedPage, popup);
});

test('switchToNewTab selects an existing tab through the page-selection hook', async () => {
  const engine = stubEngine();
  const tab = { waitForLoadState: async () => {} };
  engine.ctx = { pages: () => [engine.page, tab] };
  engine.selectPage = async (selected) => { engine.selectedPage = selected; engine.page = selected; };

  await runSteps(engine, [{ switchToNewTab: {} }]);

  assert.equal(engine.selectedPage, tab);
});
