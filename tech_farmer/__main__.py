"""Local CLI. No token argument, saved token, arbitrary updater URL or remote execution."""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass, replace
import getpass
import importlib
from importlib import metadata
import json
import logging
import os
from pathlib import Path
import re
import subprocess
import sys
import warnings

# Absolute-file launch also works with -I and from another working directory.
if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    __package__ = "tech_farmer"

from . import __version__, REPOSITORY, PYTHON_NOTICE, VISION_PYTHON_NOTICE, runtime_label, supported_python
from .config import LIMIT_FIELDS, SETTINGS_NAME, Settings, load_settings, load_source_config, save_settings, set_limit, snowflake
from .safety import LIMITS, UserError, local_root

ROOT = Path(__file__).resolve().parents[1]
PYTHON_PROBE = (
    "import json,sys,sysconfig; print(json.dumps({'version':list(sys.version_info[:3]),"
    "'releaselevel':sys.version_info.releaselevel,'serial':sys.version_info.serial,"
    "'implementation':sys.implementation.name,'free_threaded':bool(sysconfig.get_config_var('Py_GIL_DISABLED')),"
    "'executable':sys.executable,'prefix':sys.prefix,'base_prefix':sys.base_prefix}))"
)
BASE_IMPORTS = (("discord.py", "discord"), ("aiohttp", "aiohttp"), ("Pillow", "PIL.Image"), ("beautifulsoup4", "bs4"))
VISION_IMPORTS = (("transformers", "transformers"), ("torch", "torch"), ("safetensors", "safetensors"))


@dataclass(frozen=True)
class PythonRuntime:
    command: tuple[str, ...]
    version: tuple[int, ...]
    executable: str
    prefix: str
    base_prefix: str

    @property
    def label(self):
        return f"CPython {'.'.join(map(str, self.version))} | {self.executable}"


def child_environment(*, token=False):
    environment = dict(os.environ)
    if not token:
        environment.pop("DISCORD_BOT_TOKEN", None)
    return environment


def probe_python(command) -> PythonRuntime | None:
    try:
        result = subprocess.run([*command, "-I", "-c", PYTHON_PROBE], cwd=ROOT, shell=False,
                                stdin=subprocess.DEVNULL, capture_output=True, text=True,
                                timeout=15, check=False, env=child_environment())
        if result.returncode != 0 or len(result.stdout) > 8192:
            return None
        data = json.loads(result.stdout)
        version = data["version"]
        if (not isinstance(version, list) or len(version) != 3 or any(type(v) is not int for v in version)
                or type(data["serial"]) is not int or data["serial"] < 0
                or not isinstance(data["implementation"], str) or not isinstance(data["releaselevel"], str)
                or type(data["free_threaded"]) is not bool
                or not supported_python(version, data["implementation"], data["free_threaded"], data["releaselevel"])
                or any(not isinstance(data[key], str) or not data[key] for key in ("executable", "prefix", "base_prefix"))):
            return None
        return PythonRuntime(tuple(command), tuple(version), data["executable"], data["prefix"], data["base_prefix"])
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError):
        return None


def venv_python() -> Path:
    return ROOT / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def choose_python(*, require_venv=False, windows=None) -> PythonRuntime:
    from tools.update import _check_chain
    environment = ROOT / ".venv"
    _check_chain(environment)
    if environment.exists():
        runtime = probe_python((str(venv_python()),))
        if runtime is None or Path(runtime.prefix).absolute() != environment or runtime.prefix == runtime.base_prefix:
            raise UserError("The existing .venv is incomplete or uses an unsupported interpreter. It was NOT deleted or rebuilt. Preserve the whole installation; see README 'Choose Python 3.15' for an explicit upgrade/recovery action.")
        return runtime
    if require_venv:
        raise UserError("Base setup is not installed here. Open START-WINDOWS.cmd and choose 1 first; settings and data stay in this folder.")
    windows = os.name == "nt" if windows is None else windows
    candidates = [("py", f"-3.{minor}") if windows else (f"python3.{minor}",) for minor in range(15, 10, -1)]
    candidates.append((sys.executable,))
    for command in candidates:
        runtime = probe_python(command)
        if runtime is not None:
            return runtime
    raise UserError(PYTHON_NOTICE + " Install an approved Python locally; no administrator or policy workaround is requested.")


