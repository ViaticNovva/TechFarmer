# TechFarmer v0.4.0 compatibility and local-setup update

Canonical repository only: https://github.com/ViaticNovva/TechFarmer. This is source preparation, **not proof of a completed GitHub publication or CI run**. No user PC, OneDrive/output data, token, Discord server, artwork or model was accessed/changed. The immutable delivered v0.3 ZIP and earlier output/test copies remain unchanged.

## One entrypoint, bounded Python compatibility

- **START-WINDOWS.cmd** opens one stdlib-only menu: base setup/repair, direct nonsecret ID wizard, bot start, source updater/recovery, optional vision packages/model, instructions and local diagnostics. Own-folder anchoring and argument-list subprocesses handle spaces/Unicode and avoid command strings from menu input. Tokens remain hidden prompt/process environment only, never saved or logged; a non-hidden prompt is refused.
- Local runtime/launcher/updater gates accept **standard CPython 3.11–3.15**, not alpha/beta/rc previews, unknown future 3.16/free-threaded/PyPy. Only final interpreter releases are accepted. New installs prefer 3.15. Existing working `.venv` is kept with its actual version; broken environments stop rather than being deleted/rebuilt. README describes an explicit preserved-old-folder route to choose 3.15.
- Stable base candidates: discord.py 2.7.1, aiohttp >=3.14.5,<4, Pillow 13.0.0, BeautifulSoup 4.15.0. Setup checks an actual binary-only pip plan, rejects prerelease/yanked/non-wheel plans, then requires `pip check` and real version/import/application API checks. It never forces an index, nightly, compiler, `--ignore-requires-python` or silent older-Python fallback. Optional updater dependency sync uses the same gates; source rollback is not a package rollback.
- **3.15 native base compatibility remains unverified locally.** Python 3.12.15 is the only local interpreter; simulated guards are not native execution. Required Windows/Linux 3.15 CI is an actual install/import/test gate, not an allowed-to-fail green badge.
- **3.15 vision is not claimed supported:** verified stable Torch 2.14.1 has no cp315 wheel. No-reference collection remains model-independent. Matching fails closed with a capability-specific warning; a future compatible stable wheel set may pass the same preflight, rather than being permanently Python-version-banned. Transformers remains 4.57.6, safetensors-only loading/pinned model revision remain unchanged, and dependency checks precede any explicit model preparation.

## GitHub Actions Python workflow

`.github/workflows/ci.yml` is reused for push to main, pull requests and manual dispatch. Hash verification comes first. Required base matrix: standard Python **3.11, 3.12, 3.13, 3.14, 3.15 × Windows/Linux**; actual SDK imports cannot skip to green. Explicit opt-in `vision_api` requires stable wheels/imports/tiny actual OWLv2 execution and downloads **no pretrained model**. The old heavyweight cloud-model option is replaced; existing local `tools/model_smoke.py` is retained.

A complete **source-only** artifact is produced only after required base checks and any selected vision check succeed on **trusted main push/manual** runs. The optional job's normal skipped state does not block base-only packaging; PRs/failed checks do not produce a trusted package. `tools/verify_manifest.py --archive` packages only verified allowlisted manifest files plus the manifest, refuses overwrites and excludes user settings/data/.runtime/.venv/cache/models. No token, live Discord action, public release/tag or contents-write permission. Actions checks source; it does not run a 24/7 bot. Manual steps and remote-run caveats are in README/START-HERE.

## Cleanup manifest — deliberate retention, no fabricated deletions

**Removed project-source paths: NONE.** All **44 existing payload paths + SOURCE-MANIFEST.sha256** are retained; no new source path requires an old-updater allowlist bootstrap. Shared installer logic is consolidated into the existing Python menu, and HTML/manual launchers are demoted to optional/compatibility roles instead of breaking old installations.

| Retained paths | Why they are not unneeded |
| --- | --- |
| `START-WINDOWS.cmd`, `tech_farmer/__main__.py`, `tech_farmer/__init__.py` | Primary menu/CLI, stdlib bootstrap and consistent runtime/version handling |
| `INSTALL-WINDOWS.cmd`, `INSTALL-VISION-WINDOWS.cmd`, `DOWNLOAD-MODEL-WINDOWS.cmd` | Thin compatibility shortcuts; INSTALL is also required by old updater manifests |
| `START-HERE.html` | Optional instructions/advanced helper, existing tested UX and old updater required path |
| `UPDATE-WINDOWS.cmd`, `tools/update.py`, `tools/verify_manifest.py`, `SOURCE-MANIFEST.sha256` | Independent recovery, strict path/hash contract, rollback/backups and source-only packaging |
| `requirements.txt`, `requirements-vision.txt`, all existing `tech_farmer/*.py` | Runtime/core source and separate optional model capability; no security/pipeline module removed |
| All existing `tests/*`, `tools/demo.py`, `tools/model_smoke.py` | Regression, SDK, geometry, source, storage, updater and actual vision test coverage |
| `.github/workflows/ci.yml`, `.gitignore`, `README.md`, `TESTING.md`, `RELEASE-NOTES.md` | Required workflow, private-data exclusions, safe instructions and historical validation ledger |

**User settings, jobs/databases, images, references, exports, caches, backups, `.venv`, `.runtime`, old output copies and the separate browser extension are not cleanup candidates and were not deleted.** OneDrive deletion is unavailable; no alternate route is attempted. Test cleanup is restricted to each test's newly created UUID fixture, never the whole runtime folder.

