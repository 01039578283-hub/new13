#!/usr/bin/env python3
"""Independent, read-only Phase 4 release auditor for 소수정예학원.com.

The auditor deliberately does not call either Phase 4 transformer's full-site
``build_plan``.  Instead it discovers the exact 3,378 public documents from
``sitemap.xml`` and, when projection scripts are supplied, composes the public
page-level transformations in memory in this order:

1. ``smallclass_phase4_technical.py``
2. ``smallclass_phase4_images.py``

No projected bytes are written.  Repository walks prune ``tmp``, ``.git`` and
``__pycache__`` before entry.  The final result is checked with parsers and
asset readers implemented here rather than relying only on transformer claims.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import importlib.util
import json
import os
import re
import struct
import subprocess
import sys
import xml.etree.ElementTree as ET
from collections import Counter
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from types import ModuleType
from typing import Any, Iterable, Mapping, Sequence
from urllib.parse import quote, unquote, urljoin, urlsplit, urlunsplit


sys.dont_write_bytecode = True

BASELINE_REF = "c6bb0e7d43bea43624adbd63db6b564eacf3f718"
BASE_URL = "https://xn--9l4b2jy4h4wan0chw4b.com"
BASE_HOST = "xn--9l4b2jy4h4wan0chw4b.com"
SITEMAP_NAMESPACE = "http://www.sitemaps.org/schemas/sitemap/0.9"
TECHNICAL_SCRIPT = "scripts/smallclass_phase4_technical.py"
IMAGES_SCRIPT = "scripts/smallclass_phase4_images.py"
AUDIT_SCRIPT = "scripts/smallclass_phase4_release_audit.py"
HUB_RELATIVE_PATH = "전국학원/초등수학학원/index.html"

PAGE_COUNT = 3_378
DETAIL_COUNT = 3_339
CATEGORY_COUNT = 9
CATEGORY_PAGE_COUNT = 371
INDEX_HREF_COUNT = 53_954
IMAGE_INPUT_COUNT = 10_048
IMAGE_OUTPUT_COUNT = 6_709
IMAGE_PAGE_COUNT = 3_367
HIDDEN_REPRESENTATIVE_COUNT = 3_339
HERO_COUNT = 27
BELOW_FOLD_COUNT = 6_682
DISTINCT_OUTPUT_ASSETS = 377
OUTPUT_IMAGE_TYPES = {".webp": 3_370, ".jpg": 3_330, ".png": 9}
HUB_REGION_COUNT = 13
HUB_CITY_COUNT = 76
HUB_LINK_COUNT = 371
HUB_QUERY = "명일동"
BROWSER_CASE_COUNT = 27
BROWSER_VIEWPORTS = (320, 390, 1440)
BROWSER_TEST_COUNT = 81
SITEMAP_SHA256 = "c18b089588fa0a054b0a5adca46c6b35f59fa0c7c4851f5bf12fcfc13738648a"
ASSET_MANIFEST_SHA256 = "bda21ed8c5bd0d3ae7816cec0b5f7ffcfca028062894c6b53d254e8f1cdceb05"
BASELINE_TARGET_MANIFEST_SHA256 = "00ffb53d795e0c2462f3552ee44b9766df4a51fcee60d6c7432b61dfeef8d567"
PRECLEANUP_TARGET_MANIFEST_SHA256 = "08830cec6591e9a1dcd5383ccc0eae8b100bb1c077844b09900b0891dd2217f3"
PROJECTED_TARGET_MANIFEST_SHA256 = "433eb216faa4e80198b56d26289a4155dd962e621d7a816e8ebdae6035b87fc6"
APPROVED_TECHNICAL_SHA256 = "7efbe917ae06d0c3a12b8527cd15d728beb1afd4cc3363315200a5839c7c7831"
APPROVED_IMAGES_SHA256 = "7b29150c7ae4ed09a857c347080a145ae9a0803b8e134a0d88192dcb8a12dd7e"

UTF8_BOM = b"\xef\xbb\xbf"
PRUNED_DIRS = {".git", "tmp", "__pycache__", "node_modules", ".vercel"}
RASTER_SUFFIXES = {".gif", ".jpg", ".jpeg", ".png", ".webp"}
VOID_ELEMENTS = {
    "area", "base", "br", "col", "embed", "hr", "img", "input", "link",
    "meta", "param", "source", "track", "wbr",
}
MANAGED_IMAGE_ATTRIBUTES = {"width", "height", "loading", "decoding", "fetchpriority"}

HEAD_RE = re.compile(r"<head\b[^>]*>.*?</head\s*>", re.IGNORECASE | re.DOTALL)
JSON_LD_RE = re.compile(
    r"<script\b(?=[^>]*\btype\s*=\s*[\"']application/ld\+json[\"'])[^>]*>"
    r"(.*?)</script\s*>",
    re.IGNORECASE | re.DOTALL,
)


@dataclass
class Audit:
    errors: list[dict[str, str]] = field(default_factory=list)
    counts: Counter[str] = field(default_factory=Counter)
    samples: dict[str, list[str]] = field(default_factory=dict)
    metrics: dict[str, Any] = field(default_factory=dict)
    sample_limit: int = 8

    def hard(self, condition: bool, code: str, detail: object = "") -> bool:
        if condition:
            return True
        value = str(detail)
        self.counts[code] += 1
        bucket = self.samples.setdefault(code, [])
        if len(bucket) < self.sample_limit:
            bucket.append(value)
        if len(self.errors) < 250:
            self.errors.append({"code": code, "detail": value})
        return False


@dataclass(frozen=True)
class SitemapRow:
    url: str
    lastmod: str
    path: Path
    relative: str


@dataclass(frozen=True)
class LinkRecord:
    tag: str
    href: str
    text: str


@dataclass(frozen=True)
class ImageRecord:
    attrs: tuple[tuple[str, str | None], ...]
    ancestor_classes: tuple[str, ...]
    ancestor_hidden: bool

    @property
    def attributes(self) -> dict[str, str | None]:
        return dict(self.attrs)

    @property
    def classes(self) -> tuple[str, ...]:
        return tuple((self.attributes.get("class") or "").split())

    @property
    def hero(self) -> bool:
        return any("hero" in token.lower() for token in (*self.ancestor_classes, *self.classes))

    @property
    def hidden_representative(self) -> bool:
        return "hidden-representative" in self.classes

    @property
    def hidden(self) -> bool:
        attrs = self.attributes
        style = re.sub(r"\s+", "", attrs.get("style") or "").lower()
        return (
            self.ancestor_hidden
            or "hidden" in attrs
            or (attrs.get("aria-hidden") or "").lower() == "true"
            or "display:none" in style
            or "visibility:hidden" in style
        )


@dataclass
class _StackEntry:
    tag: str
    classes: tuple[str, ...]
    hidden: bool
    skip_text: bool
    heading: str | None = None
    link_index: int | None = None
    fact_indexes: tuple[int, ...] = ()
    faq_scope: bool = False


class PageParser(HTMLParser):
    """Independent DOM inventory for links, images, metadata and protected copy."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.stack: list[_StackEntry] = []
        self.canonicals: list[str] = []
        self.robots: list[str] = []
        self.meta_properties: dict[str, list[str]] = {}
        self.links: list[dict[str, str]] = []
        self.images: list[ImageRecord] = []
        self.duplicate_attributes: list[str] = []
        self.picture_count = 0
        self.srcset_count = 0
        self.headings: dict[str, list[str]] = {"title": [], "h1": [], "h2": [], "h3": []}
        self.visible_parts: list[str] = []
        self.facts: list[dict[str, Any]] = []
        self.faq_parts: list[str] = []

    @staticmethod
    def _attrs(tag: str, attrs: list[tuple[str, str | None]]) -> tuple[tuple[str, str | None], ...]:
        lowered = tuple((name.lower(), value) for name, value in attrs)
        names = [name for name, _value in lowered]
        duplicates = sorted(name for name, count in Counter(names).items() if count > 1)
        if duplicates:
            raise ValueError(f"<{tag}> duplicate attributes: {duplicates}")
        return lowered

    def _start(self, tag: str, attrs: list[tuple[str, str | None]], self_closing: bool) -> None:
        tag = tag.lower()
        try:
            attributes = self._attrs(tag, attrs)
        except ValueError as exc:
            self.duplicate_attributes.append(str(exc))
            attributes = tuple((name.lower(), value) for name, value in attrs)
        amap = dict(attributes)
        classes = tuple((amap.get("class") or "").split())
        own_style = re.sub(r"\s+", "", amap.get("style") or "").lower()
        parent_hidden = any(entry.hidden for entry in self.stack)
        own_hidden = (
            "hidden" in amap
            or (amap.get("aria-hidden") or "").lower() == "true"
            or "display:none" in own_style
            or "visibility:hidden" in own_style
        )
        hidden = parent_hidden or own_hidden
        skip_text = tag in {"script", "style", "noscript", "template"} or any(
            entry.skip_text for entry in self.stack
        )

        if tag == "link" and "canonical" in {
            token.lower() for token in (amap.get("rel") or "").split()
        }:
            self.canonicals.append(html.unescape(amap.get("href") or "").strip())
        if tag == "meta" and (amap.get("name") or "").lower() == "robots":
            self.robots.append(html.unescape(amap.get("content") or "").strip())
        if tag == "meta" and amap.get("property"):
            key = (amap.get("property") or "").lower()
            self.meta_properties.setdefault(key, []).append(
                html.unescape(amap.get("content") or "").strip()
            )
        if tag == "picture":
            self.picture_count += 1
        if (tag == "source" and "srcset" in amap) or (tag == "img" and "srcset" in amap):
            self.srcset_count += 1
        if tag == "img":
            ancestor_classes = tuple(token for entry in self.stack for token in entry.classes)
            self.images.append(ImageRecord(attributes, ancestor_classes, parent_hidden))

        link_index: int | None = None
        if tag in {"a", "area"} and "href" in amap:
            link_index = len(self.links)
            self.links.append({"tag": tag, "href": html.unescape(amap.get("href") or ""), "text": ""})

        heading = tag if tag in self.headings else None
        fact_indexes: tuple[int, ...] = ()
        if "data-source-field" in amap or "data-source-status" in amap:
            fact_index = len(self.facts)
            self.facts.append(
                {
                    "tag": tag,
                    "field": amap.get("data-source-field") or "",
                    "status": amap.get("data-source-status") or "",
                    "text": "",
                }
            )
            fact_indexes = (fact_index,)
        faq_scope = any(entry.faq_scope for entry in self.stack) or "academy-faq-list" in classes

        if not self_closing and tag not in VOID_ELEMENTS:
            self.stack.append(
                _StackEntry(
                    tag=tag,
                    classes=classes,
                    hidden=hidden,
                    skip_text=skip_text,
                    heading=heading,
                    link_index=link_index,
                    fact_indexes=fact_indexes,
                    faq_scope=faq_scope,
                )
            )

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._start(tag, attrs, False)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._start(tag, attrs, True)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index].tag == tag:
                self.stack = self.stack[:index]
                return

    def handle_data(self, data: str) -> None:
        if not data:
            return
        if not any(entry.skip_text for entry in self.stack):
            self.visible_parts.append(data)
        for entry in self.stack:
            if entry.heading:
                self.headings[entry.heading].append(data)
            if entry.link_index is not None:
                self.links[entry.link_index]["text"] += data
            for index in entry.fact_indexes:
                self.facts[index]["text"] += data
        if any(entry.faq_scope for entry in self.stack):
            self.faq_parts.append(data)

    def finish(self) -> None:
        self.close()
        for record in self.links:
            record["text"] = clean(record["text"])
        for record in self.facts:
            record["text"] = clean(record["text"])


