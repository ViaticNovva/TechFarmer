"""Self-authored static-reader fixtures; all requests use an injected transport."""
from __future__ import annotations

from dataclasses import FrozenInstanceError, asdict, replace
import io
import json
import unittest
from unittest.mock import patch

from PIL import Image

from tech_farmer.source_setup import (
    SourceApprovalError, approve_source, inspect_source, prepare_source,
)
from tech_farmer.sources import SourceClient, SourceConfig, SourceError, SourceLimits, SourceProfile
from tests.test_sources import BOOKS, CDN, PNG, PUBLIC_IP, FixtureTransport, Reply


SEED = BOOKS + "/series/garden/chapter-1"
SERIES = BOOKS + "/series/garden"


def html(body: str):
    return Reply(("<!doctype html><html><body>" + body + "</body></html>").encode())


def chapter(*images, next_url=None):
    body = '<div class="reader">' + "".join('<img src="' + image + '">' for image in images) + '</div>'
    if next_url:
        body += '<a rel="next" href="' + next_url + '">Next</a>'
    return html(body)


def raster(fmt="PNG", size=(16, 12)):
    image = Image.new("RGB", size, (43, 116, 71))
    output = io.BytesIO()
    image.save(output, format=fmt)
    return output.getvalue()


class SourceSetupSafetyTests(unittest.IsolatedAsyncioTestCase):
    async def test_rights_are_required_even_for_public_url_and_explicit_profile(self):
        transport = FixtureTransport()
        for existing in (None, {"authorized": True, "profiles": [{"name": "existing", "page_origins": [BOOKS]}]}):
            result = await prepare_source(SEED, existing_config=existing, transport=transport)
            self.assertEqual((result.status, result.code), ("unsupported", "authorization_required"))
            self.assertIsNone(result.config)
        self.assertEqual(transport.calls, [])
        self.assertEqual(transport.resolutions, [])

    async def test_bad_urls_do_not_open_connections_or_echo_secrets(self):
        for url in (
            "https://127.0.0.1/p", "https://[::1]/p", "https://169.254.169.254/p",
            "https://localhost/p", "https://host.internal/p", "https://2130706433/p",
            "https://user:PRIVATE_PASSWORD@books.example/p", "ftp://books.example/p",
            "http://books.example/p", "https://books.example:8443/p",
            "https://books.example/a/../p", "https://books.example/%252e%252e/p",
            SEED + "?token=PRIVATE_TOKEN", SEED + "#PRIVATE_FRAGMENT",
            BOOKS + "/%2F", BOOKS + "/%2findex.html",
        ):
            with self.subTest(url=url):
                transport = FixtureTransport()
                proposal = await prepare_source(url, rights_confirmed=True, transport=transport)
                self.assertIn(proposal.status, {"unsupported", "ambiguous"})
                self.assertIsNone(proposal.config)
                self.assertFalse(transport.calls)
                self.assertNotIn("PRIVATE_", json.dumps(proposal.to_dict()))

    async def test_private_and_mixed_dns_fail_before_socket(self):
        for addresses in (("127.0.0.1",), (PUBLIC_IP, "10.0.0.7")):
            transport = FixtureTransport(dns={"books.example": addresses})
            result = await prepare_source(SEED, rights_confirmed=True, transport=transport)
            self.assertEqual(result.code, "unsafe_address")
            self.assertFalse(transport.calls)

    async def test_http_requires_explicit_opt_in(self):
        seed = "http://books.example/image"
        transport = FixtureTransport({seed: Reply(PNG, content_type="image/png")})
        result = await prepare_source(seed, rights_confirmed=True, allow_http=True, transport=transport)
        self.assertEqual(result.status, "ready")
        self.assertTrue(result.config["allow_http"])
        self.assertEqual([request["addresses"] for request in transport.requests], [(PUBLIC_IP,), (PUBLIC_IP,)])
        self.assertTrue(all(set(request["headers"]) == {"User-Agent", "Accept", "Accept-Encoding"} for request in transport.requests))

    async def test_response_robots_meta_header_and_access_restrictions_are_preserved(self):
        cases = (
            ({BOOKS + "/robots.txt": Reply(b"User-agent: *\nDisallow: /series/\n", content_type="text/plain")}, "robots_denied"),
            ({SEED: Reply(status=403)}, "access_denied"),
            ({SEED: Reply(status=429)}, "rate_limited"),
            ({SEED: Reply(b'<div class="reader"><img src="/images/1.png"></div>', headers={"X-Robots-Tag": "noai"})}, "site_restricted"),
            ({SEED: html('<meta name="robots" content="noimageindex"><div class="reader"><img src="/images/1.png"></div>')}, "site_restricted"),
            ({SEED: Reply(b"x", headers={"Content-Encoding": "gzip"})}, "compressed_response"),
        )
        for routes, code in cases:
            with self.subTest(code=code):
                transport = FixtureTransport(routes)
                result = await prepare_source(SEED, rights_confirmed=True, transport=transport)
                self.assertEqual(result.code, code)
                self.assertIsNone(result.config)
                self.assertEqual(transport.closed_responses, len(transport.calls))

    async def test_external_and_out_of_scope_redirects_are_not_followed(self):
        for target in (CDN + "/page.png", BOOKS + "/other/chapter", "https://127.0.0.1/private"):
            transport = FixtureTransport({SEED: Reply(status=302, headers={"Location": target})})
            result = await prepare_source(SEED, rights_confirmed=True, transport=transport)
            self.assertEqual(result.status, "unsupported")
            self.assertNotIn(target, transport.calls)
            self.assertEqual(len(transport.calls), 2)

    async def test_same_scope_redirect_uses_original_security_checks(self):
        final = SEED + "/reader"
        transport = FixtureTransport({SEED: Reply(status=302, headers={"Location": final}), final: chapter("/images/garden/1.png")})
        result = await prepare_source(SEED, rights_confirmed=True, transport=transport)
        self.assertEqual(result.status, "ready")
        self.assertEqual(transport.calls, [BOOKS + "/robots.txt", SEED, final])

    async def test_external_image_addresses_are_checked_without_artwork_requests(self):
        for image, dns, expected in (
            ("https://127.0.0.1/page.png", {}, "unsafe_address"),
            ("https://user:PRIVATE@images.example/page.png", {}, "invalid_url"),
            (CDN + "/page.png", {"images.example": ("192.168.1.2",)}, "unsafe_address"),
            (CDN + "/page.png", {"images.example": (PUBLIC_IP, "127.0.0.1")}, "unsafe_address"),
        ):
            with self.subTest(expected=expected):
                transport = FixtureTransport({SEED: chapter(image)}, dns=dns)
                result = await prepare_source(SEED, rights_confirmed=True, transport=transport)
                self.assertEqual(result.code, expected)
                self.assertNotIn(image, transport.calls)
                self.assertNotIn("PRIVATE", json.dumps(result.to_dict()))

    async def test_limits_never_turn_first_n_into_ready(self):
        cases = (
            ({SEED: chapter("/images/1.png", "/images/2.png")}, SourceLimits(max_pages=1), "inspection_limit"),
            ({SEED: chapter("/images/1.png", next_url=SEED + "/2")}, SourceLimits(max_documents_per_batch=1), "inspection_limit"),
            ({SEED: chapter("/images/1.png")}, SourceLimits(max_html_bytes=10), "response_too_large"),
            ({SEED: Reply(PNG, content_type="image/png")}, SourceLimits(max_image_bytes=10), "response_too_large"),
        )
        for routes, limits, code in cases:
            transport = FixtureTransport(routes)
            result = await prepare_source(SEED, rights_confirmed=True, transport=transport, limits=limits)
            self.assertEqual(result.code, code)
            self.assertIsNone(result.config)

    async def test_invalid_options_do_not_fetch(self):
        for options in ({"limits": {"max_pages": 0}}, {"allow_http": "yes"}, {"scope": "internet"}, {"existing_config": {"allow_private": True}}):
            transport = FixtureTransport()
            result = await prepare_source(SEED, rights_confirmed=True, transport=transport, **options)
            self.assertEqual(result.status, "unsupported")
            self.assertFalse(transport.calls)


