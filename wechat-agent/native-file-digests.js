// In-process cache of actual native bytes, invalidated by inode and nanosecond
// timestamps. No filename/metadata guess can replace hashing the first read.
const fs = require('fs');
const crypto = require('crypto');
const cache = new Map();
const MAX_ENTRIES = 512;
function signature(stat) {
  return [stat.dev, stat.ino, stat.size, stat.mtimeNs, stat.ctimeNs].join(':');
}
async function nativeFileDigests(source) {
  const initial = fs.statSync(source, {bigint:true});
  if (!initial.isFile() || initial.size <= 0n) return null;
  const version = signature(initial);
  const old = cache.get(source);
  if (old?.version === version) return old.promise;
  const entry = {version};
  entry.promise = (async () => {
    const md5 = crypto.createHash('md5'), sha = crypto.createHash('sha256');
    for await (const block of fs.createReadStream(source)) {
      md5.update(block); sha.update(block);
    }
    const final = fs.statSync(source, {bigint:true});
    if (signature(final) !== version) return null;
    return {source, md5:md5.digest('hex'), sha:sha.digest('hex'), size:Number(final.size)};
  })();
  cache.set(source, entry);
  while (cache.size > MAX_ENTRIES) cache.delete(cache.keys().next().value);
  try {
    const result = await entry.promise;
    if (!result && cache.get(source) === entry) cache.delete(source);
    return result;
  } catch (error) {
    if (cache.get(source) === entry) cache.delete(source);
    throw error;
  }
}
module.exports = {nativeFileDigests};
