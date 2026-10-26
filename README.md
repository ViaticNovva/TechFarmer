# TechFarmer 0.4.0

**Only canonical source: https://github.com/ViaticNovva/TechFarmer. Native Python, not a hosted app.**

**One primary entrypoint: `START-WINDOWS.cmd`.** GitHub hosts the project; it does **not** execute a CMD file on your PC. Download/extract the **whole** project first. The steps below consolidate this same repository in place—no repository recreation or history rewrite.

Give the bot a permitted **source link**. Add an optional image/reference profile when you want local character-guided matching. Without a reference, it explicitly **collects native pages—not a particular character—and needs no model install**. Supported static readers and direct PNG/JPEG/WebP links no longer need hand-written selector JSON. Unknown, ambiguous, protected and JavaScript-only readers stop rather than guessing or bypassing access.

## Start here: one launcher, first successful pull

1. Download the **complete repository** from the canonical link above (GitHub **Code → Download ZIP**) and extract it into a **new local, non-shared, non-synced folder you control**. Do not run inside a ZIP preview. Do not move/delete an existing installation or cloud data. A README-only/partial tree is not an application.
2. Install approved **standard CPython 3.15** if needed, then double-click **`START-WINDOWS.cmd`**. Choose **1 — Setup/repair base**. New installs prefer 3.15; an existing supported `.venv` keeps its actual interpreter, displayed on screen. Compatible wheels and imports must pass; no compiler, nightly, administrator or package-index workaround is attempted.
3. Choose **2 — Configure IDs**. Enter your real Discord **User, Server and Application IDs** in the local wizard. It validates and saves nonsecret settings directly—**no HTML download or JSON move is needed**. Create/authorize your bot yourself in Discord's official Developer Portal if necessary; the wizard prints the fixed official authorization link. Discord authorization is required: this is not zero-config.
4. Choose **3 — Start bot**. Enter the token **only at the hidden local console prompt**, or use `DISCORD_BOT_TOKEN` in this process's environment. Never put it in JSON, HTML, chat, a URL, source or command arguments. Keep this PC/console running.
5. Read `/farmer consent`, then accept with `accept:true`. Choose an **existing private regular text channel** with `/farmer storage channel:#chosen-private accept:true`. Only use permitted material. The bot does not create a channel, change permissions or assume privacy. Storage is optional for local-only files.
6. Run **`/farmer pull url:<your permitted source link>`**. No reference means native-page collection, **not character recognition**, and no model install. Check `/farmer status`. If prompted, `/farmer approve job:<id>` displays the exact external image origins; its button approves only that displayed set. When complete, **`/farmer save job:<id> mode:collection`** stores results only in your selected private channel. Repeat to continue pending batches; `/farmer receipts` shows outcomes.

**Python 3.15 status:** all local entrypoints accept the standard interpreter. The base package set is a **compatibility candidate**, not a verified native 3.15 claim until its binary resolution, imports and required Windows/Linux CI jobs pass. Only Python **3.12.15** was available locally. Optional vision currently has a verified blocker: stable **Torch 2.14.1 has no cp315 wheel** (metadata checked 2026-10-25). No-reference collection does not use Torch. See diagnostics below; nothing silently downgrades your interpreter.

Controls/errors/previews remain ephemeral. Stored messages/files are visible to **everyone with channel access, including permitted roles and administrators**, and subject to Discord limits/retention—not a guaranteed permanent archive or owner-only privacy. Keep local originals, exports and receipts as backups. No bot login or server post was performed while developing this release.

### Want a character, rather than all pages?

This is optional. In the same menu choose **5 — Install optional matching packages**, then **6 — Prepare optional model** only after actual imports pass. Packages/model files can be large; model download asks you to type `DOWNLOAD`. Existing working standard 3.11–3.14 vision environments can be kept. On 3.15 the current stable-Torch wheel gap fails closed; a future stable compatible wheel set still has to pass the same wheel/import/API checks. There is no permanent blanket version ban, nightly or silent 3.12 fallback.

Use `/farmer pull url:<link> reference:<your image upload>` or `profile:Hero` for views of **one** character. Review with `/farmer review`, accept uncertain matches with `/farmer decide`, then `/farmer save job:<id> mode:accepted`. Missing references, dependencies or weights never fabricate results or silently become collection. References are never automatically posted to storage.

## Folder map — what to open, what to keep

- **`START-WINDOWS.cmd`**: normal setup/start/update menu. **`README.md` / `START-HERE.html`**: instructions, not an application running on GitHub.
- **`tech_farmer/`**: bot and local image/source pipeline. **`tools/`**: updater, integrity checker and explicit diagnostic demos. **`tests/`**: regression/safety coverage, not junk.
- **`.github/workflows/ci.yml`**: required Python checks and trusted successful-run source artifact; see the Actions/manual-run section below.
- **`data/`, `exports/`, `.cache/`, `.venv/`, `.runtime/` and local settings/backups**: your local working state. Keep it private; never publish or delete it as repository cleanup. Compatibility CMD shortcuts are retained for old installs, not extra required steps.

