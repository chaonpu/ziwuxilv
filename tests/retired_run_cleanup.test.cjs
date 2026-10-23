'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const clean = require('../tools/retired_run_cleanup.cjs');
const now = Date.parse('2026-10-24T02:00:00+08:00');
function fixture(change={}) {
  const root = 'chaonpu/Runner';
  const context = {repo:{owner:'chaonpu',repo:'Runner'},actor:'chaonpu',eventName:'push',ref:'refs/heads/main'};
  const row = {repo:root,id:123,path:'.github/workflows/retired.yml',createdAt:'2026-10-17T00:00:00Z',...change};
  const plan = {schemaVersion:1,operation:'delete-approved-retired-run-history',root,createdAt:new Date(now-1000).toISOString(),expiresAt:new Date(now+1000).toISOString(),runs:[row]};
  const live = {id:row.id,path:row.path,created_at:row.createdAt,repository:{full_name:root},status:'completed',conclusion:'success'};
  const calls=[]; let deleted=false;
  const github={rest:{actions:{
    listWorkflowRunsForRepo:async()=>{calls.push('LIST');return {data:{total_count:1,workflow_runs:[live]}};},
    getWorkflowRun:async()=>{calls.push('GET');if(deleted)throw Object.assign(new Error('missing'),{status:404});return {data:live};},
    deleteWorkflowRun:async()=>{calls.push('DELETE');deleted=true;},
    cancelWorkflowRun:async()=>{calls.push('CANCEL');live.status='completed';live.conclusion='cancelled';},
  }}};
  return {context,plan,github,target:root,now,pause:async()=>{},calls,live,row};
}
test('deletes only the approved completed run and verifies absence',async()=>{
  const f=fixture();const r=await clean(f);assert.equal(r.deleted,1);assert.deepEqual(f.calls,['LIST','GET','DELETE','GET']);
});
test('current workflows cannot be authorized by a manifest',async()=>{
  const f=fixture({path:'.github/workflows/android-production.yml'});await assert.rejects(clean(f),/protected/);assert.deepEqual(f.calls,[]);
});
test('all identities are checked before any API call',async()=>{
  const f=fixture();f.plan.runs.push({...f.row});await assert.rejects(clean(f),/duplicate/);assert.deepEqual(f.calls,[]);
});
test('expired plans do not delete anything',async()=>{
  const f=fixture();f.now+=2000;await assert.rejects(clean(f),/expired/);assert.deepEqual(f.calls,[]);
});
test('running and queued records are preserved',async()=>{
  for(const status of ['queued','in_progress','waiting']) {const f=fixture();f.live.status=status;assert.equal((await clean(f)).skipped,1);assert.deepEqual(f.calls,['LIST','GET']);}
});
test('mismatched path, date or repository stops deletion',async()=>{
  for(const change of [{path:'.github/workflows/new.yml'},{created_at:'2026-10-18T00:00:00Z'},{repository:{full_name:'foreign/repo'}}]) {const f=fixture();Object.assign(f.live,change);await assert.rejects(clean(f),/identity/);assert.deepEqual(f.calls,['LIST','GET']);}
});
test('foreign repositories and actors cannot execute cleanup',async()=>{
  for(const actor of ['someone','github-actions[bot]']) {const f=fixture();f.context.actor=actor;await assert.rejects(clean(f),/context/);assert.deepEqual(f.calls,[]);}
  const f=fixture({repo:'ZIWUXILV/database'});await assert.rejects(clean(f),/identity/);assert.deepEqual(f.calls,[]);
});
test('a read failure never authorizes deletion',async()=>{
  const f=fixture();f.github.rest.actions.getWorkflowRun=async()=>{throw Object.assign(new Error('forbidden'),{status:403});};await assert.rejects(clean(f),/forbidden/);assert.deepEqual(f.calls,['LIST']);
});
test('missing repository visibility cannot be counted as absent run records',async()=>{
  const f=fixture();f.github.rest.actions.listWorkflowRunsForRepo=async()=>{throw Object.assign(new Error('private repository unavailable'),{status:404});};await assert.rejects(clean(f),/unavailable/);assert.deepEqual(f.calls,[]);
});
test('only the exact old orphan queue can be cancelled',async()=>{
  const f=fixture({id:999,cancelStaleQueued:true});await assert.rejects(clean(f),/queue/);assert.deepEqual(f.calls,[]);
});
test('approved old queue is cancelled before its record is deleted',async()=>{
  const f=fixture({repo:'chaonpu/ziwuxilv',id:37642338428,path:'.github/workflows/access-state.yml',createdAt:'2026-10-07T15:06:54Z',cancelStaleQueued:true});
  f.context.repo.repo='ziwuxilv';f.plan.root=f.target='chaonpu/ziwuxilv';f.live.repository.full_name=f.target;f.live.status='queued';
  const r=await clean(f);assert.equal(r.cancelled,1);assert.equal(r.deleted,1);assert.deepEqual(f.calls,['LIST','GET','CANCEL','GET','DELETE','GET']);
});
test('a stale-queue candidate that has started running is preserved',async()=>{
  const f=fixture({repo:'chaonpu/ziwuxilv',id:37642338428,path:'.github/workflows/access-state.yml',createdAt:'2026-10-07T15:06:54Z',cancelStaleQueued:true});
  f.context.repo.repo='ziwuxilv';f.plan.root=f.target='chaonpu/ziwuxilv';f.live.repository.full_name=f.target;f.live.status='in_progress';
  const r=await clean(f);assert.equal(r.skipped,1);assert.deepEqual(f.calls,['LIST','GET']);
});
function orphan() {
  const f=fixture({repo:'chaonpu/ziwuxilv',id:37642338428,path:'.github/workflows/access-state.yml',createdAt:'2026-10-07T15:06:54Z',cancelStaleQueued:true});
  f.context.repo.repo='ziwuxilv';f.plan.root=f.target='chaonpu/ziwuxilv';f.live.repository.full_name=f.target;f.live.status='queued';
  f.github.rest.actions.cancelWorkflowRun=async()=>{f.calls.push('CANCEL');throw Object.assign(new Error('not queued yet'),{status:409});};
  f.github.rest.actions.listJobsForWorkflowRun=async()=>{f.calls.push('JOBS');return {data:{total_count:0,jobs:[]}};};
  return f;
}
test('older-than-two-week orphan with zero jobs can be deleted after cancel409',async()=>{
  const f=orphan();const r=await clean(f);assert.equal(r.deleted,1);assert.equal(r.cancelled,0);assert.deepEqual(f.calls,['LIST','GET','CANCEL','GET','JOBS','GET','DELETE','GET']);
});
test('an orphan candidate with an actual job is preserved',async()=>{
  const f=orphan();f.github.rest.actions.listJobsForWorkflowRun=async()=>({data:{total_count:1,jobs:[{status:'queued'}]}});
  assert.equal((await clean(f)).skipped,1);assert(!f.calls.includes('DELETE'));
});
test('cancel409 cannot bypass the two-week age boundary',async()=>{
  const f=orphan();f.row.createdAt=f.live.created_at='2026-10-14T00:00:00Z';
  assert.equal((await clean(f)).skipped,1);assert(!f.calls.includes('DELETE'));assert(!f.calls.includes('JOBS'));
});
