// Read-only Hive 2 primitive frame decoder. Format reference (not copied code):
// https://github.com/hivedb/hive/tree/v2.2.3/hive/lib/src/binary
const { TextDecoder } = require('node:util');
const decoder = new TextDecoder('utf-8', { fatal: true });
function crc32(data) {
  let crc = 0xffffffff;
  for (const b of data) {
    crc ^= b;
    for (let i = 0; i < 8; i++) crc = (crc >>> 1) ^ ((crc & 1) ? 0xedb88320 : 0);
  }
  return (crc ^ 0xffffffff) >>> 0;
}
class Reader {
  constructor(data) { this.data = data; this.pos = 0; }
  take(n) {
    if (n < 0 || this.pos + n > this.data.length) throw Error('truncated Hive value');
    const out = this.data.subarray(this.pos, this.pos + n); this.pos += n; return out;
  }
  byte() { return this.take(1)[0]; }
  uint() { return this.take(4).readUInt32LE(); }
  string(n = this.uint()) { return decoder.decode(this.take(n)); }
  key() { const t = this.byte(); if (t === 0) return this.uint(); if (t === 1) return this.string(this.byte()); throw Error('unknown Hive key'); }
  value(depth = 0) {
    if (depth > 50) throw Error('Hive nesting limit');
    const t = this.byte();
    if (t === 0) return null;
    if (t === 1 || t === 2) return this.take(8).readDoubleLE();
    if (t === 3) return this.byte() !== 0;
    if (t === 4) return this.string();
    if (t === 5) return this.take(this.uint());
    if (t >= 6 && t <= 11) {
      const n = this.uint(); if (n > 1000000) throw Error('Hive collection limit');
      const out = t === 11 ? Object.create(null) : [];
      for (let i = 0; i < n; i++) {
        if (t === 11) { const k = this.value(depth + 1); out[String(k)] = this.value(depth + 1); }
        else out.push(t === 6 || t === 7 ? this.take(8).readDoubleLE() : t === 8 ? this.byte() !== 0 : t === 9 ? this.string() : this.value(depth + 1));
      }
      return out;
    }
    throw Error(`unsupported Hive adapter ${t}`);
  }
}
function readHive(data) {
  const entries = new Map(); let pos = 0, frames = 0;
  while (pos < data.length) {
    if (pos + 4 > data.length) throw Error('incomplete Hive frame');
    const n = data.readUInt32LE(pos);
    if (n < 9 || pos + n > data.length) throw Error('incomplete Hive frame');
    const frame = data.subarray(pos, pos + n);
    if (crc32(frame.subarray(0, n - 4)) !== frame.readUInt32LE(n - 4)) throw Error('Hive CRC mismatch; retry snapshot');
    const reader = new Reader(frame.subarray(4, n - 4)), key = reader.key();
    // Latest value wins; deleted records must not be resurrected.
    if (reader.pos === reader.data.length) entries.delete(key);
    else { const value = reader.value(); if (reader.pos !== reader.data.length) throw Error('trailing Hive value'); entries.set(key, value); }
    pos += n; frames++;
  }
  return { entries, frames };
}
module.exports = { readHive, crc32 };
