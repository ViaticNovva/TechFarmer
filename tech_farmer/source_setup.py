"""Turn an attested source link into a bounded SourceClient configuration.

``await prepare_source(url, rights_confirmed=True)`` inspects public static HTML
(or one direct raster), not arbitrary links or external artwork. The transport,
DNS pinning, robots, redirects and response budgets are those of ``sources``.
Recognized reader/chapter-list containers are required; JavaScript, authentication
and access challenges are neither executed nor bypassed. No selector editing is
needed for the supported layouts. Query-only chapter routing needs an explicit
advanced profile; built-in pagination declares only positive ``page``/``paged``.

A frozen SourceProposal contains no image URLs or signed request parameters.
Its ``config`` and ``to_dict()`` return independent JSON-compatible copies.
``needs_origin_approval`` configurations are deliberately unauthorized. Save the
proposal privately and call ``approve_source(saved, proposal_id=displayed_id,
approved_origins=saved['required_origins'])`` only after the owner approves that
exact list. The hash binds seed, scope and configuration; approvals cannot add
origins, change selectors or enlarge paths. Reinspection must precede collection;
unknown/new origins remain outside the returned SourceClient configuration.

Inspection is bounded, not a successful first-N sample. Larger/ambiguous sources
should use a specific chapter link or an existing explicit profile. Profiles
provided by the caller are not mutated: only the selected profile is copied for
the job, with image origins narrowed to those actually discovered. No test-only
private-network option, cookies, referrers, credentials or bypass transport exists.
"""
from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Iterable, Mapping
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass, field, replace
import hashlib
import importlib
import json
import re
from urllib.parse import parse_qsl, urljoin, urlsplit

from .images import decode
from .safety import UserError
from .sources import (
    AiohttpTransport, SourceClient, SourceConfig, SourceError, SourceLimits,
    SourceProfile, SourceTransport, _configuration, _decoded_path, _image_attribute, _issue_path_root, _origin,
    _positive_number, _response_headers, _url, _validate_image,
)


_READER_CONTAINERS = (
    "[data-reader]", "#readerarea", "#reader", "#comic", ".comic-reader",
    ".reading-content", ".reader-content", ".chapter-content", ".reader-area",
    ".manga-reader", ".chapter-reader", ".reader",
)
_CHAPTER_CONTAINERS = (
    "[data-chapter-list]", ".chapter-list", ".chapters", ".listing-chapters_wrap",
    ".eplister", "#chapterlist", "#chapters", ".chapters-list",
)
_CONTEXT_EXCLUDE = (
    ".ad,.ads,.advertisement,[data-ad],.cover,[data-role=cover],"
    ".recommendations,.recommended,.related,.related-posts,[data-recommendations],"
    "aside,header,footer,[hidden],[aria-hidden=true],template,noscript"
)
_IMAGE_EXCLUDE = _CONTEXT_EXCLUDE + ",.thumbnail,.icon,.logo,.avatar,nav"
_LINK_GUARD = f":not(:is({_CONTEXT_EXCLUDE}),:is({_CONTEXT_EXCLUDE}) *)"
_READER_NEXT = (
    "a[data-page-next][href]", "a[rel~=next][href]", "link[rel~=next][href]",
    ".pagination a.next[href]", "a.page-next[href]",
)
_SERIES_NEXT = (
    "a[data-series-next][href]", "a[rel~=next][href]", "link[rel~=next][href]",
    ".pagination a.next[href]", "a.next.page-numbers[href]", "a.series-next[href]",
)
_HTML_TYPES = frozenset({"text/html", "application/xhtml+xml"})
_RASTER_TYPES = frozenset({"image/png", "image/jpeg", "image/webp", "image/gif"})
_SETUP_MESSAGES = {
    "invalid_scope": "Choose auto, page, chapter/issue or series scope.",
    "invalid_approval": "This approval does not match the saved source proposal. Inspect the source again and approve exactly the displayed origins.",
    "ambiguous_layout": "Several reader or chapter-list areas match. Choose page/chapter/series scope, or supply the specific chapter link instead of the index.",
    "unsupported_layout": "No supported static reader or chapter list was found. Use a direct PNG/JPEG/WebP or a public static chapter link; JavaScript-only/protected readers need an authorized supported source.",
    "javascript_required": "This reader requires JavaScript or a site challenge. TechFarmer does not run scripts or bypass access controls; use an authorized public static chapter or direct image link.",
    "source_scope": "The link does not identify a bounded series/chapter path. Use that series or chapter's own URL, not the website-wide index.",
    "pagination_scope": "Pagination leaves the selected chapter/series or uses ambiguous routing. Use a specific chapter/page URL or an explicit advanced profile.",
    "query_routing_required": "Query-only chapter routing needs an explicit semantic profile. Use a stable path-based chapter link; signed URLs and undeclared query fields are not saved.",
    "inspection_limit": "The bounded inspection could not cover the whole source. Use a chapter link or an explicit profile with suitable limits; no partial source was silently approved.",
    "unsupported_image": "The direct image is corrupt, animated, unsupported or exceeds the normal image/pixel limits. Use a still PNG, JPEG or WebP within those limits.",
}


