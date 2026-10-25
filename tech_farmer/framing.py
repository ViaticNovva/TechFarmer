"""Native-pixel crop planning; no resizing, segmentation or invented anatomy.

Boxes and panel evidence use original-image xyxy pixels, not xywh. Trim first,
then add padding, then apply aspect/alignment. Explicit trimmed edges are hard
limits: aspect expansion cannot put removed pixels back. ``contain`` preserves
that region or returns a review blocker; ``cover`` explicitly permits aspect
trimming. A crop reference guides aspect only, never pose or body-part semantics.
"""
from __future__ import annotations

from dataclasses import dataclass, fields
import math
from numbers import Real
from typing import Any, Mapping, Sequence

SIDES = ("left", "top", "right", "bottom")
KINDS = {"detected", "tight", "reference", "panel", "face", "upper_body", "full_body"}
SEMANTIC_KINDS = {"face", "upper_body", "full_body"}
ALIGNMENTS = {
    "center": ("center", "center"), "left": ("start", "center"),
    "right": ("end", "center"), "top": ("center", "start"),
    "bottom": ("center", "end"), "top-left": ("start", "start"),
    "top-right": ("end", "start"), "bottom-left": ("start", "end"),
    "bottom-right": ("end", "end"),
}


@dataclass(frozen=True)
class CropSpec:
    kind: str = "detected"
    aspect: float | str | None = None
    padding: float | Mapping[str, float] = 0
    trim: float | Mapping[str, float] = 0
    align: str = "center"
    keep_side: str | None = None
    fit: str = "contain"


def _finite(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, Real) or not math.isfinite(float(value)):
        raise ValueError(f"{name} must be a finite number.")
    return float(value)


def _size(value: Sequence[int], name: str) -> tuple[int, int]:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise ValueError(f"{name} must be (width, height).")
    values = [_finite(v, name) for v in value]
    if any(v <= 0 or not v.is_integer() for v in values):
        raise ValueError(f"{name} dimensions must be positive integers.")
    return int(values[0]), int(values[1])


def _box(value: Sequence[float], name: str = "bbox") -> list[float]:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        raise ValueError(f"{name} must be [left, top, right, bottom] in original pixels.")
    result = [_finite(v, name) for v in value]
    if result[2] <= result[0] or result[3] <= result[1]:
        raise ValueError(f"{name} must have positive width and height.")
    return result


def _sides(value: float | Mapping, name: str) -> dict[str, float]:
    if isinstance(value, Mapping):
        unknown = set(value) - set(SIDES)
        if unknown:
            raise ValueError(f"Unknown {name} sides: {', '.join(sorted(map(str, unknown)))}")
        result = {side: _finite(value.get(side, 0), name) for side in SIDES}
    else:
        number = _finite(value, name)
        result = {side: number for side in SIDES}
    if any(v < 0 for v in result.values()):
        raise ValueError(f"{name} must be nonnegative native-pixel amounts; use trim, not negative padding.")
    return result


def _ratio(value: float | str | None) -> float | None:
    if value is None:
        return None
    if isinstance(value, str):
        try:
            if ":" in value:
                parts = value.split(":")
                if len(parts) != 2:
                    raise ValueError
                numerator, denominator = map(float, parts)
                if not math.isfinite(numerator) or not math.isfinite(denominator) or numerator <= 0 or denominator <= 0:
                    raise ValueError
                result = numerator / denominator
            else:
                result = float(value)
        except (ValueError, ZeroDivisionError, OverflowError):
            raise ValueError("aspect must be a positive ratio or 'width:height'.") from None
    else:
        result = _finite(value, "aspect")
    if not math.isfinite(result) or result <= 0:
        raise ValueError("aspect must be finite and positive.")
    return result


def _spec(value: CropSpec | Mapping | None) -> CropSpec:
    if value is None:
        result = CropSpec()
    elif isinstance(value, CropSpec):
        result = value
    elif isinstance(value, Mapping):
        unknown = set(value) - {field.name for field in fields(CropSpec)}
        if unknown:
            raise ValueError(f"Unknown CropSpec fields: {', '.join(sorted(map(str, unknown)))}")
        result = CropSpec(**value)
    else:
        raise ValueError("spec must be CropSpec or a mapping.")
    if not isinstance(result.kind, str) or result.kind not in KINDS:
        raise ValueError("Unsupported crop kind.")
    if not isinstance(result.align, str) or result.align not in ALIGNMENTS:
        raise ValueError("align must be center, left/right/top/bottom, or a hyphenated corner.")
    if result.keep_side is not None and result.keep_side not in SIDES:
        raise ValueError("keep_side must be left, top, right or bottom.")
    if result.fit not in ("contain", "cover"):
        raise ValueError("fit must be contain or cover.")
    return result


