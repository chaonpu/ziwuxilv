"""Trusted-main image registry, reference reconciliation and authorized content deletion.

Git deletion removes objects from the current tree, not from historical commits/CDN.
Every destructive run requires a complete live Discussions and avatar inventory.
"""
from __future__ import annotations
import copy, hashlib, json, os, re, sys, urllib.request, subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from profile_registry import api, shell, load_json, write_json, ADMIN_ID, REPOSITORY

REGISTRY = 'media/images.json'
REQUEST = 'ZIWUXILV_IMAGE_LIFECYCLE_V1\n'
SUBMIT = 'ZIWUXILV_IMAGE_LIFECYCLE_SUBMIT_V1\n'
RESULT = 'ZIWUXILV_IMAGE_LIFECYCLE_RESULT_V1\n'
MARKER = re.compile(r'<!--\s*ZIWUXILV_IMAGES_V1:(\[[^\n]*?\])\s*-->')
IMAGE_ID = re.compile(r'img_[a-f0-9]{64}')
MANAGED_PATH = re.compile(r'(?:media/[0-9]+/[a-f0-9]{32}/[0-2]\.webp|profiles/[0-9]+/avatar\.webp)')
URL = re.compile(r'https://raw\.githubusercontent\.com/chaonpu/ziwuxilv/(?:main|[a-f0-9]{40})/((?:media|profiles)/[^\s)<>?]+)')

class LifecycleError(Exception): pass

def now(): return datetime.now(timezone.utc)
def image_id(path, digest): return 'img_' + hashlib.sha256((path + ':' + digest).encode()).hexdigest()
def registry(): return load_json(REGISTRY, {'version': 1, 'images': {}, 'deleted_contents': {}})

def register(value, path, owner, digest, created_at=None):
    if not MANAGED_PATH.fullmatch(path) or int(path.split('/')[1]) != int(owner):
        raise LifecycleError('Invalid managed image path')
    if not re.fullmatch('[a-f0-9]{64}', digest): raise LifecycleError('Invalid image digest')
    iid = image_id(path, digest)
    value.setdefault('images', {}).setdefault(iid, dict(url=f'https://raw.githubusercontent.com/{REPOSITORY}/main/{path}',
        storage_path=path, owner_id=str(owner), sha256=digest, created_at=created_at or now().isoformat(), references=[]))
    return iid

def graphql(query, variables=None):
    req = urllib.request.Request('https://api.github.com/graphql',
        data=json.dumps(dict(query=query, variables=variables or {})).encode(), method='POST', headers={
        'Authorization': 'Bearer ' + os.environ['GH_TOKEN'], 'Content-Type': 'application/json',
        'User-Agent': 'ZiWuXiLv-Image-Lifecycle'})
    with urllib.request.urlopen(req, timeout=40) as response: result = json.load(response)
    if result.get('errors') or not isinstance(result.get('data'), dict):
        raise LifecycleError('Discussions inventory or mutation failed')
    return result['data']

def pages(fetch):
    cursor, seen = None, set()
    while True:
        connection = fetch(cursor)
        if not isinstance(connection, dict) or not isinstance(connection.get('nodes'), list) or 'pageInfo' not in connection:
            raise LifecycleError('Incomplete reference inventory')
        for node in connection['nodes']:
            if not isinstance(node, dict): raise LifecycleError('Invalid inventory node')
            yield node
        info = connection['pageInfo']
        if not info.get('hasNextPage'): break
        cursor = info.get('endCursor')
        if not cursor or cursor in seen: raise LifecycleError('Truncated reference inventory')
        seen.add(cursor)

FIELDS = 'id body author { login ... on User { databaseId } }'
POST_FIELDS = FIELDS + ' number createdAt category { name }'
COMMENT_FIELDS = FIELDS + ' deletedAt'
PAGE = 'pageInfo { hasNextPage endCursor }'