def _json(value) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":"), allow_nan=False)


@dataclass(frozen=True)
class SourceProposal:
    status: str
    source_type: str = "unknown"
    page_count: int = 0
    chapter_count: int = 0
    required_origins: tuple[str, ...] = ()
    proposal_id: str = ""
    code: str = ""
    message: str = ""
    seed: str = ""
    scope: str = "auto"
    choices: tuple[str, ...] = ()
    _config_json: str = field(default="", repr=False)

    @property
    def config(self) -> dict | None:
        return json.loads(self._config_json) if self._config_json else None

    @property
    def origins(self) -> tuple[str, ...]:
        return self.required_origins

    def to_dict(self) -> dict:
        return {
            "schema_version": 1, "status": self.status, "source_type": self.source_type,
            "page_count": self.page_count, "chapter_count": self.chapter_count,
            "required_origins": list(self.required_origins), "proposal_id": self.proposal_id,
            "code": self.code, "message": self.message, "seed": self.seed,
            "scope": self.scope, "choices": list(self.choices), "config": self.config,
        }


class SourceApprovalError(ValueError):
    code = "invalid_approval"

    def __init__(self):
        super().__init__(_SETUP_MESSAGES[self.code])


class _StopInspection(Exception):
    def __init__(self, code: str, *, ambiguous=False):
        self.code, self.ambiguous = code, ambiguous
        super().__init__(_SETUP_MESSAGES[code])


def _proposal_hash(seed: str, scope: str, source_type: str, config: dict, origins: tuple[str, ...]) -> str:
    bound = dict(config, authorized=False)
    return hashlib.sha256(_json({
        "version": 1, "seed": seed, "scope": scope, "source_type": source_type,
        "config": bound, "required_origins": origins,
    }).encode("utf-8")).hexdigest()


def _ready(seed, scope, source_type, config, origins, page_count, chapter_count) -> SourceProposal:
    origins = tuple(sorted(set(origins)))
    config = json.loads(_json(config))
    _configuration(dict(config, authorized=True))
    config["authorized"] = not origins
    identifier = _proposal_hash(seed, scope, source_type, config, origins)
    if source_type == "image":
        detail = "One direct raster image passed the normal byte, format and pixel checks."
    else:
        detail = f"Static {source_type}: {page_count} image reference(s), {chapter_count} chapter(s). Image bodies have not been fetched."
    if origins:
        detail += " Approve the exact external image origins before collection."
    return SourceProposal(
        "needs_origin_approval" if origins else "ready", source_type, page_count,
        chapter_count, origins, identifier, "origin_approval_required" if origins else "",
        detail, seed, scope, (), _json(config),
    )