def verify_source():
    from tools.update import MANIFEST, MAX_MANIFEST, _read, _verify_inventory, parse_manifest
    _verify_inventory(ROOT, parse_manifest(_read(ROOT, MANIFEST, MAX_MANIFEST)))
    print("Complete local source matches SOURCE-MANIFEST.sha256 (integrity, not a publisher signature).")


def _requirements(vision=False):
    names = {name.lower() for name, _ in BASE_IMPORTS + (VISION_IMPORTS if vision else ())}
    declared = {}
    for filename in ("requirements.txt", "requirements-vision.txt") if vision else ("requirements.txt",):
        for line in (ROOT / filename).read_text(encoding="utf-8").splitlines():
            if not line or line.startswith("#") or (filename == "requirements-vision.txt" and line == "-r requirements.txt"):
                continue
            match = re.fullmatch(r"([A-Za-z0-9_.-]+)((?:(?:==|>=|<=|>|<)[0-9]+(?:\.[0-9]+){1,3})(?:,(?:==|>=|<=|>|<)[0-9]+(?:\.[0-9]+){0,3})*)", line)
            if not match or match[1].lower() not in names or match[1].lower() in declared:
                raise UserError("Dependency declarations are not the reviewed package/version-only set. Restore complete canonical source; no packages were installed.")
            declared[match[1].lower()] = match[2]
    if set(declared) != names:
        raise UserError("Dependency declarations are incomplete. Restore the complete canonical source.")
    return declared


def _satisfies_version(actual, spec):
    match = re.fullmatch(r"([0-9]+(?:\.[0-9]+){1,3})(?:\+[A-Za-z0-9.]+)?", actual)
    if not match:
        return False
    def numeric(value):
        parts = tuple(map(int, value.split(".")))
        return parts + (0,) * (4 - len(parts))
    installed = numeric(match[1])
    for constraint in spec.split(","):
        operator, value = re.fullmatch(r"(==|>=|<=|>|<)([0-9.]+)", constraint).groups()
        expected = numeric(value)
        if not {"==": installed == expected, ">=": installed >= expected, "<=": installed <= expected,
                ">": installed > expected, "<": installed < expected}[operator]:
            return False
    return True


def dependency_preflight(*, vision=False) -> int:
    print("Actual interpreter:", runtime_label())
    declared = _requirements(vision)
    failed = False
    for distribution, module in BASE_IMPORTS + (VISION_IMPORTS if vision else ()):
        try:
            importlib.import_module(module)
            actual = metadata.version(distribution)
            valid = _satisfies_version(actual, declared[distribution.lower()])
            print(f"{distribution}: {actual} | {'version/import OK' if valid else 'outside reviewed requirements'}")
            failed |= not valid
        except Exception as exc:
            print(f"{distribution}: missing/unusable ({type(exc).__name__}); use Setup/repair base or the optional vision installer.")
            failed = True
    if vision and sys.version_info[:2] == (3, 15):
        print(VISION_PYTHON_NOTICE)
    if not failed:
        try:
            from .bot import FarmerClient, FarmerGroup
            if vision:
                from .vision import _import_runtime
                _import_runtime()
            if not callable(FarmerClient) or not callable(FarmerGroup):
                raise ImportError("SDK API unavailable")
        except Exception as exc:
            print(f"Application API import failed ({type(exc).__name__}); no connection or model download was made.")
            failed = True
    print("Preflight FAILED; see README diagnostics." if failed else "Actual imports/API preflight passed. No Discord login, source request, model download or recognition test was performed.")
    return 2 if failed else 0


def run_local(runtime, command):
    result = subprocess.run([*runtime.command, "-I", "-X", "utf8", str(ROOT / "tech_farmer" / "__main__.py"), command],
                            cwd=ROOT, shell=False, check=False, env=child_environment(token=command == "run"))
    return result.returncode


