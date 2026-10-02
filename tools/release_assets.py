"""GitHub Release Assets storage helpers for binary user content and APKs.

The Git repository stores only metadata/receipts. Binary blobs are uploaded to Releases
with the workflow's short-lived GITHUB_TOKEN.
"""
from __future__ import annotations
import json, mimetypes, os, re, urllib.request, urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError

REPOSITORY = "chaonpu/ziwuxilv"
API = "https://api.github.com"
UPLOADS = "https://uploads.github.com"
CONFIG = "storage/assets.json"
TAG_RE = re.compile(r"community-assets-[0-9]{4}-[0-9]{2}")
ASSET_NAME_RE = re.compile(r"(?:media|avatar)-[A-Za-z0-9._-]{1,180}")

class ReleaseAssetError(RuntimeError):
    pass

def _token():
    value = os.environ.get("GH_TOKEN", "")
    if not value:
        raise ReleaseAssetError("Missing GH_TOKEN")
    return value

def _request(url, method="GET", body=None, content_type="application/vnd.github+json"):
    data = None
    if isinstance(body, (dict, list)):
        data = json.dumps(body, separators=(",", ":")).encode()
    elif body is not None:
        data = body
    headers = {
        "Authorization": "Bearer " + _token(),
        "Accept": "application/vnd.github+json",
        "User-Agent": "ZiWuXiLv-Release-Assets",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if data is not None:
        headers["Content-Type"] = content_type
        headers["Content-Length"] = str(len(data))
    request = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            raw = response.read()
            if not raw:
                return None
            return json.loads(raw.decode("utf-8"))
    except HTTPError as error:
        if error.code == 404:
            return None
        detail = error.read(4096).decode("utf-8", "replace")
        raise ReleaseAssetError(f"GitHub API {error.code}: {detail}") from error

def storage_backend(kind):
    try:
        value = json.loads(Path(CONFIG).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return "git"
    mapping = {
        "community": "communityImages",
        "avatars": "avatars",
        "android": "androidApk",
    }
    backend = value.get(mapping.get(kind, kind), "git")
    if backend not in ("git", "release_asset"):
        raise ReleaseAssetError("Invalid storage backend")
    return backend

def monthly_tag(timestamp=None):
    timestamp = timestamp or datetime.now(timezone.utc)
    return "community-assets-" + timestamp.astimezone(timezone.utc).strftime("%Y-%m")

def get_release(tag):
    if not TAG_RE.fullmatch(tag):
        raise ReleaseAssetError("Invalid release tag")
    quoted = urllib.parse.quote(tag, safe="")
    return _request(f"{API}/repos/{REPOSITORY}/releases/tags/{quoted}")

def ensure_release(tag):
    current = get_release(tag)
    if current:
        return current
    created = _request(f"{API}/repos/{REPOSITORY}/releases", "POST", {
        "tag_name": tag,
        "target_commitish": "main",
        "name": tag,
        "body": "Binary assets for ZiWuXiLv community images and shared avatars. Managed automatically.",
        "draft": False,
        "prerelease": False,
    })
    if not isinstance(created, dict) or not created.get("id"):
        raise ReleaseAssetError("Release creation failed")
    return created

def list_assets(release_id):
    rows = []
    page = 1
    while True:
        batch = _request(f"{API}/repos/{REPOSITORY}/releases/{int(release_id)}/assets?per_page=100&page={page}")
        if not isinstance(batch, list):
            raise ReleaseAssetError("Release asset listing failed")
        rows.extend(batch)
        if len(batch) < 100:
            return rows
        page += 1
        if page > 20:
            raise ReleaseAssetError("Too many assets in one release")

def upload_bytes(tag, name, data, content_type=None):
    if not TAG_RE.fullmatch(tag) or not ASSET_NAME_RE.fullmatch(name):
        raise ReleaseAssetError("Invalid release asset identity")
    if not isinstance(data, (bytes, bytearray)) or not data:
        raise ReleaseAssetError("Empty release asset")
    release = ensure_release(tag)
    for asset in list_assets(release["id"]):
        if asset.get("name") == name:
            if int(asset.get("size") or -1) != len(data):
                raise ReleaseAssetError("Existing release asset size mismatch")
            return asset
    query = urllib.parse.urlencode({"name": name})
    mime = content_type or mimetypes.guess_type(name)[0] or "application/octet-stream"
    result = _request(
        f"{UPLOADS}/repos/{REPOSITORY}/releases/{int(release['id'])}/assets?{query}",
        "POST", bytes(data), mime)
    if not isinstance(result, dict) or not result.get("id") or not result.get("browser_download_url"):
        raise ReleaseAssetError("Release asset upload failed")
    return result

def delete_asset(asset_id):
    if not isinstance(asset_id, int) or asset_id <= 0:
        raise ReleaseAssetError("Invalid asset id")
    # DELETE is idempotent: a missing asset is already in the desired state.
    return _request(f"{API}/repos/{REPOSITORY}/releases/assets/{asset_id}", "DELETE")

def release_asset_url_valid(url, tag=None, name=None):
    try:
        parsed = urllib.parse.urlparse(url)
    except ValueError:
        return False
    if parsed.scheme != "https" or parsed.netloc != "github.com":
        return False
    prefix = f"/{REPOSITORY}/releases/download/"
    if not parsed.path.startswith(prefix):
        return False
    rest = parsed.path[len(prefix):].split("/", 1)
    if len(rest) != 2 or not TAG_RE.fullmatch(rest[0]) or not ASSET_NAME_RE.fullmatch(rest[1]):
        return False
    return (tag is None or rest[0] == tag) and (name is None or rest[1] == name)