## Migration and preserved behavior

Complete v0.3 source updates remain compatible with its exact allowlist and retain settings/private files/working environment, with source rollback. The old v0.3 updater still needs its original supported 3.11/3.12 to perform that first update; a 3.15-only installation uses the documented new-folder route, not a disguised overwrite of tracked updater files. Historical v0.2 absent-updater bootstrap remains explicit. No source/data migration is guessed for an unknown partial installation.

All 0.3 behavior below remains: permitted links first; optional real local OWLv2 references; honest no-reference collection; user-defined framing; immutable originals/resume; owner+guild-only controls; selected private storage with fresh permission checks and durable receipts; exact displayed CDN approval; OS locks/integrity; no public fallback, secrets or fake face/character results. Existing bot installations usually need stop/update/restart rather than a new invite, but real permissions/scopes are not presumed.

See **TESTING.md** for executed current results, explicit skips/gaps and the unchanged historical v0.3 failures. Final frozen-byte independent review and canonical GitHub publication remain release-owner steps.

## Historical v0.3.0 release notes (retained)

Canonical repository only: https://github.com/ViaticNovva/TechFarmer. Native Python; no hosted app, account change, live Discord post, artwork/model download or separate output ZIP was made during this build.

## Link first, reference optional

- New `/farmer pull url:<link>` defaults to **collect-pages** with no identity claim and no model prerequisite. Explicit `collect-panels` uses a labeled white-gutter heuristic. Optional `reference:<upload>` or `profile:<name>` selects real local image-guided OWLv2 matching; invalid/missing matching inputs never silently become collection.
- Bounded source inspection handles supported direct still raster images and recognized static reader/chapter lists without manual selectors. It retains safe DNS/socket binding, robots, redirect/access controls and resource limits. External image hosts require per-job approval of the **displayed immutable proposal**. Changed hosts/scope invalidate prior confirmation; cross-chapter pagination cannot borrow another issue's pages.
- `/farmer harvest` stays available with detailed crop controls. Same-character multi-reference snapshots, raw model scores, native pixels, originals, separate edits and local provenance remain preserved. A framing image guides aspect only; anatomy/pose is not fabricated.

## Explicit persistent private-channel storage

- `/farmer storage channel:<private text channel> accept:true` persists the owner-selected same-guild destination and its separate consent. Fresh REST channel/role/member checks run before each batch and resumed save. Public, news, thread, DM, forum, foreign-guild, missing-access and missing View/Send/Attach cases fail closed. No channel creation, permission mutation or current-channel fallback.
- `/farmer save job:<id> mode:accepted` sends only accepted matches. `mode:collection` explicitly sends complete no-reference results, never references. Three bounded batches per request; sent/already stored/unknown/oversized/remaining counts and the exact continuation command are reported.
- Durable per-output receipts deduplicate confirmed sends across restart. Uncertain/failed/interrupted sends are **unknown, not blindly retried**. SDK rate limits are respected without inventing unsupported nonce enforcement. Oversized native PNGs remain local, unchanged; no public/external fallback.
- Commands/errors/previews stay ephemeral. Channel messages/files are persistent but visible to every authorized member/role/admin and subject to Discord retention/limits—not a permanent archive or owner-only privacy. Keep local originals/exports/receipts.

## Setup, migration and updates

Base install/start/status/page collection no longer requires a source profile, vision packages or model weights. The local setup page has no token input/network/background storage; the optional storage ID is a suggestion until bot-side consent/validation. Tokens remain hidden-console/process-environment only; privileged intents/Admin are not requested. Restart syncs updated guild slash commands.

`UPDATE-WINDOWS.cmd` and `tools/update.py` use only the fixed canonical repository, verify the complete nonempty source manifest, preserve private configuration/data/cache/backups, and journal source updates under ignored `.runtime/update/`. Bot/CLI/updater share a runtime lock; the first old-version upgrade also needs local stopped-process acknowledgment. Discord `/farmer version` and `/farmer update` show version/check information only, never run Python or update code. First-time full-source upgrade, future single updater and rollback limitations are in README.

Settings schemas 1/2 migrate with exact backups. Compatible v0.2 SQLite state receives a backup and additive columns; IDs, references and review state survive. An incompatible v0.1/prototype database remains unchanged with an explicit **NOT migrated** notice and a new database. `/settings` and `/reload` only change documented numeric limits, never owner/guild/application/token/device/paths.

## Evidence and honest limits

See `TESTING.md` for the final sequential run, targeted results, earlier FUSE filesystem failures, exact skips and independent review. Real image/parser/SQLite/native-file fixture tests are distinct from injected model/network sinks. Actual discord.py/aiohttp imports and command serialization are required in lightweight CI; unavailable local SDK/model dependencies are not claimed tested. Native Windows, live Discord/TLS, third-party sites, pretrained inference and character accuracy remain unverified.

Unknown, ambiguous, JavaScript-only, authentication/paywall/CAPTCHA/DRM/robots-blocked sources are unsupported; no access workaround or universal-site claim exists. No supplied comic example or commercial art was fetched. OWLv2 is not verified character re-identification; scores are not accuracy percentages. Only previews are resized.

`SOURCE-MANIFEST.sha256` is the exact reviewed publishable text list, not a publisher signature or remote commit. Independent frozen-byte review and complete canonical publication remain the release owner's responsibility. The previous delivered v0.2 output copy is unchanged.