@dataclass
class HubInventory:
    directory_count: int = 0
    search: list[dict[str, str | None]] = field(default_factory=list)
    clear: list[dict[str, str | None]] = field(default_factory=list)
    status: list[dict[str, str | None]] = field(default_factory=list)
    labels_for: list[str] = field(default_factory=list)
    regions: list[dict[str, Any]] = field(default_factory=list)
    city_count: int = 0
    grid_count: int = 0
    links: list[LinkRecord] = field(default_factory=list)


class HubParser(HTMLParser):
    """Parse only the projected directory scope and its searchable links."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.inventory = HubInventory()
        self.stack: list[dict[str, Any]] = []
        self.directory_depth = 0
        self.grid_depth = 0
        self.active_link: int | None = None

    @staticmethod
    def _map(attrs: list[tuple[str, str | None]]) -> dict[str, str | None]:
        return {name.lower(): value for name, value in attrs}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        amap = self._map(attrs)
        classes = set((amap.get("class") or "").split())
        starts_directory = "data-academy-directory" in amap
        if starts_directory:
            self.inventory.directory_count += 1
            self.directory_depth += 1
        inside = self.directory_depth > 0
        starts_grid = inside and "academy-link-grid" in classes
        if starts_grid:
            self.inventory.grid_count += 1
            self.grid_depth += 1
        if inside and "data-academy-search" in amap:
            self.inventory.search.append(amap)
        if inside and "data-academy-search-clear" in amap:
            self.inventory.clear.append(amap)
        if inside and "data-academy-search-status" in amap:
            self.inventory.status.append(amap)
        if inside and tag == "label" and amap.get("for"):
            self.inventory.labels_for.append(amap.get("for") or "")
        if inside and tag == "details" and "data-academy-region" in amap:
            self.inventory.regions.append(
                {"name": amap.get("data-academy-region") or "", "open": "open" in amap}
            )
        if inside and "academy-city-group" in classes:
            self.inventory.city_count += 1
        starts_link = inside and bool(self.grid_depth) and tag == "a" and "href" in amap
        if starts_link:
            self.active_link = len(self.inventory.links)
            self.inventory.links.append(LinkRecord("a", html.unescape(amap.get("href") or ""), ""))
        if tag not in VOID_ELEMENTS:
            self.stack.append(
                {
                    "tag": tag,
                    "directory": starts_directory,
                    "grid": starts_grid,
                    "link": self.active_link if starts_link else None,
                }
            )

    def handle_data(self, data: str) -> None:
        if self.active_link is None or not data:
            return
        record = self.inventory.links[self.active_link]
        self.inventory.links[self.active_link] = LinkRecord(record.tag, record.href, record.text + data)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index]["tag"] != tag:
                continue
            removed = self.stack[index:]
            self.stack = self.stack[:index]
            for entry in reversed(removed):
                if entry["link"] is not None and self.active_link == entry["link"]:
                    self.active_link = None
                if entry["grid"]:
                    self.grid_depth -= 1
                if entry["directory"]:
                    self.directory_depth -= 1
            return

    def finish(self) -> HubInventory:
        self.close()
        self.inventory.links = [
            LinkRecord(record.tag, record.href, clean(record.text)) for record in self.inventory.links
        ]
        return self.inventory


def clean(value: object) -> str:
    return re.sub(r"\s+", " ", html.unescape(str(value or ""))).strip()


def sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def manifest(values: Mapping[str, bytes]) -> str:
    digest = hashlib.sha256()
    for name, value in sorted(values.items()):
        encoded = name.encode("utf-8")
        digest.update(len(encoded).to_bytes(4, "big"))
        digest.update(encoded)
        digest.update(len(value).to_bytes(8, "big"))
        digest.update(hashlib.sha256(value).digest())
    return digest.hexdigest()


def decode_html(value: bytes) -> tuple[str, bytes]:
    if value.startswith(UTF8_BOM):
        return value[len(UTF8_BOM):].decode("utf-8"), UTF8_BOM
    return value.decode("utf-8"), b""


def physical_trailing_whitespace(value: bytes) -> list[tuple[int, bytes]]:
    findings: list[tuple[int, bytes]] = []
    for line_number, line in enumerate(value.splitlines(keepends=True), 1):
        if line.endswith(b"\r\n"):
            body = line[:-2]
        elif line.endswith((b"\r", b"\n")):
            body = line[:-1]
        else:
            body = line
        match = re.search(rb"[ \t]+$", body)
        if match:
            findings.append((line_number, match.group(0)))
    return findings


def trailing_whitespace_inventory(
    root: Path,
    documents: Mapping[Path, bytes],
    sample_limit: int = 8,
) -> dict[str, Any]:
    occurrences = 0
    affected_files = 0
    samples: list[dict[str, Any]] = []
    for path in sorted(documents, key=lambda value: value.relative_to(root).as_posix()):
        findings = physical_trailing_whitespace(documents[path])
        if not findings:
            continue
        affected_files += 1
        occurrences += len(findings)
        relative = path.relative_to(root).as_posix()
        for line_number, suffix in findings:
            if len(samples) >= sample_limit:
                break
            samples.append(
                {
                    "path": relative,
                    "line": line_number,
                    "suffix": suffix.decode("ascii").replace(" ", "<SP>").replace("\t", "<TAB>"),
                    "suffix_hex": suffix.hex(" "),
                }
            )
    return {
        "occurrences": occurrences,
        "affected_files": affected_files,
        "samples": samples,
    }


def parse_page(text: str) -> PageParser:
    parser = PageParser()
    parser.feed(text)
    parser.finish()
    return parser


def head_fragment(text: str) -> str:
    matches = HEAD_RE.findall(text)
    if len(matches) != 1:
        raise ValueError(f"expected one head, found {len(matches)}")
    return matches[0]


def page_url(root: Path, path: Path) -> str:
    relative = path.relative_to(root).as_posix()
    route = "" if relative == "index.html" else relative[:-len("index.html")]
    return f"{BASE_URL}/{quote(route, safe='/')}"


def sitemap_rows(root: Path) -> tuple[bytes, list[SitemapRow]]:
    data = (root / "sitemap.xml").read_bytes()
    document = ET.fromstring(data)
    if document.tag != f"{{{SITEMAP_NAMESPACE}}}urlset":
        raise ValueError(f"unexpected sitemap root: {document.tag}")
    rows: list[SitemapRow] = []
    for index, node in enumerate(list(document)):
        if node.tag != f"{{{SITEMAP_NAMESPACE}}}url":
            raise ValueError(f"sitemap child[{index}] is not url")
        locs = node.findall(f"{{{SITEMAP_NAMESPACE}}}loc")
        lasts = node.findall(f"{{{SITEMAP_NAMESPACE}}}lastmod")
        if len(locs) != 1 or not (locs[0].text or "").strip():
            raise ValueError(f"sitemap url[{index}] loc cardinality")
        if len(lasts) != 1 or not (lasts[0].text or "").strip():
            raise ValueError(f"sitemap url[{index}] lastmod cardinality")
        url = (locs[0].text or "").strip()
        parsed = urlsplit(url)
        if f"{parsed.scheme}://{parsed.netloc}" != BASE_URL or parsed.query or parsed.fragment:
            raise ValueError(f"non-canonical sitemap URL: {url}")
        decoded = unquote(parsed.path)
        if not decoded.startswith("/") or not decoded.endswith("/"):
            raise ValueError(f"non-trailing sitemap route: {url}")
        route = decoded.strip("/")
        path = (root / "index.html" if not route else root / route / "index.html").resolve()
        path.relative_to(root)
        relative = path.relative_to(root).as_posix()
        rows.append(SitemapRow(url, (lasts[0].text or "").strip(), path, relative))
    return data, rows


def internal_index_href(value: str) -> bool:
    value = html.unescape(value)
    if not value or value != value.strip():
        return False
    parsed = urlsplit(value)
    if parsed.scheme and parsed.scheme.lower() not in {"http", "https"}:
        return False
    if parsed.hostname:
        try:
            host = parsed.hostname.encode("idna").decode("ascii").lower()
        except UnicodeError:
            return False
        if host != BASE_HOST:
            return False
    elif parsed.netloc:
        return False
    return parsed.path == "index.html" or parsed.path.endswith("/index.html")


def normalized_destination(base: str, value: str, *, strip_query_fragment: bool = False) -> str | None:
    decoded = html.unescape(value.strip())
    if not decoded:
        decoded = "./"
    raw = urlsplit(decoded)
    if raw.scheme and raw.scheme.lower() not in {"http", "https"}:
        return None
    resolved = urlsplit(urljoin(base, decoded))
    try:
        host = (resolved.hostname or "").encode("idna").decode("ascii").lower()
    except UnicodeError:
        return None
    if host != BASE_HOST:
        return None
    path = resolved.path
    if path == "/index.html":
        path = "/"
    elif path.endswith("/index.html"):
        path = path[:-len("index.html")]
    path = quote(unquote(path), safe="/~._-")
    query = "" if strip_query_fragment else resolved.query
    fragment = "" if strip_query_fragment else resolved.fragment
    return urlunsplit(("https", BASE_HOST, path, query, fragment))


def import_projection(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def iter_repository_files(root: Path) -> Iterable[Path]:
    """Walk without ever entering tmp/.git/__pycache__."""
    for current, directories, files in os.walk(root, topdown=True, followlinks=False):
        directories[:] = sorted(name for name in directories if name not in PRUNED_DIRS)
        base = Path(current)
        for name in sorted(files):
            if name.endswith(".pyc") or ".smallclass-phase4-" in name:
                continue
            yield base / name


def freeze_maps(root: Path, targets: set[Path]) -> tuple[dict[str, bytes], dict[str, bytes], dict[str, bytes]]:
    target_values: dict[str, bytes] = {}
    asset_values: dict[str, bytes] = {}
    external_values: dict[str, bytes] = {}
    resolved_targets = {path.resolve() for path in targets}
    for path in iter_repository_files(root):
        resolved = path.resolve()
        relative = path.relative_to(root).as_posix()
        value = path.read_bytes()
        if resolved in resolved_targets:
            target_values[relative] = value
        elif relative.startswith("assets/"):
            asset_values[relative] = value
        else:
            external_values[relative] = value
    return target_values, asset_values, external_values


def require_projection_api(module: ModuleType, names: Sequence[str], audit: Audit, label: str) -> bool:
    missing = [name for name in names if not callable(getattr(module, name, None))]
    return audit.hard(not missing, "projection_api", f"{label}: missing={missing}")


def project_technical(
    root: Path,
    rows: Sequence[SitemapRow],
    source: Mapping[Path, bytes],
    module: ModuleType,
    audit: Audit,
    *,
    local_second_pass: bool = True,
) -> tuple[dict[Path, bytes], dict[str, Any]]:
    output: dict[Path, bytes] = {}
    changes = 0
    changed_files = 0
    hub_changed = False
    hub_state = ""
    second_pass_changes = 0
    destination_mismatches = 0
    hub_path = (root / HUB_RELATIVE_PATH).resolve()

    for row in rows:
        value = source[row.path]
        text, bom = decode_html(value)
        after_hub = text
        this_hub_changed = False
        this_hub_state = "applied"
        if row.path == hub_path:
            after_hub, this_hub_changed, this_hub_state = module.replace_hub_contract(text)
            hub_changed = this_hub_changed
            hub_state = this_hub_state
        after, href_changes = module.transform_index_hrefs(after_hub)
        if href_changes:
            changed_files += 1
        changes += len(href_changes)
        for change in href_changes:
            before_destination = normalized_destination(row.url, change.old)
            after_destination = normalized_destination(row.url, change.new)
            if before_destination != after_destination:
                destination_mismatches += 1
        if local_second_pass:
            second_hub = after
            second_hub_changed = False
            second_hub_state = "applied"
            if row.path == hub_path:
                second_hub, second_hub_changed, second_hub_state = module.replace_hub_contract(after)
            second, second_changes = module.transform_index_hrefs(second_hub)
            if second != after or second_hub_changed or second_hub_state != "applied":
                second_pass_changes += 1
            second_pass_changes += len(second_changes)
        output[row.path] = bom + after.encode("utf-8")

    if changes == INDEX_HREF_COUNT and changed_files == PAGE_COUNT:
        state = "baseline"
    elif changes == 0 and changed_files == 0:
        state = "applied"
    else:
        state = "partial"
    audit.hard(state != "partial", "technical_projection_state", f"hrefs={changes} files={changed_files}")
    audit.hard(destination_mismatches == 0, "technical_destination", destination_mismatches)
    audit.hard(second_pass_changes == 0, "technical_local_idempotency", second_pass_changes)
    if state == "baseline":
        audit.hard(hub_changed and hub_state == "baseline", "technical_hub_state", hub_state)
    if state == "applied":
        audit.hard(not hub_changed and hub_state == "applied", "technical_hub_state", hub_state)
    return output, {
        "state": state,
        "href_changes": changes,
        "changed_files": changed_files,
        "hub_changed": hub_changed,
        "hub_state": hub_state,
        "second_pass_changes": second_pass_changes,
        "destination_mismatches": destination_mismatches,
    }


def project_images(
    root: Path,
    rows: Sequence[SitemapRow],
    source: Mapping[Path, bytes],
    module: ModuleType,
    audit: Audit,
    *,
    local_second_pass: bool = True,
) -> tuple[dict[Path, bytes], dict[str, Any]]:
    stats = module.MutationStats()
    dimensions: dict[Path, tuple[int, int]] = {}
    output: dict[Path, bytes] = {}
    second_pass_changes = 0
    for row in rows:
        text, bom = decode_html(source[row.path])
        after = module.transform_document(text, root, row.path, dimensions, stats)
        if local_second_pass:
            second = module.transform_document(after, root, row.path, dimensions, None)
            if second != after:
                second_pass_changes += 1
        output[row.path] = bom + after.encode("utf-8")
    module_error = ""
    try:
        module.validate_global_projection(stats)
    except Exception as exc:  # independent gates below still run
        module_error = str(exc)
    audit.hard(not module_error, "images_module_projection", module_error)
    audit.hard(second_pass_changes == 0, "images_local_idempotency", second_pass_changes)
    audit.hard(len(dimensions) == DISTINCT_OUTPUT_ASSETS, "images_dimension_inventory", len(dimensions))
    return output, {
        "input_images": stats.counts["input_images"],
        "input_hidden": stats.counts["input_hidden"],
        "output_images": stats.counts["output_images"],
        "output_hidden": stats.counts["output_hidden"],
        "hero": stats.counts["output_hero"],
        "below_fold": stats.counts["output_below_fold"],
        "pages_with_images": stats.counts["output_pages_with_images"],
        "distinct_output_assets": len(stats.output_asset_references),
        "dimensions": len(dimensions),
        "second_pass_changes": second_pass_changes,
        "module_error": module_error,
    }


def png_dimensions(data: bytes) -> tuple[int, int] | None:
    if data.startswith(b"\x89PNG\r\n\x1a\n") and len(data) >= 24:
        return struct.unpack(">II", data[16:24])
    return None


def gif_dimensions(data: bytes) -> tuple[int, int] | None:
    if data[:6] in {b"GIF87a", b"GIF89a"} and len(data) >= 10:
        return struct.unpack("<HH", data[6:10])
    return None


def jpeg_dimensions(data: bytes) -> tuple[int, int] | None:
    if not data.startswith(b"\xff\xd8"):
        return None
    sof = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}
    offset = 2
    while offset + 3 < len(data):
        if data[offset] != 0xFF:
            offset += 1
            continue
        while offset < len(data) and data[offset] == 0xFF:
            offset += 1
        if offset >= len(data):
            break
        marker = data[offset]
        offset += 1
        if marker in {0xD8, 0xD9} or 0xD0 <= marker <= 0xD7:
            continue
        if offset + 2 > len(data):
            break
        length = int.from_bytes(data[offset:offset + 2], "big")
        if length < 2 or offset + length > len(data):
            break
        if marker in sof and length >= 7:
            height = int.from_bytes(data[offset + 3:offset + 5], "big")
            width = int.from_bytes(data[offset + 5:offset + 7], "big")
            return width, height
        offset += length
    return None


def webp_dimensions(data: bytes) -> tuple[int, int] | None:
    if len(data) < 30 or not (data.startswith(b"RIFF") and data[8:12] == b"WEBP"):
        return None
    chunk = data[12:16]
    payload = data[20:]
    if chunk == b"VP8X":
        return 1 + int.from_bytes(payload[4:7], "little"), 1 + int.from_bytes(payload[7:10], "little")
    if chunk == b"VP8L" and payload and payload[0] == 0x2F:
        bits = int.from_bytes(payload[1:5], "little")
        return 1 + (bits & 0x3FFF), 1 + ((bits >> 14) & 0x3FFF)
    if chunk == b"VP8 ":
        frame = payload.find(b"\x9d\x01\x2a")
        if frame >= 0 and frame + 7 <= len(payload):
            return (
                int.from_bytes(payload[frame + 3:frame + 5], "little") & 0x3FFF,
                int.from_bytes(payload[frame + 5:frame + 7], "little") & 0x3FFF,
            )
    return None


def independent_dimensions(path: Path, cache: dict[Path, tuple[int, int]]) -> tuple[int, int]:
    if path in cache:
        return cache[path]
    data = path.read_bytes()
    value = png_dimensions(data) or gif_dimensions(data) or jpeg_dimensions(data) or webp_dimensions(data)
    if value is None:
        raise ValueError(f"unsupported/corrupt image: {path}")
    width, height = value
    if width <= 0 or height <= 0 or width > 1_000_000 or height > 1_000_000:
        raise ValueError(f"implausible dimensions {value}: {path}")
    cache[path] = value
    return value


def resolve_image(root: Path, page: Path, source: str) -> Path:
    source = html.unescape(source.strip())
    if not source:
        raise ValueError("empty img src")
    parsed = urlsplit(source)
    if parsed.scheme in {"http", "https"}:
        if f"{parsed.scheme}://{parsed.netloc}" != BASE_URL:
            raise ValueError(f"remote image: {source}")
        candidate = root / unquote(parsed.path).lstrip("/")
    elif parsed.scheme or source.startswith("//") or source.startswith("data:"):
        raise ValueError(f"unsupported image URI: {source}")
    elif parsed.path.startswith("/"):
        candidate = root / unquote(parsed.path).lstrip("/")
    else:
        candidate = page.parent / unquote(parsed.path)
    candidate = candidate.resolve()
    relative = candidate.relative_to(root)
    if not relative.parts or relative.parts[0] != "assets":
        raise ValueError(f"image outside assets: {source}")
    if candidate.suffix.lower() not in RASTER_SUFFIXES:
        raise ValueError(f"non-raster image: {source}")
    if not candidate.is_file():
        raise ValueError(f"broken image: {source}")
    return candidate


def json_nodes(value: Any) -> Iterable[Mapping[str, Any]]:
    if isinstance(value, Mapping):
        yield value
        for child in value.values():
            yield from json_nodes(child)
    elif isinstance(value, list):
        for child in value:
            yield from json_nodes(child)


def article_dates(text: str) -> list[tuple[str, str, str]]:
    rows: list[tuple[str, str, str]] = []
    for raw in JSON_LD_RE.findall(text):
        value = json.loads(raw)
        for node in json_nodes(value):
            types = node.get("@type", [])
            if isinstance(types, str):
                types = [types]
            if "Article" in types:
                rows.append(
                    (
                        str(node.get("@id") or node.get("url") or ""),
                        str(node.get("datePublished") or ""),
                        str(node.get("dateModified") or ""),
                    )
                )
    return rows


REQUIRED_SEARCH_JS = (
    'document.querySelectorAll("[data-academy-directory]")',
    'directory.querySelector("[data-academy-search]")',
    'directory.querySelector("[data-academy-search-clear]")',
    'directory.querySelector("[data-academy-search-status]")',
    'querySelectorAll(".academy-link-grid a")',
    "const matches = normalize(link.textContent).includes(query);",
    "link.hidden = !matches;",
    "region.open = index === 0;",
    'input?.addEventListener("input", applySearch)',
    'clearButton?.addEventListener("click"',
    'input.value = "";',
    "resetDirectory();",
    "검색 결과",
)


def detail_relative(relative: str) -> bool:
    parts = relative.split("/")
    return len(parts) == 4 and parts[0] == "전국학원" and parts[-1] == "index.html"


def normalized_fact_rows(parser: PageParser) -> list[tuple[str, str, str, str]]:
    return [
        (record["tag"], record["field"], record["status"], record["text"])
        for record in parser.facts
    ]


def unmanaged_images(parser: PageParser) -> list[tuple[tuple[str, str | None], ...]]:
    return [
        tuple((name, value) for name, value in image.attrs if name not in MANAGED_IMAGE_ATTRIBUTES)
        for image in parser.images
        if not image.hidden_representative
    ]


def inspect_hub(
    root: Path,
    text: str,
    canonical_set: set[str],
    script_bytes: bytes,
    audit: Audit,
) -> dict[str, Any]:
    parser = HubParser()
    parser.feed(text)
    hub = parser.finish()
    audit.hard(hub.directory_count == 1, "hub_directory_count", hub.directory_count)
    audit.hard(len(hub.search) == 1, "hub_search_count", len(hub.search))
    audit.hard(len(hub.clear) == 1, "hub_clear_count", len(hub.clear))
    audit.hard(len(hub.status) == 1, "hub_status_count", len(hub.status))
    if len(hub.search) == 1:
        search = hub.search[0]
        audit.hard(search.get("id") == "academy-local-search-elementary-math", "hub_search_id", search)
        audit.hard(search.get("type") == "search", "hub_search_type", search)
        audit.hard(search.get("inputmode") == "search", "hub_search_inputmode", search)
        audit.hard(search.get("autocomplete") == "off", "hub_search_autocomplete", search)
        audit.hard(
            "academy-local-search-elementary-math" in hub.labels_for,
            "hub_search_label",
            hub.labels_for,
        )
    if len(hub.clear) == 1:
        clear_attrs = hub.clear[0]
        audit.hard(clear_attrs.get("type") == "button", "hub_clear_type", clear_attrs)
        audit.hard("hidden" in clear_attrs, "hub_clear_initial_hidden", clear_attrs)
    if len(hub.status) == 1:
        status = hub.status[0]
        audit.hard("data-academy-result" in status, "hub_status_result_hook", status)
        audit.hard(status.get("aria-live") == "polite", "hub_status_live", status)

    audit.hard(len(hub.regions) == HUB_REGION_COUNT, "hub_region_count", len(hub.regions))
    audit.hard(hub.city_count == HUB_CITY_COUNT, "hub_city_count", hub.city_count)
    audit.hard(hub.grid_count == HUB_CITY_COUNT, "hub_grid_count", hub.grid_count)
    audit.hard(len(hub.links) == HUB_LINK_COUNT, "hub_link_count", len(hub.links))
    open_regions = [region for region in hub.regions if region["open"]]
    audit.hard(len(open_regions) == 1, "hub_open_region_count", open_regions)
    audit.hard(bool(hub.regions) and hub.regions[0]["name"] == "서울", "hub_first_region", hub.regions[:1])
    audit.hard(bool(hub.regions) and hub.regions[0]["open"], "hub_first_region_open", hub.regions[:1])

    hrefs = [record.href for record in hub.links]
    texts = [clean(record.text) for record in hub.links]
    destinations = [
        normalized_destination(f"{BASE_URL}/전국학원/초등수학학원/", value, strip_query_fragment=True)
        for value in hrefs
    ]
    audit.hard(len(set(hrefs)) == HUB_LINK_COUNT, "hub_href_unique", len(set(hrefs)))
    audit.hard(len(set(destinations)) == HUB_LINK_COUNT, "hub_destination_unique", len(set(destinations)))
    audit.hard(set(destinations) <= canonical_set, "hub_destination_broken", sorted(set(destinations) - canonical_set)[:4])
    normalize = lambda value: re.sub(r"\s+", "", value).lower()
    query_matches = sum(normalize(HUB_QUERY) in normalize(value) for value in texts)
    initial_visible = len(texts)
    query_visible = query_matches
    reset_visible = len(texts)
    audit.hard(initial_visible == 371, "hub_initial_visible", initial_visible)
    audit.hard(query_visible == 1, "hub_query_visible", query_visible)
    audit.hard(reset_visible == 371, "hub_reset_visible", reset_visible)

    script = script_bytes.decode("utf-8")
    missing = [snippet for snippet in REQUIRED_SEARCH_JS if snippet not in script]
    audit.hard(not missing, "hub_shared_js_contract", missing)
    return {
        "directory": hub.directory_count,
        "regions": len(hub.regions),
        "cities": hub.city_count,
        "links": len(hub.links),
        "initial_visible": initial_visible,
        "query": HUB_QUERY,
        "query_visible": query_visible,
        "query_status": f"‘{HUB_QUERY}’ 검색 결과 {query_visible}개의 동네 페이지를 찾았습니다.",
        "reset_visible": reset_visible,
        "script_sha256": sha256(script_bytes),
    }


def audit_documents(
    root: Path,
    rows: Sequence[SitemapRow],
    source: Mapping[Path, bytes],
    final: Mapping[Path, bytes],
    audit: Audit,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    canonical_set = {row.url for row in rows}
    baseline_index_hrefs = 0
    final_index_hrefs = 0
    changed_hrefs = 0
    link_destination_mismatches = 0
    link_sequence_mismatches = 0
    broken_destinations: set[str] = set()
    internal_destinations: set[str] = set()
    head_mismatches = 0
    json_mismatches = 0
    visible_mismatches = 0
    source_fact_mismatches = 0
    faq_mismatches = 0
    heading_mismatches = 0
    duplicate_attributes = 0
    baseline_images = 0
    baseline_hidden = 0
    baseline_hidden_representatives = 0
    output_images = 0
    output_hidden = 0
    output_hidden_representatives = 0
    output_pages_with_images = 0
    output_hero = 0
    output_below = 0
    picture_srcset = 0
    broken_images: list[str] = []
    image_policy_errors: list[str] = []
    unmanaged_image_mismatches = 0
    detail_image_count_errors = 0
    output_assets: Counter[str] = Counter()
    output_types: Counter[str] = Counter()
    dimensions: dict[Path, tuple[int, int]] = {}
    detail_articles = 0
    detail_date_errors: list[str] = []
    detail_modified: Counter[str] = Counter()
    detail_og_references = 0
    detail_og_assets: set[str] = set()
    detail_og_errors: list[str] = []
    phase3_before = hashlib.sha256()
    phase3_after = hashlib.sha256()
    category_cases: dict[str, dict[str, str]] = {}
    hub_cases: list[dict[str, Any]] = []
    hub_text = ""

    for row in rows:
        before_text, _before_bom = decode_html(source[row.path])
        final_text, _final_bom = decode_html(final[row.path])
        try:
            before_parser = parse_page(before_text)
            final_parser = parse_page(final_text)
        except Exception as exc:
            audit.hard(False, "html_parse", f"{row.relative}: {exc}")
            continue
        duplicate_attributes += len(before_parser.duplicate_attributes) + len(final_parser.duplicate_attributes)

        try:
            before_head = head_fragment(before_text)
            final_head = head_fragment(final_text)
        except Exception as exc:
            audit.hard(False, "head_parse", f"{row.relative}: {exc}")
            continue
        if before_head != final_head:
            head_mismatches += 1
        if JSON_LD_RE.findall(before_text) != JSON_LD_RE.findall(final_text):
            json_mismatches += 1
        if [clean(value) for value in before_parser.headings["title"]] != [
            clean(value) for value in final_parser.headings["title"]
        ] or [clean(value) for value in before_parser.headings["h1"]] != [
            clean(value) for value in final_parser.headings["h1"]
        ]:
            heading_mismatches += 1
        if row.relative != HUB_RELATIVE_PATH and clean(" ".join(before_parser.visible_parts)) != clean(
            " ".join(final_parser.visible_parts)
        ):
            visible_mismatches += 1
        if normalized_fact_rows(before_parser) != normalized_fact_rows(final_parser):
            source_fact_mismatches += 1
        if clean(" ".join(before_parser.faq_parts)) != clean(" ".join(final_parser.faq_parts)):
            faq_mismatches += 1

        protected_before = json.dumps(
            {
                "relative": row.relative,
                "head": before_head,
                "visible": "" if row.relative == HUB_RELATIVE_PATH else clean(" ".join(before_parser.visible_parts)),
                "facts": normalized_fact_rows(before_parser),
                "faq": clean(" ".join(before_parser.faq_parts)),
            },
            ensure_ascii=False,
            sort_keys=True,
        ).encode("utf-8")
        protected_after = json.dumps(
            {
                "relative": row.relative,
                "head": final_head,
                "visible": "" if row.relative == HUB_RELATIVE_PATH else clean(" ".join(final_parser.visible_parts)),
                "facts": normalized_fact_rows(final_parser),
                "faq": clean(" ".join(final_parser.faq_parts)),
            },
            ensure_ascii=False,
            sort_keys=True,
        ).encode("utf-8")
        phase3_before.update(len(protected_before).to_bytes(8, "big")); phase3_before.update(protected_before)
        phase3_after.update(len(protected_after).to_bytes(8, "big")); phase3_after.update(protected_after)

        audit.hard(final_parser.canonicals == [row.url], "canonical_exact", f"{row.relative}: {final_parser.canonicals}")
        if len(final_parser.robots) != 1:
            audit.hard(False, "robots_cardinality", f"{row.relative}: {final_parser.robots}")
        else:
            tokens = {token.strip().lower() for token in final_parser.robots[0].split(",")}
            audit.hard("noindex" not in tokens, "noindex", row.relative)
            audit.hard({"index", "follow"} <= tokens, "robots_index_follow", f"{row.relative}: {tokens}")

        before_links = before_parser.links
        final_links = final_parser.links
        if len(before_links) != len(final_links):
            link_sequence_mismatches += 1
        for before_link, after_link in zip(before_links, final_links):
            if before_link["tag"] != after_link["tag"]:
                link_sequence_mismatches += 1
            before_index = internal_index_href(before_link["href"])
            after_index = internal_index_href(after_link["href"])
            baseline_index_hrefs += before_index
            final_index_hrefs += after_index
            changed_hrefs += before_link["href"] != after_link["href"]
            before_destination = normalized_destination(row.url, before_link["href"])
            after_destination = normalized_destination(row.url, after_link["href"])
            if before_destination != after_destination:
                link_destination_mismatches += 1
            route = normalized_destination(row.url, after_link["href"], strip_query_fragment=True)
            if route is not None:
                internal_destinations.add(route)
                if route not in canonical_set:
                    broken_destinations.add(route)

        baseline_images += len(before_parser.images)
        baseline_hidden += sum(image.hidden for image in before_parser.images)
        baseline_hidden_representatives += sum(image.hidden_representative for image in before_parser.images)
        output_images += len(final_parser.images)
        output_hidden += sum(image.hidden for image in final_parser.images)
        output_hidden_representatives += sum(image.hidden_representative for image in final_parser.images)
        output_pages_with_images += bool(final_parser.images)
        output_hero += sum(image.hero for image in final_parser.images)
        output_below += sum(not image.hero for image in final_parser.images)
        picture_srcset += final_parser.picture_count + final_parser.srcset_count
        if unmanaged_images(before_parser) != unmanaged_images(final_parser):
            unmanaged_image_mismatches += 1
        if detail_relative(row.relative) and len(final_parser.images) != 2:
            detail_image_count_errors += 1

        for image in final_parser.images:
            attrs = image.attributes
            source_value = attrs.get("src") or ""
            try:
                asset = resolve_image(root, row.path, source_value)
                width, height = independent_dimensions(asset, dimensions)
            except Exception as exc:
                if len(broken_images) < 12:
                    broken_images.append(f"{row.relative}: {exc}")
                continue
            relative_asset = asset.relative_to(root).as_posix()
            output_assets[relative_asset] += 1
            output_types[asset.suffix.lower()] += 1
            expected = {
                "width": str(width),
                "height": str(height),
                "loading": "eager" if image.hero else "lazy",
                "decoding": "async",
            }
            for name, value in expected.items():
                if attrs.get(name) != value and len(image_policy_errors) < 12:
                    image_policy_errors.append(
                        f"{row.relative}: {source_value} {name}={attrs.get(name)!r}, expected={value!r}"
                    )
            priority = attrs.get("fetchpriority")
            if image.hero and priority != "high" and len(image_policy_errors) < 12:
                image_policy_errors.append(f"{row.relative}: hero fetchpriority={priority!r}")
            if not image.hero and priority is not None and len(image_policy_errors) < 12:
                image_policy_errors.append(f"{row.relative}: below-fold fetchpriority={priority!r}")

        dates = article_dates(final_text)
        if detail_relative(row.relative):
            og_values = final_parser.meta_properties.get("og:image", [])
            detail_og_references += len(og_values)
            if len(og_values) != 1:
                if len(detail_og_errors) < 10:
                    detail_og_errors.append(f"{row.relative}: og:image count={len(og_values)}")
            else:
                try:
                    og_asset = resolve_image(root, row.path, og_values[0])
                    detail_og_assets.add(og_asset.relative_to(root).as_posix())
                except Exception as exc:
                    if len(detail_og_errors) < 10:
                        detail_og_errors.append(f"{row.relative}: {exc}")
            detail_articles += len(dates)
            if len(dates) != 1:
                if len(detail_date_errors) < 10:
                    detail_date_errors.append(f"{row.relative}: Article count={len(dates)}")
            else:
                _identifier, _published, modified = dates[0]
                detail_modified[modified] += 1
                if modified != row.lastmod or modified != "2026-08-18":
                    if len(detail_date_errors) < 10:
                        detail_date_errors.append(
                            f"{row.relative}: modified={modified!r} lastmod={row.lastmod!r}"
                        )

        if row.relative == HUB_RELATIVE_PATH:
            hub_text = final_text
        parts = row.relative.split("/")
        if len(parts) == 3 and parts[0] == "전국학원" and parts[-1] == "index.html":
            hub_cases.append({"kind": "hub", "slug": parts[1], "path": urlsplit(row.url).path})
        if detail_relative(row.relative):
            category = parts[1]
            unsupported = any(record["status"] == "unconfirmed-grade" for record in final_parser.facts)
            slot = "unsupported" if unsupported else "supported"
            category_cases.setdefault(category, {}).setdefault(slot, urlsplit(row.url).path)

    orphan_urls = canonical_set - internal_destinations
    audit.hard(duplicate_attributes == 0, "duplicate_attributes", duplicate_attributes)
    audit.hard(baseline_index_hrefs in {INDEX_HREF_COUNT, 0}, "input_internal_index_state", baseline_index_hrefs)
    audit.hard(final_index_hrefs == 0, "internal_index_href_residual", final_index_hrefs)
    audit.hard(link_sequence_mismatches == 0, "link_sequence", link_sequence_mismatches)
    audit.hard(link_destination_mismatches == 0, "link_destination_exact", link_destination_mismatches)
    audit.hard(changed_hrefs == baseline_index_hrefs, "internal_href_change_count", f"changed={changed_hrefs} input={baseline_index_hrefs}")
    audit.hard(not broken_destinations, "internal_broken", sorted(broken_destinations)[:8])
    audit.hard(not orphan_urls, "internal_orphan", sorted(orphan_urls)[:8])
    audit.hard(head_mismatches == 0, "head_unchanged", head_mismatches)
    audit.hard(json_mismatches == 0, "schema_unchanged", json_mismatches)
    audit.hard(visible_mismatches == 0, "content_copy_unchanged", visible_mismatches)
    audit.hard(source_fact_mismatches == 0, "source_facts_unchanged", source_fact_mismatches)
    audit.hard(faq_mismatches == 0, "faq_unchanged", faq_mismatches)
    audit.hard(heading_mismatches == 0, "title_h1_unchanged", heading_mismatches)
    audit.hard(phase3_before.digest() == phase3_after.digest(), "phase3_quality_snapshot", "protected snapshot drift")

    audit.hard(baseline_images in {IMAGE_INPUT_COUNT, IMAGE_OUTPUT_COUNT}, "input_image_state", baseline_images)
    audit.hard(baseline_hidden_representatives in {HIDDEN_REPRESENTATIVE_COUNT, 0}, "input_hidden_state", baseline_hidden_representatives)
    audit.hard(output_images == IMAGE_OUTPUT_COUNT, "image_count", output_images)
    audit.hard(output_pages_with_images == IMAGE_PAGE_COUNT, "image_page_count", output_pages_with_images)
    audit.hard(output_hidden == 0, "hidden_images", output_hidden)
    audit.hard(output_hidden_representatives == 0, "hidden_representatives", output_hidden_representatives)
    audit.hard(output_hero == HERO_COUNT, "hero_count", output_hero)
    audit.hard(output_below == BELOW_FOLD_COUNT, "below_fold_count", output_below)
    audit.hard(picture_srcset == 0, "responsive_image_uncontracted", picture_srcset)
    audit.hard(not broken_images, "broken_images", broken_images)
    audit.hard(not image_policy_errors, "image_policy", image_policy_errors)
    audit.hard(unmanaged_image_mismatches == 0, "image_unmanaged_attributes", unmanaged_image_mismatches)
    audit.hard(detail_image_count_errors == 0, "detail_image_count", detail_image_count_errors)
    audit.hard(len(output_assets) == DISTINCT_OUTPUT_ASSETS, "distinct_output_assets", len(output_assets))
    audit.hard(dict(output_types) == OUTPUT_IMAGE_TYPES, "output_image_types", dict(output_types))
    audit.hard(len(dimensions) == DISTINCT_OUTPUT_ASSETS, "intrinsic_dimension_assets", len(dimensions))
    audit.hard(detail_articles == DETAIL_COUNT, "detail_article_count", detail_articles)
    audit.hard(not detail_date_errors, "article_sitemap_date", detail_date_errors)
    audit.hard(detail_og_references == DETAIL_COUNT, "detail_og_count", detail_og_references)
    audit.hard(not detail_og_errors, "detail_og_assets", detail_og_errors)
    audit.hard(len(detail_og_assets) == 2_226, "detail_og_distinct_assets", len(detail_og_assets))

    hub_metrics: dict[str, Any] = {}
    if hub_text:
        hub_metrics = inspect_hub(root, hub_text, canonical_set, (root / "assets/script.js").read_bytes(), audit)
    else:
        audit.hard(False, "hub_missing", HUB_RELATIVE_PATH)

    browser_cases = sorted(hub_cases, key=lambda value: value["slug"])
    for category in sorted(category_cases):
        slots = category_cases[category]
        audit.hard("supported" in slots, "browser_supported_case", category)
        audit.hard("unsupported" in slots, "browser_unsupported_case", category)
        for slot in ("supported", "unsupported"):
            if slot in slots:
                browser_cases.append(
                    {
                        "kind": "detail",
                        "slug": category,
                        "path": slots[slot],
                        "unsupported": slot == "unsupported",
                    }
                )
    audit.hard(len(browser_cases) == BROWSER_CASE_COUNT, "browser_case_count", len(browser_cases))
    return {
        "links": {
            "input_internal_index": baseline_index_hrefs,
            "projected_internal_index": final_index_hrefs,
            "changed_href_values": changed_hrefs,
            "destination_mismatches": link_destination_mismatches,
            "broken": len(broken_destinations),
            "orphans": len(orphan_urls),
        },
        "images": {
            "input": baseline_images,
            "input_hidden": baseline_hidden,
            "input_hidden_representative": baseline_hidden_representatives,
            "output": output_images,
            "output_hidden": output_hidden,
            "pages_with_images": output_pages_with_images,
            "hero": output_hero,
            "below_fold": output_below,
            "distinct_assets": len(output_assets),
            "types": dict(output_types),
            "dimension_assets": len(dimensions),
            "broken": len(broken_images),
        },
        "protected": {
            "head_mismatches": head_mismatches,
            "schema_mismatches": json_mismatches,
            "copy_mismatches": visible_mismatches,
            "source_fact_mismatches": source_fact_mismatches,
            "faq_mismatches": faq_mismatches,
            "phase3_before_sha256": phase3_before.hexdigest(),
            "phase3_after_sha256": phase3_after.hexdigest(),
            "detail_article_dates": dict(detail_modified),
            "detail_og_references": detail_og_references,
            "detail_og_distinct_assets": len(detail_og_assets),
        },
        "hub": hub_metrics,
    }, browser_cases


def browser_contract(cases: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    return {
        "case_count": len(cases),
        "viewports": list(BROWSER_VIEWPORTS),
        "test_count": len(cases) * len(BROWSER_VIEWPORTS),
        "expected": {
            "status": 200,
            "h1": 1,
            "overflow": 0,
            "broken_visible_images": 0,
            "hidden_images": 0,
            "canonical": "production route exact",
            "noindex": 0,
            "hub_initial": 371,
            "hub_query": HUB_QUERY,
            "hub_query_visible": 1,
            "hub_reset": 371,
            "hub_reset_input": "",
            "hub_reset_status": "광역지역을 열거나 동네 이름을 검색하세요.",
            "hub_reset_open_regions": 1,
            "hub_reset_first_region": True,
            "hub_reset_clear_hidden": True,
            "hub_reset_focus": True,
            "image_intrinsic_and_policy_errors": 0,
        },
    }


def run_browser(base: str, cases: Sequence[Mapping[str, Any]], root: Path, timeout: int) -> dict[str, Any]:
    base = base.rstrip("/")
    node_path = os.environ.get("NODE_PATH", "")
    if not node_path:
        candidates = [root / "node_modules", root.parent / "node_modules"]
        candidates.extend(
            sibling / "node_modules"
            for sibling in sorted(root.parent.iterdir(), key=lambda value: value.name)
            if sibling.is_dir() and sibling.name not in PRUNED_DIRS
        )
        for candidate in candidates:
            if (candidate / "playwright").is_dir():
                node_path = str(candidate)
                break
    script = r'''
const { chromium } = require('playwright');
const base = process.argv[2].replace(/\/$/, '');
const production = process.argv[3].replace(/\/$/, '');
const cases = JSON.parse(process.argv[4]);
const widths = JSON.parse(process.argv[5]);
(async()=>{
 const browser=await chromium.launch({headless:true}); const rows=[];
 for(const width of widths){
  const context=await browser.newContext({viewport:{width,height:844},locale:'ko-KR'});
  for(const testCase of cases){
   const page=await context.newPage(); const errors=[]; const failed=[];
   page.on('pageerror',e=>errors.push(e.message));
   page.on('console',m=>m.type()==='error'&&errors.push(m.text()));
   page.on('requestfailed',r=>failed.push(r.url()));
   page.on('response',r=>r.status()>=400&&failed.push(`${r.status()} ${r.url()}`));
   let response=null;
   try { response=await page.goto(base+testCase.path,{waitUntil:'load',timeout:30000}); }
   catch(e){ errors.push(String(e)); }
   const search={initial:0,shown:0,reset:0,status:'',value:null,resetStatus:'',openRegions:0,firstOpen:false,clearHidden:false,focused:false};
   if(testCase.kind==='hub'){
    const input=page.locator('[data-academy-search]');
    search.initial=await page.locator('.academy-link-grid a:not([hidden])').count();
    if(await input.count()===1){
     await input.fill('명일동'); await page.waitForTimeout(100);
     search.shown=await page.locator('.academy-link-grid a:not([hidden])').count();
     search.status=await page.locator('[data-academy-search-status]').innerText().catch(()=> '');
     const clear=page.locator('[data-academy-search-clear]');
      if(await clear.count()===1) await clear.click(); else await input.fill('');
      await page.waitForTimeout(100);
      search.reset=await page.locator('.academy-link-grid a:not([hidden])').count();
      search.value=await input.inputValue();
      search.resetStatus=await page.locator('[data-academy-search-status]').innerText().catch(()=> '');
      search.openRegions=await page.locator('[data-academy-region][open]:not([hidden])').count();
      search.firstOpen=await page.locator('[data-academy-region]').evaluateAll(nodes=>
       nodes.length===13&&nodes.every((node,index)=>!node.hidden&&(index===0?node.open:!node.open))
      );
      search.clearHidden=await clear.evaluate(node=>node.hidden).catch(()=>false);
      search.focused=await input.evaluate(node=>document.activeElement===node);
    }
   }
   const beforeLoad=await page.evaluate(()=>{
    const records=[...document.images].map(img=>{
     const ancestry=[img,...Array.from((function*(){let n=img.parentElement;while(n){yield n;n=n.parentElement}})())];
     const hero=ancestry.some(node=>[...node.classList].some(token=>token.toLowerCase().includes('hero')));
     const style=getComputedStyle(img); const hidden=img.hidden||img.getAttribute('aria-hidden')==='true'||style.display==='none'||style.visibility==='hidden';
     const width=Number(img.getAttribute('width')); const height=Number(img.getAttribute('height'));
     const policy=(img.getAttribute('decoding')==='async' && width>0 && height>0 &&
       (hero ? img.getAttribute('loading')==='eager'&&img.getAttribute('fetchpriority')==='high'
             : img.getAttribute('loading')==='lazy'&&!img.hasAttribute('fetchpriority')));
     return {hero,hidden,policy,width,height};
    });
    return {records,hidden:records.filter(x=>x.hidden).length,policyBad:records.filter(x=>!x.policy).length};
   });
   await page.evaluate(async()=>{
    const images=[...document.images].filter(x=>{const s=getComputedStyle(x);return s.display!=='none'&&s.visibility!=='hidden'});
    images.forEach(x=>x.loading='eager');
    await Promise.race([
     Promise.all(images.map(x=>x.complete?Promise.resolve():new Promise(resolve=>{
      x.addEventListener('load',resolve,{once:true});x.addEventListener('error',resolve,{once:true});
     }))),
     new Promise(resolve=>setTimeout(resolve,3000))
    ]);
   });
   const dom=await page.evaluate(()=>({
    h1:document.querySelectorAll('h1').length,
    overflow:document.documentElement.scrollWidth>document.documentElement.clientWidth+1,
    brokenImages:[...document.images].filter(x=>{const s=getComputedStyle(x);return s.display!=='none'&&s.visibility!=='hidden'&&(!x.complete||!x.naturalWidth)}).length,
    intrinsicMismatch:[...document.images].filter(x=>x.naturalWidth>0&&(
      Number(x.getAttribute('width'))!==x.naturalWidth||Number(x.getAttribute('height'))!==x.naturalHeight
    )).length,
    canonical:document.querySelector('link[rel="canonical"]')?.href||'',
    metaRobots:document.querySelector('meta[name="robots"]')?.content||'',
    markers:document.querySelectorAll('[data-source-status="unconfirmed-grade"]').length
   }));
   const headers=response?await response.allHeaders():{};
   rows.push({width,...testCase,status:response&&response.status(),errors,failed,search,beforeLoad,dom,xRobots:headers['x-robots-tag']||''});
   await page.close();
  }
  await context.close();
 }
 await browser.close();
 const bad=rows.filter(row=>
  row.status!==200||row.errors.length||row.failed.length||row.dom.h1!==1||row.dom.overflow||
  row.dom.brokenImages||row.dom.intrinsicMismatch||row.beforeLoad.hidden||row.beforeLoad.policyBad||
  row.dom.canonical!==production+row.path||/noindex/i.test(row.dom.metaRobots)||/noindex/i.test(row.xRobots)||
  (row.kind==='detail'&&((row.unsupported&&row.dom.markers<3)||(!row.unsupported&&row.dom.markers)))||
   (row.kind==='hub'&&(row.search.initial!==371||row.search.shown!==1||row.search.reset!==371||
    !row.search.status.includes('검색 결과')||row.search.value!==''||
    row.search.resetStatus!=='광역지역을 열거나 동네 이름을 검색하세요.'||
    row.search.openRegions!==1||!row.search.firstOpen||!row.search.clearHidden||!row.search.focused))
 );
 process.stdout.write(JSON.stringify({tests:rows.length,failures:bad.length,bad:bad.slice(0,20)}));
})().catch(error=>{process.stderr.write(String(error));process.exit(2)});
'''
    environment = os.environ.copy()
    if node_path:
        environment["NODE_PATH"] = node_path
    try:
        completed = subprocess.run(
            [
                "node", "-", base, BASE_URL,
                json.dumps(list(cases), ensure_ascii=False),
                json.dumps(list(BROWSER_VIEWPORTS)),
            ],
            input=script,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=environment,
        )
    except subprocess.TimeoutExpired:
        return {"tests": 0, "failures": 1, "error": "browser timeout"}
    if completed.returncode:
        return {
            "tests": 0,
            "failures": 1,
            "returncode": completed.returncode,
            "error": completed.stderr[-1500:],
        }
    try:
        return json.loads(completed.stdout)
    except Exception:
        return {"tests": 0, "failures": 1, "error": completed.stdout[-1500:]}


def semantic_self_tests() -> list[str]:
    errors: list[str] = []
    old = "../index.html?q=1#x"
    new = "../?q=1#x"
    base = f"{BASE_URL}/a/b/"
    if not internal_index_href(old) or internal_index_href(new):
        errors.append("internal-index classification")
    if normalized_destination(base, old) != normalized_destination(base, new):
        errors.append("index/trailing destination parity")
    synthetic = '<a href="index.html">Root</a><img class="hero" src="/assets/x.webp" width="1">'
    parser = parse_page(synthetic)
    if len(parser.links) != 1 or parser.links[0]["text"] != "Root" or len(parser.images) != 1:
        errors.append("HTML inventory")
    png = b"\x89PNG\r\n\x1a\n" + b"\0" * 8 + struct.pack(">II", 17, 23)
    if png_dimensions(png) != (17, 23):
        errors.append("PNG dimensions")
    clean_lines = (b"", b"<p>x</p>\r\n", b"a\nb\r\nc")
    if any(physical_trailing_whitespace(value) for value in clean_lines):
        errors.append("physical trailing-whitespace clean CRLF/LF/EOF")
    dirty_lines = (b"  \r\n", b"<p>x</p>\t\n", b"tail ")
    dirty_findings = [physical_trailing_whitespace(value) for value in dirty_lines]
    if [len(value) for value in dirty_findings] != [1, 1, 1] or [
        value[0][0] for value in dirty_findings
    ] != [1, 1, 1]:
        errors.append("physical trailing-whitespace dirty CRLF/LF/EOF")
    return errors


def git_output(root: Path, *arguments: str) -> bytes:
    completed = subprocess.run(
        ["git", *arguments],
        cwd=root,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if completed.returncode:
        raise RuntimeError(completed.stderr.decode("utf-8", "replace"))
    return completed.stdout


def audit_site(args: argparse.Namespace) -> dict[str, Any]:
    root = args.root.resolve()
    audit = Audit()
    head = git_output(root, "rev-parse", "HEAD").decode("ascii", "replace").strip()
    branch = git_output(root, "branch", "--show-current").decode("utf-8", "replace").strip()
    ancestor = subprocess.run(
        ["git", "merge-base", "--is-ancestor", BASELINE_REF, head],
        cwd=root,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    audit.hard(ancestor.returncode == 0, "baseline_ancestry", f"head={head}")
    self_test_errors = semantic_self_tests()
    audit.hard(not self_test_errors, "semantic_self_test", self_test_errors)
    project = args.projected_technical_script is not None or args.projected_images_script is not None
    audit.hard(
        (args.projected_technical_script is None) == (args.projected_images_script is None),
        "projection_pair",
        "technical and images scripts must be supplied together",
    )

    sitemap_before, rows = sitemap_rows(root)
    audit.hard(sha256(sitemap_before) == SITEMAP_SHA256, "sitemap_exact_bytes", sha256(sitemap_before))
    urls = [row.url for row in rows]
    paths = [row.path for row in rows]
    audit.hard(len(rows) == PAGE_COUNT, "sitemap_count", len(rows))
    audit.hard(len(set(urls)) == PAGE_COUNT, "sitemap_unique", len(set(urls)))
    audit.hard(len(set(paths)) == PAGE_COUNT, "sitemap_path_unique", len(set(paths)))
    missing_paths = [row.relative for row in rows if not row.path.is_file()]
    audit.hard(not missing_paths, "sitemap_missing_page", missing_paths[:8])
    expected_urls = [page_url(root, row.path) for row in rows]
    audit.hard(urls == expected_urls, "sitemap_filesystem_order", "URL/path/order mismatch")
    detail_rows = [row for row in rows if detail_relative(row.relative)]
    audit.hard(len(detail_rows) == DETAIL_COUNT, "detail_count", len(detail_rows))
    category_counts = Counter(row.relative.split("/")[1] for row in detail_rows)
    audit.hard(
        len(category_counts) == CATEGORY_COUNT and set(category_counts.values()) == {CATEGORY_PAGE_COUNT},
        "detail_category_counts",
        dict(category_counts),
    )
    detail_lastmods = Counter(row.lastmod for row in detail_rows)
    audit.hard(detail_lastmods == {"2026-08-18": DETAIL_COUNT}, "detail_lastmod", dict(detail_lastmods))

    target_set = set(paths)
    pre_target, pre_assets, pre_external = freeze_maps(root, target_set)
    audit.hard(len(pre_target) == PAGE_COUNT, "freeze_target_count", len(pre_target))
    source = {row.path: pre_target[row.relative] for row in rows if row.relative in pre_target}
    audit.hard(len(source) == PAGE_COUNT, "source_document_count", len(source))
    actual_trailing_whitespace = trailing_whitespace_inventory(root, source)
    audit.hard(
        project or actual_trailing_whitespace["occurrences"] == 0,
        "actual_physical_trailing_whitespace",
        actual_trailing_whitespace,
    )
    input_manifest_sha = manifest(pre_target)
    asset_manifest_sha = manifest(pre_assets)
    audit.hard(
        input_manifest_sha in {
            BASELINE_TARGET_MANIFEST_SHA256,
            PRECLEANUP_TARGET_MANIFEST_SHA256,
            PROJECTED_TARGET_MANIFEST_SHA256,
        },
        "input_target_manifest",
        input_manifest_sha,
    )
    audit.hard(asset_manifest_sha == ASSET_MANIFEST_SHA256, "asset_baseline_manifest", asset_manifest_sha)

    phase3_expected = {
        "scripts/smallclass_phase3_fact_copy_audit.py": "0bc71175f27bd572ab65e1f4886346d01203b98fc1f0ec33d216cbec48ac3de8",
        "scripts/smallclass_phase3_release_audit.py": "e3bcb5c849dd1d34ee47badb24cc7556ba93a362786afd6ebc23c2c1170daa7a",
    }
    phase3_actual = {
        relative: sha256((root / relative).read_bytes()) for relative in phase3_expected
    }
    audit.hard(phase3_actual == phase3_expected, "phase3_auditor_freeze", phase3_actual)

    script_hashes_before: dict[str, str] = {}
    technical_metrics: dict[str, Any] = {"mode": "not projected"}
    images_metrics: dict[str, Any] = {"mode": "not projected"}
    pipeline_second_changes = 0
    final = dict(source)
    if project and args.projected_technical_script and args.projected_images_script:
        expected_technical = (root / TECHNICAL_SCRIPT).resolve()
        expected_images = (root / IMAGES_SCRIPT).resolve()
        technical_path = (
            args.projected_technical_script
            if args.projected_technical_script.is_absolute()
            else root / args.projected_technical_script
        ).resolve()
        images_path = (
            args.projected_images_script
            if args.projected_images_script.is_absolute()
            else root / args.projected_images_script
        ).resolve()
        audit.hard(technical_path == expected_technical, "technical_script_path", technical_path)
        audit.hard(images_path == expected_images, "images_script_path", images_path)
        script_hashes_before = {
            TECHNICAL_SCRIPT: sha256(technical_path.read_bytes()),
            IMAGES_SCRIPT: sha256(images_path.read_bytes()),
        }
        approved_script_hashes = {
            TECHNICAL_SCRIPT: APPROVED_TECHNICAL_SHA256,
            IMAGES_SCRIPT: APPROVED_IMAGES_SHA256,
        }
        audit.hard(
            script_hashes_before == approved_script_hashes,
            "projection_script_approved_sha256",
            script_hashes_before,
        )
        technical = import_projection("smallclass_phase4_release_technical", technical_path)
        images = import_projection("smallclass_phase4_release_images", images_path)
        if require_projection_api(
            technical,
            ("replace_hub_contract", "transform_index_hrefs"),
            audit,
            "technical",
        ) and require_projection_api(
            images,
            ("transform_document", "validate_global_projection", "MutationStats"),
            audit,
            "images",
        ):
            technical_after, technical_metrics = project_technical(root, rows, source, technical, audit)
            final, images_metrics = project_images(root, rows, technical_after, images, audit)
            second_technical, second_technical_metrics = project_technical(
                root, rows, final, technical, audit, local_second_pass=False
            )
            second_final, second_images_metrics = project_images(
                root, rows, second_technical, images, audit, local_second_pass=False
            )
            pipeline_second_changes = sum(second_final[path] != final[path] for path in paths)
            audit.hard(second_technical_metrics["state"] == "applied", "pipeline_second_technical", second_technical_metrics)
            audit.hard(pipeline_second_changes == 0, "pipeline_second_idempotency", pipeline_second_changes)
            images_metrics["pipeline_second"] = second_images_metrics
    audit.hard(set(final) == target_set, "projection_scope_exact", f"actual={len(final)} expected={len(target_set)}")
    changed_pages = sum(source[path] != final[path] for path in paths)
    final_manifest_sha = manifest({row.relative: final[row.path] for row in rows})
    audit.hard(
        final_manifest_sha == PROJECTED_TARGET_MANIFEST_SHA256 if project else final_manifest_sha in {
            PRECLEANUP_TARGET_MANIFEST_SHA256,
            PROJECTED_TARGET_MANIFEST_SHA256,
        },
        "projected_target_manifest",
        final_manifest_sha,
    )
    final_trailing_whitespace = trailing_whitespace_inventory(root, final)
    if project:
        audit.hard(
            final_trailing_whitespace["occurrences"] == 0,
            "projected_physical_trailing_whitespace",
            final_trailing_whitespace,
        )

    content_metrics, browser_cases = audit_documents(root, rows, source, final, audit)
    contract = browser_contract(browser_cases)
    audit.hard(contract["case_count"] == BROWSER_CASE_COUNT, "browser_contract_cases", contract)
    audit.hard(contract["test_count"] == BROWSER_TEST_COUNT, "browser_contract_tests", contract)
    browser_result: dict[str, Any] | None = None
    actual_release = not project and input_manifest_sha == PROJECTED_TARGET_MANIFEST_SHA256
    browser_required = args.require_browser or actual_release
    if args.browser_base:
        if changed_pages:
            audit.hard(False, "browser_projection_not_materialized", f"changed_pages={changed_pages}")
        else:
            browser_result = run_browser(args.browser_base, browser_cases, root, args.browser_timeout)
            audit.hard(browser_result.get("tests") == BROWSER_TEST_COUNT, "browser_test_count", browser_result)
            audit.hard(browser_result.get("failures") == 0, "browser_failures", browser_result)
    elif browser_required:
        audit.hard(False, "browser_required", "supply --browser-base for a materialized release")

    post_target, post_assets, post_external = freeze_maps(root, target_set)
    audit.hard(pre_target == post_target, "actual_target_freeze", "target bytes changed during audit")
    audit.hard(pre_assets == post_assets, "asset_bytes_freeze", "asset bytes changed during audit")
    audit.hard(pre_external == post_external, "external_scope_freeze", "external bytes changed during audit")
    audit.hard((root / "sitemap.xml").read_bytes() == sitemap_before, "sitemap_bytes_freeze", "sitemap changed")
    script_hashes_after = {
        relative: sha256((root / relative).read_bytes()) for relative in script_hashes_before
    }
    audit.hard(script_hashes_before == script_hashes_after, "projection_script_freeze", script_hashes_after)

    return {
        "ok": not audit.errors,
        "errors": sum(audit.counts.values()),
        "error_counts": dict(sorted(audit.counts.items())),
        "samples": audit.samples,
        "mode": "sequential-projected" if project else ("actual-release" if actual_release else "actual-baseline"),
        "baseline_ref": BASELINE_REF,
        "git": {"head": head, "branch": branch},
        "root": str(root),
        "summary": {
            "sitemap_urls": len(rows),
            "sitemap_unique": len(set(urls)),
            "detail_pages": len(detail_rows),
            "detail_lastmod": dict(detail_lastmods),
            "changed_pages_in_memory": changed_pages,
            "pipeline_second_changes": pipeline_second_changes,
        },
        "projection": {
            "technical": technical_metrics,
            "images": images_metrics,
            "script_sha256": script_hashes_before,
            "input_manifest_sha256": input_manifest_sha,
            "projected_manifest_sha256": final_manifest_sha,
        },
        "contracts": content_metrics,
        "physical_trailing_whitespace": {
            "actual": actual_trailing_whitespace,
            "final": final_trailing_whitespace,
        },
        "browser_contract": contract,
        "browser_required": browser_required,
        "browser": browser_result,
        "freeze": {
            "target_pre_sha256": manifest(pre_target),
            "target_post_sha256": manifest(post_target),
            "assets_pre_sha256": manifest(pre_assets),
            "assets_post_sha256": manifest(post_assets),
            "external_pre_sha256": manifest(pre_external),
            "external_post_sha256": manifest(post_external),
            "sitemap_sha256": sha256(sitemap_before),
            "auditor_sha256": sha256((root / AUDIT_SCRIPT).read_bytes()),
        },
    }


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--projected-technical-script", type=Path)
    parser.add_argument("--projected-images-script", type=Path)
    parser.add_argument("--browser-base")
    parser.add_argument("--require-browser", action="store_true")
    parser.add_argument("--browser-timeout", type=int, default=420)
    parser.add_argument("--json", action="store_true", help="Retained for CLI compatibility; JSON is always emitted")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        report = audit_site(args)
    except Exception as exc:
        report = {
            "ok": False,
            "errors": 1,
            "error_counts": {"audit_exception": 1},
            "samples": {"audit_exception": [f"{type(exc).__name__}: {exc}"]},
        }
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
