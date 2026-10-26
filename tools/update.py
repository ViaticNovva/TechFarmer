"""Canonical, manifest-verified TechFarmer source updater (CPython 3.11–3.15, stdlib).

First installation of this tool: copy the reviewed tools/update.py and
UPDATE-WINDOWS.cmd into the existing project, retaining its old manifest. Later,
double-click UPDATE-WINDOWS.cmd. Never replace settings or a database to update.

check_update() is metadata-only and safe for Discord. RuntimeLock() must cover
an entire bot/pull session. Updating and rollback are LOCAL, explicit actions.
SHA-256 detects damage, not authorship: trust is the fixed canonical TLS origin.
Backups and crash journals live under .runtime/update/. Rollback restores SOURCE,
not installed Python packages. No Git, administrator rights or model downloads.
"""
from __future__ import annotations

import argparse
import base64
import binascii
from contextlib import AbstractContextManager
from dataclasses import dataclass
import errno
from hashlib import sha256
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import ssl
import stat
import subprocess
import sys
import sysconfig
import time
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import (HTTPRedirectHandler, HTTPSHandler, ProxyHandler,
                            Request, build_opener)
from uuid import uuid4
import zipfile

REPOSITORY = "https://github.com/ViaticNovva/TechFarmer"
API_ROOT = "https://api.github.com/repos/ViaticNovva/TechFarmer"
MAIN_URL = API_ROOT + "/git/ref/heads/main"
MANIFEST = "SOURCE-MANIFEST.sha256"
RUNTIME = ".runtime/update"
STATE = RUNTIME + "/state.json"
JOURNAL = RUNTIME + "/journal.json"
INCOMPLETE = RUNTIME + "/legacy-incomplete"
LOCK = ".runtime/techfarmer.lock"
HTTP_TIMEOUT = 12
HTTP_TOTAL_SECONDS = 45
MAX_METADATA = 256 * 1024
MAX_MANIFEST = 64 * 1024
MAX_ARCHIVE = 8 * 1024 * 1024
MAX_FILE = 2 * 1024 * 1024
MAX_EXPANDED = 16 * 1024 * 1024
MAX_ENTRIES = 128
MAX_RATIO = 100
MAX_JOURNAL = 192 * 1024
COMMIT_RE = re.compile(r"[0-9a-f]{40}\Z")
HASH_RE = re.compile(r"[0-9a-f]{64}\Z")
VERSION_RE = re.compile(r"[0-9]+(?:\.[0-9]+){1,3}(?:[-+][A-Za-z0-9.-]+)?\Z")

# Exact paths, not writable-directory prefixes. New source paths require review
# of this allowlist; settings, tokens, databases and model files never belong here.
MANAGED_FILES = frozenset({
    ".github/workflows/ci.yml", ".gitignore", "DOWNLOAD-MODEL-WINDOWS.cmd",
    "INSTALL-VISION-WINDOWS.cmd", "INSTALL-WINDOWS.cmd", "README.md",
    "RELEASE-NOTES.md", "START-HERE.html", "START-WINDOWS.cmd", "TESTING.md",
    "UPDATE-WINDOWS.cmd", "requirements.txt", "requirements-vision.txt",
    "tech_farmer/__init__.py", "tech_farmer/__main__.py", "tech_farmer/bot.py",
    "tech_farmer/config.py", "tech_farmer/engine.py", "tech_farmer/exports.py",
    "tech_farmer/framing.py", "tech_farmer/images.py", "tech_farmer/safety.py",
    "tech_farmer/service.py", "tech_farmer/sources.py", "tech_farmer/store.py",
    "tech_farmer/vision.py", "tech_farmer/storage.py", "tech_farmer/source_setup.py",
    "tests/__init__.py", "tests/helpers.py", "tests/test_config_service.py",
    "tests/test_pipeline.py", "tests/test_sdk.py", "tests/test_setup.cjs",
    "tests/test_sources.py", "tests/test_vision_framing.py", "tests/test_storage.py",
    "tests/test_v03_workflow.py", "tests/test_source_setup.py", "tests/test_updater.py",
    "tools/demo.py", "tools/model_smoke.py", "tools/verify_manifest.py", "tools/update.py",
})
BASE_REQUIRED = frozenset({
    "tech_farmer/__init__.py", "tech_farmer/__main__.py", "tech_farmer/bot.py",
    "tech_farmer/config.py", "tech_farmer/engine.py", "tech_farmer/exports.py",
    "tech_farmer/framing.py", "tech_farmer/images.py", "tech_farmer/safety.py",
    "tech_farmer/service.py", "tech_farmer/sources.py", "tech_farmer/store.py",
    "tech_farmer/vision.py", "requirements.txt", "requirements-vision.txt",
    "START-WINDOWS.cmd", "INSTALL-WINDOWS.cmd", "START-HERE.html",
})
REQUIRED_FILES = BASE_REQUIRED | frozenset({
    "tech_farmer/storage.py", "tech_farmer/source_setup.py", "tools/update.py",
    "UPDATE-WINDOWS.cmd", "tools/verify_manifest.py",
})
BOOTSTRAP_FILES = frozenset({"tools/update.py", "UPDATE-WINDOWS.cmd"})
SOURCE_DIRS = frozenset(str(parent) for name in MANAGED_FILES
                        for parent in PurePosixPath(name).parents if str(parent) != ".")
Transport = Callable[..., bytes]


class UpdateError(Exception):
    """A safe, actionable error. Never contains upstream response bodies/secrets."""


class RuntimeBusyError(UpdateError):
    """The bot, a local pull or another updater holds the real operating-system lock."""


def _hash(raw: bytes) -> str:
    return sha256(raw).hexdigest()


def _relative(name: str) -> str:
    if (not isinstance(name, str) or not name or "\\" in name or ":" in name
            or "\x00" in name or name.startswith("/")
            or any(part in {"", ".", ".."} or part.endswith((" ", "."))
                   for part in name.split("/"))):
        raise UpdateError("Unsafe relative path; nothing was extracted.")
    return name