class DirectAndLayoutTests(unittest.IsolatedAsyncioTestCase):
    async def test_extensionless_direct_raster_uses_mime_not_filename(self):
        for fmt, mime in (("PNG", "image/png"), ("JPEG", "image/jpeg"), ("WEBP", "image/webp")):
            body = raster(fmt)
            transport = FixtureTransport({SEED: Reply(body, content_type=mime)})
            result = await inspect_source(SEED, rights_confirmed=True, transport=transport)
            self.assertEqual((result.status, result.source_type, result.page_count), ("ready", "image", 1))
            async with SourceClient(result.config, transport) as client:
                manifest = await client.discover(SEED)
                self.assertTrue(manifest["complete"])
                self.assertEqual(await client.fetch_image(manifest["pages"][0]["fetch_id"]), body)

    async def test_bad_direct_images_fail_with_no_fallback(self):
        for body, mime, code in (
            (PNG, "image/jpeg", "image_type_mismatch"),
            (b"<svg></svg>", "image/svg+xml", "content_type"),
            (b"\x89PNG\r\n\x1a\ncorrupt", "image/png", "unsupported_image"),
            (raster(size=(16001, 1)), "image/png", "unsupported_image"),
        ):
            transport = FixtureTransport({SEED: Reply(body, content_type=mime)})
            result = await prepare_source(SEED, rights_confirmed=True, transport=transport)
            self.assertEqual(result.code, code)
            self.assertIsNone(result.config)

    async def test_lazy_original_srcset_picture_and_numbers_reuse_source_engine(self):
        transport = FixtureTransport({SEED: html('''
            <h2 class="chapter-title">Chapter 12</h2><div class="reading-content">
              <img src="/tiny.png" data-original="/images/garden/full.png" data-page-number="7">
              <img src="data:image/png;base64,AAAA" data-src="/images/garden/lazy.png" data-page-number="8">
              <picture><source type="image/webp" srcset="/images/garden/small.webp 320w, /images/garden/big.webp 1800w">
                <img src="/images/garden/preview.png" srcset="/images/garden/medium.png 900w" data-page-number="9"></picture>
              <div class="ad"><img src="https://ads.example/tracker.png"></div>
              <div class="cover"><img src="/covers/front.png"></div>
              <div class="recommendations"><img src="/recommendations/other.png"></div>
            </div>''')})
        result = await prepare_source(SEED, rights_confirmed=True, transport=transport)
        self.assertEqual((result.status, result.source_type, result.page_count), ("ready", "reader", 3))
        self.assertFalse(any("/images/" in call for call in transport.calls))
        async with SourceClient(result.config, transport) as client:
            manifest = await client.discover(SEED)
        self.assertTrue(manifest["complete"])
        self.assertEqual([page["page_number"] for page in manifest["pages"]], [7, 8, 9])
        self.assertEqual([page["image_url"] for page in manifest["pages"]], [BOOKS + "/images/garden/full.png", BOOKS + "/images/garden/lazy.png", BOOKS + "/images/garden/big.webp"])
        self.assertEqual({page["issue"] for page in manifest["pages"]}, {"Chapter 12"})

    async def test_data_page_number_metadata_is_not_replaced_with_issue_number(self):
        transport = FixtureTransport({SEED: html('<div data-reader><img src="/images/a.png" data-page="2"><img src="/images/b.png" data-page="4"></div>')})
        proposal = await prepare_source(SEED, rights_confirmed=True, transport=transport)
        self.assertEqual(proposal.status, "ready")
        self.assertEqual(proposal.config["profiles"][0]["page_number_attribute"], "data-page")
        async with SourceClient(proposal.config, transport) as client:
            manifest = await client.discover(SEED)
        self.assertEqual([page["page_number"] for page in manifest["pages"]], [2, 4])

    async def test_unknown_ambiguous_and_javascript_layouts_give_actionable_results(self):
        for body, status, code in (
            ('<main><img src="/advert.png"></main>', "unsupported", "unsupported_layout"),
            ('<div class="reader"><img src="/one.png"></div><div class="reader"><img src="/two.png"></div>', "ambiguous", "ambiguous_layout"),
            ('<div class="reader"><img src="/one.png"></div><div class="chapters"><a href="chapter-2">Chapter 2</a></div>', "ambiguous", "ambiguous_layout"),
            ('<script>renderReader()</script><p>Please enable JavaScript.</p>', "unsupported", "javascript_required"),
            ('<form><input type="password"></form><div class="reader"><img src="/one.png"></div>', "unsupported", "javascript_required"),
        ):
            transport = FixtureTransport({SEED: html(body)})
            result = await prepare_source(SEED, rights_confirmed=True, transport=transport)
            self.assertEqual((result.status, result.code), (status, code))
            self.assertIsNone(result.config)
            self.assertNotIn("attachment", result.message.lower())
        result = await prepare_source(BOOKS + "/", rights_confirmed=True, transport=FixtureTransport())
        self.assertEqual(result.code, "source_scope")

    async def test_explicit_scope_resolves_reader_vs_series_ambiguity(self):
        transport = FixtureTransport({SEED: html('<div class="reader"><img src="/images/a.png"></div><div class="chapters"><a href="' + SEED + '/child">Chapter 2</a></div>')})
        result = await prepare_source(SEED, rights_confirmed=True, scope="chapter", transport=transport)
        self.assertEqual((result.status, result.source_type, result.scope), ("ready", "reader", "issue"))
        self.assertIsNone(result.config["profiles"][0]["chapter_selector"])

    async def test_rel_next_reader_pagination_and_next_chapter_are_separate(self):
        second = SEED + "/2"
        other = SERIES + "/chapter-2"
        transport = FixtureTransport({SEED: chapter("/images/garden/1.png", next_url=second), second: chapter("/images/garden/2.png", next_url=other)})
        result = await prepare_source(SEED, rights_confirmed=True, transport=transport)
        self.assertEqual((result.status, result.page_count), ("ready", 2))
        self.assertNotIn(other, transport.calls)
        async with SourceClient(result.config, transport) as client:
            manifest = await client.discover(SEED)
        self.assertTrue(manifest["complete"])
        self.assertEqual(len(manifest["issues"]), 1)
        self.assertEqual(len(manifest["pages"]), 2)
        self.assertNotIn(other, transport.calls)

    async def test_pagination_collision_never_reassigns_another_chapters_pages(self):
        first, second = SERIES + "/ch1", SERIES + "/ch2"
        continuation = first + "/p2"
        for semantic in (False, True):
            with self.subTest(semantic=semantic):
                link = '<a ' + ('rel="next" ' if semantic else '') + 'href="' + continuation + '">Related</a>'
                transport = FixtureTransport({
                    SERIES: html('<div class="chapters"><a href="' + first + '">Chapter 1</a><a href="' + second + '">Chapter 2</a></div>'),
                    first: chapter("/art/1.png", next_url=continuation),
                    continuation: chapter("/art/2.png"),
                    second: html('<div class="reader"><img src="/art/3.png"></div>' + link),
                })
                proposal = await prepare_source(SERIES, rights_confirmed=True, transport=transport)
                self.assertEqual((proposal.status, proposal.page_count, proposal.chapter_count), ("ready", 3, 2))
                self.assertTrue(proposal.config["profiles"][0]["reader_next_within_issue"])
                async with SourceClient(proposal.config, transport) as client:
                    manifest = await client.discover(SERIES)
                self.assertTrue(manifest["complete"], manifest["problems"])
                self.assertEqual(len(manifest["pages"]), proposal.page_count)
                self.assertEqual([issue["page_count"] for issue in manifest["issues"]], [2, 1])

    async def test_explicit_page_number_path_is_bounded_without_guessing_next_url(self):
        first, second = SEED + "/page/1", SEED + "/page/2"
        transport = FixtureTransport({first: chapter("/art/1.png", next_url=second), second: chapter("/art/2.png")})
        proposal = await prepare_source(first, rights_confirmed=True, transport=transport)
        self.assertEqual((proposal.status, proposal.page_count), ("ready", 2))
        async with SourceClient(proposal.config, transport) as client:
            manifest = await client.discover(first)
        self.assertTrue(manifest["complete"])
        self.assertEqual(len(manifest["pages"]), 2)
        self.assertEqual(set(transport.calls), {BOOKS + "/robots.txt", first, second})

    async def test_page_scope_does_not_follow_reader_pagination(self):
        transport = FixtureTransport({SEED: chapter("/images/garden/1.png", next_url=SEED + "/2")})
        result = await prepare_source(SEED, scope="page", rights_confirmed=True, transport=transport)
        self.assertEqual((result.status, result.page_count), ("ready", 1))
        self.assertIsNone(result.config["profiles"][0]["reader_next_selector"])

    async def test_series_and_reader_pagination_skip_recommendations_without_guessing(self):
        chapter2, chapter10 = SERIES + "/chapter-2", SERIES + "/chapter-10"
        listing2, reader2 = SERIES + "/list/2", SEED + "/2"
        unrelated = BOOKS + "/series/other/chapter-1"
        transport = FixtureTransport({
            SERIES: html('<div class="chapter-list"><a href="' + chapter2 + '">Chapter 2</a><a href="' + SEED + '">Chapter 1</a><div class="recommendations"><a href="' + unrelated + '">Chapter 500</a></div></div><a rel="next" href="' + listing2 + '">More</a>'),
            listing2: html('<div class="chapters"><a href="' + chapter10 + '">Chapter 10</a></div>'),
            SEED: chapter("/images/garden/1.png", next_url=reader2),
            reader2: chapter("/images/garden/2.png", next_url=chapter2),
            chapter2: chapter("/images/garden/3.png", next_url=chapter10),
            chapter10: chapter("/images/garden/4.png"),
        })
        result = await prepare_source(SERIES, rights_confirmed=True, transport=transport)
        self.assertEqual((result.status, result.source_type, result.page_count, result.chapter_count), ("ready", "series", 4, 3))
        async with SourceClient(result.config, transport) as client:
            manifest = await client.discover(SERIES)
        self.assertTrue(manifest["complete"], manifest["problems"])
        self.assertEqual([issue["issue"] for issue in manifest["issues"]], ["Chapter 1", "Chapter 2", "Chapter 10"])
        self.assertEqual([issue["page_count"] for issue in manifest["issues"]], [2, 1, 1])
        self.assertNotIn(unrelated, transport.calls)
        self.assertFalse(any("/images/" in call for call in transport.calls))
        self.assertEqual(set(transport.calls), {BOOKS + "/robots.txt", SERIES, listing2, SEED, reader2, chapter2, chapter10})

    async def test_query_pagination_is_declared_but_query_chapter_routing_is_not_guessed(self):
        second = SEED + "?page=2"
        transport = FixtureTransport({SEED: chapter("/images/garden/1.png", next_url="?page=2"), second: chapter("/images/garden/2.png")})
        result = await prepare_source(SEED, rights_confirmed=True, transport=transport)
        self.assertEqual(result.status, "ready")
        self.assertEqual(result.config["profiles"][0]["page_number_param"], "page")
        async with SourceClient(result.config, transport) as client:
            self.assertEqual(client.persistent_seed(second), second)
            manifest = await client.discover(SEED)
        self.assertTrue(manifest["complete"])
        self.assertEqual([page["page_number"] for page in manifest["pages"]], [1, 2])
        result = await prepare_source(BOOKS + "/read?chapter=4", rights_confirmed=True, transport=FixtureTransport())
        self.assertEqual(result.code, "query_routing_required")
    async def test_catalog_and_reader_can_use_separate_explicit_pagination_keys(self):
        seed, listing2, reader2 = SERIES + "?paged=1", SERIES + "?paged=2", SEED + "?page=2"
        next_chapter = SERIES + "/chapter-2"
        transport = FixtureTransport({
            seed: html('<div class="chapters"><a href="' + SEED + '">Chapter 1</a></div><a rel="next" href="?paged=2">More</a>'),
            listing2: html('<div class="chapters"><a href="' + next_chapter + '">Chapter 2</a></div>'),
            SEED: chapter("/art/1.png", next_url="?page=2"),
            reader2: chapter("/art/2.png"), next_chapter: chapter("/art/3.png"),
        })
        proposal = await prepare_source(seed, rights_confirmed=True, transport=transport)
        self.assertEqual((proposal.status, proposal.page_count), ("ready", 3))
        self.assertEqual(proposal.config["profiles"][0]["page_number_param"], "page")
        self.assertEqual(proposal.config["profiles"][0]["identity_query_params"], ["paged"])
        async with SourceClient(proposal.config, transport) as client:
            self.assertEqual(client.persistent_seed(seed), seed)
            manifest = await client.discover(seed)
        self.assertTrue(manifest["complete"], manifest["problems"])
        self.assertEqual(len(manifest["pages"]), 3)


