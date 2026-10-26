"""Authorized, declarative source discovery; no site-specific scraping or model logic.

``SourceClient(SourceConfig(...))`` accepts the equivalent nested mapping. Profiles
name exact approved origins, path roots and CSS selectors; authorization is an
explicit caller attestation, not something inferred from a public URL. There is
no catch-all website adapter. JavaScript, authentication and access challenges
are not executed or bypassed. Discovery of HTML collects links, not artwork.

``await discover(url, scope='auto', progress=None, cancel=None, cursor=None)``
returns cumulative JSON-compatible metadata. ``chapter`` aliases ``issue``.
A progress callback receives that same manifest shape and may be async; cancellation
is a synchronous predicate or an object with ``is_set()``. Per-call budgets pause
with an opaque, client-local cursor. Hard state bounds are explicit ``limited``
results, not successful first-N results. Restarting requires rediscovery; merge
persisted pages by page_key, not fetch_id. Query-only chapters need explicitly
declared identity_query_params; ambiguity is reported rather than silently merged.
Implicit page identity never depends on gallery order. Automatic setup enables
reader_next_within_issue: semantic next links stay within the original issue's
origin, path and declared routing, never the current pagination document's scope.
Advanced profiles keep their explicit preexisting pagination unless they opt in.
Explicitly declared image_identity_query_params identify image endpoints; otherwise only an opaque
SHA-256 of the exact URL contributes to page_key. Token rotation without declared
image semantics intentionally changes identity: the caller must pause when old
page keys disappear, never substitute new images for previously completed pages.
Same-image occurrences in different issues or at distinct explicit page numbers
are retained; redundant references within the same issue/page are deduplicated.

``persistent_seed(url)`` is the offline gate before saving any user-supplied seed:
it rejects fragments, credentials and nonsemantic/secret-like query parameters,
without silently changing the resource. It does not establish permission or DNS.
Every exported URL omits query/fragment/credentials. Pass a page's opaque fetch_id
to ``fetch_image`` to use its private exact URL. References and cursors are bounded,
memory-only and renewed while this client is active. Idle expiry still requires
rediscovery (or a privately refreshed signed seed); never persist exact signed URLs.
Production requires aiohttp, imported only on first real request. Test transports
implement resolve() and open(); there is no allow-private-network switch.
TransportResponse.headers accepts a mapping or ordered (name, value) pairs. Real
transports must preserve repeated fields; restrictive robots directives accumulate.

The transport binds validated DNS answers to the actual socket resolver. Every
redirect is manually checked, including robots and origin/path scope. No cookies,
credentials, referrers, proxies, automatic decompression or automatic retries are
used. Raster MIME/signature checks here do not replace downstream image decoding,
pixel limits or decompression-bomb checks.
"""
from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
import hashlib
import importlib
import inspect
import ipaddress
import json
import math
import re
import secrets
import socket
import time
import unicodedata
from typing import Any, AsyncContextManager, Protocol
from urllib.parse import parse_qs, parse_qsl, quote, unquote, urlencode, urljoin, urlsplit, urlunsplit


from . import __version__

USER_AGENT = "TechFarmer/" + __version__
_IMAGE_TYPES = {"image/png", "image/jpeg", "image/gif", "image/webp"}
_HTML_TYPES = {"text/html", "application/xhtml+xml"}
_REDIRECTS = {301, 302, 303, 307, 308}
_MESSAGES = {
    "authorization_required": "Configure an explicitly authorized source profile before fetching material.",
    "invalid_config": "The source profile or resource limits are invalid.",
    "invalid_url": "Use a well-formed public HTTP(S) URL without credentials or nonstandard ports.",
    "unsafe_persistent_seed": "A saved seed may contain only explicitly declared simple routing parameters, never credentials, signatures, tokens or fragments; provide a public route or refresh the source privately.",
    "unsafe_address": "The source resolves to a private, reserved, local or otherwise unsafe address.",
    "out_of_scope": "A linked resource is outside the explicitly approved origins or paths.",
    "ambiguous_profile": "More than one source profile matches this URL; narrow the configured paths.",
    "identity_ambiguous": "Distinct chapter links share a redacted route; declare their semantic identity_query_params before collecting them.",
    "unsupported_layout": "No supported reader images or declared chapter links were found; JavaScript-only and unknown layouts are unsupported.",
    "unsupported_image": "The resource is not a supported PNG, JPEG, GIF or WebP image.",
    "image_type_mismatch": "The image signature does not match its declared raster content type.",
    "content_type": "The response has a missing or unsupported content type.",
    "compressed_response": "Compressed HTTP responses are refused; the server must honor identity encoding.",
    "response_too_large": "The streamed response exceeds its configured byte limit.",
    "invalid_response": "The server returned malformed response metadata or a truncated response.",
    "access_denied": "The server denied access; authentication, paywalls and access challenges are not bypassed.",
    "rate_limited": "The server rate-limited access; no alternate identity or automatic retry is attempted.",
    "http_error": "The server did not return a successful response; no automatic retry is attempted.",
    "timeout": "A source request exceeded its time limit.",
    "transport_failure": "The source request failed; private request details have been omitted.",
    "dns_failure": "The source hostname could not be resolved safely.",
    "redirect_limit": "The response exceeded the redirect limit or redirected in a loop.",
    "redirect_downgrade": "An HTTPS resource attempted to redirect to insecure HTTP.",
    "robots_denied": "The source's robots.txt disallows this resource.",
    "robots_unavailable": "The source's robots.txt could not be evaluated safely; access is refused.",
    "robots_delay": "The source requests a crawl delay beyond the configured waiting limit; access is refused.",
    "site_restricted": "The response declares a robots or indexing restriction; access is refused.",
    "dependency_missing": "Production fetching requires aiohttp; install the declared dependency before using real sources.",
    "html_dependency_missing": "HTML discovery requires beautifulsoup4; install the declared dependency.",
    "cancelled": "Discovery or fetching was cancelled; remaining work has not been fetched.",
    "cancel_check_failed": "The cancellation callback failed; fetching was stopped.",
    "progress_failed": "The progress callback failed; discovery was paused with its accumulated manifest.",
    "document_budget": "The per-call document budget was reached; resume with the returned cursor.",
    "page_budget": "The per-call page budget was reached; resume with the returned cursor.",
    "state_limit": "The configured total discovery/state limit was reached; rediscover with an explicitly larger budget and deduplicate saved pages.",
    "session_limit": "Too many live discovery sessions are retained; finish, close or allow them to expire.",
    "invalid_cursor": "The cursor is unknown, expired, or belongs to a different source URL or scope; rediscover instead.",
    "reference_expired": "The private image reference is unknown or expired; rediscover to refresh it.",
    "ambiguous_reference": "Several private images share this redacted URL; use the page's fetch_id.",
    "pagination_loop": "A repeated pagination link was skipped without guessing another URL.",
    "pagination_outside_issue": "A next link outside this issue's origin, path or declared routing was skipped rather than assigned to this issue.",
    "page_number_conflict": "A repeated declared page number was retained separately and assigned a unique discovery page number.",
    "additional_problems": "Additional source problems were counted after the problem-detail limit.",
    "closed": "This source client is closed; create a new configured client.",
}


class SourceError(Exception):
    """A fixed, safe error; never includes a raw request URL or transport exception."""

    def __init__(self, code: str):
        self.code = code if code in _MESSAGES else "transport_failure"
        self.message = _MESSAGES[self.code]
        super().__init__(self.message)


