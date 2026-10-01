# Shared image lifecycle

## Existing storage and compatibility

Posts and comments/replies are GitHub Discussions in chaonpu/ziwuxilv. Their GraphQL node IDs are the content IDs. Community images are WebP files at `media/<authenticated GitHub user ID>/<request UUID>/<0..2>.webp`; avatars are `profiles/<user ID>/avatar.webp`. Existing Markdown contains immutable Git raw URLs. The upload workflow accepts sealed, owner-authenticated Issues, validates and re-encodes WebP, and writes a durable receipt. The app has no Contents write credentials.

## Registry

`media/images.json` has version 1, `images`, `deleted_contents` and `last_complete_scan_at`. Image keys are `img_` plus SHA-256 of `storage_path + ':' + image_sha256`. Each image records URL, storage_path, owner_id, SHA-256, creation time and references `{type, id}`. Types include post, comment, reply and avatar. Deterministic IDs support shared references. New content carries `<!-- ZIWUXILV_IMAGES_V1:["img_..."] -->`; generated Markdown is only a compatibility presentation for GitHub readers. The Android business operation passes the content node ID, never a list of files to remove.

Uploads atomically commit files, registry entries and receipts. Profile changes maintain avatar refs in that same registry. The registry write, profile writer, media writer and lifecycle jobs use the same Actions concurrency group. Other concurrent main writes are preserved with non-fast-forward retry.

## Authorized deletion

A sealed lifecycle Issue identifies `attach` or `delete` and a content ID. The server re-fetches the real Issue/Comment actor, validates repository, exact seal ID and body hash, and scans all pages of Discussions, comments, replies and the avatar index. Admin GitHub ID 205125766 may delete any content; other users may delete their own comments/replies only. Attach requests must own the actual GitHub content. Arbitrary image IDs cannot authorize deletion.

Before the content mutation, a durable pending receipt saves the verified authorization and affected descendants. Failed content deletion preserves blobs and their refs. Confirmed deletion is followed by another complete inventory. Shared refs retain the image; the last ref removes the file and registry entry in one Git commit. Deleted node IDs invalidate app notifications. If the Git push or acknowledgment fails, the same request resumes without deleting content twice. A failure response is a resumable status, never a fabricated success.

## Collection and migration

Scheduled every six hours and available by manual dispatch. It scans every page and stops on incomplete inventories, malformed refs or a missing avatar index. It reconciles ID markers and recognized legacy managed URLs; external URLs are never owned or deleted. Legacy files are adopted into the registry without editing existing Discussion bodies. Uploads not yet published and other orphan files get at least 48 hours and a second complete scan before collection. Live avatar refs protect avatars; replaced IDs sharing the current avatar path cannot unlink its blob.

## Storage limitation

Deletion removes a file from the current repository tree, not from historical Git commits. GitHub does not provide a raw-content CDN purge API here. Existing immutable raw URLs may remain reachable through history/CDN. The app removes invalid content and image presentations on sync and does not persist a discussion-image disk cache. This implementation does not claim irreversible erasure from all historical storage. Such erasure requires a separately authorized storage migration or history rewrite.

## Validation

`python -m unittest discover -s tests -v` exercises actual temporary Git remotes as well as reference logic: shared and last refs, admin other-user deletion, ownership and seal forgery, failed mutation recovery, truncated scans, avatar shared-path safety, orphan grace, upload integrity, profile uniqueness/cooldowns and concurrent main preservation.
