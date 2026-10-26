"""Durable private jobs, frozen references, originals and review state (SQLite)."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import json
import sqlite3
import threading
from uuid import uuid4

from .images import decode
from .safety import LIMITS, UserError, StorageBudgetExceeded, atomic_write, digest, json_text, label, local_root, persistent_seed

CONSENT_VERSION = 2
CONSENT_TEXT = (
    "Use only source pages and references you own, have licensed, or have explicit permission to download and edit. "
    "Public access is NOT a license. No protection, login, CAPTCHA or paywall bypass is supported. "
    "Identity references run through a local generic OWLv2 detector; matches are uncertain, not verified character identity. "
    "References, source URLs, originals, crops and provenance stay on this PC; requested previews/exports are uploaded to Discord. "
    "A separately confirmed private storage channel keeps persistent results visible to everyone with channel access, "
    "including permitted roles and administrators, not just you. Discord copies are NOT end-to-end encrypted or local-only. "
    "Model preparation contacts Hugging Face only for model files, "
    "not your images. Keep the PC running. Revoking consent stops new work, but does not erase local or Discord copies."
)
ACTIVE_PHASES = {"queued", "discovering", "matching"}
RESUMABLE_PHASES = {"paused", "paused_restart", "paused_budget", "paused_model", "paused_source", "paused_error", "review_partial", "awaiting_approval"}
STORAGE_CONSENT_VERSION = 1
STORAGE_CONSENT_TEXT = (
    "Save requested results persistently in this private server channel. Everyone with channel access can read and download them; "
    "permitted roles and administrators mean this is NOT owner-only privacy. No channel permissions will be changed. "
    "Only accepted matches or explicitly requested complete page/panel collections are sent, never reference uploads. "
    "Revoking consent stops future batches, not existing Discord copies."
)


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def ident(prefix: str) -> str:
    return prefix + "_" + uuid4().hex[:16]


def unpack(row):
    if row is None:
        return None
    result = dict(row)
    for key in list(result):
        if key.endswith("_json"):
            result[key[:-5]] = json.loads(result.pop(key))
    return result


class Store:
    def __init__(self, owner_id: int, guild_id: int, root: str | Path = "data", exports: str | Path = "exports"):
        self.owner_id, self.guild_id = owner_id, guild_id
        self.root, self.export_root = local_root(root), local_root(exports)
        self.path = self.root / "tech-farmer.sqlite3"
        self.notices: list[str] = []
        if self.path.is_symlink():
            raise UserError("Database must not be a symbolic link.")
        self.lock = threading.RLock()
        self.safety_generation = 0
        self._prepare_database()
        with self.db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS profiles (name TEXT PRIMARY KEY, created TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS refs (
                    id TEXT PRIMARY KEY, profile TEXT NOT NULL REFERENCES profiles(name), role TEXT NOT NULL,
                    path TEXT NOT NULL, sha256 TEXT NOT NULL, info_json TEXT NOT NULL, created TEXT NOT NULL,
                    UNIQUE(profile, role, sha256));
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY, created TEXT NOT NULL, updated TEXT NOT NULL,
                    phase TEXT NOT NULL, control TEXT NOT NULL DEFAULT '', source_url TEXT NOT NULL,
                    scope TEXT NOT NULL, profile TEXT NOT NULL, spec_json TEXT NOT NULL,
                    refs_json TEXT NOT NULL, model_json TEXT NOT NULL, threshold REAL NOT NULL,
                    discovery_complete INTEGER NOT NULL DEFAULT 0, discovery_problems_json TEXT NOT NULL DEFAULT '[]',
                    last_code TEXT NOT NULL DEFAULT '', run_count INTEGER NOT NULL DEFAULT 0,
                    mode TEXT NOT NULL DEFAULT 'match', source_config_json TEXT NOT NULL DEFAULT '{}',
                    source_proposal_json TEXT NOT NULL DEFAULT '{}', approved_proposal_json TEXT NOT NULL DEFAULT '{}');
                CREATE TABLE IF NOT EXISTS pages (
                    id TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES jobs(id), page_key TEXT NOT NULL,
                    issue TEXT NOT NULL, issue_url TEXT NOT NULL, page_number INTEGER NOT NULL,
                    image_url TEXT NOT NULL, fetch_id TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
                    original_path TEXT, sha256 TEXT, info_json TEXT NOT NULL DEFAULT '{}',
                    duplicate_of TEXT, error_code TEXT NOT NULL DEFAULT '', UNIQUE(job_id,page_key));
                CREATE TABLE IF NOT EXISTS candidates (
                    id TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES jobs(id), page_id TEXT NOT NULL REFERENCES pages(id),
                    match_key TEXT NOT NULL, bbox_json TEXT NOT NULL, box_json TEXT,
                    score REAL NOT NULL, reference_indices_json TEXT NOT NULL, warnings_json TEXT NOT NULL,
                    crop_path TEXT, crop_sha256 TEXT, spec_json TEXT NOT NULL, needs_review INTEGER NOT NULL,
                    decision TEXT NOT NULL DEFAULT 'pending', UNIQUE(job_id,page_id,match_key));
                CREATE INDEX IF NOT EXISTS pages_job ON pages(job_id,status);
                CREATE INDEX IF NOT EXISTS crops_job ON candidates(job_id,decision);
                CREATE TABLE IF NOT EXISTS storage_receipts (
                    channel_id TEXT NOT NULL, sha256 TEXT NOT NULL, job_id TEXT NOT NULL,
                    candidate_id TEXT NOT NULL, status TEXT NOT NULL, message_id TEXT,
                    updated TEXT NOT NULL, PRIMARY KEY(channel_id,sha256));
            """)
            columns = {row[1] for row in db.execute("PRAGMA table_info(jobs)")}
            for key, declaration in {
                "mode": "TEXT NOT NULL DEFAULT 'match'", "source_config_json": "TEXT NOT NULL DEFAULT '{}'",
                "source_proposal_json": "TEXT NOT NULL DEFAULT '{}'", "approved_proposal_json": "TEXT NOT NULL DEFAULT '{}'",
            }.items():
                if key not in columns:
                    db.execute(f"ALTER TABLE jobs ADD COLUMN {key} {declaration}")
            db.execute("PRAGMA user_version=3")
            owner_scope = json_text([owner_id, guild_id])
            row = db.execute("SELECT value FROM settings WHERE key='owner_scope'").fetchone()
            if row is not None and row[0] != owner_scope:
                raise UserError("This data folder belongs to a different owner/server. Use a separate project copy; data is not shared across owners.")
            db.execute("INSERT OR IGNORE INTO settings VALUES ('owner_scope',?)", (owner_scope,))

    def _prepare_database(self):
        if not self.path.exists():
            if any(self.root.glob("*.db")) or any(self.root.glob("*.sqlite3")):
                self.notices.append("Legacy database files were preserved unchanged. A new v0.3 database was created; manual-prototype jobs were NOT migrated.")
            return
        required = {
            "settings": {"key", "value"}, "profiles": {"name", "created"},
            "refs": {"id", "profile", "role", "path", "sha256", "info_json", "created"},
            "jobs": {"id", "created", "updated", "phase", "control", "source_url", "scope", "profile", "spec_json", "refs_json", "model_json", "threshold", "discovery_complete", "discovery_problems_json", "last_code", "run_count"},
            "pages": {"id", "job_id", "page_key", "issue", "issue_url", "page_number", "image_url", "fetch_id", "status", "original_path", "sha256", "info_json", "duplicate_of", "error_code"},
            "candidates": {"id", "job_id", "page_id", "match_key", "bbox_json", "box_json", "score", "reference_indices_json", "warnings_json", "crop_path", "crop_sha256", "spec_json", "needs_review", "decision"},
        }
        connection = sqlite3.connect(self.path, timeout=10)
        compatible = False
        try:
            try:
                compatible = all(columns <= {row[1] for row in connection.execute(f"PRAGMA table_info({table})")} for table, columns in required.items())
                if compatible:
                    scope = connection.execute("SELECT value FROM settings WHERE key='owner_scope'").fetchone()
                    if scope and scope[0] != json_text([self.owner_id, self.guild_id]):
                        raise UserError("This data folder belongs to a different owner/server; it was not migrated.")
                    version = connection.execute("PRAGMA user_version").fetchone()[0]
                    if version > 3:
                        raise UserError("This database was made by a newer version. It was not downgraded or changed.")
                    if version < 3:
                        folder = self.root / "backups"
                        folder.mkdir(exist_ok=True)
                        if folder.is_symlink():
                            raise UserError("Database backup folder must not be a symbolic link.")
                        backup = folder / ("schema2-" + uuid4().hex + ".sqlite3")
                        target = sqlite3.connect(backup)
                        try:
                            connection.backup(target)
                        finally:
                            target.close()
                        self.notices.append("Compatible v0.2 database backed up under data/backups before additive v0.3 migration; IDs, references and decisions were preserved.")
            except sqlite3.DatabaseError:
                if compatible:
                    raise UserError("Compatible database backup/preflight failed. Existing jobs were preserved; no replacement database was created. Check local disk access before restarting.") from None
                compatible = False
        finally:
            connection.close()
        if not compatible:
            if self.path.name == "tech-farmer-v03.sqlite3":
                raise UserError("The v0.3 database is incompatible or corrupt; existing files were preserved, not replaced.")
            legacy = self.path
            self.path = self.root / "tech-farmer-v03.sqlite3"
            if self.path.is_symlink():
                raise UserError("New-version database path is unsafe; legacy data was not changed.")
            self.notices.append("Incompatible legacy database preserved unchanged. Using tech-farmer-v03.sqlite3; legacy jobs were NOT migrated. Keep the original files and backups.")
            if self.path.exists():
                candidate = self.path
                self._prepare_database()
                if self.path != candidate or self.path == legacy:
                    raise UserError("New-version database is incompatible; all existing databases were preserved.")

    @contextmanager
    def db(self):
        with self.lock:
            connection = sqlite3.connect(self.path, timeout=10)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA journal_mode=WAL")
            try:
                with connection:
                    yield connection
            finally:
                connection.close()

    def consent(self, accept: bool) -> None:
        with self.db() as db:
            value = json_text({"accepted": bool(accept), "version": CONSENT_VERSION, "updated": now()})
            db.execute("INSERT OR REPLACE INTO settings VALUES ('consent',?)", (value,))
            self.safety_generation += 1
            if not accept:
                db.execute("UPDATE jobs SET control='pause', last_code='consent_revoked' WHERE phase IN ('queued','discovering','matching') AND control<>'cancel'")

    def has_consent(self) -> bool:
        with self.db() as db:
            row = db.execute("SELECT value FROM settings WHERE key='consent'").fetchone()
        value = json.loads(row[0]) if row else {}
        return value.get("accepted") is True and value.get("version") == CONSENT_VERSION

    def require_consent(self):
        if not self.has_consent():
            raise UserError("Read /farmer consent, then accept:true before references, harvest, review or export.")

    def add_reference(self, profile: str, raw: bytes, role="identity", mime=None, suffix=None, *, storage_limit=None) -> dict:
        self.require_consent()
        profile = label(profile)
        if role not in {"identity", "framing"}:
            raise UserError("Reference role must be identity or framing.")
        _, info = decode(raw, mime, suffix, reference=True)
        sha = digest(raw)
        with self.db() as db:
            db.execute("INSERT OR IGNORE INTO profiles VALUES (?,?)", (profile, now()))
            old = db.execute("SELECT * FROM refs WHERE profile=? AND role=? AND sha256=?", (profile, role, sha)).fetchone()
            if old:
                return unpack(old)
            count = db.execute("SELECT COUNT(*) FROM refs WHERE profile=? AND role=?", (profile, role)).fetchone()[0]
            if count >= (LIMITS.references if role == "identity" else 1):
                raise UserError("Profile is full: at most eight identity references and one optional framing reference. Use a new profile to change the set.")
            if storage_limit is not None and self.storage_bytes() + len(raw) > storage_limit:
                raise StorageBudgetExceeded("Local storage budget reached; reference was not saved.")
            ref_id = ident("r")
            path = self.root / "references" / (ref_id + ".original")
            atomic_write(path, raw)
            db.execute("INSERT INTO refs VALUES (?,?,?,?,?,?,?)", (ref_id, profile, role, str(path.relative_to(self.root)), sha, json_text(info), now()))
            return unpack(db.execute("SELECT * FROM refs WHERE id=?", (ref_id,)).fetchone())

    def profiles(self) -> list[dict]:
        with self.db() as db:
            return [dict(row) for row in db.execute("SELECT profiles.name, SUM(refs.role='identity') AS identity_refs, SUM(refs.role='framing') AS framing_refs FROM profiles LEFT JOIN refs ON profiles.name=refs.profile GROUP BY profiles.name ORDER BY profiles.name")]

    def create_job(self, source_url: str, scope: str, profile: str | None, spec: dict, model: dict, threshold=0.15, *, routing_keys=(), storage_limit=None, mode="match", source_config=None) -> dict:
        self.require_consent()
        if scope not in {"auto", "page", "chapter", "series"}:
            raise UserError("Scope must be auto, page, chapter or series.")
        if not isinstance(source_url, str) or not 1 <= len(source_url) <= 3000:
            raise UserError("Provide a supported source URL.")
        source_url = persistent_seed(source_url, routing_keys)
        if type(threshold) not in {int, float} or not 0 < threshold <= 1:
            raise UserError("Threshold must be greater than zero and at most one; it is not an accuracy percentage.")
        if mode not in {"match", "collect-pages", "collect-panels"}:
            raise UserError("Choose match, collect-pages or collect-panels mode.")
        if mode != "match" and profile:
            raise UserError("Collection does not use identity references. Omit the profile or choose match mode.")
        profile = label(profile) if profile else ""
        with self.db() as db:
            rows = [unpack(row) for row in db.execute("SELECT * FROM refs WHERE profile=? ORDER BY rowid", (profile,))] if mode == "match" else []
            if mode == "match" and not any(row["role"] == "identity" for row in rows):
                raise UserError("Add one or more identity references to this profile first. A framing-only reference cannot identify a character.")
            if storage_limit is not None and self.storage_bytes() + sum((self.root / row["path"]).stat().st_size for row in rows) > storage_limit:
                raise UserError("Free more local data budget before freezing this job's references; no source was fetched.")
            job_id, frozen = ident("j"), []
            for row in rows:
                raw = self.read_local(row["path"])
                if digest(raw) != row["sha256"]:
                    raise UserError("A saved reference has changed. Restore it or create a new profile.")
                destination = self.root / "jobs" / job_id / "references" / (row["id"] + ".original")
                atomic_write(destination, raw)
                frozen.append({**row, "path": str(destination.relative_to(self.root))})
            db.execute("INSERT INTO jobs(id,created,updated,phase,source_url,scope,profile,spec_json,refs_json,model_json,threshold,mode,source_config_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                       (job_id, now(), now(), "queued", source_url, scope, profile, json_text(spec), json_text(frozen), json_text(model), threshold, mode, json_text(source_config or {})))
        return self.job(job_id)

    def set_source_proposal(self, job_id: str, proposal: dict):
        with self.db() as db:
            db.execute("UPDATE jobs SET source_proposal_json=?,updated=? WHERE id=?", (json_text(proposal), now(), job_id))

    def approve_source(self, job_id: str, accept: bool, *, proposal_id: str | None = None) -> dict:
        from .source_setup import approve_source
        self.require_consent()
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            job = unpack(db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone())
            if not job or job["phase"] != "awaiting_approval" or not job["source_proposal"]:
                raise UserError("This job has no pending external-image-origin approval. Use /farmer status.")
            if accept is not True:
                db.execute("UPDATE jobs SET phase='paused',control='pause',approved_proposal_json='{}',last_code='source_approval_declined' WHERE id=?", (job_id,))
            else:
                proposal = job["source_proposal"]
                if not proposal_id or proposal_id != proposal.get("proposal_id"):
                    raise UserError("Source proposal changed or confirmation is missing. Inspect /farmer approve again and use its new approval button; no new origins were approved.")
                approved = approve_source(proposal, proposal_id=proposal_id, approved_origins=proposal["required_origins"])
                db.execute("UPDATE jobs SET approved_proposal_json=?,phase='paused',control='',last_code='source_approved',updated=? WHERE id=?", (json_text(approved.to_dict()), now(), job_id))
        return self.job(job_id)

    def has_active_jobs(self) -> bool:
        with self.db() as db:
            return bool(db.execute("SELECT 1 FROM jobs WHERE phase IN ('queued','discovering','matching') LIMIT 1").fetchone())

    def storage_destination(self) -> dict:
        with self.db() as db:
            row = db.execute("SELECT value FROM settings WHERE key='storage_destination'").fetchone()
        return json.loads(row[0]) if row else {}

    def set_storage_destination(self, channel_id: int | None, accept: bool):
        if type(accept) is not bool or (accept and (type(channel_id) is not int or channel_id <= 0)):
            raise UserError("Choose a real private text channel and explicitly accept its persistent-storage notice.")
        if accept:
            self.require_consent()
        previous = self.storage_destination()
        value = {"channel_id": channel_id or previous.get("channel_id"), "accepted": accept,
                 "version": STORAGE_CONSENT_VERSION, "owner_id": self.owner_id, "guild_id": self.guild_id, "updated": now()}
        with self.db() as db:
            db.execute("INSERT OR REPLACE INTO settings VALUES ('storage_destination',?)", (json_text(value),))
            self.safety_generation += 1

    def storage_snapshot(self, owner_id: int, guild_id: int):
        with self.lock:
            return self.require_storage_destination(owner_id, guild_id), self.safety_generation

    def require_storage_destination(self, owner_id: int, guild_id: int) -> dict:
        self.require_consent()
        value = self.storage_destination()
        if (owner_id != self.owner_id or guild_id != self.guild_id or value.get("owner_id") != self.owner_id
                or value.get("guild_id") != self.guild_id or value.get("accepted") is not True
                or value.get("version") != STORAGE_CONSENT_VERSION or type(value.get("channel_id")) is not int):
            raise UserError("No consented private storage destination. Use /farmer storage channel:<private text channel> accept:true; there is no current-channel fallback.")
        return value

    def receipt(self, channel_id: int, sha256: str) -> dict | None:
        with self.db() as db:
            row = db.execute("SELECT * FROM storage_receipts WHERE channel_id=? AND sha256=?", (str(channel_id), sha256)).fetchone()
        return dict(row) if row else None

    def receipts(self, job_id: str) -> list[dict]:
        self.job(job_id)
        with self.db() as db:
            return [dict(row) for row in db.execute("SELECT * FROM storage_receipts WHERE job_id=? ORDER BY updated,sha256", (job_id,))]

    def claim_outputs(self, channel_id: int, job_id: str, outputs: list[dict]) -> list[dict]:
        claimed = []
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            for item in outputs:
                cursor = db.execute("INSERT OR IGNORE INTO storage_receipts(channel_id,sha256,job_id,candidate_id,status,updated) VALUES (?,?,?,?,?,?)", (str(channel_id), item["sha256"], job_id, item["candidate_id"], "sending", now()))
                if cursor.rowcount:
                    claimed.append(item)
        return claimed

    def release_unsent(self, channel_id: int, hashes: list[str]):
        with self.db() as db:
            for sha in hashes:
                db.execute("DELETE FROM storage_receipts WHERE channel_id=? AND sha256=? AND status='sending'", (str(channel_id), sha))

    def finish_outputs(self, channel_id: int, hashes: list[str], status: str, message_id: int | None = None):
        if status not in {"sent", "unknown"} or (status == "sent" and (type(message_id) is not int or message_id <= 0)):
            raise UserError("Invalid durable storage receipt outcome.")
        with self.db() as db:
            for sha in hashes:
                db.execute("UPDATE storage_receipts SET status=?,message_id=?,updated=? WHERE channel_id=? AND sha256=? AND status='sending'", (status, str(message_id) if message_id else None, now(), str(channel_id), sha))

    def read_local(self, relative: str) -> bytes:
        path = self.root / relative
        if path.is_symlink() or not path.resolve().is_relative_to(self.root) or any(parent.is_symlink() for parent in path.parents):
            raise UserError("Local data path is unsafe.")
        return path.read_bytes()

    def job(self, job_id: str) -> dict:
        with self.db() as db:
            result = unpack(db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone())
        if result is None:
            raise UserError("Unknown local job ID. Use /farmer status.")
        return result

    def jobs(self, offset=0, limit=8) -> list[dict]:
        with self.db() as db:
            return [unpack(row) for row in db.execute("SELECT * FROM jobs ORDER BY rowid DESC LIMIT ? OFFSET ?", (limit, offset))]

    def set_phase(self, job_id: str, phase: str, code=""):
        with self.db() as db:
            row = db.execute("SELECT control FROM jobs WHERE id=?", (job_id,)).fetchone()
            if row and row[0] == "cancel":
                phase = "cancelled"
            elif row and row[0] == "pause":
                phase = "paused"
            db.execute("UPDATE jobs SET phase=?,last_code=?,updated=? WHERE id=?", (phase, code, now(), job_id))

    def control(self, job_id: str, action: str) -> dict:
        if action not in {"pause", "resume", "cancel"}:
            raise UserError("Choose pause, resume or cancel.")
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            job = unpack(db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone())
            if job is None:
                raise UserError("Unknown local job ID. Use /farmer status.")
            if job["control"] == "cancel" and action != "cancel":
                raise UserError("Cancellation is already requested or complete; pause/resume cannot undo it.")
            if action == "resume":
                self.require_consent()
                if job["phase"] not in RESUMABLE_PHASES:
                    raise UserError("This job cannot resume in its current state. Cancelled jobs stay cancelled; create a new job if needed.")
                db.execute("UPDATE jobs SET phase='queued',control='',last_code='',updated=? WHERE id=?", (now(), job_id))
                db.execute("UPDATE pages SET status='pending',error_code='' WHERE job_id=? AND status IN ('failed','processing')", (job_id,))
            elif action == "cancel":
                if job["phase"] in ACTIVE_PHASES:
                    db.execute("UPDATE jobs SET control='cancel',updated=? WHERE id=?", (now(), job_id))
                else:
                    db.execute("UPDATE jobs SET phase='cancelled',control='cancel',updated=? WHERE id=?", (now(), job_id))
            else:
                if job["phase"] in ACTIVE_PHASES:
                    db.execute("UPDATE jobs SET control='pause',updated=? WHERE id=?", (now(), job_id))
                elif job["phase"] != "cancelled":
                    db.execute("UPDATE jobs SET phase='paused',control='pause',updated=? WHERE id=?", (now(), job_id))
        return self.job(job_id)

    def recover(self) -> int:
        with self.db() as db:
            count = db.execute("SELECT COUNT(*) FROM jobs WHERE phase IN ('queued','discovering','matching')").fetchone()[0]
            db.execute("UPDATE jobs SET phase=CASE WHEN control='cancel' THEN 'cancelled' ELSE 'paused_restart' END,last_code='process_restarted' WHERE phase IN ('queued','discovering','matching')")
            db.execute("UPDATE pages SET status='pending' WHERE status='processing'")
            db.execute("UPDATE storage_receipts SET status='unknown',updated=? WHERE status='sending'", (now(),))
        return count

    def begin(self, job_id: str):
        with self.db() as db:
            db.execute("UPDATE jobs SET run_count=run_count+1,phase='discovering',updated=? WHERE id=?", (now(), job_id))

    def merge_manifest(self, job_id: str, manifest: dict):
        with self.db() as db:
            for page in manifest.get("pages", []):
                page_key = page.get("page_key") or digest(json_text([page["issue_url"], page["page_number"], page["image_url"]]).encode())
                db.execute("INSERT INTO pages(id,job_id,page_key,issue,issue_url,page_number,image_url,fetch_id) VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(job_id,page_key) DO UPDATE SET fetch_id=excluded.fetch_id",
                           (ident("p"), job_id, page_key, str(page.get("issue", "")), page["issue_url"], page["page_number"], page["image_url"], page.get("fetch_id", page["image_url"])))
            problems = manifest.get("problems", [])
            db.execute("UPDATE jobs SET discovery_complete=?,discovery_problems_json=?,updated=? WHERE id=?",
                       (int(manifest.get("complete", False)), json_text(problems), now(), job_id))

    def pages(self, job_id: str, pending=False) -> list[dict]:
        with self.db() as db:
            condition = " AND status='pending'" if pending else ""
            return [unpack(row) for row in db.execute("SELECT * FROM pages WHERE job_id=?" + condition + " ORDER BY rowid", (job_id,))]

    def page(self, page_id: str) -> dict:
        with self.db() as db:
            result = unpack(db.execute("SELECT * FROM pages WHERE id=?", (page_id,)).fetchone())
        if result is None:
            raise UserError("Unknown page ID.")
        return result

    def duplicate(self, job_id: str, sha256: str, exclude: str):
        with self.db() as db:
            row = db.execute("SELECT id FROM pages WHERE job_id=? AND sha256=? AND id<>? AND status='done' LIMIT 1", (job_id, sha256, exclude)).fetchone()
        return row[0] if row else None

    def fail_page(self, page_id: str, code: str):
        with self.db() as db:
            db.execute("UPDATE pages SET status='failed',error_code=? WHERE id=?", (code, page_id))

    def save_page(self, page: dict, raw: bytes, info: dict, crops: list[dict], duplicate_of=None, *, storage_limit=None):
        with self.lock:
            required = len(raw) + sum(len(crop.get("png") or b"") for crop in crops)
            if storage_limit is not None and self.storage_bytes() + required > storage_limit:
                raise StorageBudgetExceeded("Local storage budget reached; no new original/crop was committed. Archive data or increase storage_mib.")
            self._save_page(page, raw, info, crops, duplicate_of)

    def _save_page(self, page: dict, raw: bytes, info: dict, crops: list[dict], duplicate_of=None):
        folder = self.root / "jobs" / page["job_id"] / "pages" / page["id"]
        original = folder / ("original-" + digest(raw)[:16] + ".bin")
        atomic_write(original, raw)
        records = []
        for candidate in crops:
            candidate = dict(candidate)
            crop_id = ident("c")
            content = candidate.pop("png", None)
            crop_path = folder / (crop_id + ".png") if content is not None else None
            if crop_path:
                atomic_write(crop_path, content)
            records.append((crop_id, page["job_id"], page["id"], candidate["match_key"], json_text(candidate["bbox"]),
                            json_text(candidate.get("box")), candidate["score"], json_text(candidate["reference_indices"]),
                            json_text(candidate["warnings"]), str(crop_path.relative_to(self.root)) if crop_path else None,
                            digest(content) if content is not None else None, json_text(candidate["spec"]), int(candidate["needs_review"]), candidate.get("decision", "pending")))
        with self.db() as db:
            for record in records:
                db.execute("INSERT OR IGNORE INTO candidates(id,job_id,page_id,match_key,bbox_json,box_json,score,reference_indices_json,warnings_json,crop_path,crop_sha256,spec_json,needs_review,decision) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", record)
            db.execute("UPDATE pages SET status='done',original_path=?,sha256=?,info_json=?,duplicate_of=?,error_code='' WHERE id=?",
                       (str(original.relative_to(self.root)), digest(raw), json_text(info), duplicate_of, page["id"]))

    def candidates(self, job_id: str, offset=0, limit=8, decision=None) -> list[dict]:
        self.job(job_id)
        with self.db() as db:
            query = "SELECT * FROM candidates WHERE job_id=?"
            args = [job_id]
            if decision:
                query += " AND decision=?"
                args.append(decision)
            query += " ORDER BY rowid"
            if limit is not None:
                query += " LIMIT ? OFFSET ?"
                args += [limit, offset]
            return [unpack(row) for row in db.execute(query, args)]

    def candidate(self, candidate_id: str) -> dict:
        with self.db() as db:
            result = unpack(db.execute("SELECT * FROM candidates WHERE id=?", (candidate_id,)).fetchone())
        if result is None:
            raise UserError("Unknown candidate ID. Use /farmer review.")
        return result

    def decide(self, candidate_id: str, decision: str):
        self.require_consent()
        item = self.candidate(candidate_id)
        if decision not in {"accepted", "rejected", "pending"}:
            raise UserError("Choose accepted, rejected or pending.")
        if decision == "accepted" and not item["crop_path"]:
            raise UserError("No supported crop exists for this candidate. Use /farmer reframe with a supported preset first.")
        with self.db() as db:
            db.execute("UPDATE candidates SET decision=? WHERE id=?", (decision, candidate_id))

    def replace_crop(self, candidate_id: str, box, spec: dict, raw: bytes, warnings: list[str], *, storage_limit=None):
        with self.db() as db:
            item = unpack(db.execute("SELECT * FROM candidates WHERE id=?", (candidate_id,)).fetchone())
            if item is None:
                raise UserError("Unknown candidate ID.")
            if storage_limit is not None and self.storage_bytes() + len(raw) > storage_limit:
                raise StorageBudgetExceeded("Local storage budget reached; reframing did not change this candidate. Archive data or increase storage_mib, then retry.")
            folder = self.root / "jobs" / item["job_id"] / "edits"
            path = folder / (candidate_id + "-" + uuid4().hex[:8] + ".png")
            atomic_write(path, raw)
            db.execute("UPDATE candidates SET box_json=?,spec_json=?,crop_path=?,crop_sha256=?,warnings_json=?,needs_review=1,decision='pending' WHERE id=?",
                       (json_text(box), json_text(spec), str(path.relative_to(self.root)), digest(raw), json_text(warnings), candidate_id))

    def summary(self, job_id: str) -> dict:
        job = self.job(job_id)
        with self.db() as db:
            pages = {row[0]: row[1] for row in db.execute("SELECT status,COUNT(*) FROM pages WHERE job_id=? GROUP BY status", (job_id,))}
            candidates = {row[0]: row[1] for row in db.execute("SELECT decision,COUNT(*) FROM candidates WHERE job_id=? GROUP BY decision", (job_id,))}
        return {"id": job_id, "phase": job["phase"], "control": job["control"], "code": job["last_code"], "mode": job["mode"],
                "required_origins": job["source_proposal"].get("required_origins", []) if job["phase"] == "awaiting_approval" else [],
                "source_message": job["source_proposal"].get("message", "") if job["source_proposal"].get("status") in {"unsupported", "ambiguous"} else "",
                "discovery_complete": bool(job["discovery_complete"]), "source_problems": len(job["discovery_problems"]),
                "pages": pages, "candidates": candidates, "run_count": job["run_count"]}

    def write_export(self, path: Path, raw: bytes, storage_limit=None):
        with self.lock:
            if not path.resolve().is_relative_to(self.export_root):
                raise UserError("Export path must stay in the local exports folder.")
            previous = path.stat().st_size if path.is_file() else 0
            if storage_limit is not None and self.storage_bytes() + max(0, len(raw) - previous) > storage_limit:
                raise StorageBudgetExceeded("Local export storage budget reached. Existing files remain unchanged; archive data or increase storage_mib.")
            atomic_write(path, raw)

    def storage_bytes(self) -> int:
        return sum(path.stat().st_size for root in (self.root, self.export_root) for path in root.rglob("*") if path.is_file() and not path.is_symlink())
