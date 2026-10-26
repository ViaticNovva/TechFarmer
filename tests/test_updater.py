"""Offline, self-authored updater fixtures; no GitHub, credentials or model downloads.

Run: python -B -m unittest tests.test_updater -v
Scratch defaults to this project's .runtime; a harness may set FIXTURE_BASE to
another workspace-owned directory, never a system temp folder. Windows CMD
execution itself requires Windows; Linux verifies its quoting and the
same Python entrypoint in a space/Unicode path, plus real POSIX process locks.
"""
from __future__ import annotations

import base64
import io
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import unittest
from unittest import mock
from urllib.request import Request
from uuid import uuid4
import warnings
import zipfile

from tools import update

PROJECT = Path(__file__).resolve().parents[1]
FIXTURE_BASE = PROJECT / ".runtime"
COMMIT = "a" * 40
NEXT_COMMIT = "b" * 40
ADDED = frozenset({
    "tech_farmer/storage.py", "tech_farmer/source_setup.py", "tests/test_storage.py",
    "tests/test_v03_workflow.py", "tests/test_source_setup.py", "tools/update.py",
    "tests/test_updater.py", "UPDATE-WINDOWS.cmd",
})
REAL_PUBLIC_FETCH = update.public_fetch
WHEEL_PLAN = json.dumps({"install": [{"metadata": {"version": "1.0.0"}, "download_info": {"url": "https://fixture.invalid/fixture-1.0.0-py3-none-any.whl"}}]}).encode()
BASE_REQUIREMENTS = (b"discord.py==2.6.4\naiohttp>=3.12.15,<4\n"
                     b"Pillow==12.3.0\nbeautifulsoup4==4.15.0\n")


def old_files():
    files = {name: ("# Self-authored fixture: " + name + "\n").encode("utf-8")
             for name in update.MANAGED_FILES - ADDED}
    files["tech_farmer/__init__.py"] = b'__version__ = "0.2.0"\n'
    files["requirements.txt"] = BASE_REQUIREMENTS
    return files


def new_files():
    files = old_files()
    files.update({name: ("# New self-authored fixture: " + name + "\n").encode("utf-8") for name in ADDED})
    files["tech_farmer/__init__.py"] = b'__version__ = "0.3.0"\n'
    files["tech_farmer/bot.py"] += b"# Reviewed fixture update, never executed.\n"
    return files


def manifest_for(files):
    return "".join(update._hash(raw) + "  " + name + "\n" for name, raw in sorted(files.items())).encode("utf-8")


def archive_for(files, *, commit=COMMIT, manifest=None, extra=(), omit=(), change=None):
    output = io.BytesIO()
    prefix = "TechFarmer-" + commit + "/"
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(prefix, b"")
            payloads = dict(files)
            payloads[update.MANIFEST] = manifest if manifest is not None else manifest_for(files)
            payloads.update(change or {})
            for name, raw in payloads.items():
                if name not in omit:
                    archive.writestr(prefix + name, raw)
            for name, raw in extra:
                archive.writestr(name, raw)
    return output.getvalue()


class FixtureTransport:
    """A local dictionary, not an HTTP client. main can move after its first read."""
    def __init__(self, files=None, *, archive=None, manifest=None, main=None, contents=None):
        self.files = new_files() if files is None else files
        self.manifest = manifest_for(self.files) if manifest is None else manifest
        self.archive = archive_for(self.files, manifest=self.manifest) if archive is None else archive
        self.main = main
        self.contents = contents
        self.calls = []
        self.main_reads = 0

    def __call__(self, url, *, max_bytes, timeout):
        self.calls.append((url, max_bytes, timeout))
        if url == update.MAIN_URL:
            self.main_reads += 1
            if self.main is not None:
                return self.main
            return update._json_bytes({"ref": "refs/heads/main", "object": {
                "type": "commit", "sha": COMMIT if self.main_reads == 1 else NEXT_COMMIT}})
        if url == update.API_ROOT + "/contents/" + update.MANIFEST + "?ref=" + COMMIT:
            if self.contents is not None:
                return self.contents
            return update._json_bytes({"type": "file", "path": update.MANIFEST,
                                       "encoding": "base64", "size": len(self.manifest),
                                       "content": base64.b64encode(self.manifest).decode("ascii")})
        if url == "https://codeload.github.com/ViaticNovva/TechFarmer/zip/" + COMMIT:
            return self.archive
        raise AssertionError("Unexpected URL: " + url)


def install(root, files=None):
    files = old_files() if files is None else files
    for name, raw in {**files, update.MANIFEST: manifest_for(files)}.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)


def payload_tree(root):
    return {path.relative_to(root).as_posix(): path.read_bytes()
            for path in root.rglob("*") if path.is_file()
            and ".runtime" not in path.relative_to(root).parts}


def remove_fixture_tree(path):
    # Path-based cleanup also works on workspace filesystems lacking dir_fd unlink.
    try:
        info = path.lstat()
    except FileNotFoundError:
        return
    if stat.S_ISDIR(info.st_mode):
        for attempt in range(4):
            try:
                for child in path.iterdir():
                    remove_fixture_tree(child)
                path.rmdir()
                return
            except FileNotFoundError:
                return
            except OSError as exc:
                if exc.errno != 39 or attempt == 3:
                    raise
    else:
        try:
            path.unlink()
        except FileNotFoundError:
            pass


