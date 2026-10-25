"""One durable worker; no Discord interaction token is retained by jobs."""
from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import dataclass
import inspect
import threading

from .framing import plan_crop
from .images import crop_native, decode, png, suggest_panels
from .safety import UserError, StorageBudgetExceeded, CropBudgetExceeded, CandidateBudgetExceeded, digest, json_text
from .store import Store


@dataclass(frozen=True)
class RunLimits:
    pages_per_run: int = 200
    candidates_per_run: int = 1000
    detections_per_page: int = 200
    storage_bytes: int = 2048 * 1024 * 1024
    tile_size: int = 960
    max_tiles: int = 24
    page_crop_bytes: int = 128 * 1024 * 1024


def crop_spec(kind="detected", aspect=None, padding="0,0,0,0", trim="0,0,0,0", align="center", shape="rectangle", fit="contain") -> dict:
    if kind not in {"detected", "tight", "reference", "panel", "face", "upper_body", "full_body"}:
        raise UserError("Unknown crop preset.")
    if shape not in {"rectangle", "ellipse", "circle"}:
        raise UserError("Shape must be rectangle, ellipse or circle.")
    if align not in {"center", "left", "right", "top", "bottom", "top-left", "top-right", "bottom-left", "bottom-right"}:
        raise UserError("Unknown alignment.")
    if fit not in {"contain", "cover"}:
        raise UserError("Fit must be contain (keep target) or cover (may trim target).")

    def margins(text):
        try:
            parts = [int(part.strip()) for part in text.split(",")]
        except (AttributeError, TypeError, ValueError):
            raise UserError("Context and trim use four pixel counts: left,top,right,bottom.") from None
        if len(parts) != 4 or any(not 0 <= number <= 30000 for number in parts):
            raise UserError("Context and trim each need four integers between zero and 30000: left,top,right,bottom.")
        return dict(zip(("left", "top", "right", "bottom"), parts))

    if aspect in {None, "", "original"}:
        aspect = None
    else:
        try:
            fields = str(aspect).split(":")
            if any(float(field) <= 0 for field in fields):
                raise ValueError
            value = float(fields[0]) if len(fields) == 1 else float(fields[0]) / float(fields[1])
            if len(fields) > 2 or not 0.05 <= value <= 20:
                raise ValueError
        except (ValueError, ZeroDivisionError, TypeError):
            raise UserError("Aspect must be a positive ratio from 1:20 to 20:1, such as 1:1 or 4:5.") from None
        aspect = value
    if shape == "circle":
        if aspect not in {None, 1.0}:
            raise UserError("Circle shape requires a 1:1 aspect.")
        aspect = 1.0
    padding_values, trim_values = margins(padding), margins(trim)
    if any(padding_values[side] and trim_values[side] for side in padding_values):
        raise UserError("Do not pad and trim the same side simultaneously. Enter the net per-side instruction instead.")
    return {"kind": kind, "aspect": aspect, "padding": padding_values, "trim": trim_values,
            "align": align, "fit": fit, "shape": shape}


def render_candidate(page, detection: dict, spec: dict, reference_size=None, *, byte_limit=None) -> dict:
    framing = {key: value for key, value in spec.items() if key != "shape"}
    panels = suggest_panels(page) if spec["kind"] == "panel" else None
    planned = plan_crop(page.size, detection["bbox"], framing, reference_size=reference_size, panels=panels)
    warnings = list(detection.get("warnings", [])) + list(planned.get("warnings", []))
    content = png(crop_native(page, planned["box"], spec.get("shape", "rectangle")), byte_limit) if planned.get("box") else None
    return {"bbox": detection["bbox"], "box": planned.get("box"), "score": float(detection["score"]),
            "reference_indices": detection.get("reference_indices", []), "warnings": warnings,
            "needs_review": True, "spec": deepcopy(spec), "png": content,
            "match_key": digest(json_text([detection["bbox"], sorted(detection.get("reference_indices", []))]).encode())}