class OriginApprovalTests(unittest.IsolatedAsyncioTestCase):
    async def test_only_exact_discovered_external_origins_can_be_approved(self):
        image = CDN + "/garden/1.png"
        transport = FixtureTransport({SEED: chapter(image), image: Reply(PNG, content_type="image/png")})
        proposal = await prepare_source(SEED, rights_confirmed=True, transport=transport)
        self.assertEqual(proposal.status, "needs_origin_approval")
        self.assertEqual(proposal.required_origins, (CDN,))
        self.assertFalse(proposal.config["authorized"])
        self.assertEqual(transport.calls, [BOOKS + "/robots.txt", SEED])
        with self.assertRaises(SourceError):
            SourceClient(proposal.config, transport)
        for origins in ((), (CDN, "https://extra.example"), ("https://sub.images.example",), (CDN, CDN)):
            with self.assertRaises(SourceApprovalError):
                approve_source(proposal, proposal_id=proposal.proposal_id, approved_origins=origins)
        approved = approve_source(proposal.to_dict(), proposal_id=proposal.proposal_id, approved_origins=(CDN,))
        self.assertEqual(approved.status, "ready")
        self.assertEqual(approved.proposal_id, proposal.proposal_id)
        async with SourceClient(approved.config, transport) as client:
            manifest = await client.discover(SEED)
            self.assertTrue(manifest["complete"])
            self.assertNotIn(image, transport.calls)
            self.assertEqual(await client.fetch_image(manifest["pages"][0]["fetch_id"]), PNG)
        self.assertIn(CDN + "/robots.txt", transport.calls)
        self.assertIn(image, transport.calls)

    async def test_snapshot_is_immutable_independent_and_hash_bound(self):
        transport = FixtureTransport({SEED: chapter(CDN + "/garden/1.png")})
        proposal = await prepare_source(SEED, rights_confirmed=True, transport=transport)
        with self.assertRaises(FrozenInstanceError):
            proposal.status = "ready"
        copy = proposal.config
        copy["profiles"][0]["image_origins"].append("https://extra.example")
        self.assertNotIn("https://extra.example", proposal.config["profiles"][0]["image_origins"])
        for field in ("image_origins", "image_paths", "reader_selector"):
            saved = proposal.to_dict()
            saved["config"]["profiles"][0][field] = ["https://extra.example"] if field == "image_origins" else ["/"] if field == "image_paths" else "img"
            with self.assertRaises(SourceApprovalError):
                approve_source(saved, proposal_id=proposal.proposal_id, approved_origins=proposal.required_origins)
        with self.assertRaises(SourceApprovalError):
            approve_source(proposal, proposal_id="stale", approved_origins=proposal.required_origins)
        first = approve_source(proposal, proposal_id=proposal.proposal_id, approved_origins=proposal.required_origins)
        again = approve_source(first.to_dict(), proposal_id=first.proposal_id, approved_origins=first.required_origins)
        self.assertEqual(first.config, again.config)

    async def test_new_external_origin_cannot_replay_previous_approval(self):
        first_transport = FixtureTransport({SEED: chapter(CDN + "/garden/1.png")})
        first = await prepare_source(SEED, rights_confirmed=True, transport=first_transport)
        changed = await prepare_source(SEED, rights_confirmed=True, transport=FixtureTransport({SEED: chapter("https://new.example/garden/1.png")}))
        self.assertNotEqual(changed.proposal_id, first.proposal_id)
        with self.assertRaises(SourceApprovalError):
            approve_source(changed, proposal_id=first.proposal_id, approved_origins=first.required_origins)

    async def test_signed_images_stay_ephemeral_and_token_rotation_does_not_change_approval_scope(self):
        first_url = CDN + "/garden/page.png?token=PRIVATE_SIGNED_A&expires=87654321"
        second_url = CDN + "/garden/page.png?token=PRIVATE_SIGNED_B&expires=87654322"
        transport = FixtureTransport({SEED: chapter(first_url), first_url: Reply(PNG, content_type="image/png")})
        proposal = await prepare_source(SEED, rights_confirmed=True, transport=transport)
        second = await prepare_source(SEED, rights_confirmed=True, transport=FixtureTransport({SEED: chapter(second_url)}))
        self.assertEqual(proposal.proposal_id, second.proposal_id)
        self.assertNotIn("PRIVATE_", json.dumps(proposal.to_dict()) + repr(proposal))
        self.assertNotIn("expires", json.dumps(proposal.to_dict()))
        ready = approve_source(proposal, proposal_id=proposal.proposal_id, approved_origins=proposal.required_origins)
        async with SourceClient(ready.config, transport) as client:
            manifest = await client.discover(SEED)
            self.assertNotIn("PRIVATE_", json.dumps(manifest))
            self.assertEqual(await client.fetch_image(manifest["pages"][0]["fetch_id"]), PNG)
        self.assertIn(first_url, transport.calls)

    async def test_approved_profile_does_not_authorize_new_sibling_origin_or_image_path(self):
        transport = FixtureTransport({SEED: chapter(CDN + "/garden/page.png")})
        proposal = await prepare_source(SEED, rights_confirmed=True, transport=transport)
        ready = approve_source(proposal, proposal_id=proposal.proposal_id, approved_origins=proposal.required_origins)
        async with SourceClient(ready.config, transport) as client:
            for url in ("https://sub.images.example/garden/page.png", CDN + "/unrelated/page.png", BOOKS + "/unrelated/chapter"):
                with self.assertRaises(SourceError) as failure:
                    await client.fetch_image(url)
                self.assertEqual(failure.exception.code, "out_of_scope")
        self.assertEqual(transport.calls, [BOOKS + "/robots.txt", SEED])


