"""Offline, reference-conditioned OWLv2 localization (not identity certification).

Importing geometry helpers does not import torch. Only ``prepare_cache`` may
contact the public model repository; readiness, load and detection are offline.
A production matcher has exactly one backend, never a fixture or pixel matcher.
"""
from __future__ import annotations

from dataclasses import dataclass, fields
import importlib
import math
from numbers import Real
from pathlib import Path
import re
import sys
import threading
from typing import Any, Callable, Mapping, Sequence

from PIL import Image

from . import VISION_PYTHON_NOTICE

MODEL_ID = "google/owlv2-base-patch16-ensemble"
MODEL_REVISION = "410d70ced26e95c344915c2f10f4ecf967f2cde4"
MODEL_LICENSE = "Apache-2.0"
MAX_REFERENCES = 8
MAX_IMAGE_PIXELS = 20_000_000
MAX_IMAGE_SIDE = 16_000
MAX_MODEL_INPUT_SIDE = 1280
MAX_TILES = 32
MAX_CANDIDATES = 4096
CACHE_FILES = (
    "config.json", "model.safetensors", "preprocessor_config.json",
    "processor_config.json", "special_tokens_map.json", "tokenizer_config.json",
    "tokenizer.json", "vocab.json", "merges.txt",
)
REQUIRED_CACHE_FILES = (
    "config.json", "model.safetensors", "preprocessor_config.json", "vocab.json", "merges.txt",
)
MODEL_NOTICE = (
    "OWLv2 is a generic image-guided object detector, not a certified comic-character "
    "identity or re-identification model. Review matches and missed appearances."
)
SCORE_NOTICE = "Model scores are ranking signals, not calibrated identity probabilities."
SETUP_ACTION = (
    "Install the documented local vision dependencies, explicitly prepare the pinned "
    "model cache with the setup/download command, then run doctor again. "
    "Harvest never downloads weights."
)


class VisionError(RuntimeError):
    """Blocking local-vision failure; never replace it with fabricated detections."""


class ModelUnavailable(VisionError):
    def __init__(self, message: str, *, code: str = "model_unavailable", action: str = SETUP_ACTION):
        super().__init__(message)
        self.code = code
        self.action = action


class ModelInferenceError(VisionError):
    """A real backend call failed or returned an unusable result."""


class DetectionBudgetExceeded(ModelInferenceError):
    """Page is incomplete; keep it pending instead of silently dropping matches."""


class InferenceCancelled(VisionError):
    """Cancellation is checked before loading and between serial model calls."""


@dataclass(frozen=True)
class MatcherConfig:
    cache_dir: str | Path | None = None
    device: str = "cpu"


@dataclass(frozen=True)
class DetectionOptions:
    tile_size: int | tuple[int, int] | None = None
    tile_overlap: float = 0.2
    max_tiles: int = 12
    include_full_page: bool = True
    nms_iou: float = 0.5
    max_detections: int = 50
    cancel: Callable[[], bool] | threading.Event | None = None


def _record(value: Any, cls: type) -> Any:
    if value is None:
        return cls()
    if isinstance(value, cls):
        return value
    if not isinstance(value, Mapping):
        raise ValueError(f"Expected {cls.__name__} or a mapping.")
    unknown = set(value) - {field.name for field in fields(cls)}
    if unknown:
        raise ValueError(f"Unknown {cls.__name__} fields: {', '.join(sorted(map(str, unknown)))}")
    return cls(**value)


def _number(value: Any, name: str, minimum: float = 0, maximum: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, Real) or not math.isfinite(float(value)):
        raise ValueError(f"{name} must be a finite number.")
    value = float(value)
    if value < minimum or (maximum is not None and value > maximum):
        raise ValueError(f"{name} is outside its permitted range.")
    return value


def _integer(value: Any, name: str, minimum: int, maximum: int) -> int:
    number = _number(value, name, minimum, maximum)
    if not number.is_integer():
        raise ValueError(f"{name} must be an integer.")
    return int(number)


