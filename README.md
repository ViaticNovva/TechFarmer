# TechFarmer 0.2.0

**Canonical project: https://github.com/ViaticNovva/TechFarmer — no alternate app, public site or download archive.**

A native Python Discord bot that takes a permitted series, chapter or page URL plus a saved single-character reference profile, discovers the chosen source, runs **local image-guided OWLv2 inference**, automatically produces native-pixel crop candidates, and lets the configured owner review/export them privately. Manual per-page uploads and per-result coordinate commands are not the core workflow.

This is an implementation for a user-run, one-server test—not a hosted bot, verified character recognizer, universal website scraper, or already-connected Discord application. You create, authorize and run your own bot. **Keep your PC and process running.**

## What changed from 0.1

0.1 was a manual crop prototype; its references did not drive matching and its source fields were attribution-only. 0.2 adds an actual source adapter, reference-conditioned pretrained model backend, durable SQLite work queue, automatic crop planning, review, restart/resume and native exports. There is no production dummy/color/hash detector and no automatic fallback when the model is unavailable. Procedural test fixtures are explicitly injected only by tests/demo code.

### Honest model and site limits

- Model: [`google/owlv2-base-patch16-ensemble`](https://huggingface.co/google/owlv2-base-patch16-ensemble), Apache-2.0, immutable revision `410d70ced26e95c344915c2f10f4ecf967f2cde4`. See [official image-guided API documentation](https://huggingface.co/docs/transformers/model_doc/owlv2).
- Uses `Owlv2Processor(images=page, query_images=reference, return_tensors="pt")`, `image_guided_detection`, and image-guided post-processing. Multi-reference and overlapping tiled inference merge same-page overlaps with NMS and retain reference indices. Exact duplicate original bytes across pages share detection work while retaining page provenance.
- Generic one-shot object matching is **not verified comic-character identity or re-identification**. Raw scores are ranking signals, not calibrated accuracy percentages. Review false positives and missed appearances. No supplied character/reference ground truth was available, so there is no recognition accuracy claim.
- Model input copies are bounded/resized for inference; native coordinate mapping returns to the original EXIF-oriented page. **Final crops are taken from original-resolution pixels**, never model thumbnails. No invented upscale detail.
- A framing reference guides **aspect only**, not pose, composition transfer, face/body segmentation or anatomy. Face, upper-body and full-body presets have no validated semantic backend and are held with **no crop** until you choose supported framing. A fixed top percentage is never represented as anatomical detection.
- Direct raster images and explicit static-HTML reader profiles are supported. Unknown layouts, JavaScript-only readers and blocked sources stop/Lite jobs, edits and full provenance live locally in ignored `data/` and `exports/`. Private replies, uploaded reference attachments, requested contact sheets and export batches are **hosted by Discord**, not end-to-end encrypted or local-only. No user images are uploaded to Hugging Face. Only explicit model preparation downloads model files. Source URLs never appear in status/preview messages, unsafe exception logs or the Discord provenance file.

Only unsigned stable source seeds are durable. URL credentials/fragments, undeclared queries and secret/signature/token-like query keys are rejected **before job persistence**. Explicitly declared nonsecret chapter/page routing keys can be retained privately to resume a reader. Discovered signed image URLs remain only in bounded, expiring source-client memory; they are not saved in state/provenance. Restart rediscovery obtains fresh image references.

Do not commit your settings, source profiles, token, data, exports or model cache. The repository is source text only. `.gitignore` is a guard, not a substitute for reviewing a commit.

## Easy local setup

1. Get the source from this canonical repository. Use **Python 3.11 or 3.12**. Python 3.12.15 was available for offline tests; newer Python/accelerator combinations are not claimed verified.
2. Open `START-HERE.html` locally. It makes no background requests. In Discord's official Developer Portal, create your own application/bot; keep privileged intents off. Enter your real Application ID, User ID and test Server ID in the page. Nothing is prefilled with another person's IDs.
3. Click **Add to Discord** and approve it yourself. The inviter needs server application-install rights. The link requests `bot applications.commands`, permissions `35840` (View Channel, Send Messages, Attach Files), and your configured guild. No Administrator/Manage Server/message-content permission. Numeric ID validation does not verify the app/server exists.
4. Save `tech-farmer-settings.json` and `source-profiles.json` beside the page. The page's source-profile helper requires explicit rights confirmation. Set the actual supported reader/chapter selectors for your permitted site; this does not prove its compatibility.
5. Windows: run `INSTALL-WINDOWS.cmd`, then `INSTALL-VISION-WINDOWS.cmd`, then `DOWNLOAD-MODEL-WINDOWS.cmd`. These are distinct intentional network steps: package installation, vision packages, pinned model preparation. No secret is needed for model download.
6. Run `START-WINDOWS.cmd`. Enter your bot token **only at its hidden local terminal prompt**, never in HTML, JSON, chat, command arguments, source or a URL. `DISCORD_BOT_TOKEN` in the local process environment is also supported; no `.env` loader is provided. No token is saved. Only the configured owner in the configured guild can use commands/buttons; DMs have no fallback.
7. Use `/farmer consent` and explicitly accept before image work. Keep the console/PC running; stop with Ctrl+C. The shutdown waits for an in-flight model call to return safely.

If device/organization policy or network restrictions block an install or source, stop and get approval. Do not change proxy, DNS, credential or package-registry settings to evade restrictions. There is no live account action in the installer.

### macOS/Linux (or terminal setup on Windows)

Run from the repository folder; activation is optional when using the virtual environment's Python directly:

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m pip install -r requirements-vision.txt
.venv/bin/python -m tech_farmer download-model
.venv/bin/python -m tech_farmer doctor
.venv/bin/python -m tech_farmer validate-config
.venv/bin/python -m tech_farmer run
```

On Windows replace `.venv/bin/python` with `.venv\Scripts\python.exe`. The doctor/download commands use saved nonsecret cache/device settings if present, otherwise `.cache/models` and `cpu`. Explicit overrides: `--model-cache .cache/modreport partiaels --device cpu`.

`requirements.txt` is lightweight. `requirements-vision.txt` pins Transformers 4.57.6, with Torch `>=2.2,<3` and safetensors `>=0.4.3,<1` compatibility ranges—not a tested full dependency lock in this offline environment. If torchvision is already installed, it must match your Torch build. CPU is the default; CUDA/MPS require working local hardware/backend support. Model cache plus runtime RAM may require several GB; performance is not benchmarked. The data/export budget does not include model cache size.

`doctor` is an offline dependency/cache preflight, not proof inference will fit RAM or run successfully. Startup/harvest always use `local_files_only=True`, `trust_remote_code=False`, `token=False`, and safetensors-only model loading. Missing dependencies/weights or failed inference pause jobs; they do not fabricate matches or trigger an implicit download.

## Private command workflow

All slash success/error/defer responses and attachments are ephemeral, with mentions suppressed. Only guild-specific commands are synced. Buttons recheck owner and guild and expire after ten minutes; a fresh slash command creates fresh controls. Jobs retain no Discord Interaction/token and do not depend on a 15-minute interaction lifetime.

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

A profile holds up to eight identity references **for one character** and one optional framing reference. Use separate profiles for separate characters. A job copies and hashes its own immutable reference snapshot; later uploads do not change that job. Profile replacement/deletion is intentionally not a silent operation: create a new profile for another set. Only your interaction's resolved original Discord attachment may be fetched.

Review shows six numbered candidates at a time, with Accept/Reject buttons and next-page navigation. Scores are not identity confidence percentages. Unsupported semantic/panel/reference requests can produce a review entry without a crop; accepting such an entry is blocked. Its preview shows the source page with the model's detected region outlined, not an invented anatomical crop. You can explicitly reframe it, for example:

```text
/farmer reframe candidate:<c_...> preset:detected aspect:4:5 context:20,0,80,0 align:left
```

`/farmer consent accept:false` revokes consent and requests active jobs pause. Status, cancellation and revocation remain available while an image operation or model readiness check is waiting. Revocation is checked again before uploading generated attachments. It does not delete local files or previously hosted Discord attachments; stop the bot and manage those copies yourself when desired.

### Crop controls

The harvest and optional reframe commands share:

- `preset`: `detected`/`tight` use the detected region; `reference` uses the framing reference's aspect; `panel` needs a containing white-gutter heuristic panel; `face`/`upper_body`/`full_body` are unsupported semantic requests held without crop, not approximated.
- `aspect`: positive `W:H` or numeric ratio, supported range 1:20 to 20:1; omitted preserves region aspect. At most pixel-rounding differences are possible. Impossible constraints block the crop rather than resize the page.
- `context`: extra **pixels** in `left,top,right,m the same side simultaneously; enter the net instruction. Trimmed edges are hard boundaries; aspect expansion cannot put them back.
- `align`: center, left, right, top, bottom, or a corner. Directional context is genuinely asymmetric. Boundary clipping/forced alignment adds review warnings.
- `fit:contain` preserves the requested target/context where feasible; `fit:cover` explicitly permits aspect trimming. Neither upscales or invents detail.
- `shape`: rectangle, ellipse or circle. Ellipse/circle adds an alpha mask at native resolution; circle requires 1:1. No destructive change to the original.
- `threshold`: default 0.15, a raw uncalibrated model-score cutoff. Lower can increase false positives; higher can miss appearances.

Panel splitting is a conservative white-gutter heuristic, not a learned panel segmentation guarantee. No containing panel means no panel crop, not a whole-page fallback.

## Explicit source profiles

The HTML helper covers one static source. For advanced cases edit `source-profiles.json` locally, never commit a real private profile. Illustrative **non-working placeholder** (replace with your permitted origin and selectors; do not treat this as authorization):

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

The normal pipeline is `queued → discovering → matching → review_ready`. **`review_ready` means every discovered page was processed, not perfect identity or reviewed/accepted results.** `paused_source`, `review_partial`, `paused_budget`, `paused_model`, `paused_error`, `paused` and `paused_restart` are not completion. `/status` reports discovery completeness, page/canbottom` order. `trim`: remove pixels in the same order. Ddidate counts, failure counts and a safe diagnostic code; URLs are omitted.

- Successful pages/candidates remain durable. Resume re-discovers the seed safely, refreshes opaque fetch IDs, merges stable page identities, and retries unfinished/failed pages. Previously completed pages are not re-inferred. A missing previously discovered page is recorded as a failure, not quietly dropped.
- Discovery cursors are in-memory only. The worker automatically follows per-batch cursors until complete or a total resource/access limit. After process restart, active/queued jobs become `paused_restart`; explicitly resume them. Cancellation is durable and does not delete evidence; cancelled jobs do not resume.
- Pause/cancel checks happen between requests/model calls and at safe page boundaries. A running native Torch operation cannot be preempted instantly. Only one job performs inference at once; CPU work and SQLite operations are off the event loop. Source references use bounded activity renewal; an actually expired/idle source must be re-discovered, not guessed.
- Exact-byte repeated originals avoid repeated inference and retain page aliases locally. Same-page tile/reference overlaps use configurable NMS. No perceptual cross-page near-duplicate merging, perfect identity deduplication, or silent first-N results are claimed.

Nonsecret settings defaults (restart to apply new settings; the page exposes the common budgets):

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

## Native exports and provenance

Only accepted candidates export. Each final PNG is written unchanged under relative `exports/<job>/`, with `provenance-local.json` (private unsigned seed/issue/page locations, reference and original/crop hashes, model revision, effective inference settings and crop recipe) and `provenance-discord.json` (no source URLs). Reference indices are explicitly tied to the frozen **identity-only** ID list; framing references are separate.

`/farmer export` sends one batch per fresh interaction. Batches respect the lower of reported Discord limito not pad and tril progress. No thirs and a conservative **8 MiB total**, with at most eight files per batch. Every accepted PNG remains local, even if no Discord batch fits it. A single oversized PNG stays local with an explicit notice: it is never downscaled or placed on a public host. Contact-sheet previews alone are resized. Request batch 1, 2, etc. as needed; there is no eight-crop overall export cap. Large provenance may also remain local if it exceeds the interaction budget.

Reframing creates a separate edited crop and resets its decision to pending. Original encoded bytes/hash and earlier crop files are preserved. Local data may contain sensitive source/character information; protect your device and backup policy accordingly.

## Tests, source integrity and release status

See `TESTING.md` and `RELEASE-NOTES.md`. No secrets, live Discord connection, licensed/commercial art or model weights are required for the procedural tests/demo. Nothing under `.runtime/`, `data/`, `exports/` or `.cache/` belongs in a source commit.

```sh
python -m unittest discover -s tests -v
node tests/test_setup.cjs
python tools/demo.py
python tools/verify_manifest.py
```

The demo uses the **real safe source adapter, SQLite worker and pixel/export code**, but an obviously injected fixed-answer matcher over self-authored shapes. It proves plumbing, not recognition. SDK and actual OWLv2 API tests have explicit missing-dependency skips offline. GitHub lightweight CI **requires real discord.py import/command serialization**, and uses read-only repository permissions. A real pretrained download/inference smoke is manual opt-in only; it never runs automatically on each text-file commit.

`SOURCE-MANIFEST.sha256` lists the exact reviewed publishable text files. CI verifies every hash and fails on missing/extra/changed source. Only after review should a maintainer run `python tools/verify_manifest.py --write`. Upload the manifest and source, then the CI workflow last when publishing through the browser. A local staging tree or passing local test is not a remote GitHub commit, a successful CI run, a live bot test, or an accuracy benchmark.
d-party comic site—including the previously mentioned example—is verified compatible or licensed. No third-party artwork was downloaded for tests.

## Rights and privacy first

Use only pages/references you own, have licensed, or have explicit permission to download and edit. **Public access is not a license.** Personal use and ephemeral Discord messages do not establish rights. This software does not bypass login, paywalls, DRM, CAPTCHA, robots restrictions or other access controls. It does not forward cookies, authorization, referrers or proxy settings; it does not screenshot a reader or guess missing numbered pages.

Source requests are limited to explicitly configured origins/path roots. DNS addresses are validated and pinned into the actual connector; redirects are rechecked and private/reserved/local addresses are refused. Reader image selection is scoped to configured selectors, not every image in arbitrary HTML.

Originals, frozen references, SQ