@dataclass(frozen=True)
class SourceLimits:
    max_html_bytes: int = 2 * 1024 * 1024
    max_image_bytes: int = 20 * 1024 * 1024
    max_robots_bytes: int = 128 * 1024
    max_documents_per_batch: int = 64
    max_documents: int = 10000
    max_pages_per_batch: int = 250
    max_issues: int = 1000
    max_pages: int = 5000
    max_pending: int = 12000
    max_references: int = 10000
    max_sessions: int = 4
    max_problems: int = 200
    max_robots_rules: int = 2000
    max_dns_addresses: int = 32
    max_redirects: int = 4
    request_timeout: float = 20.0
    connect_timeout: float = 8.0
    max_robots_delay: float = 60.0
    session_ttl: float = 1800.0


@dataclass(frozen=True)
class SourceProfile:
    name: str
    page_origins: tuple[str, ...]
    image_origins: tuple[str, ...] = ()
    page_paths: tuple[str, ...] = ("/",)
    image_paths: tuple[str, ...] = ("/",)
    chapter_selector: str | None = "[data-chapter-list] a[href], .chapter-list a[href]"
    reader_selector: str | None = "[data-reader] img, .comic-reader img"
    series_next_selector: str | None = "a[data-series-next][href]"
    reader_next_selector: str | None = "a[data-page-next][href]"
    exclude_selector: str | None = ".ad, .ads, .advertisement, .cover, .thumbnail, .icon, .logo, [data-ad], [data-role='cover']"
    page_number_attribute: str = "data-page-number"
    page_number_param: str | None = None
    issue_title_selector: str | None = None
    identity_query_params: tuple[str, ...] = ()
    image_identity_query_params: tuple[str, ...] = ()
    reader_next_within_issue: bool = False


@dataclass(frozen=True)
class SourceConfig:
    profiles: tuple[SourceProfile, ...] = ()
    authorized: bool = False
    allow_http: bool = False
    limits: SourceLimits = field(default_factory=SourceLimits)


HeaderFields = Mapping[str, str] | Sequence[tuple[str, str]]


@dataclass(repr=False)
class TransportResponse:
    status: int
    headers: HeaderFields
    body: AsyncIterator[bytes]


def _response_headers(fields: HeaderFields) -> dict[str, str]:
    if isinstance(fields, Mapping):
        pairs = fields.items()
    elif isinstance(fields, Sequence) and not isinstance(fields, (str, bytes)):
        pairs = fields
    else:
        raise SourceError("invalid_response")
    if len(fields) > 128:
        raise SourceError("invalid_response")
    result, size = {}, 0
    for pair in pairs:
        if not isinstance(pair, (tuple, list)) or len(pair) != 2:
            raise SourceError("invalid_response")
        name, value = pair
        if not isinstance(name, str) or not isinstance(value, str) or not re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+", name):
            raise SourceError("invalid_response")
        if any(character in value for character in "\r\n\x00"):
            raise SourceError("invalid_response")
        size += len(name) + len(value)
        if size > 64 * 1024:
            raise SourceError("invalid_response")
        key = name.lower()
        if key in result:
            if key in {"content-type", "content-length", "location"}:
                raise SourceError("invalid_response")
            result[key] += ", " + value
        else:
            result[key] = value
    return result


class SourceTransport(Protocol):
    async def resolve(self, host: str, port: int) -> Sequence[str]: ...

    def open(
        self, url: str, *, addresses: tuple[str, ...], headers: Mapping[str, str],
        timeout: float, connect_timeout: float,
    ) -> AsyncContextManager[TransportResponse]: ...


def _public_ip(address: str) -> str:
    try:
        ip = ipaddress.ip_address(address)
    except (ValueError, TypeError):
        raise SourceError("unsafe_address") from None
    if not ip.is_global or ip.is_multicast or ip.is_reserved or ip.is_unspecified:
        raise SourceError("unsafe_address")
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.is_site_local or ip.ipv4_mapped or ip.sixtofour or ip.teredo or any(
            ip in network for network in (
                ipaddress.ip_network("64:ff9b::/96"),
                ipaddress.ip_network("64:ff9b:1::/48"),
            )
        ):
            raise SourceError("unsafe_address")
    return str(ip)


def _decoded_path(path: str) -> str:
    value = path or "/"
    for _ in range(4):
        decoded = unquote(value, errors="strict")
        if decoded == value:
            break
        value = decoded
    else:
        raise SourceError("invalid_url")
    if "\\" in value or any(unicodedata.category(ch).startswith("C") for ch in value):
        raise SourceError("invalid_url")
    if any(part in {".", ".."} for part in value.split("/")):
        raise SourceError("invalid_url")
    return value


def _url(value: str, allow_http: bool = False) -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= 4096:
        raise SourceError("invalid_url")
    if any(ch.isspace() or unicodedata.category(ch).startswith("C") for ch in value) or "\\" in value:
        raise SourceError("invalid_url")
    try:
        parsed = urlsplit(value)
        if parsed.scheme not in ({"https", "http"} if allow_http else {"https"}):
            raise SourceError("invalid_url")
        if not parsed.hostname or parsed.username is not None or parsed.password is not None:
            raise SourceError("invalid_url")
        host = parsed.hostname.encode("idna").decode("ascii").lower().rstrip(".")
        if not host or "%" in host:
            raise SourceError("invalid_url")
        port = parsed.port
        if port is not None and port != (443 if parsed.scheme == "https" else 80):
            raise SourceError("invalid_url")
        try:
            ipaddress.ip_address(host)
        except ValueError:
            labels = host.split(".")
            if len(labels) < 2 or len(host) > 253 or not re.fullmatch(r"[a-z][a-z0-9-]*", labels[-1]):
                raise SourceError("invalid_url")
            if any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in labels):
                raise SourceError("invalid_url")
            if host == "localhost" or host.endswith((".localhost", ".local", ".internal", ".lan", ".home", ".onion")):
                raise SourceError("unsafe_address")
        else:
            host = _public_ip(host)
        if re.search(r"%(?![0-9a-fA-F]{2})", parsed.path + parsed.query):
            raise SourceError("invalid_url")
        _decoded_path(parsed.path)
        path = quote(parsed.path or "/", safe="/%:@!$&'()*+,;=-._~")
        query = quote(parsed.query, safe="/%?:@!$&'()*+,;=-._~")
        authority = f"[{host}]" if ":" in host else host
        canonical = urlunsplit((parsed.scheme, authority, path, query, ""))
        if len(canonical) > 4096:
            raise SourceError("invalid_url")
        return canonical
    except (ValueError, UnicodeError):
        raise SourceError("invalid_url") from None


def redact_url(value: str) -> str:
    """Safe provenance only. Invalid/credentialed input is omitted, not echoed."""
    try:
        parsed = urlsplit(_url(value, allow_http=True))
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))
    except SourceError:
        return ""


def _origin(value: str) -> str:
    parsed = urlsplit(value)
    return f"{parsed.scheme}://{parsed.netloc}"


def _clean_label(value: Any, fallback: str = "Issue") -> str:
    text = " ".join("".join(" " if unicodedata.category(ch).startswith("C") else ch for ch in str(value or "")).split())
    text = re.sub(r"https?://\S+", lambda match: redact_url(match.group()) or "[link]", text, flags=re.I)
    return text[:120] or str(fallback)[:120]


def _natural_key(value: str) -> tuple:
    return tuple((1, int(part)) if part.isdecimal() else (0, part.casefold()) for part in re.split(r"(\d+)", value))


