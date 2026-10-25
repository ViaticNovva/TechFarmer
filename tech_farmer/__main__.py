"""Local CLI. No token argument, saved token, arbitrary updater URL or remote execution."""
from __future__ import annotations

import argparse
import asyncio
import getpass
import logging
import os
from pathlib import Path
import sys

from . import __version__, REPOSITORY
from .config import LIMIT_FIELDS, SETTINGS_NAME, Settings, load_settings, load_source_config, save_settings, set_limit, snowflake
from .safety import LIMITS, UserError, local_root

ROOT = Path(__file__).resolve().parents[1]


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
    parser.add_argument("command", nargs="?", default="run", choices=("run", "setup", "doctor", "download-model", "validate-config", "version", "update", "settings", "consent", "pull", "status", "approve", "control", "export"))
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
    try:
        if not ((3, 11) <= sys.version_info[:2] <= (3, 12)):
            raise UserError("Use supported Python 3.11 or 3.12. Newer Python/vision combinations are not verified here.")
        if args.command == "version":
            print(f"TechFarmer {__version__}\n{REPOSITORY}\nStop bot → UPDATE-WINDOWS.cmd (or python tools/update.py --update) → restart.")
            return 0
        from tools.update import RuntimeLock, check_update
        if args.command == "update":
            result = check_update(ROOT)
            print(f"Installed {__version__} | {result['status']}\n{result['message']}\nCheck only. Stop the bot, run UPDATE-WINDOWS.cmd or python tools/update.py --update, then restart.")
            return 0 if result["status"] != "unavailable" else 2
        if Path.cwd().resolve() != ROOT:
            raise UserError("Run this command from the TechFarmer project folder so settings and data stay beside this installation.")
        with RuntimeLock(ROOT):
            if args.command == "setup":
                values = {}
                for key in ("owner_id", "guild_id", "application_id"):
                    value = getattr(args, key)
                    if not value:
                        if not sys.stdin.isatty():
                            raise UserError("Setup needs --owner-id, --guild-id and --application-id, or an interactive local terminal. Never supply a token to setup.")
                        value = input(key.replace("_", " ") + " (nonsecret numeric ID): ")
                    values[key] = snowflake(value, key)
                values["storage_channel_id"] = snowflake(args.storage_channel_id, "storage_channel_id") if args.storage_channel_id else None
                if Path(SETTINGS_NAME).exists():
                    previous = load_settings()
                    if any(values[key] != getattr(previous, key) for key in ("owner_id", "guild_id", "application_id")):
                        raise UserError("Setup does not silently replace an existing installation's identity. Keep this installation and use a separate project for another owner/server.")
                    from dataclasses import replace
                    settings = replace(previous, storage_channel_id=values["storage_channel_id"] or previous.storage_channel_id)
                else:
                    settings = Settings(**values)
                save_settings(settings)
                print("Nonsecret settings saved. Source profile and model install are optional. Start the bot, accept rights consent, then /farmer pull. Storage ID is only a suggestion until private-channel validation and explicit /farmer storage consent.")
                return 0
            if args.command in {"doctor", "download-model"}:
                from .vision import ReferenceMatcher
                matcher = ReferenceMatcher(cli_model_config(args))
                if args.command == "download-model":
                    print("Explicit pinned OWLv2 model preparation from Hugging Face; no user images are sent. Several GB may be needed. This is optional for no-reference collection.")
                    result = matcher.prepare_cache()
                else:
                    result = matcher.readiness()
                print("Model status:", result.get("code", "unknown"), "\n", result.get("message", ""), "\n", result.get("action", ""))
                return 0 if result.get("ready") else 2
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
                token = getpass.getpass("Bot token (hidden, local console only): ")
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


if __name__ == "__main__":
    raise SystemExit(main())
