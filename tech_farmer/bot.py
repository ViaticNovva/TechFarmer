"""discord.py adapter; importing this module never connects an account."""
from __future__ import annotations

from typing import Literal
import discord
from discord import app_commands

from .service import FarmerService
from .safety import UserError
from .storage import DiscordStorageGateway, StorageManager

Preset = Literal["detected", "tight", "reference", "panel", "face", "upper_body", "full_body"]
Shape = Literal["rectangle", "ellipse", "circle"]
Align = Literal["center", "left", "right", "top", "bottom", "top-left", "top-right", "bottom-left", "bottom-right"]


class ActionButton(discord.ui.Button):
    def __init__(self, service, label, action, *, style=discord.ButtonStyle.secondary, **args):
        super().__init__(label=label, style=style)
        self.service, self.action, self.args = service, action, args

    async def callback(self, interaction: discord.Interaction):
        await self.service.run(interaction, self.action, **self.args)


class PrivateView(discord.ui.View):
    def __init__(self, service):
        super().__init__(timeout=600)
        self.service = service

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if not self.service.authorized(interaction):
            await self.service.error(interaction)
            return False
        return True

    async def on_error(self, interaction: discord.Interaction, error: Exception, item):
        await self.service.error(interaction)


class JobView(PrivateView):
    def __init__(self, service, job_id):
        super().__init__(service)
        self.add_item(ActionButton(service, "Refresh status", "status", job_id=job_id))
        for operation in ("pause", "resume", "cancel"):
            self.add_item(ActionButton(service, operation.title(), "control", job_id=job_id, operation=operation))
        self.add_item(ActionButton(service, "Review matches", "review", job_id=job_id, offset=0))


class ApprovalView(PrivateView):
    def __init__(self, service, job_id, proposal_id):
        super().__init__(service)
        self.add_item(ActionButton(service, "Approve these origins", "approve", style=discord.ButtonStyle.success,
                                   job_id=job_id, proposal_id=proposal_id, accept=True))
        self.add_item(ActionButton(service, "Decline", "approve", job_id=job_id, proposal_id=proposal_id, accept=False))


class ReviewView(PrivateView):
    def __init__(self, service, job_id, offset, items):
        super().__init__(service)
        for number, item in enumerate(items, 1):
            self.add_item(ActionButton(service, f"Accept {number}", "decide", style=discord.ButtonStyle.success, candidate_id=item["id"], decision="accepted"))
            self.add_item(ActionButton(service, f"Reject {number}", "decide", style=discord.ButtonStyle.danger, candidate_id=item["id"], decision="rejected"))
        if offset:
            self.add_item(ActionButton(service, "Previous six", "review", job_id=job_id, offset=max(0, offset - 6)))
        self.add_item(ActionButton(service, "Refresh", "review", job_id=job_id, offset=offset))
        if len(items) == 6:
            self.add_item(ActionButton(service, "Next six", "review", job_id=job_id, offset=offset + 6))


