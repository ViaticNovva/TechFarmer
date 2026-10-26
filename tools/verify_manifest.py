"""Verify browser-published text against reviewed source; --write is for the reviewer only."""
from __future__ import annotations

import argparse
from hashlib import sha256
from pathlib import Path
import os
import sys

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = "SOURCE-MANIFEST.sha256"
RUNTIME_DIRS = {".git", ".venv", ".runtime", ".cache", "data", "exports", "__pycache__", ".pytest_cache", "node_modules"}
RUNTIME_FILES = {"tech-farmer-settings.json", "source-profiles.json", ".env"}


def inventory() -> dict[str, str]:
    result = {}
    for folder, directories, files in os.walk(ROOT, followlinks=False):
        directories[:] = sorted(name for name in directories if name not in RUNTIME_DIRS)
        for name in directories:
            if (Path(folder) / name).is_symlink():
                raise ValueError("Publishable source must not contain directory links.")
        for name in sorted(files):
            path = Path(folder) / name
            relative = path.relative_to(ROOT)
            if (name in RUNTIME_FILES or any(name.startswith(private + ".backup-") for private in ("tech-farmer-settings.json", "source-profiles.json"))
                    or name.startswith(".env.") or name.endswith((".pyc", ".pyo", ".log", ".part")) or relative.as_posix() == MANIFEST):
                continue
            if path.is_symlink():
                raise ValueError("Publishable source must not contain symbolic links: " + relative.as_posix())
            raw = path.read_bytes()
            raw.decode("utf-8")
            if not raw.strip() or b"\r" in raw or b"\0" in raw or not raw.endswith(b"\n"):
                raise ValueError("Source must be nonempty UTF-8 text with LF newlines and a final newline: " + relative.as_posix())
            result[relative.as_posix()] = sha256(raw).hexdigest()
    return dict(sorted(result.items()))


def archive_verified_source(expected):
    """Create-only CI archive from manifest bytes, never a recursive folder ZIP."""
    import zipfile
    if __package__:
        from . import update
    else:
        import update
    manifest = update._read(ROOT, MANIFEST, update.MAX_MANIFEST)
    if update.parse_manifest(manifest) != expected:
        raise ValueError("Manifest changed before packaging; no source archive was created.")
    payloads = {}
    for name, digest in expected.items():
        raw = update._read(ROOT, name)
        if sha256(raw).hexdigest() != digest:
            raise ValueError("Source changed before packaging: " + name)
        payloads[name] = raw
    payloads[MANIFEST] = manifest
    update._mkdir(ROOT, ".runtime/ci-source")
    target = update._path(ROOT, ".runtime/ci-source/TechFarmer-source.zip")
    with zipfile.ZipFile(target, "x", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, raw in sorted(payloads.items()):
            archive.writestr("TechFarmer/" + name, raw)
    return target


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--write", action="store_true", help="Regenerate only after review; CI never uses this")
    mode.add_argument("--archive", action="store_true", help="After successful verification, create-only manifest-listed source ZIP in .runtime/ci-source; never user data/models")
    args = parser.parse_args(argv)
    try:
        actual = inventory()
        if __package__:
            from .update import UpdateError, parse_manifest
        else:
            from update import UpdateError, parse_manifest
        content = "".join(f"{value}  {name}\n" for name, value in actual.items())
        try:
            parse_manifest(content.encode("utf-8"))
        except UpdateError as exc:
            raise ValueError("Publication is not compatible with the exact updater allowlist/required entrypoints: " + str(exc)) from None
        path = ROOT / MANIFEST
        if args.write:
            path.write_text(content, encoding="utf-8", newline="\n")
            print(f"Wrote {MANIFEST}: {len(actual)} text source files. This is not a remote commit.")
            return 0
        expected = {}
        for line in path.read_text(encoding="utf-8").splitlines():
            value, name = line.split("  ", 1)
            if name in expected or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
                raise ValueError("Malformed or duplicate manifest entry.")
            expected[name] = value
        missing = sorted(set(expected) - set(actual))
        extra = sorted(set(actual) - set(expected))
        changed = sorted(name for name in set(actual) & set(expected) if actual[name] != expected[name])
        if missing or extra or changed:
            print("Source integrity FAILED. Missing:", missing, "Extra:", extra, "Changed:", changed)
            return 1
        print(f"Verified {len(actual)} text source files against {MANIFEST}.")
        if args.archive:
            try:
                archive = archive_verified_source(expected)
            except UpdateError as exc:
                raise ValueError("Source archive refused: " + str(exc)) from None
            print("Created manifest-only source archive:", archive)
        return 0
    except (OSError, UnicodeError, ValueError) as exc:
        print("Source integrity FAILED:", str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
