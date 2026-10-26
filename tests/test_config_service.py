"""Nonsecret setup and owner/guild routing; no network or Discord SDK doubles."""
import asyncio
import json
import os
from pathlib import Path
from types import SimpleNamespace
import unittest

from tech_farmer.config import load_settings, snowflake
from tech_farmer.engine import JobRunner, crop_spec
from tech_farmer.images import png
from tech_farmer.safety import UserError
from tech_farmer.service import FarmerService, attachment_limit
from tech_farmer.store import Store
from tests.helpers import FixtureMatcher, FixtureSource, drawing, workspace


class ConfigTests(unittest.TestCase):
    def test_nonsecret_settings_round_trip_and_unknown_secret_refused(self):
        with workspace() as root:
            path = root / "settings.json"
            data = {"schema_version": 2, "owner_id": "123456789012345678", "guild_id": "234567890123456789", "application_id": "345678901234567890", "device": "cpu", "model_cache": ".cache/models", "pages_per_run": 3}
            path.write_text(json.dumps(data))
            settings = load_settings(path, environ={})
            self.assertEqual(settings.pages_per_run, 3)
            self.assertEqual(settings.device, "cpu")
            data["token"] = "not-a-real-token"
            path.write_text(json.dumps(data))
            with self.assertRaises(UserError):
                load_settings(path, environ={})

    def test_ids_paths_and_ranges(self):
        for value in ("", "01", "123456789012345678 ", "18446744073709551616", "secret.value"):
            with self.assertRaises(UserError):
                snowflake(value, "id")
        with workspace() as root:
            path = root / "settings.json"
            data = {"schema_version": 2, "owner_id": "123456789012345678", "guild_id": "234567890123456789", "application_id": "345678901234567890"}
            for key, bad in (("model_cache", "../escape"), ("device", "cloud"), ("pages_per_run", True), ("max_tiles", 33), ("detections_per_page", 1001)):
                path.write_text(json.dumps({**data, key: bad}))
                with self.assertRaises(UserError, msg=key):
                    load_settings(path, environ={})

    def test_crop_settings_reject_bad_values_and_no_anatomical_guess(self):
        for kwargs in ({"padding": "1,2,3"}, {"trim": "-1,0,0,0"}, {"aspect": "nan"}, {"shape": "circle", "aspect": "4:5"}):
            with self.assertRaises(UserError):
                crop_spec(**kwargs)
        self.assertEqual(crop_spec("face")["kind"], "face")


WHEEL_PLAN = json.dumps({"install": [{"metadata": {"version": "1.0.0"}, "download_info": {"url": "https://fixture.invalid/fixture-1.0.0-py3-none-any.whl"}}]}).encode()