def install_dependencies(*, vision=False):
    from tools.update import RuntimeLock, _wheel_preflight
    with RuntimeLock(ROOT):
        verify_source()
        _requirements(vision)
        runtime = choose_python(require_venv=vision)
        print("Using", runtime.label)
        if vision and runtime.version[:2] == (3, 15):
            print(VISION_PYTHON_NOTICE)
        if not (ROOT / ".venv").exists():
            print("Creating this installation's .venv only. No global packages or user data are changed.")
            result = subprocess.run([*runtime.command, "-m", "venv", str(ROOT / ".venv")], cwd=ROOT,
                                    shell=False, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL, timeout=180, check=False, env=child_environment())
            if result.returncode:
                raise UserError("Virtual-environment creation failed. Any partial .venv was preserved, not deleted. Read README recovery before retrying.")
            runtime = choose_python(require_venv=True)
        else:
            print("Keeping the existing .venv and its actual Python version. Interpreter upgrades are never automatic.")
        requirements = ROOT / ("requirements-vision.txt" if vision else "requirements.txt")
        command = [*runtime.command, "-m", "pip", "--disable-pip-version-check", "--no-input",
                   "--require-virtualenv", "install", "--only-binary=:all:"]
        def pip(arguments):
            return subprocess.run(arguments, cwd=ROOT, shell=False, stdin=subprocess.DEVNULL,
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=900,
                                  check=False, env=child_environment()).returncode
        print("Checking compatible stable wheels using your existing approved pip/index configuration. No source builds.")
        if not _wheel_preflight([*command, "-r", str(requirements)], ROOT, environment=child_environment()):
            raise UserError("Wheel resolution failed before installation. This can mean missing stable wheels, unavailable index/network, a prerelease/yanked/non-wheel plan or a dependency conflict; it is NOT proof your Python is unsupported. No packages were installed by this check. No index/proxy/DNS/policy change or older-Python fallback was attempted.")
        print("Installing explicit optional vision packages (can be large). No model weights." if vision else "Installing base packages into .venv only.")
        repair = [] if vision else ["--force-reinstall"]
        if pip([*command, *repair, "-r", str(requirements)]):
            raise UserError("Package installation failed; the existing .venv was kept, not rolled back. Retry this explicit setup after resolving approved index/wheel access; no data, models or source were deleted.")
        if pip([*runtime.command, "-m", "pip", "--disable-pip-version-check", "check"]):
            raise UserError("Installed packages have a dependency conflict. Setup is NOT complete. Keep .venv/data and inspect locally with .venv Python -m pip check; do not post secrets or raw index logs.")
        # The child preflight is read-only and intentionally needs no RuntimeLock.
        result = run_local(runtime, "vision-preflight" if vision else "preflight")
        if result:
            raise UserError("Imports/API preflight failed after installation. Setup is NOT complete; .venv and all user data were preserved. No model was downloaded and no substitute matching will run.")
        print("Optional packages ready; prepare the model explicitly with menu 6." if vision else "Base setup complete. Next choose 2 to configure IDs, then 3 to start. Vision/model installation is optional.")
        return 0


def configure_ids(args):
    previous = load_settings(environ={}) if Path(SETTINGS_NAME).exists() else None
    interactive = sys.stdin.isatty()
    print("Local nonsecret setup. Never enter a bot token here; Start bot has a hidden prompt.")
    values = {}
    for key in ("owner_id", "guild_id", "application_id"):
        value = getattr(args, key)
        stored = str(getattr(previous, key)) if previous else None
        if value is None:
            if interactive:
                prompt = key.replace("_", " ") + (f" [Enter keeps {stored}]" if stored else " (real numeric Discord ID)") + ": "
                value = input(prompt)
                if not value and stored:
                    value = stored
            elif stored:
                value = stored
            else:
                raise UserError("Setup needs --owner-id, --guild-id and --application-id, or an interactive local terminal. Never supply a token to setup.")
        values[key] = snowflake(value, key)
    channel = args.storage_channel_id
    if channel is None and interactive:
        channel = input("Private storage channel ID (optional; Enter keeps current/chooses later): ")
    values["storage_channel_id"] = snowflake(channel, "storage_channel_id") if channel else (previous.storage_channel_id if previous else None)
    if previous:
        if any(values[key] != getattr(previous, key) for key in ("owner_id", "guild_id", "application_id")):
            raise UserError("Setup does not replace this installation's owner/server/application. Existing settings/jobs were kept. Use a separate installation for another identity.")
        settings = replace(previous, storage_channel_id=values["storage_channel_id"])
    else:
        settings = Settings(**values)
    save_settings(settings)
    print("Validated nonsecret settings saved locally with backups for prior settings; no JSON download/move needed.")
    from urllib.parse import urlencode
    authorization = "https://discord.com/oauth2/authorize?" + urlencode({
        "client_id": str(settings.application_id), "scope": "bot applications.commands", "permissions": "35840",
        "guild_id": str(settings.guild_id), "disable_guild_select": "true", "integration_type": "0"})
    print("Only if not already authorized: open this official bot-install link yourself and approve it:", authorization)
    print("Existing bot with these scopes usually needs only stop/update/restart, not another invitation. IDs, server permissions and channel privacy are NOT verified here.")
    print("Start bot → /farmer consent → choose /farmer storage channel:#private accept:true → /farmer pull url:<permitted link>.")
    return 0


