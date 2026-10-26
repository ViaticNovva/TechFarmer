"""Shared local validation. Exceptions exposed to Discord contain no source URLs."""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
import json
import re


class UserError(ValueError):
    """A deliberately safe, user-facing error, never an upstream exception string."""


class StorageBudgetExceeded(UserError):
    """No new image file was written; archive data or increase the local budget."""


class CropBudgetExceeded(UserError):
    """A page's native crops exceed the bounded in-memory encoding budget."""


class CandidateBudgetExceeded(UserError):
    """Keep the whole page pending instead of discarding detections."""


@dataclass(frozen=True)
class Limits:
    image_bytes: int = 24 * 1024 * 1024
    reference_bytes: int = 8 * 1024 * 1024
    pixels: int = 20_000_000
    side: int = 16_000
    references: int = 8
    discord_bytes: int = 8 * 1024 * 1024


LIMITS = Limits()
DISCORD_HOSTS = frozenset({"cdn.discordapp.com", "media.discordapp.net"})
ID_RE = re.compile(r"^[jprc]_[a-f0-9]{16}$")


def digest(data: bytes) -> str:
    return sha256(data).hexdigest()


def json_text(value) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":"), allow_nan=False)


def label(value: str, maximum: int = 60) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 _.-]{0," + str(maximum - 1) + r"}", value):
        raise UserError("Use a short name containing only letters, numbers, spaces, dots, hyphens or underscores.")
    return value.strip()


def local_root(path: str | Path) -> Path:
    path = Path(path)
    if path.is_absolute() or ".." in path.parts:
        raise UserError("Storage paths must be relative to the project folder, without '..'.")
    if any(part.startswith("~") for part in path.parts):
        raise UserError("Storage paths must be ordinary project-relative folders.")
    root = Path.cwd().resolve()
    target = root / path
    current = root
    for part in path.parts:
        current /= part
        if current.is_symlink():
            raise UserError("Storage and cache folders must not be symbolic links.")
    if not target.resolve().is_relative_to(root):
        raise UserError("Storage must stay inside the project folder.")
    target.mkdir(parents=True, exist_ok=True, mode=0o700)
    return target


def persistent_seed(value: str, query_keys=()) -> str:
    """Persist only a stable unsigned seed; discovered signed URLs stay in memory."""
    from urllib.parse import parse_qsl, urlsplit
    try:
        parsed = urlsplit(value)
        if (parsed.scheme not in {"https", "http"} or not parsed.hostname or parsed.username is not None
                or parsed.password is not None or parsed.fragment or parsed.port not in {None, 80, 443}
                or any(char.isspace() or ord(char) < 32 for char in value)):
            raise ValueError
        forbidden = {"sig", "signature", "token", "access_token", "auth", "authorization", "key", "api_key",
                     "password", "credential", "session", "sessionid", "code", "secret", "expires", "policy", "se", "sp", "sv"}
        for key, content in parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True, max_num_fields=16):
            if (key not in query_keys or key.lower() in forbidden or any(word in key.lower() for word in ("token", "secret", "password", "signature", "credential"))
                    or not re.fullmatch(r"[A-Za-z0-9_.~-]{1,160}", content)):
                raise ValueError
    except (ValueError, TypeError):
        raise UserError("Use an unsigned stable source URL without credentials or fragments. Query keys must be explicitly declared nonsecret chapter/page routing fields; signed seeds are not saved.") from None
    return value


def atomic_write(path: Path, raw: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink() or any(parent.is_symlink() for parent in path.parents):
        raise UserError("Refusing a symbolic-link storage destination.")
    part = path.with_name(path.name + ".part")
    if part.is_symlink():
        raise UserError("Refusing a symbolic-link staging file.")
    try:
        with part.open("wb") as stream:
            stream.write(raw)
            stream.flush()
            import os
            os.fsync(stream.fileno())
        part.replace(path)
    finally:
        if part.exists() and not part.is_symlink():
            part.unlink()