def _page_size(size: Sequence[int]) -> tuple[int, int]:
    if not isinstance(size, (tuple, list)) or len(size) != 2:
        raise ValueError("Image size must be (width, height).")
    width, height = (_integer(v, "image dimension", 1, MAX_IMAGE_SIDE) for v in size)
    if width * height > MAX_IMAGE_PIXELS:
        raise ValueError(f"Images may contain at most {MAX_IMAGE_PIXELS:,} pixels.")
    return width, height


def _check_image(image: Image.Image) -> None:
    if not isinstance(image, Image.Image):
        raise ValueError("Local vision requires a decoded PIL image, not a URL or filename.")
    _page_size(image.size)


def _check_cancel(cancel: Callable[[], bool] | threading.Event | None) -> None:
    if cancel is not None:
        cancelled = cancel() if callable(cancel) else cancel.is_set()
        if cancelled:
            raise InferenceCancelled("Local inference cancelled; page is not complete.")


def _options(value: DetectionOptions | Mapping | None) -> DetectionOptions:
    options = _record(value, DetectionOptions)
    _number(options.tile_overlap, "tile_overlap", 0, 0.5)
    _integer(options.max_tiles, "max_tiles", 1, MAX_TILES)
    _integer(options.max_detections, "max_detections", 1, 1000)
    if not 0 < _number(options.nms_iou, "nms_iou", 0, 1) <= 1:
        raise ValueError("nms_iou must be greater than zero.")
    if not isinstance(options.include_full_page, bool):
        raise ValueError("include_full_page must be boolean.")
    if options.cancel is not None and not (
        callable(options.cancel) or callable(getattr(options.cancel, "is_set", None))
    ):
        raise ValueError("cancel must be a zero-argument callback or an Event.")
    return options


def _axis_starts(length: int, size: int, overlap: float) -> list[int]:
    if size >= length:
        return [0]
    stride = max(1, int(size * (1 - overlap)))
    starts = list(range(0, length - size + 1, stride))
    if starts[-1] != length - size:
        starts.append(length - size)
    return starts


def plan_tiles(
    page_size: Sequence[int], tile_size: int | tuple[int, int],
    overlap: float = 0.2, max_tiles: int = 12,
) -> tuple[list[tuple[int, int, int, int]], list[str]]:
    """Cover the entire page with bounded, overlapping native-pixel windows.

    Coarsening, rather than dropping excess windows, preserves page coverage.
    It cannot promise that small characters remain recognizable by the model.
    """
    width, height = _page_size(page_size)
    overlap = _number(overlap, "tile_overlap", 0, 0.5)
    max_tiles = _integer(max_tiles, "max_tiles", 1, MAX_TILES)
    if isinstance(tile_size, Real) and not isinstance(tile_size, bool):
        tw = th = _integer(tile_size, "tile_size", 128, 4096)
    elif isinstance(tile_size, (tuple, list)) and len(tile_size) == 2:
        tw, th = (_integer(v, "tile dimension", 128, 4096) for v in tile_size)
    else:
        raise ValueError("tile_size must be an integer or a (width, height) pair.")
    tw, th = min(width, tw), min(height, th)
    xs, ys = _axis_starts(width, tw, overlap), _axis_starts(height, th, overlap)
    requested = len(xs) * len(ys)
    while len(xs) * len(ys) > max_tiles:
        scale = max(1.05, math.sqrt(len(xs) * len(ys) / max_tiles))
        tw, th = min(width, math.ceil(tw * scale)), min(height, math.ceil(th * scale))
        xs, ys = _axis_starts(width, tw, overlap), _axis_starts(height, th, overlap)
    warnings = []
    if requested > max_tiles:
        warnings.append(
            f"Requested tiling needed {requested} windows; coarsened to {len(xs) * len(ys)} "
            f"to respect the {max_tiles}-tile budget. Small-character recall may decrease."
        )
    return [(x, y, x + tw, y + th) for y in ys for x in xs], warnings


