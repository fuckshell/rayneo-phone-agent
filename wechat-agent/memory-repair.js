"use strict";
const crypto = require("node:crypto");
const FIX_ID = "memory-phase-receipts-20261109-v1";
const CACHE_FIX_ID = "memory-cached-extraction-v1";

// local_id alone is reused by imports; retain timestamp, body and sender.
function memorySourceHash(event = {}) {
  const fields = ["talker", "db", "local_id", "msg_svr_id", "ts", "type", "content", "text", "is_send", "sender"];
  return crypto.createHash("sha256").update(JSON.stringify(fields.map(k => event[k] ?? null))).digest("hex");
}
function memoryRepairPending(job) {
  const repair = job.memory_repair;
  return [FIX_ID, CACHE_FIX_ID].includes(repair?.fix_id) && repair.pending === true && Number(repair.attempts || 0) === 0 &&
    repair.source_hash === memorySourceHash(job.event);
}
function grantMemoryCacheRepair(job, proof) {
  const old = job?.memory_repair;
  if (proof?.fix_id !== CACHE_FIX_ID || proof.job_id !== job?.id || proof.cache_verified !== true ||
      !/^[a-f0-9]{64}$/.test(proof.cache_key || "") || !/^[a-f0-9]{64}$/.test(proof.cache_sha256 || "") ||
      job.memory_status !== "retry" || job.memory_error !== "flash-http-503" ||
      Number(job.memory_attempts || 0) !== proof.expected_attempts ||
      proof.source_hash !== memorySourceHash(job.event) ||
      old?.fix_id !== FIX_ID || old.pending !== false || old.attempts !== 1 || old.outcome !== "retry" ||
      old.error !== job.memory_error || !old.started_at || old.started_at !== proof.previous_started_at ||
      old.source_hash !== proof.source_hash ||
      (job.memory_repair_history || []).some(x => x.fix_id === CACHE_FIX_ID)) return false;
  job.memory_repair_history = [...(job.memory_repair_history || []), structuredClone(old)];
  job.memory_repair = {fix_id: CACHE_FIX_ID, source_hash: proof.source_hash,
    pending: true, attempts: 0, granted_at: new Date().toISOString(),
    cache_key: proof.cache_key, cache_sha256: proof.cache_sha256,
    previous_status: job.memory_status, previous_attempts: job.memory_attempts,
    previous_error: job.memory_error};
  return true;
}
function grantMemoryRepair(job, proof) {
  if (!job || job.memory_repair || proof?.fix_id !== FIX_ID || proof.job_id !== job.id ||
      !job.event?.talker || !job.event.ts || proof.source_hash !== memorySourceHash(job.event) ||
      !["retry", "deferred"].includes(job.memory_status) ||
      proof.expected_status !== job.memory_status ||
      proof.expected_attempts !== Number(job.memory_attempts || 0) ||
      (proof.expected_error ?? null) !== (job.memory_error ?? null)) return false;
  job.memory_repair = {
    fix_id: FIX_ID, source_hash: proof.source_hash, pending: true, attempts: 0,
    granted_at: new Date().toISOString(),
    previous_status: job.memory_status, previous_attempts: Number(job.memory_attempts || 0),
    previous_error: job.memory_error ?? null,
  };
  // Keep the original failure and budget until the actual child returns.
  return true;
}
// Repairs and ordinary attempts share the same paid-attempt accounting.
// A cache-only merge does not submit another generation request.
function memoryLifetimeAttempts(job) {
  const ordinary = Math.max(0, Number(job.memory_attempts || 0) - Number(job.memory_priority_yields || 0));
  const repairs = [...(job.memory_repair_history || []), job.memory_repair].filter(Boolean);
  return ordinary + repairs.reduce((n, repair) => n +
    (repair.fix_id === CACHE_FIX_ID ? 0 : Math.max(0, Number(repair.attempts || 0))), 0);
}
function successfulMemoryReceipt(job, sourceHash) {
  return job.memory_status === "succeeded" && job.memory_receipt?.source_hash === sourceHash &&
    job.memory_receipt?.result?.ok === true && job.memory_receipt?.completed_at;
}
module.exports = {FIX_ID, CACHE_FIX_ID, memorySourceHash, memoryRepairPending, grantMemoryRepair,
  grantMemoryCacheRepair, successfulMemoryReceipt, memoryLifetimeAttempts};
