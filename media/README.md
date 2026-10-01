# Community images

The APP selects and compresses up to three images per post or reply. An authenticated,
sealed Issue request is processed by trusted main code; users need no repository write
permission or manual GitHub upload. Each image is re-encoded to remove metadata and
stored under the real numeric account ID and immutable request ID.

Receipts are committed atomically with the images. Retries recover the same request.
The APP verifies the receipt and embeds public image URLs in a Discussion authored by
the signed-in user. Existing Discussion posting permissions remain in force.

Limits: one-frame WebP, 1280 pixels per side, 256 KiB per image, three images.
Published images are public. No credentials or write-capable repository tokens are
included in the APK. A submitted upload can remain stored if the user abandons a draft.
