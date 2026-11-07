# Repository rules

Read `docs/API_CONTRACT_V1.md` and `docs/plans/ACTIVE.md` before changing behavior.
Center and Windows relay are implemented. Android is contract-only; never claim Android hardware validation.
Relay runs one process with one download thread, independent sync/report threads, and a local SQLite volume.
Preserve relay outbox/event identities, assignment revisions, immutable manifests and protected caches across updates.
Run relay tests and real HTTP center/relay validation after protocol or synchronization changes.
Preserve published manifests, referenced original media, task history and monotonic assignment revisions.
Explicit media deletion may remove only unreferenced assets; block saved drafts and all published/history references.
Use SQLite BEGIN IMMEDIATE for writes; keep the unique running-task constraint. One Web
process and one conversion worker only. No network requests from center to workshop devices.
Never embed keys in frontend, URLs or logs. Keep download authentication.
Run `python -m pytest -q` and, when tools are installed, `python -m scripts.validate_media`.
Do not replace Excel rendering with extracted cell text or force all worksheets onto one page by default.
An explicit screen_16_9 upload may fit each visible sheet onto one 1920x1080 playback image.
Direct PDF uploads keep existing pages and bypass Office parsing; do not claim this fixes Excel shape conversion.
Keep the API contract and validation evidence aligned with implementation.
