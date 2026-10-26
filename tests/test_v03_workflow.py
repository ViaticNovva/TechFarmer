"""v0.3 integration: real source parser, collection/native files, migration and caller-bound approval."""
import asyncio
from contextlib import redirect_stdout
from dataclasses import replace
from io import StringIO
import json
from pathlib import Path
import sqlite3
import unittest
from unittest.mock import AsyncMock, patch

from tech_farmer import __version__
from tech_farmer.config import Settings, load_settings, load_source_config, set_limit
from tech_farmer.engine import JobRunner, RunLimits, crop_spec
from tech_farmer.exports import export_job
from tech_farmer.images import decode, png
from tech_farmer.safety import UserError, digest
from tech_farmer.source_setup import prepare_source
from tech_farmer.sources import SourceClient
from tech_farmer.store import Store
from tests.helpers import FixtureMatcher, drawing, workspace
from tests.test_config_service import CapturingService, interaction
from tests.test_sources import BOOKS, CDN, FixtureTransport, Reply

SEED = BOOKS + "/series/garden/chapter-1"


class NoModel:
    def readiness(self):
        raise AssertionError("No-reference collection must not inspect or install a model")

    load = detect = readiness


def reader(*urls):
    return Reply(('<div class="reader">' + ''.join(f'<img src="{url}">' for url in urls) + '</div>').encode())


def runner_for(store, transport, matcher=None, **limits):
    async def prepare(url, **kwargs):
        return await prepare_source(url, transport=transport, **kwargs)
    return JobRunner(store, matcher or NoModel(), None, RunLimits(**limits), source_preparer=prepare,
                     prepared_source_factory=lambda config: SourceClient(config, transport))


class WorkflowTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_fixture_url_no_reference_native_collection_and_restart(self):
        with workspace() as root:
            store = Store(101, 202, root / "data", root / "exports")
            store.consent(True)
            raws = [png(drawing(index)) for index in (7, 8)]
            transport = FixtureTransport({SEED: reader("/images/1.png", "/images/2.png"),
                                          BOOKS + "/images/1.png": Reply(raws[0], content_type="image/png"),
                                          BOOKS + "/images/2.png": Reply(raws[1], content_type="image/png")})
            runner = runner_for(store, transport, pages_per_run=1)
            with patch("tech_farmer.config.load_source_config", return_value={"authorized": False, "profiles": []}):
                job = await runner.create_pull(SEED, Settings(101, 202, 303))
            self.assertEqual((job["mode"], job["refs"], job["model"]), ("collect-pages", [], {}))
            await runner.run_job(job["id"])
            self.assertEqual(store.job(job["id"])["phase"], "paused_budget")
            self.assertEqual(store.read_local(store.pages(job["id"])[0]["original_path"]), raws[0])
            reopened = Store(101, 202, root / "data", root / "exports")
            reopened.recover()
            reopened.control(job["id"], "resume")
            await runner_for(reopened, transport).run_job(job["id"])
            self.assertEqual(reopened.job(job["id"])["phase"], "review_ready")
            rows = reopened.candidates(job["id"])
            self.assertEqual(len(rows), 2)
            for index, row in enumerate(rows):
                image, _ = decode(reopened.read_local(row["crop_path"]))
                self.assertEqual(image.tobytes(), drawing(7 + index).tobytes())
                self.assertEqual(row["decision"], "collected")
                self.assertEqual(row["reference_indices"], [])
            plan = export_job(reopened, job["id"], 100000, mode="collection")
            public = json.loads((plan.folder / "provenance-discord.json").read_text())
            self.assertTrue(all(row["score_uncalibrated"] is None for row in public["crops"]))
            self.assertFalse(public["crops"][0]["inference"]["performed"])
            self.assertNotIn(BOOKS, json.dumps(public))

    async def test_url_and_multiple_frozen_references_invoke_matcher_not_collection(self):
        with workspace() as root:
            store = Store(101, 202, root / "data", root / "exports")
            store.consent(True)
            for index in (77, 78):
                store.add_reference("Hero", png(drawing(index)))
            raw = png(drawing(3))
            transport = FixtureTransport({SEED: reader("/images/1.png"), BOOKS + "/images/1.png": Reply(raw, content_type="image/png")})
            matcher = FixtureMatcher()
            runner = runner_for(store, transport, matcher)
            job = store.create_job(SEED, "auto", "Hero", crop_spec(), matcher.readiness())
            store.add_reference("Hero", png(drawing(99)))
            await runner.run_job(job["id"])
            self.assertEqual(store.job(job["id"])["phase"], "review_ready")
            self.assertEqual(matcher.calls[0]["refs"], [(77, 17, 23, 255), (78, 17, 23, 255)])
            self.assertEqual(store.candidates(job["id"])[0]["score"], 0.8)
            self.assertEqual(store.read_local(store.pages(job["id"])[0]["original_path"]), raw)

    async def test_external_origins_wait_then_bound_approval_reinspection_fetches(self):
        with workspace() as root:
            store = Store(101, 202, root / "data", root / "exports")
            store.consent(True)
            image_url = CDN + "/garden/page.png"
            transport = FixtureTransport({SEED: reader(image_url), image_url: Reply(png(drawing()), content_type="image/png")})
            runner = runner_for(store, transport)
            job = store.create_job(SEED, "chapter", None, crop_spec(), {}, mode="collect-pages")
            await runner.run_job(job["id"])
            current = store.job(job["id"])
            self.assertEqual(current["phase"], "awaiting_approval")
            self.assertEqual(current["source_proposal"]["required_origins"], [CDN])
            self.assertNotIn(image_url, transport.calls)
            with self.assertRaises(UserError):
                store.approve_source(job["id"], True)
            store.approve_source(job["id"], True, proposal_id=current["source_proposal"]["proposal_id"])
            store.control(job["id"], "resume")
            await runner.run_job(job["id"])
            self.assertEqual(store.job(job["id"])["phase"], "review_ready")
            self.assertIn(image_url, transport.calls)

    async def test_discord_confirmation_cannot_silently_approve_replaced_host_snapshot(self):
        with workspace() as root:
            store = Store(101, 202, root / "data", root / "exports")
            store.consent(True)
            proposals = []
            for host in ("https://first-cdn.example", "https://changed-cdn.example"):
                proposals.append(await prepare_source(SEED, rights_confirmed=True, transport=FixtureTransport({SEED: reader(host + "/p.png")})))
            job = store.create_job(SEED, "chapter", None, crop_spec(), {}, mode="collect-pages")
            store.set_source_proposal(job["id"], proposals[0].to_dict())
            store.set_phase(job["id"], "awaiting_approval")
            runner = runner_for(store, FixtureTransport())
            service = CapturingService(store, runner, Settings(101, 202, 303))
            shown = []
            service.view_factory = lambda kind, **kwargs: shown.append((kind, kwargs))
            await service.run(interaction(), "approve", job_id=job["id"])
            self.assertEqual(shown[-1][1]["proposal_id"], proposals[0].proposal_id)
            self.assertIn("first-cdn.example", service.messages[-1])
            store.set_source_proposal(job["id"], proposals[1].to_dict())
            await service.run(interaction(), "approve", job_id=job["id"], accept=True, proposal_id=proposals[0].proposal_id)
            self.assertIn("changed", service.messages[-1])
            self.assertEqual(store.job(job["id"])["approved_proposal"], {})
            self.assertEqual(store.job(job["id"])["phase"], "awaiting_approval")
            self.assertEqual(runner.scheduled, set())
            await service.run(interaction(), "approve", job_id=job["id"])
            self.assertIn("changed-cdn.example", service.messages[-1])
            await service.run(interaction(), "approve", job_id=job["id"], accept=True, proposal_id=shown[-1][1]["proposal_id"])
            self.assertEqual(store.job(job["id"])["approved_proposal"]["required_origins"], ["https://changed-cdn.example"])
            self.assertIn(job["id"], runner.scheduled)

    async def test_source_link_never_enters_attachment_downloader_and_local_file_stays_local(self):
        from tech_farmer.__main__ import local_reference
        with workspace() as root:
            store = Store(101, 202, root / "data", root / "exports")
            store.consent(True)
            runner = runner_for(store, FixtureTransport())
            service = CapturingService(store, runner, Settings(101, 202, 303))
            service.downloader = AsyncMock(side_effect=AssertionError("Source URLs are not attachment URLs"))
            with patch("tech_farmer.config.load_source_config", return_value={"authorized": False, "profiles": []}):
                await service.run(interaction(), "pull", url=SEED)
            service.downloader.assert_not_awaited()
            self.assertEqual(store.jobs()[0]["mode"], "collect-pages")
            local = root / "my-reference.png"
            local.write_bytes(png(drawing(55)))
            item = local_reference(store, "LocalHero", local, 50 * 1024 * 1024)
            self.assertEqual(store.read_local(item["path"]), local.read_bytes())
            self.assertFalse(item["path"].startswith("http"))

    async def test_new_commands_check_owner_and_update_is_check_only(self):
        with workspace() as root:
            store = Store(101, 202, root / "data", root / "exports")
            service = CapturingService(store, runner_for(store, FixtureTransport()), Settings(101, 202, 303))
            for action in ("pull", "approve", "storage", "save", "receipts", "settings", "reload", "version", "update"):
                await service.run(interaction(owner=999), action)
                self.assertIn("Restricted", service.messages[-1])
            with patch("tools.update.check_update", return_value={"status": "up_to_date", "message": "Verified fixed canonical check."}) as check, patch("subprocess.run", side_effect=AssertionError("Discord must not execute Python or a shell")):
                await service.run(interaction(), "update", url="https://untrusted.example", branch="evil")
            check.assert_called_once()
            self.assertEqual(len(check.call_args.args), 1)
            self.assertIn("No update was installed", service.messages[-1])
            await service.run(interaction(), "version")
            self.assertIn("Installed TechFarmer " + __version__, service.messages[-1])