class AdvancedProfileTests(unittest.IsolatedAsyncioTestCase):
    async def test_existing_explicit_selector_paths_queries_preserved_input_not_mutated(self):
        seed = BOOKS + "/read?chapter=7"
        image = CDN + "/licensed/art/7.png"
        profile = SourceProfile(
            "owned", (BOOKS,), (CDN, "https://unused.example"), ("/read",), ("/licensed/art",),
            reader_selector="article.authorized-page img", chapter_selector=None,
            reader_next_selector=None, series_next_selector=None,
            identity_query_params=("chapter",), image_identity_query_params=("page",),
            page_number_attribute="data-folio", issue_title_selector="h2",
        )
        existing = asdict(SourceConfig((profile,), True, limits=SourceLimits(max_pages=20)))
        before = json.dumps(existing, sort_keys=True)
        transport = FixtureTransport({seed: html('<h2>Issue 7</h2><article class="authorized-page"><img src="' + image + '" data-folio="4"></article>')})
        proposal = await prepare_source(seed, existing_config=existing, rights_confirmed=True, transport=transport)
        self.assertEqual(proposal.status, "needs_origin_approval")
        self.assertEqual(proposal.source_type, "configured")
        self.assertEqual(proposal.required_origins, (CDN,))
        self.assertEqual(before, json.dumps(existing, sort_keys=True))
        expected = json.loads(json.dumps(asdict(replace(profile, image_origins=(CDN,)))))
        self.assertEqual(proposal.config["profiles"], [expected])
        self.assertEqual(proposal.config["limits"], existing["limits"])
        self.assertNotIn("unused.example", json.dumps(proposal.to_dict()))
        self.assertEqual(transport.calls, [BOOKS + "/robots.txt", seed])

    async def test_existing_profile_does_not_preapprove_external_direct_seed_redirect(self):
        profile = SourceProfile("explicit", (BOOKS,), (CDN, BOOKS))
        config = SourceConfig((profile,), True)
        target = CDN + "/garden/page.png"
        transport = FixtureTransport({SEED: Reply(status=302, headers={"Location": target})})
        result = await prepare_source(SEED, config, True, transport)
        self.assertEqual(result.status, "unsupported")
        self.assertNotIn(target, transport.calls)

    async def test_explicit_external_html_routes_do_not_stream_image_bodies(self):
        external = "https://readers.example"
        target = external + "/garden/chapter-1"
        profile = SourceProfile("explicit", (BOOKS, external), (CDN,), chapter_selector=".chapters a", reader_selector=".reader img")
        transport = FixtureTransport({SERIES: html('<div class="chapters"><a href="' + target + '">Chapter 1</a></div>'), target: Reply(PNG, content_type="image/png")})
        result = await prepare_source(SERIES, SourceConfig((profile,), True), True, transport)
        self.assertEqual(result.status, "unsupported")
        self.assertEqual(result.code, "out_of_scope")
        # Two robots bodies and the catalog HTML; no external image body.
        self.assertEqual(transport.chunks, 3)

    async def test_advanced_direct_raster_still_passes_normal_pixel_and_decode_checks(self):
        profile = SourceProfile("explicit", (BOOKS,))
        for body in (b"\x89PNG\r\n\x1a\ncorrupt", raster(size=(16001, 1))):
            transport = FixtureTransport({SEED: Reply(body, content_type="image/png")})
            proposal = await prepare_source(SEED, SourceConfig((profile,), True), True, transport)
            self.assertEqual((proposal.status, proposal.code), ("unsupported", "unsupported_image"))

    async def test_existing_profile_limits_cannot_be_silently_overridden(self):
        profile = SourceProfile("explicit", (BOOKS,), reader_selector=".reader img")
        config = SourceConfig((profile,), True, limits=SourceLimits(max_pages=1))
        result = await prepare_source(SEED, config, True, FixtureTransport({SEED: chapter("/images/1.png", "/images/2.png")}), limits={"max_pages": 100})
        self.assertEqual(result.code, "inspection_limit")


if __name__ == "__main__":
    unittest.main()
