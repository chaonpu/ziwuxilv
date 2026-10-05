"""Repository-scoped Actions cleanup; never deletes Git history, caches or Releases."""
import argparse
import base64
import json
import os
import re
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

REFERENCE_PATHS = ['release/latest-handoff.json', 'release/latest-signed.json', 'release/latest-run.json',
                   'release/latest-publish-run.json', 'signing-request.json', 'android-publish-request.json',
                   'release/maintenance-protection.json']

def instant(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00'))

def references(values):
    artifacts, runs, acknowledged = set(), set(), set()
    def walk(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if key in ('artifactId', 'sourceArtifactId') and type(item) is int: artifacts.add(item)
                if key in ('runId', 'testRunId', 'signingRunId') and type(item) is int: runs.add(item)
                if key == 'protectedArtifactIds': artifacts.update(int(x) for x in item)
                if key == 'protectedRunIds': runs.update(int(x) for x in item)
                if key == 'acknowledgedArtifactIds': acknowledged.update(int(x) for x in item)
                walk(item)
        elif isinstance(value, list):
            for item in value: walk(item)
    for value in values: walk(value)
    return artifacts, runs, acknowledged

def family(name):
    if name.startswith('ziwu-xilv-v') and name.endswith('-signed'): return 'ziwu-xilv-signed-'
    for prefix in ('test-verified-', 'release-source-', 'ziwu-xilv-signed-'):
        if name.startswith(prefix): return prefix
    if name == 'ziwu-xilv-preview': return 'preview'
    return 'other'

def plan(artifacts, runs, workflow_paths, values, now):
    pinned_artifacts, pinned_runs, acknowledged = references(values)
    run_by_id = {r['id']: r for r in runs}
    protected, candidates, keep = [], [], {}
    for artifact in sorted(artifacts, key=lambda x:x['created_at'], reverse=True):
        artifact_id = artifact['id']; run_id = artifact.get('workflow_run', {}).get('id')
        group = family(artifact['name'])
        run = run_by_id.get(run_id)
        release = group in ('test-verified-', 'release-source-', 'ziwu-xilv-signed-')
        # Unknown cross-repo acknowledgements are not evidence of a completed handoff.
        if (artifact_id in pinned_artifacts or run_id in pinned_runs or run is None
            or run['status'] != 'completed' or (release and artifact_id not in acknowledged)):
            protected.append({'kind':'artifact','id':artifact_id,'reason':'reference, unfinished run or unacknowledged release handoff'})
            continue
        keep[group] = keep.get(group, 0) + 1
        days = 7 if group != 'other' else (3 if 'smoke' in artifact['name'] or 'results' in artifact['name'] else 14)
        aged = now - instant(artifact['created_at']) >= timedelta(days=days)
        excess = group != 'other' and keep[group] > 3
        if aged or excess: candidates.append({'kind':'artifact','id':artifact_id,'reason':'retention or latest-three limit'})
    deleting = {x['id'] for x in candidates}
    for run in runs:
        path = run.get('path', '').split('@', 1)[0]
        if run.get('status') != 'completed' or not path or path in workflow_paths: continue
        active_artifacts = [a for a in artifacts if a.get('workflow_run', {}).get('id') == run['id']
                            and not a.get('expired') and a['id'] not in deleting]
        if run['id'] in pinned_runs or active_artifacts:
            protected.append({'kind':'run','id':run['id'],'reason':'referenced run or retained artifact'})
        elif now - instant(run['updated_at']) >= timedelta(days=30):
            candidates.append({'kind':'run','id':run['id'],'reason':'removed workflow and 30-day grace'})
    return {'schemaVersion':1,'candidates':candidates,'protected':protected,
            'note':'Custom protection does not prevent GitHub artifact expiry. Unacknowledged handoffs are retained.'}

def api(repo, path, method='GET'):
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repo): raise ValueError('Invalid repository')
    token = os.environ['GH_TOKEN']
    request = urllib.request.Request('https://api.github.com/repos/'+repo+'/'+path, method=method,
        headers={'Authorization':'Bearer '+token,'Accept':'application/vnd.github+json','X-GitHub-Api-Version':'2022-11-28'})
    with urllib.request.urlopen(request, timeout=30) as response:
        data = response.read()
        return json.loads(data) if data else None

def pages(repo,path,key):
    values=[]
    for page in range(1,10001):
        data=api(repo,path+('&' if '?' in path else '?')+f'per_page=100&page={page}')
        batch=data[key] if key else data;values.extend(batch)
        if len(batch)<100:return values
    raise ValueError('Pagination limit exceeded; no deletions performed')

def main():
    p=argparse.ArgumentParser();p.add_argument('--apply',action='store_true');p.add_argument('--output',default='maintenance-result.json');args=p.parse_args()
    repo=os.environ['GITHUB_REPOSITORY'];values=[]
    for path in REFERENCE_PATHS:
        try: value=api(repo,'contents/'+path+'?ref=main')
        except urllib.error.HTTPError as error:
            if error.code==404: continue
            raise
        values.append(json.loads(base64.b64decode(value['content'])))
    artifacts=pages(repo,'actions/artifacts','artifacts');runs=pages(repo,'actions/runs','workflow_runs')
    # Use the current Git tree, not the workflow API which also lists obsolete definitions.
    tree=api(repo,'git/trees/main?recursive=1')
    if tree.get('truncated'):raise ValueError('Incomplete workflow inventory')
    paths={x['path'] for x in tree['tree'] if x['path'].startswith('.github/workflows/') and x['path'].endswith(('.yml','.yaml'))}
    result=plan(artifacts,runs,paths,values,datetime.now(timezone.utc));result['dryRun']=not args.apply;result['repository']=repo
    result['deleted']=[]
    try:
        if args.apply:
            for item in result['candidates']:
                endpoint='actions/artifacts/' if item['kind']=='artifact' else 'actions/runs/'
                api(repo,endpoint+str(item['id']),'DELETE');result['deleted'].append(item)
    finally:
        from pathlib import Path
        Path(args.output).write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(result,indent=2))

if __name__=='__main__':main()
