import datetime as dt
import json
import unittest
from pathlib import Path

from tools.android_publish_guard import (
    GuardError, PRODUCTION_CERT, validate_request,
)

NOW = dt.datetime(2026, 10, 17, 3, 0, tzinfo=dt.timezone.utc)

def valid():
    return {
        "sourceUrl": "https://example.com/release-artifacts/ziwuxilv-v2.3.5.zip",
        "artifactSha256": "a" * 64,
        "sourceCommit": "c" * 40,
        "versionName": "2.3.5",
        "versionCode": 118,
        "apkSha256": "b" * 64,
        "signingCertificateSha256": PRODUCTION_CERT,
        "updateLog": "Release notes",
        "forceUpdate": False,
    }

def active():
    return {"versionName": "2.3.4", "versionCode": 117}

class AndroidPublishGuardTests(unittest.TestCase):
    def assert_blocked(self, req, msg):
        with self.assertRaises(GuardError) as ctx:
            validate_request(req, active(), now=NOW)
        self.assertEqual(str(ctx.exception), msg)

    def test_fresh_nonpublic_staging_url_only_eligible_not_published(self):
        x = validate_request(valid(), active(), now=NOW)
        self.assertEqual(x["status"], "eligible_for_artifact_verification")
        self.assertFalse(x["downloadVerified"])
        self.assertFalse(x["published"])

    def test_same_version_rejected_before_any_write(self):
        p = valid()
        p["versionCode"] = 117
        self.assert_blocked(p, "not_newer_than_active_public_release")

    def test_lower_version_rejected(self):
        p = valid()
        p["versionCode"] = 116
        self.assert_blocked(p, "not_newer_than_active_public_release")

    def test_expired_presigned_url_blocked(self):
        p = valid()
        p["sourceUrl"] = ("https://example.com/file.zip"
                          "?se=2026-10-16T00%3A00%3A00Z&sig=SECRET")
        self.assert_blocked(p, "source_url_expired")

    def test_about_to_expire_url_blocked(self):
        p = valid()
        p["sourceUrl"] = "https://example.com/file.zip?expires=1792206060"
        self.assert_blocked(p, "source_url_expired")

    def test_future_signed_url_credentials_cannot_enter_public_request(self):
        p = valid()
        p["sourceUrl"] = ("https://example.com/release.zip"
                          "?se=2026-11-01T00%3A00%3A00Z&sig=SECRET")
        self.assert_blocked(p, "source_url_embeds_public_credentials")

    def test_wrong_certificate_blocked(self):
        p = valid()
        p["signingCertificateSha256"] = "0" * 64
        self.assert_blocked(p, "production_certificate_mismatch")

    def test_private_url_host_blocked(self):
        for name in ("localhost", "127.0.0.1", "10.0.0.1", "192.168.1.12", "[::1]"):
            p = valid()
            p["sourceUrl"] = "https://" + name + "/signed.zip"
            self.assert_blocked(p, "source_url_host_not_public")

    def test_nonhttps_userinfo_and_escaped_newlines_rejected(self):
        for url in (
            "http://example.com/file.zip",
            "https://user:secret@example.com/file.zip",
            "https://example.com/file.zip\nAuthorization: secret",
        ):
            p = valid()
            p["sourceUrl"] = url
            self.assert_blocked(p, "source_url_invalid")

    def test_invalid_version_code_not_bool(self):
        p = valid()
        p["versionCode"] = True
        self.assert_blocked(p, "invalid_version_code")

    def test_missing_active_version_fails_closed(self):
        with self.assertRaisesRegex(GuardError, "active_version_unverified"):
            validate_request(valid(), {}, now=NOW)

    def test_required_metadata_mandatory(self):
        p = valid()
        del p["apkSha256"]
        self.assert_blocked(p, "missing_required_field")

    def test_existing_public_request_is_not_republished(self):
        root = Path(__file__).resolve().parents[1]
        req_path = root / "android-publish-request.json"
        active_path = root / "android-native/version.json"
        if not req_path.is_file() or not active_path.is_file():
            self.skipTest("This checkout lacks the actual public release request")
        old = json.loads(req_path.read_text(encoding="utf-8"))
        installed = json.loads(active_path.read_text(encoding="utf-8"))
        with self.assertRaises(GuardError) as caught:
            validate_request(old, installed, now=NOW)
        self.assertEqual(str(caught.exception), "not_newer_than_active_public_release")

if __name__ == "__main__":
    unittest.main()
