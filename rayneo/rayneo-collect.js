// Read the main Android user's current cache. No UI navigation or app writes.
const fs = require('node:fs');
const path = require('node:path');
const cp = require('node:child_process');
const crypto = require('node:crypto');
const { readHive } = require('./hive-reader');
function adb(args) {
  if (!process.env.RAYNEO_SERIAL) throw Error('RAYNEO_SERIAL_REQUIRED');
  return cp.execFileSync(process.env.RAYNEO_ADB || 'adb',
    ['-s', process.env.RAYNEO_SERIAL, ...args],
    { timeout: 30000, maxBuffer: 64 * 1024 * 1024, windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'] });
}
function extract(entries, captured_at) {
  const rows = [];
  for (const [key, cached] of entries) {
    if (typeof key !== 'string' || !key.includes('/lifelog/api/v1/')) continue;
    const endpoint = new URL(key).pathname;
    if (!['/lifelog/api/v1/journal/latest', '/lifelog/api/v1/events'].includes(endpoint)) continue;
    const response = typeof cached?.response === 'string' ? JSON.parse(cached.response) : cached?.response;
    const data = response?.data;
    if (!data) continue;
    const base = { source: 'rayneo-hive-structured', endpoint, captured_at, cache_created_at: cached.createdAt };
    if (endpoint.endsWith('/journal/latest')) {
      if (!Array.isArray(data)) throw Error('journal schema changed');
      for (const item of data) if (typeof item.context === 'string' && item.context.trim()) rows.push({ ...base,
        kind: 'lifelog_daily_summary', id: String(item.id), event_date: item.periodStart,
        generated_at: item.generatedAt, content: { title: item.title, context: item.context } });
    } else {
      if (!Array.isArray(data.events)) throw Error('events schema changed');
      for (const item of data.events) if (typeof item.summaryContent === 'string' && item.summaryContent.trim()) rows.push({ ...base,
        kind: 'lifelog_event', id: String(item.id), event_date: data.date,
        start_time: item.startTime, end_time: item.endTime,
        content: { title: item.title, summary: item.summaryContent } });
    }
  }
  return rows;
}
function collect(output) {
  const dir = '/data/user/0/com.rayneo.venus.pub/app_flutter';
  const names = adb(['shell', 'su', '-c', `ls ${dir}`]).toString('utf8').trim().split(/\s+/)
    .filter(n => /^api_cache_user_\d+\.hive$/.test(n));
  if (names.length !== 1) throw Error(`Expected one main-user RayNeo account cache; found ${names.length}`);
  const buffer = adb(['exec-out', 'su', '-c', `cat ${dir}/${names[0]}`]);
  const parsed = readHive(buffer);
  const rows = extract(parsed.entries, new Date().toISOString());
  if (!rows.length) throw Error('No structured RayNeo summaries in phone cache');
  const fresh = appendRows(output, rows);
  return { frames: parsed.frames, records: rows.length, new_records: fresh,
    event_dates: [...new Set(rows.map(r => r.event_date))].sort(), captured_at: new Date().toISOString() };
}
function appendRows(output, rows) {
  fs.mkdirSync(path.dirname(output), { recursive: true });
  const existing = new Set();
  const identity = r => crypto.createHash('sha256').update(JSON.stringify([r.kind,r.id,r.event_date,r.content])).digest('hex');
  if (fs.existsSync(output)) for (const line of fs.readFileSync(output,'utf8').split('\n')) {
    try { const r = JSON.parse(line); if (r.source === 'rayneo-hive-structured') existing.add(identity(r)); } catch {}
  }
  const fresh = rows.filter(r => { const id = identity(r); if (existing.has(id)) return false; existing.add(id); return true; });
  const prior = fs.existsSync(output) ? fs.readFileSync(output) : Buffer.alloc(0);
  const prefix = prior.length && prior[prior.length - 1] !== 10 ? '\n' : '';
  if (fresh.length || prefix) {
    fs.appendFileSync(output, prefix + (fresh.length ? fresh.map(r => JSON.stringify(r)).join('\n') + '\n' : ''), {mode:0o600});
  }
  return fresh.length;
}
module.exports = { collect, extract, appendRows };