def open_local(which):
    paths = {"instructions": ROOT / "START-HERE.html", "data": ROOT / "data"}
    path = paths[which]
    from tools.update import _check_chain
    _check_chain(path)
    print("Local path:", path)
    if path.exists() and not (path.is_dir() if which == "data" else path.is_file()):
        raise UserError("The local instructions/data path has an unexpected type; nothing was opened.")
    if not path.exists():
        print("Not created yet; setup/start will create needed local state. Nothing was moved or deleted.")
    elif os.name == "nt":
        os.startfile(str(path))
    return 0


def local_diagnostics():
    print("Installation:", ROOT)
    print("Settings:", "present (not printed)" if (ROOT / SETTINGS_NAME).exists() else "not configured; choose 2")
    print("Data:", ROOT / "data", "| Exports:", ROOT / "exports")
    print("Models: .cache/models by default, or your saved local model_cache. No models are needed for page collection.")
    verify_source()
    runtime = choose_python(require_venv=True)
    print("Project environment:", runtime.label)
    result = run_local(runtime, "preflight")
    print("For job progress use /farmer status while the bot runs, or local 'status' after stopping it. Permissions/recognition are not checked offline.")
    open_local("data")
    return result


def local_menu():
    print(f"TechFarmer {__version__} | {REPOSITORY}\nInstallation: {ROOT}\nMenu interpreter: {runtime_label()}")
    print("Use a local, non-shared, non-synced folder you control. No admin/UAC or policy changes. No files are moved automatically.")
    while True:
        print("\n1 Setup/repair base   2 Configure IDs   3 Start bot   4 Update/check/rollback source")
        print("5 Install optional matching packages   6 Prepare optional model (explicit large download)")
        print("7 Open instructions   8 Data/status + base diagnostics   0 Exit")
        try:
            choice = input("Choose a number: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nClosed. Settings, jobs and environments were kept.")
            return 0
        if choice in {"", "0"}:
            return 0
        try:
            if choice in {"1", "5"}:
                install_dependencies(vision=choice == "5")
            elif choice in {"2", "3", "6"}:
                runtime = choose_python(require_venv=choice != "2")
                print("Using", runtime.label)
                command = {"2": "setup", "3": "run", "6": "download-model"}[choice]
                if choice == "6":
                    if run_local(runtime, "vision-preflight"):
                        print("Matching dependencies are unavailable; no model download was attempted. Base collection needs neither.")
                        continue
                    if input("Pinned model download may use several GB. Type DOWNLOAD to proceed: ").strip() != "DOWNLOAD":
                        print("Cancelled. No model download requested.")
                        continue
                if run_local(runtime, command):
                    print("Action did not complete. Read the diagnostic above; no success or connection is assumed.")
            elif choice == "4":
                print("Stop every bot/pull first. The independent updater verifies and journals SOURCE only.")
                runtime = choose_python()
                result = subprocess.run([*runtime.command, "-I", "-X", "utf8", str(ROOT / "tools" / "update.py")], cwd=ROOT,
                                        shell=False, check=False, env=child_environment())
                print("Close/reopen START-WINDOWS.cmd after source changes so no old menu code remains in memory. Then Setup/repair base and Start bot.")
                return result.returncode
            elif choice == "7":
                open_local("instructions")
            elif choice == "8":
                local_diagnostics()
            else:
                print("Choose one of the displayed numbers. No command or URL was executed.")
        except UserError as exc:
            print(str(exc))
        except (OSError, subprocess.SubprocessError, EOFError, KeyboardInterrupt):
            print("Local action stopped/failed. Preserve settings/data/.venv; inspect README. No policy workaround or secret log is used.")
        except Exception as exc:
            if type(exc).__name__ in {"RuntimeBusyError", "UpdateError"}:
                print(str(exc))
            else:
                print("Local action failed safely. Close other bot/updater windows and check README; raw exceptions are not printed.")


def cli_model_config(args):
    saved = load_settings() if Path(SETTINGS_NAME).exists() else None
    cache = args.model_cache or (saved.model_cache if saved else ".cache/models")
    device = args.device or (saved.device if saved else "cpu")
    local_root(cache)
    return {"cache_dir": cache, "device": device}


