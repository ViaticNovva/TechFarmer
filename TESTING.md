# Validation and release gates — TechFarmer 0.4.0

Canonical source: https://github.com/ViaticNovva/TechFarmer. This report distinguishes implementation, executed tests, injected fixtures and unverified deployment/recognition. No live app, bot connection, credentials, third-party artwork collection or remote Git push was performed while building the source.

## v0.4 compatibility evidence — 2026-10-25

**Only local Python 3.12.15 is available. No native 3.13/3.14/3.15, Windows install, actual Discord SDK/aiohttp, Torch/Transformers/safetensors, package download, model download, live Discord connection or remote Actions result is claimed.** Python 3.15.0 is a stable release (2026-10-09), not a prerelease; accepting its interpreter is not proof of package/runtime compatibility.

The source accepts standard CPython 3.11–3.15. New-runtime discovery prefers 3.15 and prints actual versions, while existing supported `.venv` is preserved. Tests simulate 3.11–3.15 accepted / 3.10 and 3.16 rejected, free-threaded/PyPy refusal, Windows candidate order, quoted Unicode paths, missing/broken environments, stable-wheel failures, actual-import failures, token non-logging, whitelist menu input, direct settings/backups and v0.3 source-update/rollback preservation. **Injected subprocess/import outcomes are policy tests, not actual future interpreter/wheel proof.** One separate subprocess executes the stdlib-only menu under the actual local interpreter from another space/Unicode path; a native Windows CMD case is explicit and skipped locally.

Executed targeted checkpoints so far:

- Initial bootstrap target: **12 tests in 6.797s: 11 passed, 1 native-Windows skip**.
- Combined compatibility/vision-cache/v0.3-upgrade/dependency-sync target: **22 tests in 18.806s: 21 passed, 1 native-Windows skip**. Actual on-disk source update/rollback kept byte-for-byte private settings/data/export/cache/venv/backups. Package processes were injected sinks, not real pip installs.
- Pre-freeze compatibility/packaging target: **25 tests in 25.406s: 24 passed, 1 native-Windows skip**. Includes create-only manifest source packaging, private-file exclusion and prerelease/yanked/non-wheel plan rejection. The first YAML parse caught an unquoted `--only-binary=:all:` scalar; folded YAML commands fixed it, and Node + PyYAML structural validation then passed.
- **One final sequential offline run:** **280 tests in 462.319s: 269 passed, 9 explicitly skipped, 2 teardown errors, no assertion failures**. It is **NOT a green full suite**. `test_atomic_replace_failure_before_and_after_is_rolled_back` and `test_final_verification_failure_rolls_back_source_and_manifest` each raised `OSError: [Errno 5] Input/output error` while fixture cleanup unlinked a staged `UPDATE-WINDOWS.cmd` on this FUSE workspace. Their assertion paths completed without a recorded failure, but cleanup errors still make the run unsuccessful; no production durability/check was weakened. The real process-crash/recovery case passed in this run. Log: `.runtime/v04-validation-50be4a1b69b94b188574d60b704b9a7a/full-tests.log`; full trace/count ledger: the same folder's `summary.json`. Failed fixtures are preserved under their own UUIDs, not swept with a runtime-folder deletion.
- The 9 full-run skips are **5 actual Discord SDK tests, 1 actual OWLv2 API test, 2 unavailable filesystem link tests and 1 native-Windows CMD test**. Missing real SDK/vision is never treated as passing those capabilities.
- **Post-full independent-review fixes:** updater's redirected import child now forces `-X utf8`; all CLI/updater/CMD gates reject alpha/beta/rc interpreters and the probe retains releaselevel/serial, while diagnostics retain preview qualifiers. Stable final CPython 3.15 remains accepted. Required Actions setup explicitly disables prerelease interpreters. Their **27-case targeted run passed 26, skipped only 1 native-Windows case, in 24.744s**, with **0 failures/errors**. It includes an actual local 3.12 redirected Unicode-output subprocess plus simulated preview/3.15 policy checks—not a native Windows/3.15 claim. Logs: `post-review-targeted.log` and `post-review-targeted.json` in the same owned validation folder. The final source contains **282 tests**. No second full run was used to chase a green result.
- Final Python parsing, Node setup/menu/workflow static checks and PyYAML workflow structure checks passed. Actual local base/vision preflight commands both returned **2**, honestly reporting missing Discord/aiohttp/vision packages and local Pillow 12.3.0 outside the new Pillow 13.0.0 requirement. No package install occurred; these failures are evidence of fail-closed diagnostics, not successful dependency compatibility.
- The **actual updater from the immutable v0.3 ZIP** accepted the new complete same-path source manifest/archive **in memory**; no network, disk archive or source update occurred during that compatibility check. Its original manifest remains `d233adaec7f1febe3daf94902b93f81463e702307fe9aa95c22bac2811f4a66e`. This complements, not replaces, the targeted physical v0.3→v0.4 update/rollback/data-preservation fixture.
- Independent read-only review separately reported **158 memory-only passes in 10.118s**, no skips and no write/network/subprocess audit violations, plus Node checks. That review found the two post-full compatibility defects above. Final frozen-byte clearance remains a separate reviewer/release-owner check; no GitHub publication/run is asserted.