def _review(message: str, warnings: list[str]) -> dict:
    return {"box": None, "warnings": list(dict.fromkeys(warnings + [message])), "needs_review": True}


def _anchor(start: int, end: int, length: int, alignment: str) -> int:
    if alignment == "start":
        return start
    if alignment == "end":
        return end - length
    return (start + end - length) // 2


def plan_crop(
    page_size: Sequence[int], bbox: Sequence[float], spec: CropSpec | Mapping | None = None,
    reference_size: Sequence[int] | None = None, panels: Sequence[Sequence[float]] | None = None,
) -> dict:
    """Return ``{box: integer xyxy | None, warnings, needs_review}``.

    ``None`` is a blocking unmet request, never a whole-page fallback. A valid box
    may also need review when requested padding/alignment cannot be honored.
    Apply it with ``page.crop(plan['box'])`` and save a separate PNG. No source
    image is accepted or mutated here, and crop output must not be upscaled.
    Semantic face/upper/full-body requests currently require review, because the
    image-guided identity backend provides no evidence for anatomical parts.
    """
    width, height = _size(page_size, "page_size")
    detected = _box(bbox)
    spec = _spec(spec)
    padding, trim = _sides(spec.padding, "padding"), _sides(spec.trim, "trim")
    for side in SIDES:
        if padding[side] and trim[side]:
            raise ValueError(f"Cannot both pad and trim {side}; express one net instruction for that edge.")
    ratio = _ratio(spec.aspect)
    guide = _size(reference_size, "reference_size") if reference_size is not None else None
    warnings: list[str] = []
    needs_review = False
    if spec.kind in SEMANTIC_KINDS:
        return _review(
            f"'{spec.kind}' requires actual anatomical/model evidence that this backend does not provide. "
            "No fixed rectangle fraction is face/body detection. Use detected/reference framing or review the request.", warnings,
        )
    if spec.kind == "reference" and guide is None:
        return _review("Reference framing needs a crop-reference image's dimensions; none were supplied.", warnings)
    if guide is not None:
        if ratio is None:
            ratio = guide[0] / guide[1]
            warnings.append("Crop-reference dimensions guide aspect only, not pose, anatomy or subject placement.")
        else:
            warnings.append("Explicit aspect overrides the crop-reference aspect; no pose or anatomical transfer is performed.")
    if spec.kind == "tight":
        warnings.append("Tight framing uses the detector rectangle, not pixel-perfect segmentation.")
    clipped = [max(0.0, detected[0]), max(0.0, detected[1]),
               min(float(width), detected[2]), min(float(height), detected[3])]
    if clipped[2] <= clipped[0] or clipped[3] <= clipped[1]:
        return _review("The detection does not intersect the original image.", warnings)
    if clipped != detected:
        warnings.append("The detected rectangle crossed page boundaries; clipped to existing native pixels.")
        needs_review = True
    base = clipped
    if spec.kind == "panel":
        if not panels:
            return _review("Full-panel framing requires supplied panel regions; no whole-page substitute was made.", warnings)
        containing = []
        for panel in panels:
            candidate = _box(panel, "panel")
            candidate = [max(0.0, candidate[0]), max(0.0, candidate[1]),
                         min(float(width), candidate[2]), min(float(height), candidate[3])]
            if (candidate[0] <= clipped[0] and candidate[1] <= clipped[1]
                    and candidate[2] >= clipped[2] and candidate[3] >= clipped[3]):
                containing.append(candidate)
        if not containing:
            return _review("No supplied panel fully contains this detection; review panel boundaries instead of substituting the page.", warnings)
        containing.sort(key=lambda b: ((b[2] - b[0]) * (b[3] - b[1]), tuple(b)))
        base = containing[0]
        warnings.append("Panel framing uses supplied panel geometry; review boundaries if that geometry is heuristic.")
        if len({tuple(panel) for panel in containing}) > 1:
            warnings.append("Multiple supplied panels contain the detection; the smallest was selected for review.")
            needs_review = True
    trimmed = [base[0] + trim["left"], base[1] + trim["top"],
               base[2] - trim["right"], base[3] - trim["bottom"]]
    if trimmed[2] <= trimmed[0] or trimmed[3] <= trimmed[1]:
        return _review("Requested trimming removes the entire region; no crop was generated.", warnings)
    limits = [math.ceil(trimmed[0]) if trim["left"] else 0,
              math.ceil(trimmed[1]) if trim["top"] else 0,
              math.floor(trimmed[2]) if trim["right"] else width,
              math.floor(trimmed[3]) if trim["bottom"] else height]
    padded = [trimmed[0] - padding["left"], trimmed[1] - padding["top"],
              trimmed[2] + padding["right"], trimmed[3] + padding["bottom"]]
    clipped_sides = [side for side, value, bound, is_start in zip(
        SIDES, padded, (0, 0, width, height), (True, True, False, False)
    ) if (value < bound if is_start else value > bound)]
    if clipped_sides:
        warnings.append("Requested padding clipped at page edge(s): " + ", ".join(clipped_sides) + ". No synthetic pixels were added.")
        needs_review = True
    if any(not v.is_integer() for v in list(padding.values()) + list(trim.values())):
        warnings.append("Subpixel padding/trim was rounded to native pixel boundaries.")
    box = [max(limits[0], math.floor(padded[0])), max(limits[1], math.floor(padded[1])),
           min(limits[2], math.ceil(padded[2])), min(limits[3], math.ceil(padded[3]))]
    if box[2] <= box[0] or box[3] <= box[1]:
        return _review("Requested framing leaves no complete native pixels.", warnings)
    x_align, y_align = ALIGNMENTS[spec.align]
    if spec.keep_side in ("left", "right"):
        x_align = "start" if spec.keep_side == "left" else "end"
    elif spec.keep_side in ("top", "bottom"):
        y_align = "start" if spec.keep_side == "top" else "end"
    if ratio is None:
        if spec.keep_side is not None and not any(trim.values()):
            warnings.append("keep_side is an alignment anchor, not a request to discard an unspecified fraction; specify aspect with fit='cover' or explicit side trim.")
            needs_review = True
        return {"box": box, "warnings": list(dict.fromkeys(warnings)), "needs_review": needs_review}
    old_width, old_height = box[2] - box[0], box[3] - box[1]
    if spec.fit == "contain":
        target_width, target_height = old_width, old_height
        if old_width / old_height < ratio:
            expanded = old_height * ratio
            if not math.isfinite(expanded):
                return _review("Requested aspect cannot fit inside this image at native resolution.", warnings)
            target_width = math.ceil(expanded - 1e-10)
        else:
            expanded = old_width / ratio
            if not math.isfinite(expanded):
                return _review("Requested aspect cannot fit inside this image at native resolution.", warnings)
            target_height = math.ceil(expanded - 1e-10)
        if target_width > limits[2] - limits[0] or target_height > limits[3] - limits[1]:
            return _review(
                "Requested aspect cannot contain the region within page/explicit-trim boundaries. "
                "Use fit='cover' to explicitly permit more trimming, or revise the aspect. No out-of-bounds crop was made.", warnings,
            )
    else:
        target_width, target_height = old_width, old_height
        if old_width / old_height < ratio:
            target_height = math.floor(old_width / ratio + 1e-10)
        else:
            target_width = math.floor(old_height * ratio + 1e-10)
        if min(target_width, target_height) < 1:
            return _review("Requested aspect leaves less than one native pixel; upscaling is not a substitute.", warnings)
        if (target_width, target_height) != (old_width, old_height):
            warnings.append("fit='cover' explicitly trims the detected/padded region to the requested aspect.")
    left = _anchor(box[0], box[2], target_width, x_align)
    top = _anchor(box[1], box[3], target_height, y_align)
    bounded_left = max(limits[0], min(left, limits[2] - target_width))
    bounded_top = max(limits[1], min(top, limits[3] - target_height))
    if (left, top) != (bounded_left, bounded_top):
        warnings.append("Aspect framing shifted inside the page/trim boundary; requested alignment could not be exact.")
        if spec.align != "center" or spec.keep_side is not None:
            needs_review = True
    box = [bounded_left, bounded_top, bounded_left + target_width, bounded_top + target_height]
    if not math.isclose(target_width / target_height, ratio, rel_tol=1e-9, abs_tol=1e-12):
        warnings.append("Requested aspect is approximated to whole native pixels (less than one pixel of rounding); no resizing was used.")
    return {"box": box, "warnings": list(dict.fromkeys(warnings)), "needs_review": needs_review}
