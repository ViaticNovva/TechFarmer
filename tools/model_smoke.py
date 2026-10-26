"""Manual pretrained API smoke on authored shapes, never an identity benchmark."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tech_farmer.vision import ReferenceMatcher
from tests.helpers import drawing


def main():
    matcher = ReferenceMatcher({"cache_dir": ".cache/models", "device": "cpu"})
    matcher.load()
    page = drawing(17, (256, 320))
    reference = page.crop((20, 24, 60, 80))
    before = page.tobytes(), reference.tobytes()
    candidates = matcher.detect(page, [reference], threshold=0.15, options={"include_full_page": True, "max_detections": 1000})
    assert before == (page.tobytes(), reference.tobytes())
    for item in candidates:
        x1, y1, x2, y2 = item["bbox"]
        assert 0 <= x1 < x2 <= page.width and 0 <= y1 < y2 <= page.height
    print(f"Pretrained pinned OWLv2 API executed locally; {len(candidates)} uncalibrated candidate(s).")
    print("Authored geometric fixture only. No comic-character identity/accuracy claim, no cloud image upload, no Discord connection.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