class _PinnedResolver:
    def __init__(self, host: str, port: int, addresses: tuple[str, ...]):
        self.host = host
        self.port = port
        self.addresses = tuple(_public_ip(address) for address in addresses)

    async def resolve(self, host: str, port: int = 0, family: int = socket.AF_UNSPEC) -> list[dict]:
        if host.lower().rstrip(".") != self.host or port != self.port:
            raise SourceError("unsafe_address")
        records = []
        for address in self.addresses:
            address_family = socket.AF_INET6 if ":" in address else socket.AF_INET
            if family not in (socket.AF_UNSPEC, address_family):
                continue
            records.append({"hostname": self.host, "host": address, "port": self.port,
                            "family": address_family, "proto": socket.IPPROTO_TCP,
                            "flags": socket.AI_NUMERICHOST})
        if not records:
            raise SourceError("dns_failure")
        return records

    async def close(self) -> None:
        return None


class AiohttpTransport:
    """No persistent pool: each request can connect only to its validated answers."""

    @staticmethod
    def _library():
        try:
            return importlib.import_module("aiohttp")
        except ImportError:
            raise SourceError("dependency_missing") from None

    async def resolve(self, host: str, port: int) -> Sequence[str]:
        self._library()
        records = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM, family=socket.AF_UNSPEC)
        return tuple(dict.fromkeys(record[4][0] for record in records))

    @asynccontextmanager
    async def open(self, url: str, *, addresses: tuple[str, ...], headers: Mapping[str, str],
                   timeout: float, connect_timeout: float) -> AsyncIterator[TransportResponse]:
        aiohttp = self._library()
        try:
            exact_url = importlib.import_module("yarl").URL(url, encoded=True)
        except ImportError:
            raise SourceError("dependency_missing") from None
        parsed = urlsplit(url)
        resolver = _PinnedResolver(parsed.hostname, 443 if parsed.scheme == "https" else 80, addresses)
        connector = aiohttp.TCPConnector(resolver=resolver, use_dns_cache=False, family=socket.AF_UNSPEC, limit=1)
        deadline = aiohttp.ClientTimeout(total=timeout, connect=min(timeout, connect_timeout), sock_read=timeout)
        async with aiohttp.ClientSession(
            connector=connector, timeout=deadline, trust_env=False,
            cookie_jar=aiohttp.DummyCookieJar(), auto_decompress=False,
            max_line_size=8190, max_field_size=8190,
        ) as session:
            async with session.get(exact_url, headers=dict(headers), allow_redirects=False, proxy=None) as response:
                if len(response.raw_headers) > 128 or sum(len(name) + len(value) for name, value in response.raw_headers) > 64 * 1024:
                    raise SourceError("invalid_response")
                fields = tuple((name.decode("ascii"), value.decode("latin-1")) for name, value in response.raw_headers)
                yield TransportResponse(response.status, fields, response.content.iter_chunked(64 * 1024))


@dataclass(frozen=True, repr=False)
class _Profile:
    source: SourceProfile
    page_origins: tuple[str, ...]
    image_origins: tuple[str, ...]
    page_paths: tuple[str, ...]
    image_paths: tuple[str, ...]

    def accepts(self, url: str, role: str) -> bool:
        origin, path = _origin(url), _decoded_path(urlsplit(url).path)
        groups = []
        if role in {"page", "either"}:
            groups.append((self.page_origins, self.page_paths))
        if role in {"image", "either"}:
            groups.append((self.image_origins, self.image_paths))
        if role == "robots":
            return origin in self.page_origins + self.image_origins and path == "/robots.txt"
        return any(origin in origins and any(prefix == "/" or path == prefix.rstrip("/") or path.startswith(prefix.rstrip("/") + "/")
                                            for prefix in paths) for origins, paths in groups)


