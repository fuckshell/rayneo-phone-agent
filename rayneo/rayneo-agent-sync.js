#!/usr/bin/env node
const fs = require('node:fs');
const path = require('node:path');
const cp = require('node:child_process');
const { collect } = require('./rayneo-collect');
const { sync, atomic } = require('./rayneo-bridge');

function wslPath(file) {
  const absolute = path.win32.resolve(file);
  if (!/^[A-Za-z]:\\/.test(absolute)) throw Error('unsupported-wsl-path');
  return `/mnt/${absolute[0].toLowerCase()}/${absolute.slice(3).replace(/\\/g, '/')}`;
}
function acquireLock(dir, now = Date.now()) {
  fs.mkdirSync(path.dirname(dir), { recursive: true });
  try { fs.mkdirSync(dir); }
  catch (e) {
    if (e.code !== 'EEXIST') throw e;
    let owner;
    try { owner = JSON.parse(fs.readFileSync(path.join(dir, 'owner.json'), 'utf8')); } catch {}
    let alive = false;
    if (Number.isInteger(owner?.pid) && owner.pid > 0) {
      try { process.kill(owner.pid, 0); alive = true; }
      catch (err) { alive = err.code !== 'ESRCH'; }
    }
    // Every child call is bounded; unknown/new locks cannot be stolen mid-write.
    if (alive || (!owner && now - fs.statSync(dir).mtimeMs < 600000)) return null;
    const stale = `${dir}.stale-${process.pid}-${now}`;
    try { fs.renameSync(dir, stale); fs.mkdirSync(dir); }
    catch { return null; }
  }
  fs.writeFileSync(path.join(dir, 'owner.json'), JSON.stringify({ pid: process.pid, created_at: now }));
  return () => { fs.unlinkSync(path.join(dir, 'owner.json')); fs.rmdirSync(dir); };
}
function failure(stage, err) {
  // Child stderr may contain account/device identifiers; never log it.
  const code = String(err?.code || 'FAILED');
  return { stage, code: /^[A-Z0-9_-]{1,40}$/.test(code) ? code : 'FAILED' };
}
function publish(knowledge) {
  const python = process.env.RAYNEO_PYTHON || 'python3';
  const output = cp.execFileSync(python, [path.join(__dirname, 'rayneo-agent-publish.py')], {
    input: JSON.stringify({ source: knowledge,
      destination: process.env.RAYNEO_PUBLISH_ROOT || path.join(__dirname, 'published') }), encoding: 'utf8',
    timeout: 300000, maxBuffer: 1024 * 1024, windowsHide: true,
    stdio: ['pipe', 'pipe', 'pipe'],
  });
  const result = JSON.parse(output);
  if (!result.ok || !result.index_verified) throw Error('destination-not-verified');
  return result;
}
function run(config, dependencies = {}) {
  const capture = dependencies.collect || collect;
  const archive = dependencies.sync || sync;
  const deliver = dependencies.publish || publish;
  const unlock = acquireLock(config.lock);
  if (!unlock) return { ok: false, skipped: 'already-running' };
  try {
    let previous = {};
    try { previous = JSON.parse(fs.readFileSync(config.status, 'utf8')); } catch {}
    const result = { ok: false, checked_at: new Date().toISOString(), errors: [],
      last_success_at: previous.last_success_at || null };
    try { result.capture = capture(config.input); }
    catch (err) { result.errors.push(failure('capture', err)); }
    // The outbox remains deliverable even when the phone is disconnected.
    try {
      result.archive = archive(config);
      result.agent = deliver(config.knowledge);
      if (!result.agent?.ok || !result.agent.index_verified) throw Error('destination-not-verified');
    } catch (err) { result.errors.push(failure('publish-and-index', err)); }
    result.ok = result.errors.length === 0;
    if (result.ok) result.last_success_at = result.checked_at;
    atomic(config.status, JSON.stringify(result, null, 2) + '\n');
    return result;
  } finally { unlock(); }
}
function configFor(base) {
  return { input: path.join(base, 'rayneo-events.jsonl'), archive: path.join(base, 'archive-v2'),
    knowledge: path.join(base, 'knowledge-v2'), statePath: path.join(base, 'rayneo-status-v2.json'),
    status: path.join(base, 'rayneo-agent-sync-status.json'), lock: path.join(base, '.rayneo-agent-sync.lock') };
}
if (require.main === module) {
  const adbIndex = process.argv.indexOf('--adb');
  if (adbIndex !== -1) process.env.RAYNEO_ADB = process.argv[adbIndex + 1];
  try {
    const result = run(configFor(__dirname));
    console.log(JSON.stringify(result));
    process.exitCode = result.ok || result.skipped ? 0 : 1;
  } catch (err) { console.error(JSON.stringify(failure('worker', err))); process.exitCode = 1; }
}
module.exports = { run, configFor, acquireLock, wslPath };
