"""Fail-closed public Android release request policy (no network, no writes).

Always validate before any publish-run status commit, release creation or
upload. Never log a source URL, because it may contain a signed query string.
"""
from __future__ import annotations

import argparse
import datetime as dt
import ipaddress
import json
import re
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

PRODUCTION_CERT = "bef3f5cbb2989734f20740eddaac3e105dfecb6c63ea9275e178eedd892ec105"
SHA = re.compile(r"^[0-9a-f]{64}$")
COMMIT = re.compile(r"^[0-9a-f]{40}$")
VERSION = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:[-.][a-zA-Z0-9.]+)?$")

class GuardError(ValueError):
    pass

def check_url(value, now):
    if not isinstance(value, str) or len(value) > 2500:
        raise GuardError("source_url_invalid")
    try:
        url = urlsplit(value)
        host = url.hostname
        if (url.scheme != "https" or not host or not url.path or url.username
                or url.password or url.port not in (None, 443)
                or any(x in value for x in ("\n", "\r", "\t", "\\"))):
            raise GuardError("source_url_invalid")
        if host == "localhost" or host.endswith((".local", ".internal", ".localhost")):
            raise GuardError("source_url_host_not_public")
        try:
            ip = ipaddress.ip_address(host)
        except ValueError:
            ip = None
        if ip is not None and not ip.is_global:
            raise GuardError("source_url_host_not_public")
        query = parse_qs(url.query)
        for raw in query.get("se", []):
            try:
                expires = dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
                if expires.tzinfo is None:
                    raise ValueError("Missing timezone")
            except ValueError as e:
                raise GuardError("source_url_expiration_invalid") from e
            if expires <= now + dt.timedelta(minutes=10):
                raise GuardError("source_url_expired")
        if "expires" in query:
            for raw in query["expires"]:
                try:
                    timestamp = int(raw)
                except ValueError as e:
                    raise GuardError("source_url_expiration_invalid") from e
                if dt.datetime.fromtimestamp(timestamp, dt.timezone.utc) <= now + dt.timedelta(minutes=10):
                    raise GuardError("source_url_expired")
        # android-publish-request.json is committed to a PUBLIC repository.
        # Never embed pre-signed download credentials or bearer tokens there.
        sensitive = {"sig", "signature", "token", "access_token", "auth",
                     "x-amz-signature", "x-amz-credential", "x-goog-signature",
                     "ske", "skt", "sks", "skoid", "sktid", "sp", "sr", "scid"}
        if sensitive.intersection(k.lower() for k in query):
            raise GuardError("source_url_embeds_public_credentials")
    except (ValueError, UnicodeError) as e:
        if isinstance(e, GuardError):
            raise
        raise GuardError("source_url_invalid") from e

def validate_request(req, active, now=None):
    now = now or dt.datetime.now(dt.timezone.utc)
    if not isinstance(req, dict) or not isinstance(active, dict):
        raise GuardError("invalid_document")
    required = ("sourceUrl", "artifactSha256", "sourceCommit", "versionName",
                "versionCode", "apkSha256", "signingCertificateSha256", "updateLog")
    if any(k not in req for k in required):
        raise GuardError("missing_required_field")
    for key in ("artifactSha256", "apkSha256", "signingCertificateSha256"):
        if not isinstance(req[key], str) or not SHA.fullmatch(req[key]):
            raise GuardError("invalid_sha256")
    if req["signingCertificateSha256"] != PRODUCTION_CERT:
        raise GuardError("production_certificate_mismatch")
    if not isinstance(req["sourceCommit"], str) or not COMMIT.fullmatch(req["sourceCommit"]):
        raise GuardError("invalid_source_commit")
    version = req["versionName"]
    if not isinstance(version, str) or not VERSION.fullmatch(version):
        raise GuardError("invalid_version")
    code = req["versionCode"]
    if type(code) is not int or code <= 0:
        raise GuardError("invalid_version_code")
    if not isinstance(req["updateLog"], str) or not (1 <= len(req["updateLog"]) <= 6000):
        raise GuardError("invalid_update_log")
    if "forceUpdate" in req and type(req["forceUpdate"]) is not bool:
        raise GuardError("invalid_force_update")
    # Signed APK source must be newer than the already released public APK.
    active_code = active.get("versionCode")
    if type(active_code) is not int or active_code <= 0:
        raise GuardError("active_version_unverified")
    if code <= active_code:
        raise GuardError("not_newer_than_active_public_release")
    check_url(req["sourceUrl"], now)
    # This does not prove artifact contents; that is the publish workflow's
    # subsequent byte-level SHA-256, certificate and package verification.
    return {"status": "eligible_for_artifact_verification",
            "versionName": version, "versionCode": code,
            "sourceCommit": req["sourceCommit"],
            "certificateMatches": True, "downloadVerified": False,
            "published": False}

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--request", type=Path, required=True)
    p.add_argument("--active", type=Path, required=True)
    args = p.parse_args()
    try:
        req = json.loads(args.request.read_text(encoding="utf-8"))
        active = json.loads(args.active.read_text(encoding="utf-8"))
        result = validate_request(req, active)
    except (GuardError, ValueError, OSError, TypeError, OverflowError) as e:
        # Error messages are fixed safe codes; never emit source URL or query.
        reason = str(e) if isinstance(e, GuardError) else "invalid_release_document"
        print(json.dumps({"status": "blocked", "reason": reason, "published": False}))
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0

if __name__ == "__main__":
    sys.exit(main())
