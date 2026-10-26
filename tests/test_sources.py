"""All content here is self-authored. Fixtures cannot open a real network socket."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass, field, replace
import importlib
import io
import json
import socket
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from PIL import Image

from tech_farmer.sources import (
    AiohttpTransport, SourceClient, SourceConfig, SourceError, SourceLimits,
    SourceProfile, TransportResponse, USER_AGENT, _PinnedResolver, _parse_robots,
    _response_headers, _srcset, _url, redact_url,
)


BOOKS = "https://books.example"
CDN = "https://images.example"
PUBLIC_IP = "93.184.216.34"
PUBLIC_V6 = "2606:4700:4700::1111"


def authored_png(color=(22, 144, 66)) -> bytes:
    image = Image.new("RGB", (12, 10), color)
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


PNG = authored_png()


@dataclass
class Reply:
    body: bytes | list[bytes] = b""
    status: int = 200
    content_type: str = "text/html"
    headers: dict | list[tuple[str, str]] = field(default_factory=dict)
    chunk_delay: float = 0
    failure: Exception | None = None


class FixtureTransport:
    """Explicit injection, not a production option that permits private addresses."""

    def __init__(self, routes=None, dns=None):
        self.routes = dict(routes or {})
        self.dns = dict(dns or {})
        self.calls = []
        self.resolutions = []
        self.requests = []
        self.chunks = 0
        self.closed_responses = 0
        self.open_times = []

    async def resolve(self, host, port):
        self.resolutions.append((host, port))
        value = self.dns.get(host, (PUBLIC_IP,))
        if isinstance(value, Exception):
            raise value
        return value

    @asynccontextmanager
    async def open(self, url, *, addresses, headers, timeout, connect_timeout):
        self.calls.append(url)
        self.open_times.append(time.monotonic())
        self.requests.append({"url": url, "addresses": addresses, "headers": dict(headers), "timeout": timeout, "connect_timeout": connect_timeout})
        reply = self.routes.get(url)
        if reply is None and url.endswith("/robots.txt"):
            reply = Reply(b"User-agent: *\nAllow: /\n", content_type="text/plain")
        if reply is None:
            raise AssertionError("Unlisted fixture request: production networking is never a fallback")
        if reply.failure:
            raise reply.failure
        response_headers = ({"Content-Type": reply.content_type, **reply.headers} if isinstance(reply.headers, dict)
                            else [("Content-Type", reply.content_type), *reply.headers])

        async def chunks():
            for chunk in [reply.body] if isinstance(reply.body, bytes) else reply.body:
                if reply.chunk_delay:
                    await asyncio.sleep(reply.chunk_delay)
                self.chunks += 1
                yield chunk
        try:
            yield TransportResponse(reply.status, response_headers, chunks())
        finally:
            self.closed_responses += 1


def config(*, limits=None, identity_query_params=(), allow_http=False, **profile_changes):
    profile = SourceProfile(
        "fixture", (BOOKS,), (CDN, BOOKS), reader_selector=".reader img, .reader picture source",
        chapter_selector=".chapters a[href]", series_next_selector="a.series-next[href]",
        reader_next_selector="a.page-next[href]", identity_query_params=identity_query_params,
    )
    return SourceConfig((replace(profile, **profile_changes),), True, allow_http, limits or SourceLimits())


def reader(*urls, next_url=None):
    images = "".join(f'<img src="{url}">' for url in urls)
    next_link = f'<a class="page-next" href="{next_url}">Next page</a>' if next_url else ""
    return Reply(f'<html><div class="reader">{images}</div>{next_link}</html>'.encode())


def codes(manifest):
    return {problem["code"] for problem in manifest["problems"]}


class UrlAndConfigurationTests(unittest.TestCase):
    def test_private_encoded_and_credentialed_urls_rejected(self):
        urls = [
            "https://127.0.0.1/a", "https://10.4.5.6/a", "https://172.16.0.1/a",
            "https://192.168.1.2/a", "https://169.254.169.254/latest/meta-data/",
            "https://100.100.100.200/a", "https://0.0.0.0/a", "https://198.18.0.1/a",
            "https://224.0.0.1/a", "https://[::1]/a", "https://[fd00::1]/a",
            "https://[fec0::1]/a", "https://[feff:ffff::1]/a", "https://[fe80::1]/a", "https://[::ffff:127.0.0.1]/a",
            "https://[64:ff9b::a9fe:a9fe]/a", "https://[2002:7f00:1::]/a",
            "https://[fe80::1%25eth0]/a", "https://127.1/a", "https://2130706433/a",
            "https://0x7f000001/a", "https://0177.0.0.1/a", "https://127.0.0.0x1/a",
            "https://%31%32%37.0.0.1/a", "https://localhost/a", "https://localhost./a",
            "https://sub.localhost/a", "https://metadata.google.internal/a",
            "https://user:password@books.example/a", "https://books.example:8443/a",
            "https://books.example:80/a", "http://books.example/a",
            "file:///etc/passwd", "ftp://books.example/a", "gopher://books.example/a",
            "https://books.example\\@127.0.0.1/a", "https://books.example/%0d%0aHeader",
            "https://books.example/%252e%252e/private", "https://books.example/a/../secret",
            "https://books.example/%ZZ/a", " https://books.example/a",
        ]
        for url in urls:
            with self.subTest(url=url), self.assertRaises(SourceError):
                _url(url)

    def test_public_hosts_ipv6_normalization_and_redaction(self):
        self.assertEqual(_url("https://BOOKS.example:443/read/1?token=SECRET#page"), BOOKS + "/read/1?token=SECRET")
        self.assertEqual(redact_url(BOOKS + "/read/1?token=SECRET#page"), BOOKS + "/read/1")
        self.assertEqual(redact_url("https://user:SECRET@books.example/a"), "")
        self.assertEqual(_url("https://[2606:4700:4700:0:0:0:0:1111]/a"), f"https://[{PUBLIC_V6}]/a")
        self.assertEqual(_url("http://books.example:80/a", allow_http=True), "http://books.example/a")

    def test_configuration_requires_authorization_and_explicit_scope(self):
        with self.assertRaises(SourceError) as error:
            SourceClient(SourceConfig())
        self.assertEqual(error.exception.code, "authorization_required")
        for bad in [
            {"authorized": True, "profiles": []},
            {"authorized": True, "profiles": [{"name": "wild", "page_origins": ["https://*.example"]}]},
            {"authorized": True, "profiles": [{"name": "path", "page_origins": [BOOKS + "/read/"]}]},
            {"authorized": True, "profiles": [{"name": "bad", "page_origins": [BOOKS]}], "allow_private": True},
        ]:
            with self.subTest(config=bad), self.assertRaises(SourceError):
                SourceClient(bad)
        for limits in [SourceLimits(max_pages=0), SourceLimits(request_timeout=float("nan")), SourceLimits(max_redirects=11)]:
            with self.assertRaises(SourceError):
                SourceClient(config(limits=limits))

    def test_mapping_configuration_and_semantic_identity_keys(self):
        client = SourceClient({"authorized": True, "profiles": [{"name": "mapping", "page_origins": [BOOKS], "identity_query_params": ["chapter"]}], "limits": {"max_pages": 8}})
        self.assertEqual(client.limits.max_pages, 8)
        self.assertEqual(client._profiles[0].image_origins, (BOOKS,))

    def test_srcset_commas_descriptors_and_bad_schemes(self):
        self.assertEqual(max(_srcset("a.png 100w, b.png?crop=1,2 900w"))[1], "b.png?crop=1,2")
        self.assertEqual(max(_srcset("tiny.png, full.png 2x"))[1], "full.png")
        self.assertFalse(_srcset("data:image/png;base64,AAAA 2x"))
        self.assertFalse(_srcset("bad.png 0w, bad.png 2h, bad.png -1x"))

    def test_robots_wildcard_anchor_and_user_agent_specificity(self):
        rules = _parse_robots(b"User-agent: *\nDisallow: /\n\nUser-agent: TechFarmer\nDisallow: /*.png*\nAllow: /images/ok.png\n", SourceLimits())
        self.assertTrue(rules.allows(BOOKS + "/reader"))
        self.assertFalse(rules.allows(BOOKS + "/images/no.png?token=x"))
        self.assertTrue(rules.allows(BOOKS + "/images/ok.png"))
        anchored = _parse_robots(b"User-agent: *\nDisallow: /private$\nDisallow: /a*b*c$\n", SourceLimits())
        self.assertFalse(anchored.allows(BOOKS + "/private"))
        self.assertTrue(anchored.allows(BOOKS + "/private/more"))
        self.assertFalse(anchored.allows(BOOKS + "/aXXbYYc"))
        self.assertTrue(anchored.allows(BOOKS + "/aXXbYYc/more"))


class TransportSafetyTests(unittest.IsolatedAsyncioTestCase):
    async def test_mixed_dns_answers_are_all_rejected_before_connection(self):
        for answers in [(PUBLIC_IP, "127.0.0.1"), (PUBLIC_V6, "::1"), ("fec0::1",), (PUBLIC_V6, "fec0::1"), ("169.254.169.254",), ("192.168.1.1",), (PUBLIC_IP, "64:ff9b::a9fe:a9fe")]:
            with self.subTest(answers=answers):
                transport = FixtureTransport(dns={"books.example": answers})
                result = await SourceClient(config(), transport).discover(BOOKS + "/series")
                self.assertIn("unsafe_address", codes(result))
                self.assertFalse(result["complete"])
                self.assertEqual(transport.calls, [])

    async def test_public_dns_is_pinned_to_each_request_and_literal_ipv6_is_checked(self):
        transport = FixtureTransport({BOOKS + "/image.png": Reply(PNG, content_type="image/png")}, dns={"books.example": (PUBLIC_IP, PUBLIC_V6)})
        body = await SourceClient(config(), transport).fetch_image(BOOKS + "/image.png")
        self.assertEqual(body, PNG)
        self.assertTrue(all(request["addresses"] == (PUBLIC_IP, PUBLIC_V6) for request in transport.requests))
        resolver = _PinnedResolver("books.example", 443, (PUBLIC_IP, PUBLIC_V6))
        with patch("socket.getaddrinfo", side_effect=AssertionError("No second DNS lookup is permitted")):
            records = await resolver.resolve("books.example", 443)
        self.assertEqual([record["host"] for record in records], [PUBLIC_IP, PUBLIC_V6])
        with self.assertRaises(SourceError):
            await resolver.resolve("another.example", 443)
        with self.assertRaises(SourceError):
            await resolver.resolve("books.example", 80)
        for addresses in [("127.0.0.1",), ("fec0::1",), (PUBLIC_V6, "fec0::1")]:
            with self.assertRaises(SourceError):
                _PinnedResolver("books.example", 443, addresses)
        origin = f"https://[{PUBLIC_V6}]"
        literal = FixtureTransport({origin + "/page.png": Reply(PNG, content_type="image/png")})
        client = SourceClient(config(page_origins=(origin,), image_origins=(origin,)), literal)
        self.assertEqual(await client.fetch_image(origin + "/page.png"), PNG)
        self.assertEqual(literal.resolutions, [])

    async def test_private_redirects_are_not_requested(self):
        for target in ["https://127.0.0.1/art", "https://[::1]/art", "https://169.254.169.254/art", "https://2130706433/art"]:
            with self.subTest(target=target):
                route = BOOKS + "/image.png?secret=PRIVATE"
                transport = FixtureTransport({route: Reply(status=302, headers={"Location": target})})
                with self.assertRaises(SourceError) as error:
                    await SourceClient(config(), transport).fetch_image(route)
                self.assertNotIn("PRIVATE", str(error.exception))
                self.assertNotIn(target, transport.calls)
                self.assertEqual(len(transport.calls), 2)

    async def test_redirect_revalidates_dns_scope_robots_and_does_not_transfer_credentials(self):
        start = BOOKS + "/image.png?token=ORIGINAL"
        final = CDN + "/image.png?token=FINAL"
        routes = {start: Reply(status=302, headers={"Location": final, "Set-Cookie": "secret=SESSION"}), final: Reply(PNG, content_type="image/png")}
        transport = FixtureTransport(routes)
        self.assertEqual(await SourceClient(config(), transport).fetch_image(start), PNG)
        self.assertEqual(transport.calls, [BOOKS + "/robots.txt", start, CDN + "/robots.txt", final])
        for request in transport.requests:
            self.assertEqual(set(request["headers"]), {"User-Agent", "Accept-Encoding", "Accept"})
            self.assertEqual(request["headers"]["User-Agent"], USER_AGENT)
            self.assertEqual(request["headers"]["Accept-Encoding"], "identity")
        mixed = FixtureTransport(routes, dns={"images.example": (PUBLIC_IP, "10.0.0.1")})
        with self.assertRaises(SourceError) as error:
            await SourceClient(config(), mixed).fetch_image(start)
        self.assertEqual(error.exception.code, "unsafe_address")
        self.assertNotIn(final, mixed.calls)
        scoped = FixtureTransport(routes)
        with self.assertRaises(SourceError) as error:
            await SourceClient(config(image_origins=(BOOKS,)), scoped).fetch_image(start)
        self.assertEqual(error.exception.code, "out_of_scope")
        self.assertNotIn(final, scoped.calls)

    async def test_redirect_loops_hop_limits_and_https_downgrade(self):
        first, second = BOOKS + "/a.png", BOOKS + "/b.png"
        transport = FixtureTransport({first: Reply(status=302, headers={"Location": second}), second: Reply(status=302, headers={"Location": first})})
        with self.assertRaises(SourceError) as error:
            await SourceClient(config(), transport).fetch_image(first)
        self.assertEqual(error.exception.code, "redirect_limit")
        self.assertEqual(transport.calls.count(first), 1)
        limited = FixtureTransport({first: Reply(status=302, headers={"Location": second}), second: Reply(PNG, content_type="image/png")})
        with self.assertRaises(SourceError) as error:
            await SourceClient(config(limits=SourceLimits(max_redirects=0)), limited).fetch_image(first)
        self.assertEqual(error.exception.code, "redirect_limit")
        self.assertNotIn(second, limited.calls)
        http_origin = "http://books.example"
        insecure = FixtureTransport({first: Reply(status=302, headers={"Location": http_origin + "/a.png"})})
        with self.assertRaises(SourceError) as error:
            await SourceClient(config(allow_http=True, image_origins=(BOOKS, http_origin)), insecure).fetch_image(first)
        self.assertEqual(error.exception.code, "redirect_downgrade")

    async def test_access_control_and_rate_failures_are_not_retried(self):
        for status, expected in [(401, "access_denied"), (403, "access_denied"), (407, "access_denied"), (451, "access_denied"), (429, "rate_limited"), (500, "http_error")]:
            with self.subTest(status=status):
                url = BOOKS + "/image.png"
                transport = FixtureTransport({url: Reply(status=status)})
                with self.assertRaises(SourceError) as error:
                    await SourceClient(config(), transport).fetch_image(url)
                self.assertEqual(error.exception.code, expected)
                self.assertEqual(transport.calls.count(url), 1)

    async def test_streaming_byte_caps_and_content_length_are_enforced(self):
        url = BOOKS + "/image.png"
        cases = [
            (Reply([b"x" * 8, b"y" * 8], content_type="image/png"), "response_too_large", 3),
            (Reply(PNG, content_type="image/png", headers={"Content-Length": "999999"}), "response_too_large", 1),
            (Reply(b"short", content_type="image/png", headers={"Content-Length": "10"}), "invalid_response", 2),
            (Reply(PNG, content_type="image/png", headers={"Content-Length": "-1"}), "invalid_response", 1),
        ]
        for reply, expected, chunks in cases:
            with self.subTest(expected=expected, headers=reply.headers):
                transport = FixtureTransport({url: reply})
                with self.assertRaises(SourceError) as error:
                    await SourceClient(config(limits=SourceLimits(max_image_bytes=10)), transport).fetch_image(url)
                self.assertEqual(error.exception.code, expected)
                self.assertEqual(transport.chunks, chunks)
                self.assertEqual(transport.closed_responses, 2)

    async def test_compressed_bombs_and_unsupported_transfer_encodings_are_not_read(self):
        for headers in [{"Content-Encoding": "gzip"}, {"Content-Encoding": "br"}, {"Transfer-Encoding": "gzip, chunked"}]:
            with self.subTest(headers=headers):
                url = BOOKS + "/image.png"
                transport = FixtureTransport({url: Reply(b"compressed-bomb-fixture", content_type="image/png", headers=headers)})
                with self.assertRaises(SourceError) as error:
                    await SourceClient(config(), transport).fetch_image(url)
                self.assertEqual(error.exception.code, "compressed_response")
                self.assertEqual(transport.chunks, 1)

    async def test_timeout_and_raw_network_errors_are_safe(self):
        url = BOOKS + "/image.png?token=DO_NOT_LOG"
        slow = FixtureTransport({url: Reply(PNG, content_type="image/png", chunk_delay=0.08)})
        with self.assertRaises(SourceError) as error:
            await SourceClient(config(limits=SourceLimits(request_timeout=0.01)), slow).fetch_image(url)
        self.assertEqual(error.exception.code, "timeout")
        self.assertEqual(slow.closed_responses, 2)
        broken = FixtureTransport({url: Reply(failure=OSError("private request " + url))})
        with self.assertRaises(SourceError) as error:
            await SourceClient(config(), broken).fetch_image(url)
        self.assertEqual(error.exception.code, "transport_failure")
        self.assertNotIn("DO_NOT_LOG", str(error.exception))

    async def test_raster_mime_and_magic_must_agree(self):
        for content_type, body, expected in [
            ("image/svg+xml", b"<svg/>", "content_type"),
            ("application/octet-stream", PNG, "content_type"),
            ("text/html", b"<form>Login</form>", "content_type"),
            ("image/jpeg", PNG, "image_type_mismatch"),
            ("image/png", b"<script>not an image</script>", "image_type_mismatch"),
            ("", PNG, "content_type"),
        ]:
            with self.subTest(content_type=content_type):
                url = BOOKS + "/image"
                transport = FixtureTransport({url: Reply(body, content_type=content_type)})
                with self.assertRaises(SourceError) as error:
                    await SourceClient(config(), transport).fetch_image(url)
                self.assertEqual(error.exception.code, expected)

    async def test_missing_production_dependency_is_explicit_and_lazy(self):
        client = SourceClient(config())
        real_import = importlib.import_module
        def unavailable(name, *args, **kwargs):
            if name == "aiohttp":
                raise ImportError("not installed")
            return real_import(name, *args, **kwargs)
        with patch("tech_farmer.sources.importlib.import_module", side_effect=unavailable), patch("socket.getaddrinfo", side_effect=AssertionError("No DNS when dependency is missing")):
            with self.assertRaises(SourceError) as error:
                await client.fetch_image(BOOKS + "/image.png")
            self.assertEqual(error.exception.code, "dependency_missing")
            manifest = await SourceClient(config()).discover(BOOKS + "/series")
            self.assertFalse(manifest["complete"])
            self.assertIn("dependency_missing", codes(manifest))

    async def test_production_connector_uses_only_pinned_resolver_and_safe_session_settings(self):
        observed = {}
        class Response:
            status = 200
            headers = {"Content-Type": "image/png", "X-Robots-Tag": "index"}
            raw_headers = ((b"Content-Type", b"image/png"), (b"X-Robots-Tag", b"noimageindex"), (b"x-robots-tag", b"index"))
            async def __aenter__(self):
                return self
            async def __aexit__(self, *_):
                return None
            @property
            def content(self):
                async def chunks(_):
                    yield PNG
                return SimpleNamespace(iter_chunked=chunks)
        class Session:
            def __init__(self, **kwargs):
                observed["session"] = kwargs
            async def __aenter__(self):
                return self
            async def __aexit__(self, *_):
                return None
            def get(self, url, **kwargs):
                observed["get"] = (url, kwargs)
                return Response()
        def connector(**kwargs):
            observed["connector"] = kwargs
            return kwargs
        def encoded_url(value, *, encoded):
            observed["encoded"] = encoded
            return value
        fake = SimpleNamespace(TCPConnector=connector, ClientTimeout=lambda **kwargs: kwargs, DummyCookieJar=lambda: "disabled-cookies", ClientSession=Session)
        real_import = importlib.import_module
        def library(name, *args, **kwargs):
            return fake if name == "aiohttp" else SimpleNamespace(URL=encoded_url) if name == "yarl" else real_import(name, *args, **kwargs)
        with patch("tech_farmer.sources.importlib.import_module", side_effect=library), patch("socket.getaddrinfo", side_effect=AssertionError("No unchecked lookup")):
            async with AiohttpTransport().open(BOOKS + "/image.png?sig=%2Fsecret", addresses=(PUBLIC_IP,), headers={"Accept-Encoding": "identity", "User-Agent": USER_AGENT}, timeout=2, connect_timeout=1) as response:
                self.assertEqual(b"".join([chunk async for chunk in response.body]), PNG)
                self.assertEqual([value for name, value in response.headers if name.lower() == "x-robots-tag"], ["noimageindex", "index"])
                self.assertTrue(SourceClient._restricted(_response_headers(response.headers)["x-robots-tag"]))
            records = await observed["connector"]["resolver"].resolve("books.example", 443)
        self.assertEqual(records[0]["host"], PUBLIC_IP)
        self.assertFalse(observed["connector"]["use_dns_cache"])
        self.assertFalse(observed["session"]["trust_env"])
        self.assertFalse(observed["session"]["auto_decompress"])
        self.assertEqual(observed["session"]["cookie_jar"], "disabled-cookies")
        self.assertFalse(observed["get"][1]["allow_redirects"])
        self.assertIsNone(observed["get"][1]["proxy"])
        self.assertTrue(observed["encoded"])


class RobotsAndScopeTests(unittest.IsolatedAsyncioTestCase):
    async def test_robots_disallow_prevents_document_and_image_fetch(self):
        robots = Reply(b"User-agent: *\nDisallow: /private\n", content_type="text/plain")
        transport = FixtureTransport({BOOKS + "/robots.txt": robots})
        result = await SourceClient(config(), transport).discover(BOOKS + "/private/reader")
        self.assertIn("robots_denied", codes(result))
        self.assertEqual(transport.calls, [BOOKS + "/robots.txt"])
        with self.assertRaises(SourceError) as error:
            await SourceClient(config(), transport).fetch_image(BOOKS + "/private/image.png")
        self.assertEqual(error.exception.code, "robots_denied")

    async def test_robots_missing_is_allowed_but_failures_and_bombs_fail_closed(self):
        image = BOOKS + "/image.png"
        for status in (404, 410):
            transport = FixtureTransport({BOOKS + "/robots.txt": Reply(status=status), image: Reply(PNG, content_type="image/png")})
            self.assertEqual(await SourceClient(config(), transport).fetch_image(image), PNG)
        cases = [
            (Reply(status=403), "access_denied"), (Reply(status=503), "http_error"),
            (Reply(b"X" * 65, content_type="text/plain"), "response_too_large"),
            (Reply(b"<html>Challenge</html>", content_type="text/plain"), "robots_unavailable"),
            (Reply(b"User-agent: *", content_type="text/html"), "content_type"),
            (Reply(b"fake compressed data", content_type="text/plain", headers={"Content-Encoding": "gzip"}), "compressed_response"),
            (Reply(b"\xff\xfe", content_type="text/plain"), "robots_unavailable"),
        ]
        for robots, code in cases:
            with self.subTest(code=code):
                transport = FixtureTransport({BOOKS + "/robots.txt": robots})
                client = SourceClient(config(limits=SourceLimits(max_robots_bytes=64)), transport)
                with self.assertRaises(SourceError) as error:
                    await client.fetch_image(image)
                self.assertEqual(error.exception.code, code)
                with self.assertRaises(SourceError):
                    await client.fetch_image(image)
                self.assertEqual(transport.calls, [BOOKS + "/robots.txt"])

    async def test_robots_delay_and_request_rate_are_honored_or_refused(self):
        image = BOOKS + "/image.png"
        transport = FixtureTransport({BOOKS + "/robots.txt": Reply(b"User-agent: *\nCrawl-delay: 0.015\n", content_type="text/plain"), image: Reply(PNG, content_type="image/png")})
        client = SourceClient(config(), transport)
        await client.fetch_image(image)
        await client.fetch_image(image)
        self.assertGreaterEqual(transport.open_times[-1] - transport.open_times[-2], 0.014)
        for directive in ("Crawl-delay: 120", "Request-rate: 1/120"):
            transport = FixtureTransport({BOOKS + "/robots.txt": Reply(("User-agent: *\n" + directive).encode(), content_type="text/plain")})
            with self.assertRaises(SourceError) as error:
                await SourceClient(config(), transport).fetch_image(image)
            self.assertEqual(error.exception.code, "robots_delay")
            self.assertNotIn(image, transport.calls)

    async def test_site_meta_and_response_robots_restrictions_are_respected(self):
        for restriction in ("noimageindex", "nofollow", "noarchive", "noai"):
            transport = FixtureTransport({BOOKS + "/read": Reply(f'<meta name="robots" content="{restriction}"><div class="reader"><img src="/p.png"></div>'.encode())})
            manifest = await SourceClient(config(), transport).discover(BOOKS + "/read")
            self.assertIn("site_restricted", codes(manifest))
            self.assertEqual(manifest["pages"], [])
        transport = FixtureTransport({BOOKS + "/p.png": Reply(PNG, content_type="image/png", headers={"X-Robots-Tag": "noimageindex"})})
        with self.assertRaises(SourceError) as error:
            await SourceClient(config(), transport).fetch_image(BOOKS + "/p.png")
        self.assertEqual(error.exception.code, "site_restricted")

    async def test_every_repeated_robots_header_constrains_access_in_both_orders_and_cases(self):
        image = BOOKS + "/image.png"
        for restrictive in ("noimageindex", "noai", "noarchive"):
            variants = [
                {"X-Robots-Tag": restrictive, "x-robots-tag": "index"},
                {"x-robots-tag": "index", "X-Robots-Tag": restrictive},
                [("X-Robots-Tag", restrictive), ("X-Robots-Tag", "index")],
                [("X-Robots-Tag", "index"), ("X-Robots-Tag", restrictive)],
                [("x-ROBOTS-tag", restrictive), ("X-robots-TAG", "index")],
                [("x-ROBOTS-tag", "index"), ("X-robots-TAG", restrictive)],
            ]
            for headers in variants:
                with self.subTest(restrictive=restrictive, headers=headers):
                    transport = FixtureTransport({image: Reply(PNG, content_type="image/png", headers=headers)})
                    with self.assertRaises(SourceError) as error:
                        await SourceClient(config(), transport).fetch_image(image)
                    self.assertEqual(error.exception.code, "site_restricted")
                    self.assertEqual(transport.chunks, 1)
                    self.assertEqual(transport.calls.count(image), 1)

    async def test_repeated_singleton_or_encoding_headers_cannot_override_safety_checks(self):
        image = BOOKS + "/image.png"
        cases = [
            ([("content-type", "text/html")], 200, "invalid_response"),
            ([("Content-Length", "100"), ("content-length", str(len(PNG)))], 200, "invalid_response"),
            ([("Location", "https://127.0.0.1/a"), ("location", CDN + "/a.png")], 302, "invalid_response"),
            ([("Content-Encoding", "gzip"), ("content-encoding", "identity")], 200, "compressed_response"),
            ([("content-encoding", "identity"), ("Content-Encoding", "gzip")], 200, "compressed_response"),
        ]
        for headers, status, expected in cases:
            with self.subTest(headers=headers):
                transport = FixtureTransport({image: Reply(PNG, status=status, content_type="image/png", headers=headers)})
                with self.assertRaises(SourceError) as error:
                    await SourceClient(config(), transport).fetch_image(image)
                self.assertEqual(error.exception.code, expected)
                self.assertEqual(transport.chunks, 1)
                self.assertEqual(len(transport.calls), 2)

    async def test_header_aggregation_is_bounded_and_rejects_malformed_fields(self):
        for fields in [[("X-Example", "x")] * 129, [("X-Example", "x" * 65536)], [("Bad Name", "value")], [("X-Example", "line\r\nbreak")]]:
            with self.subTest(kind=type(fields)), self.assertRaises(SourceError) as error:
                _response_headers(fields)
            self.assertEqual(error.exception.code, "invalid_response")
        self.assertEqual(_response_headers([("X-Robots-Tag", "index"), ("x-robots-tag", "noarchive")])["x-robots-tag"], "index, noarchive")

    async def test_origins_paths_and_html_base_are_explicit_and_no_subdomain_wildcard(self):
        transport = FixtureTransport()
        client = SourceClient(config(page_paths=("/series", "/read"), image_paths=("/images",)), transport)
        for url in ["https://sub.books.example/read", BOOKS + "/reader", BOOKS + "/read/%252e%252e/private"]:
            with self.subTest(url=url), self.assertRaises(SourceError):
                await client.discover(url)
        self.assertEqual(transport.calls, [])
        transport.routes[BOOKS + "/read/1"] = Reply(b'<base href="https://outside.example/"><div class="reader"><img src="image.png"></div>')
        manifest = await client.discover(BOOKS + "/read/1")
        self.assertIn("out_of_scope", codes(manifest))
        self.assertEqual(manifest["pages"], [])
        transport.routes[BOOKS + "/read/2"] = reader(CDN + "/private/art.png", "https://unknown.example/images/a.png")
        manifest = await client.discover(BOOKS + "/read/2")
        self.assertIn("out_of_scope", codes(manifest))
        self.assertEqual(manifest["pages"], [])


class DiscoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_direct_image_and_fetch_contract_with_self_authored_bytes(self):
        url = BOOKS + "/direct.png?signature=PRIVATE#fragment"
        exact = url.split("#")[0]
        transport = FixtureTransport({exact: Reply(PNG, content_type="image/png")})
        async with SourceClient(config(), transport) as client:
            manifest = await client.discover(url)
            self.assertTrue(manifest["complete"])
            self.assertEqual(manifest["scope"], "page")
            self.assertEqual(len(manifest["pages"]), 1)
            self.assertEqual(manifest["pages"][0]["page_number"], 1)
            self.assertEqual(manifest["source_url"], BOOKS + "/direct.png")
            self.assertNotIn("PRIVATE", json.dumps(manifest))
            self.assertEqual(await client.fetch_image(manifest["pages"][0]["fetch_id"]), PNG)
            self.assertEqual(await client.fetch_image(manifest["pages"][0]["image_url"]), PNG)
        with self.assertRaises(SourceError) as error:
            await client.fetch_image(exact)
        self.assertEqual(error.exception.code, "closed")

    async def test_full_quality_reader_context_srcset_lazy_images_exclusions_and_dedup(self):
        root = BOOKS + "/read/1?token=SOURCE_SECRET"
        html = b'''<html><img src="/banner.png"><div class="reader">
          <img src="/thumb.png" data-original="https://images.example/images/1.png?sig=IMAGE_SECRET">
          <img src="/placeholder.png" data-src="https://images.example/images/2.png">
          <img src="/tiny.png" srcset="https://images.example/images/tiny.png 100w, https://images.example/images/3.png 1800w">
          <picture><source type="image/webp" srcset="https://images.example/images/4.webp 3x"><img src="/thumbnail.png" srcset="/small.png 1x, /medium.png 2x"></picture>
          <img data-srcset="https://images.example/images/5.png?crop=1,2 2x" src="data:image/png;base64,AAAA">
          <img class="cover" src="/cover.png"><div class="ad"><img src="/ad.png"></div>
          <img class="icon" src="/icon.png"><img src="https://images.example/images/2.png#duplicate">
          </div><footer><img src="/outside.png"></footer></html>'''
        transport = FixtureTransport({root: Reply(html), CDN + "/images/1.png?sig=IMAGE_SECRET": Reply(PNG, content_type="image/png")})
        client = SourceClient(config(), transport)
        manifest = await client.discover(root)
        self.assertTrue(manifest["complete"], manifest["problems"])
        self.assertEqual([page["image_url"] for page in manifest["pages"]], [CDN + "/images/1.png", CDN + "/images/2.png", CDN + "/images/3.png", CDN + "/images/4.webp", CDN + "/images/5.png"])
        self.assertEqual(transport.calls, [BOOKS + "/robots.txt", root])
        self.assertNotIn("SECRET", json.dumps(manifest))
        self.assertEqual(await client.fetch_image(manifest["pages"][0]["fetch_id"]), PNG)
        self.assertEqual(transport.calls[-2:], [CDN + "/robots.txt", CDN + "/images/1.png?sig=IMAGE_SECRET"])

    async def test_series_24_issues_all_pages_catalog_pagination_and_natural_order(self):
        root = BOOKS + "/series/sample"
        second = root + "?catalog=2"
        def catalog(numbers, next_url):
            links = "".join(f'<a href="/read/{number}">Issue {number}</a>' for number in numbers)
            next_link = f'<a class="series-next" href="{next_url}">More</a>' if next_url else ""
            return Reply(f'<div class="chapters">{links}</div>{next_link}'.encode())
        routes = {root: catalog(range(24, 12, -1), second), second: catalog([12, 2, *range(11, 0, -1)], root)}
        expected_documents = {root, second}
        for number in range(1, 25):
            issue = BOOKS + f"/read/{number}"
            page2 = issue + "?page=2"
            routes[issue] = reader(CDN + f"/images/{number}-1.png", CDN + f"/images/{number}-2.png", next_url=page2)
            routes[page2] = Reply(f'<div class="reader"><img src="{CDN}/images/{number}-3.png" data-page-number="3"></div><a class="page-next" href="{issue}">Loop</a>'.encode())
            expected_documents.update((issue, page2))
        transport = FixtureTransport(routes)
        snapshots = []
        async def progress(manifest):
            snapshots.append((len(manifest["pages"]), manifest["complete"]))
        manifest = await SourceClient(config(), transport).discover(root, scope="series", progress=progress)
        self.assertTrue(manifest["complete"], manifest["problems"])
        self.assertEqual(len(manifest["issues"]), 24)
        self.assertEqual(len(manifest["pages"]), 72)
        self.assertEqual(manifest["documents"], 50)
        self.assertEqual([issue["issue"] for issue in manifest["issues"]], [f"Issue {number}" for number in range(1, 25)])
        self.assertTrue(all(issue["page_count"] == 3 for issue in manifest["issues"]))
        self.assertEqual([page["page_number"] for page in manifest["pages"]], [1, 2, 3] * 24)
        self.assertEqual(len({page["page_key"] for page in manifest["pages"]}), 72)
        self.assertEqual(set(transport.calls), expected_documents | {BOOKS + "/robots.txt"})
        self.assertEqual(len(transport.calls), 51)
        self.assertIn("pagination_loop", codes(manifest))
        self.assertEqual(snapshots[-1], (72, True))
        self.assertEqual([size for size, complete in snapshots[:-1]], list(range(1, 73)))

    async def test_scope_page_does_not_expand_but_chapter_alias_follows_declared_links(self):
        root, next_url = BOOKS + "/read/1", BOOKS + "/read/1?page=2"
        routes = {root: reader(CDN + "/images/1.png", next_url=next_url), next_url: reader(CDN + "/images/2.png")}
        for scope, count in [("page", 1), ("chapter", 2), ("issue", 2), ("auto", 2)]:
            with self.subTest(scope=scope):
                transport = FixtureTransport(routes)
                manifest = await SourceClient(config(), transport).discover(root, scope=scope)
                self.assertTrue(manifest["complete"])
                self.assertEqual(len(manifest["pages"]), count)
                self.assertEqual(next_url in transport.calls, count == 2)

    async def test_unknown_javascript_only_and_login_layouts_are_unsupported_not_guessed(self):
        for body in [b'<script>window.pages=["/secret.png"]</script>', b'<div class="gallery"><img src="/art.png"></div>', b'<form><input name="password"></form>', b'<a href="/read/2">Undeclared link</a>']:
            transport = FixtureTransport({BOOKS + "/reader": Reply(body)})
            manifest = await SourceClient(config(), transport).discover(BOOKS + "/reader")
            self.assertFalse(manifest["complete"])
            self.assertEqual(manifest["status"], "unsupported")
            self.assertIn("unsupported_layout", codes(manifest))
            self.assertEqual(transport.calls, [BOOKS + "/robots.txt", BOOKS + "/reader"])

    async def test_partial_failures_are_explicit_and_other_issues_are_not_lost(self):
        root = BOOKS + "/series"
        transport = FixtureTransport({root: Reply(b'<div class="chapters"><a href="/read/1">Issue 1</a><a href="/read/2">Issue 2</a></div>'), BOOKS + "/read/1": Reply(status=403), BOOKS + "/read/2": reader(CDN + "/images/2.png")})
        manifest = await SourceClient(config(), transport).discover(root)
        self.assertFalse(manifest["complete"])
        self.assertEqual(manifest["status"], "partial")
        self.assertIn("access_denied", codes(manifest))
        self.assertEqual(len(manifest["pages"]), 1)
        self.assertEqual(len(manifest["issues"]), 2)
        self.assertIsNone(manifest["cursor"])

    async def test_cursor_batches_accumulate_without_refetching_or_first_n_success(self):
        root = BOOKS + "/read"
        transport = FixtureTransport({root: reader(*(CDN + f"/images/{number}.png" for number in range(1, 6)))})
        client = SourceClient(config(limits=SourceLimits(max_pages_per_batch=2)), transport)
        first = await client.discover(root)
        self.assertEqual(first["status"], "paused")
        self.assertFalse(first["complete"])
        self.assertIn("page_budget", codes(first))
        second = await client.discover(root, cursor=first["cursor"])
        third = await client.discover(root, cursor=second["cursor"])
        self.assertEqual([len(result["pages"]) for result in (first, second, third)], [2, 4, 5])
        self.assertTrue(third["complete"])
        self.assertIsNone(third["cursor"])
        self.assertNotIn("page_budget", codes(third))
        self.assertEqual(first["pages"], third["pages"][:2])
        self.assertEqual(transport.calls.count(root), 1)

    async def test_document_budget_pause_resumes_catalog_before_sorted_issues(self):
        root = BOOKS + "/series"
        second = root + "?page=2"
        transport = FixtureTransport({root: Reply(f'<div class="chapters"><a href="/read/10">Issue 10</a></div><a class="series-next" href="{second}">More</a>'.encode()), second: Reply(b'<div class="chapters"><a href="/read/2">Issue 2</a></div>'), BOOKS + "/read/2": reader(CDN + "/images/2.png"), BOOKS + "/read/10": reader(CDN + "/images/10.png")})
        client = SourceClient(config(limits=SourceLimits(max_documents_per_batch=1)), transport)
        result = await client.discover(root)
        self.assertIn("document_budget", codes(result))
        self.assertEqual(result["pages"], [])
        while result["cursor"]:
            result = await client.discover(root, cursor=result["cursor"])
        self.assertTrue(result["complete"])
        self.assertEqual([page["issue"] for page in result["pages"]], ["Issue 2", "Issue 10"])
        self.assertEqual(len(transport.calls), 5)

    async def test_hard_total_and_reference_limits_report_explicit_limited_results(self):
        root = BOOKS + "/read"
        for limits in [SourceLimits(max_pages=2), SourceLimits(max_references=2), SourceLimits(max_pending=2)]:
            with self.subTest(limits=limits):
                transport = FixtureTransport({root: reader(*(CDN + f"/images/{number}.png" for number in range(4)))})
                manifest = await SourceClient(config(limits=limits), transport).discover(root)
                self.assertEqual(manifest["status"], "limited")
                self.assertFalse(manifest["complete"])
                self.assertIn("state_limit", codes(manifest))
                self.assertIsNone(manifest["cursor"])
        for limits in [SourceLimits(max_issues=1), SourceLimits(max_documents=1)]:
            transport = FixtureTransport({BOOKS + "/series": Reply(b'<div class="chapters"><a href="/read/1">1</a><a href="/read/2">2</a></div>')})
            manifest = await SourceClient(config(limits=limits), transport).discover(BOOKS + "/series")
            self.assertEqual(manifest["status"], "limited")
            self.assertEqual(manifest["pages"], [])

    async def test_cancel_between_pages_and_before_fetch_can_resume(self):
        root, next_url = BOOKS + "/read", BOOKS + "/read?page=2"
        transport = FixtureTransport({root: reader(CDN + "/images/1.png", CDN + "/images/2.png", next_url=next_url), next_url: reader(CDN + "/images/3.png")})
        client = SourceClient(config(), transport)
        cancelled = asyncio.Event()
        async def progress(manifest):
            if len(manifest["pages"]) == 2:
                cancelled.set()
        first = await client.discover(root, progress=progress, cancel=cancelled.is_set)
        self.assertEqual(first["status"], "cancelled")
        self.assertNotIn(next_url, transport.calls)
        cancelled.clear()
        final = await client.discover(root, cancel=cancelled, cursor=first["cursor"])
        self.assertTrue(final["complete"])
        self.assertEqual(len(final["pages"]), 3)
        untouched = FixtureTransport()
        cancelled.set()
        paused = await SourceClient(config(), untouched).discover(root, cancel=cancelled)
        self.assertEqual(paused["status"], "cancelled")
        self.assertEqual(untouched.calls, [])

    async def test_progress_failure_preserves_checkpoint_and_callback_cannot_mutate_state(self):
        root = BOOKS + "/read"
        transport = FixtureTransport({root: reader(CDN + "/images/1.png", CDN + "/images/2.png")})
        client = SourceClient(config(), transport)
        def failing(manifest):
            manifest["pages"].clear()
            raise RuntimeError("callback secret=PRIVATE")
        manifest = await client.discover(root, progress=failing)
        self.assertEqual(len(manifest["pages"]), 1)
        self.assertIn("progress_failed", codes(manifest))
        self.assertNotIn("PRIVATE", json.dumps(manifest))
        final = await client.discover(root, cursor=manifest["cursor"])
        self.assertTrue(final["complete"])
        self.assertEqual(len(final["pages"]), 2)

    async def test_cursors_and_references_expire_and_mismatched_scope_is_rejected(self):
        root = BOOKS + "/read"
        client = SourceClient(config(limits=SourceLimits(max_pages_per_batch=1)), FixtureTransport({root: reader(CDN + "/images/1.png", CDN + "/images/2.png")}))
        manifest = await client.discover(root)
        for url, scope in [(root + "?different=1", "auto"), (root, "page")]:
            with self.assertRaises(SourceError) as error:
                await client.discover(url, scope=scope, cursor=manifest["cursor"])
            self.assertEqual(error.exception.code, "invalid_cursor")
        key = manifest["pages"][0]["fetch_id"]
        client._references[key].expires = 0
        with self.assertRaises(SourceError) as error:
            await client.fetch_image(key)
        self.assertEqual(error.exception.code, "reference_expired")
        client._sessions[manifest["cursor"]].expires = 0
        with self.assertRaises(SourceError) as error:
            await client.discover(root, cursor=manifest["cursor"])
        self.assertEqual(error.exception.code, "invalid_cursor")

    async def test_query_only_chapters_have_distinct_stable_page_keys_without_secret_provenance(self):
        async def discover(version):
            root = BOOKS + "/series"
            first = BOOKS + f"/reader?chapter=1&sig=CHAPTER_SECRET_{version}"
            second = BOOKS + f"/reader?chapter=2&sig=CHAPTER_SECRET_{version}"
            image1 = CDN + f"/page.png?chapter=1&page=1&sig=IMAGE_SECRET_{version}"
            image2 = CDN + f"/page.png?chapter=2&page=1&sig=IMAGE_SECRET_{version}"
            transport = FixtureTransport({root: Reply(f'<div class="chapters"><a href="{second}">Issue 2</a><a href="{first}">Issue 1</a></div>'.encode()), first: reader(image1), second: reader(image2), image1: Reply(PNG, content_type="image/png"), image2: Reply(PNG, content_type="image/png")})
            client = SourceClient(config(identity_query_params=("chapter",), image_identity_query_params=("chapter", "page")), transport)
            result = await client.discover(root)
            return client, result
        client, first = await discover("A")
        _, second = await discover("B")
        self.assertTrue(first["complete"], first["problems"])
        self.assertEqual(len(first["pages"]), 2)
        self.assertEqual([page["page_number"] for page in first["pages"]], [1, 1])
        self.assertEqual(len({page["page_key"] for page in first["pages"]}), 2)
        self.assertEqual([page["page_key"] for page in first["pages"]], [page["page_key"] for page in second["pages"]])
        self.assertNotEqual(first["pages"][0]["fetch_id"], second["pages"][0]["fetch_id"])
        self.assertNotIn("SECRET", json.dumps(first))
        self.assertNotIn("?", json.dumps(first))
        with self.assertRaises(SourceError) as error:
            await client.fetch_image(first["pages"][0]["image_url"])
        self.assertEqual(error.exception.code, "ambiguous_reference")
        self.assertEqual(await client.fetch_image(first["pages"][0]["fetch_id"]), PNG)

    async def test_query_identity_ambiguity_is_reported_not_silently_merged(self):
        root, first, second = BOOKS + "/series", BOOKS + "/read?chapter=1", BOOKS + "/read?chapter=2"
        transport = FixtureTransport({root: Reply(f'<div class="chapters"><a href="{first}">Issue 1</a><a href="{second}">Issue 2</a></div>'.encode()), first: reader(CDN + "/1.png"), second: reader(CDN + "/2.png")})
        result = await SourceClient(config(), transport).discover(root)
        self.assertFalse(result["complete"])
        self.assertIn("identity_ambiguous", codes(result))
        self.assertNotIn(second, transport.calls)

    async def test_page_number_metadata_survives_query_redaction_and_conflicts_are_explicit(self):
        root = BOOKS + "/read?page=17&token=SECRET"
        result = await SourceClient(config(page_number_param="page"), FixtureTransport({root: reader(CDN + "/17.png")})).discover(root, scope="page")
        self.assertEqual(result["pages"][0]["page_number"], 17)
        self.assertNotIn("SECRET", json.dumps(result))
        duplicate = Reply(b'<div class="reader"><img src="/1.png" data-page-number="3"><img src="/2.png" data-page-number="3"></div>')
        result = await SourceClient(config(), FixtureTransport({BOOKS + "/read": duplicate})).discover(BOOKS + "/read")
        self.assertEqual([page["page_number"] for page in result["pages"]], [3, 4])
        self.assertEqual(result["pages"][1]["source_page_number"], 3)
        self.assertIn("page_number_conflict", codes(result))


class ReviewRegressionTests(unittest.IsolatedAsyncioTestCase):
    async def test_persistent_seed_is_offline_scoped_and_keeps_only_safe_declared_routing(self):
        transport = FixtureTransport()
        client = SourceClient(config(identity_query_params=("chapter", "series"), page_number_param="page", page_paths=("/read",), image_origins=(CDN,), image_paths=("/images",)), transport)
        self.assertEqual(client.persistent_seed(BOOKS + "/read?series=original-demo&chapter=3&page=7"), BOOKS + "/read?chapter=3&page=7&series=original-demo")
        self.assertEqual(client.persistent_seed(BOOKS + "/read?%63hapter=3"), BOOKS + "/read?chapter=3")
        self.assertEqual(client.persistent_seed(BOOKS + "/read"), BOOKS + "/read")
        rejected = [
            BOOKS + "/read?token=PRIVATE", BOOKS + "/read?chapter=3&sig=PRIVATE",
            BOOKS + "/read#PRIVATE", BOOKS + "/read#", "https://user:PRIVATE@books.example/read",
            BOOKS + "/read?chapter=1&chapter=2", BOOKS + "/read?chapter=",
            BOOKS + "/read?chapter=eyJheader.eyJclaims.signature", BOOKS + "/read?chapter=abc%26token%3DPRIVATE",
            BOOKS + "/read?chapter=abcdef0123456789abcdef0123456789", BOOKS + "/read?chapter=10000000000000000000000",
            BOOKS + "/read?page=one", BOOKS + "/read?page=0", BOOKS + "/other?chapter=3",
        ]
        for url in rejected:
            with self.subTest(url=url):
                with self.assertRaises(SourceError) as error:
                    client.persistent_seed(url)
                self.assertNotIn("PRIVATE", str(error.exception))
        self.assertEqual(transport.calls, [])
        self.assertEqual(transport.resolutions, [])

    async def test_declared_secret_query_parameter_names_are_still_refused_for_saved_seed(self):
        for name in ["token", "api_key", "authorization", "sessionid", "signature", "X-Amz-Credential", "expires", "nonce", "password", "cookie", "accessToken"]:
            client = SourceClient(config(identity_query_params=(name,)))
            with self.subTest(name=name), self.assertRaises(SourceError) as error:
                client.persistent_seed(BOOKS + f"/read?{name}=3")
            self.assertEqual(error.exception.code, "unsafe_persistent_seed")

    async def test_same_image_in_distinct_issues_is_retained_not_globally_deduplicated(self):
        root, shared = BOOKS + "/series", CDN + "/shared.png"
        transport = FixtureTransport({root: Reply(b'<div class="chapters"><a href="/read/1">Issue 1</a><a href="/read/2">Issue 2</a></div>'), BOOKS + "/read/1": reader(shared, shared), BOOKS + "/read/2": reader(shared, shared), shared: Reply(PNG, content_type="image/png")})
        client = SourceClient(config(), transport)
        result = await client.discover(root)
        self.assertTrue(result["complete"], result["problems"])
        self.assertEqual(len(result["pages"]), 2)
        self.assertEqual([issue["page_count"] for issue in result["issues"]], [1, 1])
        self.assertEqual(len({page["page_key"] for page in result["pages"]}), 2)
        self.assertEqual(len({page["fetch_id"] for page in result["pages"]}), 1)
        self.assertEqual(await client.fetch_image(result["pages"][0]["fetch_id"]), PNG)

    async def test_distinct_explicit_pages_with_same_image_are_retained_and_dom_duplicates_suppressed(self):
        root = BOOKS + "/read"
        body = b'<div class="reader"><img src="/same.png" data-page-number="1"><img src="/same.png" data-page-number="2"><img src="/same.png"><img src="/same.png" data-page-number="2"></div>'
        result = await SourceClient(config(), FixtureTransport({root: Reply(body)})).discover(root)
        self.assertTrue(result["complete"])
        self.assertEqual([page["page_number"] for page in result["pages"]], [1, 2])
        self.assertEqual(len({page["page_key"] for page in result["pages"]}), 2)

    async def test_shared_redirect_reader_does_not_erase_another_declared_issue(self):
        root, first, second = BOOKS + "/series", BOOKS + "/read/first", BOOKS + "/read/second"
        transport = FixtureTransport({root: Reply(f'<div class="chapters"><a href="{first}">Issue 1</a><a href="{second}">Issue 2</a></div>'.encode()), first: Reply(status=302, headers={"Location": second}), second: reader(CDN + "/shared.png")})
        result = await SourceClient(config(), transport).discover(root)
        self.assertTrue(result["complete"], result["problems"])
        self.assertEqual([issue["page_count"] for issue in result["issues"]], [1, 1])
        self.assertEqual(len(result["pages"]), 2)

    async def test_query_only_image_order_changes_do_not_substitute_completed_page_identity(self):
        root, image1, image2 = BOOKS + "/read", CDN + "/image?id=1", CDN + "/image?id=2"
        routes = {root: reader(image1, image2), image1: Reply(PNG, content_type="image/png"), image2: Reply(authored_png((90, 30, 150)), content_type="image/png")}
        first_client = SourceClient(config(), FixtureTransport(routes))
        initial = await first_client.discover(root)
        completed_key = initial["pages"][0]["page_key"]
        initial_by_url = {first_client._references[page["fetch_id"]].url: page["page_key"] for page in initial["pages"]}
        routes[root] = reader(image2, image1)
        fresh_transport = FixtureTransport(routes)
        fresh_client = SourceClient(config(), fresh_transport)
        fresh = await fresh_client.discover(root)
        fresh_by_url = {fresh_client._references[page["fetch_id"]].url: page["page_key"] for page in fresh["pages"]}
        self.assertEqual(initial_by_url, fresh_by_url)
        self.assertNotEqual(fresh["pages"][0]["page_key"], completed_key)
        pending = [page for page in fresh["pages"] if page["page_key"] != completed_key]
        self.assertEqual(len(pending), 1)
        await fresh_client.fetch_image(pending[0]["fetch_id"])
        self.assertIn(image2, fresh_transport.calls)
        self.assertNotIn(image1, fresh_transport.calls)
        self.assertNotIn("?", json.dumps(fresh))

    async def test_declared_image_semantics_stay_stable_across_token_rotation_unknown_semantics_do_not(self):
        async def discover(token, semantics):
            root, image = BOOKS + "/read", CDN + f"/image?id=7&sig={token}"
            client = SourceClient(config(image_identity_query_params=semantics), FixtureTransport({root: reader(image)}))
            return (await client.discover(root))["pages"][0]["page_key"]
        self.assertEqual(await discover("SECRET_A", ("id",)), await discover("SECRET_B", ("id",)))
        self.assertNotEqual(await discover("SECRET_A", ()), await discover("SECRET_B", ()))

    async def test_html_parsing_runs_off_event_loop_and_does_not_mutate_state_after_task_cancel(self):
        root = BOOKS + "/read"
        client = SourceClient(config(), FixtureTransport({root: reader(CDN + "/1.png")}))
        original = client._parse_html
        entered, release = threading.Event(), threading.Event()
        thread_ids = []
        def slow_parse(*args):
            thread_ids.append(threading.get_ident())
            entered.set()
            release.wait(1)
            return original(*args)
        with patch.object(client, "_parse_html", side_effect=slow_parse):
            task = asyncio.create_task(client.discover(root))
            for _ in range(100):
                if entered.is_set():
                    break
                await asyncio.sleep(0.002)
            self.assertTrue(entered.is_set())
            self.assertNotEqual(thread_ids[0], threading.get_ident())
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            state = next(iter(client._sessions.values()))
            self.assertEqual(state.pages, [])
            self.assertEqual(state.documents, 0)
            self.assertEqual(state.todo[0].url, root)
            release.set()
            await asyncio.sleep(0.03)
            self.assertEqual(state.pages, [])
            result = await client.discover(root, cursor=state.token)
        self.assertTrue(result["complete"])
        self.assertEqual(len(result["pages"]), 1)

    async def test_active_client_renews_other_pending_image_references(self):
        root, first, second = BOOKS + "/read", CDN + "/1.png", CDN + "/2.png"
        transport = FixtureTransport({root: reader(first, second), first: Reply(PNG, content_type="image/png"), second: Reply(PNG, content_type="image/png")})
        client = SourceClient(config(), transport)
        result = await client.discover(root)
        pending_key = result["pages"][1]["fetch_id"]
        soon = time.monotonic() + 0.2
        client._references[pending_key].expires = soon
        await client.fetch_image(result["pages"][0]["fetch_id"])
        self.assertGreater(client._references[pending_key].expires, soon + 100)
        self.assertEqual(await client.fetch_image(pending_key), PNG)

    async def test_picture_number_excluded_cover_and_recommendation_links_remain_in_declared_context(self):
        root = BOOKS + "/series"
        transport = FixtureTransport({root: Reply(b'<div class="reader"><img class="cover" src="/cover.png"></div><div class="chapters"><a href="/read/1">Issue 1</a></div><section data-similar-tabs><a data-rec-post href="/read/999">Recommendation</a></section>'), BOOKS + "/read/1": Reply(b'<div class="reader"><picture><source srcset="/full.png 2x"><img src="/small.png" data-page-number="7"></picture></div>')})
        result = await SourceClient(config(), transport).discover(root)
        self.assertTrue(result["complete"])
        self.assertEqual(result["scope"], "series")
        self.assertEqual(len(result["issues"]), 1)
        self.assertEqual(result["pages"][0]["page_number"], 7)
        self.assertNotIn(BOOKS + "/read/999", transport.calls)

    async def test_malformed_base_invalid_selector_and_nondecimal_issue_labels_fail_safely(self):
        root = BOOKS + "/read"
        malformed = Reply(b'<base href="https://[broken/"><div class="reader"><img src="/p.png"></div>')
        result = await SourceClient(config(), FixtureTransport({root: malformed})).discover(root)
        self.assertIn("invalid_url", codes(result))
        result = await SourceClient(config(reader_selector="["), FixtureTransport({root: reader("/p.png")})).discover(root)
        self.assertIn("invalid_config", codes(result))
        root = BOOKS + "/series"
        transport = FixtureTransport({root: Reply('<div class="chapters"><a href="/read/1">①</a></div>'.encode()), BOOKS + "/read/1": reader(CDN + "/1.png")})
        result = await SourceClient(config(), transport).discover(root)
        self.assertTrue(result["complete"])


class ReaderBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_optional_reader_boundary_uses_issue_seed_not_current_document(self):
        seed, second, third = (BOOKS + path for path in ("/book/issue/index.html", "/book/issue/part2", "/book/issue/part3"))
        other = BOOKS + "/book/other/part1"
        transport = FixtureTransport({
            seed: reader(CDN + "/1.png", next_url=second),
            second: reader(CDN + "/2.png", next_url=third),
            third: reader(CDN + "/3.png", next_url=other),
        })
        result = await SourceClient(config(reader_next_within_issue=True), transport).discover(seed)
        self.assertTrue(result["complete"])
        self.assertEqual(len(result["pages"]), 3)
        self.assertIn("pagination_outside_issue", codes(result))
        self.assertNotIn(other, transport.calls)

    async def test_guard_accepts_explicit_page_routes_and_keeps_declared_issue_query_fixed(self):
        first = BOOKS + "/book/issue/page/1?chapter=1&page=1"
        second = BOOKS + "/book/issue/page/2?chapter=1&page=2"
        outside = BOOKS + "/book/issue/page/3?chapter=2&page=3"
        transport = FixtureTransport({first: reader(CDN + "/1.png", next_url=second), second: reader(CDN + "/2.png", next_url=outside)})
        result = await SourceClient(config(reader_next_within_issue=True, identity_query_params=("chapter",), page_number_param="page"), transport).discover(first)
        self.assertTrue(result["complete"])
        self.assertEqual([page["page_number"] for page in result["pages"]], [1, 2])
        self.assertNotIn(outside, transport.calls)

    async def test_guard_rejects_undeclared_query_routing_and_nonboolean_configuration(self):
        seed, outside = BOOKS + "/book/issue", BOOKS + "/book/issue?page=2&token=PRIVATE"
        transport = FixtureTransport({seed: reader(CDN + "/1.png", next_url=outside)})
        result = await SourceClient(config(reader_next_within_issue=True, page_number_param="page"), transport).discover(seed)
        self.assertTrue(result["complete"])
        self.assertEqual(len(result["pages"]), 1)
        self.assertNotIn(outside, transport.calls)
        self.assertNotIn("PRIVATE", json.dumps(result))
        for flag in ("true", 1, None):
            with self.assertRaises(SourceError):
                SourceClient(config(reader_next_within_issue=flag))

    async def test_advanced_default_keeps_preexisting_explicit_cross_path_behavior(self):
        seed, other = BOOKS + "/book/one", BOOKS + "/book/two"
        transport = FixtureTransport({seed: reader(CDN + "/1.png", next_url=other), other: reader(CDN + "/2.png")})
        result = await SourceClient(config(), transport).discover(seed)
        self.assertTrue(result["complete"])
        self.assertEqual(len(result["pages"]), 2)
        self.assertIn(other, transport.calls)


if __name__ == "__main__":
    unittest.main()