class UpdaterTests(unittest.TestCase):
    def setUp(self):
        self.workspace = FIXTURE_BASE / ("updater-test-" + uuid4().hex)
        self.root = self.workspace / "Tech Farmer Å 日本 !"
        self.root.mkdir(parents=True)
        self.addCleanup(remove_fixture_tree, self.workspace)
        patcher = mock.patch.object(update, "public_fetch", side_effect=AssertionError("Live network forbidden in updater tests"))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_new_directory_entries_are_synced_for_recovery_durability(self):
        with mock.patch.object(update, "_sync_dir") as sync:
            update._mkdir(self.root, ".runtime/update/fixture")
        self.assertEqual(sync.call_args_list, [mock.call(self.root), mock.call(self.root / ".runtime"),
                                               mock.call(self.root / ".runtime/update")])
        with mock.patch.object(update, "_sync_dir") as sync:
            update._mkdir(self.root, ".runtime/update/fixture")
        sync.assert_not_called()

    def test_v02_fixture_has_original_36_payloads(self):
        self.assertEqual(len(old_files()), 36)
        self.assertEqual(len(update.parse_manifest(manifest_for(old_files()), incoming=False)), 36)

    def test_complete_pinned_snapshot_and_main_ref_race(self):
        transport = FixtureTransport()
        snapshot = update.download_snapshot(transport)
        self.assertEqual(snapshot.commit, COMMIT)
        self.assertEqual(snapshot.files[update.MANIFEST], transport.manifest)
        self.assertEqual(transport.main_reads, 1)
        self.assertEqual(len(transport.calls), 3)
        self.assertTrue(all(NEXT_COMMIT not in call[0] for call in transport.calls))
        self.assertIn("?ref=" + COMMIT, transport.calls[1][0])
        self.assertTrue(transport.calls[2][0].endswith("/" + COMMIT))

    def test_partial_document_only_repository_is_refused(self):
        install(self.root)
        before = payload_tree(self.root)
        transport = FixtureTransport(files={"README.md": b"Fixture docs only.\n"})
        with self.assertRaisesRegex(update.UpdateError, "Incomplete source"):
            update.apply_update(self.root, confirm_stopped=True, transport=transport)
        self.assertEqual(payload_tree(self.root), before)
        self.assertEqual(len(transport.calls), 2)
        self.assertFalse((self.root / update.JOURNAL).exists())

    def test_absent_or_private_manifest_is_not_installable(self):
        transport = FixtureTransport(contents=b'{"message":"Not Found"}')
        with self.assertRaisesRegex(update.UpdateError, "documentation-only/private/incomplete"):
            update.download_snapshot(transport)
        self.assertEqual(len(transport.calls), 2)

    def test_malformed_metadata_json_utf8_and_commit(self):
        malformed = [b"\xff", b"{", b"[]", b"null", b'{"x":NaN}',
                     b'{"ref":"refs/heads/main","ref":"duplicate"}',
                     update._json_bytes({"ref": "refs/heads/main", "object": {"type": "tag", "sha": COMMIT}}),
                     update._json_bytes({"ref": "refs/heads/main", "object": {"type": "commit", "sha": "main"}})]
        for raw in malformed:
            with self.subTest(raw=raw):
                with self.assertRaises(update.UpdateError):
                    update.download_snapshot(FixtureTransport(main=raw))

    def test_malformed_manifest_metadata(self):
        good = {"type": "file", "path": update.MANIFEST, "encoding": "base64", "size": 1, "content": "@@@"}
        variants = [b"\xff", b"[]", update._json_bytes(good)]
        for key, value in [("size", True), ("size", update.MAX_MANIFEST + 1), ("path", ".env"),
                           ("encoding", "none"), ("type", "symlink"), ("content", "YQ==")]:
            changed = dict(good)
            changed[key] = value
            if key == "content":
                changed["size"] = 2
            variants.append(update._json_bytes(changed))
        for raw in variants:
            with self.subTest(raw=raw):
                with self.assertRaises(update.UpdateError):
                    update.download_snapshot(FixtureTransport(contents=raw))

    def test_malformed_manifest_and_protected_paths(self):
        good = manifest_for(new_files())
        variants = [b"", b"\xff\n", good.replace(b"\n", b"\r\n"), good[:-1], b"\xef\xbb\xbf" + good,
                    good + good.splitlines(keepends=True)[0], good + b"malformed\n"]
        protected = [".env", ".env.example", "tech-farmer-settings.json", "source-profiles.json",
                     "data/jobs.sqlite", "references/picture.png", "exports/a.json", ".cache/models/a.bin",
                     ".venv/pyvenv.cfg", update.STATE, "tech_farmer/unknown.py", "../README.md"]
        variants.extend(good + ("0" * 64 + "  " + name + "\n").encode() for name in protected)
        for raw in variants:
            with self.subTest(raw=raw[-140:]):
                with self.assertRaises(update.UpdateError):
                    update.parse_manifest(raw)

    def test_empty_or_whitespace_required_source_is_incomplete_even_with_matching_hashes(self):
        for payload in [b"", b"\n", b" \t\n"]:
            with self.subTest(payload=payload):
                files = {name: payload for name in update.REQUIRED_FILES}
                with self.assertRaisesRegex(update.UpdateError, "nonempty"):
                    update.verify_archive(archive_for(files), COMMIT, manifest_for(files))
        files = new_files()
        files["tests/__init__.py"] = b""
        with self.assertRaisesRegex(update.UpdateError, "nonempty"):
            update.verify_archive(archive_for(files), COMMIT, manifest_for(files))

    def test_missing_all_runtime_setup_and_updater_entrypoints(self):
        for name in sorted(update.REQUIRED_FILES):
            with self.subTest(name=name):
                files = new_files()
                del files[name]
                with self.assertRaisesRegex(update.UpdateError, "Incomplete source"):
                    update.parse_manifest(manifest_for(files))

    def test_zip_traversal_absolute_backslash_drive_and_unc(self):
        files = new_files()
        prefix = "TechFarmer-" + COMMIT + "/"
        names = ["../escape.py", "/absolute.py", "C:/escape.py", "C:\\escape.py", "\\\\server\\file.py",
                 prefix + "../data/secret", prefix + "tools\\bad.py", prefix + "tools//bad.py",
                 prefix + "./README.md", prefix + "tools/bad. /x.py"]
        for name in names:
            with self.subTest(name=name):
                raw = archive_for(files, extra=[(name, b"fixture\n")])
                with self.assertRaises(update.UpdateError):
                    update.verify_archive(raw, COMMIT, manifest_for(files))

    def test_zip_nul_name_is_checked_before_zipfile_truncation(self):
        files = new_files()
        name = "TechFarmer-" + COMMIT + "/evilXname.py"
        raw = archive_for(files, extra=[(name, b"fixture\n")])
        raw = raw.replace(b"evilXname.py", b"evil\x00name.py")
        with self.assertRaisesRegex(update.UpdateError, "Unsafe ZIP path"):
            update.verify_archive(raw, COMMIT, manifest_for(files))

    def test_zip_duplicate_and_case_collision(self):
        files = new_files()
        prefix = "TechFarmer-" + COMMIT + "/"
        for name in ["README.md", "readme.MD"]:
            with self.subTest(name=name):
                raw = archive_for(files, extra=[(prefix + name, b"duplicate\n")])
                with self.assertRaisesRegex(update.UpdateError, "Duplicate or case-colliding"):
                    update.verify_archive(raw, COMMIT, manifest_for(files))

    def test_zip_symlink_and_special_files(self):
        files = new_files()
        for kind in [stat.S_IFLNK, stat.S_IFIFO, stat.S_IFSOCK]:
            info = zipfile.ZipInfo("TechFarmer-" + COMMIT + "/tools/link.py")
            info.create_system = 3
            info.external_attr = (kind | 0o777) << 16
            raw = archive_for(files, extra=[(info, b"../../private\n")])
            with self.subTest(kind=kind):
                with self.assertRaisesRegex(update.UpdateError, "links, special"):
                    update.verify_archive(raw, COMMIT, manifest_for(files))

    def test_zip_bomb_member_total_count_and_download_bounds(self):
        files = new_files()
        files["tech_farmer/bot.py"] = b"a" * 65536 + b"\n"
        with self.assertRaisesRegex(update.UpdateError, "expansion/size"):
            update.verify_archive(archive_for(files), COMMIT, manifest_for(files))
        regular = new_files()
        raw = archive_for(regular)
        for constant, value in [("MAX_FILE", 20), ("MAX_EXPANDED", 100), ("MAX_ENTRIES", 3), ("MAX_ARCHIVE", 20)]:
            with self.subTest(constant=constant), mock.patch.object(update, constant, value):
                with self.assertRaises(update.UpdateError):
                    update.verify_archive(raw, COMMIT, manifest_for(regular))
        with self.assertRaises(update.UpdateError):
            update._fetch(update.MAIN_URL, 3, lambda *a, **k: b"1234")

    def test_wrong_hash_missing_payload_and_disagreeing_manifest(self):
        files, manifest = new_files(), manifest_for(new_files())
        cases = [archive_for(files, change={"tech_farmer/bot.py": b"bad content\n"}),
                 archive_for(files, omit=["tech_farmer/bot.py"]),
                 archive_for(files, omit=[update.MANIFEST]),
                 archive_for(files, change={update.MANIFEST: manifest + b"\n"}),
                 archive_for(files, commit=NEXT_COMMIT), b"not a zip"]
        for raw in cases:
            with self.subTest(size=len(raw)):
                with self.assertRaises(update.UpdateError):
                    update.verify_archive(raw, COMMIT, manifest)

    def test_archive_unmanifested_data_empty_directories_and_binary_source_refused(self):
        files = new_files()
        prefix = "TechFarmer-" + COMMIT + "/"
        for name in [".env", "data/", "unknown.py", ".cache/models/"]:
            with self.subTest(name=name):
                with self.assertRaises(update.UpdateError):
                    update.verify_archive(archive_for(files, extra=[(prefix + name, b"")]), COMMIT, manifest_for(files))
        files["tech_farmer/bot.py"] = b"binary\x00\n"
        with self.assertRaises(update.UpdateError):
            update.verify_archive(archive_for(files), COMMIT, manifest_for(files))

    def test_exact_hosts_resources_and_redirects_only(self):
        good = "https://github.com/ViaticNovva/TechFarmer/archive/" + COMMIT + ".zip"
        target = "https://codeload.github.com/ViaticNovva/TechFarmer/zip/" + COMMIT
        redirect = update.CanonicalRedirectHandler()
        self.assertEqual(redirect.redirect_request(Request(good), None, 302, "", {}, target).full_url, target)
        bad = ["http://api.github.com/repos/ViaticNovva/TechFarmer/git/ref/heads/main",
               "https://api.github.com.evil.invalid/", "https://api.github.com./", "https://user:pass@api.github.com/",
               "https://api.github.com:444/", "https://raw.githubusercontent.com/ViaticNovva/TechFarmer/main/README.md",
               "https://github.com/other/TechFarmer/archive/" + COMMIT + ".zip", target + "?token=fake",
               target.replace(COMMIT, "main"), target + "#fragment", "file:///private/source.zip"]
        for url in bad:
            with self.subTest(url=url):
                with self.assertRaises(update.UpdateError):
                    update._resource(url)
                with self.assertRaises(update.UpdateError):
                    redirect.redirect_request(Request(good), None, 302, "", {}, url)
        with self.assertRaises(update.UpdateError):
            redirect.redirect_request(Request(target), None, 302, "", {}, target.replace(COMMIT, NEXT_COMMIT))

    def test_public_transport_tls_no_proxy_auth_and_bounded_reads(self):
        class Response:
            status = 200
            def __init__(self, raw, headers=None):
                self.data = io.BytesIO(raw)
                self.headers = headers or {"Content-Length": str(len(raw))}
            def geturl(self):
                return update.MAIN_URL
            def read1(self, size):
                return self.data.read(size)
            def __enter__(self):
                return self
            def __exit__(self, *_):
                return False

        opener = mock.Mock()
        opener.open.return_value = Response(b"fixture")
        with mock.patch.object(update, "build_opener", return_value=opener) as build:
            self.assertEqual(REAL_PUBLIC_FETCH(update.MAIN_URL, max_bytes=20, timeout=4), b"fixture")
        self.assertEqual(build.call_args.args[0].proxies, {})
        request = opener.open.call_args.args[0]
        self.assertNotIn("Authorization", request.headers)
        self.assertNotIn("Cookie", request.headers)
        self.assertEqual(opener.open.call_args.kwargs["timeout"], 4)
        for response in [Response(b"123", {"Content-Length": "200"}),
                         Response(b"123", {"Content-Length": "4"}),
                         Response(b"123", {"Content-Encoding": "gzip"}),
                         Response(b"123456", {"Other": "no length"})]:
            opener.open.return_value = response
            with self.subTest(headers=response.headers), mock.patch.object(update, "build_opener", return_value=opener):
                with self.assertRaises(update.UpdateError):
                    REAL_PUBLIC_FETCH(update.MAIN_URL, max_bytes=5, timeout=4)
        opener.open.return_value = Response(b"fixture")
        with mock.patch.object(update, "build_opener", return_value=opener), mock.patch.object(update.time, "monotonic", side_effect=[0, 100]):
            with self.assertRaisesRegex(update.UpdateError, "timed out"):
                REAL_PUBLIC_FETCH(update.MAIN_URL, max_bytes=20, timeout=4)

    def test_check_only_has_no_writes_or_archive_requests(self):
        install(self.root)
        transport = FixtureTransport()
        before = payload_tree(self.root)
        timestamps = {path: path.stat().st_mtime_ns for path in self.root.rglob("*")}
        result = update.check_update(self.root, transport=transport)
        self.assertEqual(result["status"], "unverified_local")
        self.assertEqual(result["local_version"], "0.2.0")
        self.assertEqual(result["remote_commit"], COMMIT)
        self.assertEqual(set(result), {"status", "local_version", "local_commit", "remote_commit", "message"})
        self.assertEqual(len(transport.calls), 1)
        self.assertEqual(transport.calls[0][0], update.MAIN_URL)
        self.assertFalse((self.root / ".runtime").exists())
        self.assertEqual(payload_tree(self.root), before)
        self.assertEqual({path: path.stat().st_mtime_ns for path in self.root.rglob("*")}, timestamps)

    def test_check_available_while_bot_lock_held_and_errors_are_safe(self):
        install(self.root)
        with update.RuntimeLock(self.root):
            result = update.check_update(self.root, transport=FixtureTransport())
            self.assertEqual(result["status"], "unverified_local")
        failure = mock.Mock(side_effect=OSError("SECRET_DO_NOT_ECHO"))
        result = update.check_update(self.root, transport=failure)
        self.assertEqual(result["status"], "unavailable")
        self.assertNotIn("SECRET", str(result))

    def test_update_preserves_private_data_unknown_files_and_does_not_extractall(self):
        install(self.root)
        private = {"tech-farmer-settings.json": b' {"version":2,"owner_id":"synthetic"}\r\n',
                   "source-profiles.json": b'{"fixtures": []}\n', ".env": b"SELF_AUTHORED_NOT_A_TOKEN=fixture\n",
                   "data/jobs.sqlite": b"fixture-database\x00\xff", "data/references/a.png": b"fixture-picture",
                   "exports/result.zip": b"fixture-export", ".cache/models/a.bin": b"fixture-model-cache",
                   ".venv/pyvenv.cfg": b"fixture-venv\r\n", "unknown.py": b"# local unknown\n",
                   "tools/my-notes.txt": b"private notes\n"}
        for name, raw in private.items():
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
        with mock.patch.object(zipfile.ZipFile, "extractall", side_effect=AssertionError("extractall forbidden")), mock.patch.object(update.subprocess, "run", side_effect=AssertionError("No dependency/model process by default")):
            result = update.apply_update(self.root, confirm_stopped=True, transport=FixtureTransport())
        self.assertEqual(result["status"], "updated")
        for name, raw in private.items():
            self.assertEqual((self.root / name).read_bytes(), raw, name)
        self.assertEqual(update._load_journal(self.root)["phase"], "committed")
        self.assertEqual(update.check_update(self.root, transport=FixtureTransport())["status"], "up_to_date")
        with update.RuntimeLock(self.root):
            pass

    def test_local_managed_edits_and_missing_files_are_refused_before_network(self):
        install(self.root)
        (self.root / "tech_farmer/bot.py").write_bytes(b"# user's local source edits\n")
        transport = FixtureTransport()
        with self.assertRaisesRegex(update.UpdateError, "NOT be overwritten"):
            update.apply_update(self.root, confirm_stopped=True, transport=transport)
        self.assertEqual(transport.calls, [])
        (self.root / "tech_farmer/bot.py").unlink()
        with self.assertRaises(update.UpdateError):
            update.apply_update(self.root, confirm_stopped=True, transport=transport)
        self.assertEqual(transport.calls, [])

    def test_untracked_incoming_collision_is_preserved(self):
        install(self.root)
        path = self.root / "tech_farmer/storage.py"
        path.write_bytes(b"# unknown private file\n")
        before = payload_tree(self.root)
        with self.assertRaisesRegex(update.UpdateError, "untracked local file"):
            update.apply_update(self.root, confirm_stopped=True, transport=FixtureTransport())
        self.assertEqual(payload_tree(self.root), before)

    def test_first_upgrade_requires_operator_stop_ack_but_receipted_repeat_does_not(self):
        install(self.root)
        before = payload_tree(self.root)
        transport = FixtureTransport()
        with self.assertRaisesRegex(update.UpdateError, "FIRST UPGRADE"):
            update.apply_update(self.root, transport=transport)
        self.assertEqual(transport.calls, [])
        self.assertEqual(payload_tree(self.root), before)
        self.assertEqual(update.apply_update(self.root, confirm_stopped=True, transport=FixtureTransport())["status"], "updated")
        self.assertEqual(update.apply_update(self.root, transport=FixtureTransport())["status"], "up_to_date")

    def test_first_time_two_file_bootstrap_is_adopted_only_if_exact(self):
        install(self.root)
        for name in update.BOOTSTRAP_FILES:
            (self.root / name).write_bytes(new_files()[name])
        before = payload_tree(self.root)
        self.assertEqual(update.apply_update(self.root, confirm_stopped=True, transport=FixtureTransport())["status"], "updated")
        update.rollback_update(self.root)
        self.assertEqual(payload_tree(self.root), before)
        (self.root / "tools/update.py").write_bytes(b"# different unreviewed updater\n")
        with self.assertRaisesRegex(update.UpdateError, "untracked local file"):
            update.apply_update(self.root, confirm_stopped=True, transport=FixtureTransport())

    def test_removed_managed_source_is_backed_up_and_rollback_restores_it(self):
        install(self.root)
        before = payload_tree(self.root)
        files = new_files()
        del files["tools/demo.py"]
        update.apply_update(self.root, confirm_stopped=True, transport=FixtureTransport(files))
        self.assertFalse((self.root / "tools/demo.py").exists())
        journal = update._load_journal(self.root)
        backup = self.root / update._backup_name(journal, "tools/demo.py")
        self.assertEqual(backup.read_bytes(), before["tools/demo.py"])
        self.assertEqual((self.root / update._backup_name(journal, update.MANIFEST)).read_bytes(), before[update.MANIFEST])
        update.rollback_update(self.root)
        self.assertEqual(payload_tree(self.root), before)

    def test_active_os_lock_refuses_update_before_network_and_stale_pid_does_not(self):
        install(self.root)
        transport = FixtureTransport()
        with update.RuntimeLock(self.root):
            with self.assertRaises(update.RuntimeBusyError):
                update.apply_update(self.root, confirm_stopped=True, transport=transport)
        self.assertEqual(transport.calls, [])
        (self.root / update.LOCK).write_bytes(b"999999999999\n")
        self.assertEqual(update.apply_update(self.root, confirm_stopped=True, transport=FixtureTransport())["status"], "updated")

    def test_separate_process_lock_is_exclusive(self):
        install(self.root)
        code = ("import sys; sys.path.insert(0, sys.argv[1]); from tools.update import RuntimeLock; "
                "lock=RuntimeLock(sys.argv[2]); lock.__enter__(); print('locked',flush=True); "
                "sys.stdin.readline(); lock.__exit__(None,None,None)")
        process = subprocess.Popen([sys.executable, "-B", "-I", "-c", code, str(PROJECT), str(self.root)],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            self.assertEqual(process.stdout.readline().strip(), "locked")
            with self.assertRaises(update.RuntimeBusyError):
                with update.RuntimeLock(self.root):
                    pass
            process.communicate("\n", timeout=15)
            self.assertEqual(process.returncode, 0)
        finally:
            if process.poll() is None:
                process.terminate()
                process.communicate(timeout=15)
        with update.RuntimeLock(self.root):
            pass

    def test_staging_copy_failure_never_changes_source(self):
        install(self.root)
        before = payload_tree(self.root)
        original = update._write_new
        def fail(root, name, raw):
            if "/stage/" in name and name.endswith("tech_farmer/bot.py"):
                raise OSError("injected copy failure")
            return original(root, name, raw)
        with mock.patch.object(update, "_write_new", side_effect=fail):
            with self.assertRaises((update.UpdateError, OSError)):
                update.apply_update(self.root, confirm_stopped=True, transport=FixtureTransport())
        self.assertEqual(payload_tree(self.root), before)
        self.assertFalse((self.root / update.JOURNAL).exists())

    def test_atomic_replace_failure_before_and_after_is_rolled_back(self):
        install(self.root)
        before = payload_tree(self.root)
        original = update._replace_file
        for after in [False, True]:
            failed = [False]
            def fail(root, source, target):
                if "/stage/" in source and target == "tech_farmer/bot.py" and not failed[0]:
                    failed[0] = True
                    if after:
                        original(root, source, target)
                    raise OSError("injected replacement failure")
                return original(root, source, target)
            with self.subTest(after=after), mock.patch.object(update, "_replace_file", side_effect=fail):
                with self.assertRaisesRegex(update.UpdateError, "were restored"):
                    update.apply_update(self.root, confirm_stopped=True, transport=FixtureTransport())
            self.assertEqual(payload_tree(self.root), before)
            self.assertEqual(update._load_journal(self.root)["phase"], "rolled_back")

    def test_final_verification_failure_rolls_back_source_and_manifest(self):
        install(self.root)
        before = payload_tree(self.root)
        with mock.patch.object(update, "_verify_installed", side_effect=update.UpdateError("injected final check failure")):
            with self.assertRaisesRegex(update.UpdateError, "were restored"):
                update.apply_update(self.root, confirm_stopped=True, transport=FixtureTransport())
        self.assertEqual(payload_tree(self.root), before)
        self.assertFalse((self.root / update.STATE).exists())

    def test_cancelled_update_rolls_back_and_can_be_retried(self):
        install(self.root)
        before = payload_tree(self.root)
        original, once = update._replace_file, [False]
        def cancel(root, source, target):
            if "/stage/" in source and not once[0]:
                once[0] = True
                raise KeyboardInterrupt
            return original(root, source, target)
        with mock.patch.object(update, "_replace_file", side_effect=cancel):
            result = update.apply_update(self.root, confirm_stopped=True, transport=FixtureTransport())
        self.assertEqual(result["status"], "cancelled")
        self.assertEqual(payload_tree(self.root), before)
        self.assertEqual(update.apply_update(self.root, confirm_stopped=True, transport=FixtureTransport())["status"], "updated")

    def test_real_process_crash_is_recovered_without_network_and_bot_cannot_start_mid_update(self):
        install(self.root)
        before = payload_tree(self.root)
        code = """
import os, sys
sys.path.insert(0, sys.argv[1])
from tools import update
from tests.test_updater import FixtureTransport
original = update._replace_file
def crash(root, source, target):
    original(root, source, target)
    if '/stage/' in source:
        os._exit(73)
update._replace_file = crash
update.apply_update(sys.argv[2], confirm_stopped=True, transport=FixtureTransport())
"""
        result = subprocess.run([sys.executable, "-B", "-I", "-c", code, str(PROJECT), str(self.root)],
                                capture_output=True, timeout=120, check=False)
        self.assertEqual(result.returncode, 73, result.stderr.decode())
        self.assertEqual(update._load_journal(self.root)["phase"], "applying")
        with self.assertRaisesRegex(update.UpdateError, "interrupted source update"):
            with update.RuntimeLock(self.root):
                pass
        transport = mock.Mock(side_effect=AssertionError("Recovery must not fetch"))
        self.assertEqual(update.apply_update(self.root, confirm_stopped=True, transport=transport)["status"], "recovered")
        self.assertEqual(payload_tree(self.root), before)
        transport.assert_not_called()
        with update.RuntimeLock(self.root):
            pass

    def test_failed_automatic_recovery_retains_journal_for_explicit_retry(self):
        install(self.root)
        before = payload_tree(self.root)
        with mock.patch.object(update, "_verify_installed", side_effect=update.UpdateError("injected verify failure")), mock.patch.object(update, "_restore", side_effect=OSError("injected restore failure")):
            with self.assertRaisesRegex(update.UpdateError, "recovery needs attention"):
                update.apply_update(self.root, confirm_stopped=True, transport=FixtureTransport())
        self.assertEqual(update._load_journal(self.root)["phase"], "applying")
        update.rollback_update(self.root)
        self.assertEqual(payload_tree(self.root), before)

    def test_repeated_update_and_rollback_and_self_update(self):
        install(self.root)
        first = update.apply_update(self.root, confirm_stopped=True, transport=FixtureTransport())
        self.assertEqual(first["status"], "updated")
        source = (self.root / "tools/update.py").read_bytes()
        self.assertEqual(source, new_files()["tools/update.py"])
        transaction = update._load_journal(self.root)["transaction"]
        timestamps = {name: (self.root / name).stat().st_mtime_ns for name in new_files()}
        self.assertEqual(update.apply_update(self.root, confirm_stopped=True, transport=FixtureTransport())["status"], "up_to_date")
        self.assertEqual(update._load_journal(self.root)["transaction"], transaction)
        self.assertEqual({name: (self.root / name).stat().st_mtime_ns for name in new_files()}, timestamps)
        next_files = new_files()
        next_files["tools/update.py"] += b"# Self update fixture revision two.\n"
        update.apply_update(self.root, confirm_stopped=True, transport=FixtureTransport(next_files))
        self.assertEqual((self.root / "tools/update.py").read_bytes(), next_files["tools/update.py"])
        update.rollback_update(self.root)
        self.assertEqual((self.root / "tools/update.py").read_bytes(), source)
        self.assertIn("already rolled back", update.rollback_update(self.root)["message"])

    def test_rollback_preserves_later_edits_and_detects_damaged_backup(self):
        install(self.root)
        update.apply_update(self.root, confirm_stopped=True, transport=FixtureTransport())
        bot = self.root / "tech_farmer/bot.py"
        bot.write_bytes(b"# Local edit made after update.\n")
        with self.assertRaisesRegex(update.UpdateError, "later local edit"):
            update.rollback_update(self.root)
        self.assertEqual(bot.read_bytes(), b"# Local edit made after update.\n")
        bot.write_bytes(new_files()["tech_farmer/bot.py"])
        journal = update._load_journal(self.root)
        backup = self.root / update._backup_name(journal, "tech_farmer/bot.py")
        backup.write_bytes(b"# Damaged backup.\n")
        with self.assertRaisesRegex(update.UpdateError, "Backup integrity"):
            update.rollback_update(self.root)
        self.assertEqual(bot.read_bytes(), new_files()["tech_farmer/bot.py"])

    def test_malformed_cross_root_or_path_injected_journal_refused(self):
        install(self.root)
        update.apply_update(self.root, confirm_stopped=True, transport=FixtureTransport())
        original = update._load_journal(self.root)
        variants = []
        for key, value in [("root", str(self.workspace)), ("transaction", "../elsewhere"),
                           ("phase", []), ("phase", "unknown"), ("schema", True),
                           ("created_dirs", ["data"]), ("created_dirs", [{}])]:
            changed = dict(original)
            changed[key] = value
            variants.append(changed)
        changed = json.loads(json.dumps(original))
        changed["before"]["tech-farmer-settings.json"] = "0" * 64
        variants.append(changed)
        before = payload_tree(self.root)
        for journal in variants:
            with self.subTest(journal=journal):
                (self.root / update.JOURNAL).write_bytes(update._json_bytes(journal))
                with self.assertRaises(update.UpdateError):
                    update.rollback_update(self.root)
                self.assertEqual(payload_tree(self.root), before)

    def test_symlink_root_runtime_parent_and_target_are_refused(self):
        if not hasattr(os, "symlink"):
            self.skipTest("symlinks unavailable")
        install(self.root)
        outside = self.workspace / "outside"
        outside.mkdir()
        external = outside / "private.txt"
        external.write_bytes(b"outside private fixture")
        link = self.root / "tech_farmer/storage.py"
        try:
            link.symlink_to(external)
        except OSError:
            self.skipTest("creating symlinks requires platform permission")
        with self.assertRaisesRegex(update.UpdateError, "Symlink"):
            update.apply_update(self.root, confirm_stopped=True, transport=FixtureTransport())
        self.assertEqual(external.read_bytes(), b"outside private fixture")
        link.unlink()
        alias = self.workspace / "alias"
        alias.symlink_to(self.root, target_is_directory=True)
        with self.assertRaisesRegex(update.UpdateError, "Symlink"):
            update.apply_update(alias, confirm_stopped=True, transport=FixtureTransport())
        remove_fixture_tree(self.root / ".runtime")
        (self.root / ".runtime").symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(update.UpdateError, "Symlink"):
            update.apply_update(self.root, confirm_stopped=True, transport=FixtureTransport())
        (self.root / ".runtime").unlink()
        tools = self.root / "tools"
        tools.rename(outside / "tools")
        tools.symlink_to(outside / "tools", target_is_directory=True)
        with self.assertRaisesRegex(update.UpdateError, "Symlink"):
            update.apply_update(self.root, confirm_stopped=True, transport=FixtureTransport())

    def test_hardlinked_managed_file_is_not_overwritten(self):
        install(self.root)
        outside = self.workspace / "hardlinked-bot.py"
        try:
            os.link(self.root / "tech_farmer/bot.py", outside)
        except OSError:
            self.skipTest("hard links unavailable")
        original = outside.read_bytes()
        with self.assertRaisesRegex(update.UpdateError, "Hard-linked"):
            update.apply_update(self.root, confirm_stopped=True, transport=FixtureTransport())
        self.assertEqual(outside.read_bytes(), original)

    def test_legacy_without_manifest_stages_sibling_and_preserves_every_original_byte(self):
        install(self.root)
        (self.root / update.MANIFEST).unlink()
        (self.root / "tech-farmer-settings.json").write_bytes(b"legacy config\r\n")
        before = payload_tree(self.root)
        result = update.apply_update(self.root, confirm_stopped=True, transport=FixtureTransport(), sync_base=True)
        self.assertEqual(result["status"], "legacy_staged")
        sibling = self.root.parent / (self.root.name + "-updated-" + COMMIT[:12])
        self.assertTrue((sibling / update.MANIFEST).is_file())
        self.assertFalse((sibling / "tech-farmer-settings.json").exists())
        self.assertEqual(payload_tree(self.root), before)
        with self.assertRaisesRegex(update.UpdateError, "already exists"):
            update.apply_update(self.root, confirm_stopped=True, transport=FixtureTransport())
        self.assertEqual(payload_tree(self.root), before)

    def test_partial_legacy_stage_never_replaces_old_source_or_becomes_runnable(self):
        install(self.root)
        (self.root / update.MANIFEST).unlink()
        before = payload_tree(self.root)
        original = update._write_new
        def fail(root, name, raw):
            if root != self.root and name == "tech_farmer/bot.py":
                raise OSError("injected legacy staging failure")
            return original(root, name, raw)
        with mock.patch.object(update, "_write_new", side_effect=fail):
            with self.assertRaisesRegex(update.UpdateError, "Legacy staging did not finish"):
                update.apply_update(self.root, confirm_stopped=True, transport=FixtureTransport())
        self.assertEqual(payload_tree(self.root), before)
        sibling = self.root.parent / (self.root.name + "-updated-" + COMMIT[:12])
        self.assertTrue((sibling / update.INCOMPLETE).exists())
        with self.assertRaisesRegex(update.UpdateError, "incomplete legacy staging"):
            with update.RuntimeLock(sibling):
                pass

    def test_light_requirements_cannot_request_heavy_packages_commands_urls_or_includes(self):
        update._light_requirements(BASE_REQUIREMENTS)
        forbidden = [b"torch==2.0\n", b"--extra-index-url https://example.invalid\n", b"-r other.txt\n",
                     b"discord.py @ https://example.invalid/pkg.whl\n", b"Pillow[extra]==12.3.0\n",
                     b"git+https://example.invalid/pkg\n", b"; arbitrary command\n"]
        for raw in forbidden:
            with self.subTest(raw=raw):
                with self.assertRaises(update.UpdateError):
                    update._light_requirements(BASE_REQUIREMENTS + raw)

    def test_binary_plan_rejects_nightly_yanked_source_builds_and_malformed_reports(self):
        self.assertTrue(update._stable_wheel_report(WHEEL_PLAN))
        base = json.loads(WHEEL_PLAN)
        for version in ("2.15.0.dev1", "2.15.0rc1", "3.0.0a1", "garbage", None):
            changed = json.loads(WHEEL_PLAN)
            changed["install"][0]["metadata"]["version"] = version
            self.assertFalse(update._stable_wheel_report(json.dumps(changed)))
        for key, value in (("is_yanked", True), ("download_info", {"url": "https://fixture.invalid/source.tar.gz"}),
                           ("download_info", {"url": "file:///fixture/wheel.whl", "dir_info": {}})):
            changed = json.loads(WHEEL_PLAN)
            changed["install"][0][key] = value
            self.assertFalse(update._stable_wheel_report(json.dumps(changed)))
        for raw in (None, b"", b"invalid", b"[]", b'{"install":[]}', b'{"install":[null]}'):
            self.assertFalse(update._stable_wheel_report(raw))
        base["install"][0]["metadata"]["version"] = "2.14.1+cpu"
        self.assertTrue(update._stable_wheel_report(json.dumps(base)))

    def test_dependency_failure_is_partial_source_stays_verified_and_venv_is_not_rolled_back(self):
        install(self.root)
        venv = self.root / ".venv"
        venv.mkdir()
        marker = venv / "private-state"
        marker.write_bytes(b"existing venv fixture")
        policy = {"PIP_INDEX_URL": "https://approved-index.fixture.invalid/simple",
                  "PIP_CONFIG_FILE": "/fixture-approved/pip.conf", "NETRC": "/fixture-approved/netrc",
                  "HTTPS_PROXY": "https://approved-proxy.fixture.invalid"}
        observed = {}
        def pip(*args, **kwargs):
            observed.update({key: os.environ.get(key) for key in policy})
            if "--dry-run" in args[0]:
                return subprocess.CompletedProcess(args[0], 0, WHEEL_PLAN)
            marker.write_bytes(b"partially changed dependency fixture")
            return subprocess.CompletedProcess(args[0], 1)
        with mock.patch.dict(os.environ, policy), mock.patch.object(update.sys, "prefix", str(venv)), mock.patch.object(update.sys, "base_prefix", "/fixture-base"), mock.patch.object(update.subprocess, "run", side_effect=pip) as run:
            result = update.apply_update(self.root, confirm_stopped=True, transport=FixtureTransport(), sync_base=True)
        self.assertEqual(result["status"], "partial")
        self.assertIn("NOT rolled back", result["message"])
        self.assertEqual(update._load_journal(self.root)["phase"], "committed")
        args, kwargs = run.call_args
        self.assertEqual(args[0], [sys.executable, "-m", "pip", "--disable-pip-version-check",
                                  "--no-input", "--require-virtualenv", "install", "--only-binary=:all:",
                                  "-r", str(self.root / "requirements.txt")])
        self.assertFalse(kwargs["shell"])
        self.assertNotIn("env", kwargs)
        self.assertEqual(observed, policy)
        self.assertEqual(kwargs["stdin"], subprocess.DEVNULL)
        self.assertEqual(kwargs["stdout"], subprocess.DEVNULL)
        self.assertEqual(kwargs["stderr"], subprocess.DEVNULL)
        self.assertNotIn("requirements-vision.txt", " ".join(args[0]))
        update.rollback_update(self.root)
        self.assertEqual(marker.read_bytes(), b"partially changed dependency fixture")

    def test_dependency_sync_requires_project_venv_and_success_is_explicit(self):
        install(self.root)
        with mock.patch.object(update.subprocess, "run", side_effect=AssertionError("No global pip")):
            result = update.apply_update(self.root, confirm_stopped=True, transport=FixtureTransport(), sync_base=True)
        self.assertEqual(result["status"], "partial")
        self.assertIn("project's .venv", result["message"])
        venv = self.root / ".venv"
        venv.mkdir()
        with mock.patch.object(update.sys, "prefix", str(venv)), mock.patch.object(update.sys, "base_prefix", "/fixture-base"), mock.patch.object(update.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, WHEEL_PLAN)) as run:
            result = update.apply_update(self.root, confirm_stopped=True, transport=FixtureTransport(), sync_base=True)
        self.assertEqual(result["status"], "updated")
        self.assertEqual(run.call_count, 4)
        self.assertIn("--dry-run", run.call_args_list[0].args[0])
        self.assertIn("--only-binary=:all:", run.call_args_list[0].args[0])
        self.assertEqual(run.call_args_list[2].args[0][-1], "check")
        self.assertEqual(run.call_args_list[3].args[0], [sys.executable, "-I", "-X", "utf8", str(self.root / "tech_farmer" / "__main__.py"), "preflight"])
        self.assertEqual(json.loads((self.root / (update.RUNTIME + "/dependencies.json")).read_text())["status"], "complete")

    def test_sync_preflight_forces_utf8_for_redirected_unicode_path_output(self):
        files = new_files()
        install(self.root, files)
        venv = self.root / ".venv"
        venv.mkdir()
        snapshot = update.download_snapshot(FixtureTransport(files))
        actual_run, observed = subprocess.run, []
        def process(arguments, **kwargs):
            if arguments[-1] == "preflight":
                self.assertEqual(arguments[1:4], ["-I", "-X", "utf8"])
                code = "import sys; print(sys.argv[1]); raise SystemExit(0 if sys.flags.utf8_mode == 1 else 2)"
                environment = {**os.environ, "PYTHONIOENCODING": "cp1252", "PYTHONUTF8": "0"}
                result = actual_run([*arguments[:-2], "-c", code, str(self.root)], cwd=self.root,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL,
                                    env=environment, shell=False, timeout=30, check=False)
                observed.extend(result.stdout.decode("utf-8").splitlines())
                return result
            return subprocess.CompletedProcess(arguments, 0, WHEEL_PLAN)
        with mock.patch.object(update.sys, "prefix", str(venv)), mock.patch.object(update.sys, "base_prefix", "/fixture-base"), mock.patch.object(update.subprocess, "run", side_effect=process):
            self.assertEqual(update._sync_base(self.root, snapshot)["status"], "updated")
        self.assertEqual(observed, [str(self.root)])

    def test_v04_reuses_the_exact_frozen_v03_path_contract(self):
        path_hash = update._hash(("\n".join(sorted(update.MANAGED_FILES)) + "\n").encode())
        self.assertEqual(path_hash, "aa36b80b1c89b904b8f9be166e4fd3bd8baa501e1e8e77bb44799f3b04ebaf25")
        self.assertEqual(len(update.MANAGED_FILES), 44)
        self.assertTrue({"START-WINDOWS.cmd", "INSTALL-WINDOWS.cmd", "START-HERE.html"} <= update.BASE_REQUIRED)
        update._light_requirements((PROJECT / "requirements.txt").read_bytes())

    def test_v03_to_v04_source_update_and_rollback_preserve_private_files_and_venv(self):
        previous = new_files()
        self.assertIn(b'"0.3.0"', previous["tech_farmer/__init__.py"])
        install(self.root, previous)
        private = {"tech-farmer-settings.json": b"nonsecret prior settings\n", "data/fixture.sqlite": b"keep database bytes",
                   "exports/native.png": b"keep native output fixture", ".cache/models/keep.fixture": b"keep cache",
                   ".venv/keep.fixture": b"keep existing interpreter", "tech-farmer-settings.json.backup-fixture": b"keep old backup"}
        for name, raw in private.items():
            target = self.root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(raw)
        incoming = dict(previous)
        incoming["tech_farmer/__init__.py"] = b'__version__ = "0.4.0"\n'
        incoming["requirements.txt"] = (PROJECT / "requirements.txt").read_bytes()
        with mock.patch.object(update.subprocess, "run", side_effect=AssertionError("Source-only upgrade must not install packages")):
            result = update.apply_update(self.root, confirm_stopped=True, transport=FixtureTransport(incoming))
        self.assertEqual(result["status"], "updated")
        self.assertEqual((self.root / "tech_farmer/__init__.py").read_bytes(), incoming["tech_farmer/__init__.py"])
        self.assertEqual((self.root / update.MANIFEST).read_bytes(), manifest_for(incoming))
        for name, raw in private.items():
            self.assertEqual((self.root / name).read_bytes(), raw)
        update.rollback_update(self.root)
        self.assertEqual((self.root / update.MANIFEST).read_bytes(), manifest_for(previous))
        for name, raw in {**previous, **private}.items():
            self.assertEqual((self.root / name).read_bytes(), raw)

    def test_successful_pip_but_failed_actual_imports_is_partial_not_complete(self):
        files = new_files()
        install(self.root, files)
        venv = self.root / ".venv"
        venv.mkdir()
        before = payload_tree(self.root)
        snapshot = update.download_snapshot(FixtureTransport(files))
        replies = [subprocess.CompletedProcess([], code, WHEEL_PLAN) for code in (0, 0, 0, 2)]
        with mock.patch.object(update.sys, "prefix", str(venv)), mock.patch.object(update.sys, "base_prefix", "/fixture-base"), mock.patch.object(update.subprocess, "run", side_effect=replies) as run:
            result = update._sync_base(self.root, snapshot)
        self.assertEqual(result["status"], "partial")
        self.assertIn("actual base package/import preflight failed", result["message"])
        self.assertEqual(run.call_count, 4)
        self.assertEqual(payload_tree(self.root), before)
        self.assertEqual(json.loads((self.root / (update.RUNTIME + "/dependencies.json")).read_text())["status"], "failed")

    def test_default_cancel_never_touches_files_or_network(self):
        with mock.patch("builtins.input", return_value=""), mock.patch("builtins.print"), mock.patch.object(update, "_root", side_effect=AssertionError("Cancel should not even open root")):
            self.assertEqual(update.main([]), 0)
        with mock.patch("builtins.input", side_effect=EOFError), mock.patch("builtins.print"):
            self.assertEqual(update.main([]), 0)

    def test_interactive_update_requires_typed_stop_ack_and_cancel_is_read_only(self):
        with mock.patch("builtins.input", side_effect=["2", "not confirmed"]), mock.patch("builtins.print"), mock.patch.object(update, "apply_update", side_effect=AssertionError("No unconfirmed update")):
            self.assertEqual(update.main([]), 0)
        with mock.patch("builtins.input", side_effect=["2", "STOPPED"]), mock.patch("builtins.print"), mock.patch.object(update, "apply_update", return_value={"status": "updated", "message": "fixture"}) as apply:
            self.assertEqual(update.main([]), 0)
            self.assertTrue(apply.call_args.kwargs["confirm_stopped"])

    def test_cli_does_not_accept_urls_branches_commands_or_sync_without_update(self):
        for argv in [["--url", "https://example.invalid"], ["--branch", "other"],
                     ["--command", "anything"], ["--sync-base"], ["--confirm-stopped"], ["--check", "--update"]]:
            with self.subTest(argv=argv), mock.patch("sys.stderr", new_callable=io.StringIO):
                with self.assertRaises(SystemExit) as error:
                    update.main(argv)
                self.assertEqual(error.exception.code, 2)

    def test_launcher_quotes_own_root_and_python_entrypoint_works_from_other_cwd(self):
        launcher = (PROJECT / "UPDATE-WINDOWS.cmd").read_text(encoding="utf-8")
        self.assertIn('cd /d "%~dp0"', launcher)
        self.assertIn("DisableDelayedExpansion", launcher)
        self.assertIn('"%~dp0tools\\update.py"', launcher)
        self.assertIn('".venv\\Scripts\\python.exe" -I', launcher)
        for forbidden in ["%*", "powershell", "ExecutionPolicy", "runas", "activate.bat", "git pull"]:
            self.assertNotIn(forbidden, launcher)
        (self.root / "tools").mkdir()
        shutil.copyfile(PROJECT / "tools/update.py", self.root / "tools/update.py")
        code = ("import importlib.util,sys; from pathlib import Path; "
                "spec=importlib.util.spec_from_file_location('fixture_update',sys.argv[1]); "
                "module=importlib.util.module_from_spec(spec); sys.modules[spec.name]=module; "
                "spec.loader.exec_module(module); print(module._root())")
        result = subprocess.run([sys.executable, "-B", "-I", "-X", "utf8", "-c", code, str(self.root / "tools/update.py")],
                                cwd=self.workspace, capture_output=True, text=True, encoding="utf-8", timeout=20, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(Path(result.stdout.strip()), self.root)
        result = subprocess.run([sys.executable, "-B", "-I", "-X", "utf8", str(self.root / "tools/update.py")],
                                cwd=self.workspace, input="\n", capture_output=True, text=True, encoding="utf-8", timeout=20, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Cancelled", result.stdout)


if __name__ == "__main__":
    unittest.main()