def build_runner(settings, store):
    from .engine import JobRunner, RunLimits
    from .source_setup import prepare_source
    from .sources import SourceClient
    from .vision import ReferenceMatcher
    limits = RunLimits(settings.pages_per_run, settings.candidates_per_run, settings.detections_per_page,
                       settings.storage_mib * 1024 * 1024, settings.tile_size, settings.max_tiles, settings.page_crop_mib * 1024 * 1024)
    runner = JobRunner(store, ReferenceMatcher(settings.model_config), lambda: SourceClient(load_source_config(settings)), limits,
                       source_preparer=prepare_source, prepared_source_factory=SourceClient)
    runner.source_settings = settings
    return runner


def local_reference(store, profile: str, path: Path, storage_limit: int):
    if (path.is_symlink() or any(parent.is_symlink() for parent in path.parents) or not path.is_file()
            or not 0 < path.stat().st_size <= LIMITS.reference_bytes):
        raise UserError("A local reference must be a regular PNG/JPEG/WebP file of at most 8 MiB, not a link. It is never converted into a Discord URL.")
    return store.add_reference(profile, path.read_bytes(), suffix=path.suffix.lower(), storage_limit=storage_limit)


def show_job(store, job_id):
    summary = store.summary(job_id)
    print(f"{summary['id']} | {summary['mode']} | {summary['phase']} | {summary['code'] or 'no error'}")
    print("Pages:", summary["pages"], "Results:", summary["candidates"], "Discovery complete:", summary["discovery_complete"])
    if summary["mode"] != "match":
        print("Collection only: no identity matching and no model score.")
    if summary.get("source_message"):
        print("Source:", summary["source_message"])
    if summary["phase"] == "awaiting_approval":
        print("Exact external image origins awaiting this job's approval:")
        for origin in summary["required_origins"]:
            print(" ", origin)
        proposal_id = store.job(job_id)["source_proposal"].get("proposal_id", "")
        print("Inspect this set, then: python -m tech_farmer approve --job", job_id, "--accept --proposal", proposal_id)