def approve_source(proposal: SourceProposal | Mapping, *, proposal_id: str,
                   approved_origins: Iterable[str]) -> SourceProposal:
    """Enable only an intact saved proposal and its exact owner-approved origins.

    Raises SourceApprovalError with a fixed, nonsecret message on a stale,
    modified, partial or enlarged approval. No requests are made here.
    """
    try:
        saved = proposal.to_dict() if isinstance(proposal, SourceProposal) else dict(proposal)
        required = saved["required_origins"]
        if (saved["schema_version"] != 1 or saved["status"] not in {"ready", "needs_origin_approval"}
                or not isinstance(required, list) or not all(isinstance(item, str) for item in required)
                or len(required) != len(set(required)) or isinstance(approved_origins, (str, bytes))):
            raise ValueError
        approved = tuple(approved_origins)
        if (not all(isinstance(item, str) for item in approved)
                or len(approved) != len(set(approved)) or set(approved) != set(required)):
            raise ValueError
        config = saved["config"]
        if not isinstance(config, dict):
            raise ValueError
        normalized, profiles = _configuration(dict(config, authorized=True))
        seed = _url(saved["seed"], normalized.allow_http)
        seed_origin = _origin(seed)
        image_origins = set(origin for profile in profiles for origin in profile.image_origins)
        if set(required) != image_origins - {seed_origin}:
            raise ValueError
        for origin in required:
            if _origin(_url(origin, normalized.allow_http)) != origin or urlsplit(origin).path:
                raise ValueError
        expected = _proposal_hash(seed, saved["scope"], saved["source_type"], config, tuple(sorted(required)))
        if not isinstance(proposal_id, str) or saved["proposal_id"] != expected or proposal_id != expected:
            raise ValueError
        if saved["scope"] not in {"auto", "series", "issue", "page"}:
            raise ValueError
        if saved["source_type"] not in {"image", "reader", "series", "configured"}:
            raise ValueError
        if any(type(saved[name]) is not int or saved[name] < 0 for name in ("page_count", "chapter_count")):
            raise ValueError
        # Recheck persistence offline without retaining a client or signed values.
        client = SourceClient(dict(config, authorized=True))
        if client.persistent_seed(seed) != seed:
            raise ValueError
        enabled = dict(config, authorized=True)
        return SourceProposal(
            "ready", saved["source_type"], saved["page_count"], saved["chapter_count"],
            tuple(sorted(required)), expected, "", "Exact source scope approved; ready for collection.",
            seed, saved["scope"], (), _json(enabled),
        )
    except (KeyError, TypeError, ValueError, SourceError, OverflowError):
        raise SourceApprovalError() from None


class _HTMLOnlyExternalTransport:
    """Explicitly configured external HTML pages cannot stream artwork at setup."""

    def __init__(self, inner: SourceTransport, seed_origin: str):
        self.inner, self.seed_origin = inner, seed_origin

    async def resolve(self, host, port):
        return await self.inner.resolve(host, port)

    @asynccontextmanager
    async def open(self, url, **kwargs):
        async with self.inner.open(url, **kwargs) as response:
            if _origin(url) != self.seed_origin:
                content_type = _response_headers(response.headers).get("content-type", "").split(";", 1)[0].strip().lower()
                if response.status == 200 and content_type in _RASTER_TYPES:
                    raise SourceError("out_of_scope")
            yield response


class _InspectionClient(SourceClient):
    def __init__(self, config, transport, seed_origin):
        super().__init__(config, _HTMLOnlyExternalTransport(
            transport if transport is not None else AiohttpTransport(), seed_origin))
        self.seed_origin = seed_origin

    async def _fetch(self, url, profile, role, purpose, cancel=None):
        # A direct seed cannot redirect to an as-yet-unapproved image origin.
        if role == "either" and purpose == "document":
            narrowed = replace(profile.source, page_origins=(self.seed_origin,), image_origins=(self.seed_origin,))
            _, profiles = _configuration(replace(self.config, profiles=(narrowed,)))
            profile = profiles[0]
        resource = await super()._fetch(url, profile, role, purpose, cancel)
        if purpose == "document" and resource.content_type in _RASTER_TYPES:
            _validate_image(resource)
            try:
                async with asyncio.timeout(self.limits.request_timeout):
                    image, _ = await asyncio.to_thread(decode, resource.body, mime=resource.content_type)
                    image.close()
            except UserError:
                raise _StopInspection("unsupported_image") from None
            except TimeoutError:
                raise SourceError("timeout") from None
        return resource