class MigrationAndCliTests(unittest.TestCase):
    def test_schema_one_two_ids_and_limits_migrate_with_exact_backups(self):
        base = {"owner_id": "123456789012345678", "guild_id": "234567890123456789", "application_id": "345678901234567890", "pages_per_run": 7}
        with workspace() as root:
            for version in (1, 2):
                path = root / f"settings{version}.json"
                original = json.dumps({"schema_version": version, **base, "owner_id": int(base["owner_id"]) if version == 1 else base["owner_id"]}).encode()
                path.write_bytes(original)
                settings = load_settings(path, environ={})
                self.assertEqual((settings.owner_id, settings.pages_per_run), (int(base["owner_id"]), 7))
                self.assertEqual(json.loads(path.read_text())["schema_version"], 3)
                self.assertEqual(list(root.glob(path.name + ".backup-*"))[0].read_bytes(), original)
                self.assertEqual(load_settings(path, environ={}), settings)
            missing = load_source_config(settings, root / "absent.json")
            self.assertEqual(missing["profiles"], [])

    def test_settings_whitelist_no_identity_path_token_mutation(self):
        with workspace() as root:
            path = root / "settings.json"
            settings = Settings(123456789012345678, 234567890123456789, 345678901234567890)
            updated = set_limit(settings, "pages_per_run", 3, path)
            self.assertEqual(updated.pages_per_run, 3)
            before = path.read_bytes()
            for name in ("owner_id", "guild_id", "application_id", "storage_channel_id", "model_cache", "device", "token", "source_url"):
                with self.assertRaises(UserError):
                    set_limit(updated, name, 123, path)
                self.assertEqual(path.read_bytes(), before)
            for bad in (True, 0, -1, "3"):
                with self.assertRaises(UserError):
                    set_limit(updated, "pages_per_run", bad, path)

    def test_v02_database_additive_backup_keeps_job_ids_references_decisions(self):
        with workspace() as root:
            store = Store(101, 202, root / "data", root / "exports")
            store.consent(True)
            reference = store.add_reference("Hero", png(drawing(77)))
            job = store.create_job(SEED, "chapter", "Hero", crop_spec(), {})
            connection = sqlite3.connect(store.path)
            try:
                for column in ("mode", "source_config_json", "source_proposal_json", "approved_proposal_json"):
                    connection.execute("ALTER TABLE jobs DROP COLUMN " + column)
                connection.execute("DROP TABLE storage_receipts")
                connection.execute("PRAGMA user_version=0")
                connection.commit()
            finally:
                connection.close()
            reopened = Store(101, 202, root / "data", root / "exports")
            migrated = reopened.job(job["id"])
            self.assertEqual(migrated["id"], job["id"])
            self.assertEqual(migrated["refs"], job["refs"])
            self.assertEqual(migrated["mode"], "match")
            self.assertEqual(reopened.read_local(reference["path"]), png(drawing(77)))
            self.assertTrue(reopened.has_consent())
            self.assertTrue(list((root / "data" / "backups").glob("schema2-*.sqlite3")))
            self.assertTrue(reopened.notices)

    def test_compatible_backup_failure_does_not_switch_to_an_empty_database(self):
        with workspace() as root:
            store = Store(101, 202, root / "data", root / "exports")
            connection = sqlite3.connect(store.path)
            try:
                connection.execute("PRAGMA user_version=0")
                connection.commit()
            finally:
                connection.close()
            before = store.path.read_bytes()
            connect = sqlite3.connect
            class BackupFailure:
                def __init__(self, path, **kwargs):
                    self.connection = connect(path, **kwargs)
                def __getattr__(self, name):
                    return getattr(self.connection, name)
                def backup(self, target):
                    raise sqlite3.OperationalError("simulated disk backup failure")
            with patch("tech_farmer.store.sqlite3.connect", side_effect=BackupFailure):
                with self.assertRaisesRegex(UserError, "no replacement database"):
                    Store(101, 202, root / "data", root / "exports")
            self.assertEqual(store.path.read_bytes(), before)
            self.assertFalse((root / "data" / "tech-farmer-v03.sqlite3").exists())

    def test_incompatible_legacy_database_is_preserved_not_claimed_migrated(self):
        with workspace() as root:
            data = root / "data"
            data.mkdir()
            path = data / "tech-farmer.sqlite3"
            connection = sqlite3.connect(path)
            try:
                connection.execute("CREATE TABLE prototype_jobs (old_id TEXT)")
                connection.execute("INSERT INTO prototype_jobs VALUES ('keep-this')")
                connection.commit()
            finally:
                connection.close()
            before = path.read_bytes()
            store = Store(101, 202, data, root / "exports")
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(store.path.name, "tech-farmer-v03.sqlite3")
            self.assertIn("NOT migrated", store.notices[0])
            self.assertEqual(store.jobs(), [])
            again = Store(101, 202, data, root / "exports")
            self.assertEqual(again.path, store.path)
            self.assertEqual(path.read_bytes(), before)

    def test_version_needs_no_configuration_sdk_model_or_network(self):
        from tech_farmer.__main__ import main
        output = StringIO()
        with patch("tech_farmer.__main__.build_runner", side_effect=AssertionError("No runtime for version")), redirect_stdout(output):
            self.assertEqual(main(["version"]), 0)
        self.assertIn(__version__, output.getvalue())
        self.assertIn("https://github.com/ViaticNovva/TechFarmer", output.getvalue())

    def test_source_archive_is_create_only_manifest_allowlisted_and_excludes_private_data(self):
        import zipfile
        from tools import verify_manifest, update
        from tests.test_updater import install, new_files
        with workspace() as relative:
            root = relative.resolve()
            files = new_files()
            install(root, files)
            for name in (".env", "tech-farmer-settings.json", "data/private.bin", "exports/private.png", ".cache/models/private.bin", ".venv/private.txt"):
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"private fixture must never be packaged")
            expected = {name: digest(raw) for name, raw in files.items()}
            with patch.object(verify_manifest, "ROOT", root):
                with redirect_stdout(StringIO()):
                    self.assertEqual(verify_manifest.main(["--archive"]), 0)
                archive = root / ".runtime" / "ci-source" / "TechFarmer-source.zip"
                before = archive.read_bytes()
                with self.assertRaises(FileExistsError):
                    verify_manifest.archive_verified_source(expected)
                self.assertEqual(archive.read_bytes(), before)
                with zipfile.ZipFile(archive) as package:
                    self.assertEqual(set(package.namelist()), {"TechFarmer/" + name for name in {*files, update.MANIFEST}})
                    for name, raw in files.items():
                        self.assertEqual(package.read("TechFarmer/" + name), raw)
                    self.assertEqual(package.read("TechFarmer/" + update.MANIFEST), (root / update.MANIFEST).read_bytes())
                (root / "README.md").write_bytes(b"changed source\n")
                with self.assertRaisesRegex(ValueError, "Source changed"):
                    verify_manifest.archive_verified_source(expected)
                self.assertEqual(archive.read_bytes(), before)

    def test_manifest_cli_refuses_unmanaged_source_before_packaging(self):
        from contextlib import redirect_stderr
        from tools import verify_manifest
        from tests.test_updater import install, new_files
        with workspace() as relative:
            root = relative.resolve()
            install(root, new_files())
            (root / "unexpected.py").write_bytes(b'"""Not in the old updater allowlist."""\n')
            with patch.object(verify_manifest, "ROOT", root), redirect_stdout(StringIO()), redirect_stderr(StringIO()) as output:
                self.assertEqual(verify_manifest.main(["--archive"]), 1)
            self.assertIn("exact updater allowlist", output.getvalue())
            self.assertFalse((root / ".runtime" / "ci-source").exists())

    def test_manifest_excludes_runtime_private_backups_and_requires_nonempty_text(self):
        from tools import verify_manifest
        with workspace() as root:
            (root / "sample.py").write_bytes(b'"""source"""\n')
            (root / "tech-farmer-settings.json.backup-123").write_text('{"private":"local"}\n')
            (root / ".runtime").mkdir()
            (root / ".runtime" / "techfarmer.lock").write_bytes(b"lock")
            with patch.object(verify_manifest, "ROOT", root.resolve()):
                self.assertEqual(set(verify_manifest.inventory()), {"sample.py"})
                with self.subTest(line_endings="CRLF"):
                    (root / "sample.py").write_bytes(b'"""source"""\r\n')
                    with self.assertRaisesRegex(ValueError, "LF newlines"):
                        verify_manifest.inventory()
                (root / "sample.py").write_text("")
                with self.assertRaises(ValueError):
                    verify_manifest.inventory()
