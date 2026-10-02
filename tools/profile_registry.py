"""Trusted-main Profile Registry writer. Issue bodies are data, never code or identity."""
from __future__ import annotations
import base64, copy, hashlib, io, json, os, re, subprocess, sys, unicodedata, urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from PIL import Image, UnidentifiedImageError
from release_assets import storage_backend, monthly_tag, upload_bytes

ADMIN_ID = 205125766
REPOSITORY = 'chaonpu/ziwuxilv'
REQUEST_PREFIX = 'ZIWUXILV_PROFILE_REQUEST_V1\n'
CHUNK_PREFIX = 'ZIWUXILV_PROFILE_AVATAR_V1\n'
SUBMIT_PREFIX = 'ZIWUXILV_PROFILE_SUBMIT_V1\n'
RESULT_PREFIX = 'ZIWUXILV_PROFILE_RESULT_V1\n'
MAX_AVATAR = 100 * 1024
SHANGHAI = timezone(timedelta(hours=8))

class ProfileError(Exception):
    def __init__(self, code, **details):
        super().__init__(code); self.code = code; self.details = details

def fail(code, **details): raise ProfileError(code, **details)

def normalize(value):
    if not isinstance(value, str): fail('INVALID_NICKNAME')
    value = unicodedata.normalize('NFKC', value)
    value = ''.join(' ' if c.isspace() else '' if unicodedata.category(c) in ('Cf', 'Cc') else c for c in value)
    nickname = re.sub(' +', ' ', value).strip()
    if not 2 <= len(nickname) <= 16 or not any(c.isalpha() or c.isdecimal() for c in nickname):
        fail('INVALID_NICKNAME')
    return nickname, nickname.lower()

def reserved(key):
    compact = ''.join(c for c in key if c.isalpha() or c.isdecimal())
    return any(x in compact for x in ('管理员', '官方', '系统', '子午汐律', 'ziwuxilv',
                                    'meridianmarketrhythm', 'administrator', 'official', 'system', 'admin'))

