"""Actual discord.py imports/serialization. CI requires the SDK; offline skips are explicit."""
import importlib.util
import os
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

HAVE_DISCORD = importlib.util.find_spec("discord") is not None
if os.environ.get("TECH_FARMER_REQUIRE_SDK") == "1" and not HAVE_DISCORD:
    raise RuntimeError("CI requires real discord.py. A skipped SDK test is not an SDK validation.")


@unittest.skipUnless(HAVE_DISCORD, "discord.py is unavailable offline; real SDK import/serialization must run in GitHub CI")
class ActualDiscordTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_command_tree_serializes_without_login(self):
        import discord
        from tech_farmer.bot import FarmerClient, FarmerGroup, JobView, ReviewView, ApprovalView
        store = SimpleNamespace(owner_id=123456789012345678, guild_id=234567890123456789)
        runner = SimpleNamespace(close=AsyncMock())
        settings = SimpleNamespace(application_id=345678901234567890, guild_id=store.guild_id)
        client = FarmerClient(store, runner, settings)
        try:
            self.assertFalse(client.intents.message_content)
            self.assertEqual(client.intents.value, 0)
            self.assertEqual(client.tree.get_commands(), [])
            commands = client.tree.get_commands(guild=client.test_guild)
            self.assertEqual(len(commands), 1)
            serialized = commands[0].to_dict(client.tree)
            self.assertEqual(serialized["name"], "farmer")
            names = {option["name"] for option in serialized["options"]}
            self.assertTrue({"harvest", "pull", "approve", "storage", "save", "receipts", "settings", "reload", "version", "update", "reference", "control", "review", "export", "status"} <= names)
            pull = next(option for option in serialized["options"] if option["name"] == "pull")
            fields = {option["name"]: option for option in pull["options"]}
            self.assertIs(fields["url"]["required"], True)
            self.assertFalse(fields["reference"].get("required", False))
            self.assertFalse(fields["profile"].get("required", False))
            self.assertEqual(fields["reference"]["type"], 11)
            approval = next(option for option in serialized["options"] if option["name"] == "approve")
            self.assertIn("proposal", {option["name"] for option in approval["options"]})
            self.assertLessEqual(len(serialized["options"]), 25)
            harvest = next(option for option in serialized["options"] if option["name"] == "harvest")
            self.assertTrue({"url", "profile", "scope", "context", "trim", "shape"} <= {option["name"] for option in harvest["options"]})
            view = JobView(client.service, "j_" + "a" * 16)
            self.assertEqual(len(view.to_components()), 1)
            items = [{"id": "c_" + f"{index:016x}"} for index in range(6)]
            review = ReviewView(client.service, "j_" + "a" * 16, 0, items)
            self.assertLessEqual(len(review.children), 25)
            self.assertTrue(review.to_components())
            approval_view = ApprovalView(client.service, "j_" + "a" * 16, "b" * 64)
            self.assertEqual(approval_view.children[0].args["proposal_id"], "b" * 64)
            self.assertTrue(approval_view.to_components())
            view.stop()
            review.stop()
            approval_view.stop()
        finally:
            await client.close()

    async def test_actual_reply_and_errors_are_private_and_mentions_suppressed(self):
        import discord
        from tech_farmer.service import FarmerService
        store = SimpleNamespace(owner_id=101, guild_id=202, require_consent=lambda: None)
        service = FarmerService(store, None, None)
        sent = AsyncMock()
        response = SimpleNamespace(is_done=lambda: False, send_message=sent)
        item = SimpleNamespace(user=SimpleNamespace(id=101), guild_id=202, guild=SimpleNamespace(filesize_limit=1000), filesize_limit=1000, response=response)
        await service.reply(item, "safe", [("test.png", b"fixture-bytes")])
        kwargs = sent.await_args.kwargs
        self.assertIs(kwargs["ephemeral"], True)
        self.assertIsInstance(kwargs["files"][0], discord.File)
        self.assertEqual(kwargs["allowed_mentions"].to_dict(), {"parse": []})
        followup = AsyncMock()
        item.response = SimpleNamespace(is_done=lambda: True)
        item.followup = SimpleNamespace(send=followup)
        await service.error(item, "Safe fixed error")
        self.assertIs(followup.await_args.kwargs["ephemeral"], True)
        outsider = SimpleNamespace(user=SimpleNamespace(id=999), guild_id=202, guild=object(), response=response)
        await service.error(outsider)
        self.assertIn("Restricted", sent.await_args.kwargs["content"])
        self.assertIs(sent.await_args.kwargs["ephemeral"], True)

    async def test_startup_syncs_actual_guild_tree_and_buttons_recheck_owner(self):
        import discord
        from tech_farmer.bot import FarmerClient, ApprovalView
        store = SimpleNamespace(owner_id=123456789012345678, guild_id=234567890123456789)
        runner = SimpleNamespace(start=AsyncMock(), close=AsyncMock())
        settings = SimpleNamespace(application_id=345678901234567890, guild_id=store.guild_id)
        client = FarmerClient(store, runner, settings)
        client._connection.user = discord.ClientUser(state=client._connection, data={"id": str(settings.application_id), "username": "Fixture", "discriminator": "0", "avatar": None, "bot": True})
        try:
            with patch.object(client.tree, "sync", new=AsyncMock(return_value=[])) as sync:
                await client.setup_hook()
                sync.assert_awaited_once_with(guild=client.test_guild)
            runner.start.assert_awaited_once()
            view = ApprovalView(client.service, "j_" + "a" * 16, "b" * 64)
            client.service.error = AsyncMock()
            outsider = SimpleNamespace(user=SimpleNamespace(id=999), guild_id=store.guild_id, guild=object())
            self.assertFalse(await view.interaction_check(outsider))
            client.service.error.assert_awaited_once_with(outsider)
            view.stop()
        finally:
            await client.close()

    async def test_reference_attachment_gate_stays_separate_from_source_urls(self):
        import discord
        from tech_farmer.service import validate_attachment
        from tech_farmer.safety import UserError
        client = discord.Client(intents=discord.Intents.none())
        channel_id, attachment_id = 456789012345678901, 567890123456789012
        url = f"https://cdn.discordapp.com/attachments/{channel_id}/{attachment_id}/reference.png"
        data = {"id": str(attachment_id), "filename": "reference.png", "size": 123, "url": url, "proxy_url": url, "content_type": "image/png"}
        attachment = discord.Attachment(data=data, state=client._connection)
        item = SimpleNamespace(channel_id=channel_id, data={"resolved": {"attachments": {str(attachment_id): dict(data)}}})
        try:
            self.assertEqual(validate_attachment(item, attachment), ("image/png", ".png"))
            with self.assertRaises(UserError):
                validate_attachment(item, "https://books.example/reader")
            item.data["resolved"]["attachments"][str(attachment_id)]["size"] = 124
            with self.assertRaises(UserError):
                validate_attachment(item, attachment)
        finally:
            await client.close()

    async def test_real_sdk_permission_resolution_and_persistent_send_mentions(self):
        import discord
        from tech_farmer.storage import DiscordStorageGateway, validate_channel
        owner_id, guild_id, bot_id, channel_id = 123456789012345678, 234567890123456789, 345678901234567890, 456789012345678901
        client = discord.Client(intents=discord.Intents.none())
        state = client._connection
        state.user = discord.ClientUser(state=state, data={"id": str(bot_id), "username": "FixtureBot", "discriminator": "0", "avatar": None, "bot": True})
        guild = discord.Guild(state=state, data={"id": str(guild_id), "name": "Fixture", "owner_id": str(owner_id), "roles": [{"id": str(guild_id), "name": "@everyone", "permissions": "0", "position": 0, "color": 0, "hoist": False, "managed": False, "mentionable": False}], "emojis": [], "stickers": [], "features": [], "premium_tier": 0})
        def member(identifier):
            return discord.Member(data={"user": {"id": str(identifier), "username": "Fixture", "discriminator": "0", "avatar": None}, "roles": [], "joined_at": "2026-01-01T00:00:00+00:00", "deaf": False, "mute": False, "flags": 0}, guild=guild, state=state)
        bot, owner = member(bot_id), member(owner_id)
        permissions = discord.Permissions(view_channel=True, send_messages=True, attach_files=True)
        channel = discord.TextChannel(state=state, guild=guild, data={"id": str(channel_id), "name": "private-fixture", "type": 0, "position": 0, "permission_overwrites": [{"id": str(guild_id), "type": 0, "allow": "0", "deny": str(discord.Permissions(view_channel=True).value)}, {"id": str(bot_id), "type": 1, "allow": str(permissions.value), "deny": "0"}]})
        gateway = DiscordStorageGateway(client, owner_id, guild_id)
        try:
            with patch.object(client, "fetch_guild", new=AsyncMock(return_value=guild)), patch.object(discord.Guild, "fetch_channels", new=AsyncMock(return_value=[channel])), patch.object(discord.Guild, "fetch_member", new=AsyncMock(side_effect=[bot, owner])):
                checked = await gateway.inspect(channel_id)
            self.assertGreater(validate_channel(checked, channel_id, guild_id), 0)
            send = AsyncMock(return_value=SimpleNamespace(id=567890123456789012))
            with patch.object(discord.TextChannel, "send", new=send):
                message_id = await gateway.send(checked, "native result", [("result.png", b"authored-test-bytes")])
            self.assertEqual(message_id, 567890123456789012)
            self.assertNotIn("ephemeral", send.await_args.kwargs)
            self.assertEqual(send.await_args.kwargs["allowed_mentions"].to_dict(), {"parse": []})
            self.assertIsInstance(send.await_args.kwargs["files"][0], discord.File)
        finally:
            await client.close()