def _selector(parts, *, guarded=False):
    if not parts:
        return None
    result = ":is(" + ",".join(dict.fromkeys(parts)) + ")" + (_LINK_GUARD if guarded else "")
    if len(result) > 1024:
        raise _StopInspection("inspection_limit")
    return result


def _root(path):
    return _issue_path_root(path)


def _inside(path, root):
    path = _decoded_path(path)
    return path.rstrip("/") == root or path.startswith(root + "/")


def _page_parameter(url):
    try:
        parsed = urlsplit(url)
        if parsed.fragment:
            raise _StopInspection("query_routing_required", ambiguous=True)
        pairs = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True, max_num_fields=2)
    except ValueError:
        raise _StopInspection("query_routing_required", ambiguous=True) from None
    if not pairs:
        return None
    if len(pairs) != 1 or pairs[0][0] not in {"page", "paged"} or _positive_number(pairs[0][1]) is None:
        raise _StopInspection("query_routing_required", ambiguous=True)
    return pairs[0][0]


def _soup(resource):
    try:
        library = importlib.import_module("bs4")
    except ImportError:
        raise SourceError("html_dependency_missing") from None
    soup = library.BeautifulSoup(resource.body, "html.parser")
    for meta in soup.find_all("meta"):
        if str(meta.get("name", "")).lower() in {"robots", "techfarmer"} and SourceClient._restricted(str(meta.get("content", ""))):
            raise SourceError("site_restricted")
    if soup.select("input[type=password],.g-recaptcha,.h-captcha,#challenge-running,#cf-challenge-running"):
        raise _StopInspection("javascript_required")
    return soup


def _excluded(node, excluded):
    return id(node) in excluded or any(id(parent) in excluded for parent in node.parents)


def _containers(soup, selectors, kind):
    excluded = {id(node) for node in soup.select(_IMAGE_EXCLUDE if kind == "reader" else _CONTEXT_EXCLUDE)}
    found, seen = [], set()
    for selector in selectors:
        for node in soup.select(selector):
            if id(node) in seen or _excluded(node, excluded):
                continue
            items = node.select("img,picture" if kind == "reader" else "a[href]")
            items = [item for item in items if not _excluded(item, excluded)]
            if kind == "reader":
                items = [item for item in items if _image_attribute(item)]
            if items:
                seen.add(id(node))
                found.append((node, selector, items))
    # One enclosing reader, not each of its nested compatibility class names.
    return [entry for entry in found if not any(id(parent) in seen for parent in entry[0].parents)]


def _layout(resource, expected):
    soup = _soup(resource)
    readers = _containers(soup, _READER_CONTAINERS, "reader")
    chapters = _containers(soup, _CHAPTER_CONTAINERS, "series")
    if expected == "auto" and readers and chapters:
        raise _StopInspection("ambiguous_layout", ambiguous=True)
    kind = expected if expected in {"reader", "series"} else "reader" if readers else "series"
    matches = readers if kind == "reader" else chapters
    if len(matches) > 1:
        raise _StopInspection("ambiguous_layout", ambiguous=True)
    if not matches:
        text = soup.get_text(" ", strip=True).lower()
        if re.search(r"enable javascript|javascript (?:is )?required|checking your browser|sign in to read|subscribe to (?:read|continue)", text):
            raise _StopInspection("javascript_required")
        raise _StopInspection("unsupported_layout")
    _, container, items = matches[0]
    number_attribute = "data-page-number"
    if kind == "reader" and any(_positive_number(item.get("data-page")) for item in items):
        if any(_positive_number(item.get("data-page-number")) for item in items):
            raise _StopInspection("ambiguous_layout", ambiguous=True)
        number_attribute = "data-page"
    return kind, container, number_attribute