### Metadata-backed candidates, with unresolved native closure

| Component | Verified public fact | What is still not proved here |
| --- | --- | --- |
| discord.py 2.7.1 | Stable 2026-03-03, Python >=3.8, py3-none-any wheel | Real SDK behavior on 3.15 |
| BeautifulSoup 4.15.0 | Stable 2026-06-07, Python >=3.7, py3-none-any wheel | Full application runtime on 3.15 |
| Pillow 13.0.0 | Stable 2026-10-16, Python >=3.11, 3.15 classifier | Exact Windows cp315 wheel and runtime not locally verified |
| aiohttp 3.14.5 | Current stable release verified | Exact cp315 wheel/transitive native closure unverified; no forced multidict/tokenizers major |
| Torch 2.14.1 | Stable 2026-09-30, Python >=3.10; complete 24-file wheel list has cp310–cp314/cp314t, **no cp315 or abi3** | No currently verified stable 3.15 vision wheel set; not full 3.15 support |
| safetensors 0.8.0 | Stable 2026-06-09, Python >=3.10, cp310-abi3-win_amd64 wheel | ABI metadata cannot replace missing Torch or actual integration tests |
| Transformers 4.57.6 | Existing pinned stable release 2026-01-16 retained | Exact historical dependency/API metadata and 3.15 integration not certified; no blind 5.x upgrade |

Public metadata references: <https://pypi.org/project/discord.py/>, <https://pypi.org/project/beautifulsoup4/>, <https://pypi.org/project/pillow/>, <https://pypi.org/project/aiohttp/>, <https://pypi.org/project/torch/2.14.1/>, <https://pypi.org/project/safetensors/>, <https://pypi.org/project/transformers/>, <https://docs.python.org/3.15/c-api/stable.html#stable-application-binary-interfaces>, <https://peps.python.org/pep-0790/>. These are metadata/schedule observations, **not execution evidence**. No packages/models were downloaded for this research or local build.

### Required real CI and source-package gates

`.github/workflows/ci.yml` covers actual standard **3.11–3.15 × Windows/Linux** on main push, PR and manual dispatch. Every required base job verifies source first, resolves/installs binary wheels, runs `pip check`, imports actual SDK/API and executes offline tests. Missing SDK is a hard failure (`TECH_FARMER_REQUIRE_SDK=1`), and **3.15 is not continue-on-error**. A manual `vision_api` selection additionally requires actual optional imports and tiny random-weight OWLv2 API execution (`TECH_FARMER_REQUIRE_VISION=1`); missing wheels or API fail, never skip-green. Its current 3.15 Torch blocker is expected to remain visible. No pretrained model or artwork is fetched by the workflow.