class JobRunner:
    def __init__(self, store: Store, matcher, source_factory, limits: RunLimits | None = None, *, source_preparer=None, prepared_source_factory=None):
        self.store, self.matcher, self.source_factory = store, matcher, source_factory
        self.source_preparer, self.prepared_source_factory = source_preparer, prepared_source_factory
        self.source_settings = None
        self.limits = limits or RunLimits()
        self.queue = asyncio.Queue()
        self.scheduled = set()
        self.signals: dict[str, threading.Event] = {}
        self.task: asyncio.Task | None = None
        self.closing = False

    async def create_pull(self, url: str, settings, *, profile=None, mode="auto", scope="auto", spec=None, threshold=0.15):
        from .config import load_source_config
        from .sources import SourceClient
        from .safety import persistent_seed
        await asyncio.to_thread(self.store.require_consent)
        mode = ("match" if profile else "collect-pages") if mode == "auto" else mode
        if mode not in {"match", "collect-pages", "collect-panels"}:
            raise UserError("Choose auto, match, collect-pages or collect-panels.")
        model = {}
        if mode == "match":
            ready = await asyncio.to_thread(self.matcher.readiness)
            if not ready.get("ready"):
                raise UserError("Local model is not ready. Run INSTALL-VISION-WINDOWS.cmd then DOWNLOAD-MODEL-WINDOWS.cmd and /farmer doctor. No dummy detector will run. No-reference collect-pages does not need a model.")
            model = {key: ready[key] for key in ("model_id", "revision") if key in ready}
            model.update({"device": settings.device, "local_files_only": True,
                          "inference_options": {"tile_size": settings.tile_size, "max_tiles": settings.max_tiles,
                                                "max_detections": settings.detections_per_page,
                                                "tile_overlap": 0.2, "nms_iou": 0.5, "include_full_page": True}})
        elif profile:
            raise UserError("Collection does not use references. Omit the profile or choose match mode.")
        config = await asyncio.to_thread(load_source_config, settings)
        routing = set()
        if config.get("profiles"):
            source = SourceClient(config)
            try:
                seed = source.persistent_seed(url)
                routing.update(key for item in source.config.profiles for key in item.identity_query_params)
                routing.update(item.page_number_param for item in source.config.profiles if item.page_number_param)
            finally:
                await source.aclose()
        else:
            from urllib.parse import parse_qsl, urlsplit
            routing = {"page", "paged"}
            seed = persistent_seed(url, routing)
            fields = parse_qsl(urlsplit(seed).query)
            if len(fields) > 1 or any(not value.isascii() or not value.isdigit() or not 1 <= int(value) <= 1000000 for _, value in fields):
                raise UserError("Automatic source routing accepts only one positive page/paged number. Use an explicit profile for other nonsecret query routing.")
        spec = spec or crop_spec()
        if mode != "match" and spec["kind"] in {"reference", "face", "upper_body", "full_body", "panel"}:
            raise UserError("No-reference collection uses detected/tight native framing. Choose collect-panels for the labeled gutter heuristic; identity/anatomy needs supported evidence.")
        return await asyncio.to_thread(self.store.create_job, seed, scope, profile, spec, model, threshold,
                                       routing_keys=routing, storage_limit=settings.storage_mib * 1024 * 1024,
                                       mode=mode, source_config=config)

    async def _prepare_source(self, job):
        if self.source_preparer is None:
            return self.source_factory()
        config = job["source_config"] or None
        options = {}
        if self.source_settings is not None:
            from .config import load_source_config
            config = config or await asyncio.to_thread(load_source_config, self.source_settings)
            options["limits"] = {"max_pages": self.source_settings.discovery_pages, "max_issues": self.source_settings.discovery_issues}
            if config and config.get("profiles"):
                config = deepcopy(config)
                config["limits"] = {**config.get("limits", {}), **options["limits"]}
        proposal = await self.source_preparer(job["source_url"], existing_config=config,
                                              rights_confirmed=await asyncio.to_thread(self.store.has_consent), scope=job["scope"], **options)
        await asyncio.to_thread(self.store.set_source_proposal, job["id"], proposal.to_dict())
        if proposal.status in {"ambiguous", "unsupported"}:
            await asyncio.to_thread(self.store.set_phase, job["id"], "paused_source", proposal.code)
            return None
        if proposal.status == "needs_origin_approval":
            approved = job["approved_proposal"]
            if approved.get("proposal_id") != proposal.proposal_id or tuple(approved.get("required_origins", ())) != tuple(proposal.required_origins):
                await asyncio.to_thread(self.store.set_phase, job["id"], "awaiting_approval", "external_image_origins_need_approval")
                return None
            from .source_setup import approve_source
            proposal = approve_source(proposal, proposal_id=proposal.proposal_id, approved_origins=approved["required_origins"])
        await asyncio.to_thread(self.store.require_consent)
        return self.prepared_source_factory(proposal.config)

    async def start(self):
        if self.task is None:
            await asyncio.to_thread(self.store.recover)
            self.task = asyncio.create_task(self._loop(), name="techfarmer-durable-worker")

    async def schedule(self, job_id: str):
        if job_id not in self.scheduled:
            self.scheduled.add(job_id)
            self.signals[job_id] = threading.Event()
            await self.queue.put(job_id)

    def signal(self, job_id: str):
        if job_id in self.signals:
            self.signals[job_id].set()

    def signal_all(self):
        for event in self.signals.values():
            event.set()

    async def close(self):
        self.closing = True
        self.signal_all()
        if self.task is not None:
            await self.queue.put(None)
            await self.task
            self.task = None

    async def _loop(self):
        while True:
            job_id = await self.queue.get()
            try:
                if job_id is None:
                    return
                if self.closing:
                    await asyncio.to_thread(self.store.set_phase, job_id, "paused_restart", "shutdown")
                else:
                    await self.run_job(job_id)
            except asyncio.CancelledError:
                if job_id:
                    await asyncio.to_thread(self.store.set_phase, job_id, "paused_restart", "worker_interrupted")
                raise
            except Exception:
                if job_id:
                    await asyncio.to_thread(self.store.set_phase, job_id, "paused_error", "worker_error")
            finally:
                self.scheduled.discard(job_id)
                self.signals.pop(job_id, None)
                self.queue.task_done()
                if job_id and not self.closing:
                    latest = await asyncio.to_thread(self.store.job, job_id)
                    if latest["phase"] == "queued":
                        await self.schedule(job_id)

    async def _stopped(self, job_id: str, event) -> bool:
        job = await asyncio.to_thread(self.store.job, job_id)
        allowed = await asyncio.to_thread(self.store.has_consent)
        if event.is_set() or job["control"] or not allowed or self.closing:
            phase = "cancelled" if job["control"] == "cancel" else "paused_restart" if self.closing else "paused"
            await asyncio.to_thread(self.store.set_phase, job_id, phase, "consent_required" if not allowed else "control_requested")
            return True
        return False

    def _references(self, job):
        identity, framing = [], None
        for reference in job["refs"]:
            raw = self.store.read_local(reference["path"])
            if digest(raw) != reference["sha256"]:
                raise UserError("Frozen reference hash mismatch.")
            image, _ = decode(raw, reference=True)
            if reference["role"] == "identity":
                identity.append(image)
            else:
                framing = image.size
        if not identity:
            raise UserError("Frozen identity references are missing.")
        return identity, framing

    def _match_page(self, page, refs, job, framing, event, *, remaining_candidates=None, remaining_bytes=None):
        options = {"tile_size": self.limits.tile_size, "max_tiles": self.limits.max_tiles,
                   "max_detections": self.limits.detections_per_page, "include_full_page": True, "tile_overlap": 0.2, "nms_iou": 0.5}
        options.update(job.get("model", {}).get("inference_options", {}))
        options["cancel"] = event
        detections = self.matcher.detect(page, refs, threshold=job["threshold"], options=options)
        if remaining_candidates is None:
            remaining_candidates = self.limits.candidates_per_run
        if len(detections) > remaining_candidates:
            raise CandidateBudgetExceeded("This page exceeds the remaining candidate budget; the entire page stays pending.")
        disk_bytes = self.limits.storage_bytes if remaining_bytes is None else remaining_bytes
        encoded_limit = min(disk_bytes, self.limits.page_crop_bytes)
        results, encoded_bytes = [], 0
        for hit in detections:
            if event.is_set():
                from .vision import InferenceCancelled
                raise InferenceCancelled("Page cancelled before crop encoding completed.")
            try:
                item = render_candidate(page, hit, job["spec"], framing, byte_limit=max(0, encoded_limit - encoded_bytes))
            except CropBudgetExceeded:
                if disk_bytes <= self.limits.page_crop_bytes:
                    raise StorageBudgetExceeded("Remaining local storage cannot hold this page's native crops; the page stays pending.") from None
                raise
            encoded_bytes += len(item["png"] or b"")
            results.append(item)
        return results

    def _collect_page(self, page, job, event, *, remaining_candidates, remaining_bytes):
        boxes = [[0, 0, page.width, page.height]] if job["mode"] == "collect-pages" else suggest_panels(page)
        if len(boxes) > remaining_candidates:
            raise CandidateBudgetExceeded("The whole collection page stays pending because its output budget is exceeded.")
        budget = min(remaining_bytes, self.limits.page_crop_bytes)
        result, used = [], 0
        for box in boxes:
            if event.is_set():
                from .vision import InferenceCancelled
                raise InferenceCancelled("Collection cancelled at a safe page boundary.")
            warning = "Page collection only: no identity matching or model score." if job["mode"] == "collect-pages" else "White-gutter panel heuristic only: not semantic segmentation or character identity. Review missed/incorrect panels."
            detection = {"bbox": box, "score": 0.0, "reference_indices": [], "warnings": [warning]}
            try:
                item = render_candidate(page, detection, job["spec"], byte_limit=max(0, budget - used))
            except CropBudgetExceeded:
                if remaining_bytes <= self.limits.page_crop_bytes:
                    raise StorageBudgetExceeded("Native collection output exceeds remaining local storage; the whole page stays pending.") from None
                raise
            used += len(item["png"] or b"")
            item["decision"] = "collected" if item["png"] is not None else "pending"
            result.append(item)
        return result

    async def run_job(self, job_id: str):
        job = await asyncio.to_thread(self.store.job, job_id)
        if job["phase"] != "queued":
            return
        event = self.signals.setdefault(job_id, threading.Event())
        if await self._stopped(job_id, event):
            return
        ready, refs, framing = {}, [], None
        if job["mode"] == "match":
            try:
                await asyncio.to_thread(self.matcher.load)
                ready = await asyncio.to_thread(self.matcher.readiness)
                if any(job["model"].get(key) and job["model"][key] != ready.get(key) for key in ("model_id", "revision")):
                    raise UserError("Pinned model metadata differs from this frozen job; create a new job instead of mixing model revisions.")
                refs, framing = await asyncio.to_thread(self._references, job)
            except Exception:
                await asyncio.to_thread(self.store.set_phase, job_id, "paused_model", "model_or_reference_unavailable")
                return
        if await self._stopped(job_id, event):
            return
        source = None
        try:
            previous_pages = await asyncio.to_thread(self.store.pages, job_id)
            previous_keys = {page["page_key"] for page in previous_pages}
            await asyncio.to_thread(self.store.begin, job_id)
            source = await self._prepare_source(job)
            if source is None or await self._stopped(job_id, event):
                return

            discovered = set()
            current_fetch_ids = set()

            async def progress(manifest):
                fresh = []
                for page in manifest.get("pages", []):
                    key = page.get("page_key") or digest(json_text([page["issue_url"], page["page_number"], page["image_url"]]).encode())
                    current_fetch_ids.add(page.get("fetch_id", page["image_url"]))
                    if key not in discovered:
                        discovered.add(key)
                        fresh.append(page)
                await asyncio.to_thread(self.store.merge_manifest, job_id, {**manifest, "pages": fresh})

            cursor = None
            while True:
                manifest = await source.discover(job["source_url"], scope=job["scope"], progress=progress, cancel=event.is_set, cursor=cursor)
                await progress(manifest)
                cursor = manifest.get("cursor")
                if not cursor or manifest.get("complete") or event.is_set():
                    break
            if await self._stopped(job_id, event):
                return
            missing_known = previous_keys - discovered
            if missing_known:
                await asyncio.to_thread(self.store.merge_manifest, job_id, {"pages": [], "complete": False,
                    "problems": manifest.get("problems", []) + [{"code": "rediscovery_missing_known_pages", "count": len(missing_known)}]})
                await asyncio.to_thread(self.store.set_phase, job_id, "paused_source", "rediscovery_missing_known_pages")
                return
            await asyncio.to_thread(self.store.set_phase, job_id, "matching")
            processed = produced = 0
            for record in await asyncio.to_thread(self.store.pages, job_id, True):
                if await self._stopped(job_id, event):
                    return
                if processed >= self.limits.pages_per_run:
                    await asyncio.to_thread(self.store.set_phase, job_id, "paused_budget", "pages_per_run")
                    return
                if await asyncio.to_thread(self.store.storage_bytes) >= self.limits.storage_bytes:
                    await asyncio.to_thread(self.store.set_phase, job_id, "paused_budget", "storage_budget")
                    return
                try:
                    if record["fetch_id"] not in current_fetch_ids:
                        await asyncio.to_thread(self.store.fail_page, record["id"], "page_not_rediscovered")
                        continue
                    raw = await source.fetch_image(record["fetch_id"], cancel=event.is_set)
                    page, info = await asyncio.to_thread(decode, raw)
                    info["mode"] = job["mode"]
                    if job["mode"] == "match":
                        info["inference"] = {"model_id": ready.get("model_id"), "revision": ready.get("revision"),
                                             "device": job["model"].get("device", "test-injected"),
                                             **job["model"].get("inference_options", {"tile_size": self.limits.tile_size, "max_tiles": self.limits.max_tiles,
                                                 "max_detections": self.limits.detections_per_page, "tile_overlap": 0.2, "nms_iou": 0.5, "include_full_page": True}),
                                             "reference_ids": [ref["id"] for ref in job["refs"] if ref["role"] == "identity"]}
                    else:
                        info["inference"] = {"performed": False, "mode": job["mode"], "identity_claim": False}
                    duplicate = await asyncio.to_thread(self.store.duplicate, job_id, digest(raw), record["id"])
                    remaining_bytes = self.limits.storage_bytes - await asyncio.to_thread(self.store.storage_bytes) - len(raw)
                    if remaining_bytes < 0:
                        raise StorageBudgetExceeded("The original exceeds the remaining local storage budget.")
                    if duplicate:
                        crops = []
                    elif job["mode"] == "match":
                        crops = await asyncio.to_thread(self._match_page, page, refs, job, framing, event,
                                                       remaining_candidates=self.limits.candidates_per_run - produced, remaining_bytes=remaining_bytes)
                    else:
                        crops = await asyncio.to_thread(self._collect_page, page, job, event,
                                                       remaining_candidates=self.limits.candidates_per_run - produced, remaining_bytes=remaining_bytes)
                        if job["mode"] == "collect-panels" and not crops:
                            info["collection_warning"] = "No white-gutter panels identified; original page retained locally without an invented whole-page panel."
                    if await self._stopped(job_id, event):
                        return
                    if produced + len(crops) > self.limits.candidates_per_run:
                        await asyncio.to_thread(self.store.set_phase, job_id, "paused_budget", "candidates_per_run_increase_if_single_page")
                        return
                    required = len(raw) + sum(len(crop["png"] or b"") for crop in crops)
                    if await asyncio.to_thread(self.store.storage_bytes) + required > self.limits.storage_bytes:
                        await asyncio.to_thread(self.store.set_phase, job_id, "paused_budget", "storage_budget")
                        return
                    await asyncio.to_thread(self.store.save_page, record, raw, info, crops, duplicate, storage_limit=self.limits.storage_bytes)
                    processed += 1
                    produced += len(crops)
                except Exception as exc:
                    name = type(exc).__name__
                    if name == "InferenceCancelled" or event.is_set():
                        await self._stopped(job_id, event)
                        return
                    if name == "CandidateBudgetExceeded":
                        await asyncio.to_thread(self.store.set_phase, job_id, "paused_budget", "candidates_per_run_increase_if_single_page")
                        return
                    if name == "CropBudgetExceeded":
                        await asyncio.to_thread(self.store.set_phase, job_id, "paused_budget", "page_crop_memory_budget")
                        return
                    if name == "StorageBudgetExceeded":
                        await asyncio.to_thread(self.store.set_phase, job_id, "paused_budget", "storage_budget")
                        return
                    if name == "DetectionBudgetExceeded":
                        await asyncio.to_thread(self.store.set_phase, job_id, "paused_budget", "detections_per_page")
                        return
                    if name in {"ModelUnavailable", "ModelInferenceError", "VisionError"}:
                        await asyncio.to_thread(self.store.set_phase, job_id, "paused_model", "model_inference_failed")
                        return
                    await asyncio.to_thread(self.store.fail_page, record["id"], "source_fetch_or_image_failed")
            if await self._stopped(job_id, event):
                return
            summary = await asyncio.to_thread(self.store.summary, job_id)
            if not summary["discovery_complete"] or summary["source_problems"]:
                phase, code = "paused_source", "discovery_incomplete_or_blocked"
            elif summary["pages"].get("failed") or summary["pages"].get("pending"):
                phase, code = "review_partial", "page_failures_retry_with_resume"
            elif not sum(summary["pages"].values()):
                phase, code = "paused_source", "no_reader_pages"
            else:
                phase, code = "review_ready", ("all_discovered_pages_processed_not_identity_verified" if job["mode"] == "match" else "native_collection_complete_no_identity_claim")
            await asyncio.to_thread(self.store.set_phase, job_id, phase, code)
        except Exception as exc:
            if not await self._stopped(job_id, event):
                import re
                code = getattr(exc, "code", "discovery_failed_check_profile")
                if not isinstance(code, str) or not re.fullmatch(r"[a-zA-Z0-9_]{1,80}", code):
                    code = "discovery_failed_check_profile"
                await asyncio.to_thread(self.store.set_phase, job_id, "paused_source", "source_" + code)
        finally:
            if source is not None:
                close = getattr(source, "aclose", None) or getattr(source, "close", None)
                if close:
                    result = close()
                    if inspect.isawaitable(result):
                        await result

    def reframe(self, candidate_id: str, spec: dict):
        self.store.require_consent()
        item = self.store.candidate(candidate_id)
        job, record = self.store.job(item["job_id"]), self.store.page(item["page_id"])
        raw = self.store.read_local(record["original_path"])
        if digest(raw) != record["sha256"]:
            raise UserError("Original page changed on disk; reframing is blocked.")
        page, _ = decode(raw)
        framing = self._references(job)[1] if job["mode"] == "match" else None
        remaining = max(0, self.limits.storage_bytes - self.store.storage_bytes())
        try:
            cropped = render_candidate(page, item, spec, framing, byte_limit=min(remaining, self.limits.page_crop_bytes))
        except CropBudgetExceeded:
            if remaining <= self.limits.page_crop_bytes:
                raise StorageBudgetExceeded("Local storage budget reached; reframing left the original and candidate unchanged.") from None
            raise
        if cropped["png"] is None:
            raise UserError("This preset lacks supported evidence on this page. Choose detected/tight/reference or supply a valid framing reference; anatomy is not inferred by percentages.")
        self.store.replace_crop(candidate_id, cropped["box"], spec, cropped["png"], cropped["warnings"] + ["User requested a separate edited crop; original page and earlier crop files preserved."], storage_limit=self.limits.storage_bytes)
        return self.store.candidate(candidate_id)
