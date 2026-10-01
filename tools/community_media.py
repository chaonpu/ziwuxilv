"""Controlled community image writer. Authenticated issue owner defines all paths."""
from __future__ import annotations
import base64, hashlib, io, json, os, re, subprocess, sys
from datetime import datetime, timezone
from pathlib import Path
from PIL import Image, UnidentifiedImageError
from profile_registry import api, shell, load_json, write_json

REPOSITORY = 'chaonpu/ziwuxilv'
REQUEST = 'ZIWUXILV_MEDIA_REQUEST_V1\n'
CHUNK = 'ZIWUXILV_MEDIA_CHUNK_V1\n'
SUBMIT = 'ZIWUXILV_MEDIA_SUBMIT_V1\n'
RESULT = 'ZIWUXILV_MEDIA_RESULT_V1\n'
LIMIT = 256 * 1024

class MediaError(Exception):
    pass

def reject(code='MEDIA_AUTH_FAILED'):
    raise MediaError(code)

def clean_image(data):
    if not data or len(data) > LIMIT: reject('MEDIA_INVALID_IMAGE')
    try:
        with Image.open(io.BytesIO(data)) as image:
            if (image.format != 'WEBP' or getattr(image, 'n_frames', 1) != 1
                or not all(1 <= size <= 1280 for size in image.size)):
                reject('MEDIA_INVALID_IMAGE')
            image.load()
            out = io.BytesIO()
            image.convert('RGB').save(out, 'WEBP', quality=88, method=6)
            clean = out.getvalue()
            if len(clean) > LIMIT: reject('MEDIA_INVALID_IMAGE')
            return clean, image.size
    except (UnidentifiedImageError, OSError, ValueError):
        reject('MEDIA_INVALID_IMAGE')

def verify(event, issue, seal, comments):
    actor = issue.get('user', {})
    owner = actor.get('id')
    if type(owner) is not int or owner <= 0 or actor.get('type') != 'User': reject()
    if issue.get('pull_request') or issue.get('state') not in ('open', 'closed'): reject()
    if (event.get('repository', {}).get('full_name') != REPOSITORY
        or event.get('issue', {}).get('number') != issue.get('number')
        or event.get('sender', {}).get('id') != owner
        or event.get('comment', {}).get('user', {}).get('id') != owner
        or seal.get('user', {}).get('id') != owner
        or seal.get('id') != event.get('comment', {}).get('id')): reject()
    body = issue.get('body', '')
    if not body.startswith(REQUEST) or not seal.get('body', '').startswith(SUBMIT): reject()
    try:
        request = json.loads(body[len(REQUEST):])
        final = json.loads(seal['body'][len(SUBMIT):])
        rid = request['request_id']
        if not isinstance(rid, str) or not re.fullmatch('[a-f0-9]{32}', rid): reject()
        if request.get('version') != 1 or ('github_id' in request and request['github_id'] != owner): reject()
        if final['request_id'] != rid or final['body_sha256'] != hashlib.sha256(body.encode()).hexdigest(): reject()
        images = request['images']
        if not isinstance(images, list) or not 1 <= len(images) <= 3: reject('MEDIA_INVALID_IMAGE')
        pieces = {}
        for comment in comments:
            text = comment.get('body', '')
            if comment.get('user', {}).get('id') != owner or not text.startswith(CHUNK): continue
            chunk = json.loads(text[len(CHUNK):])
            if chunk.get('request_id') != rid: continue
            index, part, data = chunk['image'], chunk['part'], chunk['data']
            if (type(index) is not int or not 0 <= index < len(images)
                or type(part) is not int or not 0 <= part < 8
                or not isinstance(data, str) or not 0 < len(data) <= 48000): reject('MEDIA_INVALID_IMAGE')
            key = (index, part)
            if key in pieces and pieces[key] != data: reject('MEDIA_INVALID_IMAGE')
            pieces[key] = data
        decoded = []
        expected = set()
        for index, image in enumerate(images):
            count = image['parts']
            if type(count) is not int or not 1 <= count <= 8: reject('MEDIA_INVALID_IMAGE')
            expected.update((index, part) for part in range(count))
            raw = base64.b64decode(''.join(pieces[(index, part)] for part in range(count)), validate=True)
            if hashlib.sha256(raw).hexdigest() != image['sha256']: reject('MEDIA_INVALID_IMAGE')
            decoded.append(clean_image(raw))
        if set(pieces) != expected: reject('MEDIA_INVALID_IMAGE')
        return owner, request, decoded
    except MediaError: raise
    except (KeyError, TypeError, ValueError): reject('MEDIA_INVALID_IMAGE')

