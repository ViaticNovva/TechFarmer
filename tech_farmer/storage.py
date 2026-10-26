"""Explicit persistent Discord storage, with fresh permission checks and durable receipts."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from io import BytesIO

from .exports import export_job, selected_results
from .safety import LIMITS, UserError, digest


@dataclass(frozen=True)
class ChannelState:
    channel_id: int
    guild_id: int
    kind: str
    everyone_visible: bool
    bot_view: bool
    bot_send: bool
    bot_attach: bool
    owner_view: bool
    byte_limit: int
    target: object = field(default=None, repr=False, compare=False)


def validate_channel(state: ChannelState, channel_id: int, guild_id: int) -> int:
    if (not isinstance(state, ChannelState) or state.channel_id != channel_id or state.guild_id != guild_id
            or state.kind != "text"):
        raise UserError("Storage must be a regular text channel in the configured server, never a DM, thread, news channel or another guild.")
    if state.everyone_visible is not False:
        raise UserError("Storage refused: @everyone can view this channel, or privacy could not be verified. Choose an existing private channel; permissions were not changed.")
    if any(value is not True for value in (state.bot_view, state.bot_send, state.bot_attach, state.owner_view)):
        raise UserError("Storage unavailable: fresh owner access and bot View Channel, Send Messages and Attach Files permissions are required. No public fallback.")
    if type(state.byte_limit) is not int or state.byte_limit <= 0:
        raise UserError("The server attachment limit could not be verified. Files remain local.")
    return min(state.byte_limit, LIMITS.discord_bytes)


class DiscordStorageGateway:
    """REST-fetched guild roles, channel overwrites and members; never cache-only checks."""
    def __init__(self, client, owner_id: int, guild_id: int):
        self.client, self.owner_id, self.guild_id = client, owner_id, guild_id

    async def inspect(self, channel_id: int) -> ChannelState:
        import discord
        if self.client.user is None:
            raise UserError("The bot is not connected; no storage message was sent.")
        try:
            guild = await self.client.fetch_guild(self.guild_id)
            channels, member, owner = await asyncio.gather(
                guild.fetch_channels(), guild.fetch_member(self.client.user.id), guild.fetch_member(self.owner_id))
            channel = next((item for item in channels if item.id == channel_id), None)
            if (guild.id != self.guild_id or not isinstance(channel, discord.TextChannel)
                    or channel.type is not discord.ChannelType.text or channel.guild.id != guild.id
                    or guild.default_role is None):
                raise UserError("Storage must be an existing regular text channel in the configured server. DM, thread, forum, news and foreign channels are refused.")
            for person in (member, owner):
                role_ids = getattr(person, "_roles", None)
                if (person.guild.id != guild.id or role_ids is None
                        or any(guild.get_role(role_id) is None for role_id in role_ids)):
                    raise UserError("Fresh server role/permission data is incomplete; storage is blocked.")
            public = channel.permissions_for(guild.default_role)
            mine = channel.permissions_for(member)
            yours = channel.permissions_for(owner)
            return ChannelState(channel.id, guild.id, "text", public.view_channel,
                                mine.view_channel, mine.send_messages, mine.attach_files,
                                yours.view_channel, guild.filesize_limit, channel)
        except UserError:
            raise
        except Exception:
            raise UserError("The private channel or current permissions could not be verified from Discord. Files remain local; no current-channel fallback.") from None

    async def send(self, state: ChannelState, content: str, attachments: list[tuple[str, bytes]]) -> int:
        import discord
        files = [discord.File(BytesIO(raw), filename=name) for name, raw in attachments]
        try:
            # discord.py's supported send API handles rate limits. A nonce alone is not
            # enforced idempotency, so crash/transport uncertainty is handled by receipts.
            message = await state.target.send(content=content, files=files, allowed_mentions=discord.AllowedMentions.none())
            return message.id
        finally:
            for item in files:
                item.close()


@dataclass
class SaveResult:
    sent: int = 0
    already_sent: int = 0
    unknown: int = 0
    oversized: int = 0
    remaining: int = 0
    links: list[str] = field(default_factory=list)


class StorageManager:
    def __init__(self, store, gateway, *, storage_limit: int):
        self.store, self.gateway, self.storage_limit = store, gateway, storage_limit
        self.lock = asyncio.Lock()

    async def configure(self, channel_id: int | None, *, owner_id: int, guild_id: int, accept: bool):
        if owner_id != self.store.owner_id or guild_id != self.store.guild_id:
            raise UserError("Storage is restricted to the configured owner and server.")
        if accept is not True:
            await asyncio.to_thread(self.store.set_storage_destination, channel_id, False)
            return
        await asyncio.to_thread(self.store.require_consent)
        if type(channel_id) is not int or channel_id <= 0:
            raise UserError("Choose an existing private text channel explicitly; the invoking channel is never used by default.")
        state = await self.gateway.inspect(channel_id)
        validate_channel(state, channel_id, guild_id)
        await asyncio.to_thread(self.store.set_storage_destination, channel_id, True)

    async def save(self, job_id: str, *, owner_id: int, guild_id: int, mode="accepted", max_batches=3) -> SaveResult:
        if type(max_batches) is not int or not 1 <= max_batches <= 10:
            raise UserError("Storage saves are limited to one through ten bounded batches per request.")
        if self.lock.locked():
            raise UserError("A storage request is still finishing. Retry after its receipt is recorded.")
        async with self.lock:
            destination = await asyncio.to_thread(self.store.require_storage_destination, owner_id, guild_id)
            channel_id = destination["channel_id"]
            state = await self.gateway.inspect(channel_id)
            cap = validate_channel(state, channel_id, guild_id)
            plan = await asyncio.to_thread(export_job, self.store, job_id, cap, storage_limit=self.storage_limit, mode=mode)
            _, _, records = await asyncio.to_thread(selected_results, self.store, job_id, mode)
            result, pending, seen = SaveResult(), [], set()
            for row in records:
                if not row["crop_path"] or row["crop_sha256"] in seen:
                    continue
                seen.add(row["crop_sha256"])
                receipt = await asyncio.to_thread(self.store.receipt, channel_id, row["crop_sha256"])
                if receipt:
                    if receipt["status"] == "sent":
                        result.already_sent += 1
                        self._link(result, guild_id, channel_id, receipt.get("message_id"))
                    else:
                        result.unknown += 1
                    continue
                name = row["id"] + "-" + row["crop_sha256"][:12] + ".png"
                path = plan.folder / name
                pending.append({"candidate_id": row["id"], "sha256": row["crop_sha256"], "path": path,
                                "size": path.stat().st_size, "name": name})
            batches = 0
            while pending and batches < max_batches:
                current = await asyncio.to_thread(self.store.require_storage_destination, owner_id, guild_id)
                if current["channel_id"] != channel_id:
                    raise UserError("Storage destination changed during this request. Existing receipts remain; start a fresh save for the newly selected channel.")
                # Re-fetch the authoritative channel, roles and members for EACH batch,
                # including a resumed save. A formerly private channel may now be public.
                state = await self.gateway.inspect(channel_id)
                cap = validate_channel(state, channel_id, guild_id)
                batch, used = [], 0
                while pending and len(batch) < 8:
                    item = pending[0]
                    if item["size"] > cap:
                        result.oversized += 1
                        pending.pop(0)
                        continue
                    if batch and used + item["size"] > cap:
                        break
                    pending.pop(0)
                    batch.append(item)
                    used += item["size"]
                if not batch:
                    continue
                payloads = {}
                for item in batch:
                    path = item["path"]
                    if path.is_symlink() or not path.resolve().is_relative_to(self.store.export_root):
                        raise UserError("Export file path changed; storage stopped without changing pixels.")
                    raw = await asyncio.to_thread(path.read_bytes)
                    if digest(raw) != item["sha256"] or len(raw) != item["size"]:
                        raise UserError("Native export bytes changed; storage stopped rather than sending substituted content.")
                    payloads[item["sha256"]] = (item["name"], raw)
                eligible = {row["id"]: row["crop_sha256"] for row in (await asyncio.to_thread(selected_results, self.store, job_id, mode))[2]}
                if any(eligible.get(item["candidate_id"]) != item["sha256"] for item in batch):
                    raise UserError("A result decision or crop changed before sending; start a fresh save.")
                state = await self.gateway.inspect(channel_id)
                final_cap = validate_channel(state, channel_id, guild_id)
                if sum(item["size"] for item in batch) > final_cap:
                    raise UserError("The Discord attachment limit changed before sending. Native files stay local; retry a fresh size-bounded save.")
                claimed = await asyncio.to_thread(self.store.claim_outputs, channel_id, job_id, batch)
                if not claimed:
                    continue
                hashes = [item["sha256"] for item in claimed]
                try:
                    latest, generation = await asyncio.to_thread(self.store.storage_snapshot, owner_id, guild_id)
                    if latest["channel_id"] != channel_id or self.store.safety_generation != generation:
                        raise UserError("Consent or destination changed before sending; no batch was sent.")
                except BaseException:
                    await asyncio.to_thread(self.store.release_unsent, channel_id, hashes)
                    raise
                try:
                    label = "accepted reviewed results" if mode == "accepted" else "native page/panel collection; no character identity claim"
                    message_id = await self.gateway.send(state, f"TechFarmer · `{job_id}` · {label}. Local native backup retained.", [payloads[sha] for sha in hashes])
                    await asyncio.to_thread(self.store.finish_outputs, channel_id, hashes, "sent", message_id)
                except BaseException:
                    await asyncio.to_thread(self.store.finish_outputs, channel_id, hashes, "unknown")
                    raise UserError("Discord send outcome is unknown. No blind retry will occur for these outputs, including after restart. Inspect the private channel and receipts; local files remain unchanged.") from None
                result.sent += len(claimed)
                self._link(result, guild_id, channel_id, message_id)
                batches += 1
            result.remaining = len(pending)
            return result

    @staticmethod
    def _link(result: SaveResult, guild_id: int, channel_id: int, message_id):
        if message_id and str(message_id).isdigit():
            link = f"https://discord.com/channels/{guild_id}/{channel_id}/{message_id}"
            if link not in result.links and len(result.links) < 3:
                result.links.append(link)