The source-only artifact job needs successful base jobs plus successful-or-not-selected vision jobs. `always()` plus explicit result checks allow default base-only packaging when optional vision is skipped; failures still block it. Only trusted **main push/manual** contexts package, never PRs. Packaging rechecks source bytes and uses the exact updater/manifest allowlist, not a recursive directory archive. Private files/models cannot enter the package; destination overwrite is refused. No release/tag or repository-write job exists. This is workflow **configuration**, not evidence that a remote run/package already exists or that Actions hosts the bot.

All 44 existing publishable payload paths are retained; the v0.3 exact path-contract SHA-256 is `aa36b80b1c89b904b8f9be166e4fd3bd8baa501e1e8e77bb44799f3b04ebaf25` (sorted names plus LF, not the content manifest). No new source-helper path or broad updater permission was introduced. Cleanup manifest/reasons are in RELEASE-NOTES; source/data/output/extension deletion is not fabricated.

## Recorded v0.3 candidate execution — 2026-10-25

The **one sequential full run** of `python -m unittest discover -s tests -v` ran **259 tests in 365.158 seconds**: 250 passed, 8 explicitly skipped, and **one updater crash test failed and also raised a teardown error**. Unittest reported `FAILED (failures=1, errors=1, skipped=8)`: both unsuccessful records belong to the same test, not two distinct failed tests. This was **not a green full suite**. The log is local/ignored `.runtime/v03-full-tests.log`.

The failing case was `test_real_process_crash_is_recovered_without_network_and_bot_cannot_start_mid_update`. Its child returned 1 rather than intentional crash exit 73, after the updater reported a failed update with previous source/manifest restored. The intended crash/recovery assertions therefore did not complete in this run. Cleanup separately raised `OSError: [Errno 5] Input/output error` while unlinking the fixture's staged `UPDATE-WINDOWS.cmd`. The workspace is FUSE; these observations do not prove a production overwrite bug, but they also do **not** verify this final full-run crash case. No production durability/hash/rollback checks were weakened and no repeated full run was used to hide the result.

The 8 skips were **five actual discord.py SDK tests**, **one actual tiny OWLv2 API test**, and **two real symlink/hardlink updater tests** because this filesystem cannot create those links. All other full-run cases—including actual SQLite migration/native files, backup-failure preservation, source-host approval, no-model collection, exact pixels, storage receipts/unknown outcomes and the other disk-backed updater cases—passed.

Separate, deliberately retained evidence:

- Core storage target: **9/9 passed** on real local files/SQLite before the final revocation regression was added.
- New workflow target: **12/12 passed in 21.307s**, before one additional backup-failure case (which passed in the full run).
- Earlier updater physical suite: **48 tests: 42 passed, 4 errors, 2 platform skips**. Errors included safe rollback/needs-attention outcomes and a child-process timeout; the existing FUSE failures remain part of the ledger, not converted into passes.
- A distinct three-case updater target passed **3/3 in 103.087s**: cancellation/rollback/retry, removed-source restoration, and actual `os._exit(73)` process-crash recovery. Only the test child timeout changed from 20 to 120 seconds for slow filesystem staging; exit/recovery/hash assertions were unchanged.
- Another three-case updater target passed **3/3 in 16.086s**: directory-entry syncing, explicit dependency-failure/source rollback with `.venv` preservation, and unsafe-journal refusal. These targeted successes do not erase the earlier/full-run failures or certify power-loss durability.
- **Final necessary changes after the full run:** optional dependency sync was corrected to inherit the operator's normal approved pip/index/proxy configuration, and storage consent can now be revoked after global rights consent is off. Their **three targeted real-file tests passed 3/3 in 11.389s**. Package execution was an injected sink, not a real pip/network install. The final source contains 260 tests; no second full run is claimed.

Setup JavaScript/launcher static checks and parsing every Python source passed. The authored pipeline demo and final source-integrity verification are separate from the above suites. No claimed test connects Discord, downloads artwork/model weights or executes a real package install.

