"""Durability, pixels, consent, frozen references and export tests without Discord/network."""
import asyncio
from dataclasses import replace
import json
import unittest
from PIL import Image
from io import BytesIO

from tech_farmer.engine import JobRunner, RunLimits, crop_spec
from tech_farmer.exports import batch_paths, export_job
from tech_farmer.images import crop_native, decode, png
from tech_farmer.safety import UserError, digest
from tech_farmer.store import Store
from tests.helpers import FixtureMatcher, FixtureSource, drawing, workspace


class PipelineTests(unittest.IsolatedAsyncioTestCase):
    def make(self, root, **limits):
        store = Store(101, 202, root / "data", root / "exports")
        store.consent(True)
        store.add_reference("Hero", png(drawing(77)))
        matcher, source = FixtureMatcher(), FixtureSource()
        runner = JobRunner(store, matcher, lambda: source, RunLimits(**limits))
        job = store.create_job("https://fixture.invalid/series", "series", "Hero", crop_spec(), matcher.readiness())
        return store, matcher, source, runner, job

    async def test_automatic_whole_source_exact_pixels_and_private_exports(self):
        with workspace() as root:
            store, matcher, source, runner, job = self.make(root)
            before = png(drawing(0))
            await runner.run_job(job["id"])
            self.assertEqual(store.summary(job["id"])["phase"], "review_ready")
            self.assertEqual(source.fetched, [0, 1, 2])
            self.assertEqual(len(matcher.calls), 3)
            self.assertEqual(matcher.calls[0]["refs"], [(77, 17, 23, 255)])
            candidates = store.candidates(job["id"])
            self.assertEqual(len(candidates), 3)
            first = candidates[0]
            page = store.page(first["page_id"])
            self.assertEqual(store.read_local(page["original_path"]), before)
            actual, _ = decode(store.read_local(first["crop_path"]))
            self.assertEqual(actual.size, (40, 56))
            self.assertEqual(actual.tobytes(), drawing(0).crop((20, 24, 60, 80)).tobytes())
            for item in candidates:
                store.decide(item["id"], "accepted")
            plan = export_job(store, job["id"], 10000)
            self.assertEqual(plan.count, 3)
            self.assertFalse(plan.partial_source)
            public = (plan.folder / "provenance-discord.json").read_text()
            local = (plan.folder / "provenance-local.json").read_text()
            self.assertNotIn("fixture.invalid", public)
            self.assertNotIn("private=", public)
            self.assertIn("https://fixture.invalid/series", local)
            self.assertNotIn("private=", local)
            files = list(plan.folder.glob("c_*.png"))
            self.assertEqual(len(files), 3)
            self.assertIn(store.read_local(first["crop_path"]), [path.read_bytes() for path in files])

    async def test_budget_pause_resume_preserves_completed_page(self):
        with workspace() as root:
            store, matcher, source, runner, job = self.make(root, pages_per_run=1)
            await runner.run_job(job["id"])
            self.assertEqual(store.job(job["id"])["phase"], "paused_budget")
            self.assertEqual(len(matcher.calls), 1)
            store.control(job["id"], "resume")
            await runner.run_job(job["id"])
            self.assertEqual(source.fetched, [0, 1])
            self.assertEqual(len(store.pages(job["id"])), 3)
            store.control(job["id"], "resume")
            await runner.run_job(job["id"])
            self.assertEqual(store.job(job["id"])["phase"], "review_ready")
            self.assertEqual(source.fetched, [0, 1, 2])

    async def test_restart_pauses_and_explicit_resume_dedupes(self):
        with workspace() as root:
            store, matcher, source, runner, job = self.make(root, pages_per_run=1)
            await runner.run_job(job["id"])
            store.control(job["id"], "resume")
            reopened = Store(101, 202, root / "data", root / "exports")
            self.assertEqual(reopened.recover(), 1)
            self.assertEqual(reopened.job(job["id"])["phase"], "paused_restart")
            reopened.control(job["id"], "resume")
            resumed = JobRunner(reopened, matcher, lambda: source)
            await resumed.run_job(job["id"])
            self.assertEqual(source.fetched, [0, 1, 2])
            self.assertEqual(len(reopened.candidates(job["id"])), 3)

    async def test_failure_is_partial_and_retry_does_not_repeat_successes(self):
        with workspace() as root:
            store, matcher, source, runner, job = self.make(root)
            source.fail_pages = {1}
            await runner.run_job(job["id"])
            self.assertEqual(store.job(job["id"])["phase"], "review_partial")
            self.assertEqual(store.summary(job["id"])["pages"], {"done": 2, "failed": 1})
            source.fail_pages.clear()
            store.control(job["id"], "resume")
            await runner.run_job(job["id"])
            self.assertEqual(source.fetched, [0, 1, 2, 1])
            self.assertEqual(store.job(job["id"])["phase"], "review_ready")

    async def test_partial_discovery_never_marked_complete(self):
        with workspace() as root:
            store, matcher, source, runner, job = self.make(root)
            source.complete = False
            await runner.run_job(job["id"])
            self.assertEqual(store.job(job["id"])["phase"], "paused_source")
            self.assertFalse(store.summary(job["id"])["discovery_complete"])

    async def test_missing_model_fails_closed_without_fetching(self):
        with workspace() as root:
            store, matcher, source, runner, job = self.make(root)
            matcher.failure = RuntimeError("Unavailable local weights")
            await runner.run_job(job["id"])
            self.assertEqual(store.job(job["id"])["phase"], "paused_model")
            self.assertEqual(source.discovered, 0)
            self.assertEqual(matcher.calls, [])
            self.assertEqual(store.candidates(job["id"]), [])

    async def test_cancel_and_revocation_stop_before_work(self):
        with workspace() as root:
            store, matcher, source, runner, job = self.make(root)
            store.control(job["id"], "cancel")
            await runner.run_job(job["id"])
            self.assertEqual(store.job(job["id"])["phase"], "cancelled")
            self.assertEqual(source.fetched, [])
            with self.assertRaises(UserError):
                store.control(job["id"], "resume")
            other = store.create_job("https://fixture.invalid/series", "series", "Hero", crop_spec(), matcher.readiness())
            store.consent(False)
            await runner.run_job(other["id"])
            self.assertEqual(store.job(other["id"])["phase"], "paused")
            self.assertEqual(matcher.calls, [])

    async def test_exact_duplicate_page_is_detected_once_with_alias(self):
        with workspace() as root:
            store, matcher, source, runner, job = self.make(root)
            source.same_bytes = True
            await runner.run_job(job["id"])
            self.assertEqual(len(matcher.calls), 1)
            self.assertEqual(len(store.candidates(job["id"])), 1)
            pages = store.pages(job["id"])
            self.assertEqual(pages[1]["duplicate_of"], pages[0]["id"])
            self.assertEqual(pages[2]["duplicate_of"], pages[0]["id"])

    async def test_frozen_identity_profiles_and_separate_framing_reference(self):
        with workspace() as root:
            store, matcher, source, runner, job = self.make(root)
            store.add_reference("Other", png(drawing(23)))
            store.add_reference("Hero", png(drawing(99)))
            store.add_reference("Hero", png(drawing(55, (64, 64))), "framing")
            await runner.run_job(job["id"])
            self.assertEqual(matcher.calls[0]["refs"], [(77, 17, 23, 255)])
            second = store.create_job("https://fixture.invalid/series", "series", "Hero", crop_spec("reference"), matcher.readiness())
            await runner.run_job(second["id"])
            self.assertEqual(matcher.calls[-1]["refs"], [(77, 17, 23, 255), (99, 17, 23, 255)])
            candidate = store.candidates(second["id"])[0]
            self.assertEqual(candidate["box"][2] - candidate["box"][0], candidate["box"][3] - candidate["box"][1])

    async def test_semantic_requests_hold_for_optional_reframe(self):
        with workspace() as root:
            store, matcher, source, runner, job = self.make(root)
            semantic = store.create_job("https://fixture.invalid/series", "series", "Hero", crop_spec("face"), matcher.readiness())
            await runner.run_job(semantic["id"])
            item = store.candidates(semantic["id"])[0]
            self.assertIsNone(item["crop_path"])
            with self.assertRaises(UserError):
                store.decide(item["id"], "accepted")
            revised = runner.reframe(item["id"], crop_spec("tight", "1:1", trim="0,4,0,4", fit="cover"))
            self.assertIsNotNone(revised["crop_path"])
            self.assertEqual(revised["decision"], "pending")
            store.decide(item["id"], "accepted")

    async def test_crop_memory_pause_keeps_entire_page_pending(self):
        with workspace() as root:
            store, matcher, source, runner, job = self.make(root, page_crop_bytes=1)
            await runner.run_job(job["id"])
            state = store.summary(job["id"])
            self.assertEqual(state["phase"], "paused_budget")
            self.assertEqual(state["code"], "page_crop_memory_budget")
            self.assertEqual(state["pages"], {"pending": 3})
            self.assertEqual(store.candidates(job["id"]), [])
            runner.limits = replace(runner.limits, page_crop_bytes=1024 * 1024)
            store.control(job["id"], "resume")
            await runner.run_job(job["id"])
            self.assertEqual(store.job(job["id"])["phase"], "review_ready")
            self.assertEqual(len(store.candidates(job["id"])), 3)

    async def test_rediscovery_missing_any_known_page_pauses_without_substitution(self):
        with workspace() as root:
            store, matcher, source, runner, job = self.make(root, pages_per_run=1)
            await runner.run_job(job["id"])
            first = store.candidates(job["id"])[0]
            source.count = 2
            store.control(job["id"], "resume")
            await runner.run_job(job["id"])
            self.assertEqual(store.job(job["id"])["phase"], "paused_source")
            self.assertEqual(store.job(job["id"])["last_code"], "rediscovery_missing_known_pages")
            self.assertFalse(store.summary(job["id"])["discovery_complete"])
            self.assertEqual(source.fetched, [0])
            self.assertEqual(store.candidates(job["id"])[0], first)

    async def test_reframe_at_storage_budget_preserves_candidate_and_original(self):
        from tech_farmer.safety import StorageBudgetExceeded
        with workspace() as root:
            store, matcher, source, runner, job = self.make(root)
            await runner.run_job(job["id"])
            item = store.candidates(job["id"])[0]
            original = store.read_local(item["crop_path"])
            files_before = sorted(str(path) for path in store.root.rglob("*.png"))
            runner.limits = replace(runner.limits, storage_bytes=store.storage_bytes())
            with self.assertRaises(StorageBudgetExceeded):
                runner.reframe(item["id"], crop_spec("tight", "1:1", fit="cover"))
            self.assertEqual(store.candidate(item["id"]), item)
            self.assertEqual(store.read_local(item["crop_path"]), original)
            self.assertEqual(sorted(str(path) for path in store.root.rglob("*.png")), files_before)

    async def test_worker_outlives_enqueue_and_stops_cleanly(self):
        with workspace() as root:
            store, matcher, source, runner, job = self.make(root)
            await runner.start()
            self.assertEqual(store.job(job["id"])["phase"], "paused_restart")
            store.control(job["id"], "resume")
            await runner.schedule(job["id"])
            await runner.queue.join()
            self.assertEqual(store.job(job["id"])["phase"], "review_ready")
            await runner.close()