## Update an existing installation

### Complete v0.3 folder: stop → update → reopen menu → restart

1. Stop the bot with **Ctrl+C** and close every other TechFarmer bot/pull window. Keep a full backup of the old installation.
2. Run its **`UPDATE-WINDOWS.cmd`**, choose Update and confirm **STOPPED**. A complete v0.3 installation can update in place: v0.4 uses **exactly the same 44 managed source paths**, so the old updater's allowlist still accepts it. The old updater itself needs its existing 3.11/3.12 interpreter for this one upgrade; it cannot magically start under 3.15.
3. Close the old menu/updater, reopen **`START-WINDOWS.cmd`**, choose **1** to sync/repair base dependencies, then **3** to start. Check `/farmer version` says **0.4.0**. Existing working `.venv` versions are kept and printed; updating source does not switch Python. All settings, jobs, originals, references, exports, caches and backups stay local.

**If only 3.15 is available and the old updater rejects it:** use the clean-folder first-upgrade route below. Do **not** overwrite tracked updater files or replace a v0.3 manifest to hide a mismatch. v0.4's independent `UPDATE-WINDOWS.cmd`/`tools/update.py` supports 3.15 and remains usable for future recovery, even if application packages are missing.

`/farmer version` displays version information; `/farmer update` and `python -m tech_farmer update` **check only** fixed canonical GitHub main. They never install or execute Python from Discord. Future source updates can use **menu 4** or the independent updater shortcut. Terminal equivalent: `python tools/update.py --update` (first unrecorded upgrade also needs `--confirm-stopped`). No arbitrary URL, branch, shell hook or alternate repository is accepted. `/farmer settings` and `/farmer reload` change only whitelisted numeric limits—not code, identity, token, device or paths.

Re-inviting an existing bot is **usually unnecessary** when it already has `bot applications.commands` and the needed channel permissions: restart syncs guild slash commands. Verify your actual application/server/scopes/permissions if commands or storage are unavailable; this build cannot verify your server offline.

### First upgrade: incomplete/older folder, or changing interpreter

1. Stop **every** older process and preserve the entire old folder. Old pre-lock processes cannot be detected by the new lock.
2. Obtain complete reviewed canonical source in a **new local sibling folder**, not over the old folder. Extract all files, including `tools/`, `tech_farmer/`, requirements and `SOURCE-MANIFEST.sha256`.
3. Before first start, copy nonsecret `tech-farmer-settings.json`, optional `source-profiles.json`, `data/`, `exports/` and optional `.cache/` only into corresponding **absent** destinations. Keep the original copies; do not merge populated data folders, copy tokens, copy an old `.venv` or copy old source over new source. Existing backup files can remain safely in the original installation.
4. Double-click the new folder's **`START-WINDOWS.cmd`**, choose **1**, then **3** (existing settings are kept). Schema 1/2 settings and compatible v0.2 SQLite state migrate with exact backups; IDs, frozen references and decisions survive. Incompatible prototype databases are preserved with a clear **NOT migrated** notice and a new database. Confirm private-channel storage again if needed.

For a complete reviewed **v0.2** folder whose old manifest does **not** contain the updater paths, the historical optional bootstrap remains: copy only the canonical `tools/update.py` and `UPDATE-WINDOWS.cmd` to the same absent paths, retain the old manifest, stop all old processes, then run the updater with explicit **STOPPED** confirmation. That exception is not permission to overwrite manifest-tracked files. Unrecognized/partial old trees are preserved and staged into a new sibling; no data migration is guessed.

The updater verifies every managed source hash, preserves private data and journals under ignored `.runtime/update/`. Bot, CLI, installers and updater share an OS lock; the idle menu does not hold it. Interrupted updates fail closed until recovery/rollback. `python tools/update.py --rollback` restores **source only**, not user data or installed packages. Optional `--sync-base` requires this project's `.venv`; it checks stable wheels, `pip check` and actual imports, reporting dependency failures separately. Never delete `.runtime/update/` to bypass recovery. SHA-256 checks integrity, not an independent publisher signature.

## What changed

0.4 adds the single stdlib menu, direct local ID wizard, explicit actual interpreter display, standard CPython 3.11–3.15 gates, binary/import dependency checks and required Windows/Linux GitHub Actions coverage. It preserves the 0.3 link-first `/pull`, explicitly labeled no-model collection, bounded source setup/host approval, owner-confirmed persistent private-channel storage, per-output send receipts, schema backups, a fixed-canonical updater, Python CLI and safe numeric `/farmer settings` + `/farmer reload`. `/harvest` and its detailed crop controls remain compatible. The local image-guided OWLv2 pipeline from 0.2 remains real; there is no production fixture/color/hash detector or fake recognition fallback.

