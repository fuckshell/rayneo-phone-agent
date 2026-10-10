const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { run, configFor, acquireLock, wslPath } = require('./rayneo-agent-sync');
const { extract, appendRows } = require('./rayneo-collect');
const { sync } = require('./rayneo-bridge');
const { readHive, crc32 } = require('./hive-reader');
const row = { source: 'rayneo-hive-structured', kind: 'lifelog_daily_summary', id: 'synthetic-1',
  event_date: '2026-10-01', content: 'Synthetic test record only.' };
function temp(t) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'rayneo-sync-test-'));
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  return configFor(dir);
}
test('WSL paths preserve spaces and Unicode without shell interpolation', () => {
  assert.equal(wslPath('C:\\test space\\file.py'), '/mnt/c/test space/file.py');
  assert.throws(() => wslPath('\\\\server\\file'));
});
test('structured collection excludes request metadata', () => {
  const entries = new Map([
    ['https://example.test/lifelog/api/v1/journal/latest', { response: { data: [
      { id: '1', context: 'Synthetic diary', periodStart: '2026-10-01' }, { id: '2', title: 'Only metadata' }] } }],
    ['https://example.test/lifelog/api/v1/events', { response: JSON.stringify({ data: {
      date: '2026-10-01', events: [{ id: '3', summaryContent: 'Synthetic event' }] } }) }],
    ['https://example.test/lifelog/api/v1/debug', { response: { data: 'request' } }],
  ]);
  assert.deepEqual(extract(entries, 'now').map(r => r.kind), ['lifelog_daily_summary', 'lifelog_event']);
  assert.throws(() => extract(new Map([[entries.keys().next().value, { response: { data: {} } }]]), 'now'));
});
test('dedup and missing final newline preserve a complete last record', t => {
  const cfg = temp(t);
  fs.writeFileSync(cfg.input, JSON.stringify(row));
  assert.equal(appendRows(cfg.input, [row]), 0);
  assert.ok(fs.readFileSync(cfg.input, 'utf8').endsWith('\n'));
  assert.equal(sync(cfg).total, 1);
  assert.equal(appendRows(cfg.input, [row]), 0);
});
test('partial tail is ignored until completed, and then replayed once', t => {
  const cfg = temp(t), text = JSON.stringify(row);
  fs.writeFileSync(cfg.input, text.slice(0, 20));
  assert.equal(sync(cfg).total, 0);
  fs.appendFileSync(cfg.input, text.slice(20) + '\n\n');
  assert.equal(sync(cfg).added, 1);
  assert.equal(sync(cfg).added, 0);
});
test('failed local publication retains archive for replay', t => {
  const cfg = temp(t);
  appendRows(cfg.input, [row]);
  fs.writeFileSync(cfg.knowledge, 'blocked');
  assert.throws(() => sync(cfg));
  assert.equal(fs.readdirSync(cfg.archive).length, 1);
  fs.unlinkSync(cfg.knowledge);
  assert.equal(sync(cfg).published, 1);
});
test('failed Agent delivery is not success and retries without new phone data', t => {
  const cfg = temp(t);
  const deps = { collect: file => ({ new_records: appendRows(file, [row]) }),
    publish: () => { throw Error('sensitive child output'); } };
  assert.equal(run(cfg, deps).ok, false);
  assert.equal(JSON.parse(fs.readFileSync(cfg.status)).last_success_at, null);
  assert.ok(!fs.readFileSync(cfg.status, 'utf8').includes('sensitive'));
  deps.publish = () => ({ ok: true, index_verified: true, records: 1 });
  const result = run(cfg, deps);
  assert.equal(result.ok, true);
  assert.equal(result.capture.new_records, 0);
  assert.equal(result.archive.total, 1);
  assert.ok(result.last_success_at);
});
test('disconnected phone does not block archived delivery or claim fresh capture', t => {
  const cfg = temp(t);
  appendRows(cfg.input, [row]);
  let delivered = false;
  const result = run(cfg, { collect: () => { throw Error('offline'); }, publish: () => {
    delivered = true; return { ok: true, index_verified: true }; } });
  assert.equal(delivered, true);
  assert.equal(result.ok, false);
  assert.equal(result.errors[0].stage, 'capture');
});
test('single writer lock prevents overlap and releases after failure', t => {
  const cfg = temp(t), unlock = acquireLock(cfg.lock);
  assert.equal(run(cfg).skipped, 'already-running');
  unlock();
  assert.equal(run(cfg, { collect: () => { throw Error(); },
    sync: () => { throw Error(); }, publish: () => { throw Error(); } }).ok, false);
  const next = acquireLock(cfg.lock);
  assert.equal(typeof next, 'function');
  next();
});
test('unverified destination response cannot become a success', t => {
  const cfg = temp(t);
  const result = run(cfg, { collect: file => ({ new_records: appendRows(file, [row]) }),
    publish: () => ({ ok: true, index_verified: false }) });
  assert.equal(result.ok, false);
  assert.equal(result.last_success_at, null);
});
test('dead worker lock recovers', t => {
  const cfg = temp(t);
  fs.mkdirSync(cfg.lock);
  fs.writeFileSync(path.join(cfg.lock, 'owner.json'), JSON.stringify({ pid: 2147483647 }));
  const unlock = acquireLock(cfg.lock);
  assert.equal(typeof unlock, 'function');
  unlock();
});
test('Hive CRC and partial frames fail closed', () => {
  const frame = Buffer.alloc(11);
  frame.writeUInt32LE(11);
  frame[4] = 1; frame[5] = 1; frame[6] = 65;
  frame.writeUInt32LE(crc32(frame.subarray(0, 7)), 7);
  assert.equal(readHive(frame).frames, 1);
  assert.throws(() => readHive(frame.subarray(0, 10)));
  frame[6] = 66;
  assert.throws(() => readHive(frame), /CRC/);
});