class LocalPixelsTests(unittest.TestCase):
    def test_shape_mask_and_original_preservation(self):
        image = drawing()
        before = image.tobytes()
        cropped = crop_native(image, [20, 24, 60, 64], "circle")
        self.assertEqual(cropped.size, (40, 40))
        self.assertEqual(cropped.getpixel((0, 0))[3], 0)
        self.assertEqual(cropped.getpixel((20, 20)), image.getpixel((40, 44)))
        self.assertEqual(image.tobytes(), before)

    def test_oversized_export_stays_local_and_batching_never_changes_bytes(self):
        with workspace() as root:
            paths = []
            for index, count in enumerate((3, 4, 12, 2, 5)):
                path = root / f"file-{index}.png"
                path.write_bytes(bytes([index]) * count)
                paths.append(path)
            before = [path.read_bytes() for path in paths]
            batches, skipped = batch_paths(paths, 8, file_limit=2)
            self.assertEqual([[path.name for path in batch] for batch in batches], [["file-0.png", "file-1.png"], ["file-3.png", "file-4.png"]])
            self.assertEqual(skipped, ["file-2.png"])
            self.assertEqual(before, [path.read_bytes() for path in paths])

    def test_owner_scope_and_consent(self):
        with workspace() as root:
            store = Store(101, 202, root / "data", root / "exports")
            with self.assertRaises(UserError):
                store.add_reference("Hero", png(drawing()))
            with self.assertRaises(UserError):
                Store(102, 202, root / "data", root / "exports")

    def test_concurrent_resume_cancel_never_loses_cancel_intent(self):
        from concurrent.futures import ThreadPoolExecutor
        import threading
        with workspace() as root:
            store = Store(101, 202, root / "data", root / "exports")
            store.consent(True)
            store.add_reference("Hero", png(drawing()))
            for _ in range(3):
                job = store.create_job("https://fixture.invalid/series", "series", "Hero", crop_spec(), {})
                store.set_phase(job["id"], "paused")
                barrier = threading.Barrier(2)
                def control(operation):
                    barrier.wait()
                    try:
                        return store.control(job["id"], operation)
                    except UserError:
                        return None
                with ThreadPoolExecutor(max_workers=2) as executor:
                    resume = executor.submit(control, "resume")
                    cancel = executor.submit(control, "cancel")
                    resume.result()
                    cancel.result()
                state = store.job(job["id"])
                self.assertEqual(state["control"], "cancel")
                with self.assertRaises(UserError):
                    store.control(job["id"], "resume")
                with self.assertRaises(UserError):
                    store.control(job["id"], "pause")
                store.set_phase(job["id"], "review_ready")
                self.assertEqual(store.job(job["id"])["phase"], "cancelled")

    def test_seed_credentials_and_unsigned_routing_state(self):
        with workspace() as root:
            store = Store(101, 202, root / "data", root / "exports")
            store.consent(True)
            store.add_reference("Hero", png(drawing()))
            for url in ("https://user:password@fixture.invalid/series", "https://fixture.invalid/series#secret", "https://fixture.invalid/series?token=private", "https://fixture.invalid/series?chapter=1"):
                with self.assertRaises(UserError):
                    store.create_job(url, "series", "Hero", crop_spec(), {})
            self.assertEqual(store.jobs(), [])
            job = store.create_job("https://fixture.invalid/series?chapter=1", "chapter", "Hero", crop_spec(), {}, routing_keys=("chapter",))
            self.assertEqual(store.job(job["id"])["source_url"], "https://fixture.invalid/series?chapter=1")
            with self.assertRaises(UserError):
                store.create_job("https://fixture.invalid/series?token=private", "series", "Hero", crop_spec(), {}, routing_keys=("token",))

    def test_incremental_crop_memory_and_candidate_limits(self):
        import random
        import threading
        from unittest.mock import patch
        from tech_farmer import engine
        from tech_farmer.safety import CandidateBudgetExceeded, CropBudgetExceeded, StorageBudgetExceeded
        page = Image.frombytes("RGB", (128, 128), random.Random(17).randbytes(128 * 128 * 3)).convert("RGBA")
        matcher = FixtureMatcher(hits=50)
        runner = JobRunner(None, matcher, None, RunLimits(page_crop_bytes=100000))
        job = {"threshold": 0.15, "spec": crop_spec(padding="30000,30000,30000,30000")}
        with patch.object(engine, "render_candidate", wraps=engine.render_candidate) as render:
            with self.assertRaises(CandidateBudgetExceeded):
                runner._match_page(page, [drawing()], job, None, threading.Event(), remaining_candidates=1, remaining_bytes=10000000)
            self.assertEqual(render.call_count, 0)
        with patch.object(engine, "render_candidate", wraps=engine.render_candidate) as render:
            with self.assertRaises(CropBudgetExceeded):
                runner._match_page(page, [drawing()], job, None, threading.Event(), remaining_candidates=50, remaining_bytes=10000000)
            self.assertLess(render.call_count, 50)
            self.assertLessEqual(render.call_count, 3)
        with self.assertRaises(StorageBudgetExceeded):
            runner._match_page(page, [drawing()], job, None, threading.Event(), remaining_candidates=50, remaining_bytes=50000)
        with self.assertRaises(CropBudgetExceeded):
            png(page, byte_limit=16)

    def test_native_aspect_and_directional_context(self):
        from tech_farmer.framing import plan_crop
        image = drawing()
        spec = crop_spec(padding="4,0,12,0", trim="0,2,0,6")
        result = plan_crop(image.size, [20, 24, 60, 80], {k: v for k, v in spec.items() if k != "shape"})
        self.assertEqual(result["box"], [16, 26, 72, 74])
        crop = crop_native(image, result["box"])
        self.assertEqual(crop.tobytes(), image.crop((16, 26, 72, 74)).tobytes())