def timestamp(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00')).astimezone(timezone.utc)

def iso(value): return value.astimezone(SHANGHAI).isoformat(timespec='seconds')

def avatar_bytes(data):
    if not data or len(data) > MAX_AVATAR: fail('AVATAR_TOO_LARGE')
    try:
        with Image.open(io.BytesIO(data)) as image:
            if image.format != 'WEBP' or image.size != (256, 256) or getattr(image, 'n_frames', 1) != 1:
                fail('AVATAR_TOO_LARGE')
            image.load()
            out = io.BytesIO()
            # Re-encoding strips EXIF, ICC and other client-supplied metadata.
            image.convert('RGB').save(out, 'WEBP', quality=88, method=6)
            clean = out.getvalue()
            if len(clean) > MAX_AVATAR: fail('AVATAR_TOO_LARGE')
            return clean
    except (UnidentifiedImageError, OSError, ValueError): fail('AVATAR_TOO_LARGE')

def verified_request(event, issue, seal, comments):
    actor = issue.get('user', {})
    actor_id = actor.get('id')
    if not isinstance(actor_id, int) or actor_id <= 0 or actor.get('type') != 'User': fail('PROFILE_AUTH_FAILED')
    if issue.get('pull_request') or issue.get('state') not in ('open', 'closed'): fail('PROFILE_AUTH_FAILED')
    if event.get('repository', {}).get('full_name') != REPOSITORY: fail('PROFILE_AUTH_FAILED')
    event_comment = event.get('comment', {})
    if (event.get('sender', {}).get('id') != actor_id or event_comment.get('user', {}).get('id') != actor_id
        or seal.get('user', {}).get('id') != actor_id or seal.get('id') != event_comment.get('id')
        or event.get('issue', {}).get('number') != issue.get('number')):
        fail('PROFILE_AUTH_FAILED')
    body = issue.get('body', '')
    if not body.startswith(REQUEST_PREFIX) or not seal.get('body', '').startswith(SUBMIT_PREFIX): fail('PROFILE_AUTH_FAILED')
    try:
        request = json.loads(body[len(REQUEST_PREFIX):])
        final = json.loads(seal['body'][len(SUBMIT_PREFIX):])
        request_id = request['request_id']
        if not re.fullmatch(r'[a-f0-9]{32}', request_id): fail('PROFILE_AUTH_FAILED')
        if final['request_id'] != request_id or final['body_sha256'] != hashlib.sha256(body.encode()).hexdigest():
            fail('PROFILE_AUTH_FAILED')
        if request.get('version') != 1: fail('PROFILE_AUTH_FAILED')
        # Submitted IDs/logins cannot determine the target directory or admin exemption.
        if 'github_id' in request and request['github_id'] != actor_id: fail('PROFILE_AUTH_FAILED')
        pieces = {}
        for comment in comments:
            text = comment.get('body', '')
            if not text.startswith(CHUNK_PREFIX) or comment.get('user', {}).get('id') != actor_id: continue
            part = json.loads(text[len(CHUNK_PREFIX):])
            if part.get('request_id') != request_id: continue
            index = part.get('index')
            if not isinstance(index, int) or not 0 <= index < 4: fail('AVATAR_TOO_LARGE')
            encoded = part.get('data', '')
            if not isinstance(encoded, str) or len(encoded) > 48000: fail('AVATAR_TOO_LARGE')
            if index in pieces and pieces[index] != encoded: fail('AVATAR_TOO_LARGE')
            pieces[index] = encoded
        action = request.get('avatar_action', 'keep')
        data = None
        if action == 'replace':
            count = request.get('avatar_parts')
            if not isinstance(count, int) or not 1 <= count <= 4 or set(pieces) != set(range(count)): fail('AVATAR_TOO_LARGE')
            raw = base64.b64decode(''.join(pieces[x] for x in range(count)), validate=True)
            if hashlib.sha256(raw).hexdigest() != request.get('avatar_sha256'): fail('AVATAR_TOO_LARGE')
            data = avatar_bytes(raw)
        elif action not in ('keep', 'remove'): fail('PROFILE_UPDATE_FAILED')
        return actor_id, actor['login'], request, data
    except ProfileError: raise
    except (KeyError, TypeError, ValueError): fail('PROFILE_AUTH_FAILED')

def apply_update(index, actor_id, login, request, avatar, now):
    nickname, key = normalize(request.get('nickname'))
    if actor_id != ADMIN_ID and reserved(key): fail('RESERVED_NICKNAME')
    profiles = index.get('profiles', {})
    previous = profiles.get(str(actor_id))
    if previous and previous.get('last_request_id') == request['request_id']:
        return index, previous, None  # Durable idempotence after commit but before notification.
    if previous and actor_id != ADMIN_ID:
        next_edit = timestamp(previous['updated_at']) + timedelta(days=30)
        if now < next_edit: fail('PROFILE_EDIT_COOLDOWN', next_edit_at=iso(next_edit))
    for owner, profile in profiles.items():
        if owner != str(actor_id) and normalize(profile['nickname'])[1] == key: fail('NICKNAME_ALREADY_USED')

    action = request.get('avatar_action', 'keep')
    backend = storage_backend('avatars')
    previous = previous or {}
    if action == 'keep':
        avatar_path = previous.get('avatar_path')
        avatar_hash = previous.get('avatar_sha256')
        avatar_url = previous.get('avatar_url')
        avatar_image_id = previous.get('avatar_image_id')
        avatar_asset_id = previous.get('avatar_asset_id')
        avatar_release_tag = previous.get('avatar_release_tag')
        avatar_asset_name = previous.get('avatar_asset_name')
    elif action == 'remove':
        avatar_path = avatar_hash = avatar_url = avatar_image_id = None
        avatar_asset_id = avatar_release_tag = avatar_asset_name = None
    else:
        avatar_hash = hashlib.sha256(avatar).hexdigest() if avatar is not None else None
        avatar_path = f'profiles/{actor_id}/avatar.webp' if backend == 'git' else None
        avatar_url = avatar_image_id = None
        avatar_asset_id = avatar_release_tag = avatar_asset_name = None

    previous_has_avatar = bool(previous.get('avatar_path') or previous.get('avatar_url'))
    current_has_avatar = bool(avatar_path or avatar_url or action == 'replace')
    if (previous and previous['nickname'] == nickname and previous.get('avatar_sha256') == avatar_hash
        and previous_has_avatar == current_has_avatar and action != 'replace'):
        fail('PROFILE_NO_CHANGE')
    if (previous and previous['nickname'] == nickname and action == 'replace'
        and previous.get('avatar_sha256') == avatar_hash):
        fail('PROFILE_NO_CHANGE')

    profile = dict(version=1, github_id=actor_id, github_login=login, nickname=nickname, nickname_key=key,
                   avatar_path=avatar_path, avatar_url=avatar_url, avatar_sha256=avatar_hash,
                   avatar_image_id=avatar_image_id, avatar_asset_id=avatar_asset_id,
                   avatar_release_tag=avatar_release_tag, avatar_asset_name=avatar_asset_name,
                   updated_at=iso(now), next_edit_at=iso(now + timedelta(days=30)),
                   last_request_id=request['request_id'])
    result = copy.deepcopy(index)
    result.update(version=1, updated_at=iso(now))
    result.setdefault('profiles', {})[str(actor_id)] = profile
    return result, profile, avatar
def api(path, method='GET', body=None):
    if not path.startswith('/repos/' + REPOSITORY + '/'):
        raise RuntimeError('Unexpected API path')
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request('https://api.github.com' + path, data=data, method=method, headers={
        'Authorization': 'Bearer '+os.environ['GH_TOKEN'], 'Accept': 'application/vnd.github+json',
        'Content-Type': 'application/json', 'User-Agent': 'ZiWuXiLv-Profile-Registry', 'X-GitHub-Api-Version': '2022-11-28'})
    with urllib.request.urlopen(req, timeout=30) as response: return json.load(response)

def shell(*args):
    return subprocess.run(args, check=True, capture_output=True, text=True, encoding='utf-8').stdout.strip()

def load_json(path, default=None):
    p = Path(path)
    return json.loads(p.read_text(encoding='utf-8')) if p.exists() else default

def write_json(path, value):
    p = Path(path); p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')

def process(event):
    number = int(event['issue']['number']); comment_id = int(event['comment']['id'])
    prefix = '/repos/'+REPOSITORY
    issue = api(f'{prefix}/issues/{number}')
    seal = api(f'{prefix}/issues/comments/{comment_id}')
    comments = []
    for page in range(1, 11):
        batch = api(f'{prefix}/issues/{number}/comments?per_page=100&page={page}')
        comments += batch
        if len(batch) < 100: break
    actor_id, login, request, avatar = verified_request(event, issue, seal, comments)
    receipt_path = f"profiles/requests/{actor_id}/{request['request_id']}.json"
    for attempt in range(4):
        # Checkout is trusted main; incoming PR code is never executed with write permissions.
        shell('git', 'fetch', 'origin', 'main')
        shell('git', 'reset', '--hard', 'origin/main')
        existing = load_json(receipt_path)
        if existing:
            result = existing
            commit = shell('git', 'rev-parse', 'HEAD')
            break
        index = load_json('profiles/index.json', {'version': 1, 'profiles': {}})
        now = datetime.now(timezone.utc)
        result = dict(version=1, request_id=request['request_id'], github_id=actor_id, issue_number=number)
        try:
            updated, profile, clean_avatar = apply_update(index, actor_id, login, request, avatar, now)
            from image_lifecycle import registry, register, register_release_asset, REGISTRY
            image_registry = registry()
            avatar_ref = dict(type='avatar', id=str(actor_id))
            for entry in image_registry['images'].values():
                entry['references'] = [r for r in entry.get('references', []) if r != avatar_ref]

            if clean_avatar is not None:
                digest = hashlib.sha256(clean_avatar).hexdigest()
                if storage_backend('avatars') == 'release_asset':
                    tag = monthly_tag(now)
                    name = f"avatar-{actor_id}-{request['request_id']}-{digest[:12]}.webp"
                    asset = upload_bytes(tag, name, clean_avatar, 'image/webp')
                    iid = register_release_asset(image_registry, actor_id, digest, int(asset['id']), tag, name,
                                                 asset['browser_download_url'], now.isoformat())
                    profile.update(avatar_path=None, avatar_url=asset['browser_download_url'],
                                   avatar_sha256=digest, avatar_image_id=iid, avatar_asset_id=int(asset['id']),
                                   avatar_release_tag=tag, avatar_asset_name=name)
                    updated['profiles'][str(actor_id)] = profile
                else:
                    target = Path(profile['avatar_path'])
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(clean_avatar)
                    iid = register(image_registry, profile['avatar_path'], actor_id, digest, now.isoformat())
                    profile.update(avatar_url=None, avatar_image_id=iid, avatar_asset_id=None,
                                   avatar_release_tag=None, avatar_asset_name=None)
                    updated['profiles'][str(actor_id)] = profile

            if profile.get('avatar_image_id'):
                iid = profile['avatar_image_id']
                if iid not in image_registry['images']: fail('PROFILE_UPDATE_FAILED')
                if avatar_ref not in image_registry['images'][iid]['references']:
                    image_registry['images'][iid]['references'].append(avatar_ref)
            elif profile.get('avatar_path'):
                iid = register(image_registry, profile['avatar_path'], actor_id, profile['avatar_sha256'], now.isoformat())
                profile['avatar_image_id'] = iid
                updated['profiles'][str(actor_id)] = profile
                if avatar_ref not in image_registry['images'][iid]['references']:
                    image_registry['images'][iid]['references'].append(avatar_ref)

            if request.get('avatar_action') == 'remove':
                path = f'profiles/{actor_id}/avatar.webp'
                if not any(e.get('storage_path') == path and e.get('references') for e in image_registry['images'].values()):
                    Path(path).unlink(missing_ok=True)

            write_json(REGISTRY, image_registry)
            write_json(f'profiles/{actor_id}/profile.json', profile)
            write_json('profiles/index.json', updated)
            result.update(status='success', profile=profile)
        except ProfileError as error:
            result.update(status='error', error=error.code, **error.details)
        write_json(receipt_path, result)
        shell('git', 'add', '--', 'profiles', *(['media'] if Path('media').exists() else []))
        shell('git', '-c', 'user.name=github-actions[bot]', '-c', 'user.email=41898282+github-actions[bot]@users.noreply.github.com',
              'commit', '-m', f"Update Profile Registry request {request['request_id']}")
        try:
            shell('git', 'push', 'origin', 'HEAD:main')
            commit = shell('git', 'rev-parse', 'HEAD'); break
        except subprocess.CalledProcessError:
            # Non-fast-forward: reload and recheck uniqueness/cooldown against newest main.
            if attempt == 3: raise
    result = dict(result, commit_sha=commit)
    # Reply after push. A retry recovers from the durable receipt and never consumes cooldown twice.
    if not any(c.get('user', {}).get('id') == 41898282 and c.get('body', '').startswith(RESULT_PREFIX) for c in comments):
        api(f'{prefix}/issues/{number}/comments', 'POST', {'body': RESULT_PREFIX+json.dumps(result, ensure_ascii=False)})
    api(f'{prefix}/issues/{number}', 'PATCH', {'state': 'closed'})
    print('Profile request processed:', request['request_id'], result['status'])

def report_failure(event):
    number = int(event['issue']['number'])
    issue = api(f'/repos/{REPOSITORY}/issues/{number}')
    owner = issue.get('user', {}).get('id')
    if owner != event.get('sender', {}).get('id') or owner != event.get('comment', {}).get('user', {}).get('id'):
        fail('PROFILE_AUTH_FAILED')
    body = issue.get('body', '')
    request = json.loads(body[len(REQUEST_PREFIX):]) if body.startswith(REQUEST_PREFIX) else {}
    request_id = request.get('request_id', '')
    if not re.fullmatch(r'[a-f0-9]{32}', request_id): fail('PROFILE_AUTH_FAILED')
    # If push completed but acknowledgment failed, report the committed success instead.
    shell('git', 'fetch', 'origin', 'main')
    shell('git', 'reset', '--hard', 'origin/main')
    receipt = load_json(f'profiles/requests/{owner}/{request_id}.json')
    result = receipt or dict(version=1, request_id=request_id, github_id=owner,
                             status='error', error='PROFILE_UPDATE_FAILED')
    result = dict(result, commit_sha=shell('git', 'rev-parse', 'HEAD'))
    api(f'/repos/{REPOSITORY}/issues/{number}/comments', 'POST', {'body': RESULT_PREFIX+json.dumps(result, ensure_ascii=False)})
    api(f'/repos/{REPOSITORY}/issues/{number}', 'PATCH', {'state': 'closed'})

if __name__ == '__main__':
    event = json.loads(Path(os.environ['GITHUB_EVENT_PATH']).read_text(encoding='utf-8'))
    if event.get('comment', {}).get('body', '').startswith(SUBMIT_PREFIX):
        if '--report-failure' in sys.argv:
            report_failure(event)
            raise SystemExit(0)
        try:
            process(event)
        except ProfileError as error:
            # Return a typed rejection only to the authenticated owner of this request.
            number = int(event['issue']['number'])
            issue = api(f'/repos/{REPOSITORY}/issues/{number}')
            owner = issue.get('user', {}).get('id')
            if owner != event.get('sender', {}).get('id') or owner != event.get('comment', {}).get('user', {}).get('id'):
                raise
            body = issue.get('body', '')
            request = json.loads(body[len(REQUEST_PREFIX):]) if body.startswith(REQUEST_PREFIX) else {}
            request_id = request.get('request_id', '')
            if not re.fullmatch(r'[a-f0-9]{32}', request_id): raise
            result = dict(version=1, request_id=request_id, github_id=owner, status='error', error=error.code, **error.details)
            api(f'/repos/{REPOSITORY}/issues/{number}/comments', 'POST', {'body': RESULT_PREFIX+json.dumps(result, ensure_ascii=False)})
            api(f'/repos/{REPOSITORY}/issues/{number}', 'PATCH', {'state': 'closed'})
            print('Profile request rejected:', request_id, error.code)

