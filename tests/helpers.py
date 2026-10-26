"""Procedural fixture data. FixtureMatcher is NEVER available as a production backend."""
from contextlib import contextmanager
from pathlib import Path
import errno
import time
from uuid import uuid4

from PIL import Image, ImageDraw
from tech_farmer.images import png

FIXTURE_BASE = Path(".runtime")


@contextmanager
def workspace():
    root = FIXTURE_BASE / ("test-" + uuid4().hex)
    root.mkdir(parents=True)
    try:
        yield root
    finally:
        def remove(path):
            if path.is_symlink() or not path.is_dir():
                path.unlink(missing_ok=True)
                return
            for child in path.iterdir():
                remove(child)
            path.rmdir()
        for attempt in range(5):
            try:
                if root.exists():
                    remove(root.resolve())
                break
            except OSError as error:
                if error.errno not in {errno.ENOENT, errno.ENOTEMPTY} or attempt == 4:
                    raise
                time.sleep(0.05)


def drawing(index=0, size=(96, 120)):
    image = Image.new("RGBA", size, (245, 245, 245, 255))
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, 2, 2), fill=(index % 256, 17, 23, 255))
    draw.rectangle((20, 24, 59, 79), fill=(20, 140 + index % 80, 70, 255))
    draw.ellipse((27, 30, 49, 52), fill="gold")
    draw.line((29, 45, 47, 45), fill="navy", width=2)
    return image


class FixtureMatcher:
    """Fixed answers for workflow testing, not a detector or recognition evidence."""
    def __init__(self, hits=1, failure=None):
        self.calls, self.hits, self.failure = [], hits, failure

    def readiness(self):
        return {"ready": True, "code": "fixture_only", "model_id": "test-fixture-not-a-model", "revision": "self-authored"}

    def load(self):
        if self.failure:
            raise self.failure

    def detect(self, page, refs, threshold=0.15, options=None):
        self.calls.append({"page": page.getpixel((0, 0)), "refs": [reference.getpixel((0, 0)) for reference in refs], "threshold": threshold, "options": options})
        return [{"bbox": [20 + i, 24, 60 + i, 80], "score": 0.8, "reference_indices": list(range(len(refs))), "warnings": ["Fixture output only; not model recognition."]} for i in range(self.hits)]


class FixtureSource:
    def __init__(self, count=3, fail_pages=(), complete=True, same_bytes=False):
        self.count, self.fail_pages, self.complete, self.same_bytes = count, set(fail_pages), complete, same_bytes
        self.fetched, self.discovered = [], 0
        self.closed = False

    async def discover(self, url, scope="auto", progress=None, cancel=None, cursor=None):
        self.discovered += 1
        result = {"pages": [], "issues": [], "problems": [], "complete": False, "cursor": None}
        for index in range(self.count):
            if cancel and cancel():
                return result
            result["pages"].append({"issue": "Self-authored fixture", "issue_url": "https://fixture.invalid/chapter/one", "page_number": index + 1, "image_url": f"https://fixture.invalid/images/{index}.png", "fetch_id": str(index)})
            if progress:
                await progress(result)
        result["complete"] = self.complete
        if not self.complete:
            result["problems"] = [{"code": "fixture_resource_pause"}]
        return result

    async def fetch_image(self, fetch_id, *, cancel=None):
        index = int(fetch_id)
        self.fetched.append(index)
        if index in self.fail_pages:
            raise ValueError("Fixture fetch failure; do not expose upstream text")
        return png(drawing(0 if self.same_bytes else index))

    async def aclose(self):
        self.closed = True