async def local_job_command(args, settings, store):
    from .engine import crop_spec
    from .exports import export_job
    from .store import CONSENT_TEXT
    store.recover()
    if args.command == "consent":
        print(CONSENT_TEXT)
        if args.accept or args.revoke:
            store.consent(args.accept and not args.revoke)
            print("Consent", "accepted." if store.has_consent() else "revoked.")
        return 0
    if args.command == "status":
        if args.job:
            show_job(store, args.job)
        else:
            for job in store.jobs(limit=20):
                show_job(store, job["id"])
        return 0
    runner = build_runner(settings, store)
    if args.command == "pull":
        if not args.url:
            raise UserError("Use pull --url <permitted source link>; --reference is an optional local image path.")
        if args.confirm_rights:
            print(CONSENT_TEXT)
            store.consent(True)
        store.require_consent()
        profile = args.profile
        if args.reference:
            if args.mode not in {"auto", "match"}:
                raise UserError("Local reference paths are only used by auto/match, not no-identity collection.")
            readiness = await asyncio.to_thread(runner.matcher.readiness)
            if not readiness.get("ready"):
                raise UserError("Reference matching needs the optional vision install and prepared local model. No substitute matcher will run; collection without references needs neither.")
            if not profile:
                from uuid import uuid4
                profile = "Local-" + uuid4().hex[:12]
            for path in args.reference:
                await asyncio.to_thread(local_reference, store, profile, Path(path), settings.storage_mib * 1024 * 1024)
        spec = crop_spec(args.preset, args.aspect, args.context, args.trim, args.align, args.shape, args.fit)
        job = await runner.create_pull(args.url, settings, profile=profile, mode=args.mode, scope=args.scope, spec=spec, threshold=args.threshold)
        print("Created local job", job["id"], "mode", job["mode"], "(no Discord upload).")
        await runner.run_job(job["id"])
        show_job(store, job["id"])
        return 0 if store.job(job["id"])["phase"] == "review_ready" else 2
    if not args.job:
        raise UserError("This command requires --job with a local job ID.")
    if args.command == "approve":
        job = store.job(args.job)
        if job["phase"] != "awaiting_approval":
            raise UserError("This job has no pending external-image-origin approval.")
        print("Approve only if you have rights for all these exact image origins:")
        for origin in job["source_proposal"].get("required_origins", []):
            print(" ", origin)
        if args.accept:
            store.approve_source(args.job, True, proposal_id=args.proposal)
            store.control(args.job, "resume")
            await runner.run_job(args.job)
        elif args.revoke:
            store.approve_source(args.job, False)
        else:
            print("Nothing approved. Repeat with --accept after inspecting the set, or --revoke to decline.")
        show_job(store, args.job)
    elif args.command == "control":
        if not args.operation:
            raise UserError("Use --operation pause, resume or cancel.")
        store.control(args.job, args.operation)
        if args.operation == "resume":
            await runner.run_job(args.job)
        show_job(store, args.job)
    elif args.command == "export":
        plan = export_job(store, args.job, LIMITS.discord_bytes, storage_limit=settings.storage_mib * 1024 * 1024, mode=args.selection)
        print(f"{plan.count} native PNGs saved locally in exports/{args.job}; {len(plan.oversized)} exceed the conservative Discord limit. No upload, resize or public fallback occurred.")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=f"TechFarmer {__version__}: source link first; optional local reference matching")
    parser.add_argument("command", nargs="?", default="run", choices=("run", "setup", "menu", "install-base", "install-vision", "preflight", "vision-preflight", "diagnostics", "doctor", "download-model", "validate-config", "version", "update", "settings", "consent", "pull", "status", "approve", "control", "export"))
    parser.add_argument("--model-cache", default=None)
    parser.add_argument("--device", choices=("cpu", "cuda", "mps"), default=None)
    for key in ("owner", "guild", "application", "storage-channel"):
        parser.add_argument("--" + key + "-id")
    parser.add_argument("--url")
    parser.add_argument("--reference", action="append", default=[], help="Local image path; repeat for views of ONE character. Never a CDN URL.")
    parser.add_argument("--profile")
    parser.add_argument("--mode", choices=("auto", "match", "collect-pages", "collect-panels"), default="auto")
    parser.add_argument("--scope", choices=("auto", "page", "chapter", "series"), default="auto")
    parser.add_argument("--preset", default="detected")
    parser.add_argument("--aspect")
    parser.add_argument("--context", default="0,0,0,0")
    parser.add_argument("--trim", default="0,0,0,0")
    parser.add_argument("--align", default="center")
    parser.add_argument("--shape", default="rectangle")
    parser.add_argument("--fit", choices=("contain", "cover"), default="contain")
    parser.add_argument("--threshold", type=float, default=0.15)
    parser.add_argument("--confirm-rights", action="store_true", help="Explicitly accept the printed rights/privacy consent for this local pull")
    consent = parser.add_mutually_exclusive_group()
    consent.add_argument("--accept", action="store_true")
    consent.add_argument("--revoke", action="store_true")
    parser.add_argument("--job")
    parser.add_argument("--proposal", help="Exact proposal ID printed with the external-origin list; required for approval")
    parser.add_argument("--operation", choices=("pause", "resume", "cancel"))
    parser.add_argument("--selection", choices=("accepted", "collection"), default="accepted")
    parser.add_argument("--name", choices=tuple(LIMIT_FIELDS))
    parser.add_argument("--value", type=int)
    args = parser.parse_args(argv)
    original_cwd = Path.cwd()
    try:
        if not supported_python():
            raise UserError(PYTHON_NOTICE)
        if args.command == "version":
            print(f"TechFarmer {__version__}\n{REPOSITORY}\nActual interpreter: {runtime_label()}\nStop bot → START-WINDOWS.cmd menu 4 (or UPDATE-WINDOWS.cmd) → reopen menu → setup/repair base → restart.")
            return 0
        args.reference = [str(Path(path).absolute()) for path in args.reference]
        os.chdir(ROOT)
        if args.command == "menu":
            return local_menu()
        if args.command in {"preflight", "vision-preflight"}:
            return dependency_preflight(vision=args.command == "vision-preflight")
        if args.command in {"install-base", "install-vision"}:
            return install_dependencies(vision=args.command == "install-vision")
        if args.command == "diagnostics":
            return local_diagnostics()
        from tools.update import RuntimeLock, check_update
        if args.command == "update":
            result = check_update(ROOT)
            print(f"Installed {__version__} | {result['status']}\n{result['message']}\nCheck only. Stop the bot, run START-WINDOWS.cmd menu 4 or UPDATE-WINDOWS.cmd, then restart.")
            return 0 if result["status"] != "unavailable" else 2
        with RuntimeLock(ROOT):
            if args.command == "setup":
                return configure_ids(args)
            if args.command in {"doctor", "download-model"}:
                from .vision import ModelUnavailable, ReferenceMatcher
                matcher = ReferenceMatcher(cli_model_config(args))
                if args.command == "download-model":
                    if dependency_preflight(vision=True):
                        raise UserError("Optional vision preflight failed. No model download was attempted; base no-reference collection needs no model.")
                    print("Explicit pinned OWLv2 model preparation from Hugging Face; no user images are sent. Several GB may be needed. This is optional for no-reference collection.")
                    try:
                        result = matcher.prepare_cache()
                    except ModelUnavailable as exc:
                        result = {"ready": False, "code": exc.code, "message": str(exc), "action": exc.action}
                else:
                    result = matcher.readiness()
                print("Model status:", result.get("code", "unknown"), "\n", result.get("message", ""), "\n", result.get("action", ""))
                return 0 if result.get("ready") else 2
            if args.command == "run":
                verify_source()
                if dependency_preflight():
                    raise UserError("Base imports are not ready; use START-WINDOWS.cmd → Setup/repair base. No token was requested and no Discord connection was made.")
            settings = load_settings()
            if args.command == "validate-config":
                config = load_source_config(settings)
                if config.get("profiles"):
                    from .sources import SourceClient
                    SourceClient(config)
                print("Nonsecret settings valid. Supported static sources can auto-discover; manual source profiles and vision install are NOT startup prerequisites. Rights, site compatibility and channel privacy are not verified offline.")
                return 0
            if args.command == "settings":
                if args.name is None and args.value is None:
                    for key, value in settings.limits.items():
                        print(f"{key}: {value}")
                elif args.name is None or args.value is None:
                    raise UserError("Use --name <numeric limit> --value <integer>.")
                else:
                    set_limit(settings, args.name, args.value)
                    print("Whitelisted limit saved with backup. Frozen job inference settings remain reproducible.")
                return 0
            from .store import Store
            store = Store(settings.owner_id, settings.guild_id)
            for notice in store.notices:
                print("NOTICE:", notice)
            if args.command != "run":
                return asyncio.run(local_job_command(args, settings, store))
            runner = build_runner(settings, store)
            from .bot import FarmerClient
            token = os.environ.pop("DISCORD_BOT_TOKEN", "")
            if not token:
                if not sys.stdin.isatty():
                    raise UserError("Run in a local terminal for the hidden token prompt or supply DISCORD_BOT_TOKEN in the process environment, never JSON, a URL or a command argument.")
                try:
                    with warnings.catch_warnings():
                        warnings.simplefilter("error", getpass.GetPassWarning)
                        token = getpass.getpass("Bot token (hidden, local console only): ")
                except (getpass.GetPassWarning, EOFError):
                    raise UserError("A hidden console prompt is unavailable. Use a real local terminal or the process environment; never enter the token into an echoing prompt or command argument.") from None
            if not token.strip() or any(char.isspace() for char in token):
                raise UserError("Token is empty or contains whitespace. Nothing was connected.")
            for name in ("discord", "aiohttp", "huggingface_hub", "transformers"):
                logging.getLogger(name).addHandler(logging.NullHandler())
                logging.getLogger(name).propagate = False
            client = FarmerClient(store, runner, settings)
            client.run(token, log_handler=None)
            return 0
    except UserError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except (KeyboardInterrupt, asyncio.CancelledError):
        print("Stopped. Originals/jobs remain local; explicitly resume interrupted work after restart.")
        return 0
    except ModuleNotFoundError:
        print("A required dependency is missing. Install requirements.txt. Only reference matching/model commands additionally need requirements-vision.txt and prepared local weights. No substitute detector was used.", file=sys.stderr)
        return 2
    except Exception as exc:
        if type(exc).__name__ in {"RuntimeBusyError", "UpdateError"}:
            print("Installation is locked or has an unfinished update. Stop the other bot/CLI/updater, then use the local updater recovery instructions. No concurrent update/run is allowed.", file=sys.stderr)
        else:
            print("Operation failed safely. Check README, local configuration and job status. Raw exception bodies, credentials and URLs are not logged.", file=sys.stderr)
        return 2
    finally:
        if Path.cwd() != original_cwd:
            os.chdir(original_cwd)


if __name__ == "__main__":
    raise SystemExit(main())