def box_iou(a: Sequence[float], b: Sequence[float]) -> float:
    intersection = max(0.0, min(a[2], b[2]) - max(a[0], b[0])) * max(
        0.0, min(a[3], b[3]) - max(a[1], b[1])
    )
    area_a = max(0.0, a[2] - a[0]) * max(0.0, a[3] - a[1])
    area_b = max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])
    union = area_a + area_b - intersection
    return intersection / union if union > 0 else 0.0


def deduplicate_detections(
    detections: Sequence[Mapping], iou_threshold: float = 0.5, max_detections: int = 50,
) -> list[dict]:
    """Score-ordered NMS, retaining all contributing references and evidence."""
    if not 0 < _number(iou_threshold, "iou_threshold", 0, 1) <= 1:
        raise ValueError("iou_threshold must be greater than zero.")
    max_detections = _integer(max_detections, "max_detections", 1, 1000)
    if len(detections) > MAX_CANDIDATES:
        raise DetectionBudgetExceeded(
            f"More than {MAX_CANDIDATES} raw candidates; page remains pending. "
            "Raise the match threshold or reduce tiling/reference count and retry."
        )
    kept: list[dict] = []
    for raw in sorted(detections, key=lambda d: (-d["score"], tuple(d["bbox"]))):
        match = next((item for item in kept if box_iou(item["bbox"], raw["bbox"]) >= iou_threshold), None)
        if match is None:
            kept.append({
                "bbox": list(raw["bbox"]), "score": float(raw["score"]),
                "reference_indices": sorted(set(raw["reference_indices"])),
                "warnings": list(dict.fromkeys(raw.get("warnings", []))),
                "evidence": [dict(item) for item in raw.get("evidence", [])],
            })
        else:
            match["reference_indices"] = sorted(set(match["reference_indices"]) | set(raw["reference_indices"]))
            match["warnings"] = list(dict.fromkeys(match["warnings"] + list(raw.get("warnings", []))))
            match["evidence"].extend(dict(item) for item in raw.get("evidence", []))
    if len(kept) > max_detections:
        raise DetectionBudgetExceeded(
            f"{len(kept)} matches survive deduplication, above max_detections={max_detections}; "
            "page remains pending. Increase the explicit budget or refine the threshold."
        )
    return kept


def map_tile_box(
    box: Sequence[float], tile: Sequence[int], page_size: Sequence[int],
    processed_size: Sequence[int] | None = None,
) -> tuple[list[float] | None, list[str]]:
    """Map post-processed input pixels into the native tile and original page.

    Supply processed_size when the bounded model-input copy was downscaled;
    independent x/y factors account for whole-pixel thumbnail rounding.
    """
    width, height = _page_size(page_size)
    if len(box) != 4 or any(isinstance(v, bool) or not isinstance(v, Real) or not math.isfinite(float(v)) for v in box):
        raise ModelInferenceError("OWLv2 returned non-finite or malformed box coordinates.")
    if box[2] < box[0] or box[3] < box[1]:
        raise ModelInferenceError("OWLv2 returned reversed box coordinates.")
    tx1, ty1, tx2, ty2 = tile
    if not (0 <= tx1 < tx2 <= width and 0 <= ty1 < ty2 <= height):
        raise ValueError("Tile is outside the original page.")
    tw, th = tx2 - tx1, ty2 - ty1
    if processed_size is not None:
        input_width, input_height = _page_size(processed_size)
        box = [float(box[0]) * tw / input_width, float(box[1]) * th / input_height,
               float(box[2]) * tw / input_width, float(box[3]) * th / input_height]
        if any(not math.isfinite(value) for value in box):
            raise ModelInferenceError("OWLv2 box coordinates overflowed during native-pixel mapping.")
    local = [max(0.0, min(float(box[0]), tw)), max(0.0, min(float(box[1]), th)),
             max(0.0, min(float(box[2]), tw)), max(0.0, min(float(box[3]), th))]
    if local[2] <= local[0] or local[3] <= local[1]:
        return None, []
    warnings = []
    if local != list(box):
        warnings.append("Model box clipped to its source image/tile boundary.")
    if ((tx1 > 0 and local[0] <= 1) or (ty1 > 0 and local[1] <= 1)
            or (tx2 < width and local[2] >= tw - 1) or (ty2 < height and local[3] >= th - 1)):
        warnings.append("Detection touches an interior tile edge and may show only part of the character; review.")
    return [local[0] + tx1, local[1] + ty1, local[2] + tx1, local[3] + ty1], warnings