def fetch_request(event):
    number, cid = int(event['issue']['number']), int(event['comment']['id'])
    prefix = '/repos/' + REPOSITORY
    issue = api(f'{prefix}/issues/{number}')
    seal = api(f'{prefix}/issues/comments/{cid}')
    comments = []
    for page in range(1, 11):
        batch = api(f'{prefix}/issues/{number}/comments?per_page=100&page={page}')
        comments.extend(batch)
        if len(batch) < 100: break
    return issue, seal, comments

def acknowledge(number, result, comments):
    if not any(c.get('user', {}).get('id') == 41898282 and c.get('user', {}).get('type') == 'Bot'
               and c.get('body', '').startswith(RESULT) for c in comments):
        api(f'/repos/{REPOSITORY}/issues/{number}/comments', 'POST',
            {'body': RESULT + json.dumps(result, ensure_ascii=False)})
    api(f'/repos/{REPOSITORY}/issues/{number}', 'PATCH', {'state': 'closed'})

def process(event):
    issue, seal, comments = fetch_request(event)
    owner, request, decoded = verify(event, issue, seal, comments)
    rid = request['request_id']
    receipt = f'media/requests/{owner}/{rid}.json'
    for attempt in range(4):
        shell('git', 'fetch', 'origin', 'main')
        shell('git', 'reset', '--hard', 'origin/main')
        result = load_json(receipt)
        if result:
            if (result.get('github_id') != owner or result.get('request_id') != rid
                or [i.get('upload_sha256') for i in result.get('images', [])]
                != [i['sha256'] for i in request['images']]): reject()
            break
        metadata = []
        for index, (data, size) in enumerate(decoded):
            path = f'media/{owner}/{rid}/{index}.webp'
            target = Path(path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            metadata.append(dict(index=index, upload_sha256=request['images'][index]['sha256'],
                                 sha256=hashlib.sha256(data).hexdigest(), width=size[0], height=size[1],
                                 bytes=len(data), path=path))
        result = dict(version=1, request_id=rid, github_id=owner, issue_number=issue['number'],
                      status='success', created_at=datetime.now(timezone.utc).isoformat(), images=metadata)
        write_json(receipt, result)
        shell('git', 'add', '--', 'media')
        shell('git', '-c', 'user.name=github-actions[bot]',
              '-c', 'user.email=41898282+github-actions[bot]@users.noreply.github.com',
              'commit', '-m', f'Upload community media request {rid}')
        try:
            shell('git', 'push', 'origin', 'HEAD:main')
            break
        except subprocess.CalledProcessError:
            if attempt == 3: raise
    acknowledge(issue['number'], dict(result, commit_sha=shell('git', 'rev-parse', 'HEAD')), comments)
    print('Community media request processed:', rid)

def report_failure(event, code='MEDIA_UPLOAD_FAILED'):
    issue, seal, comments = fetch_request(event)
    # Failure notifications require the same real owner and sealed request authentication.
    owner = issue.get('user', {}).get('id')
    body = issue.get('body', '')
    if (type(owner) is not int or owner <= 0 or issue.get('user', {}).get('type') != 'User'
        or issue.get('pull_request') or event.get('repository', {}).get('full_name') != REPOSITORY
        or event.get('issue', {}).get('number') != issue.get('number')
        or event.get('sender', {}).get('id') != owner
        or event.get('comment', {}).get('user', {}).get('id') != owner
        or seal.get('user', {}).get('id') != owner
        or seal.get('id') != event.get('comment', {}).get('id')
        or not body.startswith(REQUEST) or not seal.get('body', '').startswith(SUBMIT)): reject()
    request = json.loads(body[len(REQUEST):])
    final = json.loads(seal['body'][len(SUBMIT):])
    rid = request.get('request_id', '')
    if (not re.fullmatch('[a-f0-9]{32}', rid) or final.get('request_id') != rid
        or final.get('body_sha256') != hashlib.sha256(body.encode()).hexdigest()): reject()
    shell('git', 'fetch', 'origin', 'main')
    shell('git', 'reset', '--hard', 'origin/main')
    result = load_json(f'media/requests/{owner}/{rid}.json')
    if result and [i.get('upload_sha256') for i in result.get('images', [])] != [i.get('sha256') for i in request.get('images', [])]: reject()
    result = result or dict(version=1, request_id=rid, github_id=owner, status='error', error=code)
    acknowledge(issue['number'], dict(result, commit_sha=shell('git', 'rev-parse', 'HEAD')), comments)

if __name__ == '__main__':
    event = json.loads(Path(os.environ['GITHUB_EVENT_PATH']).read_text(encoding='utf-8'))
    if event.get('comment', {}).get('body', '').startswith(SUBMIT):
        if '--report-failure' in sys.argv: report_failure(event)
        else:
            try: process(event)
            except MediaError as error: report_failure(event, str(error))