class _AutomaticInspection:
    def __init__(self, client, seed, scope, profile):
        self.client, self.seed, self.scope, self.profile = client, seed, scope, profile
        self.root = _root(urlsplit(seed).path)
        self.origin = _origin(seed)
        self.readers, self.chapters = set(), set()
        self.reader_next, self.series_next = set(), set()
        self.image_urls, self.issues, self.seen = set(), set(), set()
        self.issue_routes = {}
        self.image_origins, self.image_paths = set(), set()
        self.number_attribute = None
        self.reader_parameter = self.series_parameter = None
        self.parameters = set()
        self.documents = 0

    def _parameter(self, parameter, kind):
        if parameter:
            self.parameters.add(parameter)
            field = "reader_parameter" if kind == "reader" else "series_parameter"
            previous = getattr(self, field)
            if previous and previous != parameter:
                raise _StopInspection("query_routing_required", ambiguous=True)
            setattr(self, field, parameter)

    def _document_url(self, raw, base, kind):
        try:
            joined = urljoin(base, raw)
            parameter = _page_parameter(joined)
            value = self.client._scoped(joined, self.client._profiles[0], "page")
        except ValueError:
            raise SourceError("invalid_url") from None
        self._parameter(parameter, kind)
        return value

    def _images(self, parsed, issue):
        for raw, number in parsed.images:
            if not raw:
                raise SourceError("unsupported_image")
            try:
                value = _url(urljoin(parsed.base, raw), self.client.config.allow_http)
            except ValueError:
                raise SourceError("invalid_url") from None
            identity = (issue, value, number)
            self.image_urls.add(identity)
            self.image_origins.add(_origin(value))
            path = _decoded_path(urlsplit(value).path)
            parent = path.rsplit("/", 1)[0]
            self.image_paths.add(parent if parent and parent != "/" else path)
            if (len(self.image_urls) > min(self.client.limits.max_pages, self.client.limits.max_references)
                    or len(self.image_origins) > 32 or len(self.image_paths) > 32):
                raise _StopInspection("inspection_limit")

    async def run(self):
        expected = "series" if self.scope == "series" else "reader" if self.scope in {"issue", "page"} else "auto"
        pending = deque([(self.seed, expected, self.seed)])
        source_type = "unknown"
        while pending:
            url, expected, issue = pending.popleft()
            if url in self.seen:
                continue
            if self.documents >= min(self.client.limits.max_documents, self.client.limits.max_documents_per_batch):
                raise _StopInspection("inspection_limit")
            resource = await self.client._fetch(url, self.client._profiles[0], "either" if not self.documents else "page", "document")
            self.seen.update((url, resource.url))
            self.documents += 1
            if resource.content_type in _RASTER_TYPES:
                if self.documents != 1 or self.scope == "series":
                    raise _StopInspection("unsupported_layout")
                config = asdict(replace(self.client.config, profiles=(replace(
                    self.profile, reader_selector=None, chapter_selector=None,
                    series_next_selector=None, reader_next_selector=None,
                    image_paths=(urlsplit(resource.url).path,),
                ),)))
                return _ready(self.seed, self.scope, "image", config, (), 1, 0)
            kind, container, number_attribute = await asyncio.to_thread(_layout, resource, expected)
            self._parameter(_page_parameter(resource.url), kind)
            if self.documents == 1:
                source_type = kind
            if kind == "reader":
                self.readers.add(container + " :is(img,picture)")
                if self.number_attribute and self.number_attribute != number_attribute:
                    raise _StopInspection("ambiguous_layout", ambiguous=True)
                self.number_attribute = number_attribute
            else:
                self.chapters.add(container + " a[href]")
            current = replace(
                self.profile,
                reader_selector=container + " :is(img,picture)" if kind == "reader" else None,
                chapter_selector=_selector([container + " a[href]"], guarded=True) if kind == "series" else None,
                reader_next_selector=_selector(_READER_NEXT, guarded=True) if kind == "reader" else None,
                series_next_selector=_selector(_SERIES_NEXT, guarded=True) if kind == "series" else None,
                page_number_attribute=number_attribute,
            )
            _, approved = _configuration(replace(self.client.config, profiles=(current,)))
            parsed = await asyncio.to_thread(self.client._parse_html, approved[0], resource)
            if kind == "series":
                for link in parsed.chapters:
                    chapter = self._document_url(link.href, parsed.base, "reader")
                    route = _root(urlsplit(chapter).path)
                    if route in self.issue_routes and self.issue_routes[route] != chapter:
                        raise _StopInspection("query_routing_required", ambiguous=True)
                    self.issue_routes[route] = chapter
                    if not _inside(route, self.root) or route == self.root:
                        raise _StopInspection("pagination_scope", ambiguous=True)
                    self.issues.add(chapter)
                    if len(self.issues) > self.client.limits.max_issues:
                        raise _StopInspection("inspection_limit")
                    pending.append((chapter, "reader", chapter))
                next_links = parsed.series_next
            else:
                self.issues.add(issue)
                self._images(parsed, issue)
                next_links = parsed.reader_next if self.scope != "page" else ()
            following = set()
            for link in next_links:
                # Rel-next often means the next chapter. It must never become
                # another page of this issue (especially during a series crawl).
                candidate = _url(urljoin(parsed.base, link.href), self.client.config.allow_http)
                boundary = self.root if kind == "series" else _root(urlsplit(issue).path)
                if _origin(candidate) != self.origin or not _inside(urlsplit(candidate).path, boundary):
                    continue
                candidate = self._document_url(link.href, parsed.base, kind)
                following.add(candidate)
                (self.series_next if kind == "series" else self.reader_next).add(link.href)
            if len(following) > 1:
                raise _StopInspection("ambiguous_layout", ambiguous=True)
            pending.extend((value, kind, issue) for value in sorted(following))
            if len(pending) > self.client.limits.max_pending:
                raise _StopInspection("inspection_limit")
        if not self.image_urls:
            raise _StopInspection("unsupported_layout")
        for origin in sorted(self.image_origins - {self.origin}):
            try:
                async with asyncio.timeout(self.client.limits.request_timeout):
                    await self.client._addresses(origin + "/")
            except TimeoutError:
                raise SourceError("timeout") from None
        final = replace(
            self.profile, image_origins=tuple(sorted(self.image_origins)),
            image_paths=tuple(sorted(self.image_paths)),
            reader_selector=_selector(sorted(self.readers)),
            chapter_selector=_selector(sorted(self.chapters), guarded=True),
            reader_next_selector=_selector(_READER_NEXT, guarded=True) if self.reader_next else None,
            series_next_selector=_selector(_SERIES_NEXT, guarded=True) if self.series_next else None,
            page_number_attribute=self.number_attribute or "data-page-number",
            page_number_param=self.reader_parameter or self.series_parameter or self.profile.page_number_param,
            identity_query_params=tuple(sorted(self.parameters - {self.reader_parameter or self.series_parameter or self.profile.page_number_param})),
        )
        config = asdict(replace(self.client.config, profiles=(final,)))
        return _ready(self.seed, self.scope, source_type, config,
                      self.image_origins - {self.origin}, len(self.image_urls), len(self.issues))


