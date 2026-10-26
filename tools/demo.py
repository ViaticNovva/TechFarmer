"""Token-free procedural pipeline demo. Its injected fixture is NOT recognition evidence."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
import sys
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tech_farmer.engine import JobRunner, crop_spec
from tech_farmer.exports import export_job
from tech_farmer.images import png
from tech_farmer.sources import SourceClient, TransportResponse
from tech_farmer.store import Store
from tests.helpers import FixtureMatcher, drawing


class AuthoredTransport:
    """In-memory responses to the REAL safe source adapter; no sockets are opened."""
    def __init__(self):
        self.responses = {
            "https://fixture.example/robots.txt": ("text/plain", b"User-agent: *\nAllow: /\n"),
            "https://fixture.example/series": ("text/html", b'<html><div class="chapter-list"><a href="/chapter/1">Chapter one</a><a href="/chapter/2">Chapter two</a></div></html>'),
            "https://fixture.example/chapter/1": ("text/html", b'<html><div class="comic-reader"><img data-page-number="1" src="/images/1.png"><img data-page-number="2" src="/images/2.png"></div></html>'),
            "https://fixture.example/chapter/2": ("text/html", b'<html><div class="comic-reader"><img data-page-number="1" src="/images/3.png"></div></html>'),
        }
        for index in range(1, 4):
            self.responses[f"https://fixture.example/images/{index}.png"] = ("image/png", png(drawing(index)))

    async def resolve(self, host, port):
        assert host == "fixture.example" and port == 443
        return ("93.184.216.34",)

    @asynccontextmanager
    async def open(self, url, *, addresses, headers, timeout, connect_timeout):
        assert addresses == ("93.184.216.34",)
        assert not {key.lower() for key in headers} & {"cookie", "authorization", "referer"}
        content_type, raw = self.responses[url]

        async def chunks():
            for start in range(0, len(raw), 256):
                yield raw[start:start + 256]

        yield TransportResponse(status=200, headers={"Content-Type": content_type}, body=chunks())


def fixture_source():
    config = {"authorized": True, "profiles": [{"name": "self-authored-fixture", "page_origins": ["https://fixture.example"], "page_paths": ["/series", "/chapter/"], "image_paths": ["/images/"], "reader_selector": ".comic-reader img", "chapter_selector": ".chapter-list a[href]"}], "limits": {"max_pages_per_batch": 1, "max_documents_per_batch": 2}}
    return SourceClient(config, transport=AuthoredTransport())


async def run():
    root = Path(".runtime") / ("demo-" + uuid4().hex[:12])
    store = Store(101, 202, root / "data", root / "exports")
    store.consent(True)
    store.add_reference("FixtureCharacter", png(drawing(77)))
    store.add_reference("FixtureCharacter", png(drawing(88)))
    matcher = FixtureMatcher()
    runner = JobRunner(store, matcher, fixture_source)
    job = store.create_job("https://fixture.example/series", "series", "FixtureCharacter", crop_spec(), matcher.readiness())
    await runner.run_job(job["id"])
    summary = store.summary(job["id"])
    assert summary["phase"] == "review_ready", summary
    assert summary["pages"] == {"done": 3}, summary
    for item in store.candidates(job["id"]):
        store.decide(item["id"], "accepted")
    exported = export_job(store, job["id"], 8 * 1024 * 1024)
    assert exported.count == 3
    print("PASS: real SourceClient discovery/cursor/robots checks + SQLite worker + native pixels + private export manifest.")
    print("Injected fixed-answer fixture only: no model recognition, external artwork, Discord SDK connection or account action.")
    print("Generated local runtime originals/crops/provenance:", str(exported.folder.relative_to(Path.cwd())))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run()))
