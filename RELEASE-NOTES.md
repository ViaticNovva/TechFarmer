# TechFarmer v0.2.0 release notes

Canonical repository: https://github.com/ViaticNovva/TechFarmer.

## Implemented automatic workflow

- URL-driven series/chapter/page discovery using explicit permitted source profiles, reader/chapter pagination, bounded streaming transport, safe DNS/socket pinning, redirect/robots/access controls and explicit incomplete states.
- Real local OWLv2 image-guided backend at a pinned Apache-2.0 revision, using safe tensors and frozen per-job multi-reference character profiles. No production fixture/color/hash detector fallback.
- Durable SQLite phases/pages/candidates, background worker independent of Discord interaction lifetime, progress polling, restart recovery, pause/resume/cancel, page/failure dedupe and source identity safeguards.
- Native-resolution crop candidates from full originals: detected/reference-aspect/panel modes, aspect/alignment, per-side context/trim, rectangular/elliptical/circular shapes, optional separate reframing edits, human accept/reject review and preserved originals/hashes/provenance.
- Accepted PNGs saved locally and sent in private size-bounded batches; oversized files remain local without downscaling or public hosting. Previews alone are resized.
- Owner-and-guild checks for commands/buttons, ephemeral success/error/defer/attachments, suppressed mentions, no privileged intents, only configured-guild sync and no DM/public fallback.
- Nonsecret offline setup page, explicit Add to Discord authorization, settings/source-profile downloads, local hidden token prompt/environment handling, Windows install/vision/model/start launchers and project-private runtime folders.
- Procedural fixtures, token-free pipeline demo, read-only GitHub CI with required real Discord SDK import/serialization, manual-only heavy model smoke, and reviewed-text SHA256 manifest verification.

## Deliberate limitations — not hidden features

OWLv2 is generic one-shot object localization, not a verified comic-character identity system. Scores are not calibrated accuracy. Face/upper-body/full-body requests lack semantic evidence and produce review blockers instead of percentage-based anatomy crops. A framing reference transfers aspect only, not pose. Panel support is a reviewable white-gutter heuristic, not universal learned segmentation.

There is no universal website support, live browser scraping, reader screenshotting, login/cookie forwarding, CAPTCHA/paywall/DRM bypass, watermark removal, OCR, inpainting, public host or bot hosting service. Source origins/paths/selectors and stable query identities must match a source the user is permitted to use. Unknown or changing identity mappings pause instead of silently substituting pages. Public access alone is not permission.

## Evidence and remaining first-run gates

Final local regression run: **148 tests, 145 passed, 3 explicit missing-dependency skips**. Setup JavaScript/static launcher checks, the token-free three-page demo and compile checks also passed. Offline execution covers real Pillow pixels, source fixture parsing/safety, actual SQLite durability and exports, injected-model integration, private routing, setup JavaScript and launcher static checks. Those checks do not establish pretrained recognition quality, native Windows execution or a live Discord connection.

This build environment lacked discord.py/aiohttp/Torch/Transformers/safetensors and ordinary package installation was unavailable through DNS. Two actual-SDK tests and one tiny actual OWLv2 API test therefore report explicit dependency skips locally; CI must execute the SDK gate after source publication. No pretrained weights or third-party comic art were downloaded. Real-model API smoke remains explicit/manual, and real accuracy evaluation requires a permitted labeled dataset that was not supplied.

The user must create/add/authorize the bot, install compatible local packages, explicitly prepare the model cache, configure a licensed source, accept rights/privacy consent, keep the PC running, and test in the configured Discord server. No app was created, no token requested, no bot connected, no remote implementation commit claimed by the local build, and no separate ZIP/output artifact published.

## Migration from the own 0.1 prototype

0.1 was manual-only; this is a new automatic engine, not a relabeling of manual reference comparison or attribution fields. Use a fresh project/data folder and schema-version-2 nonsecret settings; there is no silent 0.1 SQLite migration. Keep previous data separately if needed. Existing originals are never inferred to be licensed or uploaded automatically.

Review details, reproducible commands, explicit skips and source-integrity gates are in `TESTING.md`. `SOURCE-MANIFEST.sha256` is the exact text source publication list; release clearance and remote publication remain the release owner's responsibility.
