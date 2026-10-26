"""Private interaction routing. Jobs never keep an Interaction or its token."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from io import BytesIO
from pathlib import Path
import re
from urllib.parse import urlsplit

from . import __version__, REPOSITORY
from .config import LIMIT_FIELDS, SETTINGS_NAME, load_settings, set_limit
from .engine import RunLimits, crop_spec
from .exports import export_job
from .images import FORMATS, contact_sheet, decode
from .safety import DISCORD_HOSTS, LIMITS, UserError
from .store import CONSENT_TEXT, STORAGE_CONSENT_TEXT
from .sources import SourceError

HELP = (
    f"**TechFarmer {__version__} — a source link first**\n"
    "1. `/farmer consent accept:true` after reading the rights/privacy notice. Public access is not a license.\n"
    "2. `/farmer pull url:<permitted source>` collects native pages, with NO character identity claim and NO model requirement. "
    "Add `reference:<upload>` or `profile:Hero` for real local image-guided matching. Profiles freeze multiple views of ONE character.\n"
    "3. `/farmer status job:<id>`; if external image origins need approval, `/farmer approve job:<id>` shows the exact set, then `accept:true`. "
    "Supported static readers/direct images need no selector JSON; unknown/protected/JS-only sites stop.\n"
    "4. Matching: `/farmer review` then `/farmer decide` accepts uncertain candidates. `/farmer control` pauses/resumes/cancels.\n"
    "5. `/farmer storage channel:<private text channel> accept:true` selects persistent server storage after its notice. "
    "`/farmer save job:<id>` saves accepted matches; `mode:collection` explicitly saves a complete no-reference collection. "
    "No automatic uploads, reference uploads, permission changes or current-channel fallback.\n"
    "6. `/farmer version` and `/farmer update` check only. Stop the bot → UPDATE-WINDOWS.cmd → restart.\n"
    "Controls, errors and previews stay ephemeral. Channel results are visible to everyone with channel access, including roles/admins, "
    "and are subject to Discord retention/limits—not a guaranteed archive. Keep local originals/exports. "
    "OWLv2 scores are not identity accuracy; framing references guide aspect only. Keep this PC running."
)


def attachment_limit(interaction) -> int:
    cap = getattr(interaction, "filesize_limit", None)
    if type(cap) is not int or cap <= 0:
        cap = LIMITS.discord_bytes
    guild_cap = getattr(getattr(interaction, "guild", None), "filesize_limit", None)
    if type(guild_cap) is int and guild_cap > 0:
        cap = min(cap, guild_cap)
    return min(cap, LIMITS.discord_bytes)


def validate_attachment(interaction, attachment):
    import discord
    if not isinstance(attachment, discord.Attachment):
        raise UserError("Use the slash command's image attachment field.")
    supplied = (getattr(interaction, "data", None) or {}).get("resolved", {}).get("attachments", {}).get(str(attachment.id))
    if not isinstance(supplied, dict) or supplied.get("url") != attachment.url or supplied.get("filename") != attachment.filename or supplied.get("size") != attachment.size:
        raise UserError("Only attachments in this exact interaction can be downloaded.")
    if type(attachment.size) is not int or not 0 < attachment.size <= LIMITS.reference_bytes:
        raise UserError("References must be nonempty images of at most 8 MiB.")
    suffix, mime = Path(attachment.filename).suffix.lower(), attachment.content_type
    if mime not in {item[0] for item in FORMATS.values()} or suffix not in {suffix for item in FORMATS.values() for suffix in item[1]}:
        raise UserError("Use a PNG, JPEG or WebP image with its matching extension and content type.")
    try:
        url = urlsplit(attachment.url)
        match = re.fullmatch(r"/attachments/(\d{1,20})/(\d{1,20})/[^/]+", url.path)
        if (url.scheme != "https" or url.hostname not in DISCORD_HOSTS or url.username or url.password
                or url.port not in {None, 443} or url.fragment or not match
                or int(match[1]) != interaction.channel_id or int(match[2]) != attachment.id):
            raise ValueError
    except (ValueError, TypeError):
        raise UserError("Only this interaction's original Discord CDN image attachment is accepted.") from None
    return mime, suffix


async def fetch_attachment(interaction, attachment):
    import aiohttp
    mime, suffix = validate_attachment(interaction, attachment)
    timeout = aiohttp.ClientTimeout(total=20, connect=5, sock_read=10)
    try:
        async with aiohttp.ClientSession(timeout=timeout, cookie_jar=aiohttp.DummyCookieJar(), trust_env=False, auto_decompress=False) as session:
            async with session.get(attachment.url, allow_redirects=False, headers={"Accept": "image/png,image/jpeg,image/webp", "User-Agent": "TechFarmer/" + __version__}) as response:
                if response.status != 200 or response.headers.get("Content-Encoding", "identity").lower() not in {"identity", ""}:
                    raise UserError("Reference attachment could not be read safely. Upload it again.")
                if response.content_length is not None and response.content_length > LIMITS.reference_bytes:
                    raise UserError("Reference exceeds the 8 MiB attachment limit.")
                result = bytearray()
                async for chunk in response.content.iter_chunked(65536):
                    if len(result) + len(chunk) > LIMITS.reference_bytes:
                        raise UserError("Reference exceeds the 8 MiB attachment limit.")
                    result.extend(chunk)
                if len(result) != attachment.size:
                    raise UserError("Reference size differs from Discord metadata. Upload it again.")
                return bytes(result), mime, suffix
    except UserError:
        raise
    except (aiohttp.ClientError, asyncio.TimeoutError):
        raise UserError("Reference attachment download failed. No URL was logged or forwarded.") from None


SERIAL_ACTIONS = {"reference", "harvest", "pull", "approve", "review", "reframe", "export", "decide", "save", "settings", "reload"}


class FarmerService:
    def __init__(self, store, runner, settings, *, downloader=fetch_attachment, storage=None, settings_path=None):
        self.store, self.runner, self.settings, self.downloader = store, runner, settings, downloader
        self.storage = storage
        self.settings_path = settings_path or Path(SETTINGS_NAME)
        self.owner_lock = asyncio.Lock()
        self.view_factory = None

    def authorized(self, interaction):
        return (getattr(interaction, "guild_id", None) == self.store.guild_id
                and getattr(interaction, "guild", None) is not None
                and getattr(getattr(interaction, "user", None), "id", None) == self.store.owner_id)

    async def reply(self, interaction, content, attachments=None, view=None):
        import discord
        files = []
        if attachments:
            await asyncio.to_thread(self.store.require_consent)
            cap = attachment_limit(interaction)
            if len(attachments) > 10 or sum(len(raw) for _, raw in attachments) > cap:
                raise UserError("This private batch exceeds the runtime limit. Request a smaller batch; local PNG pixels were not resized.")
            files = [discord.File(BytesIO(raw), filename=name) for name, raw in attachments]
        kwargs = {"content": content[:1950], "ephemeral": True, "allowed_mentions": discord.AllowedMentions.none()}
        if files:
            kwargs["files"] = files
        if view is not None:
            kwargs["view"] = view
        try:
            if interaction.response.is_done():
                await interaction.followup.send(**kwargs)
            else:
                await interaction.response.send_message(**kwargs)
        finally:
            for file in files:
                file.close()

    async def error(self, interaction, message=None):
        if not self.authorized(interaction):
            message = "Restricted to the configured owner in the configured test server. DMs are disabled."
        try:
            await self.reply(interaction, message or "The operation could not finish. Check local configuration and /farmer status. No public fallback or unsafe exception log was used.")
        except Exception:
            pass

    def _view(self, kind, **kwargs):
        return self.view_factory(kind, **kwargs) if self.view_factory else None

    async def status_reply(self, interaction, job_id=None, offset=0):
        if job_id:
            state = await asyncio.to_thread(self.store.summary, job_id)
            body = (f"**{state['id']} · {state['phase']} · {state['mode']}**\n"
                    f"Source discovery complete: {state['discovery_complete']} · reported source problems: {state['source_problems']}\n"
                    f"Pages: {state['pages']}\nCandidates: {state['candidates']}\n"
                    f"Last code: `{state['code'] or 'none'}` · requested control: `{state['control'] or 'none'}`\n"
                    "`review_ready` means discovered pages processed, NOT verified identity. A paused/partial job is not complete. "
                    "Pause/cancel takes effect at the next safe boundary; an in-flight local model call must return first. "
                    "Fresh /status and buttons work without the original pull interaction. No-reference collections make no identity claim.")
            if state.get("source_message"):
                body += "\nSource: " + state["source_message"]
            if state["phase"] == "awaiting_approval":
                body += f"\n{len(state['required_origins'])} external image origin(s) need your approval. Use `/farmer approve job:{job_id}` to inspect the exact set before accepting."
            await self.reply(interaction, body, view=self._view("job", job_id=job_id))
        else:
            jobs = await asyncio.to_thread(self.store.jobs, offset)
            content = "\n".join(f"`{job['id']}` · {job['phase']} · {job['mode']}" + (f" · profile {job['profile']}" if job["profile"] else " · no identity claim") for job in jobs) or "No jobs in this range."
            await self.reply(interaction, content + f"\nNext jobs: `/farmer status offset:{offset + 8}`. Source URLs are deliberately omitted.")

    def _review(self, job_id, offset):
        items = self.store.candidates(job_id, offset, 6)
        matching = self.store.job(job_id)["mode"] == "match"
        images, lines = [], []
        for number, item in enumerate(items, 1):
            page = self.store.page(item["page_id"])
            if item["crop_path"]:
                image, _ = decode(self.store.read_local(item["crop_path"]), generated=True)
            else:
                image, _ = decode(self.store.read_local(page["original_path"]))
                from PIL import ImageDraw
                ImageDraw.Draw(image).rectangle(item["bbox"], outline="#ffba00", width=max(2, min(image.size) // 150))
            image.thumbnail((254, 228))
            images.append((f"{number}. {item['id']}", image))
            warning = "; ".join(item["warnings"][:2]) or "Verify character identity visually."
            score = f"raw score {item['score']:.3f}" if matching else "collection, no model score/identity claim"
            lines.append(f"{number}. `{item['id']}` p{page['page_number']} · {item['decision']} · {score}\n{warning[:100]}")
        return items, lines, contact_sheet(images) if images else None

    @asynccontextmanager
    async def command_gate(self, action):
        if action in SERIAL_ACTIONS:
            async with self.owner_lock:
                yield
        else:
            yield

    async def _apply_settings(self, updated):
        if any(getattr(updated, key) != getattr(self.settings, key) for key in ("owner_id", "guild_id", "application_id", "device", "model_cache")):
            raise UserError("Reload cannot change owner, guild, application, model device or paths. Stop and configure locally instead.")
        if await asyncio.to_thread(self.store.has_active_jobs):
            raise UserError("Pause active jobs and wait for their safe boundary before changing limits or reloading; then explicitly resume.")
        self.settings = updated
        self.runner.limits = RunLimits(updated.pages_per_run, updated.candidates_per_run, updated.detections_per_page,
                                       updated.storage_mib * 1024 * 1024, updated.tile_size, updated.max_tiles,
                                       updated.page_crop_mib * 1024 * 1024)
        self.runner.source_settings = updated
        if self.storage is not None:
            self.storage.storage_limit = updated.storage_mib * 1024 * 1024

    async def run(self, interaction, action, **args):
        if not self.authorized(interaction):
            await self.error(interaction)
            return
        if action in SERIAL_ACTIONS and self.owner_lock.locked():
            await self.error(interaction, "Another image command is still finishing. Retry shortly; consent, status, pause and cancel remain available.")
            return
        async with self.command_gate(action):
            try:
                await interaction.response.defer(ephemeral=True, thinking=True)
                if action not in {"help", "consent", "status", "control", "doctor", "version", "update", "storage", "settings", "reload"}:
                    await asyncio.to_thread(self.store.require_consent)
                if action == "help":
                    await self.reply(interaction, HELP)
                elif action == "consent":
                    accept = args.get("accept")
                    if accept is not None:
                        await asyncio.to_thread(self.store.consent, accept)
                        if not accept:
                            self.runner.signal_all()
                    await self.reply(interaction, CONSENT_TEXT + "\nUse `/farmer consent accept:true` to accept, or `accept:false` to revoke. Revocation does not delete existing files.")
                elif action == "status":
                    await self.status_reply(interaction, args.get("job_id"), args.get("offset", 0))
                elif action == "doctor":
                    result = await asyncio.to_thread(self.runner.matcher.readiness)
                    await self.reply(interaction, f"Model ready: {result.get('ready', False)} · {result.get('code', 'unknown')}\n{result.get('message', '')}\n{result.get('action', '')}\nNo network/model download is performed by this command.")
                elif action == "version":
                    await self.reply(interaction, f"**Installed TechFarmer {__version__}**\n{REPOSITORY}\n`/farmer update` checks canonical main only. To apply: stop this bot → UPDATE-WINDOWS.cmd (or python tools/update.py --update) → restart. Discord cannot run Python, install updates or change repositories/branches.")
                elif action == "update":
                    from tools.update import check_update
                    result = await asyncio.to_thread(check_update, Path(__file__).resolve().parents[1])
                    await self.reply(interaction, f"Installed {__version__} · check: {result['status']}\n{result['message']}\nNo update was installed. Stop the bot → UPDATE-WINDOWS.cmd → restart. First-time old installs: see README for copying the canonical updater into place. Back up local settings/data first.")
                elif action == "settings":
                    key, value = args.get("name"), args.get("value")
                    if key is None and value is None:
                        await self.reply(interaction, "**Reloadable numeric limits**\n" + "\n".join(f"{name}: {getattr(self.settings, name)}" for name in LIMIT_FIELDS) + "\nPause jobs before changes. Owner/guild/application/token/device/paths are never mutable here. Destination selection is /farmer storage.")
                    elif key not in LIMIT_FIELDS or value is None:
                        raise UserError("Specify a whitelisted numeric limit and integer value. Identity, token, URLs and paths cannot change from Discord.")
                    else:
                        if await asyncio.to_thread(self.store.has_active_jobs):
                            raise UserError("Pause active jobs and wait for their safe boundary before changing limits.")
                        updated = await asyncio.to_thread(set_limit, self.settings, key, value, self.settings_path)
                        await self._apply_settings(updated)
                        await self.reply(interaction, f"Saved {key}={value} with a settings backup. Limits reloaded; explicitly resume paused jobs. Frozen model inference settings stay reproducible for existing jobs.")
                elif action == "reload":
                    updated = await asyncio.to_thread(load_settings, self.settings_path)
                    await self._apply_settings(updated)
                    await self.reply(interaction, "Whitelisted nonsecret limits reloaded. Owner/guild/application/device/paths did not change; storage still requires its own private-channel consent. Resume paused jobs explicitly.")
                elif action == "storage":
                    if self.storage is None:
                        raise UserError("Persistent Discord storage is unavailable in this process; files stay local.")
                    destination = await asyncio.to_thread(self.store.storage_destination)
                    channel = args.get("channel")
                    selected = getattr(channel, "id", None) or destination.get("channel_id") or getattr(self.settings, "storage_channel_id", None)
                    if args.get("accept") is not None:
                        await self.storage.configure(selected, owner_id=interaction.user.id, guild_id=interaction.guild_id, accept=args["accept"])
                        await self.reply(interaction, ("Persistent storage consent saved for channel " + str(selected) + ". " if args["accept"] else "Persistent storage consent revoked. ") + STORAGE_CONSENT_TEXT + " Use /farmer save explicitly. Never a guaranteed permanent archive; keep local backups.")
                    else:
                        active = str(destination.get("channel_id")) if destination.get("accepted") else "none"
                        await self.reply(interaction, STORAGE_CONSENT_TEXT + "\nCurrently consented destination: " + active + ". Requested/setup channel: " + (str(selected) if selected else "unset") + " (not activated by this notice). Use `/farmer storage channel:<private text channel> accept:true` to validate and confirm, or `accept:false` to revoke. No auto-save is enabled.")
                elif action == "save":
                    if self.storage is None:
                        raise UserError("No Discord storage adapter is available; native files remain local.")
                    result = await self.storage.save(args["job_id"], owner_id=interaction.user.id, guild_id=interaction.guild_id, mode=args.get("mode", "accepted"))
                    await self.reply(interaction, f"Persistent channel save: {result.sent} native PNG(s) sent, {result.already_sent} already receipted, {result.unknown} unknown (NOT retried), {result.oversized} too large and unchanged locally; {result.remaining} pending this request's batch cap. Continue with `/farmer save job:{args['job_id']} mode:{args.get('mode', 'accepted')}`. Files are visible to channel members/roles/admins, not a guaranteed permanent archive. References were not uploaded.\n" + "\n".join(result.links))
                elif action == "receipts":
                    rows = await asyncio.to_thread(self.store.receipts, args["job_id"])
                    offset = args.get("offset", 0)
                    lines = []
                    for row in rows[offset:offset + 8]:
                        line = f"`{row['candidate_id']}` · {row['status']} · {row['sha256'][:12]}"
                        if row["status"] == "sent" and row.get("message_id"):
                            line += f"\nhttps://discord.com/channels/{self.store.guild_id}/{row['channel_id']}/{row['message_id']}"
                        lines.append(line)
                    await self.reply(interaction, ("\n".join(lines) or "No storage receipts for this job.") + f"\nNext: `/farmer receipts job:{args['job_id']} offset:{offset + 8}`. Unknown/sending outcomes are never blindly resent; inspect the channel yourself. Local originals remain available.")
                elif action == "profiles":
                    rows = await asyncio.to_thread(self.store.profiles)
                    offset = args.get("offset", 0)
                    text = "\n".join(f"{row['name']}: {row['identity_refs']} identity, {row['framing_refs']} framing" for row in rows[offset:offset + 12]) or "No profiles yet."
                    await self.reply(interaction, text + f"\nNext page: `/farmer profiles offset:{offset + 12}`. Each profile must represent ONE character.")
                elif action == "reference":
                    raw, mime, suffix = await self.downloader(interaction, args["image"])
                    if await asyncio.to_thread(self.store.storage_bytes) + len(raw) > self.settings.storage_mib * 1024 * 1024:
                        raise UserError("Local storage budget reached. Increase storage_mib or archive old data first.")
                    item = await asyncio.to_thread(self.store.add_reference, args["profile"], raw, args.get("role", "identity"), mime, suffix, storage_limit=self.settings.storage_mib * 1024 * 1024)
                    await self.reply(interaction, f"Saved `{item['id']}` as {item['role']} reference for this profile. Add more views of the SAME character, or use a separate profile. New jobs freeze their own reference copies; existing jobs do not change.")
                elif action in {"harvest", "pull"}:
                    profile = args.get("profile")
                    mode = "match" if action == "harvest" else args.get("mode", "auto")
                    reference = args.get("reference")
                    if reference is not None:
                        if mode not in {"auto", "match"}:
                            raise UserError("Use a reference only with auto/match mode; collection never guesses character identity.")
                        ready = await asyncio.to_thread(self.runner.matcher.readiness)
                        if not ready.get("ready"):
                            raise UserError("Local model is not ready. Prepare the optional vision install/model before reference matching. No dummy detector will run; omit the reference for native page collection.")
                        raw, mime, suffix = await self.downloader(interaction, reference)
                        if not profile:
                            from uuid import uuid4
                            profile = "Quick-" + uuid4().hex[:12]
                        await asyncio.to_thread(self.store.add_reference, profile, raw, "identity", mime, suffix,
                                                storage_limit=self.settings.storage_mib * 1024 * 1024)
                    spec = crop_spec(args.get("preset", "detected"), args.get("aspect"), args.get("context", "0,0,0,0"), args.get("trim", "0,0,0,0"), args.get("align", "center"), args.get("shape", "rectangle"), args.get("fit", "contain"))
                    job = await self.runner.create_pull(args["url"], self.settings, profile=profile, mode=mode,
                                                         scope=args.get("scope", "auto"), spec=spec, threshold=args.get("threshold", 0.15))
                    await self.runner.schedule(job["id"])
                    description = "local image-guided matching against frozen SAME-character references; review required" if job["mode"] == "match" else "native page/panel collection; NO character identity claim and no model required"
                    await self.reply(interaction, f"Queued `{job['id']}` · {job['mode']} · {description}. Use `/farmer status job:{job['id']}`. If external image origins are discovered, `/farmer approve job:{job['id']}` lists the exact set before fetching their images. This reply is not permanent progress. No results are posted automatically.", view=self._view("job", job_id=job["id"]))
                elif action == "approve":
                    job = await asyncio.to_thread(self.store.job, args["job_id"])
                    if job["phase"] != "awaiting_approval":
                        raise UserError("This job has no pending external-image-origin approval. Check /farmer status.")
                    proposal = job["source_proposal"]
                    disclosure = "\n".join(proposal.get("required_origins", []))
                    if not disclosure or len(disclosure) > 1400:
                        raise UserError("The full exact origin set cannot fit safely in this Discord confirmation. Use Python CLI approve to inspect all origins; nothing was approved here.")
                    if args.get("accept") is not None:
                        await asyncio.to_thread(self.store.approve_source, job["id"], args["accept"], proposal_id=args.get("proposal_id"))
                        if args["accept"]:
                            await asyncio.to_thread(self.store.control, job["id"], "resume")
                            await self.runner.schedule(job["id"])
                        await self.reply(interaction, "Source-origin approval " + ("saved; queued for safe reinspection." if args["accept"] else "declined; job paused.") + "\nExact external image origins:\n" + disclosure)
                    else:
                        await self.reply(interaction, "**Approve this job's exact external image origins**\n" + disclosure + f"\nConfirm download/edit rights with **Approve these origins** below. The button is bound to this displayed set; changed scope requires fresh approval. Slash alternative: `/farmer approve job:{job['id']} accept:true proposal:{proposal['proposal_id']}`. Newly unapproved external image origins stay blocked.", view=self._view("approval", job_id=job["id"], proposal_id=proposal["proposal_id"]))
                elif action == "control":
                    result = await asyncio.to_thread(self.store.control, args["job_id"], args["operation"])
                    if args["operation"] == "resume":
                        await self.runner.schedule(result["id"])
                    else:
                        self.runner.signal(result["id"])
                    await self.status_reply(interaction, result["id"])
                elif action == "review":
                    items, lines, sheet = await asyncio.to_thread(self._review, args["job_id"], args.get("offset", 0))
                    attachments = [("review-preview.png", sheet)] if sheet else None
                    await self.reply(interaction, "\n".join(lines) + "\nResized preview only. Select a candidate, then Accept/Reject, or use /farmer decide. No automatic identity guarantee.", attachments,
                                     self._view("review", job_id=args["job_id"], offset=args.get("offset", 0), items=items))
                elif action == "decide":
                    await asyncio.to_thread(self.store.decide, args["candidate_id"], args["decision"])
                    await self.reply(interaction, f"Candidate `{args['candidate_id']}` is {args['decision']}. Originals and rejected candidates remain local.")
                elif action == "reframe":
                    spec = crop_spec(args.get("preset", "detected"), args.get("aspect"), args.get("context", "0,0,0,0"), args.get("trim", "0,0,0,0"), args.get("align", "center"), args.get("shape", "rectangle"), args.get("fit", "contain"))
                    item = await asyncio.to_thread(self.runner.reframe, args["candidate_id"], spec)
                    await self.reply(interaction, f"Saved a separate native-pixel edit for `{item['id']}`; decision reset to pending. Original page and prior crop files were not overwritten.")
                elif action == "export":
                    plan = await asyncio.to_thread(export_job, self.store, args["job_id"], attachment_limit(interaction), storage_limit=self.settings.storage_mib * 1024 * 1024, mode=args.get("mode", "accepted"))
                    batch = args.get("batch", 0)
                    if batch < 0 or batch >= len(plan.batches):
                        await self.reply(interaction, f"Saved {plan.count} exact PNGs locally under exports/{args['job_id']}/. {len(plan.oversized)} file(s) exceed this Discord limit and remain local, unchanged. Available private batches: {len(plan.batches)} (numbered from zero).")
                    else:
                        files = [(path.name, await asyncio.to_thread(path.read_bytes)) for path in plan.batches[batch]]
                        await self.reply(interaction, f"Batch {batch + 1}/{len(plan.batches)} · {plan.count} eligible native PNGs saved locally in exports/{args['job_id']}/. Partial source: {plan.partial_source}. {len(plan.oversized)} oversized file(s) remain local—never downscaled or publicly hosted. Local provenance includes private URLs; Discord provenance omits them. Use `/farmer export job:{args['job_id']} batch:{batch + 1}` for the next batch.", files)
                else:
                    raise UserError("Unknown operation. Use /farmer help.")
            except UserError as exc:
                await self.error(interaction, str(exc))
            except SourceError as exc:
                await self.error(interaction, exc.message)
            except Exception:
                await self.error(interaction)