### Honest model and site limits

- Model: [`google/owlv2-base-patch16-ensemble`](https://huggingface.co/google/owlv2-base-patch16-ensemble), Apache-2.0, immutable revision `410d70ced26e95c344915c2f10f4ecf967f2cde4`. See [official image-guided API documentation](https://huggingface.co/docs/transformers/model_doc/owlv2).
- Uses `Owlv2Processor(images=page, query_images=reference, return_tensors="pt")`, `image_guided_detection`, and image-guided post-processing. Multi-reference and overlapping tiled inference merge same-page overlaps with NMS and retain reference indices. Exact duplicate original bytes across pages share detection work while retaining page provenance.
- Generic one-shot object matching is **not verified comic-character identity or re-identification**. Raw scores are ranking signals, not calibrated accuracy percentages. Review false positives and missed appearances. No supplied character/reference ground truth was available, so there is no recognition accuracy claim.
- Model input copies are bounded/resized for inference; native coordinate mapping returns to the original EXIF-oriented page. **Final crops are taken from original-resolution pixels**, never model thumbnails. No invented upscale detail.
- A framing reference guides **aspect only**, not pose, composition transfer, face/body segmentation or anatomy. Face, upper-body and full-body presets have no validated semantic backend and are held with **no crop** until you choose supported framing. A fixed top percentage is never represented as anatomical detection.
- Direct raster images and explicit static-HTML reader profiles are supported. Unknown layouts, JavaScript-only readers and blocked sources stop/report partial progress. No third-party comic site—including the previously mentioned example—is verified compatible or licensed. No third-party artwork was downloaded for tests.

## Rights and privacy first

Use only pages/references you own, have licensed, or have explicit permission to download and edit. **Public access is not a license.** Personal use and ephemeral Discord messages do not establish rights. This software does not bypass login, paywalls, DRM, CAPTCHA, robots restrictions or other access controls. It does not forward cookies, authorization, referrers or proxy settings; it does not screenshot a reader or guess missing numbered pages.

Source requests are limited to inspected, bounded origins/path roots, or an advanced explicit profile. External image origins require this job's exact displayed approval before image fetching; changed proposals invalidate old buttons/CLI hashes. DNS addresses are validated and pinned into the actual connector; redirects are rechecked and private/reserved/local addresses are refused. Reader selection uses recognized static containers, not every image in arbitrary HTML. Advanced profiles retain selectors/routing fields but image hosts are narrowed to actually discovered origins and approved per job.

Originals, frozen references, SQLite jobs, edits and full provenance live locally in ignored `data/` and `exports/`. Private replies, uploaded reference attachments, requested contact sheets and export batches are **hosted by Discord**, not end-to-end encrypted or local-only. No user images are uploaded to Hugging Face. Only explicit model preparation downloads model files. Source URLs never appear in status/preview messages, unsafe exception logs or the Discord provenance file.

Only unsigned stable source seeds are durable. URL credentials/fragments, undeclared queries and secret/signature/token-like query keys are rejected **before job persistence**. Explicitly declared nonsecret chapter/page routing keys can be retained privately to resume a reader. Discovered signed image URLs remain only in bounded, expiring source-client memory; they are not saved in state/provenance. Restart rediscovery obtains fresh image references.

Do not commit your settings, source profiles, token, data, exports or model cache. The repository is source text only. `.gitignore` is a guard, not a substitute for reviewing a commit.

## Local setup, Python choice and diagnostics

The **only primary Windows entrypoint is `START-WINDOWS.cmd`**. It locates its own folder from any working directory, quotes spaces/Unicode paths and disables delayed expansion for `!` in paths. Its menu/bootstrap uses the Python standard library before application dependencies are installed. No username, absolute user path, UAC request, policy change or automatic file move is hard-coded.

Menu: **1** base setup/repair · **2** ID wizard · **3** start bot · **4** source update/check/rollback · **5** optional vision packages · **6** explicit model preparation · **7** local instructions · **8** local data/status/base diagnostics · **0** exit. The old INSTALL/INSTALL-VISION/DOWNLOAD-MODEL files are retained compatibility shortcuts to this same menu, not extra setup requirements. `UPDATE-WINDOWS.cmd` remains an independent stdlib recovery entrypoint.

In the [official Discord Developer Portal](https://discord.com/developers/applications), create your own application/bot if needed; keep privileged intents off. Enable Discord Developer Mode to copy real numeric IDs. The wizard prints an authorization link requesting only `bot applications.commands`, permissions **35840** (View Channel, Send Messages, Attach Files), and your configured server. No Administrator/Manage Server/message-content permission. Numeric ID validation does not prove the app/server exists or that you have installation rights. An optional saved storage ID is merely a suggestion until `/farmer storage` consent and fresh permission checks.

`START-HERE.html` is now **optional instructions/advanced helpers**. It has no token input, background network requests, browser storage or automatic authorization. Its older JSON/source-profile download helpers remain available for deliberate advanced/manual use; do not overwrite existing settings with them. Normal setup uses the wizard directly.

### Choose Python 3.15 (explicitly, without losing a working environment)

- A **new** Windows install tries standard 3.15, then 3.14, 3.13, 3.12, 3.11, then a supported PATH Python. The actual chosen interpreter is always printed. It never calls an older interpreter “3.15”. Stable, **final** standard CPython **3.11–3.15** is the bounded target range. Alpha/beta/rc interpreters, 3.16+, free-threaded builds and PyPy are refused; a preview is never displayed/accepted as an unqualified stable 3.15.0.
- An **existing working `.venv` wins**, even if you later install 3.15. Source updates/setup do not delete, relocate or recreate it. `version`, the menu, preflights and updater display actual runtime versions. To keep a working vision setup, simply keep its existing interpreter.
- To intentionally switch to 3.15, install approved standard 3.15, use the **new-folder first-upgrade route above**, and do not copy the old `.venv`. The new menu prefers 3.15 and creates a separate environment. Keep the entire old installation and its data/working environment as rollback evidence. Never run both copies against shared data.
- If `.venv` exists but is broken/unsupported, setup stops instead of overwriting it. Preserve it and follow that clean-folder route, or repair the original interpreter at its original path with your approved tools. A copied/moved venv is not portable. Do not blindly delete `.venv` or the whole `.runtime`.

### Dependency and capability diagnostics

Base requirements are **discord.py 2.7.1**, **aiohttp >=3.14.5,<4**, **Pillow 13.0.0**, **BeautifulSoup 4.15.0**. These are stable metadata-backed candidates, **not a fully verified 3.15 native dependency closure**. Pillow 13 advertises 3.15; universal discord.py/BeautifulSoup wheels do not prove aiohttp's transitive native wheels or runtime behavior. Required CI must resolve/install actual compatible binary wheels, run `pip check`, import the real SDK and execute tests on Windows/Linux 3.15. A failed wheel gate is a failed gate, never proof of support.

Menu setup uses only your existing approved pip/index settings and `--only-binary=:all:`. A dry-run wheel report rejects prerelease/yanked/non-wheel plans **before installation**, including a pre-release policy inherited from pip configuration. Then installed packages undergo `pip check` and actual version/import/API preflight. No `--ignore-requires-python`, source compiler, nightly, alternate index, proxy/DNS/policy workaround or silent interpreter fallback is used. Raw package/index error bodies are not logged; a failure may mean network/index access, missing wheels or conflicting packages, not simply “bad Python”. Stop and obtain approval if organizational policy blocks access.

For diagnostics use menu **8**, or `.venv` Python with `-m tech_farmer preflight` (base) / `-m tech_farmer vision-preflight` (optional imports; no weights) and `-m pip check`. Install/repair success is reported **only after actual imports pass**. A partial pip install is not rolled back; source rollback never restores Python packages. Preserve the environment and data, resolve access/conflicts using approved tools, then retry explicitly. Do not share tokens, private index credentials or raw logs.

Optional vision keeps **Transformers ==4.57.6**, **Torch >=2.2,<3**, **safetensors >=0.4.3,<1**. Bounds are not a tested full lock. Verified stable **Torch 2.14.1** has Windows cp310–cp314 wheels and no cp315/abi3 wheel. Safetensors 0.8.0 has a cp310-abi3 Windows wheel, but that cannot supply missing Torch. Exact Transformers 4.57.6/transitive behavior on 3.15 was not verified. Stable ABI/metadata is not runtime proof. A future legitimate stable wheel set can pass the same preflight; do not force a newer tokenizers major, Transformers 5.x or a nightly. If torchvision is present it must match Torch. CPU remains default; accelerators need working local hardware/backends.

`doctor` checks local dependencies/device/cache, not inference RAM or recognition quality. `download-model` checks actual dependencies **before** attempting the pinned optional download. Startup/matching use `local_files_only=True`, `trust_remote_code=False`, `token=False` and safetensors-only weights. Failure pauses matching with an explicit diagnostic, never a fake detector or implicit download. Native-page collection without references remains independent of vision. Model cache/RAM can need several GB and are outside the data/export budget.

### macOS/Linux or terminal use

From the extracted repository (choose your approved actual interpreter explicitly):

```sh
python3.15 -m tech_farmer menu
# Or individual equivalent local actions:
python3.15 -m tech_farmer install-base
.venv/bin/python -m tech_farmer setup
.venv/bin/python -m tech_farmer preflight
.venv/bin/python -m tech_farmer run
# Optional only after compatible stable vision packages are available:
.venv/bin/python -m tech_farmer install-vision
.venv/bin/python -m tech_farmer vision-preflight
.venv/bin/python -m tech_farmer download-model
.venv/bin/python -m tech_farmer doctor
```

On Windows replace `.venv/bin/python` with `.venv\Scripts\python.exe`. Commands are rooted in their own installation; a relative `--reference` is resolved from your invocation directory before anchoring state. Absolute-file launch also works from another folder: `python -I "/path/to/TechFarmer/tech_farmer/__main__.py" menu` (replace this illustrative path with your own). Do not change real paths to someone else's username. Hidden token input is mandatory when using a console; if it cannot be hidden the program refuses instead of echoing it. No token is saved, and no `.env` loader exists.

## Advanced matching commands and optional framing

All slash success/error/defer responses, previews and `/export` attachments are ephemeral, with mentions suppressed. **`/save` is different:** only its explicitly confirmed private destination receives persistent result messages. There is no auto-save or current-channel fallback. Only guild-specific commands are synced on startup. Buttons recheck owner/guild and expire after ten minutes; a fresh slash command creates fresh controls. Source-approval buttons bind the exact displayed proposal. Jobs retain no Discord Interaction/token and do not depend on a 15-minute interaction lifetime.

```text
/farmer help
/farmer consent
/farmer consent accept:true
/farmer reference profile:Hero image:<your reference>
/farmer reference profile:Hero image:<another view of the SAME character>
/farmer reference profile:Hero image:<optional framing image> role:framing
/farmer profiles
/farmer doctor
/farmer harvest url:<permitted series URL> profile:Hero scope:series preset:detected
/farmer status
/farmer status job:<j_...>
/farmer control job:<j_...> operation:pause
/farmer control job:<j_...> operation:resume
/farmer control job:<j_...> operation:cancel
/farmer review job:<j_...> offset:0
/farmer decide candidate:<c_...> decision:accepted
/farmer export job:<j_...> batch:0
```

A profile holds up to eight identity references **for one character** and one optional framing reference. Use separate profiles for separate characters. A job copies and hashes its own immutable reference snapshot; later uploads do not change that job. Profile replacement/deletion is intentionally not a silent operation: create a new profile for another set. Only your interaction's resolved original Discord attachment may be fetched **as a reference upload**. Source links go through the separate safe source engine and are not restricted to Discord CDN attachments. Python CLI references are explicit local paths, never converted into CDN URLs.

Review shows six numbered candidates at a time, with Accept/Reject buttons and next-page navigation. Scores are not identity confidence percentages. Unsupported semantic/panel/reference requests can produce a review entry without a crop; accepting such an entry is blocked. Its preview shows the source page with the model's detected region outlined, not an invented anatomical crop. You can explicitly reframe it, for example:

```text
/farmer reframe candidate:<c_...> preset:detected aspect:4:5 context:20,0,80,0 align:left
```

`/farmer consent accept:false` revokes consent and requests active jobs pause. Status, cancellation and revocation remain available while an image operation or model readiness check is waiting. Revocation is checked again before uploading generated attachments. It does not delete local files or previously hosted Discord attachments; stop the bot and manage those copies yourself when desired.

### Crop controls

The harvest and optional reframe commands share:

- `preset`: `detected`/`tight` use the detected region; `reference` uses the framing reference's aspect; `panel` needs a containing white-gutter heuristic panel; `face`/`upper_body`/`full_body` are unsupported semantic requests held without crop, not approximated.
- `aspect`: positive `W:H` or numeric ratio, supported range 1:20 to 20:1; omitted preserves region aspect. At most pixel-rounding differences are possible. Impossible constraints block the crop rather than resize the page.
- `context`: extra **pixels** in `left,top,right,bottom` order. `trim`: remove pixels in the same order. Do not pad and trim the same side simultaneously; enter the net instruction. Trimmed edges are hard boundaries; aspect expansion cannot put them back.
- `align`: center, left, right, top, bottom, or a corner. Directional context is genuinely asymmetric. Boundary clipping/forced alignment adds review warnings.
- `fit:contain` preserves the requested target/context where feasible; `fit:cover` explicitly permits aspect trimming. Neither upscales or invents detail.
- `shape`: rectangle, ellipse or circle. Ellipse/circle adds an alpha mask at native resolution; circle requires 1:1. No destructive change to the original.
- `threshold`: default 0.15, a raw uncalibrated model-score cutoff. Lower can increase false positives; higher can miss appearances.

Panel splitting is a conservative white-gutter heuristic, not a learned panel segmentation guarantee. No containing panel means no panel crop, not a whole-page fallback.

## Source compatibility and optional advanced profiles

Automatic setup supports direct still PNG/JPEG/WebP (including extensionless raster URLs) and recognized static reader/chapter containers such as `.reader`, `.comic-reader`, `.reading-content`, `#readerarea`, `[data-reader]`, `.chapters`, `.chapter-list` and `[data-chapter-list]`. Lazy image attributes/srcset are bounded by the safe source engine. Pagination stays within inspected series/chapter boundaries; absent page numbers/URLs are never invented. The simple auto mode accepts positive `page`/`paged` query pagination; other semantic query-only chapters need an advanced profile. Website-wide indices, ambiguous containers, external seed redirects, protected/JS-only pages and sources beyond inspection limits fail with an actionable code. This is not universal site support. No supplied third-party comic example or real artwork was downloaded or verified.

The optional HTML helper covers one static source. For advanced cases edit `source-profiles.json` locally, never commit a real private profile. Illustrative **non-working placeholder** (replace with your permitted origin and selectors; do not treat this as authorization):

```json
{
  "authorized": true,
  "allow_http": false,
  "profiles": [{
    "name": "my-permitted-library",
    "page_origins": ["https://your-library.example"],
    "image_origins": ["https://your-images.example"],
    "page_paths": ["/series/", "/chapter/"],
    "image_paths": ["/pages/"],
    "chapter_selector": ".chapter-list a[href]",
    "reader_selector": ".comic-reader img",
    "series_next_selector": "a[data-series-next][href]",
    "reader_next_selector": "a[data-page-next][href]",
    "page_number_attribute": "data-page-number",
    "identity_query_params": [],
    "image_identity_query_params": [],
    "page_number_param": null
  }]
}
```

Origins are exact; no wildcard hosts. Empty `image_origins` inherits `page_origins`. Allow only the origin/path roots you actually need. A direct image URL must fit an approved image scope. Only links inside the declared chapter/reader selectors are followed. Declared next links, lazy attributes and supported `srcset` candidates are parsed; absent pages are never guessed.

Query-only routing needs explicit nonsecret `identity_query_params` (for example `chapter`) and optionally a positive-integer `page_number_param`. Distinct chapters that otherwise collapse to the same redacted URL are reported as ambiguous, not merged silently. Signed seed URLs/credential-like queries are refused even if listed. Use an unsigned stable reader URL; internally discovered signed image fetch URLs are memory-only. For query-routed images, declare stable nonsecret `image_identity_query_params` (for example `id`). Implicit page identities are not based on discovery position. Without a semantic image-key declaration, the exact image URL is represented only by an opaque hash; rotating signatures therefore count as changed identities. If any known page identity disappears on rediscovery, the job pauses rather than substituting another image. Start a new job with the correct identity declaration (or restore the unchanged source); no raw signed query is saved. Approved public HTTP is an explicit advanced opt-in (`allow_http`), not the setup default and never a private-network exception.

Blocked access, robots denial, unsafe DNS/redirects, unknown layouts, response/pixel limits and discovery caps are explicit failures/partial states. A source profile is a parser configuration—not evidence of rights or proven compatibility.

## Durable states and resource pauses

The normal pipeline is `queued → discovering → matching → review_ready`. **`review_ready` means every discovered page was processed, not perfect identity or reviewed/accepted results.** `paused_source`, `review_partial`, `paused_budget`, `paused_model`, `paused_error`, `paused` and `paused_restart` are not completion. `/status` reports discovery completeness, page/candidate counts, failure counts and a safe diagnostic code; URLs are omitted.

- Successful pages/candidates remain durable. Resume re-discovers the seed safely, refreshes opaque fetch IDs, merges stable page identities, and retries unfinished/failed pages. Previously completed pages are not re-inferred. A missing previously discovered page is recorded as a failure, not quietly dropped.
- Discovery cursors are in-memory only. The worker automatically follows per-batch cursors until complete or a total resource/access limit. After process restart, active/queued jobs become `paused_restart`; explicitly resume them. Cancellation is durable and does not delete evidence; cancelled jobs do not resume.
- Pause/cancel checks happen between requests/model calls and at safe page boundaries. A running native Torch operation cannot be preempted instantly. Only one job performs inference at once; CPU work and SQLite operations are off the event loop. Source references use bounded activity renewal; an actually expired/idle source must be re-discovered, not guessed.
- Exact-byte repeated originals avoid repeated inference and retain page aliases locally. Same-page tile/reference overlaps use configurable NMS. No perceptual cross-page near-duplicate merging, perfect identity deduplication, or silent first-N results are claimed.

Nonsecret settings defaults (pause jobs, wait for a safe boundary, then use `/farmer settings name:<limit> value:<integer>` or `/farmer reload`; identity/token/device/paths cannot be changed from Discord). Existing jobs keep frozen inference options; new jobs use changed tile/detection settings. Discovery/storage run budgets can be raised before resume. The page exposes common budgets:

| Setting | Default | When exceeded |
| --- | ---: | --- |
| `pages_per_run` | 200 | Explicit pause; resume processes the next unfinished pages |
| `candidates_per_run` | 1000 | Page remains pending; resume, or increase if one page alone exceeds it |
| `detections_per_page` | 200 | Detection-budget pause; no silent truncated hit list; maximum configurable 1000 |
| `storage_mib` | 2048 | Data/exports pause; archive local data or increase budget; model cache excluded |
| `page_crop_mib` | 128 | Bounded encoded crops retained per page; pause keeps page pending, configurable 16–512 MiB |
| `discovery_pages` | 2000 | Incomplete source; increase and rediscover/resume |
| `discovery_issues` | 200 | Incomplete source; increase and rediscover/resume |
| `tile_size` / `max_tiles` | 960 / 24 | Whole-page plus bounded tile coverage; coarsening is warned, not omitted-page success |

Candidate count is checked before rendering. Native PNGs are encoded incrementally against remaining disk space and a separate per-page memory budget; exceeding either keeps the whole page pending, never truncates the detections or downscales finals. Use narrower framing, or carefully raise `page_crop_mib` and available RAM where needed; the default is a resource pause, not completion. Allow additional RAM/disk headroom for decoded originals, model tensors, references, SQLite and atomic-write staging.

Source advanced limits also bound HTML documents, pending references, robots data, redirects and response bytes. Per-batch defaults are 250 pages/64 documents; the worker follows their cursors automatically. Total document default is 10000. Limits are declared in `SourceLimits`; never raise them blindly. Default source image download is 20 MiB. Decoder accepts still PNG/JPEG/WebP only, at most 20 megapixels and 16000 pixels per side; references at most 8 MiB. GIF/animation has no image-edit support even if a source transport recognizes its signature. No silent downscale bypasses input limits.

## Python CLI (no Discord login for local collection)

Run from the project folder with your base virtual environment. `python -m tech_farmer setup --owner-id <id> --guild-id <id> --application-id <id>` saves only nonsecret settings; storage ID is optional. Examples:

```sh
python -m tech_farmer version
python -m tech_farmer pull --url https://your-permitted.example/book/chapter --confirm-rights
python -m tech_farmer status --job j_...
# If prompted, use the exact proposal hash printed beside the origin list:
python -m tech_farmer approve --job j_... --accept --proposal <displayed-hash>
python -m tech_farmer export --job j_... --selection collection
# Optional model-backed matching; repeat --reference for SAME-character views:
python -m tech_farmer pull --url https://your-permitted.example/book/chapter --reference references/hero.png
```

The example domain is a placeholder, not a verified source/license. Local reference paths must be small regular PNG/JPEG/WebP files; they are not downloaded as attachment URLs. The same scope/mode/aspect/context/trim/align/shape controls are available as flags. `consent --revoke` stops new work; `control --job <id> --operation resume` rediscover/resumes unfinished pages. CLI, bot and updater cannot run concurrently in one install. A local collection never posts to Discord; `/farmer save` is the explicit server-posting route.

## Persistent server storage and receipts

`/farmer storage channel:<text channel> accept:true` checks an existing **same-guild, regular text** destination; public `@everyone`-visible, news, thread, DM, forum and foreign-guild destinations are rejected. The owner must retain access; the bot needs View Channel, Send Messages and Attach Files. No Administrator or privileged intents are requested. Guild roles, channel overwrites, owner membership and bot membership are fetched fresh through REST **before every batch and resumed save**; missing permission data fails closed. Channel files are visible to other authorized members/roles/admins. Permissions are never altered to make a channel qualify.

`/farmer save job:<id> mode:accepted` uploads only accepted crop versions. `mode:collection` is explicit and available only for complete no-reference page/panel jobs (rejected/pending edits are excluded). No references or private provenance URLs are auto-uploaded. Save handles at most three size-bounded batches per invocation; repeat the same command for remaining outputs. Native originals/exports stay local; oversized PNGs stay local, unchanged, with an explanation. No downscale or external/public fallback exists.

`/farmer receipts job:<id>` shows message links and per-output hashes/outcomes. A durable intent is recorded **before** send; confirmed message IDs deduplicate the same output in that destination across restart. An interrupted/failed/cancelled or uncertain send becomes **unknown**, never blindly retried. Inspect the actual channel yourself; a missing receipt confirmation is not proof a message was never posted. Discord's SDK handles rate limits; this implementation does not invent nonce enforcement the SDK cannot guarantee. This is not a guaranteed exactly-once delivery protocol or permanent file archive.

`/farmer storage accept:false` revokes only future storage; `/farmer consent accept:false` revokes all new image work. Controls, errors, origin lists and resized previews stay ephemeral. Already sent/in-flight messages cannot be recalled by revocation. Keep your local originals, native exports, SQLite receipts and backups.

## Native exports and provenance

By default only accepted candidates export. Complete no-reference jobs require the explicit `/farmer export ... mode:collection` or CLI `--selection collection`. Each final PNG is written unchanged under relative `exports/<job>/`, with `provenance-local.json` (private unsigned seed/issue/page locations, reference and original/crop hashes, model revision, effective inference settings and crop recipe) and `provenance-discord.json` (no source URLs). Reference indices are explicitly tied to the frozen **identity-only** ID list; framing references are separate.

`/farmer export` sends one batch per fresh interaction. Batches respect the lower of reported Discord limits and a conservative **8 MiB total**, with at most eight files per batch. Every accepted PNG remains local, even if no Discord batch fits it. A single oversized PNG stays local with an explicit notice: it is never downscaled or placed on a public host. Contact-sheet previews alone are resized. Request batch 1, 2, etc. as needed; there is no eight-crop overall export cap. Large provenance may also remain local if it exceeds the interaction budget.

Reframing creates a separate edited crop and resets its decision to pending. Original encoded bytes/hash and earlier crop files are preserved. Local data may contain sensitive source/character information; protect your device and backup policy accordingly.

## Tests, source integrity and release status

See `TESTING.md` and `RELEASE-NOTES.md`. No secrets, live Discord connection, licensed/commercial art or model weights are required for the procedural tests/demo. Nothing under `.runtime/`, `data/`, `exports/` or `.cache/` belongs in a source commit.

```sh
python -m unittest discover -s tests -v
node tests/test_setup.cjs
python tools/demo.py
python tools/verify_manifest.py
```

The demo uses the **real safe source adapter, SQLite worker and pixel/export code**, but an obviously injected fixed-answer matcher over self-authored shapes. It proves plumbing, not recognition. SDK and actual OWLv2 API tests have explicit missing-dependency skips offline. Those skips are **not** runtime support evidence. Required GitHub base CI fails on missing actual SDK/imports. The opt-in vision API job fails on missing vision packages/wheels/API instead of silently skipping; it uses tiny random weights, not a pretrained model.

### GitHub Actions Python workflow (checks, not bot hosting)

The same canonical repository contains **`.github/workflows/ci.yml`**. It runs on **push to main**, **pull request** and **manual workflow_dispatch**—no schedule. Source hashes/required entrypoints are checked **before package installation**. Required base jobs cover **standard CPython 3.11–3.15 on Windows and Linux**, with binary-only wheel resolution, `pip check`, actual SDK imports/serialization, offline unit/integration/updater tests, launcher/JS checks and the procedural demo. There is **no `continue-on-error`** on the 3.15 gate. Missing 3.15 dependencies mean failed validation, not green compatibility.

After the complete reviewed source is actually published, open the canonical repository → **Actions → Reviewed Python compatibility and offline tests → Run workflow**. Leave **`vision_api` unchecked** for base checks; explicitly check it for optional stable-wheel/import/tiny OWLv2 API tests on 3.12/3.15 Windows/Linux. The 3.15 vision job is expected to expose the current Torch wheel blocker; future passing metadata alone is insufficient without imports/API. Optional vision packages can be large, but the workflow **never downloads pretrained weights or artwork**. The previous heavyweight pretrained CI option is replaced by this no-model API gate; `tools/model_smoke.py` and the explicit local model commands remain available separately.

The workflow has read-only `contents` permission, no persisted checkout credentials, no bot token/secret, no Discord login/post, no arbitrary source fetch, no public release/tag/write step and no artifact containing private data/models. It does **not** host a 24/7 bot. Your PC still runs the bot. Workflow configuration in source is **not** a remotely executed or passing workflow; check actual GitHub run results after publication.

A **TechFarmer-verified-source** artifact is emitted only after all required base checks and any selected optional vision checks pass on a trusted **main push/manual run**, never on PR builds or failed checks. It is a complete manifest-listed text-source ZIP plus the manifest, not a runtime/data/model backup or public release. The fixed packaging command `python tools/verify_manifest.py --archive` re-verifies bytes, refuses an existing destination, and includes only allowlisted manifest paths—not recursive folder contents. Download it from that successful Actions run if needed; a partial repository or documentation-only ZIP remains insufficient. No source artifact was built or uploaded remotely while preparing this update.

`SOURCE-MANIFEST.sha256` lists the exact reviewed publishable text files. CI verifies every hash and fails on missing/extra/changed source. Only after review should a maintainer run `python tools/verify_manifest.py --write`. Upload the manifest and source, then the CI workflow last when publishing through the browser. A local staging tree or passing local test is not a remote GitHub commit, a successful CI run, a live bot test, or an accuracy benchmark.
