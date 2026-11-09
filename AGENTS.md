# Repository rules

Read `docs/API_CONTRACT_V1.md` and `docs/plans/ACTIVE.md` before changing behavior.
Center, Windows relay and Android Kotlin player are implemented; never claim MediaTek hardware validation from emulator tests.
Android uses Room, local verified files and Media3; only contacts relay, never center/VPN/Google Play services.
Per user preference, future Android device validation uses real Android hardware only. Do not create, start or recreate emulator data unless the user explicitly requests it. Existing emulator evidence is historical, not physical-device evidence.
test-output currently also contains the live local center and relay directories referenced by LOCAL_INSTANCE.json and LOCAL_RELAY_INSTANCE.json; never treat those operational paths as disposable fixtures. Clean obsolete fixtures/install archives after retaining concise validation records; retain reusable build tools, final APKs and private signing data.
Build android/ with its Gradle Wrapper, run JVM tests and lint; distinguish APK build, emulator and physical-device evidence.
Relay runs one process with one download thread, independent sync/report threads, and a local SQLite volume.
Preserve relay outbox/event identities, assignment revisions, immutable manifests and protected caches across updates.
Run relay tests and real HTTP center/relay validation after protocol or synchronization changes.
Preserve published manifests, referenced original media, task history and monotonic assignment revisions.
Explicit media deletion may remove only unreferenced assets; block saved drafts and all non-expired published/history references. The user-authorized two-publication retention policy can expire unused old publications and release exclusively referenced media; preserve immutable metadata/descriptors, protect runtime references and reject a publication that would require more than one protected old version.
Use SQLite BEGIN IMMEDIATE for writes; keep the unique running-task constraint. One Web
process and one conversion worker only. No network requests from center to workshop devices.
Never embed keys in frontend, URLs or logs. Keep download authentication.
Run `python -m pytest -q` and, when tools are installed, `python -m scripts.validate_media`.
Do not replace Excel rendering with extracted cell text or force all worksheets onto one page by default.
An explicit screen_16_9 upload may fit each visible sheet onto one 1920x1080 playback image.
Direct PDF uploads keep existing pages and bypass Office parsing; do not claim this fixes Excel shape conversion.
Keep the API contract and validation evidence aligned with implementation.
For each delivered center code/UI update, increment CENTER_VERSION in center/version.py before building/deploying; document the release in docs/VALIDATION.md. This system version is independent of uploaded content and playlist versions.
Android deliveries use the existing private android/signing release key and increment versionCode/versionName. Never replace/regenerate an existing signing key, commit signing credentials, or tell users a differently signed APK can preserve data by overwriting. Missing release credentials must fail the build clearly.