Independent read-only review reproduced the stale source-approval, cross-issue pagination, empty-source updater and event-loop lock defects and verified their fixes. It separately exercised source/model geometry, owner/guild/no-fallback routes, SDK-free storage outcomes, consent-generation races and pip argument/environment handling under write/network-blocking audit hooks. Those memory-only reviews are **not independent on-disk rollback evidence**. Final release clearance requires the reviewer's before/after check of the exact frozen manifest. Native Windows, real SDK/TLS/source fetching, remote CI, pretrained inference and character accuracy remain unverified here.

## Environment and missing capabilities

Local Python **3.12.15**, Pillow **12.3.0**, BeautifulSoup **4.15.0** were available. `discord`, `aiohttp`, `torch`, `transformers`, `safetensors` and `httpx` were absent. Prior ordinary package installation had failed DNS; no alternate registry, proxy, DNS or credential workaround was attempted. Consequently, local SDK/pretrained execution is not claimed. Skips must remain visible.

The historical v0.3 source targeted Python 3.11/3.12; v0.4 accepts the bounded 3.11–3.15 standard-interpreter range subject to the new real dependency/CI gates. Windows `.cmd` files and setup JavaScript were examined/executed only as **static/DOM-simulation checks on Linux**, not a native Windows install/start. Nothing in this report proves OAuth authorization, a Discord server install, live private attachments, GPU performance, real website compatibility or comic-character accuracy.

## Reproduce offline tests

From the repository root, with lightweight image/parser dependencies available:

```sh
python -m unittest discover -s tests -v
node tests/test_setup.cjs
python tools/demo.py
python -m compileall -q tech_farmer tools tests
python tools/verify_manifest.py
```

Tests generate all HTML/image/cache fixtures procedurally under their own project-relative folders and clean them afterward. The demo retains its own ignored `.runtime/demo-.../` originals, SQLite state, crops and provenance for inspection; it creates no publishable image, archive or external-site copy. Never commit `.runtime/`, private data, exports, real configuration or cache files.

### What the executed fixture tests establish

- Native Pillow crop dimensions/pixels, aspect and asymmetric side controls, shape alpha masks, unchanged source/reference hashes, preserved originals and separate reframe edits.
- True reference flow into the matcher invocation: jobs freeze multiple identity references from a single profile; framing references are separate. Model tests use private injected processor/model primitives to check `query_images`, `query_pixel_values`, image-guided inference calls, post-processing, scale-back, tiled coordinates, NMS/reference aggregation, safe loads, cancellation and explicit detection budgets. **These doubles are not detection accuracy evidence.**
- Missing real model dependencies/cache fail closed, before page fetching, without fabricated matching or automatic download.
- Actual SQLite persistence: consent/owner scope, source-page dedupe, candidate state, pause/resume/restart, cancellation, partial failures, preserved completed pages, honest discovery incompleteness and quotas.
- Safety regressions: stable unsigned seed persistence, denied secret/credential queries, control transaction races, revocation/cancellation while slow readiness/image operations wait, source identity changes, and quota-limited reframing that preserves the candidate/original.
- Exact final PNG byte exports and batching/oversize policy. Public Discord provenance excludes source locations; local provenance retains only permitted unsigned routing identities. No per-export eight-crop total cap.
- Safe source transport with self-authored response streams, public-address pinning, redirect/origin/path constraints, no credentials/cookies/referrer/proxy forwarding, robots/access/challenge refusal, bounded readers and explicit partial discovery. Production connector construction is checked using a transport/SDK primitive double, not actual HTTP.
- Setup strict IDs, conservative OAuth scopes/permissions, no token fields/background network/browser storage, nonsecret JSON downloads, explicit source rights and Windows launcher static checks.

The end-to-end `tools/demo.py` uses the **actual SourceClient, Store, JobRunner and crop/export implementation**, synthetic reader pagination/HTML/images, and an obviously fixed-answer injected `FixtureMatcher` from `tests/helpers.py`. It tests the automatic URL-to-export workflow without a token or network. The fixed answer is never selectable from production configuration or commands.

