'use strict';
const assert = require('node:assert/strict');

const KEEP = {
  'chaonpu/Runner': ['android-production.yml','android-publish.yml','calendar-health.yml','private-history-cleanup.yml','production-dispatch.yml','stock-us.yml'],
  'chaonpu/Stock': ['run-a-share.yml','run-us.yml','production-readiness-a.yml','production-readiness-us.yml','production-readiness-cloud-a.yml','production-readiness-cloud-us.yml'],
  'chaonpu/ziwuxilv': ['access-state.yml','actions-storage-cleanup.yml','android-publication-guard-tests.yml','calendar-sync.yml','calendar-watchdog.yml','community-media-tests.yml','community-media-upload.yml','discussion-retention.yml','image-lifecycle.yml','profile-tests.yml','profile-update.yml','publish-android-release.yml','repository-maintenance.yml'],
};

module.exports = async function clean({github, context, plan, target, now = Date.now(), pause = ms => new Promise(r => setTimeout(r, ms))}) {
  const root = context.repo.owner + '/' + context.repo.repo;
  const allowed = root === 'chaonpu/Runner' ? ['chaonpu/Runner','chaonpu/Stock'] : ['chaonpu/ziwuxilv'];
  assert(['chaonpu/Runner','chaonpu/ziwuxilv'].includes(root), 'untrusted repository');
  assert(context.actor === 'chaonpu' && context.eventName === 'push' && context.ref === 'refs/heads/main', 'untrusted context');
  assert(plan.schemaVersion === 1 && plan.operation === 'delete-approved-retired-run-history' && plan.root === root, 'invalid plan scope');
  const created = Date.parse(plan.createdAt), expires = Date.parse(plan.expiresAt);
  assert(Number.isFinite(created) && Number.isFinite(expires) && created <= now + 300000 && now < expires && expires - created <= 86400000, 'expired plan');
  assert(allowed.includes(target) && Array.isArray(plan.runs) && plan.runs.length <= 500, 'invalid target');
  const seen = new Set();
  for (const r of plan.runs) {
    assert(allowed.includes(r.repo) && Number.isSafeInteger(r.id) && r.id > 0 && /^\.github\/workflows\/[A-Za-z0-9_.-]+\.ya?ml$/.test(r.path), 'invalid run identity');
    const key = r.repo + ':' + r.id;
    assert(!seen.has(key), 'duplicate run identity'); seen.add(key);
    assert(Number.isFinite(Date.parse(r.createdAt)) && Date.parse(r.createdAt) <= created, 'invalid run date');
    const stale = r.cancelStaleQueued === true;
    if (stale) assert(root === 'chaonpu/ziwuxilv' && r.repo === root && r.id === 37642338428 && r.path === '.github/workflows/access-state.yml' && created - Date.parse(r.createdAt) >= 7 * 86400000, 'stale queue not approved');
    else assert(!KEEP[r.repo].includes(r.path.split('/').pop()), 'current workflow protected');
  }
  const [owner, repo] = target.split('/');
  // A private-repository 404 can mean missing permission. Prove Actions listing
  // access first so an inaccessible repository is never reported as already clean.
  const inventory = (await github.rest.actions.listWorkflowRunsForRepo({owner, repo, per_page:1})).data;
  assert(Number.isSafeInteger(inventory.total_count) && Array.isArray(inventory.workflow_runs), 'repository visibility unverified');
  const result = {repository:target, approved:0, deleted:0, alreadyAbsent:0, skipped:0, cancelled:0};
  async function read(id) {
    try { return (await github.rest.actions.getWorkflowRun({owner, repo, run_id:id})).data; }
    catch (e) { if (e.status === 404) return null; throw e; }
  }
  function matches(live, r) {
    return live && live.id === r.id && live.path === r.path && live.repository?.full_name === target && live.created_at === r.createdAt;
  }
  for (const r of plan.runs.filter(r => r.repo === target)) {
    result.approved++;
    let live = await read(r.id);
    if (!live) { result.alreadyAbsent++; continue; }
    assert(matches(live, r), 'run identity changed');
    if (r.cancelStaleQueued) {
      if (live.status !== 'queued') { result.skipped++; continue; }
      await github.rest.actions.cancelWorkflowRun({owner, repo, run_id:r.id});
      result.cancelled++;
      for (let n = 0; n < 20; n++) {
        await pause(1000); live = await read(r.id);
        if (!live || live.status === 'completed') break;
      }
      if (!live) { result.alreadyAbsent++; continue; }
      assert(matches(live, r), 'cancelled run identity changed');
      if (live.status !== 'completed' || live.conclusion !== 'cancelled') { result.skipped++; continue; }
    }
    if (live.status !== 'completed') { result.skipped++; continue; }
    await github.rest.actions.deleteWorkflowRun({owner, repo, run_id:r.id});
    assert(await read(r.id) === null, 'deletion not verified');
    result.deleted++;
  }
  console.log(JSON.stringify(result));
  return result;
};
