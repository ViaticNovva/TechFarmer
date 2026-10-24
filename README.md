# TechFarmer

> **Status: v0.2 automatic-harvest rebuild is in development. This is not yet a working release.**

TechFarmer is being rebuilt as a private, owner-only Discord bot for one test server, backed by a local worker. The user manages the Discord app installation. This repository is the canonical home for builds and updates.

## Target workflow

1. Supply a series, chapter, or page URL, one or more character-identity references, and optionally a crop/framing reference.
2. Discover permitted issues and pages, then automatically detect candidate character appearances.
3. Apply user-directed framing, side, aspect ratio, and padding.
4. Review candidates privately and export at native pixel resolution.

The intended workflow does **not** require uploading every page or entering pixel coordinates for every result. The earlier manual prototype is not the completed automatic-harvest target.

## Development expectations

Implementation, model accuracy, and live-site compatibility will be reported honestly, including untested paths, failures, and limitations. A development commit is not a claim that the bot works end to end.

## Content and privacy boundaries

- Use only material the user owns, has licensed, or has permission to process.
- No paywall, DRM, authentication, or CAPTCHA bypass; no public redistribution.
- Private Discord uploads are allowed within the user's private workflow.
- Never commit tokens, credentials, actual local settings, source/reference images, harvested pages, private review data, exports, or model caches/weights. Keep them local and out of version control.

The initial repository scaffold documents the rebuild; runnable setup and verified capabilities will be added with the implementation.