def _check_chain(path: Path) -> None:
    for component in (*reversed(path.parents), path):
        try:
            info = component.lstat()
        except FileNotFoundError:
            continue
        if (stat.S_ISLNK(info.st_mode)
                or getattr(info, "st_file_attributes", 0) & 0x400):
            raise UpdateError("Symlink/junction/reparse paths are not allowed for an update.")
        if component != path and not stat.S_ISDIR(info.st_mode):
            raise UpdateError("A source parent is not a real directory.")
        if stat.S_ISREG(info.st_mode) and info.st_nlink != 1:
            raise UpdateError("Hard-linked source/runtime files are not safe update targets.")


def _root(root=None) -> Path:
    path = Path(os.path.abspath(os.fspath(root if root is not None else Path(__file__).parent.parent)))
    _check_chain(path)
    if not path.is_dir():
        raise UpdateError("The TechFarmer installation folder does not exist.")
    return path


def _path(root: Path, name: str) -> Path:
    path = root.joinpath(*_relative(name).split("/"))
    _check_chain(path)
    return path


def _exists(root: Path, name: str) -> bool:
    try:
        _path(root, name).lstat()
        return True
    except FileNotFoundError:
        return False


def _mkdir(root: Path, name: str) -> None:
    current = root
    for part in _relative(name).split("/"):
        current = current / part
        _check_chain(current)
        try:
            current.mkdir(mode=0o700)
        except FileExistsError:
            if not current.is_dir():
                raise UpdateError("An update directory collides with an existing file.") from None
        else:
            _sync_dir(current.parent)
        _check_chain(current)


def _read(root: Path, name: str, limit: int = MAX_FILE) -> bytes:
    path = _path(root, name)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    try:
        fd = os.open(path, flags)
    except OSError:
        raise UpdateError("A required local source/runtime file could not be read: " + name) from None
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > limit:
            raise UpdateError("A local source/runtime file is not a bounded regular file: " + name)
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise UpdateError("A local file exceeds the safety limit: " + name)
    return raw


def _sync_dir(path: Path) -> None:
    if os.name != "nt":
        _check_chain(path)
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0))
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def _write_new(root: Path, name: str, raw: bytes) -> None:
    parent = str(PurePosixPath(name).parent)
    if parent != ".":
        _mkdir(root, parent)
    path = _path(root, name)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    fd = os.open(path, flags, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    _sync_dir(path.parent)


def _replace_file(root: Path, source: str, target: str) -> None:
    parent = str(PurePosixPath(target).parent)
    if parent != ".":
        _mkdir(root, parent)
    source_path, target_path = _path(root, source), _path(root, target)
    if not source_path.is_file():
        raise UpdateError("A staged update file is missing.")
    os.replace(source_path, target_path)
    _sync_dir(target_path.parent)


def _unlink(root: Path, name: str) -> None:
    if not _exists(root, name):
        return
    path = _path(root, name)
    if not stat.S_ISREG(path.lstat().st_mode):
        raise UpdateError("Refusing to remove a non-file update target.")
    path.unlink()
    _sync_dir(path.parent)


def _atomic_write(root: Path, name: str, raw: bytes) -> None:
    parent = PurePosixPath(name).parent
    scratch = (parent / (".tf-" + uuid4().hex + ".part")).as_posix()
    try:
        _write_new(root, scratch, raw)
        _replace_file(root, scratch, name)
    finally:
        if _exists(root, scratch):
            _unlink(root, scratch)


def _json(raw: bytes):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise UpdateError("Duplicate JSON fields are not allowed.")
            result[key] = value
        return result

    def constant(_):
        raise UpdateError("Non-finite JSON values are not allowed.")

    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=pairs, parse_constant=constant)
    except (UnicodeError, ValueError, RecursionError):
        raise UpdateError("Malformed UTF-8/JSON from the repository or updater state.") from None


def _json_bytes(value) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n").encode("utf-8")


def _resource(url: str) -> tuple[str, str]:
    try:
        parts = urlsplit(url)
        if (parts.scheme != "https" or parts.username is not None or parts.password is not None
                or parts.port not in (None, 443) or parts.fragment):
            raise ValueError
    except ValueError:
        raise UpdateError("Only fixed canonical public HTTPS update URLs are allowed.") from None
    host, path = parts.hostname, parts.path
    base = "/repos/ViaticNovva/TechFarmer"
    if host == "api.github.com" and path == base + "/git/ref/heads/main" and not parts.query:
        return "metadata", "main"
    if (host == "api.github.com" and path == base + "/contents/" + MANIFEST
            and re.fullmatch(r"ref=[0-9a-f]{40}", parts.query)):
        return "manifest", parts.query[4:]
    if host == "codeload.github.com" and not parts.query:
        match = re.fullmatch(r"/ViaticNovva/TechFarmer/zip/([0-9a-f]{40})", path)
        if match:
            return "archive", match[1]
    if host == "github.com" and not parts.query:
        match = re.fullmatch(r"/ViaticNovva/TechFarmer/archive/([0-9a-f]{40})\.zip", path)
        if match:
            return "archive", match[1]
    raise UpdateError("A non-canonical or mutable update URL/redirect was refused.")


