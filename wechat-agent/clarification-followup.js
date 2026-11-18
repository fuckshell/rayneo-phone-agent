const fs = require('node:fs');
const crypto = require('node:crypto');
const assert = require('node:assert/strict');

function eligible(job) {
  return job?.status === 'retry' && job.last_error === 'online-clarification-suppressed' &&
    Number(job.attempts) > 0 && Number(job.attempts) < 2 &&
    !job.repair_execution && !job.recovery_execution && !job.clarification_followup;
}

function evidenceRows(job, proof = job.clarification_followup) {
  assert.equal(proof?.ok, true);
  assert.equal(proof.job_id, job.id);
  assert.deepEqual(proof.original_event, job.event);
  assert.ok(proof.rows.length > 0 && proof.rows.length <= 12);
  assert.equal(proof.rows.length, proof.line_proofs.length);
  const lines = fs.readFileSync(proof.source, 'utf8').split(/\r?\n/);
  return proof.line_proofs.map((p, i) => {
    const raw = lines[p.line - 1];
    assert.equal(crypto.createHash('sha256').update(raw).digest('hex'), p.sha256);
    const row = JSON.parse(raw);
    assert.deepEqual(row, proof.rows[i]);
    assert.ok(row.ts > job.event.ts && row.ts <= job.event.ts + 86400000);
    return row;
  });
}

function resume(job, proof) {
  assert.ok(eligible(job));
  assert.equal(proof.attempts, job.attempts);
  assert.equal(proof.last_error, job.last_error);
  assert.deepEqual(proof.original_result, job.result);
  evidenceRows(job, proof);
  job.clarification_followup = {...proof, resumed_at: new Date().toISOString()};
  // Use the remaining ordinary attempt, never reset or create a repair budget.
  job.status = 'queued';
  job.retry_exhausted = false;
  job.next_attempt_at_ms = 0;
  job.updated_at = job.clarification_followup.resumed_at;
  delete job.completed_at;
}

module.exports = {eligible, evidenceRows, resume};