async def _configured(url, config, transport, scope):
    normalized, _ = _configuration(config)
    seed_origin = _origin(_url(url, normalized.allow_http))
    inspection_limits = replace(
        normalized.limits,
        max_documents=min(normalized.limits.max_documents, normalized.limits.max_documents_per_batch),
        max_pages_per_batch=normalized.limits.max_pages,
    )
    async with _InspectionClient(replace(normalized, limits=inspection_limits), transport, seed_origin) as client:
        seed = client.persistent_seed(url)
        selected = client._profile(seed, "either")
        manifest = await client.discover(seed, scope=scope)
        if manifest["status"] in {"paused", "limited"}:
            raise _StopInspection("inspection_limit")
        if not manifest["complete"]:
            problems = manifest["problems"]
            if problems:
                raise SourceError(problems[0]["code"])
            raise _StopInspection("unsupported_layout")
        observed = {_origin(page["image_url"]) for page in manifest["pages"]}
        if not observed:
            raise _StopInspection("unsupported_layout")
        for origin in sorted(observed - {seed_origin}):
            try:
                async with asyncio.timeout(client.limits.request_timeout):
                    await client._addresses(origin + "/")
            except TimeoutError:
                raise SourceError("timeout") from None
        source = replace(selected.source, image_origins=tuple(sorted(observed)))
        result = asdict(replace(normalized, profiles=(source,)))
        return _ready(seed, scope, "configured", result, observed - {seed_origin},
                      len(manifest["pages"]), len(manifest["issues"]))