class BootstrapTests(unittest.TestCase):
    """Version simulation and injected processes are NOT native Python 3.15 evidence."""
    @staticmethod
    def runtime(root, version=(3, 12, 15)):
        from tech_farmer.__main__ import PythonRuntime
        return PythonRuntime((str(root / ".venv" / "Scripts" / "python.exe"),), version,
                             str(root / ".venv" / "Scripts" / "python.exe"), str(root / ".venv"), str(root / "base"))

    def test_simulated_standard_python_matrix_and_updater_gates_agree(self):
        from contextlib import redirect_stdout, redirect_stderr
        from io import StringIO
        from unittest.mock import patch
        import tech_farmer
        from tech_farmer.__main__ import main
        from tools import update
        for minor in range(10, 17):
            with self.subTest(simulated_minor=minor), patch.object(tech_farmer.sys, "version_info", (3, minor, 0, "final", 0)), patch.object(tech_farmer.sysconfig, "get_config_var", return_value=0), redirect_stdout(StringIO()), redirect_stderr(StringIO()), patch("builtins.input", return_value=""):
                expected = 0 if 11 <= minor <= 15 else 2
                self.assertEqual(main(["version"]), expected)
                self.assertEqual(update.main([]), expected)
        self.assertFalse(tech_farmer.supported_python((3, 15), "pypy", False))
        self.assertFalse(tech_farmer.supported_python((3, 15), "cpython", True))

    def test_preview_interpreters_are_rejected_not_mislabeled_as_stable_315(self):
        from contextlib import redirect_stdout, redirect_stderr
        from io import StringIO
        import subprocess
        from unittest.mock import patch
        import tech_farmer
        from tech_farmer import __main__ as cli
        from tools import update
        for level in ("alpha", "beta", "candidate"):
            with self.subTest(releaselevel=level), patch.object(tech_farmer.sys, "version_info", (3, 15, 0, level, 1)), patch.object(tech_farmer.sysconfig, "get_config_var", return_value=0), redirect_stdout(StringIO()), redirect_stderr(StringIO()):
                self.assertFalse(tech_farmer.supported_python())
                self.assertIn(level, tech_farmer.runtime_label())
                self.assertEqual(cli.main(["version"]), 2)
                self.assertEqual(update.main([]), 2)
            data = {"version": [3, 15, 0], "releaselevel": level, "serial": 1, "implementation": "cpython",
                    "free_threaded": False, "executable": "fixture-python", "prefix": "fixture", "base_prefix": "fixture"}
            with patch.object(cli.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, json.dumps(data), "")):
                self.assertIsNone(cli.probe_python(("py", "-3.15")))
        self.assertIn("sys.version_info.releaselevel", cli.PYTHON_PROBE)
        self.assertIn("sys.version_info.serial", cli.PYTHON_PROBE)

    def test_probe_uses_quoted_argument_list_and_reports_actual_version(self):
        from unittest.mock import patch
        from tech_farmer import __main__ as cli
        import subprocess
        command = (str(Path.cwd() / "Tech Farmer Å 日本 !" / "python.exe"),)
        data = {"version": [3, 15, 0], "releaselevel": "final", "serial": 0, "implementation": "cpython", "free_threaded": False,
                "prefix": "fixture-env", "base_prefix": "fixture-base", "executable": command[0]}
        with patch.object(cli.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, json.dumps(data), "")) as run:
            result = cli.probe_python(command)
        self.assertEqual(result.version, (3, 15, 0))
        self.assertIn(command[0], result.label)
        self.assertEqual(run.call_args.args[0], [*command, "-I", "-c", cli.PYTHON_PROBE])
        self.assertFalse(run.call_args.kwargs["shell"])
        for bad in ("{}", "not JSON", json.dumps({**data, "version": [3, 16, 0]}), json.dumps({**data, "free_threaded": True}), json.dumps({**data, "releaselevel": None})):
            with patch.object(cli.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, bad, "")):
                self.assertIsNone(cli.probe_python(command))

    def test_new_install_prefers_simulated_315_and_explicitly_labels_fallback(self):
        from unittest.mock import patch
        from tech_farmer import __main__ as cli
        with workspace() as relative:
            root = relative.resolve()
            future = self.runtime(root, (3, 15, 0))
            with patch.object(cli, "ROOT", root), patch.object(cli, "probe_python", return_value=future) as probe:
                self.assertEqual(cli.choose_python(windows=True).version, (3, 15, 0))
                probe.assert_called_once_with(("py", "-3.15"))
            older = self.runtime(root, (3, 14, 0))
            with patch.object(cli, "ROOT", root), patch.object(cli, "probe_python", side_effect=[None, older]) as probe:
                self.assertIn("3.14.0", cli.choose_python(windows=True).label)
                self.assertEqual([call.args[0] for call in probe.call_args_list], [("py", "-3.15"), ("py", "-3.14")])

    def test_existing_venv_kept_and_broken_venv_never_silently_recreated(self):
        from unittest.mock import patch
        from tech_farmer import __main__ as cli
        with workspace() as relative:
            root = relative.resolve()
            (root / ".venv").mkdir()
            marker = root / ".venv" / "keep-fixture"
            marker.write_bytes(b"existing environment")
            old = self.runtime(root)
            with patch.object(cli, "ROOT", root), patch.object(cli, "probe_python", return_value=old) as probe:
                self.assertEqual(cli.choose_python(windows=True), old)
                self.assertEqual(probe.call_count, 1)
                self.assertIn(".venv", probe.call_args.args[0][0])
            with patch.object(cli, "ROOT", root), patch.object(cli, "probe_python", return_value=None) as probe:
                with self.assertRaisesRegex(UserError, "NOT deleted or rebuilt"):
                    cli.choose_python(windows=True)
                self.assertEqual(probe.call_count, 1)
            self.assertEqual(marker.read_bytes(), b"existing environment")

    def test_wheel_failure_keeps_existing_venv_data_and_approved_configuration(self):
        from contextlib import nullcontext, redirect_stdout
        from io import StringIO
        import os
        import subprocess
        from unittest.mock import patch
        from tech_farmer import __main__ as cli
        with workspace() as relative:
            root = relative.resolve()
            (root / ".venv").mkdir()
            marker = root / ".venv" / "keep-fixture"
            marker.write_bytes(b"existing environment")
            policy = {"PIP_INDEX_URL": "https://approved.fixture.invalid/simple", "DISCORD_BOT_TOKEN": "fixture-secret-do-not-log"}
            output = StringIO()
            with patch.object(cli, "ROOT", root), patch.object(cli, "verify_source"), patch.object(cli, "_requirements"), patch.object(cli, "choose_python", return_value=self.runtime(root)), patch("tools.update.RuntimeLock", return_value=nullcontext()), patch.dict(os.environ, policy), patch.object(cli.subprocess, "run", return_value=subprocess.CompletedProcess([], 1)) as run, redirect_stdout(output):
                with self.assertRaisesRegex(UserError, "before installation"):
                    cli.install_dependencies()
            self.assertEqual(run.call_count, 1)
            command = run.call_args.args[0]
            self.assertIn("--dry-run", command)
            self.assertIn("--ignore-installed", command)
            self.assertIn("--only-binary=:all:", command)
            self.assertNotIn("--ignore-requires-python", command)
            self.assertFalse(run.call_args.kwargs["shell"])
            self.assertEqual(run.call_args.kwargs["env"]["PIP_INDEX_URL"], policy["PIP_INDEX_URL"])
            self.assertNotIn("DISCORD_BOT_TOKEN", run.call_args.kwargs["env"])
            self.assertNotIn(policy["DISCORD_BOT_TOKEN"], output.getvalue())
            self.assertIn("3.12.15", output.getvalue())
            self.assertEqual(marker.read_bytes(), b"existing environment")

    def test_installer_import_failure_is_not_reported_as_success(self):
        from contextlib import nullcontext, redirect_stdout
        from io import StringIO
        import subprocess
        from unittest.mock import patch
        from tech_farmer import __main__ as cli
        with workspace() as relative:
            root = relative.resolve()
            (root / ".venv").mkdir()
            with patch.object(cli, "ROOT", root), patch.object(cli, "verify_source"), patch.object(cli, "_requirements"), patch.object(cli, "choose_python", return_value=self.runtime(root)), patch("tools.update.RuntimeLock", return_value=nullcontext()), patch.object(cli.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, WHEEL_PLAN)) as pip, patch.object(cli, "run_local", return_value=2) as imports, redirect_stdout(StringIO()):
                with self.assertRaisesRegex(UserError, "Setup is NOT complete"):
                    cli.install_dependencies()
            self.assertEqual(pip.call_count, 3)
            self.assertIn("check", pip.call_args.args[0])
            self.assertEqual(imports.call_args.args[1], "preflight")
            self.assertTrue((root / ".venv").is_dir())

    def test_simulated_future_stable_315_vision_depends_on_preflights_not_a_permanent_ban(self):
        from contextlib import nullcontext, redirect_stdout
        from io import StringIO
        import subprocess
        from unittest.mock import patch
        from tech_farmer import __main__ as cli
        with workspace() as relative:
            root = relative.resolve()
            (root / ".venv").mkdir()
            with patch.object(cli, "ROOT", root), patch.object(cli, "verify_source"), patch.object(cli, "_requirements"), patch.object(cli, "choose_python", return_value=self.runtime(root, (3, 15, 0))), patch("tools.update.RuntimeLock", return_value=nullcontext()), patch.object(cli.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, WHEEL_PLAN)) as pip, patch.object(cli, "run_local", return_value=0) as imports, redirect_stdout(StringIO()) as output:
                self.assertEqual(cli.install_dependencies(vision=True), 0)
            self.assertEqual(imports.call_args.args[1], "vision-preflight")
            self.assertEqual(pip.call_count, 3)
            self.assertIn("no cp315 wheel", output.getvalue())
            self.assertNotIn("--pre", pip.call_args_list[1].args[0])

    def test_base_import_preflight_never_imports_optional_vision(self):
        from contextlib import redirect_stdout
        from io import StringIO
        from unittest.mock import patch
        from tech_farmer import __main__ as cli
        seen = []
        def unavailable(name):
            seen.append(name)
            raise ModuleNotFoundError("fixture secret exception is not logged")
        with patch.object(cli.importlib, "import_module", side_effect=unavailable), redirect_stdout(StringIO()) as output:
            self.assertEqual(cli.dependency_preflight(), 2)
        self.assertEqual(seen, [module for _, module in cli.BASE_IMPORTS])
        self.assertNotIn("fixture secret", output.getvalue())
        self.assertIn("Preflight FAILED", output.getvalue())
        for actual, spec, expected in (("13.0.0", "==13.0.0", True), ("12.3.0", "==13.0.0", False), ("2.14.1+cpu", ">=2.2,<3", True), ("2.15.0.dev1", ">=2.2,<3", False), ("3.0.0", ">=2.2,<3", False)):
            self.assertEqual(cli._satisfies_version(actual, spec), expected)

    def test_menu_ignores_arbitrary_commands_and_never_prompts_for_token_on_cancel(self):
        from contextlib import redirect_stdout
        from io import StringIO
        from unittest.mock import patch
        from tech_farmer import __main__ as cli
        with patch("builtins.input", side_effect=["https://not-executed.invalid", "echo bad & exit", "0"]), patch.object(cli.subprocess, "run") as run, patch.object(cli.getpass, "getpass") as secret, redirect_stdout(StringIO()) as output:
            self.assertEqual(cli.local_menu(), 0)
        run.assert_not_called()
        secret.assert_not_called()
        self.assertIn("No command or URL was executed", output.getvalue())

    def test_wizard_keeps_existing_ids_limits_paths_and_exact_backup(self):
        from contextlib import redirect_stdout
        from io import StringIO
        from unittest.mock import patch
        from tech_farmer import __main__ as cli
        from tech_farmer.config import Settings, save_settings
        import os
        original_cwd = Path.cwd()
        with workspace() as relative:
            root = relative.resolve()
            previous = Settings(123456789012345678, 234567890123456789, 345678901234567890,
                                pages_per_run=37, model_cache=".cache/existing", storage_channel_id=456789012345678901)
            save_settings(previous, root / "tech-farmer-settings.json")
            raw = (root / "tech-farmer-settings.json").read_bytes()
            args = SimpleNamespace(owner_id=None, guild_id=None, application_id=None, storage_channel_id=None)
            try:
                os.chdir(root)
                with patch.object(cli.sys.stdin, "isatty", return_value=True), patch("builtins.input", side_effect=["", "", "", ""]), patch.object(cli.getpass, "getpass") as secret, redirect_stdout(StringIO()):
                    self.assertEqual(cli.configure_ids(args), 0)
            finally:
                os.chdir(original_cwd)
            secret.assert_not_called()
            self.assertEqual(load_settings(root / "tech-farmer-settings.json", environ={}), previous)
            self.assertEqual(next(root.glob("tech-farmer-settings.json.backup-*")).read_bytes(), raw)

    def test_absolute_file_entrypoint_is_stdlib_only_from_space_unicode_cwd(self):
        import shutil
        import subprocess
        import sys
        from tech_farmer import __main__ as cli
        with workspace() as relative:
            root = relative.resolve() / "Tech Farmer Å 日本 !"
            package = root / "tech_farmer"
            package.mkdir(parents=True)
            for name in ("__init__.py", "__main__.py", "config.py", "safety.py"):
                shutil.copyfile(cli.ROOT / "tech_farmer" / name, package / name)
            result = subprocess.run([sys.executable, "-B", "-I", "-X", "utf8", str(package / "__main__.py"), "menu"], cwd=root.parent,
                                    input="0\n", capture_output=True, text=True, encoding="utf-8", timeout=30, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(str(root), result.stdout)
            self.assertIn("Menu interpreter:", result.stdout)
            self.assertFalse((root / "data").exists())
            self.assertFalse((root / ".venv").exists())

    @unittest.skipUnless(os.name == "nt", "Native Windows CMD test requires Windows; Linux static checks are not native execution")
    def test_native_windows_main_menu_cancel_from_another_unicode_path(self):
        import shutil
        import subprocess
        from tech_farmer import __main__ as cli
        with workspace() as relative:
            root = relative.resolve() / "Tech Farmer Å 日本 !"
            package = root / "tech_farmer"
            package.mkdir(parents=True)
            for name in ("__init__.py", "__main__.py", "config.py", "safety.py"):
                shutil.copyfile(cli.ROOT / "tech_farmer" / name, package / name)
            shutil.copyfile(cli.ROOT / "START-WINDOWS.cmd", root / "START-WINDOWS.cmd")
            (root / "SOURCE-MANIFEST.sha256").write_text("Menu cancel fixture, not an installable manifest.\n")
            result = subprocess.run(["cmd", "/d", "/c", str(root / "START-WINDOWS.cmd")], cwd=root.parent,
                                    input="0\n", capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=90, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("Menu interpreter:", result.stdout)
            self.assertFalse((root / ".venv").exists())


class Response:
    def __init__(self):
        self.defers = []

    async def defer(self, **kwargs):
        self.defers.append(kwargs)


def interaction(owner=101, guild=202, dm=False):
    return SimpleNamespace(user=SimpleNamespace(id=owner), guild_id=None if dm else guild,
                           guild=None if dm else SimpleNamespace(filesize_limit=8 * 1024 * 1024),
                           response=Response())


class CapturingService(FarmerService):
    """Captures only router output; actual discord.File/serialization are in test_sdk."""
    def __init__(self, *args):
        super().__init__(*args)
        self.messages = []

    async def reply(self, interaction, content, attachments=None, view=None):
        self.messages.append(content)


class ServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_every_action_checks_owner_and_guild_before_work(self):
        with workspace() as root:
            store = Store(101, 202, root / "data", root / "exports")
            runner = JobRunner(store, FixtureMatcher(), lambda: FixtureSource())
            service = CapturingService(store, runner, SimpleNamespace(storage_mib=2048))
            actions = ("help", "consent", "status", "doctor", "profiles", "reference", "harvest", "control", "review", "decide", "reframe", "export")
            for outsider in (interaction(owner=999), interaction(guild=999), interaction(dm=True)):
                for action in actions:
                    await service.run(outsider, action)
                    self.assertIn("Restricted", service.messages[-1])
                self.assertEqual(outsider.response.defers, [])
            self.assertFalse(store.has_consent())
            self.assertEqual(store.jobs(), [])

    async def test_authorized_defer_is_ephemeral_and_consent_gates_work(self):
        with workspace() as root:
            store = Store(101, 202, root / "data", root / "exports")
            runner = JobRunner(store, FixtureMatcher(), lambda: FixtureSource())
            service = CapturingService(store, runner, SimpleNamespace(storage_mib=2048))
            item = interaction()
            await service.run(item, "harvest")
            self.assertEqual(item.response.defers, [{"ephemeral": True, "thinking": True}])
            self.assertIn("consent", service.messages[-1])
            self.assertEqual(store.jobs(), [])
            await service.run(interaction(), "consent", accept=True)
            self.assertTrue(store.has_consent())
            await service.run(interaction(), "consent", accept=False)
            self.assertFalse(store.has_consent())

    async def test_missing_model_never_queues_substitute(self):
        with workspace() as root:
            store = Store(101, 202, root / "data", root / "exports")
            store.consent(True)
            matcher = FixtureMatcher()
            matcher.readiness = lambda: {"ready": False}
            runner = JobRunner(store, matcher, lambda: FixtureSource())
            service = CapturingService(store, runner, SimpleNamespace(storage_mib=2048))
            await service.run(interaction(), "harvest", url="https://fixture.invalid/chapter", profile="Hero")
            self.assertEqual(store.jobs(), [])
            self.assertIn("No dummy detector", service.messages[-1])

    async def test_safety_controls_bypass_slow_doctor_and_image_lock(self):
        import threading
        with workspace() as root:
            store = Store(101, 202, root / "data", root / "exports")
            store.consent(True)
            store.add_reference("Hero", png(drawing()))
            matcher = FixtureMatcher()
            runner = JobRunner(store, matcher, lambda: FixtureSource())
            job = store.create_job("https://fixture.invalid/series", "series", "Hero", crop_spec(), matcher.readiness())
            service = CapturingService(store, runner, SimpleNamespace(storage_mib=2048))
            entered, release = threading.Event(), threading.Event()
            def slow_readiness():
                entered.set()
                release.wait(10)
                return {"ready": True}
            matcher.readiness = slow_readiness
            doctor = asyncio.create_task(service.run(interaction(), "doctor"))
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait, 3))
                async with service.owner_lock:
                    await asyncio.wait_for(service.run(interaction(), "control", job_id=job["id"], operation="cancel"), 3)
                    await asyncio.wait_for(service.run(interaction(), "consent", accept=False), 3)
                self.assertFalse(store.has_consent())
                self.assertEqual(store.job(job["id"])["control"], "cancel")
            finally:
                release.set()
                await doctor

    def test_conservative_runtime_attachment_limit(self):
        self.assertEqual(attachment_limit(SimpleNamespace(filesize_limit=100, guild=None)), 100)
        self.assertEqual(attachment_limit(SimpleNamespace(filesize_limit=200, guild=SimpleNamespace(filesize_limit=80))), 80)
        self.assertEqual(attachment_limit(SimpleNamespace(filesize_limit=None, guild=None)), 8 * 1024 * 1024)