def _configuration(config: SourceConfig | Mapping) -> tuple[SourceConfig, tuple[_Profile, ...]]:
    try:
        if isinstance(config, Mapping):
            values = dict(config)
            values["profiles"] = tuple(profile if isinstance(profile, SourceProfile) else SourceProfile(**profile)
                                       for profile in values.get("profiles", ()))
            if isinstance(values.get("limits"), Mapping):
                values["limits"] = SourceLimits(**values["limits"])
            config = SourceConfig(**values)
        if not isinstance(config, SourceConfig) or type(config.allow_http) is not bool:
            raise SourceError("invalid_config")
        if config.authorized is not True:
            raise SourceError("authorization_required")
        limits = config.limits
        if not isinstance(limits, SourceLimits):
            raise SourceError("invalid_config")
        for name, value in vars(limits).items():
            if name in {"request_timeout", "connect_timeout", "max_robots_delay", "session_ttl"}:
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 < value <= 86400:
                    raise SourceError("invalid_config")
            elif type(value) is not int or value < (0 if name == "max_redirects" else 1) or value > 64 * 1024 * 1024:
                raise SourceError("invalid_config")
        if limits.max_redirects > 10 or limits.max_sessions > 32 or limits.max_html_bytes > 8 * 1024 * 1024 or limits.max_robots_bytes > 512 * 1024:
            raise SourceError("invalid_config")
        if not 1 <= len(config.profiles) <= 16:
            raise SourceError("invalid_config")
        profiles, names = [], set()
        for profile in config.profiles:
            if not isinstance(profile, SourceProfile) or not isinstance(profile.name, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", profile.name) or profile.name in names:
                raise SourceError("invalid_config")
            names.add(profile.name)
            origin_groups = []
            for group in (profile.page_origins, profile.image_origins or profile.page_origins):
                if isinstance(group, str) or not 1 <= len(group) <= 32:
                    raise SourceError("invalid_config")
                origins = []
                for raw in group:
                    value = _url(raw, config.allow_http)
                    parsed = urlsplit(value)
                    if parsed.path != "/" or parsed.query or urlsplit(raw).fragment:
                        raise SourceError("invalid_config")
                    origins.append(_origin(value))
                origin_groups.append(tuple(dict.fromkeys(origins)))
            path_groups = []
            for group in (profile.page_paths, profile.image_paths):
                if isinstance(group, str) or not 1 <= len(group) <= 32:
                    raise SourceError("invalid_config")
                paths = []
                for raw in group:
                    if not isinstance(raw, str) or not raw.startswith("/") or "?" in raw or "#" in raw or len(raw) > 2048:
                        raise SourceError("invalid_config")
                    paths.append(_decoded_path(raw))
                path_groups.append(tuple(paths))
            for name in ("chapter_selector", "reader_selector", "series_next_selector", "reader_next_selector", "exclude_selector", "issue_title_selector"):
                selector = getattr(profile, name)
                if selector is not None and (not isinstance(selector, str) or not 1 <= len(selector) <= 1024):
                    raise SourceError("invalid_config")
            for name in ("page_number_attribute", "page_number_param"):
                value = getattr(profile, name)
                if value is not None and (not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", value)):
                    raise SourceError("invalid_config")
            if type(profile.reader_next_within_issue) is not bool:
                raise SourceError("invalid_config")
            for parameters in (profile.identity_query_params, profile.image_identity_query_params):
                if isinstance(parameters, str) or len(parameters) > 16 or any(
                    not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", name)
                    for name in parameters
                ):
                    raise SourceError("invalid_config")
            profiles.append(_Profile(profile, *origin_groups, *path_groups))
        return config, tuple(profiles)
    except (TypeError, ValueError, AttributeError, UnicodeError):
        raise SourceError("invalid_config") from None


def _robots_normalize(value: str) -> str:
    value = quote(value, safe="/%?=&:@!$'()+,;*-._~")
    def replacement(match):
        byte = int(match.group()[1:], 16)
        return chr(byte) if chr(byte) in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~" else f"%{byte:02X}"
    return re.sub(r"%[0-9a-fA-F]{2}", replacement, value)


def _robots_match(pattern: str, target: str) -> bool:
    anchored = pattern.endswith("$")
    pieces = (pattern[:-1] if anchored else pattern).split("*")
    if not target.startswith(pieces[0]):
        return False
    if len(pieces) == 1:
        return not anchored or target == pieces[0]
    position = len(pieces[0])
    for piece in pieces[1:-1]:
        location = target.find(piece, position)
        if location < 0:
            return False
        position = location + len(piece)
    tail = pieces[-1]
    if anchored:
        return target.endswith(tail) and len(target) - len(tail) >= position
    return target.find(tail, position) >= 0


@dataclass(repr=False)
class _Robots:
    rules: tuple[tuple[str, bool], ...] = ()
    delay: float = 0.0

    def allows(self, url: str) -> bool:
        parsed = urlsplit(url)
        target = _robots_normalize(parsed.path + ("?" + parsed.query if parsed.query else ""))
        matching = [(len(pattern.replace("*", "").removesuffix("$")), allowed)
                    for pattern, allowed in self.rules if _robots_match(pattern, target)]
        return max(matching, default=(-1, True))[1]


def _parse_robots(body: bytes, limits: SourceLimits) -> _Robots:
    try:
        text = body.decode("utf-8-sig", errors="strict")
    except UnicodeError:
        raise SourceError("robots_unavailable") from None
    if text.lstrip().startswith("<"):
        raise SourceError("robots_unavailable")
    groups, agents, directives = [], [], []
    count = 0
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        key, value = (part.strip() for part in line.split(":", 1))
        key = key.lower()
        if key == "user-agent":
            if directives:
                groups.append((agents, directives))
                agents, directives = [], []
            agents.append(value.lower())
        elif agents and key in {"allow", "disallow", "crawl-delay", "request-rate"}:
            count += 1
            if count > limits.max_robots_rules or len(value) > 4096:
                raise SourceError("robots_unavailable")
            directives.append((key, value))
    if agents:
        groups.append((agents, directives))
    product = USER_AGENT.split("/", 1)[0].lower()
    selected, best = [], -1
    for names, rules in groups:
        specificity = max((0 if name == "*" else len(name) for name in names if name == "*" or name in product), default=-1)
        if specificity > best:
            selected, best = list(rules), specificity
        elif specificity == best and specificity >= 0:
            selected.extend(rules)
    result, delay = [], 0.0
    for key, value in selected:
        if key in {"allow", "disallow"}:
            if value:
                if not value.startswith("/") and value != "*":
                    raise SourceError("robots_unavailable")
                result.append((_robots_normalize(value), key == "allow"))
        else:
            try:
                if key == "crawl-delay":
                    seconds = float(value)
                else:
                    requests, interval = value.split("/", 1)
                    seconds = float(interval) / float(requests)
                if not math.isfinite(seconds) or seconds < 0:
                    raise ValueError
                delay = max(delay, seconds)
            except (ValueError, ZeroDivisionError):
                raise SourceError("robots_unavailable") from None
    if delay > limits.max_robots_delay:
        raise SourceError("robots_delay")
    return _Robots(tuple(result), delay)


@dataclass(repr=False)
class _Resource:
    url: str
    status: int
    content_type: str
    body: bytes


@dataclass(repr=False)
class _Task:
    kind: str
    url: str
    issue_url: str = ""
    label: str = ""
    number: int | None = None


@dataclass(frozen=True, repr=False)
class _Link:
    href: str
    label: str = ""
    number: int | None = None


@dataclass(frozen=True, repr=False)
class _ParsedHTML:
    base: str
    had_images: bool
    images: tuple[tuple[str | None, int | None], ...]
    chapters: tuple[_Link, ...]
    series_next: tuple[_Link, ...]
    reader_next: tuple[_Link, ...]
    title: str


@dataclass(repr=False)
class _State:
    source: str
    requested_scope: str
    profile: _Profile
    token: str
    expires: float
    scope: str = "auto"
    phase: str = "seed"
    todo: deque = field(default_factory=deque)
    seen_documents: set[str] = field(default_factory=set)
    seen_reader_documents: dict[str, set[str]] = field(default_factory=dict)
    seen_images: dict[tuple[str, tuple], set[int]] = field(default_factory=dict)
    issues: dict[str, str] = field(default_factory=dict)
    issue_identities: dict[tuple, str] = field(default_factory=dict)
    pages: list[dict] = field(default_factory=list)
    page_numbers: dict[str, set[int]] = field(default_factory=dict)
    problems: list[dict] = field(default_factory=list)
    documents: int = 0
    failed: bool = False


@dataclass(repr=False)
class _Reference:
    url: str
    profile: _Profile
    expires: float


class _StopRequested(Exception):
    pass


def _check_cancel(cancel) -> None:
    if cancel is None:
        return
    try:
        value = cancel.is_set() if hasattr(cancel, "is_set") else cancel()
        if inspect.isawaitable(value):
            if inspect.iscoroutine(value):
                value.close()
            raise SourceError("cancel_check_failed")
        if value:
            raise _StopRequested
    except (_StopRequested, SourceError):
        raise
    except Exception:
        raise SourceError("cancel_check_failed") from None


def _raster_type(body: bytes) -> str | None:
    if body.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if body.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if body.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if len(body) >= 12 and body[:4] == b"RIFF" and body[8:12] == b"WEBP":
        return "image/webp"
    return None


def _validate_image(resource: _Resource) -> None:
    if resource.content_type not in _IMAGE_TYPES:
        raise SourceError("unsupported_image")
    if _raster_type(resource.body) != resource.content_type:
        raise SourceError("image_type_mismatch")


def _srcset(value: str) -> list[tuple[tuple[int, float], str]]:
    result, position = [], 0
    while position < len(value):
        while position < len(value) and (value[position].isspace() or value[position] == ","):
            position += 1
        start = position
        while position < len(value) and not value[position].isspace():
            position += 1
        candidate = value[start:position]
        if not candidate:
            break
        if candidate.endswith(","):
            candidate, descriptor = candidate.rstrip(","), ""
        else:
            start = position
            while position < len(value) and value[position] != ",":
                position += 1
            descriptor = value[start:position].strip()
        position += 1
        if candidate.startswith(("data:", "blob:")):
            continue
        if not descriptor:
            rank = (0, 1.0)
        elif re.fullmatch(r"[1-9][0-9]*w", descriptor):
            rank = (1, float(descriptor[:-1]))
        elif re.fullmatch(r"(?:[0-9]+(?:\.[0-9]+)?)x", descriptor):
            rank = (0, float(descriptor[:-1]))
        else:
            continue
        if math.isfinite(rank[1]) and rank[1] > 0:
            result.append((rank, candidate))
    return result


def _image_attribute(node) -> str | None:
    picture = node if node.name == "picture" else node.find_parent("picture")
    primary = picture.find("img") if picture is not None else node
    primary = primary or node
    for attribute in ("data-original", "data-full", "data-full-src", "data-zoom-image"):
        value = primary.get(attribute)
        if isinstance(value, str) and value.strip():
            return value.strip()
    candidates = []
    elements = [primary] + (picture.find_all("source") if picture is not None else [])
    for element in elements:
        if element.name == "source" and element.get("type") and element.get("type").lower() not in _IMAGE_TYPES:
            continue
        for attribute in ("data-srcset", "srcset"):
            value = element.get(attribute)
            if isinstance(value, str):
                candidates.extend(_srcset(value))
    if candidates:
        return max(candidates, key=lambda candidate: candidate[0])[1]
    for attribute in ("data-src", "src", "href"):
        value = primary.get(attribute)
        if isinstance(value, str) and value.strip() and not value.startswith(("data:", "blob:")):
            return value.strip()
    return None


def _positive_number(value: Any) -> int | None:
    return int(value) if isinstance(value, str) and re.fullmatch(r"[1-9][0-9]{0,8}", value) else None


def _issue_path_root(path: str) -> str:
    """Bound a reader's index or explicit /page/N route, never invent a URL."""
    path = _decoded_path(path).rstrip("/")
    if path.rsplit("/", 1)[-1].lower() in {"index.html", "index.htm", "index.php"}:
        path = path.rsplit("/", 1)[0]
    return re.sub(r"/page/[1-9][0-9]{0,8}$", "", path).rstrip("/")


def _within_issue_page(target: str, issue: str, profile: SourceProfile) -> bool:
    boundary = _issue_path_root(urlsplit(issue).path)
    path = _decoded_path(urlsplit(target).path)
    if _origin(target) != _origin(issue) or not (
        path.rstrip("/") == boundary or (boundary and path.startswith(boundary + "/"))
    ):
        return False
    try:
        original = parse_qsl(urlsplit(issue).query, keep_blank_values=True, strict_parsing=True, max_num_fields=17)
        linked = parse_qsl(urlsplit(target).query, keep_blank_values=True, strict_parsing=True, max_num_fields=17)
    except ValueError:
        return False
    allowed = set(profile.identity_query_params)
    if profile.page_number_param:
        allowed.add(profile.page_number_param)
    for pairs in (original, linked):
        if len({key for key, _ in pairs}) != len(pairs) or any(key not in allowed for key, _ in pairs):
            return False
    before, after = dict(original), dict(linked)
    page_parameter = profile.page_number_param
    if page_parameter and page_parameter in after and _positive_number(after[page_parameter]) is None:
        return False
    return all(before.get(key) == after.get(key) for key in allowed - {page_parameter})


class SourceClient:
    def __init__(self, config: SourceConfig | Mapping, transport: SourceTransport | None = None):
        self.config, self._profiles = _configuration(config)
        self.limits = self.config.limits
        self._transport = transport if transport is not None else AiohttpTransport()
        self._sessions: dict[str, _State] = {}
        self._references: dict[str, _Reference] = {}
        self._reference_urls: dict[tuple[str, str], str] = {}
        self._robots: dict[str, _Robots] = {}
        self._robots_failures: dict[str, str] = {}
        self._next_request: dict[str, float] = {}
        self._lock = asyncio.Lock()
        self._closed = False

    def persistent_seed(self, url: str) -> str:
        """Offline, scope-checked persistence input, not a DNS/access guarantee.

        Only declared semantic keys with short numeric/lowercase-slug values can
        survive. Exact signed links belong exclusively in memory; this method
        never silently strips a key and thereby changes the requested resource.
        """
        if self._closed:
            raise SourceError("closed")
        if not isinstance(url, str) or "#" in url:
            raise SourceError("unsafe_persistent_seed")
        value = _url(url, self.config.allow_http)
        profile = self._profile(value, "either").source
        allowed = set(profile.identity_query_params)
        if profile.page_number_param:
            allowed.add(profile.page_number_param)
        parsed = urlsplit(value)
        try:
            parameters = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True, max_num_fields=17)
        except ValueError:
            raise SourceError("unsafe_persistent_seed") from None
        names = set()
        for name, parameter in parameters:
            sensitive = re.sub(r"[^a-z0-9]", "", name.lower())
            if name not in allowed or name in names or re.search(
                r"token|secret|signature|sig$|auth|pass|session|cookie|credential|key|expiry|expires|nonce|oauth|saml|ticket|jwt|email|user", sensitive
            ):
                raise SourceError("unsafe_persistent_seed")
            simple = bool(re.fullmatch(r"[0-9]{1,12}(?:\.[0-9]{1,3})?|[a-z][a-z0-9-]{0,39}", parameter))
            if not simple or re.search(r"[a-z0-9]{21,}|[a-f0-9]{16,}", parameter):
                raise SourceError("unsafe_persistent_seed")
            if name == profile.page_number_param and _positive_number(parameter) is None:
                raise SourceError("unsafe_persistent_seed")
            names.add(name)
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(sorted(parameters)), ""))

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        await self.aclose()

    async def aclose(self) -> None:
        async with self._lock:
            self._sessions.clear()
            self._references.clear()
            self._reference_urls.clear()
            self._robots.clear()
            self._robots_failures.clear()
            self._next_request.clear()
            self._closed = True

    def _prune(self) -> None:
        if self._closed:
            raise SourceError("closed")
        now = time.monotonic()
        self._sessions = {key: state for key, state in self._sessions.items() if state.expires > now}
        expired = [key for key, reference in self._references.items() if reference.expires <= now]
        for key in expired:
            reference = self._references.pop(key)
            self._reference_urls.pop((reference.profile.source.name, reference.url), None)

    def _touch_private_state(self) -> None:
        expires = time.monotonic() + self.limits.session_ttl
        for reference in self._references.values():
            reference.expires = expires
        for state in self._sessions.values():
            state.expires = expires

    def _scoped(self, value: str, profile: _Profile, role: str) -> str:
        value = _url(value, self.config.allow_http)
        if not profile.accepts(value, role):
            raise SourceError("out_of_scope")
        return value

    def _profile(self, value: str, role: str) -> _Profile:
        matches = [profile for profile in self._profiles if profile.accepts(value, role)]
        if not matches:
            raise SourceError("out_of_scope")
        if len(matches) != 1:
            raise SourceError("ambiguous_profile")
        return matches[0]

    async def _addresses(self, url: str) -> tuple[str, ...]:
        parsed = urlsplit(url)
        host, port = parsed.hostname, 443 if parsed.scheme == "https" else 80
        try:
            ipaddress.ip_address(host)
        except ValueError:
            try:
                addresses = await self._transport.resolve(host, port)
            except SourceError:
                raise
            except Exception:
                raise SourceError("dns_failure") from None
        else:
            addresses = (host,)
        if isinstance(addresses, str) or not isinstance(addresses, Sequence) or not 1 <= len(addresses) <= self.limits.max_dns_addresses:
            raise SourceError("dns_failure")
        return tuple(dict.fromkeys(_public_ip(address) for address in addresses))

    async def _pace(self, origin: str, cancel) -> None:
        while True:
            _check_cancel(cancel)
            remaining = self._next_request.get(origin, 0) - time.monotonic()
            if remaining <= 0:
                self._next_request[origin] = time.monotonic() + self._robots[origin].delay
                return
            await asyncio.sleep(min(remaining, 0.1))

    async def _ensure_robots(self, url: str, profile: _Profile, cancel) -> None:
        origin = _origin(url)
        if origin in self._robots_failures:
            raise SourceError(self._robots_failures[origin])
        if origin not in self._robots:
            try:
                response = await self._fetch(origin + "/robots.txt", profile, "robots", "robots", cancel)
                self._robots[origin] = _Robots() if response.status in {404, 410} else _parse_robots(response.body, self.limits)
            except SourceError as error:
                self._robots_failures[origin] = error.code
                raise
        if not self._robots[origin].allows(url):
            raise SourceError("robots_denied")
        await self._pace(origin, cancel)

    async def _fetch(self, url: str, profile: _Profile, role: str, purpose: str, cancel=None) -> _Resource:
        current = self._scoped(url, profile, role)
        original_origin, visited = _origin(current), set()
        for hop in range(self.limits.max_redirects + 1):
            _check_cancel(cancel)
            if current in visited:
                raise SourceError("redirect_limit")
            visited.add(current)
            if purpose != "robots":
                await self._ensure_robots(current, profile, cancel)
            headers = {"User-Agent": USER_AGENT, "Accept-Encoding": "identity", "Accept": "text/plain" if purpose == "robots" else "image/png,image/jpeg,image/webp,image/gif" if purpose == "image" else "text/html,application/xhtml+xml,image/png,image/jpeg,image/webp,image/gif"}
            redirect = None
            try:
                async with asyncio.timeout(self.limits.request_timeout):
                    addresses = await self._addresses(current)
                    _check_cancel(cancel)
                    async with self._transport.open(current, addresses=addresses, headers=headers,
                                                    timeout=self.limits.request_timeout, connect_timeout=self.limits.connect_timeout) as response:
                        if type(response.status) is not int:
                            raise SourceError("invalid_response")
                        received = _response_headers(response.headers)
                        if response.status in _REDIRECTS:
                            if hop == self.limits.max_redirects or not received.get("location"):
                                raise SourceError("redirect_limit")
                            redirect = self._scoped(urljoin(current, received["location"]), profile, role)
                            if current.startswith("https:") and redirect.startswith("http:"):
                                raise SourceError("redirect_downgrade")
                            if purpose == "robots" and _origin(redirect) != original_origin:
                                raise SourceError("robots_unavailable")
                        elif purpose == "robots" and response.status in {404, 410}:
                            self._touch_private_state()
                            return _Resource(current, response.status, "", b"")
                        else:
                            if response.status in {401, 403, 407, 451}:
                                raise SourceError("access_denied")
                            if response.status == 429:
                                raise SourceError("rate_limited")
                            if response.status != 200:
                                raise SourceError("http_error")
                            if received.get("content-encoding", "identity").strip().lower() not in {"", "identity"} or received.get("transfer-encoding", "chunked").strip().lower() not in {"", "chunked"}:
                                raise SourceError("compressed_response")
                            content_type = received.get("content-type", "").split(";", 1)[0].strip().lower()
                            allowed = {"text/plain", "text/x-robots"} if purpose == "robots" else _IMAGE_TYPES if purpose == "image" else _IMAGE_TYPES | _HTML_TYPES
                            if content_type not in allowed:
                                raise SourceError("content_type")
                            if purpose != "robots" and self._restricted(received.get("x-robots-tag", "")):
                                raise SourceError("site_restricted")
                            limit = self.limits.max_robots_bytes if purpose == "robots" else self.limits.max_image_bytes if content_type in _IMAGE_TYPES else self.limits.max_html_bytes
                            declared = received.get("content-length")
                            if declared is not None:
                                if not re.fullmatch(r"[0-9]{1,12}", declared):
                                    raise SourceError("invalid_response")
                                if int(declared) > limit:
                                    raise SourceError("response_too_large")
                            body = bytearray()
                            async for chunk in response.body:
                                _check_cancel(cancel)
                                if not isinstance(chunk, bytes):
                                    raise SourceError("invalid_response")
                                if len(body) + len(chunk) > limit:
                                    raise SourceError("response_too_large")
                                body.extend(chunk)
                            if declared is not None and int(declared) != len(body):
                                raise SourceError("invalid_response")
                            self._touch_private_state()
                            return _Resource(current, response.status, content_type, bytes(body))
            except (SourceError, _StopRequested):
                raise
            except (TimeoutError, asyncio.TimeoutError):
                raise SourceError("timeout") from None
            except Exception:
                raise SourceError("transport_failure") from None
            if redirect is not None:
                current = redirect
        raise SourceError("redirect_limit")

    @staticmethod
    def _restricted(value: str) -> bool:
        return bool(set(re.findall(r"[a-z]+", value.lower())) & {"none", "noindex", "nofollow", "noarchive", "noimageindex", "noai", "noimageai"})

    def _problem(self, state: _State, code: str, url: str, severity: str = "error") -> None:
        state.failed |= severity == "error"
        safe = redact_url(url)
        for problem in state.problems:
            if problem["code"] == code and problem["url"] == safe and problem["severity"] == severity:
                problem["count"] += 1
                return
        if len(state.problems) >= self.limits.max_problems:
            if state.problems[-1]["code"] == "additional_problems":
                state.problems[-1]["count"] += 1
            else:
                state.problems.append({"code": "additional_problems", "message": _MESSAGES["additional_problems"], "url": "", "severity": "error", "count": 1})
            return
        state.problems.append({"code": code, "message": _MESSAGES[code], "url": safe, "severity": severity, "count": 1})

    def _manifest(self, state: _State, status: str, reason: str | None = None) -> dict:
        finished = status in {"complete", "partial", "unsupported", "limited"}
        order = {url: index for index, (url, label) in enumerate(sorted(state.issues.items(), key=lambda item: (_natural_key(item[1]), _natural_key(item[0]))))}
        pages = [dict(page) for page in sorted(state.pages, key=lambda page: (page["_issue_order"], page["page_number"]))]
        for page in pages:
            page.pop("_issue_order", None)
        issues = [{"issue": label, "issue_url": redact_url(url), "page_count": len(state.page_numbers.get(url, ()))}
                  for url, label in sorted(state.issues.items(), key=lambda item: order[item[0]])]
        problems = [dict(problem) for problem in state.problems]
        if reason:
            problems.append({"code": reason, "message": _MESSAGES[reason], "url": redact_url(state.source), "severity": "pause", "count": 1})
        return {"source_url": redact_url(state.source), "scope": state.scope, "pages": pages, "issues": issues,
                "problems": problems, "complete": status == "complete", "cursor": None if finished else state.token,
                "status": status, "documents": state.documents}

    async def _notify(self, progress, manifest: dict) -> None:
        if progress is None:
            return
        try:
            result = progress(manifest)
            if inspect.isawaitable(result):
                await result
        except Exception:
            raise SourceError("progress_failed") from None

    def _reference(self, url: str, profile: _Profile) -> str:
        key = self._reference_urls.get((profile.source.name, url))
        if key in self._references:
            self._references[key].expires = time.monotonic() + self.limits.session_ttl
            return key
        if len(self._references) >= self.limits.max_references:
            raise SourceError("state_limit")
        key = "image_" + secrets.token_hex(16)
        self._references[key] = _Reference(url, profile, time.monotonic() + self.limits.session_ttl)
        self._reference_urls[(profile.source.name, url)] = key
        return key

    def _issue_identity(self, url: str, profile: SourceProfile) -> tuple:
        try:
            parameters = parse_qs(urlsplit(url).query, keep_blank_values=True, max_num_fields=256)
        except ValueError:
            raise SourceError("invalid_url") from None
        return (redact_url(url), tuple((name, tuple(parameters.get(name, ())))
                                      for name in sorted(set(profile.identity_query_params))))

    def _register_issue(self, state: _State, url: str, label: str) -> None:
        identity = self._issue_identity(url, state.profile.source)
        existing = state.issue_identities.get(identity)
        if existing is not None and existing != url:
            if any(values for _, values in identity[1]):
                return
            raise SourceError("identity_ambiguous")
        if url not in state.issues and len(state.issues) >= self.limits.max_issues:
            raise SourceError("state_limit")
        state.issue_identities[identity] = url
        state.issues.setdefault(url, label)

    def _image_identity(self, url: str, profile: SourceProfile) -> tuple:
        if profile.image_identity_query_params:
            try:
                parameters = parse_qs(urlsplit(url).query, keep_blank_values=True, max_num_fields=256)
            except ValueError:
                raise SourceError("invalid_url") from None
            semantic = tuple((name, tuple(parameters.get(name, ()))) for name in sorted(set(profile.image_identity_query_params)))
            if any(values for _, values in semantic):
                return ("semantic", redact_url(url), semantic)
        return ("exact-url-sha256", hashlib.sha256(url.encode("utf-8")).hexdigest())

    def _seen_page(self, state: _State, task: _Task) -> bool:
        identity = self._image_identity(task.url, state.profile.source)
        numbers = state.seen_images.get((task.issue_url, identity), set())
        return bool(numbers) if task.number is None else task.number in numbers

    @staticmethod
    def _record_document(state: _State, task: _Task, final_url: str) -> None:
        state.seen_documents.update((task.url, final_url))
        if task.kind in {"reader", "seed"}:
            state.seen_reader_documents.setdefault(task.issue_url or task.url, set()).update((task.url, final_url))

    def _add_page(self, state: _State, task: _Task) -> bool:
        if self._seen_page(state, task):
            return False
        if len(state.pages) >= self.limits.max_pages:
            raise SourceError("state_limit")
        reference = self._reference(task.url, state.profile)
        numbers = state.page_numbers.setdefault(task.issue_url, set())
        number = task.number or (max(numbers, default=0) + 1)
        extra = {}
        if number in numbers:
            extra["source_page_number"] = number
            number = max(numbers, default=0) + 1
            self._problem(state, "page_number_conflict", task.issue_url, "warning")
        numbers.add(number)
        image_identity = self._image_identity(task.url, state.profile.source)
        state.seen_images.setdefault((task.issue_url, image_identity), set()).add(task.number or number)
        order = sorted(state.issues, key=lambda url: (_natural_key(state.issues[url]), _natural_key(url)))
        occurrence = ("source-page", task.number) if task.number is not None else ("implicit-page",)
        identity = (self._issue_identity(task.issue_url, state.profile.source), image_identity, occurrence)
        page_key = hashlib.sha256(json.dumps(identity, ensure_ascii=True, separators=(",", ":")).encode("utf-8")).hexdigest()
        state.pages.append({"issue": task.label, "issue_url": redact_url(task.issue_url), "page_number": number,
                            "image_url": redact_url(task.url), "fetch_id": reference, "page_key": page_key,
                            "_issue_order": order.index(task.issue_url), **extra})
        return True

    def _select(self, soup, selector: str | None) -> list:
        if not selector:
            return []
        try:
            return soup.select(selector)
        except Exception:
            raise SourceError("invalid_config") from None

    def _linked(self, raw: str, base: str, state: _State, role: str) -> str | None:
        try:
            return self._scoped(urljoin(base, raw), state.profile, role)
        except (SourceError, ValueError) as error:
            self._problem(state, error.code if isinstance(error, SourceError) else "invalid_url", base)
            return None

    def _enqueue(self, state: _State, tasks: list[_Task], front: bool = False) -> None:
        if len(state.todo) + len(tasks) > self.limits.max_pending:
            raise SourceError("state_limit")
        if front:
            state.todo.extendleft(reversed(tasks))
        else:
            state.todo.extend(tasks)

    def _number_from_url(self, url: str, profile: SourceProfile) -> int | None:
        if profile.page_number_param:
            try:
                values = parse_qs(urlsplit(url).query, max_num_fields=256).get(profile.page_number_param, [])
            except ValueError:
                raise SourceError("invalid_url") from None
            if len(values) == 1:
                return _positive_number(values[0])
        return None

    def _parse_html(self, approved: _Profile, resource: _Resource) -> _ParsedHTML:
        if not approved.accepts(resource.url, "page"):
            raise SourceError("out_of_scope")
        try:
            bs4 = importlib.import_module("bs4")
        except ImportError:
            raise SourceError("html_dependency_missing") from None
        soup = bs4.BeautifulSoup(resource.body, "html.parser")
        for meta in soup.find_all("meta"):
            if str(meta.get("name", "")).lower() in {"robots", "techfarmer"} and self._restricted(str(meta.get("content", ""))):
                raise SourceError("site_restricted")
        profile, base = approved.source, resource.url
        base_element = soup.find("base", href=True)
        if base_element:
            try:
                base = self._scoped(urljoin(base, str(base_element["href"])), approved, "either")
            except ValueError:
                raise SourceError("invalid_url") from None
        selected_images = self._select(soup, profile.reader_selector)
        excluded = {id(node) for node in self._select(soup, profile.exclude_selector)}
        images, pictures = [], set()
        for node in selected_images:
            picture = node if node.name == "picture" else node.find_parent("picture")
            primary = (picture.find("img") if picture is not None else node) or node
            if any(id(element) in excluded for element in (node, primary)) or any(id(parent) in excluded for parent in node.parents):
                continue
            identity = id(picture if picture is not None else node)
            if identity in pictures:
                continue
            pictures.add(identity)
            number = _positive_number(primary.get(profile.page_number_attribute)) if profile.page_number_attribute else None
            images.append((_image_attribute(node), number))

        def links(selector):
            result = []
            for node in self._select(soup, selector):
                href = node.get("href")
                if not isinstance(href, str) or not href.strip():
                    continue
                label = _clean_label(node.get("data-issue") or node.get("data-chapter") or node.get_text(" ", strip=True), "")
                number = _positive_number(node.get(profile.page_number_attribute)) if profile.page_number_attribute else None
                result.append(_Link(href.strip(), label, number))
            return tuple(result)
        title = self._select(soup, profile.issue_title_selector)
        return _ParsedHTML(base, bool(images), tuple(images), links(profile.chapter_selector),
                           links(profile.series_next_selector), links(profile.reader_next_selector),
                           _clean_label(title[0].get_text(" ", strip=True), "") if title else "")

    def _apply_html(self, state: _State, task: _Task, resource: _Resource, parsed: _ParsedHTML) -> None:
        profile, base, mode = state.profile.source, parsed.base, task.kind
        if mode == "seed":
            mode = "series" if state.requested_scope == "series" or (state.requested_scope == "auto" and not parsed.had_images and parsed.chapters) else "reader"
            state.scope = "series" if mode == "series" else "page" if state.requested_scope == "page" else "issue"
            state.phase = "catalog" if mode == "series" else "readers"
        if mode == "series":
            if not parsed.chapters and not parsed.series_next:
                raise SourceError("unsupported_layout")
            for link in parsed.chapters:
                linked = self._linked(link.href, base, state, "page")
                if linked is None or linked in state.seen_documents:
                    continue
                if linked not in state.issues:
                    self._register_issue(state, linked, link.label or _clean_label(urlsplit(linked).path.rsplit("/", 1)[-1]))
            self._enqueue(state, self._pagination(parsed.series_next, base, state, "series"))
            return
        issue_url = task.issue_url or task.url
        label = task.label or parsed.title or ("Page" if state.scope == "page" else "Issue")
        self._register_issue(state, issue_url, label)
        images = []
        for raw, number in parsed.images:
            if not raw:
                self._problem(state, "unsupported_image", resource.url)
                continue
            linked = self._linked(raw, base, state, "image")
            if linked:
                images.append(_Task("image", linked, issue_url, label, number))
        if len(images) == 1 and images[0].number is None:
            images[0].number = task.number or self._number_from_url(resource.url, profile)
        next_links = parsed.reader_next if state.scope != "page" else ()
        next_tasks = self._pagination(next_links, base, state, "reader", issue_url, label)
        if not images and not next_tasks:
            raise SourceError("unsupported_layout")
        self._enqueue(state, images + next_tasks, front=True)

    def _pagination(self, links: tuple[_Link, ...], base: str, state: _State, kind: str, issue_url: str = "", label: str = "") -> list[_Task]:
        tasks = []
        pending = {task.url for task in state.todo if task.kind == kind and (kind != "reader" or task.issue_url == issue_url)}
        seen = state.seen_reader_documents.get(issue_url, set()) if kind == "reader" else state.seen_documents
        for link in links:
            if kind == "reader" and state.profile.source.reader_next_within_issue:
                try:
                    candidate = _url(urljoin(base, link.href), self.config.allow_http)
                    if not _within_issue_page(candidate, issue_url, state.profile.source):
                        self._problem(state, "pagination_outside_issue", base, "warning")
                        continue
                except (SourceError, ValueError) as error:
                    self._problem(state, error.code if isinstance(error, SourceError) else "invalid_url", base)
                    continue
            linked = self._linked(link.href, base, state, "page")
            if linked is None:
                continue
            if linked in seen:
                self._problem(state, "pagination_loop", base, "warning")
            elif linked not in pending:
                tasks.append(_Task(kind, linked, issue_url, label, link.number))
                pending.add(linked)
        return tasks

    async def discover(self, url: str, scope: str = "auto", progress: Callable | None = None,
                       cancel=None, cursor: str | None = None) -> dict:
        async with self._lock:
            self._prune()
            source = _url(url, self.config.allow_http)
            scope = "issue" if scope == "chapter" else scope
            if scope not in {"auto", "series", "issue", "page"}:
                raise SourceError("invalid_config")
            if cursor is not None:
                if not isinstance(cursor, str) or cursor not in self._sessions:
                    raise SourceError("invalid_cursor")
                state = self._sessions[cursor]
                if state.source != source or state.requested_scope != scope:
                    raise SourceError("invalid_cursor")
            else:
                if len(self._sessions) >= self.limits.max_sessions:
                    raise SourceError("session_limit")
                state = _State(source, scope, self._profile(source, "either"), "cursor_" + secrets.token_hex(16), time.monotonic() + self.limits.session_ttl)
                state.todo.append(_Task("seed", source))
                self._sessions[state.token] = state
            state.expires = time.monotonic() + self.limits.session_ttl
            documents, pages, reason, status = 0, 0, None, "discovering"
            try:
                while True:
                    _check_cancel(cancel)
                    if not state.todo:
                        if state.phase == "catalog":
                            state.phase = "readers"
                            tasks = [_Task("reader", issue_url, issue_url, label) for issue_url, label in sorted(state.issues.items(), key=lambda item: (_natural_key(item[1]), _natural_key(item[0])))]
                            self._enqueue(state, tasks)
                            if tasks:
                                continue
                        status = "complete" if state.pages and not state.failed else "partial" if state.pages else "unsupported"
                        if not state.pages and not state.problems:
                            self._problem(state, "unsupported_layout", source)
                        break
                    task = state.todo[0]
                    if task.kind == "image":
                        if self._seen_page(state, task):
                            state.todo.popleft()
                            continue
                        if pages >= self.limits.max_pages_per_batch:
                            status, reason = "paused", "page_budget"
                            break
                        if self._add_page(state, task):
                            pages += 1
                        state.todo.popleft()
                        await self._notify(progress, self._manifest(state, "discovering"))
                        continue
                    seen = state.seen_reader_documents.get(task.issue_url, set()) if task.kind == "reader" else state.seen_documents
                    if task.url in seen:
                        state.todo.popleft()
                        continue
                    if state.documents >= self.limits.max_documents:
                        raise SourceError("state_limit")
                    if documents >= self.limits.max_documents_per_batch:
                        status, reason = "paused", "document_budget"
                        break
                    try:
                        resource = await self._fetch(task.url, state.profile, "either" if task.kind == "seed" else "page", "document", cancel)
                        parsed = None
                        if resource.content_type in _HTML_TYPES:
                            parsed = await asyncio.to_thread(self._parse_html, state.profile, resource)
                        _check_cancel(cancel)
                        state.todo.popleft()
                        self._record_document(state, task, resource.url)
                        documents += 1
                        state.documents += 1
                        if resource.content_type in _IMAGE_TYPES:
                            _validate_image(resource)
                            if task.kind != "seed" or state.requested_scope == "series":
                                raise SourceError("unsupported_layout")
                            self._scoped(resource.url, state.profile, "image")
                            state.scope, state.phase = "page", "readers"
                            self._register_issue(state, source, "Page")
                            self._enqueue(state, [_Task("image", resource.url, source, "Page", self._number_from_url(source, state.profile.source))], front=True)
                        else:
                            self._apply_html(state, task, resource, parsed)
                    except SourceError as error:
                        if error.code == "state_limit":
                            raise
                        if state.todo and state.todo[0] is task:
                            state.todo.popleft()
                            self._record_document(state, task, task.url)
                            documents += 1
                            state.documents += 1
                        self._problem(state, error.code, task.url)
                        if error.code in {"dependency_missing", "html_dependency_missing", "invalid_config"}:
                            status = "partial" if state.pages else "unsupported"
                            break
                manifest = self._manifest(state, status, reason)
                await self._notify(progress, manifest)
            except _StopRequested:
                manifest = self._manifest(state, "cancelled", "cancelled")
                try:
                    await self._notify(progress, manifest)
                except SourceError:
                    pass
            except SourceError as error:
                if error.code == "progress_failed":
                    manifest = self._manifest(state, "paused", "progress_failed")
                elif error.code == "state_limit":
                    self._problem(state, error.code, source)
                    manifest = self._manifest(state, "limited")
                else:
                    self._problem(state, error.code, source)
                    manifest = self._manifest(state, "partial" if state.pages else "unsupported")
            if manifest["cursor"] is None:
                self._sessions.pop(state.token, None)
            self._touch_private_state()
            return manifest

    async def fetch_image(self, url: str, *, cancel=None) -> bytes:
        async with self._lock:
            self._prune()
            self._touch_private_state()
            if isinstance(url, str) and url.startswith("image_"):
                reference = self._references.get(url)
                if reference is None:
                    raise SourceError("reference_expired")
                exact, profile = reference.url, reference.profile
                reference.expires = time.monotonic() + self.limits.session_ttl
            else:
                exact = _url(url, self.config.allow_http)
                references = [reference for reference in self._references.values() if redact_url(reference.url) == exact]
                if len({reference.url for reference in references}) > 1:
                    raise SourceError("ambiguous_reference")
                if references:
                    exact, profile = references[0].url, references[0].profile
                else:
                    profile = self._profile(exact, "image")
            try:
                resource = await self._fetch(exact, profile, "image", "image", cancel)
                _validate_image(resource)
                return resource.body
            except _StopRequested:
                raise SourceError("cancelled") from None
