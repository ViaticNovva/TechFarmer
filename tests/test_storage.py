"""Persistent storage with real SQLite/native files and an injected, non-network gateway."""
import asyncio
from dataclasses import replace
import threading
import time
import unittest

from tech_farmer.engine import JobRunner, crop_spec
from tech_farmer.images import png
from tech_farmer.safety import UserError, digest
from tech_farmer.storage import ChannelState, StorageManager, validate_channel
from tech_farmer.store import Store
from tests.helpers import FixtureMatcher, FixtureSource, drawing, workspace


class FixtureGateway:
    def __init__(self, state=None):
        self.state = state or ChannelState(303, 202, "text", False, True, True, True, True, 8 * 1024 * 1024)
        self.inspections, self.sent = [], []
        self.failure = None
        self.after_send = None

    async def inspect(self, channel_id):
        self.inspections.append(channel_id)
        return self.state

    async def send(self, state, content, attachments):
        self.sent.append((state, content, attachments))
        if self.failure:
            raise self.failure
        if self.after_send:
            self.after_send()
        return 1000 + len(self.sent)


class StoragePolicyTests(unittest.TestCase):
    def test_private_regular_same_guild_and_complete_permissions_required(self):
        good = ChannelState(303, 202, "text", False, True, True, True, True, 999)
        self.assertEqual(validate_channel(good, 303, 202), 999)
        bad = ({"kind": value} for value in ("news", "thread", "dm", "forum", "category"))
        for changes in [*bad, {"guild_id": 999}, {"channel_id": 999}, {"everyone_visible": True},
                        {"everyone_visible": None}, {"bot_view": False}, {"bot_send": False},
                        {"bot_attach": None}, {"owner_view": False}, {"byte_limit": 0}]:
            with self.subTest(changes=changes), self.assertRaises(UserError):
                validate_channel(replace(good, **changes), 303, 202)