def inventory():
    repo = graphql('query { repository(owner:"chaonpu",name:"ziwuxilv") { id } }')['repository']
    if not repo: raise LifecycleError('Missing repository')
    posts = pages(lambda cursor: graphql('query($c:String) { repository(owner:"chaonpu",name:"ziwuxilv") {'
        ' discussions(first:100,after:$c) { nodes { ' + POST_FIELDS + ' } ' + PAGE + ' } } }', {'c':cursor})['repository']['discussions'])
    records = []
    for post in posts:
        post.update(type='post', post_id=post['id'], repository_id=repo['id']); records.append(post)
        comments = pages(lambda cursor: graphql('query($id:ID!,$c:String) { node(id:$id) { ... on Discussion {'
            ' comments(first:100,after:$c) { nodes { ' + COMMENT_FIELDS + ' } ' + PAGE + ' } } } }',
            {'id':post['id'], 'c':cursor})['node']['comments'])
        for comment in comments:
            comment.update(type='comment', post_id=post['id'], repository_id=repo['id']); records.append(comment)
            replies = pages(lambda cursor: graphql('query($id:ID!,$c:String) { node(id:$id) { ... on DiscussionComment {'
                ' replies(first:100,after:$c) { nodes { ' + COMMENT_FIELDS + ' } ' + PAGE + ' } } } }',
                {'id':comment['id'], 'c':cursor})['node']['replies'])
            for reply in replies:
                reply.update(type='reply', post_id=post['id'], parent_id=comment['id'], repository_id=repo['id']); records.append(reply)
    # Avatar registry is authoritative and lives in the same serialized Git write domain.
    profiles = load_json('profiles/index.json')
    if not isinstance(profiles, dict) or not isinstance(profiles.get('profiles'), dict): raise LifecycleError('Incomplete avatar inventory')
    for owner, profile in profiles['profiles'].items():
        if profile.get('avatar_path'):
            records.append(dict(type='avatar', id=str(owner), body='', owner_id=str(owner),
                path=profile['avatar_path'], digest=profile['avatar_sha256']))
    return records

def references(body):
    ids = set()
    for marker in MARKER.findall(body or ''):
        try: values = json.loads(marker)
        except ValueError as error: raise LifecycleError('Invalid image references') from error
        if not isinstance(values, list) or any(not isinstance(x,str) or not IMAGE_ID.fullmatch(x) for x in values):
            raise LifecycleError('Invalid image references')
        ids.update(values)
    return ids

def reconcile(value, records, timestamp=None):
    value = copy.deepcopy(value)
    images = value.setdefault('images', {})
    for entry in images.values(): entry['references'] = []
    for content in records:
        ids = references(content.get('body', ''))
        if content['type'] == 'avatar':
            ids.add(register(value, content['path'], content['owner_id'], content['digest'], timestamp))
        for path in URL.findall(content.get('body','')):
            if not MANAGED_PATH.fullmatch(path): continue  # External URLs are never deleted.
            target = Path(path)
            if not target.is_file(): continue
            ids.add(register(value, path, path.split('/')[1], hashlib.sha256(target.read_bytes()).hexdigest(), timestamp))
        for iid in ids:
            if iid not in images: raise LifecycleError('Referenced image missing from registry')
            ref = dict(type=content['type'], id=content['id'])
            if ref not in images[iid]['references']: images[iid]['references'].append(ref)
    value['last_complete_scan_at'] = timestamp or now().isoformat()
    return value

def collect(value, timestamp=None, immediate=None):
    """New uploads get a lease; ordinary orphans must survive two scans and 48 hours."""
    timestamp = timestamp or now()
    immediate = set(immediate or [])
    value = copy.deepcopy(value); removed = []
    for iid, entry in list(value['images'].items()):
        if entry.get('references'):
            entry.pop('orphan_since', None); continue
        created = datetime.fromisoformat(entry['created_at'].replace('Z','+00:00'))
        orphan = entry.get('orphan_since')
        if iid not in immediate and (timestamp - created < timedelta(hours=48) or not orphan):
            entry.setdefault('orphan_since', timestamp.isoformat()); continue
        if iid not in immediate and timestamp - datetime.fromisoformat(orphan.replace('Z','+00:00')) < timedelta(hours=48): continue
        path = entry['storage_path']
        if not MANAGED_PATH.fullmatch(path): raise LifecycleError('Unsafe storage path')
        removed.append((iid,path)); del value['images'][iid]
    # A replaced avatar may have an older ID for the same path. Never unlink its current blob.
    surviving_paths = {x['storage_path'] for x in value['images'].values()}
    return value, [path for _,path in removed if path not in surviving_paths]

