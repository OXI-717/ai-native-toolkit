// Scenario load, env interpolation, schema validation, resolver-rule building.

import { readFileSync } from 'node:fs';
import { parse as parseYaml } from 'yaml';

const VAR_RE = /\$\$|\$([A-Z_][A-Z0-9_]*)/g;

// Walk every string in the object tree, collecting referenced var names ($$ is an escape, not a var).
export function collectVars(node, acc = new Set()) {
  if (typeof node === 'string') {
    let m;
    VAR_RE.lastIndex = 0;
    while ((m = VAR_RE.exec(node))) { if (m[1]) acc.add(m[1]); }
  } else if (Array.isArray(node)) {
    node.forEach((v) => collectVars(v, acc));
  } else if (node && typeof node === 'object') {
    Object.values(node).forEach((v) => collectVars(v, acc));
  }
  return [...acc];
}

// Replace $VAR with env value (and $$ with $). Throws naming the field path if a var is unset.
export function interpolate(node, env, path = '') {
  if (typeof node === 'string') {
    return node.replace(VAR_RE, (full, name) => {
      if (full === '$$') return '$';
      if (!(name in env) || env[name] === undefined || env[name] === '') {
        throw new Error(`Unset env var $${name} referenced at "${path || '<root>'}"`);
      }
      return env[name];
    });
  }
  if (Array.isArray(node)) return node.map((v, i) => interpolate(v, env, `${path}[${i}]`));
  if (node && typeof node === 'object') {
    const out = {};
    for (const [k, v] of Object.entries(node)) out[k] = interpolate(v, env, path ? `${path}.${k}` : k);
    return out;
  }
  return node;
}

// Minimal draft-07 subset validator: required, additionalProperties:false, const, type, enum/oneOf-ish.
function validate(node, schema, schemaRoot, path = '') {
  const fail = (msg) => { throw new Error(`scenario invalid at "${path || '<root>'}": ${msg}`); };
  if (schema.const !== undefined && node !== schema.const) fail(`expected const ${JSON.stringify(schema.const)}`);
  if (schema.type === 'object' || schema.properties) {
    if (typeof node !== 'object' || node === null || Array.isArray(node)) fail('expected object');
    for (const r of schema.required || []) if (!(r in node)) fail(`missing required "${r}"`);
    if (schema.additionalProperties === false) {
      for (const k of Object.keys(node)) if (!(schema.properties && k in schema.properties)) fail(`additional property "${k}"`);
    }
    for (const [k, v] of Object.entries(node)) {
      if (schema.properties && schema.properties[k]) validate(v, schema.properties[k], schemaRoot, path ? `${path}.${k}` : k);
    }
  } else if (schema.type === 'array') {
    if (!Array.isArray(node)) fail('expected array');
    if (schema.items) node.forEach((v, i) => validate(v, schema.items, schemaRoot, `${path}[${i}]`));
  } else if (schema.oneOf) {
    if (!schema.oneOf.some((s) => { try { validate(node, s, schemaRoot, path); return true; } catch { return false; } })) fail('matched no oneOf branch');
  } else if (schema.type === 'integer') {
    if (!Number.isInteger(node)) fail('expected integer');
    if (schema.minimum !== undefined && node < schema.minimum) fail(`minimum ${schema.minimum}`);
  } else if (schema.type === 'string') {
    if (typeof node !== 'string') fail('expected string');
    if (schema.pattern && !new RegExp(schema.pattern).test(node)) fail(`pattern ${schema.pattern}`);
  } else if (schema.type === 'boolean') {
    if (typeof node !== 'boolean') fail('expected boolean');
  }
}

export function loadScenario(path, env = process.env) {
  const raw = path.endsWith('.json') ? JSON.parse(readFileSync(path, 'utf8')) : parseYaml(readFileSync(path, 'utf8'));
  if (raw == null || typeof raw !== 'object') throw new Error('scenario must be a mapping');
  if (raw.schemaVersion !== '1') {
    throw new Error(`unsupported schemaVersion ${JSON.stringify(raw.schemaVersion)} — engine speaks "1". Migrate the scenario; the engine will not run it.`);
  }
  const declared = collectVars(raw);
  const missing = declared.filter((v) => !(v in env) || env[v] === undefined || env[v] === '');
  if (missing.length) throw new Error(`unset env vars referenced in scenario: ${missing.join(', ')}`);
  const filled = interpolate(raw, env);
  const schemaUrl = new URL('../scenario.schema.json', import.meta.url);
  const schema = JSON.parse(readFileSync(schemaUrl, 'utf8'));
  validate(filled, schema, schema);
  return filled;
}

// Build the Chromium --host-resolver-rules launch arg, or null if no overrides. `to` may be ip or ip:port.
export function resolverRules(scenario) {
  const o = scenario.dnsOverride;
  if (!o || !o.length) return null;
  return '--host-resolver-rules=' + o.map((r) => `MAP ${r.from} ${r.to}`).join(',');
}