class CanonicalRedirectHandler(HTTPRedirectHandler):
    max_redirections = 3
    max_repeats = 2

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if _resource(req.full_url) != _resource(newurl):
            raise UpdateError("A redirect changed the pinned update resource.")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def public_fetch(url: str, *, max_bytes: int, timeout: int) -> bytes:
    """Public TLS only. No tokens, cookies, netrc, proxy environment or fallback."""
    _resource(url)
    opener = build_opener(ProxyHandler({}), CanonicalRedirectHandler(),
                          HTTPSHandler(context=ssl.create_default_context()))
    request = Request(url, headers={"User-Agent": "TechFarmer-source-updater",
                                   "Accept": "application/vnd.github+json",
                                   "Accept-Encoding": "identity"})
    deadline = time.monotonic() + HTTP_TOTAL_SECONDS
    try:
        with opener.open(request, timeout=timeout) as response:
            if response.status != 200 or _resource(response.geturl()) != _resource(url):
                raise UpdateError("The canonical server did not return the requested public resource.")
            if response.headers.get("Content-Encoding", "identity").lower() != "identity":
                raise UpdateError("Unexpected encoded HTTP response was refused.")
            length = response.headers.get("Content-Length")
            if length is not None and (not length.isdecimal() or int(length) > max_bytes):
                raise UpdateError("The update download exceeds its byte limit.")
            chunks, total = [], 0
            while True:
                if time.monotonic() >= deadline:
                    raise UpdateError("Canonical GitHub download timed out; no alternate source was tried.")
                chunk = response.read1(min(64 * 1024, max_bytes + 1 - total))
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise UpdateError("The update download exceeds its byte limit.")
                chunks.append(chunk)
            if length is not None and total != int(length):
                raise UpdateError("The canonical download was truncated.")
            return b"".join(chunks)
    except HTTPError as exc:
        if exc.code == 404:
            raise UpdateError("Canonical public repository/source is unavailable, private or incomplete. Nothing will be installed.") from None
        raise UpdateError("Canonical public GitHub refused the request (possibly rate limited). Retry later; no alternate source was tried.") from None
    except (URLError, TimeoutError, OSError):
        raise UpdateError("Canonical GitHub is unreachable or TLS verification failed. Check approved network access; no proxy/DNS workaround was attempted.") from None


def _fetch(url: str, limit: int, transport: Transport | None) -> bytes:
    _resource(url)
    try:
        raw = (transport or public_fetch)(url, max_bytes=limit, timeout=HTTP_TIMEOUT)
    except UpdateError:
        raise
    except (OSError, TimeoutError):
        raise UpdateError("Canonical GitHub could not be reached. No alternate source was tried.") from None
    if not isinstance(raw, bytes) or len(raw) > limit:
        raise UpdateError("The canonical response has an invalid type or exceeds its byte limit.")
    return raw


def _resolve_main(transport: Transport | None) -> str:
    metadata = _json(_fetch(MAIN_URL, MAX_METADATA, transport))
    reference = metadata.get("object") if isinstance(metadata, dict) else None
    if (not isinstance(metadata, dict) or metadata.get("ref") != "refs/heads/main"
            or not isinstance(reference, dict) or reference.get("type") != "commit"
            or not isinstance(reference.get("sha"), str) or not COMMIT_RE.fullmatch(reference["sha"])):
        raise UpdateError("Canonical main metadata lacks an immutable commit hash.")
    return reference["sha"]


def _text(raw: bytes, name: str) -> str:
    try:
        text = raw.decode("utf-8")
    except UnicodeError:
        raise UpdateError("Source is not valid UTF-8: " + name) from None
    if not text.strip() or "\x00" in text or "\r" in text or text.startswith("\ufeff") or not raw.endswith(b"\n"):
        raise UpdateError("Source must be nonempty plain UTF-8/LF text with a final newline: " + name)
    return text


def parse_manifest(raw: bytes, *, incoming: bool = True) -> dict[str, str]:
    if not raw or len(raw) > MAX_MANIFEST:
        raise UpdateError("The source manifest is missing or too large; incomplete snapshot refused.")
    result = {}
    for line in _text(raw, MANIFEST).splitlines():
        match = re.fullmatch(r"([0-9a-f]{64})  (.+)", line)
        if not match:
            raise UpdateError("Malformed source manifest; incomplete snapshot refused.")
        digest, name = match.groups()
        _relative(name)
        if name not in MANAGED_FILES:
            raise UpdateError("Manifest contains an unreviewed/protected path: " + name[:120])
        if name in result:
            raise UpdateError("Duplicate source manifest entry.")
        result[name] = digest
    missing = (REQUIRED_FILES if incoming else BASE_REQUIRED) - result.keys()
    if missing:
        raise UpdateError("Incomplete source snapshot: required bot/setup files are missing (" + ", ".join(sorted(missing)) + ").")
    return result


@dataclass(frozen=True)
class Snapshot:
    commit: str
    manifest: bytes
    hashes: dict[str, str]
    files: dict[str, bytes]


