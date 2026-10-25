"""Exact PNG exports; batching changes packaging, never final image pixels."""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path

from . import __version__
from .safety import UserError, digest
from .store import Store


@dataclass
class ExportPlan:
    folder: Path
    batches: list[list[Path]]
    oversized: list[str]
    count: int
    partial_source: bool


def batch_paths(paths: list[Path], byte_limit: int, file_limit: int = 8) -> tuple[list[list[Path]], list[str]]:
    if type(byte_limit) is not int or byte_limit < 1 or not 1 <= file_limit <= 10:
        raise UserError("Invalid private attachment budget.")
    batches, skipped, current, used = [], [], [], 0
    for path in paths:
        size = path.stat().st_size
        if size > byte_limit:
            skipped.append(path.name)
            continue
        if current and (used + size > byte_limit or len(current) >= file_limit):
            batches.append(current)
            current, used = [], 0
        current.append(path)
        used += size
    if current:
        batches.append(current)
    return batches, skipped


def selected_results(store: Store, job_id: str, mode="accepted"):
    store.require_consent()
    job, summary = store.job(job_id), store.summary(job_id)
    if mode not in {"accepted", "collection"}:
        raise UserError("Choose accepted matches or explicit collection output.")
    if mode == "collection":
        if job["mode"] not in {"collect-pages", "collect-panels"}:
            raise UserError("Collection export cannot include uncertain character matches. Review and accept matches first.")
        if not summary["discovery_complete"] or summary["source_problems"] or summary["pages"].get("failed") or summary["pages"].get("pending") or summary["phase"] != "review_ready":
            raise UserError("Collection is not complete. Resume or resolve source/page failures before storing the complete collection.")
        records = [row for row in store.candidates(job_id, limit=None) if row["decision"] in {"collected", "accepted"}]
    else:
        records = store.candidates(job_id, limit=None, decision="accepted")
    if not records:
        raise UserError("No eligible native outputs yet. Accept matches, or for a complete no-reference job choose mode:collection. Originals stay local.")
    return job, summary, records


def export_job(store: Store, job_id: str, byte_limit: int, *, storage_limit: int | None = None, mode="accepted") -> ExportPlan:
    job, summary, records = selected_results(store, job_id, mode)
    folder = store.export_root / job_id
    folder.mkdir(parents=True, exist_ok=True)
    local_rows, public_rows, paths = [], [], []
    for candidate in records:
        if not candidate["crop_path"]:
            continue
        raw = store.read_local(candidate["crop_path"])
        if digest(raw) != candidate["crop_sha256"]:
            raise UserError("A crop changed on disk; export stopped rather than silently exporting different pixels.")
        destination = folder / (candidate["id"] + "-" + candidate["crop_sha256"][:12] + ".png")
        if not destination.exists():
            store.write_export(destination, raw, storage_limit)
        elif digest(destination.read_bytes()) != candidate["crop_sha256"]:
            raise UserError("An existing export was changed externally. Move it aside before retrying; it was not overwritten.")
        page = store.page(candidate["page_id"])
        public = {"file": destination.name, "candidate_id": candidate["id"], "page_id": page["id"],
                  "page_number": page["page_number"], "source_sha256": page["sha256"],
                  "crop_sha256": candidate["crop_sha256"], "box_xyxy": candidate["box"],
                  "detected_bbox_xyxy": candidate["bbox"], "score_uncalibrated": candidate["score"] if job["mode"] == "match" else None,
                  "reference_indices": candidate["reference_indices"], "inference": page["info"].get("inference", {}), "spec": candidate["spec"],
                  "warnings": candidate["warnings"], "review": candidate["decision"]}
        local_rows.append({**public, "issue": page["issue"], "issue_url": page["issue_url"],
                           "image_url": page["image_url"], "original_path": page["original_path"]})
        public_rows.append(public)
        paths.append(destination)
    partial = not summary["discovery_complete"] or bool(summary["pages"].get("failed") or summary["pages"].get("pending"))
    common = {"version": __version__, "job_id": job_id, "mode": job["mode"], "export_selection": mode, "partial_source": partial,
              "model": job["model"], "threshold": job["threshold"] if job["mode"] == "match" else None,
              "references": [{"id": ref["id"], "role": ref["role"], "sha256": ref["sha256"]} for ref in job["refs"]],
              "identity_reference_ids": [ref["id"] for ref in job["refs"] if ref["role"] == "identity"],
              "reference_index_definition": "zero-based indices into identity_reference_ids; framing references are not identity queries",
              "notice": ("Scores are not accuracy percentages; generic one-shot matching requires visual review. " if job["mode"] == "match" else "Page/panel collection performs no character identity matching and has no model score. ") + "Final PNG pixels are not downscaled."}
    store.write_export(folder / "provenance-local.json", json.dumps({**common, "source_url": job["source_url"], "profile": job["profile"], "pages": store.pages(job_id), "crops": local_rows}, indent=2).encode(), storage_limit)
    public_manifest = folder / "provenance-discord.json"
    store.write_export(public_manifest, json.dumps({**common, "crops": public_rows}, indent=2).encode(), storage_limit)
    batches, oversized = batch_paths([*paths, public_manifest], byte_limit)
    return ExportPlan(folder, batches, oversized, len(paths), partial)