class StorageTests(unittest.IsolatedAsyncioTestCase):
    async def prepared(self, root, count=3, matching=False):
        store = Store(101, 202, root / "data", root / "exports")
        store.consent(True)
        if matching:
            store.add_reference("Hero", png(drawing(77)))
        matcher = FixtureMatcher()
        source = FixtureSource(count=count)
        runner = JobRunner(store, matcher, lambda: source)
        job = store.create_job("https://fixture.invalid/chapter", "chapter", "Hero" if matching else None,
                               crop_spec(), matcher.readiness() if matching else {},
                               mode="match" if matching else "collect-pages")
        await runner.run_job(job["id"])
        self.assertEqual(store.job(job["id"])["phase"], "review_ready")
        gateway = FixtureGateway()
        manager = StorageManager(store, gateway, storage_limit=50 * 1024 * 1024)
        await manager.configure(303, owner_id=101, guild_id=202, accept=True)
        return store, job, gateway, manager

    async def test_success_is_persistent_exact_native_and_restart_deduped(self):
        with workspace() as root:
            store, job, gateway, manager = await self.prepared(root)
            originals = [store.read_local(page["original_path"]) for page in store.pages(job["id"])]
            result = await manager.save(job["id"], owner_id=101, guild_id=202, mode="collection")
            self.assertEqual((result.sent, result.unknown, result.remaining), (3, 0, 0))
            self.assertEqual(result.links, ["https://discord.com/channels/202/303/1001"])
            uploaded = gateway.sent[0][2]
            self.assertEqual([digest(raw) for _, raw in uploaded], [row["crop_sha256"] for row in store.candidates(job["id"])])
            self.assertTrue(all(name.startswith("c_") and name.endswith(".png") for name, _ in uploaded))
            self.assertEqual(originals, [store.read_local(page["original_path"]) for page in store.pages(job["id"])])
            reopened = Store(101, 202, root / "data", root / "exports")
            reopened.recover()
            restarted = StorageManager(reopened, gateway, storage_limit=50 * 1024 * 1024)
            again = await restarted.save(job["id"], owner_id=101, guild_id=202, mode="collection")
            self.assertEqual((again.sent, again.already_sent), (0, 3))
            self.assertEqual(len(gateway.sent), 1)
            self.assertTrue(all(row["status"] == "sent" for row in reopened.receipts(job["id"])))

    async def test_only_accepted_matches_never_pending_or_reference_uploads(self):
        with workspace() as root:
            store, job, gateway, manager = await self.prepared(root, matching=True)
            with self.assertRaises(UserError):
                await manager.save(job["id"], owner_id=101, guild_id=202)
            with self.assertRaises(UserError):
                await manager.save(job["id"], owner_id=101, guild_id=202, mode="collection")
            candidates = store.candidates(job["id"])
            store.decide(candidates[0]["id"], "accepted")
            store.decide(candidates[1]["id"], "rejected")
            result = await manager.save(job["id"], owner_id=101, guild_id=202)
            self.assertEqual(result.sent, 1)
            self.assertEqual(gateway.sent[0][2][0][1], store.read_local(candidates[0]["crop_path"]))
            self.assertNotEqual(gateway.sent[0][2][0][1], store.read_local(job["refs"][0]["path"]))

    async def test_unknown_send_and_restart_sending_never_blindly_retry(self):
        with workspace() as root:
            store, job, gateway, manager = await self.prepared(root)
            gateway.failure = ConnectionError("sensitive upstream response must never escape")
            with self.assertRaisesRegex(UserError, "unknown") as caught:
                await manager.save(job["id"], owner_id=101, guild_id=202, mode="collection")
            self.assertNotIn("sensitive", str(caught.exception))
            self.assertTrue(all(row["status"] == "unknown" for row in store.receipts(job["id"])))
            gateway.failure = None
            reopened = Store(101, 202, root / "data", root / "exports")
            reopened.recover()
            result = await StorageManager(reopened, gateway, storage_limit=50 * 1024 * 1024).save(job["id"], owner_id=101, guild_id=202, mode="collection")
            self.assertEqual((result.sent, result.unknown), (0, 3))
            self.assertEqual(len(gateway.sent), 1)
            with reopened.db() as db:
                db.execute("UPDATE storage_receipts SET status='sending'")
            reopened.recover()
            self.assertTrue(all(row["status"] == "unknown" for row in reopened.receipts(job["id"])))

    async def test_cancelled_network_send_is_unknown_not_retried(self):
        with workspace() as root:
            store, job, gateway, manager = await self.prepared(root, count=1)
            gateway.failure = asyncio.CancelledError()
            with self.assertRaisesRegex(UserError, "unknown"):
                await manager.save(job["id"], owner_id=101, guild_id=202, mode="collection")
            self.assertEqual(store.receipts(job["id"])[0]["status"], "unknown")

    async def test_channel_that_becomes_public_stops_next_batch_and_resume_revalidates(self):
        with workspace() as root:
            store, job, gateway, manager = await self.prepared(root, count=9)
            gateway.after_send = lambda: setattr(gateway, "state", replace(gateway.state, everyone_visible=True))
            with self.assertRaisesRegex(UserError, "@everyone"):
                await manager.save(job["id"], owner_id=101, guild_id=202, mode="collection")
            self.assertEqual(len(gateway.sent), 1)
            self.assertEqual(len(store.receipts(job["id"])), 8)
            with self.assertRaises(UserError):
                await manager.save(job["id"], owner_id=101, guild_id=202, mode="collection")
            gateway.after_send = None
            gateway.state = replace(gateway.state, everyone_visible=False)
            result = await manager.save(job["id"], owner_id=101, guild_id=202, mode="collection")
            self.assertEqual((result.sent, result.already_sent), (1, 8))
            self.assertGreaterEqual(len(gateway.inspections), 6)

    async def test_revoke_before_send_releases_definitely_unsent_receipts(self):
        with workspace() as root:
            store, job, gateway, manager = await self.prepared(root, count=1)
            snapshot = store.storage_snapshot
            def revoke_after_snapshot(owner, guild):
                result = snapshot(owner, guild)
                store.consent(False)
                return result
            store.storage_snapshot = revoke_after_snapshot
            with self.assertRaises(UserError):
                await manager.save(job["id"], owner_id=101, guild_id=202, mode="collection")
            self.assertEqual(gateway.sent, [])
            self.assertEqual(store.receipts(job["id"]), [])
            store.storage_snapshot = snapshot
            store.consent(True)
            self.assertEqual((await manager.save(job["id"], owner_id=101, guild_id=202, mode="collection")).sent, 1)

    async def test_unset_foreign_revoked_destination_and_oversize_no_fallback(self):
        with workspace() as root:
            store, job, gateway, manager = await self.prepared(root, count=1)
            for owner, guild in ((999, 202), (101, 999)):
                with self.assertRaises(UserError):
                    await manager.save(job["id"], owner_id=owner, guild_id=guild, mode="collection")
            store.set_storage_destination(None, False)
            with self.assertRaises(UserError):
                await manager.save(job["id"], owner_id=101, guild_id=202, mode="collection")
            await manager.configure(303, owner_id=101, guild_id=202, accept=True)
            original = store.read_local(store.candidates(job["id"])[0]["crop_path"])
            gateway.state = replace(gateway.state, byte_limit=1)
            result = await manager.save(job["id"], owner_id=101, guild_id=202, mode="collection")
            self.assertEqual((result.sent, result.oversized), (0, 1))
            self.assertEqual(gateway.sent, [])
            self.assertEqual(store.receipts(job["id"]), [])
            self.assertEqual(store.read_local(store.candidates(job["id"])[0]["crop_path"]), original)

    async def test_storage_can_be_revoked_after_global_consent_without_network(self):
        with workspace() as root:
            store = Store(101, 202, root / "data", root / "exports")
            store.consent(True)
            gateway = FixtureGateway()
            manager = StorageManager(store, gateway, storage_limit=50 * 1024 * 1024)
            await manager.configure(303, owner_id=101, guild_id=202, accept=True)
            store.consent(False)
            inspections = list(gateway.inspections)
            for owner, guild in ((999, 202), (101, 999)):
                with self.assertRaises(UserError):
                    await manager.configure(None, owner_id=owner, guild_id=guild, accept=False)
                self.assertIs(store.storage_destination()["accepted"], True)
            await manager.configure(None, owner_id=101, guild_id=202, accept=False)
            self.assertIs(store.storage_destination()["accepted"], False)
            self.assertFalse(store.has_consent())
            with self.assertRaises(UserError):
                await manager.configure(303, owner_id=101, guild_id=202, accept=True)
            with self.assertRaises(UserError):
                store.set_storage_destination(303, True)
            self.assertEqual(gateway.inspections, inspections)
            self.assertEqual(gateway.sent, [])

    async def test_final_store_lock_wait_does_not_block_event_loop_heartbeat(self):
        with workspace() as root:
            store, job, gateway, manager = await self.prepared(root, count=1)
            entered = threading.Event()
            snapshot = store.storage_snapshot
            def contended_snapshot(owner, guild):
                with store.lock:
                    entered.set()
                    time.sleep(0.25)
                    return snapshot(owner, guild)
            store.storage_snapshot = contended_snapshot
            saving = asyncio.create_task(manager.save(job["id"], owner_id=101, guild_id=202, mode="collection"))
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait, 30))
                started = time.monotonic()
                for _ in range(10):
                    await asyncio.sleep(0.005)
                self.assertLess(time.monotonic() - started, 0.20)
                self.assertFalse(saving.done())
                self.assertEqual((await saving).sent, 1)
            finally:
                await saving