def _import_runtime() -> tuple[Any, Any, Any]:
    try:
        torch = importlib.import_module("torch")
        transformers = importlib.import_module("transformers")
        processor_cls = transformers.Owlv2Processor
        model_cls = transformers.Owlv2ForObjectDetection
        importlib.import_module("safetensors")
        return torch, processor_cls, model_cls
    except Exception as exc:
        raise ModelUnavailable(
            f"Local OWLv2 dependencies are unavailable or incompatible ({type(exc).__name__}). "
            "No model inference was run." + (" " + VISION_PYTHON_NOTICE if sys.version_info[:2] == (3, 15) else ""), code="dependencies_unavailable",
        ) from exc


def _snapshot_download(cache_dir: str | Path | None, *, local_files_only: bool) -> Path:
    try:
        hub = importlib.import_module("huggingface_hub")
        snapshot = hub.snapshot_download(
            repo_id=MODEL_ID, revision=MODEL_REVISION, token=False,
            cache_dir=str(cache_dir) if cache_dir is not None else None,
            allow_patterns=list(CACHE_FILES), local_files_only=local_files_only,
        )
    except Exception as exc:
        raise ModelUnavailable(
            ("Pinned safe model files are not available in the local cache. " if local_files_only
             else "Explicit model-cache preparation failed; no inference is available. ")
            + f"({type(exc).__name__})", code="cache_unavailable" if local_files_only else "download_failed",
        ) from exc
    return Path(snapshot)


def _validate_snapshot(snapshot: Path) -> None:
    try:
        missing = [name for name in REQUIRED_CACHE_FILES if not (snapshot / name).is_file()
                   or (snapshot / name).stat().st_size == 0]
    except OSError as exc:
        raise ModelUnavailable("Unable to inspect the local model cache.", code="cache_unavailable") from exc
    if missing:
        raise ModelUnavailable(
            "Pinned cache is incomplete (missing " + ", ".join(missing) + "). "
            "Pickle/.bin weights are never accepted as a fallback.", code="cache_incomplete",
        )


def _device(torch: Any, requested: str) -> str:
    if requested == "auto":
        if torch.cuda.is_available():
            return "cuda"
        mps = getattr(getattr(torch, "backends", None), "mps", None)
        return "mps" if mps is not None and mps.is_available() else "cpu"
    if requested.startswith("cuda"):
        available = torch.cuda.is_available()
        if ":" in requested:
            available = available and int(requested.split(":")[1]) < torch.cuda.device_count()
        if not available:
            raise ModelUnavailable("Requested CUDA device is unavailable.", code="device_unavailable", action="Select device='cpu' or configure a supported local CUDA device.")
    if requested == "mps":
        mps = getattr(getattr(torch, "backends", None), "mps", None)
        if mps is None or not mps.is_available():
            raise ModelUnavailable("Requested MPS device is unavailable.", code="device_unavailable", action="Select device='cpu' or configure a supported local MPS device.")
    return requested


def _model_image(image: Image.Image) -> Image.Image:
    # Bound preprocessing before OWLv2 can square-pad a very long, thin page.
    # This is a model-input copy, never the exported native-pixel crop.
    small = image.copy()
    small.thumbnail((MAX_MODEL_INPUT_SIDE, MAX_MODEL_INPUT_SIDE), Image.Resampling.LANCZOS)
    if small.mode in ("RGBA", "LA") or "transparency" in small.info:
        rgba = small.convert("RGBA")
        result = Image.new("RGB", small.size, "white")
        result.paste(rgba, mask=rgba.getchannel("A"))
        return result
    return small.convert("RGB")


def _list(value: Any) -> list:
    if hasattr(value, "detach"):
        value = value.detach().cpu()
    if hasattr(value, "tolist"):
        value = value.tolist()
    if not isinstance(value, (list, tuple)):
        raise ModelInferenceError("OWLv2 post-processing returned an unexpected result type.")
    return list(value)


