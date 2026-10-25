"""Strict nonsecret setup; a token is read ONLY by the hidden console prompt/env."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import re

from .safety import UserError, atomic_write

SCHEMA_VERSION = 3
LIMIT_FIELDS = {
    "pages_per_run": (200, 1, 10000), "candidates_per_run": (1000, 1, 100000),
    "detections_per_page": (200, 1, 1000), "storage_mib": (2048, 16, 1048576),
    "page_crop_mib": (128, 16, 512), "discovery_pages": (2000, 1, 100000),
    "discovery_issues": (200, 1, 10000), "tile_size": (960, 256, 4096),
    "max_tiles": (24, 1, 32),
}

SETTINGS_NAME = "tech-farmer-settings.json"
SOURCE_NAME = "source-profiles.json"
ID_RE = re.compile(r"^[1-9][0-9]{16,19}$")


def snowflake(value, name: str) -> int:
    if not isinstance(value, str) or not ID_RE.fullmatch(value) or int(value) >= 2**64:
        raise UserError(f"Set {name} to your real Discord numeric ID (17–20 digits, no spaces or leading zero).")
    return int(value)


def bounded_int(value, name: str, low: int, high: int) -> int:
    if type(value) is not int or not low <= value <= high:
        raise UserError(f"{name} must be an integer between {low} and {high}.")
    return value


@dataclass(frozen=True)
class Settings:
    owner_id: int
    guild_id: int
    application_id: int
    device: str = "cpu"
    model_cache: str = ".cache/models"
    pages_per_run: int = 200
    candidates_per_run: int = 1000
    detections_per_page: int = 200
    storage_mib: int = 2048
    page_crop_mib: int = 128
    discovery_pages: int = 2000
    discovery_issues: int = 200
    tile_size: int = 960
    max_tiles: int = 24
    storage_channel_id: int | None = None

    @property
    def model_config(self) -> dict:
        return {"cache_dir": self.model_cache, "device": self.device}

    @property
    def limits(self) -> dict:
        return {key: getattr(self, key) for key in LIMIT_FIELDS}

    def to_dict(self) -> dict:
        result = {"schema_version": SCHEMA_VERSION, **asdict(self)}
        for key in ("owner_id", "guild_id", "application_id", "storage_channel_id"):
            result[key] = str(result[key]) if result[key] is not None else None
        return result


def read_json(path: Path, limit: int = 65536):
    if path.is_symlink() or not path.is_file() or path.stat().st_size > limit:
        raise UserError("Configuration must be a small regular local JSON file, not a symbolic link.")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        raise UserError("Configuration must contain valid UTF-8 JSON.") from None


def load_settings(path: Path | None = None, environ=None) -> Settings:
    env = os.environ if environ is None else environ
    path = path or Path(SETTINGS_NAME)
    data = read_json(path, 8192) if path.exists() else {}
    allowed = {"schema_version", "owner_id", "guild_id", "application_id", "device", "model_cache", "storage_channel_id", *LIMIT_FIELDS}
    if not isinstance(data, dict) or set(data) - allowed or (data and (type(data.get("schema_version")) is not int or data["schema_version"] not in {1, 2, SCHEMA_VERSION})):
        raise UserError("Use schema_version 1, 2 or 3 nonsecret settings. Tokens and unknown fields are forbidden; existing files were not changed.")
    values = {}
    for key in ("owner_id", "guild_id", "application_id"):
        if key in data:
            if data.get("schema_version") in {1, 2} and type(data[key]) is int:
                data[key] = str(data[key])
            snowflake(data[key], key)
        values[key] = snowflake(env.get("TECH_FARMER_" + key.upper(), data.get(key, "")), key)
    device = data.get("device", "cpu")
    if device not in {"cpu", "cuda", "mps"}:
        raise UserError("device must be cpu, cuda or mps. CPU is the supported setup default; accelerators need suitable local PyTorch.")
    cache = data.get("model_cache", ".cache/models")
    if not isinstance(cache, str) or not re.fullmatch(r"[A-Za-z0-9_.\-/]{1,120}", cache) or Path(cache).is_absolute() or ".." in Path(cache).parts:
        raise UserError("model_cache must be a simple project-relative folder, without '..'.")
    for key, (default, low, high) in LIMIT_FIELDS.items():
        values[key] = bounded_int(data.get(key, default), key, low, high)
    channel = data.get("storage_channel_id")
    if channel is not None and channel != "":
        channel = snowflake(channel, "storage_channel_id")
    else:
        channel = None
    result = Settings(**values, device=device, model_cache=cache, storage_channel_id=channel)
    if data and data["schema_version"] < SCHEMA_VERSION:
        backup_settings(path)
        # Preserve the stored IDs, not transient environment overrides, in the migration.
        migrated = {**data, "schema_version": SCHEMA_VERSION}
        atomic_write(path, (json.dumps(migrated, indent=2) + "\n").encode("utf-8"))
    return result


def backup_settings(path: Path) -> Path:
    from .safety import digest
    raw = path.read_bytes()
    backup = path.with_name(path.name + ".backup-" + digest(raw)[:16])
    if backup.is_symlink():
        raise UserError("Settings backup path is unsafe; original configuration was not changed.")
    if backup.exists():
        if backup.read_bytes() != raw:
            raise UserError("Settings backup already differs; original configuration was not changed.")
    else:
        atomic_write(backup, raw)
    return backup


def save_settings(settings: Settings, path: Path | None = None) -> None:
    path = path or Path(SETTINGS_NAME)
    if path.exists():
        read_json(path, 8192)
        backup_settings(path)
    atomic_write(path, (json.dumps(settings.to_dict(), indent=2) + "\n").encode("utf-8"))


def set_limit(settings: Settings, key: str, value: int, path: Path | None = None) -> Settings:
    from dataclasses import replace
    if key not in LIMIT_FIELDS:
        raise UserError("Only documented numeric limits can change here. Owner, guild, token, application, device and paths cannot be changed from Discord.")
    _, low, high = LIMIT_FIELDS[key]
    updated = replace(settings, **{key: bounded_int(value, key, low, high)})
    save_settings(updated, path)
    return updated


def load_source_config(settings: Settings, path: Path | None = None) -> dict:
    path = path or Path(SOURCE_NAME)
    if not path.exists():
        return {"authorized": False, "profiles": [], "limits": {"max_pages": settings.discovery_pages, "max_issues": settings.discovery_issues}}
    data = read_json(path)
    if not isinstance(data, dict) or set(data) - {"authorized", "allow_http", "profiles", "limits"}:
        raise UserError("source-profiles.json accepts only nonsecret source profiles, authorization flags and numeric limits. Tokens, headers and unknown fields are forbidden.")
    if any(key in data and type(data[key]) is not bool for key in ("authorized", "allow_http")):
        raise UserError("Source authorization and allow_http must be explicit booleans.")
    if "profiles" in data and not isinstance(data["profiles"], list):
        raise UserError("Source profiles must be a list of explicit nonsecret profiles.")
    limits = data.setdefault("limits", {})
    from .sources import SourceLimits
    import math
    if (not isinstance(limits, dict) or set(limits) - set(SourceLimits.__dataclass_fields__)
            or any(type(value) not in {int, float} or not math.isfinite(value) for value in limits.values())):
        raise UserError("Source limits must contain only documented finite numeric limits, never secrets or headers.")
    limits["max_pages"] = settings.discovery_pages
    limits["max_issues"] = settings.discovery_issues
    return data