class FarmerGroup(app_commands.Group):
    def __init__(self, service):
        super().__init__(name="farmer", description="Private automatic reference-guided harvesting", guild_only=True)
        self.service = service

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if not self.service.authorized(interaction):
            await self.service.error(interaction)
            return False
        return True

    async def on_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        await self.service.error(interaction)

    @app_commands.command(description="Private setup and automatic harvesting workflow")
    async def help(self, interaction: discord.Interaction):
        await self.service.run(interaction, "help")

    @app_commands.command(description="Read, accept or revoke explicit rights and privacy consent")
    async def consent(self, interaction: discord.Interaction, accept: bool | None = None):
        await self.service.run(interaction, "consent", accept=accept)

    @app_commands.command(description="Offline model/cache check; never downloads weights")
    async def doctor(self, interaction: discord.Interaction):
        await self.service.run(interaction, "doctor")

    @app_commands.command(description="Add one of up to eight SAME-character references or one framing aspect guide")
    async def reference(self, interaction: discord.Interaction, profile: app_commands.Range[str, 1, 60], image: discord.Attachment, role: Literal["identity", "framing"] = "identity"):
        await self.service.run(interaction, "reference", profile=profile, image=image, role=role)

    @app_commands.command(description="List saved single-character reference profiles")
    async def profiles(self, interaction: discord.Interaction, offset: app_commands.Range[int, 0, 1000000] = 0):
        await self.service.run(interaction, "profiles", offset=offset)

    @app_commands.command(description="Pull a permitted source link; optional reference/profile enables local matching")
    @app_commands.describe(reference="Optional image upload of ONE character; model install required only for matching", profile="Saved SAME-character references; omit both references for no-model page collection", mode="Auto: references match; no reference collects native pages without identity claims")
    async def pull(self, interaction: discord.Interaction, url: app_commands.Range[str, 1, 3000], reference: discord.Attachment | None = None, profile: app_commands.Range[str, 1, 60] | None = None, scope: Literal["auto", "series", "chapter", "page"] = "auto", mode: Literal["auto", "match", "collect-pages", "collect-panels"] = "auto", preset: Preset = "detected", aspect: str | None = None):
        await self.service.run(interaction, "pull", url=url, reference=reference, profile=profile, scope=scope, mode=mode, preset=preset, aspect=aspect)

    @app_commands.command(description="Inspect and approve this job's exact discovered external image origins")
    async def approve(self, interaction: discord.Interaction, job: str, accept: bool | None = None, proposal: str | None = None):
        await self.service.run(interaction, "approve", job_id=job, accept=accept, proposal_id=proposal)

    @app_commands.command(description="Automatically discover permitted source pages, match frozen references and crop")
    @app_commands.describe(context="Extra pixels left,top,right,bottom; e.g. 20,0,80,0", trim="Remove pixels left,top,right,bottom", threshold="Raw model score threshold, NOT an accuracy probability", aspect="Optional width:height ratio, e.g. 4:5")
    async def harvest(self, interaction: discord.Interaction, url: app_commands.Range[str, 1, 3000], profile: app_commands.Range[str, 1, 60], scope: Literal["auto", "series", "chapter", "page"] = "auto", preset: Preset = "detected", aspect: str | None = None, context: str = "0,0,0,0", trim: str = "0,0,0,0", align: Align = "center", shape: Shape = "rectangle", fit: Literal["contain", "cover"] = "contain", threshold: app_commands.Range[float, 0.01, 1.0] = 0.15):
        await self.service.run(interaction, "harvest", url=url, profile=profile, scope=scope, preset=preset, aspect=aspect, context=context, trim=trim, align=align, shape=shape, fit=fit, threshold=threshold)

    @app_commands.command(description="Poll durable jobs; no dependency on the original interaction token")
    async def status(self, interaction: discord.Interaction, job: str | None = None, offset: app_commands.Range[int, 0, 1000000] = 0):
        await self.service.run(interaction, "status", job_id=job, offset=offset)

    @app_commands.command(description="Pause, resume or cancel a durable background harvest")
    async def control(self, interaction: discord.Interaction, job: str, operation: Literal["pause", "resume", "cancel"]):
        await self.service.run(interaction, "control", job_id=job, operation=operation)

    @app_commands.command(description="Review six uncertain candidate previews with private decision buttons")
    async def review(self, interaction: discord.Interaction, job: str, offset: app_commands.Range[int, 0, 1000000] = 0):
        await self.service.run(interaction, "review", job_id=job, offset=offset)

    @app_commands.command(description="Accept, reject or reset a single candidate without deleting the original")
    async def decide(self, interaction: discord.Interaction, candidate: str, decision: Literal["accepted", "rejected", "pending"]):
        await self.service.run(interaction, "decide", candidate_id=candidate, decision=decision)

    @app_commands.command(description="Optional correction: create a separate crop with a new preset/side/aspect")
    async def reframe(self, interaction: discord.Interaction, candidate: str, preset: Preset = "detected", aspect: str | None = None, context: str = "0,0,0,0", trim: str = "0,0,0,0", align: Align = "center", shape: Shape = "rectangle", fit: Literal["contain", "cover"] = "contain"):
        await self.service.run(interaction, "reframe", candidate_id=candidate, preset=preset, aspect=aspect, context=context, trim=trim, align=align, shape=shape, fit=fit)

    @app_commands.command(description="Save accepted native PNGs locally and send one size-bounded private batch")
    async def export(self, interaction: discord.Interaction, job: str, batch: app_commands.Range[int, 0, 1000000] = 0, mode: Literal["accepted", "collection"] = "accepted"):
        await self.service.run(interaction, "export", job_id=job, batch=batch, mode=mode)

    @app_commands.command(description="Choose/revoke an existing private server text channel for persistent results")
    async def storage(self, interaction: discord.Interaction, channel: discord.TextChannel | None = None, accept: bool | None = None):
        await self.service.run(interaction, "storage", channel=channel, accept=accept)

    @app_commands.command(description="Explicitly save accepted matches or complete collections to the private destination")
    async def save(self, interaction: discord.Interaction, job: str, mode: Literal["accepted", "collection"] = "accepted"):
        await self.service.run(interaction, "save", job_id=job, mode=mode)

    @app_commands.command(description="Inspect persistent send receipts; unknown sends are not blindly retried")
    async def receipts(self, interaction: discord.Interaction, job: str, offset: app_commands.Range[int, 0, 1000000] = 0):
        await self.service.run(interaction, "receipts", job_id=job, offset=offset)

    @app_commands.command(description="Show installed local version and stop/update/restart instructions")
    async def version(self, interaction: discord.Interaction):
        await self.service.run(interaction, "version")

    @app_commands.command(description="Check canonical GitHub main only; NEVER executes code or installs from Discord")
    async def update(self, interaction: discord.Interaction):
        await self.service.run(interaction, "update")

    @app_commands.command(description="View/change whitelisted numeric limits; never identity, token or paths")
    async def settings(self, interaction: discord.Interaction, name: Literal["pages_per_run", "candidates_per_run", "detections_per_page", "storage_mib", "page_crop_mib", "discovery_pages", "discovery_issues", "tile_size", "max_tiles"] | None = None, value: int | None = None):
        await self.service.run(interaction, "settings", name=name, value=value)

    @app_commands.command(description="Reload safe numeric limits from local settings while jobs are paused")
    async def reload(self, interaction: discord.Interaction):
        await self.service.run(interaction, "reload")