def authorized(actor, content):
    if actor == ADMIN_ID: return True
    return content['type'] in ('comment','reply') and (content.get('author') or {}).get('databaseId') == actor

def verified_request(event, issue, seal):
    actor = issue.get('user', {})
    owner = actor.get('id'); body = issue.get('body','')
    if (type(owner) is not int or owner <= 0 or actor.get('type') != 'User' or issue.get('pull_request')
        or event.get('repository',{}).get('full_name') != REPOSITORY
        or event.get('issue',{}).get('number') != issue.get('number')
        or event.get('sender',{}).get('id') != owner
        or event.get('comment',{}).get('user',{}).get('id') != owner
        or seal.get('user',{}).get('id') != owner or seal.get('id') != event.get('comment',{}).get('id')
        or not body.startswith(REQUEST) or not seal.get('body','').startswith(SUBMIT)):
        raise LifecycleError('LIFECYCLE_AUTH_FAILED')
    request = json.loads(body[len(REQUEST):]); final = json.loads(seal['body'][len(SUBMIT):])
    if (request.get('version') != 1 or not re.fullmatch('[a-f0-9]{32}', request.get('request_id',''))
        or final.get('request_id') != request['request_id']
        or final.get('body_sha256') != hashlib.sha256(body.encode()).hexdigest()
        or request.get('action') not in ('attach','delete')
        or not isinstance(request.get('content_id'),str) or not 1 <= len(request['content_id']) <= 200):
        raise LifecycleError('LIFECYCLE_AUTH_FAILED')
    return owner, request, hashlib.sha256(body.encode()).hexdigest()

def commit(value, removals=(), receipt_path=None, receipt=None):
    for path in set(removals):
        if not MANAGED_PATH.fullmatch(path): raise LifecycleError('Unsafe deletion path')
        Path(path).unlink(missing_ok=True)
    write_json(REGISTRY, value)
    if receipt_path: write_json(receipt_path, receipt)
    shell('git', 'add', '--', 'media', 'profiles')
    if not shell('git','diff','--cached','--name-only'): return
    shell('git','-c','user.name=github-actions[bot]', '-c','user.email=41898282+github-actions[bot]@users.noreply.github.com',
        'commit','-m','Reconcile shared image lifecycle')
    shell('git','push','origin','HEAD:main')