async def prepare_source(url: str, existing_config: SourceConfig | Mapping | None = None,
                         rights_confirmed: bool = False, transport: SourceTransport | None = None,
                         *, scope: str = "auto", allow_http: bool = False,
                         limits: SourceLimits | Mapping | None = None) -> SourceProposal:
    """Inspect without bypasses; authorization and scope are never inferred from publicity.

    ``allow_http=True`` is an explicit opt-in, or an existing advanced profile
    may opt in. HTTPS is the default. Existing profile limits remain unchanged;
    ``limits`` configures automatic setup only. Signed seeds are rejected before
    requests; discovered signed image links live solely inside this call and
    later inside SourceClient's normal opaque, expiring references.
    """
    seed = ""
    scope = "issue" if scope == "chapter" else scope
    try:
        if rights_confirmed is not True:
            raise SourceError("authorization_required")
        if scope not in {"auto", "series", "issue", "page"}:
            raise _StopInspection("invalid_scope")
        if existing_config is not None and not isinstance(existing_config, (SourceConfig, Mapping)):
            raise SourceError("invalid_config")
        profiles = existing_config.profiles if isinstance(existing_config, SourceConfig) else (existing_config or {}).get("profiles")
        if profiles:
            return await _configured(url, existing_config, transport, scope)
        options = asdict(existing_config) if isinstance(existing_config, SourceConfig) else dict(existing_config or {})
        if set(options) - {"profiles", "authorized", "allow_http", "limits"}:
            raise SourceError("invalid_config")
        if type(allow_http) is not bool or ("allow_http" in options and type(options["allow_http"]) is not bool):
            raise SourceError("invalid_config")
        allow_http = allow_http or options.get("allow_http", False)
        limits = limits if limits is not None else options.get("limits", SourceLimits())
        limits = SourceLimits(**limits) if isinstance(limits, Mapping) else limits
        value = _url(url, allow_http)
        # Reject fragments rather than silently changing the persisted source.
        if "#" in url:
            raise SourceError("unsafe_persistent_seed")
        parameter = _page_parameter(value)
        root = _root(urlsplit(value).path)
        if not root:
            raise _StopInspection("source_scope", ambiguous=True)
        profile = SourceProfile(
            "automatic", (_origin(value),), (_origin(value),), (root,), (root,),
            reader_selector=None, chapter_selector=None,
            series_next_selector=None, reader_next_selector=None,
            exclude_selector=_IMAGE_EXCLUDE, page_number_param=parameter,
            issue_title_selector="[data-chapter-title],.chapter-title",
            reader_next_within_issue=True,
        )
        config = SourceConfig((profile,), True, allow_http, limits)
        async with _InspectionClient(config, transport, _origin(value)) as client:
            seed = client.persistent_seed(url)
            return await _AutomaticInspection(client, seed, scope, profile).run()
    except _StopInspection as error:
        return SourceProposal(
            "ambiguous" if error.ambiguous else "unsupported", code=error.code,
            message=str(error), seed=seed, scope=scope,
            choices=("Use a specific chapter link", "Choose page/chapter/series scope") if error.ambiguous else (),
        )
    except SourceError as error:
        return SourceProposal("unsupported", code=error.code, message=error.message, seed=seed, scope=scope)
    except (TypeError, ValueError, OverflowError):
        error = SourceError("invalid_config")
        return SourceProposal("unsupported", code=error.code, message=error.message, seed=seed, scope=scope)


inspect_source = prepare_source