def verify_archive(raw: bytes, commit: str, manifest: bytes) -> Snapshot:
    if not COMMIT_RE.fullmatch(commit) or len(raw) > MAX_ARCHIVE:
        raise UpdateError("Invalid pinned archive or archive size.")
    expected = parse_manifest(manifest)
    prefix = "TechFarmer-" + commit + "/"
    expected_paths = set(expected) | {MANIFEST}
    allowed_dirs = {prefix.rstrip("/")}
    for name in expected_paths:
        allowed_dirs.update(prefix + str(p) for p in PurePosixPath(name).parents if str(p) != ".")
    files, seen, expanded = {}, set(), 0
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            entries = archive.infolist()
            if len(entries) > MAX_ENTRIES:
                raise UpdateError("Archive has too many entries.")
            for info in entries:
                original = info.orig_filename
                if "\x00" in original or "\\" in original or ":" in original or original.startswith("/"):
                    raise UpdateError("Unsafe ZIP path refused.")
                name = original[:-1] if original.endswith("/") else original
                _relative(name)
                if name.casefold() in seen:
                    raise UpdateError("Duplicate or case-colliding ZIP entry refused.")
                seen.add(name.casefold())
                mode = (info.external_attr >> 16) & 0xFFFF
                kind = stat.S_IFMT(mode)
                if (kind not in {0, stat.S_IFREG, stat.S_IFDIR}
                        or info.external_attr & 0x400 or info.flag_bits & 1
                        or info.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}):
                    raise UpdateError("ZIP links, special files, encryption or unsupported compression refused.")
                if info.is_dir():
                    if name not in allowed_dirs or info.file_size or kind == stat.S_IFREG:
                        raise UpdateError("Unexpected archive directory refused.")
                    continue
                if kind == stat.S_IFDIR or not original.startswith(prefix):
                    raise UpdateError("Archive is not the exact pinned canonical root.")
                relative = original[len(prefix):]
                if relative not in expected_paths:
                    raise UpdateError("Archive contains unmanifested/protected source; snapshot refused.")
                expanded += info.file_size
                if (info.file_size > MAX_FILE or expanded > MAX_EXPANDED
                        or info.file_size > max(1, info.compress_size) * MAX_RATIO):
                    raise UpdateError("Archive expansion/size safety limit exceeded.")
                with archive.open(info) as stream:
                    payload = stream.read(min(MAX_FILE, info.file_size) + 1)
                if len(payload) != info.file_size:
                    raise UpdateError("An archive member has an invalid expanded size.")
                _text(payload, relative)
                if relative == MANIFEST:
                    if payload != manifest:
                        raise UpdateError("Pinned archive and pinned source manifest disagree.")
                elif _hash(payload) != expected[relative]:
                    raise UpdateError("Source hash mismatch: " + relative)
                files[relative] = payload
    except (zipfile.BadZipFile, NotImplementedError, RuntimeError, EOFError, OSError, ValueError):
        raise UpdateError("Malformed or truncated source ZIP; nothing was extracted.") from None
    if set(files) != expected_paths:
        raise UpdateError("Incomplete source archive: required manifest payloads are missing.")
    return Snapshot(commit, manifest, expected, files)


def download_snapshot(transport: Transport | None = None) -> Snapshot:
    commit = _resolve_main(transport)
    url = API_ROOT + "/contents/" + MANIFEST + "?ref=" + commit
    metadata = _json(_fetch(url, MAX_METADATA, transport))
    if (not isinstance(metadata, dict) or metadata.get("type") != "file"
            or metadata.get("path") != MANIFEST or metadata.get("encoding") != "base64"
            or not isinstance(metadata.get("content"), str)
            or type(metadata.get("size")) is not int or not 0 < metadata["size"] <= MAX_MANIFEST):
        raise UpdateError("Canonical main has no complete source manifest. A documentation-only/private/incomplete repository cannot be installed.")
    try:
        manifest = base64.b64decode(metadata["content"].replace("\n", ""), validate=True)
    except (ValueError, binascii.Error):
        raise UpdateError("Malformed pinned source manifest encoding.") from None
    if len(manifest) != metadata["size"]:
        raise UpdateError("Pinned manifest length does not match its metadata.")
    parse_manifest(manifest)
    archive = _fetch("https://codeload.github.com/ViaticNovva/TechFarmer/zip/" + commit,
                     MAX_ARCHIVE, transport)
    return verify_archive(archive, commit, manifest)


def _version(raw: bytes) -> str:
    try:
        text = raw.decode("utf-8")
    except UnicodeError:
        return "unknown"
    match = re.search(r'^__version__\s*=\s*[\'"]([^\'"\n]+)[\'"]\s*$', text, re.MULTILINE)
    return match[1] if match and VERSION_RE.fullmatch(match[1]) else "unknown"


def _state(root: Path):
    if not _exists(root, STATE):
        return None
    state = _json(_read(root, STATE, 8192))
    if (not isinstance(state, dict) or set(state) != {"schema", "commit", "manifest_sha256", "version"}
            or type(state["schema"]) is not int or state["schema"] != 1
            or not isinstance(state["commit"], str) or not COMMIT_RE.fullmatch(state["commit"])
            or not isinstance(state["manifest_sha256"], str) or not HASH_RE.fullmatch(state["manifest_sha256"])
            or not isinstance(state["version"], str)
            or state["version"] != "unknown" and not VERSION_RE.fullmatch(state["version"])):
        raise UpdateError("Invalid updater state; preserve .runtime/update and inspect it before updating.")
    return state


def check_update(root=None, *, transport: Transport | None = None) -> dict:
    """Read version + ONE fixed public main metadata request; no lock/write/install.

    Discord may call this (prefer asyncio.to_thread). Do not expose apply_update,
    rollback_update, a URL, a branch or shell arguments through Discord.
    Metadata alone cannot establish that remote main contains installable source.
    """
    result = {"status": "unavailable", "local_version": "unknown", "local_commit": None,
              "remote_commit": None, "message": "No update was installed."}
    try:
        root = _root(root)
        if _exists(root, "tech_farmer/__init__.py"):
            result["local_version"] = _version(_read(root, "tech_farmer/__init__.py", 64 * 1024))
        state = _state(root)
        if state:
            result["local_commit"] = state["commit"]
        result["remote_commit"] = _resolve_main(transport)
        if state and _exists(root, MANIFEST) and _hash(_read(root, MANIFEST, MAX_MANIFEST)) == state["manifest_sha256"]:
            same = state["commit"] == result["remote_commit"]
            result["status"] = "up_to_date" if same else "update_available"
            result["message"] = ("Canonical main matches the recorded source commit. This is a version check, not an integrity audit."
                                 if same else "A different canonical commit is available for verification. Stop the bot and run the LOCAL updater; remote completeness is not yet verified.")
        else:
            result["status"] = "unverified_local"
            result["message"] = "Local commit is not recorded. Canonical main was found, but installable source is not yet verified. Stop the bot and use the LOCAL updater."
    except (UpdateError, OSError):
        result["status"] = "unavailable"
        result["message"] = "Update check unavailable: canonical public source/network or local updater state could not be verified. No update was installed; no alternative source was tried."
    return result