class ReferenceMatcher:
    """One lazy model cache per matcher; model loading and inference are serialized.

    ``readiness`` checks dependencies/device/cache without loading weights. A true
    result with ``loaded=False`` is a preflight, not proof that inference works.
    ``load`` and ``detect`` may still fail (e.g. insufficient RAM), and must block
    the job. ``prepare_cache`` is for explicit user-controlled setup ONLY.
    """

    def __init__(self, config: MatcherConfig | Mapping | None = None):
        self.config = _record(config, MatcherConfig)
        if self.config.cache_dir is not None and (
            not isinstance(self.config.cache_dir, (str, Path)) or not str(self.config.cache_dir).strip()
        ):
            raise ValueError("cache_dir must be a path or None.")
        if not isinstance(self.config.device, str) or not re.fullmatch(r"cpu|auto|mps|cuda(?::\d+)?", self.config.device):
            raise ValueError("device must be cpu, auto, mps, cuda or cuda:N.")
        self._torch = self._processor = self._model = None
        self._loaded_device: str | None = None
        self._lock = threading.RLock()

    def _metadata(self) -> dict:
        return {"model_id": MODEL_ID, "revision": MODEL_REVISION, "license": MODEL_LICENSE,
                "device": self._loaded_device or self.config.device, "local_inference_only": True}

    def readiness(self) -> dict:
        with self._lock:
            if self._model is not None:
                return {**self._metadata(), "ready": True, "loaded": True, "code": "loaded",
                        "message": "Pinned local OWLv2 is loaded; recognition accuracy is not certified.", "action": ""}
            try:
                torch, _, _ = _import_runtime()
                device = _device(torch, self.config.device)
                _validate_snapshot(_snapshot_download(self.config.cache_dir, local_files_only=True))
            except ModelUnavailable as exc:
                return {**self._metadata(), "ready": False, "loaded": False, "code": exc.code,
                        "message": str(exc), "action": exc.action}
            return {**self._metadata(), "device": device, "ready": True, "loaded": False,
                    "code": "cache_ready", "message": "Offline dependency/cache preflight passed; weights are not loaded and inference has not been validated.",
                    "action": "Call load or run the local model smoke check before relying on this runtime."}

    def prepare_cache(self) -> dict:
        """Explicitly download only pinned public config/tokenizer/safetensors files.

        No images are accepted or transmitted. Runtime code never calls this method.
        Authentication is disabled even if the environment has an HF token.
        """
        with self._lock:
            torch, _, _ = _import_runtime()
            _device(torch, self.config.device)
            snapshot = _snapshot_download(self.config.cache_dir, local_files_only=False)
            _validate_snapshot(snapshot)
            return {**self.readiness(), "prepared": True, "snapshot": str(snapshot)}

    def load(self) -> None:
        with self._lock:
            if self._model is not None:
                return
            torch, processor_cls, model_cls = _import_runtime()
            device = _device(torch, self.config.device)
            snapshot = _snapshot_download(self.config.cache_dir, local_files_only=True)
            _validate_snapshot(snapshot)
            safe_local = dict(local_files_only=True, trust_remote_code=False, token=False,
                              revision=MODEL_REVISION)
            try:
                processor = processor_cls.from_pretrained(str(snapshot), use_fast=False, **safe_local)
                model = model_cls.from_pretrained(str(snapshot), use_safetensors=True, **safe_local)
                model.to(device)
                model.eval()
            except Exception as exc:
                raise ModelUnavailable(
                    f"Pinned local OWLv2 failed to load ({type(exc).__name__}); no inference was run.",
                    code="load_failed", action="Check dependency compatibility, the safetensors cache and available RAM; retry with device='cpu'. Model weights alone are roughly 620 MB.",
                ) from exc
            self._torch, self._processor, self._model = torch, processor, model
            self._loaded_device = device

    def detect(
        self, page: Image.Image, refs: Sequence[Image.Image], threshold: float = 0.15,
        options: DetectionOptions | Mapping | None = None,
    ) -> list[dict]:
        """Run actual image-guided inference for each reference and image window.

        Every reference becomes ``query_images`` input, not decoration or a hash.
        Returned boxes are floating-point xyxy in the original PIL page's pixels;
        references are zero-indexed. Original page/reference pixels are unchanged.
        A budget/model/cancellation failure never returns a partial result list.
        """
        _check_image(page)
        if not isinstance(refs, (list, tuple)) or not 1 <= len(refs) <= MAX_REFERENCES:
            raise ValueError(f"Supply between 1 and {MAX_REFERENCES} identity-reference images.")
        for reference in refs:
            _check_image(reference)
        threshold = _number(threshold, "threshold", 0, 1)
        options = _options(options)
        _check_cancel(options.cancel)
        full = (0, 0, page.width, page.height)
        warnings = [MODEL_NOTICE, SCORE_NOTICE]
        if options.tile_size is None:
            windows = [full]
        else:
            windows, tile_warnings = plan_tiles(page.size, options.tile_size, options.tile_overlap, options.max_tiles)
            warnings += tile_warnings
            if options.include_full_page and full not in windows:
                windows.insert(0, full)
        with self._lock:
            _check_cancel(options.cancel)
            self.load()
            _check_cancel(options.cancel)
            candidates: list[dict] = []
            for reference_index, reference in enumerate(refs):
                _check_cancel(options.cancel)
                query = _model_image(reference)
                for tile_index, tile in enumerate(windows):
                    _check_cancel(options.cancel)
                    image = _model_image(page.crop(tile))
                    try:
                        inputs = self._processor(images=image, query_images=query, return_tensors="pt")
                        inputs = {key: value.to(self._loaded_device) if hasattr(value, "to") else value
                                  for key, value in inputs.items()}
                        with self._torch.inference_mode():
                            outputs = self._model.image_guided_detection(**inputs)
                            processed = self._processor.post_process_image_guided_detection(
                                outputs=outputs, threshold=threshold, nms_threshold=options.nms_iou,
                                target_sizes=[(image.height, image.width)],
                            )
                        _check_cancel(options.cancel)
                        if not isinstance(processed, (list, tuple)) or len(processed) > 1:
                            raise ModelInferenceError("Expected at most one OWLv2 result for one page/reference call.")
                        if not processed:
                            del inputs, outputs, processed
                            continue
                        item = processed[0]
                        if not isinstance(item, Mapping) or "boxes" not in item or "scores" not in item:
                            raise ModelInferenceError("OWLv2 result has no boxes/scores.")
                        boxes, scores = _list(item["boxes"]), _list(item["scores"])
                        if len(boxes) != len(scores):
                            raise ModelInferenceError("OWLv2 returned inconsistent box/score counts.")
                        for box, score in zip(boxes, scores):
                            score = _number(score, "model score", 0, 1)
                            if score < threshold:
                                continue
                            mapped, box_warnings = map_tile_box(box, tile, page.size, processed_size=image.size)
                            if mapped is None:
                                continue
                            candidates.append({
                                "bbox": mapped, "score": score, "reference_indices": [reference_index],
                                "warnings": warnings + box_warnings,
                                "evidence": [{"reference_index": reference_index, "score": score,
                                              "bbox": mapped.copy(), "tile_index": tile_index, "tile_box": list(tile)}],
                            })
                            if len(candidates) > MAX_CANDIDATES:
                                raise DetectionBudgetExceeded(
                                    f"More than {MAX_CANDIDATES} raw candidates; page remains pending. "
                                    "Raise the threshold or reduce tiling/reference count."
                                )
                        del inputs, outputs, processed
                    except VisionError:
                        raise
                    except Exception as exc:
                        raise ModelInferenceError(
                            f"Local image-guided inference failed ({type(exc).__name__}); page is incomplete. "
                            "Check local runtime/device memory and retry; no fallback matches were generated."
                        ) from exc
            _check_cancel(options.cancel)
            return deduplicate_detections(candidates, options.nms_iou, options.max_detections)