class FarmerClient(discord.Client):
    def __init__(self, store, runner, settings):
        super().__init__(intents=discord.Intents.none(), allowed_mentions=discord.AllowedMentions.none(), max_messages=None, application_id=settings.application_id)
        self.test_guild = discord.Object(id=settings.guild_id)
        self.runner = runner
        self.tree = app_commands.CommandTree(self)
        storage = StorageManager(store, DiscordStorageGateway(self, store.owner_id, store.guild_id),
                                 storage_limit=getattr(settings, "storage_mib", 2048) * 1024 * 1024)
        self.service = FarmerService(store, runner, settings, storage=storage)
        self.service.view_factory = lambda kind, **kwargs: {"job": JobView, "review": ReviewView, "approval": ApprovalView}[kind](self.service, **kwargs)
        self.tree.add_command(FarmerGroup(self.service), guild=self.test_guild)
        self.tree.on_error = self.on_tree_error

    async def on_tree_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        await self.service.error(interaction)

    async def setup_hook(self):
        if self.user is None or self.user.id != self.application_id:
            raise UserError("The authenticated bot does not match the configured Application ID.")
        await self.tree.sync(guild=self.test_guild)
        await self.runner.start()

    async def close(self):
        await self.runner.close()
        await super().close()

    async def on_ready(self):
        print("TechFarmer connected. Guild slash commands synced; controls are ephemeral. Only explicitly confirmed private-channel saves are persistent. Poll /farmer status.", flush=True)