class RuntimeLock(AbstractContextManager):
    """Exclusive OS lock shared by bot, local pull, updater and rollback.

    PID text is diagnostic only. A stale PID/file never counts as a held lock.
    Do not unlink the lock file: doing so can create two independent locks.
    """
    def __init__(self, root=None, *, for_update: bool = False):
        self.root = _root(root)
        self.for_update = for_update
        self.fd = None

    def __enter__(self):
        _mkdir(self.root, ".runtime")
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
        self.fd = os.open(_path(self.root, LOCK), flags, 0o600)
        try:
            info = os.fstat(self.fd)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise UpdateError("Runtime lock is not a private regular file.")
            try:
                if os.name == "nt":
                    import msvcrt
                    os.lseek(self.fd, 0, os.SEEK_SET)
                    msvcrt.locking(self.fd, msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                raise RuntimeBusyError("TechFarmer is running or another local updater/pull is active. Stop it before updating; a PID file alone is not used as proof.") from None
            os.lseek(self.fd, 0, os.SEEK_SET)
            os.write(self.fd, (str(os.getpid()) + "\n").encode("ascii"))
            if _exists(self.root, INCOMPLETE):
                raise UpdateError("This is an incomplete legacy staging folder, not a ready installation. Keep using/preserving the original folder and review the staged copy; do not start the bot here.")
            if not self.for_update:
                journal = _load_journal(self.root)
                if journal and journal["phase"] not in {"committed", "rolled_back"}:
                    raise UpdateError("An interrupted source update needs recovery. Do not start the bot; run python tools/update.py --rollback locally.")
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def __exit__(self, *_):
        if self.fd is not None:
            try:
                if os.name == "nt":
                    import msvcrt
                    os.lseek(self.fd, 0, os.SEEK_SET)
                    try:
                        msvcrt.locking(self.fd, msvcrt.LK_UNLCK, 1)
                    except OSError:
                        pass
                else:
                    import fcntl
                    fcntl.flock(self.fd, fcntl.LOCK_UN)
            finally:
                os.close(self.fd)
                self.fd = None
        return False


def _hash_map(value, allowed, *, nullable=False) -> bool:
    return (isinstance(value, dict) and len(value) <= len(MANAGED_FILES) + 2
            and all(name in allowed and ((digest is None and nullable)
                    or isinstance(digest, str) and HASH_RE.fullmatch(digest))
                    for name, digest in value.items()))


def _load_journal(root: Path):
    if not _exists(root, JOURNAL):
        return None
    journal = _json(_read(root, JOURNAL, MAX_JOURNAL))
    keys = {"schema", "root", "transaction", "phase", "commit", "before", "after",
            "before_inventory", "after_inventory", "created_dirs"}
    if (not isinstance(journal, dict) or set(journal) != keys
            or type(journal["schema"]) is not int or journal["schema"] != 1
            or journal["root"] != str(root)
            or not isinstance(journal["transaction"], str)
            or not re.fullmatch(r"[0-9a-f]{32}", journal["transaction"])
            or not isinstance(journal["phase"], str)
            or journal["phase"] not in {"prepared", "applying", "committed", "rolling_back", "rolled_back"}
            or not isinstance(journal["commit"], str) or not COMMIT_RE.fullmatch(journal["commit"])
            or not _hash_map(journal["before"], MANAGED_FILES | {MANIFEST, STATE}, nullable=True)
            or not _hash_map(journal["after"], MANAGED_FILES | {MANIFEST, STATE}, nullable=True)
            or not _hash_map(journal["before_inventory"], MANAGED_FILES)
            or not _hash_map(journal["after_inventory"], MANAGED_FILES)
            or set(journal["before"]) != set(journal["after"])
            or not journal["before"].get(MANIFEST) or not journal["after"].get(MANIFEST)
            or not journal["after"].get(STATE)
            or not isinstance(journal["created_dirs"], list)
            or any(not isinstance(name, str) or name not in SOURCE_DIRS for name in journal["created_dirs"])
            or len(set(journal["created_dirs"])) != len(journal["created_dirs"])):
        raise UpdateError("Invalid/cross-root update journal; no files will be changed.")
    before, after = journal["before_inventory"], journal["after_inventory"]
    touched = {name for name in before.keys() | after.keys() if before.get(name) != after.get(name)}
    if (set(journal["before"]) != touched | {MANIFEST, STATE}
            or not BASE_REQUIRED <= before.keys() or not REQUIRED_FILES <= after.keys()
            or any(journal["before"][name] != before.get(name) or journal["after"][name] != after.get(name)
                   for name in touched)):
        raise UpdateError("Inconsistent update journal; backups were not used.")
    return journal


def _write_journal(root: Path, journal, phase: str) -> None:
    journal["phase"] = phase
    _atomic_write(root, JOURNAL, _json_bytes(journal))


def _current_hash(root: Path, name: str):
    return _hash(_read(root, name)) if _exists(root, name) else None


def _verify_inventory(root: Path, inventory: dict[str, str]) -> None:
    changed = [name for name, digest in inventory.items() if _current_hash(root, name) != digest]
    if changed:
        raise UpdateError("Local managed source was changed or removed; it will NOT be overwritten: " + ", ".join(sorted(changed)[:12]))


def _verify_installed(root: Path, snapshot: Snapshot, removed=()) -> None:
    _verify_inventory(root, snapshot.hashes)
    if _read(root, MANIFEST, MAX_MANIFEST) != snapshot.manifest or any(_exists(root, name) for name in removed):
        raise UpdateError("Final source verification failed.")


def _backup_name(journal, name: str) -> str:
    return RUNTIME + "/transactions/" + journal["transaction"] + "/before/" + name


def _restore(root: Path, journal, *, recovering: bool) -> None:
    before, after = journal["before"], journal["after"]
    backups = {}
    for name, digest in before.items():
        if digest is not None:
            raw = _read(root, _backup_name(journal, name))
            if _hash(raw) != digest:
                raise UpdateError("Backup integrity failed; nothing was restored. Preserve .runtime/update.")
            backups[name] = raw
    for name in before:
        allowed = {before[name], after[name]} if recovering else {after[name]}
        if _current_hash(root, name) not in allowed:
            raise UpdateError("Rollback/recovery found a later local edit and will not overwrite it: " + name)
    if recovering:
        unchanged = {name: digest for name, digest in journal["before_inventory"].items() if name not in before}
        _verify_inventory(root, unchanged)
    else:
        _verify_inventory(root, journal["after_inventory"])
    _write_journal(root, journal, "rolling_back")
    ordered = sorted(set(before) - {MANIFEST, STATE}) + [MANIFEST, STATE]
    for name in ordered:
        if before[name] is None:
            _unlink(root, name)
        else:
            _atomic_write(root, name, backups[name])
    _verify_inventory(root, journal["before_inventory"])
    if any(_current_hash(root, name) != digest for name, digest in before.items()):
        raise UpdateError("Recovery verification failed; preserve backups and retry --rollback.")
    for name in sorted(journal["created_dirs"], key=lambda value: value.count("/"), reverse=True):
        try:
            _path(root, name).rmdir()
        except FileNotFoundError:
            pass
        except OSError as exc:
            if exc.errno not in {errno.ENOTEMPTY, errno.EEXIST}:
                raise
    _write_journal(root, journal, "rolled_back")


def rollback_update(root=None) -> dict:
    root = _root(root)
    with RuntimeLock(root, for_update=True):
        journal = _load_journal(root)
        if not journal:
            raise UpdateError("No source-update transaction is available to roll back.")
        if journal["phase"] == "rolled_back":
            return {"status": "rolled_back", "message": "Source was already rolled back. Python packages were not changed."}
        _restore(root, journal, recovering=journal["phase"] != "committed")
        return {"status": "rolled_back", "message": "Previous source and manifest restored; settings/data were not changed. Installed Python packages are NOT rolled back."}


def _new_state(snapshot: Snapshot) -> bytes:
    return _json_bytes({"schema": 1, "commit": snapshot.commit,
                        "manifest_sha256": _hash(snapshot.manifest),
                        "version": _version(snapshot.files["tech_farmer/__init__.py"])})


def _apply(root: Path, snapshot: Snapshot, old_manifest: bytes, old: dict[str, str]) -> dict:
    _verify_inventory(root, old)
    for name in snapshot.hashes.keys() - old.keys():
        if _exists(root, name):
            if name in BOOTSTRAP_FILES and _current_hash(root, name) == snapshot.hashes[name]:
                old[name] = snapshot.hashes[name]
            else:
                raise UpdateError("An untracked local file would be overwritten; preserve/review it first: " + name)
    _state(root)
    if (old == snapshot.hashes and old_manifest == snapshot.manifest
            and _exists(root, STATE) and _read(root, STATE, 8192) == _new_state(snapshot)):
        return {"status": "up_to_date", "commit": snapshot.commit,
                "message": "Source already matches the verified canonical snapshot. No source was replaced."}
    touched = {name for name in old.keys() | snapshot.hashes.keys() if old.get(name) != snapshot.hashes.get(name)}
    before = {name: old.get(name) for name in sorted(touched)}
    after = {name: snapshot.hashes.get(name) for name in sorted(touched)}
    before.update({MANIFEST: _hash(old_manifest), STATE: _current_hash(root, STATE)})
    state = _new_state(snapshot)
    after.update({MANIFEST: _hash(snapshot.manifest), STATE: _hash(state)})
    transaction = uuid4().hex
    prefix = RUNTIME + "/transactions/" + transaction
    created_dirs = sorted({str(parent) for name in touched for parent in PurePosixPath(name).parents
                           if str(parent) != "." and not _exists(root, str(parent))})
    journal = {"schema": 1, "root": str(root), "transaction": transaction, "phase": "prepared",
               "commit": snapshot.commit, "before": before, "after": after,
               "before_inventory": dict(old), "after_inventory": dict(snapshot.hashes),
               "created_dirs": created_dirs}
    for name, digest in before.items():
        if digest is not None:
            raw = _read(root, name)
            if _hash(raw) != digest:
                raise UpdateError("Local source changed during preparation; update refused.")
            backup = _backup_name(journal, name)
            _write_new(root, backup, raw)
            if _hash(_read(root, backup)) != digest:
                raise UpdateError("Backup verification failed before installation.")
    payloads = dict(snapshot.files)
    payloads[STATE] = state
    for name, digest in after.items():
        if digest is not None:
            stage = prefix + "/stage/" + name
            _write_new(root, stage, payloads[name])
            if _hash(_read(root, stage)) != digest:
                raise UpdateError("Staging verification failed; installed source is untouched.")
    _verify_inventory(root, old)
    try:
        _write_journal(root, journal, "prepared")
        _write_journal(root, journal, "applying")
        for name in sorted(touched) + [MANIFEST]:
            if _current_hash(root, name) != before[name]:
                raise UpdateError("A local source file changed during installation.")
            if after[name] is None:
                _unlink(root, name)
            else:
                _replace_file(root, prefix + "/stage/" + name, name)
        _verify_installed(root, snapshot, old.keys() - snapshot.hashes.keys())
        _replace_file(root, prefix + "/stage/" + STATE, STATE)
        if any(_current_hash(root, name) != digest for name, digest in after.items()):
            raise UpdateError("Final transaction verification failed.")
        _write_journal(root, journal, "committed")
    except BaseException as exc:
        try:
            _restore(root, journal, recovering=True)
        except BaseException:
            raise UpdateError("Update interrupted; recovery needs attention. Do NOT start TechFarmer. Preserve .runtime/update and run --rollback. Source backups are retained; no settings/data were deleted.") from None
        if isinstance(exc, KeyboardInterrupt):
            return {"status": "cancelled", "message": "Cancelled; previous source was restored. No dependency sync was run."}
        raise UpdateError("Update failed; previous source and manifest were restored. Settings/data and Python packages were not changed.") from None
    return {"status": "updated", "commit": snapshot.commit,
            "message": "Verified source update complete. Settings/jobs/references/exports/cache/.venv were not changed. Source rollback is available; model/vision refresh was NOT run."}


def _stage_legacy(root: Path, snapshot: Snapshot) -> dict:
    # There is no trustworthy source ownership list in a v0.1 folder. Never infer
    # one by filenames, and never move/copy its private settings or databases.
    name = root.name + "-updated-" + snapshot.commit[:12]
    destination = root.parent / name
    _check_chain(destination)
    if destination.exists():
        raise UpdateError("A legacy staging sibling already exists: " + str(destination) + ". It was not overwritten; keep the original installation and review the staged folder.")
    destination.mkdir(mode=0o700, exist_ok=False)
    _sync_dir(root.parent)
    _write_new(destination, INCOMPLETE, b"Incomplete legacy source staging; preserve the original installation.\n")
    try:
        for name in sorted(snapshot.files):
            _write_new(destination, name, snapshot.files[name])
        _verify_installed(destination, snapshot)
        _write_new(destination, STATE, _new_state(snapshot))
        _unlink(destination, INCOMPLETE)
        _sync_dir(root.parent)
    except BaseException:
        raise UpdateError("Legacy staging did not finish; the original installation is unchanged. Do not start the partial sibling at " + str(destination) + ". Keep its incomplete marker and preserve the original folder.") from None
    return {"status": "legacy_staged", "commit": snapshot.commit,
            "message": "No old manifest: nothing in the original installation was replaced. Verified clean source is staged at " + str(destination)
            + ". Keep the original folder at " + str(root)
            + ". Review/copy settings and private data only through the documented migration; retain original jobs/database/references/exports/cache. Recreate a new-folder .venv rather than moving the original. No configuration/database migration or dependency/model download was performed."}


def _light_requirements(raw: bytes) -> None:
    allowed = {"discord.py", "aiohttp", "pillow", "beautifulsoup4"}
    seen = set()
    for line in _text(raw, "requirements.txt").splitlines():
        if not line or line.startswith("#"):
            continue
        match = re.fullmatch(r"([A-Za-z0-9_.-]+)((?:==|>=|<=|~=|!=|>|<)[0-9][A-Za-z0-9.+-]*(?:,(?:==|>=|<=|~=|!=|>|<)[0-9][A-Za-z0-9.+-]*)*)", line)
        if not match or match[1].lower() not in allowed or match[1].lower() in seen:
            raise UpdateError("Base dependency sync refused: requirements must contain only the four reviewed light packages/version ranges, no URLs/options/includes or vision/model packages.")
        seen.add(match[1].lower())
    if seen != allowed:
        raise UpdateError("Base dependency list is incomplete; no packages were installed.")


def _stable_wheel_report(raw) -> bool:
    """Inspect pip's dry-run plan without printing index URLs or package log bodies."""
    if not isinstance(raw, (str, bytes)) or not 0 < len(raw) <= 2 * 1024 * 1024:
        return False
    try:
        report = json.loads(raw)
        packages = report["install"]
        if not isinstance(packages, list) or not packages or len(packages) > 256:
            return False
        for package in packages:
            version = package["metadata"]["version"]
            download = package["download_info"]
            if (not isinstance(version, str)
                    or not re.fullmatch(r"[0-9]+(?:\.[0-9]+)*(?:\.post[0-9]+)?(?:\+[A-Za-z0-9.]+)?", version)
                    or package.get("is_yanked", False)
                    or "vcs_info" in download or "dir_info" in download
                    or not urlsplit(download["url"]).path.lower().endswith(".whl")):
                return False
        return True
    except (AttributeError, TypeError, KeyError, ValueError, UnicodeError):
        return False


def _wheel_preflight(command, root: Path, *, environment=None) -> bool:
    # A configured pre-release policy must not silently install nightlies. Inspect
    # the actual wheel plan, rather than overriding the operator's index settings.
    arguments = [*command, "--dry-run", "--ignore-installed", "--quiet", "--report", "-"]
    options = {"env": environment} if environment is not None else {}
    result = subprocess.run(arguments, cwd=root, shell=False, stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                            timeout=900, check=False, **options)
    return result.returncode == 0 and _stable_wheel_report(result.stdout)


def _sync_base(root: Path, snapshot: Snapshot) -> dict:
    status, message = "failed", "Source is verified, but base dependency sync failed. The .venv is NOT rolled back; retry explicitly with --update --sync-base."
    try:
        if sys.prefix == sys.base_prefix or Path(sys.prefix).absolute() != root / ".venv":
            raise UpdateError("Source updated, but base sync requires running this updater with this project's .venv Python. Run INSTALL-WINDOWS.cmd or retry using .venv Python; no global packages were changed.")
        _check_chain(root / ".venv")
        requirements = _read(root, "requirements.txt")
        if _hash(requirements) != snapshot.hashes["requirements.txt"]:
            raise UpdateError("Requirements changed after verification; no packages were installed.")
        _light_requirements(requirements)
        command = [sys.executable, "-m", "pip", "--disable-pip-version-check",
                   "--no-input", "--require-virtualenv", "install", "--only-binary=:all:",
                   "-r", str(root / "requirements.txt")]
        if not _wheel_preflight(command, root):
            raise UpdateError("Source is verified, but compatible stable wheel resolution failed (index/network/wheel/conflict or prerelease/yanked/non-wheel plan). No packages were installed by this preflight. Run START-WINDOWS.cmd setup explicitly after resolving access; no older-Python fallback or source build occurred.")
        checks = [command, [sys.executable, "-m", "pip", "--disable-pip-version-check", "check"],
                  [sys.executable, "-I", "-X", "utf8", str(root / "tech_farmer" / "__main__.py"), "preflight"]]
        for index, arguments in enumerate(checks):
            result = subprocess.run(arguments, cwd=root, shell=False,
                                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL, timeout=300, check=False)
            if result.returncode:
                if index > 0:
                    message = "Source is verified, but actual base package/import preflight failed. Setup is NOT complete. The .venv is NOT rolled back; run START-WINDOWS.cmd setup/diagnostics. No Discord login or model download occurred."
                break
        else:
            status, message = "complete", "Verified source, stable-wheel resolution, pip check and actual base imports completed. No vision/model refresh was run. Source rollback does not undo Python packages."
    except UpdateError as exc:
        message = str(exc) + " Source rollback does not roll back the .venv."
    except (OSError, subprocess.SubprocessError):
        pass
    _atomic_write(root, RUNTIME + "/dependencies.json",
                  _json_bytes({"schema": 1, "commit": snapshot.commit, "status": status}))
    return {"status": "updated" if status == "complete" else "partial", "commit": snapshot.commit, "message": message}


def apply_update(root=None, *, transport: Transport | None = None, sync_base: bool = False,
                 confirm_stopped: bool = False) -> dict:
    """Explicit LOCAL source update. Never call from a Discord command handler.

    A pre-lock installation needs operator confirmation: a newly created OS lock
    cannot detect an already-running v0.2 process that never acquired that lock.
    """
    root = _root(root)
    with RuntimeLock(root, for_update=True):
        journal = _load_journal(root)
        if journal and journal["phase"] not in {"committed", "rolled_back"}:
            _restore(root, journal, recovering=True)
            return {"status": "recovered", "message": "Interrupted update rolled back from verified backups. No network request was made. Run --update again when ready. Python packages were not restored."}
        old_manifest = _read(root, MANIFEST, MAX_MANIFEST) if _exists(root, MANIFEST) else None
        old = parse_manifest(old_manifest, incoming=False) if old_manifest is not None else None
        if old is not None:
            _verify_inventory(root, old)
            receipt = _state(root)
            if (not receipt or receipt["manifest_sha256"] != _hash(old_manifest)) and confirm_stopped is not True:
                raise UpdateError("FIRST UPGRADE: older TechFarmer processes do not hold this new lock. Stop EVERY old bot/pull window, then confirm locally with --update --confirm-stopped (or type STOPPED in the launcher). Acquiring an unused lock is NOT proof that an older bot stopped. No network/source change was made.")
        snapshot = download_snapshot(transport)
        if old_manifest is None:
            return _stage_legacy(root, snapshot)
        result = _apply(root, snapshot, old_manifest, old)
        if sync_base and result["status"] in {"updated", "up_to_date"}:
            return _sync_base(root, snapshot)
        return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--check", action="store_true", help="Read public main metadata only; does not change files")
    group.add_argument("--update", action="store_true", help="Stop the bot first; verify and install canonical source")
    group.add_argument("--rollback", action="store_true", help="Recover/undo the last source transaction, not Python packages")
    parser.add_argument("--sync-base", action="store_true", help="With --update only: explicitly sync four light packages via this project's .venv and normal approved pip configuration; no index/policy override or model/vision refresh")
    parser.add_argument("--confirm-stopped", action="store_true", help="With --update only: explicitly confirm EVERY old/pre-lock bot and pull process was stopped; required for first upgrade")
    args = parser.parse_args(argv)
    if (args.sync_base or args.confirm_stopped) and not args.update:
        parser.error("--sync-base and --confirm-stopped require explicit --update")
    try:
        if (sys.implementation.name != "cpython" or not (3, 11) <= sys.version_info[:2] <= (3, 15)
                or sys.version_info[3] != "final" or sysconfig.get_config_var("Py_GIL_DISABLED")):
            raise UpdateError("Use stable, final standard CPython 3.11–3.15; alpha/beta/rc previews, free-threaded builds and 3.16+ are outside this release's target range. No administrator/policy changes are needed.")
        print("Updater interpreter:", ".".join(map(str, sys.version_info[:3])), "|", sys.executable)
        if not (args.check or args.update or args.rollback):
            print("TechFarmer source updater — canonical public repository ONLY:", REPOSITORY)
            print("[1] Check version (no changes)  [2] Update source (stop bot first)  [3] Roll back source  [Enter] Cancel")
            print("Settings/jobs/cache/.venv stay local. SHA-256 is integrity, not an author signature. No automatic model/dependency downloads.")
            try:
                choice = input("Choose 1, 2 or 3: ").strip()
            except EOFError:
                choice = ""
            if choice not in {"1", "2", "3"}:
                print("Cancelled. No source/network changes were requested.")
                return 0
            args.check, args.update, args.rollback = choice == "1", choice == "2", choice == "3"
            if args.update:
                print("Close EVERY running TechFarmer bot/pull first. Old v0.2 processes cannot be detected by the new lock.")
                try:
                    acknowledged = input("Type STOPPED to confirm, or Enter to cancel: ").strip()
                except EOFError:
                    acknowledged = ""
                if acknowledged != "STOPPED":
                    print("Cancelled. No update/network request was made.")
                    return 0
                args.confirm_stopped = True
        if args.check:
            result = check_update()
            print("Local version:", result["local_version"], "| recorded commit:", result["local_commit"] or "not recorded")
            print("Canonical main:", result["remote_commit"] or "unavailable")
        elif args.rollback:
            result = rollback_update()
        else:
            print("Verifying canonical source before installation. Incomplete documentation-only snapshots will be REFUSED.")
            result = apply_update(sync_base=args.sync_base, confirm_stopped=args.confirm_stopped)
        print(result["status"] + ": " + result["message"])
        return 2 if result["status"] in {"unavailable", "partial"} else 0
    except (UpdateError, OSError) as exc:
        print("Update stopped:", str(exc) if isinstance(exc, UpdateError) else "A local filesystem operation failed. Preserve .runtime/update; run --rollback if a transaction was interrupted.", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("Cancelled. If an update had started, retain .runtime/update and use --rollback before starting the bot.")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