## Actual SDK gate (required in lightweight CI)

```sh
python -m pip install -r requirements.txt
python -c "import discord, aiohttp; from tech_farmer.bot import FarmerClient, FarmerGroup; print(discord.__version__)"
TECH_FARMER_REQUIRE_SDK=1 python -m unittest discover -s tests -v
```

On PowerShell set `$env:TECH_FARMER_REQUIRE_SDK='1'` before running the test command; on Windows cmd use `set TECH_FARMER_REQUIRE_SDK=1`.

`tests/test_sdk.py` imports real discord.py, serializes the actual application command tree and button views, and constructs real `discord.File`/mention settings for mocked private response sinks. It does not log in. When SDK packages are absent locally, these five tests explicitly skip. They cover the real command tree (including optional attachment/profile options and the 25-command group bound), startup guild synchronization, bound approval buttons/owner checks, unchanged interaction-attachment validation, effective channel permissions from real SDK objects, and persistent sends with real `discord.File`/suppressed mentions. HTTP/response sinks are mocked; no login occurs. **Setting `TECH_FARMER_REQUIRE_SDK=1` makes absence a hard failure**, never a silently skipped CI success.

The current workflow is described in the v0.4 gate above. It consolidates the former v0.3 Linux 3.11/3.12 and Windows 3.12 updater-only jobs into a required full Windows/Linux 3.11–3.15 matrix. Native Windows Python/CMD evidence exists only after those cases actually run; Linux static checks are not a native manual installation. Remote CI results must be checked after complete canonical publication; this report is not a passing run.

## Optional real OWLv2 checks — separate evidence levels

1. **Tiny random-weight API smoke:** with vision dependencies installed, `tests/test_vision_framing.py` builds a tiny actual OWLv2 configuration and tokenizer from self-authored files, invokes the actual processor/model/postprocessor, and checks shape/API compatibility. It does not download pretrained weights and is not recognition evidence. Without the optional dependencies this one test explicitly skips.
2. **Pretrained API execution:** explicit user-controlled commands below download the pinned safetensors model and run it locally against generated shapes. Successful execution still would not prove comic-character identity accuracy.
3. **Real recognition evaluation:** not performed. It needs user-permitted representative references/pages, manually labeled ground truth, false-positive/miss accounting, crop quality review, environment details and benchmark methodology. No accuracy percentage is invented.

```sh
python -m pip install -r requirements-vision.txt
python -m unittest discover -s tests -p test_vision_framing.py -v
python -m tech_farmer download-model --model-cache .cache/models --device cpu
python -m tech_farmer doctor --model-cache .cache/models --device cpu
python tools/model_smoke.py
```

The former v0.3 heavyweight pretrained CI option is replaced by the v0.4 no-model `vision_api` gate. The explicit **local** pretrained commands and `tools/model_smoke.py` remain available; they may require several GB. No local pretrained execution is asserted here. Imports/doctor/load stay local-only; only explicit `download-model` may fetch pinned weights after dependency preflight.

## Review and browser-publication integrity

Independent review identified and drove fixes for credential-bearing seed persistence, safety-control lock availability, control race/cancellation resurrection, reframe/storage quotas, repeated-image occurrence loss, implicit page identity on reorder, IPv6 site-local address filtering and incremental bounded native-crop encoding before quota checks. Review is separate from recognition/deployment validation. Publish only after the parent/release owner accepts the final reviewed text manifest.

`SOURCE-MANIFEST.sha256` is the source publication list and byte-level integrity contract. `tools/verify_manifest.py` checks every publishable UTF-8/LF text file (excluding the manifest itself and runtime/private/Git data). CI rejects missing/extra/changed source. A reviewer may regenerate with `--write` **after** reviewing changes; CI never regenerates. The manifest is not a signature or guarantee of model accuracy/security beyond the reviewed bytes. Publish the CI workflow last during browser-based multi-file publication, to avoid useless partial-tree runs. A local staging hash is not a remote commit.
