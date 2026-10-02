# Shared image lifecycle

## Existing storage and compatibility

Posts and comments/replies are GitHub Discussions in chaonpu/ziwuxilv. Their GraphQL node IDs are the content IDs. Community images are WebP files at `media/<authenticated GitHub user ID>/<request UUID>/<0..2>.webp`; avatars are `profiles/<user ID>/avatar.webp`. Existing Markdown contains immutable Git raw URLs. The upload workflow accepts sealed, owner-authenticated Issues, validates and re-encodes WebP, and writes a durable receipt. The app has no Contents write credentials.

## Registry

`media/images.json` has version 1, `images`, `deleted_contents` and `last_complete_scan_at`. Image keys are `img_` plus SHA-256 of `storage_path + ':' + image_sha256`. Each image records URL, storage_path, owner_id, SHA-256, creation time and references `{type, id}`. Types include post, comment, reply and avatar. Deterministic IDs support shared references. New content carries `<!-- ZIWUXILV_IMAGES_V1:["img_..."] -->`; generated Markdown is only a compatibility presentation for GitHub readers. The Android business operation passes the content node ID, never a list of files to remove.

Uploads atomically commit files, registry entries and receipts. Profile changes maintain avatar refs in that same registry. The registry write, profile writer, media writer and lifecycle jobs use the same Actions concurrency group. Other concurrent main writes are preserved with non-fast-forward retry.

## Authorized deletion

A sealed lifecycle Issue identifies `attach` or `delete` and a content ID. The server re-fetches the real Issue/Comment actor, validates repository, exact seal ID and body hash, and scans all pages of Discussions, comments, replies and the avatar index. Admin GitHub ID 205125766 may delete any content; other users may delete their own comments/replies only. Attach requests must own the actual GitHub content. Arbitrary image IDs cannot authorize deletion.

Before the content mutation, a durable pending receipt saves the verified authorization and affected descendants. Failed content deletion preserves blobs and their refs. Confirmed deletion is followed by another complete inventory. Shared refs retain the image; the last ref removes the file and registry entry in one Git commit. Deleted node IDs invalidate app notifications. If the Git push or acknowledgment fails, the same request resumes without deleting content twice. A failure response is a resumable status, never a fabricated success.

GitHub wipes a parent comment that has replies, preserving those replies. Comment deletion therefore detaches only the selected comment, while post deletion affects the entire discussion. The `deletedAt` field lets a previously authorized pending operation recover after a parent wipe without repeating its mutation or hiding another author's reply. See [GitHub Discussions schema](https://docs.github.com/en/graphql/reference/discussions).


## Release Asset binary storage

Binary storage is now abstracted from the Git metadata layer. `storage/assets.json` selects the backend independently for community images, shared avatars, and Android APKs.

Community images and avatars can be stored as GitHub Release Assets under monthly, automatically sharded releases such as `community-assets-2026-10` and `community-assets-2026-10-02`. The repository keeps only registry metadata and durable receipts: asset ID, release tag, asset name, public download URL, owner, SHA-256 and content references. Each monthly release rolls to another shard before the GitHub per-release asset limit is reached.

The lifecycle registry supports both legacy Git-backed entries and Release-Asset-backed entries during migration. A Discussion deletion still performs a complete reference inventory first. If the last reference belongs to a Release Asset, the trusted Action deletes that exact GitHub release asset by numeric asset ID and removes the registry entry. Shared references remain protected.

The rollout is deliberately staged. Community images and avatars remain on the legacy Git backend until a Release-Asset-capable Android version has been published. Android APK publishing can move to Release Assets immediately because existing clients already accept HTTPS update URLs and redirects. After the compatible client is deployed, switching `communityImages` and `avatars` to `release_asset` activates the new storage without changing the user-facing upload protocol.

## Discussion retention

A separate daily workflow runs at 03:30 Asia/Shanghai and performs a complete Discussions inventory before deleting anything. Ordinary discussions are eligible only when their `createdAt` timestamp is more than 90 days old.

The retention pass never deletes:
- pinned discussions;
- discussions in categories whose names indicate announcements or rules (`公告`, `规则`, `Announcements`, `Rules`);
- discussions marked for permanent retention by administrator GitHub ID 205125766. The administrator can add a comment containing a line exactly equal to `#永久保留` or the marker `ZIWUXILV_RETAIN`. The same text from a non-admin account is ignored.

Deletion is tree-aware and resumable. Before the remote Discussion mutation, the workflow records the post, all comments/replies, and every managed image referenced by that tree. After GitHub confirms deletion, it performs another complete inventory, adds the deleted node IDs to `deleted_contents`, and immediately removes image blobs that no longer have any surviving reference. Images shared by another live post/comment/reply are retained. A durable receipt under `media/retention/` lets an interrupted run finish without needing to delete the Discussion twice.

## Collection and migration

Scheduled every six hours and available by manual dispatch. It scans every page and stops on incomplete inventories, malformed refs or a missing avatar index. It reconciles ID markers and recognized legacy managed URLs; external URLs are never owned or deleted. Legacy files are adopted into the registry without editing existing Discussion bodies. Uploads not yet published and other orphan files get at least 48 hours and a second complete scan before collection. Live avatar refs protect avatars; replaced IDs sharing the current avatar path cannot unlink its blob.

## Storage limitation

Deletion removes a file from the current repository tree, not from historical Git commits. GitHub does not provide a raw-content CDN purge API here. Existing immutable raw URLs may remain reachable through history/CDN. The app removes invalid content and image presentations on sync and does not persist a discussion-image disk cache. This implementation does not claim irreversible erasure from all historical storage. Such erasure requires a separately authorized storage migration or history rewrite.

## Validation

`python -m unittest discover -s tests -v` exercises actual temporary Git remotes as well as reference logic: shared and last refs, admin other-user deletion, ownership and seal forgery, failed mutation recovery, truncated scans, avatar shared-path safety, orphan grace, upload integrity, profile uniqueness/cooldowns and concurrent main preservation.
