import json, os, sys, tempfile, unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import release_assets as r
import image_lifecycle as l
import profile_registry as p


class ReleaseAssetHelpersTest(unittest.TestCase):
    def test_monthly_tags_shard_before_asset_limit(self):
        now = datetime(2026, 10, 2, tzinfo=timezone.utc)
        self.assertEqual("community-assets-2026-10", r.monthly_tag(now, 1))
        self.assertEqual("community-assets-2026-10-02", r.monthly_tag(now, 2))
        self.assertTrue(r.release_asset_url_valid(
            "https://github.com/chaonpu/ziwuxilv/releases/download/community-assets-2026-10/media-123-a.webp"))
        self.assertTrue(r.release_asset_url_valid(
            "https://github.com/chaonpu/ziwuxilv/releases/download/community-assets-2026-10-02/avatar-123-a.webp"))
        self.assertFalse(r.release_asset_url_valid("https://example.com/file.webp"))

    def test_upload_monthly_rolls_to_second_release(self):
        data = b"image"
        first = {"id": 1}
        second = {"id": 2}
        full = [{"name": f"old-{i}", "size": 1} for i in range(950)]
        with patch.object(r, "get_release", side_effect=[first, second]),              patch.object(r, "list_assets", side_effect=[full, []]),              patch.object(r, "upload_bytes", return_value={"id": 9, "size": len(data)}) as upload:
            tag, asset = r.upload_monthly_bytes("media-123-a.webp", data, "image/webp",
                                                datetime(2026, 10, 2, tzinfo=timezone.utc))
        self.assertEqual("community-assets-2026-10-02", tag)
        self.assertEqual(9, asset["id"])
        upload.assert_called_once_with("community-assets-2026-10-02", "media-123-a.webp", data, "image/webp")

    def test_storage_config_defaults_to_git_and_reads_release_mode(self):
        with tempfile.TemporaryDirectory() as td:
            old = Path.cwd()
            try:
                os.chdir(td)
                self.assertEqual("git", r.storage_backend("community"))
                Path("storage").mkdir()
                Path(r.CONFIG).write_text(json.dumps({"communityImages": "release_asset"}), encoding="utf-8")
                self.assertEqual("release_asset", r.storage_backend("community"))
            finally:
                os.chdir(old)

    def test_release_backend_requires_capable_client(self):
        with patch.object(r, "storage_backend", return_value="release_asset"):
            self.assertEqual("git", r.negotiated_backend("community", {}))
            self.assertEqual("git", r.negotiated_backend("community", {"release_assets": False}))
            self.assertEqual("release_asset", r.negotiated_backend("community", {"release_assets": True}))
        with patch.object(r, "storage_backend", return_value="git"):
            self.assertEqual("git", r.negotiated_backend("community", {"release_assets": True}))


class ReleaseLifecycleTest(unittest.TestCase):
    def test_release_asset_registration_and_collection(self):
        value = {"version": 1, "images": {}, "deleted_contents": {}}
        url = "https://github.com/chaonpu/ziwuxilv/releases/download/community-assets-2026-10/media-123-a.webp"
        iid = l.register_release_asset(value, 123, "a" * 64, 77, "community-assets-2026-10",
                                       "media-123-a.webp", url, "2026-10-01T00:00:00+00:00")
        self.assertEqual("release_asset", value["images"][iid]["backend"])
        value, removals = l.collect(value, datetime(2026, 10, 3, tzinfo=timezone.utc), immediate=[iid])
        self.assertNotIn(iid, value["images"])
        self.assertEqual(["release_asset:77"], removals)

    def test_avatar_inventory_accepts_release_image_id(self):
        value = {"version": 1, "images": {}, "deleted_contents": {}}
        url = "https://github.com/chaonpu/ziwuxilv/releases/download/community-assets-2026-10/avatar-123-a.webp"
        iid = l.register_release_asset(value, 123, "b" * 64, 88, "community-assets-2026-10",
                                       "avatar-123-a.webp", url)
        records = [{"type": "avatar", "id": "123", "body": "", "owner_id": "123", "image_id": iid}]
        out = l.reconcile(value, records)
        self.assertEqual([{"type": "avatar", "id": "123"}], out["images"][iid]["references"])


class ReleaseProfileMetadataTest(unittest.TestCase):
    def test_release_avatar_prepares_metadata_without_git_path(self):
        now = datetime(2026, 10, 2, tzinfo=timezone.utc)
        request = {"version": 1, "request_id": "a" * 32, "nickname": "海风", "avatar_action": "replace",
                   "release_assets": True}
        with patch.object(p, "negotiated_backend", return_value="release_asset"):
            updated, profile, avatar = p.apply_update({"version": 1, "profiles": {}}, 123, "u", request, b"x", now)
        self.assertIsNone(profile["avatar_path"])
        self.assertIsNone(profile["avatar_url"])
        self.assertEqual(__import__("hashlib").sha256(b"x").hexdigest(), profile["avatar_sha256"])
        self.assertEqual(profile, updated["profiles"]["123"])
        self.assertEqual(b"x", avatar)


if __name__ == "__main__":
    unittest.main()
