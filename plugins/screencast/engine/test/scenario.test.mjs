import { test } from 'node:test';
import assert from 'node:assert/strict';
import { interpolate, collectVars, loadScenario, resolverRules } from '../lib/scenario.mjs';
import { writeFileSync, mkdirSync } from 'node:fs';

const TMP = new URL('./.out/', import.meta.url).pathname;
mkdirSync(TMP, { recursive: true });
const wr = (name, body) => { const p = TMP + name; writeFileSync(p, body); return p; };

test('collectVars finds all $VAR tokens across nested fields', () => {
  const obj = { url: '$BASE_URL/x', steps: [{ caption: 'hi $NAME' }, { type: { value: '$API_KEY' } }] };
  assert.deepEqual(collectVars(obj).sort(), ['API_KEY', 'BASE_URL', 'NAME']);
});

test('interpolate substitutes from env', () => {
  const out = interpolate({ url: '$BASE_URL/x', n: 5 }, { BASE_URL: 'https://h' });
  assert.equal(out.url, 'https://h/x');
  assert.equal(out.n, 5);
});

test('interpolate hard-fails on unset var, naming the field path', () => {
  assert.throws(
    () => interpolate({ a: { b: '$MISSING' } }, {}),
    /MISSING.*a\.b/s
  );
});

test('interpolate leaves $$ escape as literal $', () => {
  assert.equal(interpolate({ v: 'a$$b' }, {}).v, 'a$b');
});

test('loadScenario parses YAML, applies env, validates', () => {
  const p = wr('ok.yaml', 'schemaVersion: "1"\nname: demo\nenv: [BASE]\nsteps:\n  - goto: { url: "$BASE/x" }\n');
  const s = loadScenario(p, { BASE: 'https://h' });
  assert.equal(s.name, 'demo');
  assert.equal(s.steps[0].goto.url, 'https://h/x');
});

test('loadScenario rejects wrong schemaVersion as hard error', () => {
  const p = wr('badver.yaml', 'schemaVersion: "2"\nname: demo\nsteps: []\n');
  assert.throws(() => loadScenario(p, {}), /schemaVersion/);
});

test('loadScenario rejects unknown top-level key', () => {
  const p = wr('unknown.yaml', 'schemaVersion: "1"\nname: demo\nbogus: 1\nsteps: []\n');
  assert.throws(() => loadScenario(p, {}), /bogus|additional/i);
});

test('resolverRules returns null when no dnsOverride', () => {
  assert.equal(resolverRules({}), null);
});

test('resolverRules builds MAP rules, preserving port', () => {
  const arg = resolverRules({ dnsOverride: [{ from: 'a.test', to: '1.2.3.4' }, { from: 'b.test', to: '127.0.0.1:8080' }] });
  assert.equal(arg, '--host-resolver-rules=MAP a.test 1.2.3.4,MAP b.test 127.0.0.1:8080');
});