def process(event):
    number = int(event['issue']['number']); cid = int(event['comment']['id'])
    issue = api(f'/repos/{REPOSITORY}/issues/{number}'); seal = api(f'/repos/{REPOSITORY}/issues/comments/{cid}')
    owner, request, body_hash = verified_request(event, issue, seal)
    rid = request['request_id']; target_id = request['content_id']
    receipt_path = f'media/lifecycle/{owner}/{rid}.json'
    for attempt in range(4):
        shell('git','fetch','origin','main'); shell('git','reset','--hard','origin/main')
        receipt = load_json(receipt_path)
        if receipt and (receipt.get('github_id') != owner or receipt.get('body_sha256') != body_hash): raise LifecycleError('LIFECYCLE_AUTH_FAILED')
        if receipt and receipt.get('status') == 'success': break
        records = inventory()  # Abort without deleting anything if even one page fails.
        target = next((x for x in records if x['id'] == target_id and x['type'] != 'avatar'), None)
        value = reconcile(registry(), records)
        if request['action'] == 'attach':
            if not target or (target.get('author') or {}).get('databaseId') != owner: raise LifecycleError('LIFECYCLE_AUTH_FAILED')
            # Content is read from GitHub; submitted image IDs cannot detach/delete another user's reference.
            receipt = dict(version=1, request_id=rid, github_id=owner, body_sha256=body_hash, status='success')
            removals = []
        else:
            if not receipt:
                if not target or target.get('deletedAt') or not authorized(owner,target): raise LifecycleError('LIFECYCLE_AUTH_FAILED')
                affected = {x['id'] for x in records if x['id']==target_id or
                    (target['type']=='post' and x.get('post_id')==target_id)}
                ids = [iid for iid,entry in value['images'].items() if any(r['id'] in affected for r in entry['references'])]
                receipt = dict(version=1,request_id=rid,github_id=owner,body_sha256=body_hash,status='pending',
                    content_id=target_id,content_type=target['type'],affected=sorted(affected),image_ids=ids)
                commit(value, receipt_path=receipt_path, receipt=receipt)  # Durable authorization before remote mutation.
            # GitHub wipes a parent comment with replies, preserving its descendants. A
            # durable intent can resume after that wipe even though its author is now null.
            if target and not target.get('deletedAt'):
                if not authorized(owner,target): raise LifecycleError('LIFECYCLE_AUTH_FAILED')
                kind = 'deleteDiscussion' if target['type']=='post' else 'deleteDiscussionComment'
                field = 'discussion' if target['type']=='post' else 'comment'
                result = graphql('mutation($id:ID!) { '+kind+'(input:{id:$id}) { '+field+' { id } } }',{'id':target_id})
                if result.get(kind,{}).get(field,{}).get('id') != target_id: raise LifecycleError('Delete not confirmed')
            # A second complete inventory protects shared refs, including all descendants and avatars.
            value = reconcile(value, inventory())
            if any(r['id']==target_id for e in value['images'].values() for r in e['references']): raise LifecycleError('Content still referenced')
            value.setdefault('deleted_contents',{}).update({x: now().isoformat() for x in receipt['affected']})
            value, removals = collect(value, immediate=receipt['image_ids'])
            receipt.update(status='success')
        try:
            commit(value, removals, receipt_path, receipt); break
        except subprocess.CalledProcessError:
            if attempt == 3: raise
    api(f'/repos/{REPOSITORY}/issues/{number}/comments','POST',{'body':RESULT+json.dumps(receipt,ensure_ascii=False)})
    api(f'/repos/{REPOSITORY}/issues/{number}','PATCH',{'state':'closed'})

def garbage_collect():
    for attempt in range(4):
        shell('git','fetch','origin','main'); shell('git','reset','--hard','origin/main')
        value = reconcile(registry(), inventory())
        # Adopt orphan legacy files as well, then quarantine them instead of deleting immediately.
        for pattern in ('media/*/*/*.webp','profiles/*/avatar.webp'):
            for target in Path('.').glob(pattern):
                path = target.as_posix()
                if MANAGED_PATH.fullmatch(path): register(value,path,path.split('/')[1],hashlib.sha256(target.read_bytes()).hexdigest())
        value, removals = collect(value)
        try: commit(value,removals); break
        except subprocess.CalledProcessError:
            if attempt == 3: raise

def report_failure(event):
    number = int(event['issue']['number']); cid = int(event['comment']['id'])
    issue = api(f'/repos/{REPOSITORY}/issues/{number}')
    seal = api(f'/repos/{REPOSITORY}/issues/comments/{cid}')
    owner, request, body_hash = verified_request(event, issue, seal)
    shell('git','fetch','origin','main'); shell('git','reset','--hard','origin/main')
    receipt = load_json(f"media/lifecycle/{owner}/{request['request_id']}.json")
    if receipt and (receipt.get('github_id') != owner or receipt.get('body_sha256') != body_hash):
        raise LifecycleError('LIFECYCLE_AUTH_FAILED')
    result = receipt if receipt and receipt.get('status') == 'success' else dict(
        version=1, request_id=request['request_id'], github_id=owner, body_sha256=body_hash,
        status='retry', error='LIFECYCLE_RETRY')
    api(f'/repos/{REPOSITORY}/issues/{number}/comments','POST',{'body':RESULT+json.dumps(result,ensure_ascii=False)})

if __name__ == '__main__':
    if '--gc' in sys.argv: garbage_collect()
    else:
        event = json.loads(Path(os.environ['GITHUB_EVENT_PATH']).read_text(encoding='utf-8'))
        if event.get('comment',{}).get('body','').startswith(SUBMIT):
            if '--report-failure' in sys.argv: report_failure(event)
            else: process(event)
