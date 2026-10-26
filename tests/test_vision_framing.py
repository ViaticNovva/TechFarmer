"""Self-authored geometry/API fixtures, never a comic-recognition benchmark.

Test doubles are patched into private methods only. Production has no fixture
backend selector. The optional OWLv2 smoke uses random weights, not recognition.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import importlib.util
from io import BytesIO
import json
import os
from pathlib import Path
import shutil
from types import SimpleNamespace
import threading
import unittest
from unittest.mock import Mock, patch
from uuid import uuid4

from PIL import Image, ImageDraw

from tech_farmer import vision
from tech_farmer.framing import CropSpec, plan_crop
from tech_farmer.vision import (
    DetectionBudgetExceeded, DetectionOptions, InferenceCancelled, MatcherConfig,
    ModelInferenceError, ModelUnavailable, ReferenceMatcher, box_iou,
    deduplicate_detections, map_tile_box, plan_tiles,
)


def authored_image(size=(180, 140), mode="RGBA"):
    image = Image.new(mode, size, (230, 235, 241, 255) if mode == "RGBA" else (230, 235, 241))
    draw = ImageDraw.Draw(image)
    width, height = size
    draw.rectangle((int(width * 0.07), int(height * 0.12), int(width * 0.46), int(height * 0.77)), fill="navy")
    draw.ellipse((int(width * 0.18), int(height * 0.19), int(width * 0.37), int(height * 0.44)), fill="orange")
    return image


def png_bytes(image):
    output = BytesIO()
    image.save(output, "PNG")
    return output.getvalue()


@contextmanager
def local_fixture_directory():
    # Keep all test I/O inside this project, never a system temporary directory.
    directory = Path(__file__).resolve().parent / (".vision-fixture-" + uuid4().hex)
    directory.mkdir()
    try:
        yield directory
    finally:
        shutil.rmtree(directory)


class FramingTests(unittest.TestCase):
    def assert_native_box(self, result, page_size):
        box = result["box"]
        self.assertIsNotNone(box)
        self.assertTrue(all(isinstance(value, int) for value in box))
        self.assertTrue(0 <= box[0] < box[2] <= page_size[0])
        self.assertTrue(0 <= box[1] < box[3] <= page_size[1])

    def test_tiny_authored_smoke_fixture_needs_no_model_or_artwork(self):
        image = authored_image((32, 32), "RGB")
        self.assertEqual(image.size, (32, 32))
        self.assertGreater(len(image.getcolors(maxcolors=1024)), 1)

    def test_detected_outward_rounding_and_tight_are_real_boxes(self):
        detected = plan_crop((200, 160), [20.2, 30.6, 70.1, 90.01])
        self.assertEqual(detected["box"], [20, 30, 71, 91])
        tight = plan_crop((200, 160), [20, 30, 70, 90], {"kind": "tight"})
        self.assertEqual(tight["box"], [20, 30, 70, 90])
        self.assertTrue(any("not pixel-perfect" in warning for warning in tight["warnings"]))

    def test_asymmetric_padding_and_trim_are_not_reversed(self):
        padded = plan_crop((300, 300), [80, 90, 150, 180], {"padding": {"left": 10, "top": 20, "right": 30, "bottom": 40}})
        self.assertEqual(padded["box"], [70, 70, 180, 220])
        trimmed = plan_crop((300, 300), [80, 90, 150, 180], {"trim": {"left": 1, "top": 2, "right": 3, "bottom": 4}})
        self.assertEqual(trimmed["box"], [81, 92, 147, 176])
        self.assertFalse(trimmed["needs_review"])

    def test_padding_after_other_side_trimming(self):
        result = plan_crop((200, 200), [40, 40, 120, 120], {"trim": {"right": 20}, "padding": {"left": 12, "bottom": 8}})
        self.assertEqual(result["box"], [28, 40, 100, 128])

    def test_conflicting_same_side_pad_and_trim_rejected(self):
        with self.assertRaisesRegex(ValueError, "both pad and trim left"):
            plan_crop((100, 100), [20, 20, 70, 70], {"padding": {"left": 3}, "trim": {"left": 2}})

    def test_side_keep_changes_actual_cover_crop(self):
        left = plan_crop((240, 160), [20, 40, 180, 120], {"aspect": "1:1", "fit": "cover", "keep_side": "left"})
        right = plan_crop((240, 160), [20, 40, 180, 120], {"aspect": "1:1", "fit": "cover", "keep_side": "right"})
        self.assertEqual(left["box"], [20, 40, 100, 120])
        self.assertEqual(right["box"], [100, 40, 180, 120])
        self.assertFalse(left["needs_review"])

    def test_top_and_bottom_keep_differ(self):
        top = plan_crop((200, 240), [50, 20, 100, 200], {"aspect": 1, "fit": "cover", "keep_side": "top"})
        bottom = plan_crop((200, 240), [50, 20, 100, 200], {"aspect": 1, "fit": "cover", "keep_side": "bottom"})
        self.assertEqual(top["box"], [50, 20, 100, 70])
        self.assertEqual(bottom["box"], [50, 150, 100, 200])

    def test_corner_alignment_and_explicit_side_override(self):
        result = plan_crop((300, 300), [60, 70, 180, 130], {"aspect": 1, "fit": "cover", "align": "top-left", "keep_side": "right"})
        self.assertEqual(result["box"], [120, 70, 180, 130])

    def test_reference_aspect_actively_changes_output(self):
        bbox = [80, 80, 160, 160]
        portrait = plan_crop((300, 300), bbox, {"kind": "reference"}, reference_size=(100, 200))
        landscape = plan_crop((300, 300), bbox, {"kind": "reference"}, reference_size=(200, 100))
        self.assertEqual(portrait["box"], [80, 40, 160, 200])
        self.assertEqual(landscape["box"], [40, 80, 200, 160])
        self.assertNotEqual(portrait["box"], landscape["box"])
        self.assertTrue(any("aspect only" in warning for warning in portrait["warnings"]))

    def test_optional_guide_affects_detected_framing(self):
        result = plan_crop((300, 300), [80, 80, 160, 160], reference_size=(200, 100))
        self.assertEqual(result["box"], [40, 80, 200, 160])

    def test_explicit_aspect_overrides_reference(self):
        result = plan_crop((300, 300), [80, 80, 160, 160], {"kind": "reference", "aspect": "1:1"}, reference_size=(200, 100))
        self.assertEqual(result["box"], [80, 80, 160, 160])
        self.assertTrue(any("overrides" in warning for warning in result["warnings"]))

    def test_missing_reference_has_no_fallback(self):
        result = plan_crop((100, 100), [20, 20, 60, 60], {"kind": "reference"})
        self.assertIsNone(result["box"])
        self.assertTrue(result["needs_review"])

    def test_edge_padding_clips_without_negative_or_synthetic_pixels(self):
        result = plan_crop((100, 80), [2, 3, 95, 78], {"padding": 15})
        self.assertEqual(result["box"], [0, 0, 100, 80])
        self.assertTrue(result["needs_review"])
        self.assertTrue(any("clipped" in warning for warning in result["warnings"]))

    def test_contain_aspect_repositions_near_edge(self):
        result = plan_crop((100, 100), [0, 40, 20, 80], {"aspect": 1})
        self.assertEqual(result["box"], [0, 40, 40, 80])
        self.assertTrue(any("shifted" in warning for warning in result["warnings"]))

    def test_nondefault_alignment_constraint_reports_review(self):
        result = plan_crop((100, 100), [0, 40, 20, 80], {"aspect": 1, "keep_side": "right"})
        self.assertEqual(result["box"], [0, 40, 40, 80])
        self.assertTrue(result["needs_review"])

    def test_trimmed_edges_are_not_restored_by_aspect(self):
        result = plan_crop((200, 160), [40, 40, 120, 120], {"trim": {"left": 20}, "aspect": 1})
        self.assertEqual(result["box"], [60, 40, 140, 120])
        self.assertGreaterEqual(result["box"][0], 60)

    def test_impossible_contain_aspect_blocks_instead_of_wrong_crop(self):
        result = plan_crop((100, 100), [0, 10, 100, 90], {"aspect": 4})
        self.assertIsNone(result["box"])
        self.assertTrue(result["needs_review"])
        cover = plan_crop((100, 100), [0, 10, 100, 90], {"aspect": 4, "fit": "cover"})
        self.assertEqual(cover["box"][2] - cover["box"][0], 100)
        self.assertEqual(cover["box"][3] - cover["box"][1], 25)

    def test_trimming_entire_region_blocks(self):
        result = plan_crop((100, 100), [20, 20, 60, 60], {"trim": {"left": 40}})
        self.assertIsNone(result["box"])
        self.assertTrue(result["needs_review"])

    def test_degenerate_and_nonfinite_inputs_rejected(self):
        for bbox in ([10, 10, 10, 30], [10, 10, 20, float("nan")], [10, 10, 20, float("inf")]):
            with self.subTest(bbox=bbox), self.assertRaises(ValueError):
                plan_crop((100, 100), bbox)
        for size in ((0, 100), (100.5, 100), (True, 100)):
            with self.subTest(size=size), self.assertRaises(ValueError):
                plan_crop(size, [10, 10, 20, 20])

    def test_unknown_and_invalid_spec_fields_rejected(self):
        for spec in ({"manual_bbox": [0, 0, 5, 5]}, {"padding": -1}, {"trim": {"west": 1}},
                     {"aspect": "1:0"}, {"aspect": float("nan")}, {"aspect": "-1:-1"},
                     {"align": "guess"}, {"kind": "head_magic"}, {"fit": "upscale"}):
            with self.subTest(spec=spec), self.assertRaises(ValueError):
                plan_crop((100, 100), [20, 20, 60, 60], spec)

    def test_extreme_ratios_do_not_escape_bounds_or_upscale(self):
        for ratio in (1e-300, 1e300):
            for fit in ("contain", "cover"):
                with self.subTest(ratio=ratio, fit=fit):
                    result = plan_crop((100, 100), [20, 20, 60, 60], {"aspect": ratio, "fit": fit})
                    self.assertIsNone(result["box"])
                    self.assertTrue(result["needs_review"])

    def test_subpixel_trim_protects_removed_edge_and_warns(self):
        result = plan_crop((100, 100), [10.2, 10.2, 50.8, 50.8], {"trim": {"left": 0.2, "bottom": 0.2}})
        self.assertEqual(result["box"], [11, 10, 51, 50])
        self.assertTrue(any("Subpixel" in warning for warning in result["warnings"]))

    def test_native_aspect_rounding_is_reported(self):
        result = plan_crop((100, 100), [30, 30, 50, 50], {"aspect": "4:3"})
        self.assertEqual((result["box"][2] - result["box"][0], result["box"][3] - result["box"][1]), (27, 20))
        self.assertTrue(any("whole native pixels" in warning for warning in result["warnings"]))

    def test_semantic_requests_never_become_fractional_fake_anatomy(self):
        for kind in ("face", "upper_body", "full_body"):
            with self.subTest(kind=kind):
                result = plan_crop((200, 200), [30, 20, 100, 190], {"kind": kind}, reference_size=(100, 100))
                self.assertIsNone(result["box"])
                self.assertTrue(result["needs_review"])
                self.assertTrue(any("actual anatomical/model evidence" in warning for warning in result["warnings"]))

    def test_panel_needs_supplied_containing_evidence(self):
        no_panels = plan_crop((200, 200), [40, 40, 80, 100], {"kind": "panel"})
        missed = plan_crop((200, 200), [40, 40, 80, 100], {"kind": "panel"}, panels=[[100, 0, 200, 200]])
        for result in (no_panels, missed):
            self.assertIsNone(result["box"])
            self.assertTrue(result["needs_review"])
        found = plan_crop((200, 200), [40, 40, 80, 100], {"kind": "panel"}, panels=[[10, 20, 100, 140]])
        self.assertEqual(found["box"], [10, 20, 100, 140])

    def test_panel_ambiguity_chooses_smallest_and_warns(self):
        result = plan_crop((200, 200), [40, 40, 80, 100], {"kind": "panel"}, panels=[[0, 0, 160, 160], [10, 20, 100, 140]])
        self.assertEqual(result["box"], [10, 20, 100, 140])
        self.assertTrue(result["needs_review"])

    def test_off_page_detection_is_review_not_page_fallback(self):
        outside = plan_crop((100, 100), [200, 200, 300, 300])
        self.assertIsNone(outside["box"])
        partial = plan_crop((100, 100), [-10, 20, 40, 80])
        self.assertEqual(partial["box"], [0, 20, 40, 80])
        self.assertTrue(partial["needs_review"])

    def test_keep_side_alone_does_not_invent_half_rectangle(self):
        result = plan_crop((100, 100), [10, 10, 90, 90], {"keep_side": "left"})
        self.assertEqual(result["box"], [10, 10, 90, 90])
        self.assertTrue(result["needs_review"])

    def test_source_pixels_and_encoded_hash_unchanged_separate_png_native(self):
        original = authored_image()
        before_pixels = original.tobytes()
        before_hash = hashlib.sha256(png_bytes(original)).hexdigest()
        result = plan_crop(original.size, [30, 20, 100, 120], CropSpec(trim={"right": 10}, padding={"left": 5}))
        crop = original.crop(result["box"])
        self.assertEqual(result["box"], [25, 20, 90, 120])
        self.assertEqual(crop.size, (65, 100))
        self.assertEqual(crop.getpixel((0, 0)), original.getpixel((25, 20)))
        self.assertEqual(crop.tobytes(), original.crop((25, 20, 90, 120)).tobytes())
        self.assertEqual(original.tobytes(), before_pixels)
        self.assertEqual(hashlib.sha256(png_bytes(original)).hexdigest(), before_hash)
        with Image.open(BytesIO(png_bytes(crop))) as saved:
            self.assertEqual(saved.size, crop.size)
            self.assertEqual(saved.format, "PNG")
            self.assertEqual(saved.tobytes(), crop.tobytes())

    def test_geometry_grid_always_inside_page(self):
        for bbox in ([0, 0, 5, 10], [95, 70, 100, 80], [40.2, 30.4, 60.8, 50.1], [-10, -20, 40, 30]):
            for aspect in (None, "1:1", "4:3", "1:3"):
                for fit in ("contain", "cover"):
                    with self.subTest(bbox=bbox, aspect=aspect, fit=fit):
                        result = plan_crop((100, 80), bbox, {"aspect": aspect, "fit": fit, "padding": 5})
                        if result["box"] is not None:
                            self.assert_native_box(result, (100, 80))
                        else:
                            self.assertTrue(result["needs_review"])


class TileAndNmsTests(unittest.TestCase):
    def test_tile_coverage_overlap_and_bounds(self):
        windows, warnings = plan_tiles((700, 300), 256, overlap=0.2, max_tiles=12)
        self.assertFalse(warnings)
        self.assertLessEqual(len(windows), 12)
        self.assertEqual(min(box[0] for box in windows), 0)
        self.assertEqual(min(box[1] for box in windows), 0)
        self.assertEqual(max(box[2] for box in windows), 700)
        self.assertEqual(max(box[3] for box in windows), 300)
        for axis in (0, 1):
            intervals = sorted({(box[axis], box[axis + 2]) for box in windows})
            for first, second in zip(intervals, intervals[1:]):
                self.assertGreater(first[1], second[0])
        for box in windows:
            self.assertTrue(0 <= box[0] < box[2] <= 700)
            self.assertTrue(0 <= box[1] < box[3] <= 300)

    def test_tile_coarsening_does_not_drop_page_edges(self):
        windows, warnings = plan_tiles((5000, 4000), 256, max_tiles=4)
        self.assertLessEqual(len(windows), 4)
        self.assertTrue(any("coarsened" in warning for warning in warnings))
        for x in range(0, 5000, 97):
            for y in range(0, 4000, 97):
                self.assertTrue(any(a <= x < c and b <= y < d for a, b, c, d in windows))
        self.assertTrue(any(a <= 4999 < c and b <= 3999 < d for a, b, c, d in windows))

    def test_single_tile_budget_covers_whole_image(self):
        windows, warnings = plan_tiles((500, 400), 128, max_tiles=1)
        self.assertEqual(windows, [(0, 0, 500, 400)])
        self.assertTrue(warnings)

    def test_invalid_tiling_parameters_rejected(self):
        for size, overlap, budget in ((0, 0.2, 10), (256, 0.7, 10), (256, 0.2, 0), (256, 0.2, 33), (True, 0.2, 5)):
            with self.subTest(size=size, overlap=overlap, budget=budget), self.assertRaises(ValueError):
                plan_tiles((500, 400), size, overlap, budget)

    def test_map_native_tile_boxes_and_clip(self):
        box, warnings = map_tile_box([10, 20, 70, 90], [200, 300, 300, 450], (1000, 1000))
        self.assertEqual(box, [210, 320, 270, 390])
        self.assertFalse(warnings)
        box, warnings = map_tile_box([-10, -20, 200, 300], [200, 300, 300, 450], (1000, 1000))
        self.assertEqual(box, [200, 300, 300, 450])
        self.assertTrue(any("tile edge" in warning for warning in warnings))
        self.assertTrue(any("clipped" in warning for warning in warnings))

    def test_downscaled_input_maps_independent_axes_back_to_native_tile(self):
        box, warnings = map_tile_box([10, 20, 50, 100], [100, 200, 1401, 867], (2000, 1000), processed_size=(650, 333))
        expected = [100 + 10 * 1301 / 650, 200 + 20 * 667 / 333,
                    100 + 50 * 1301 / 650, 200 + 100 * 667 / 333]
        for actual, wanted in zip(box, expected):
            self.assertAlmostEqual(actual, wanted)
        self.assertFalse(warnings)

    def test_invalid_or_empty_model_box(self):
        box, _ = map_tile_box([20, 20, 20, 30], [0, 0, 100, 100], (100, 100))
        self.assertIsNone(box)
        with self.assertRaises(ModelInferenceError):
            map_tile_box([0, 0, float("nan"), 20], [0, 0, 100, 100], (100, 100))
        with self.assertRaises(ModelInferenceError):
            map_tile_box([50, 0, 20, 20], [0, 0, 100, 100], (100, 100))

    def test_iou_and_multireference_evidence_preserved(self):
        raw = [
            {"bbox": [10, 10, 60, 60], "score": 0.8, "reference_indices": [0], "warnings": ["a"], "evidence": [{"reference_index": 0}]},
            {"bbox": [11, 11, 61, 61], "score": 0.9, "reference_indices": [1], "warnings": ["b"], "evidence": [{"reference_index": 1}]},
            {"bbox": [100, 100, 150, 150], "score": 0.7, "reference_indices": [0], "warnings": [], "evidence": [{"reference_index": 0}]},
        ]
        before = json.dumps(raw)
        result = deduplicate_detections(raw)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["bbox"], [11, 11, 61, 61])
        self.assertEqual(result[0]["reference_indices"], [0, 1])
        self.assertEqual(len(result[0]["evidence"]), 2)
        self.assertEqual(set(result[0]["warnings"]), {"a", "b"})
        self.assertEqual(json.dumps(raw), before)
        self.assertEqual(box_iou([0, 0, 10, 10], [0, 0, 10, 10]), 1)
        self.assertEqual(box_iou([0, 0, 10, 10], [20, 20, 30, 30]), 0)

    def test_final_budget_exceeded_is_not_truncation(self):
        raw = [{"bbox": [i * 30, 0, i * 30 + 20, 20], "score": 0.9, "reference_indices": [0]} for i in range(3)]
        with self.assertRaisesRegex(DetectionBudgetExceeded, "page remains pending"):
            deduplicate_detections(raw, max_detections=2)
        self.assertEqual(len(deduplicate_detections(raw, max_detections=200)), 3)


class _TensorDouble:
    def __init__(self, value):
        self.value = value
        self.devices = []

    def to(self, device):
        self.devices.append(device)
        return self


class _TorchDouble:
    def __init__(self):
        self.active = 0
        self.mode_calls = 0
        self.cuda = SimpleNamespace(is_available=lambda: False, device_count=lambda: 0)
        self.backends = SimpleNamespace(mps=SimpleNamespace(is_available=lambda: False))

    @contextmanager
    def inference_mode(self):
        self.active += 1
        self.mode_calls += 1
        try:
            yield
        finally:
            self.active -= 1


class _ProcessorDouble:
    def __init__(self):
        self.queries = []
        self.post_calls = []
        self.tensors = []

    def __call__(self, *, images, query_images, return_tensors):
        self.queries.append({"page_size": images.size, "query_size": query_images.size,
                             "query_pixel": query_images.getpixel((0, 0)), "return_tensors": return_tensors})
        values = {"pixel_values": _TensorDouble(images.size),
                  "query_pixel_values": _TensorDouble(query_images.getpixel((0, 0)))}
        self.tensors.extend(values.values())
        return values

    def post_process_image_guided_detection(self, *, outputs, threshold, nms_threshold, target_sizes):
        self.post_calls.append({"threshold": threshold, "nms_threshold": nms_threshold, "target_sizes": target_sizes})
        return outputs


class _ModelDouble:
    def __init__(self, torch):
        self.torch = torch
        self.calls = []
        self.devices = []
        self.eval_called = False
        self.respond = lambda values: [{"boxes": [[10, 20, 60, 80]], "scores": [0.8]}]

    def to(self, device):
        self.devices.append(device)
        return self

    def eval(self):
        self.eval_called = True
        return self

    def image_guided_detection(self, **values):
        if not self.torch.active:
            raise AssertionError("image-guided API must run inside torch.inference_mode")
        if "query_pixel_values" not in values:
            raise AssertionError("identity query is missing")
        self.calls.append(values)
        return self.respond(values)


class ReferenceMatcherTests(unittest.TestCase):
    def setUp(self):
        self.torch = _TorchDouble()
        self.processor = _ProcessorDouble()
        self.model = _ModelDouble(self.torch)
        self.processor_cls = SimpleNamespace(from_pretrained=Mock(return_value=self.processor))
        self.model_cls = SimpleNamespace(from_pretrained=Mock(return_value=self.model))
        self.runtime = patch.object(vision, "_import_runtime", return_value=(self.torch, self.processor_cls, self.model_cls)).start()
        self.snapshot = patch.object(vision, "_snapshot_download", return_value=Path(__file__).resolve().parent / "unused-test-snapshot").start()
        self.validate = patch.object(vision, "_validate_snapshot").start()
        self.addCleanup(patch.stopall)
        self.matcher = ReferenceMatcher(MatcherConfig(device="cpu"))
        self.page = authored_image((180, 140))
        self.red = Image.new("RGB", (32, 32), "red")
        self.blue = Image.new("RGB", (40, 50), "blue")

    def test_queries_actively_feed_image_guided_model_and_multiref_nms(self):
        self.model.respond = lambda values: [{"boxes": [[10, 20, 60, 80] if values["query_pixel_values"].value == (255, 0, 0) else [12, 20, 62, 80]],
                                               "scores": [0.7 if values["query_pixel_values"].value == (255, 0, 0) else 0.9]}]
        result = self.matcher.detect(self.page, [self.red, self.blue], threshold=0.33)
        self.assertEqual(len(self.model.calls), 2)
        self.assertEqual([q["query_pixel"] for q in self.processor.queries], [(255, 0, 0), (0, 0, 255)])
        self.assertTrue(all(q["return_tensors"] == "pt" for q in self.processor.queries))
        self.assertEqual(result[0]["bbox"], [12, 20, 62, 80])
        self.assertEqual(result[0]["reference_indices"], [0, 1])
        self.assertEqual(len(result[0]["evidence"]), 2)
        self.assertEqual(self.processor.post_calls[0], {"threshold": 0.33, "nms_threshold": 0.5, "target_sizes": [(140, 180)]})
        self.assertTrue(all(tensor.devices == ["cpu"] for tensor in self.processor.tensors))
        self.assertEqual(self.torch.mode_calls, 2)
        self.assertTrue(any("not calibrated" in warning for warning in result[0]["warnings"]))

    def test_single_model_load_safe_local_defaults(self):
        self.matcher.detect(self.page, [self.red])
        self.matcher.detect(self.page, [self.blue])
        self.assertEqual(self.model_cls.from_pretrained.call_count, 1)
        self.assertEqual(self.processor_cls.from_pretrained.call_count, 1)
        for cls in (self.model_cls, self.processor_cls):
            kwargs = cls.from_pretrained.call_args.kwargs
            self.assertIs(kwargs["local_files_only"], True)
            self.assertIs(kwargs["trust_remote_code"], False)
            self.assertIs(kwargs["token"], False)
            self.assertEqual(kwargs["revision"], vision.MODEL_REVISION)
        self.assertIs(self.model_cls.from_pretrained.call_args.kwargs["use_safetensors"], True)
        self.assertIs(self.processor_cls.from_pretrained.call_args.kwargs["use_fast"], False)
        self.assertEqual(self.model.devices, ["cpu"])
        self.assertTrue(self.model.eval_called)
        self.assertTrue(all(call.kwargs["local_files_only"] for call in self.snapshot.call_args_list))

    def test_readiness_preflight_not_inference_claim(self):
        status = self.matcher.readiness()
        self.assertTrue(status["ready"])
        self.assertFalse(status["loaded"])
        self.assertEqual(status["code"], "cache_ready")
        self.assertIn("not been validated", status["message"])
        self.model_cls.from_pretrained.assert_not_called()
        self.assertTrue(self.snapshot.call_args.kwargs["local_files_only"])

    def test_explicit_prepare_is_only_network_capable_path(self):
        status = self.matcher.prepare_cache()
        self.assertTrue(status["prepared"])
        self.assertEqual([call.kwargs["local_files_only"] for call in self.snapshot.call_args_list], [False, True])
        self.model_cls.from_pretrained.assert_not_called()

    def test_missing_dependencies_block_jobs_without_snapshot_or_matches(self):
        self.runtime.side_effect = ModelUnavailable("Dependencies missing", code="dependencies_unavailable")
        status = self.matcher.readiness()
        self.assertFalse(status["ready"])
        self.assertEqual(status["code"], "dependencies_unavailable")
        self.assertTrue(status["action"])
        with self.assertRaises(ModelUnavailable):
            self.matcher.detect(self.page, [self.red])
        self.snapshot.assert_not_called()
        self.assertFalse(self.model.calls)

    def test_missing_cache_blocks_no_automatic_prepare(self):
        self.snapshot.side_effect = ModelUnavailable("No local cache", code="cache_unavailable")
        status = self.matcher.readiness()
        self.assertFalse(status["ready"])
        with self.assertRaises(ModelUnavailable):
            self.matcher.detect(self.page, [self.red])
        self.assertTrue(all(call.kwargs["local_files_only"] for call in self.snapshot.call_args_list))

    def test_failed_load_is_blocking_and_not_marked_loaded(self):
        self.model_cls.from_pretrained.side_effect = RuntimeError("synthetic out-of-memory")
        with self.assertRaises(ModelUnavailable) as error:
            self.matcher.load()
        self.assertEqual(error.exception.code, "load_failed")
        self.assertIsNone(self.matcher._model)
        self.assertFalse(self.model.calls)

    def test_explicit_unavailable_gpu_blocks_not_silent_cpu(self):
        matcher = ReferenceMatcher({"device": "cuda:0"})
        self.assertEqual(matcher.readiness()["code"], "device_unavailable")
        with self.assertRaises(ModelUnavailable):
            matcher.detect(self.page, [self.red])
        self.snapshot.assert_not_called()

    def test_auto_device_uses_cpu_when_no_gpu(self):
        matcher = ReferenceMatcher({"device": "auto"})
        matcher.load()
        self.assertEqual(matcher.readiness()["device"], "cpu")

    def test_model_failure_returns_no_partial_success(self):
        def respond(values):
            if values["query_pixel_values"].value == (0, 0, 255):
                raise RuntimeError("synthetic backend failure")
            return [{"boxes": [[10, 20, 60, 80]], "scores": [0.8]}]
        self.model.respond = respond
        with self.assertRaises(ModelInferenceError):
            self.matcher.detect(self.page, [self.red, self.blue])
        self.assertEqual(len(self.model.calls), 2)

    def test_malformed_scores_boxes_block(self):
        for response in ([{"boxes": [[1, 2, 3, 4]], "scores": []}],
                         [{"boxes": [[1, 2, float("nan"), 4]], "scores": [0.8]}],
                         [{"boxes": [[1, 2, 3, 4]], "scores": [float("nan")]}],
                         [{"other": []}]):
            with self.subTest(response=response):
                self.model.respond = lambda values: response
                with self.assertRaises(ModelInferenceError):
                    self.matcher.detect(self.page, [self.red])

    def test_no_detections_is_valid_not_fabricated(self):
        for response in ([], [{"boxes": [], "scores": []}], [{"boxes": [[1, 2, 3, 4]], "scores": [0.01]}]):
            self.model.respond = lambda values: response
            self.assertEqual(self.matcher.detect(self.page, [self.red], threshold=0.5), [])

    def test_tile_model_sizes_and_original_page_coordinate_mapping(self):
        page = authored_image((700, 300))
        windows, _ = plan_tiles(page.size, 256)
        result = self.matcher.detect(page, [self.red], options={"tile_size": 256, "include_full_page": False})
        expected = {(x1 + 10, y1 + 20, x1 + 60, y1 + 80) for x1, y1, _, _ in windows}
        self.assertEqual({tuple(item["bbox"]) for item in result}, expected)
        self.assertEqual(len(self.model.calls), len(windows))
        self.assertTrue(all(call["target_sizes"] == [(256, 256)] for call in self.processor.post_calls))
        self.assertTrue(all(item["reference_indices"] == [0] for item in result))

    def test_model_input_memory_bound_and_native_scale_back(self):
        page = authored_image((2560, 1280), "RGB")
        reference = Image.new("RGB", (1600, 800), "red")
        before = [hashlib.sha256(image.tobytes()).hexdigest() for image in (page, reference)]
        result = self.matcher.detect(page, [reference])
        self.assertEqual(self.processor.queries[0]["page_size"], (1280, 640))
        self.assertEqual(self.processor.queries[0]["query_size"], (1280, 640))
        self.assertEqual(self.processor.post_calls[0]["target_sizes"], [(640, 1280)])
        self.assertEqual(result[0]["bbox"], [20, 40, 120, 160])
        self.assertEqual(result[0]["evidence"][0]["tile_box"], [0, 0, 2560, 1280])
        self.assertEqual(before, [hashlib.sha256(image.tobytes()).hexdigest() for image in (page, reference)])

    def test_full_page_kept_with_bounded_tile_count(self):
        page = authored_image((700, 300))
        self.matcher.detect(page, [self.red], options={"tile_size": 128, "max_tiles": 2})
        self.assertLessEqual(len(self.model.calls), 3)
        self.assertEqual(self.processor.queries[0]["page_size"], page.size)

    def test_cancel_before_load(self):
        event = threading.Event()
        event.set()
        with self.assertRaises(InferenceCancelled):
            self.matcher.detect(self.page, [self.red], options={"cancel": event})
        self.runtime.assert_not_called()

    def test_cancel_between_references_does_not_return_partial_matches(self):
        event = threading.Event()
        def respond(values):
            event.set()
            return [{"boxes": [[10, 20, 60, 80]], "scores": [0.8]}]
        self.model.respond = respond
        with self.assertRaises(InferenceCancelled):
            self.matcher.detect(self.page, [self.red, self.blue], options={"cancel": event.is_set})
        self.assertEqual(len(self.model.calls), 1)

    def test_cancel_between_tiles(self):
        event = threading.Event()
        def respond(values):
            event.set()
            return [{"boxes": [[10, 20, 60, 80]], "scores": [0.8]}]
        self.model.respond = respond
        with self.assertRaises(InferenceCancelled):
            self.matcher.detect(authored_image((700, 300)), [self.red], options={"tile_size": 256, "cancel": event})
        self.assertEqual(len(self.model.calls), 1)

    def test_raw_budget_fail_closed(self):
        self.model.respond = lambda values: [{"boxes": [[1, 1, 10, 10], [20, 1, 30, 10], [40, 1, 50, 10]], "scores": [0.9, 0.8, 0.7]}]
        with patch.object(vision, "MAX_CANDIDATES", 2), self.assertRaises(DetectionBudgetExceeded):
            self.matcher.detect(self.page, [self.red])

    def test_final_budget_fail_closed(self):
        self.model.respond = lambda values: [{"boxes": [[1, 1, 10, 10], [20, 1, 30, 10]], "scores": [0.9, 0.8]}]
        with self.assertRaises(DetectionBudgetExceeded):
            self.matcher.detect(self.page, [self.red], options={"max_detections": 1})

    def test_unknown_fixture_backend_manual_boxes_and_oversized_refs_rejected(self):
        for config in ({"backend": "fixture"}, {"model_id": "different/model"}, {"revision": "main"}, {"device": "fixture"}):
            with self.subTest(config=config), self.assertRaises(ValueError):
                ReferenceMatcher(config)
        with self.assertRaises(ValueError):
            self.matcher.detect(self.page, [self.red], options={"manual_bbox": [0, 0, 10, 10]})
        with self.assertRaises(ValueError):
            self.matcher.detect(self.page, [self.red] * 9)
        with self.assertRaises(ValueError):
            self.matcher.detect(self.page, [])
        with self.assertRaises(ValueError):
            self.matcher.detect(self.page, [self.red], options={"tile_size": 0})
        with self.assertRaises(ValueError):
            self.matcher.detect(self.page, [self.red], options={"cancel": "no"})

    def test_page_and_reference_hashes_unchanged_by_inference(self):
        refs = [self.red, self.blue]
        originals = [self.page] + refs
        before = [hashlib.sha256(png_bytes(image)).hexdigest() for image in originals]
        self.matcher.detect(self.page, refs, options=DetectionOptions(tile_size=128))
        self.assertEqual(before, [hashlib.sha256(png_bytes(image)).hexdigest() for image in originals])


class CacheSafetyTests(unittest.TestCase):
    def test_pinned_public_snapshot_allowlist_no_code_pickle_or_token(self):
        downloader = Mock(return_value=str(Path(__file__).resolve().parent / "unused-cache"))
        hub = SimpleNamespace(snapshot_download=downloader)
        with patch.object(vision.importlib, "import_module", return_value=hub) as imported:
            vision._snapshot_download(None, local_files_only=True)
            vision._snapshot_download("local-model-cache", local_files_only=False)
        self.assertEqual([call.args for call in imported.call_args_list], [("huggingface_hub",), ("huggingface_hub",)])
        for call in downloader.call_args_list:
            self.assertEqual(call.kwargs["repo_id"], "google/owlv2-base-patch16-ensemble")
            self.assertEqual(call.kwargs["revision"], "410d70ced26e95c344915c2f10f4ecf967f2cde4")
            self.assertIs(call.kwargs["token"], False)
            self.assertTrue(all(not name.endswith((".py", ".bin", ".pt", ".pkl")) for name in call.kwargs["allow_patterns"]))
            self.assertIn("model.safetensors", call.kwargs["allow_patterns"])
        self.assertEqual([call.kwargs["local_files_only"] for call in downloader.call_args_list], [True, False])

    def test_bin_only_or_partial_cache_not_ready(self):
        with local_fixture_directory() as directory:
            for name in vision.REQUIRED_CACHE_FILES:
                if name != "model.safetensors":
                    (directory / name).write_text("self-authored structural fixture, not model data", encoding="utf-8")
            (directory / "pytorch_model.bin").write_bytes(b"never load this")
            with self.assertRaises(ModelUnavailable) as error:
                vision._validate_snapshot(directory)
            self.assertEqual(error.exception.code, "cache_incomplete")
            self.assertIn("model.safetensors", str(error.exception))
            (directory / "model.safetensors").write_bytes(b"")
            with self.assertRaises(ModelUnavailable):
                vision._validate_snapshot(directory)

    def test_simulated_315_missing_vision_blocks_before_cache_or_model_download(self):
        matcher = ReferenceMatcher()
        with patch.object(vision.sys, "version_info", (3, 15, 0)), patch.object(vision.importlib, "import_module", side_effect=ModuleNotFoundError("torch")), patch.object(vision, "_snapshot_download") as download:
            status = matcher.readiness()
            self.assertFalse(status["ready"])
            self.assertEqual(status["code"], "dependencies_unavailable")
            self.assertIn("no cp315 wheel", status["message"])
            with self.assertRaises(ModelUnavailable):
                matcher.prepare_cache()
        download.assert_not_called()

    def test_simulated_future_315_imports_are_not_permanently_version_banned(self):
        processor, model = object(), object()
        modules = {"torch": object(), "transformers": SimpleNamespace(Owlv2Processor=processor, Owlv2ForObjectDetection=model), "safetensors": object()}
        with patch.object(vision.sys, "version_info", (3, 15, 0)), patch.object(vision.importlib, "import_module", side_effect=lambda name: modules[name]):
            self.assertEqual(vision._import_runtime(), (modules["torch"], processor, model))

    def test_runtime_import_failure_has_actionable_error(self):
        with patch.object(vision.sys, "version_info", (3, 12, 15)), patch.object(vision.importlib, "import_module", side_effect=ModuleNotFoundError("torch")):
            with self.assertRaises(ModelUnavailable) as error:
                vision._import_runtime()
        self.assertEqual(error.exception.code, "dependencies_unavailable")
        self.assertIn("explicitly prepare", error.exception.action)


_REAL_OWLV2 = all(importlib.util.find_spec(name) is not None for name in ("torch", "transformers", "safetensors"))
if os.environ.get("TECH_FARMER_REQUIRE_VISION") == "1" and not _REAL_OWLV2:
    raise RuntimeError("Explicit vision CI requires actual Torch/Transformers/safetensors; missing packages are not a skipped success.")


@unittest.skipUnless(_REAL_OWLV2, "Optional local torch/transformers/safetensors unavailable; no real model/API/recognition run")
class RandomWeightOwlv2ApiSmoke(unittest.TestCase):
    def test_real_processor_and_tiny_random_model_image_guided_api_not_recognition(self):
        import torch
        from transformers import CLIPTokenizer, Owlv2Config, Owlv2ForObjectDetection, Owlv2ImageProcessor, Owlv2Processor

        with local_fixture_directory() as directory:
            (directory / "vocab.json").write_text(json.dumps({"<|startoftext|>": 0, "<|endoftext|>": 1, "a</w>": 2}), encoding="utf-8")
            (directory / "merges.txt").write_text("#version: 0.2\n", encoding="utf-8")
            tokenizer = CLIPTokenizer(vocab_file=str(directory / "vocab.json"), merges_file=str(directory / "merges.txt"), model_max_length=16)
            processor = Owlv2Processor(
                image_processor=Owlv2ImageProcessor(size={"height": 32, "width": 32}), tokenizer=tokenizer,
            )
            config = Owlv2Config(
                text_config={"vocab_size": 16, "hidden_size": 32, "intermediate_size": 64,
                             "num_hidden_layers": 1, "num_attention_heads": 4, "max_position_embeddings": 16},
                vision_config={"hidden_size": 32, "intermediate_size": 64, "num_hidden_layers": 1,
                               "num_attention_heads": 4, "image_size": 32, "patch_size": 16},
                projection_dim=32,
            )
            with torch.random.fork_rng(devices=[]):
                torch.manual_seed(17)
                model = Owlv2ForObjectDetection(config).eval()
                page, query = authored_image((32, 32), "RGB"), Image.new("RGB", (32, 32), "orange")
                inputs = processor(images=page, query_images=query, return_tensors="pt")
                self.assertIn("query_pixel_values", inputs)
                with torch.inference_mode():
                    outputs = model.image_guided_detection(**inputs)
                    result = processor.post_process_image_guided_detection(
                        outputs, threshold=0.0, nms_threshold=1.0, target_sizes=[(32, 32)],
                    )
                self.assertIsInstance(result, list)
                self.assertEqual(len(result), 1)
                self.assertEqual(result[0]["boxes"].shape[-1], 4)
                self.assertEqual(len(result[0]["boxes"]), len(result[0]["scores"]))


if __name__ == "__main__":
    unittest.main()
