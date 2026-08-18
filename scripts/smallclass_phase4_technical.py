#!/usr/bin/env python3
"""Phase 4 technical transformer for xn--9l4b2jy4h4wan0chw4b.com.

The default mode is a read-only, full-site dry run.  The transformer has two
strictly bounded responsibilities:

1. Repair the elementary-math directory search DOM contract so the existing
   shared JavaScript can filter all 371 locality links and reset the result.
2. Replace internal navigation hrefs ending in ``index.html`` with their
   canonical trailing-slash spelling without changing their destinations.

No canonical, sitemap, JSON-LD, script, stylesheet, image, or public URL is
changed.  ``--apply`` writes only planned HTML files, through same-directory
temporary files and ``os.replace``.  A second in-memory pass must always be a
no-op before any write is allowed.
"""

from __future__ import annotations

import argparse
import hashlib
import html as html_module
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import uuid
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import Iterable, Iterator, Sequence
from urllib.parse import quote, unquote, urljoin, urlsplit, urlunsplit


sys.dont_write_bytecode = True

BASELINE_REF = "c6bb0e7d43bea43624adbd63db6b564eacf3f718"
BASE_URL = "https://xn--9l4b2jy4h4wan0chw4b.com"
BASE_HOST = "xn--9l4b2jy4h4wan0chw4b.com"
SCRIPT_RELATIVE_PATH = "scripts/smallclass_phase4_technical.py"
HUB_RELATIVE_PATH = "전국학원/초등수학학원/index.html"

EXPECTED_PAGE_COUNT = 3_378
EXPECTED_SITEMAP_COUNT = 3_378
EXPECTED_INDEX_HREF_COUNT = 53_954
EXPECTED_INDEX_HREF_FILE_COUNT = 3_378
EXPECTED_HUB_REGION_COUNT = 13
EXPECTED_HUB_CITY_COUNT = 76
EXPECTED_HUB_LINK_COUNT = 371
EXPECTED_QUERY = "명일동"

EXCLUDED_DIR_NAMES = {".git", "tmp", "__pycache__"}
TEMP_NAME_PREFIX = ".smallclass-phase4-"
TRANSACTION_JOURNAL_NAME = f"{TEMP_NAME_PREFIX}transaction.json"
TRANSACTION_LOCK_NAME = f"{TEMP_NAME_PREFIX}transaction.lock"
TRANSACTION_VERSION = 1
SCHEMA_BEARING_ATTRIBUTES = {
    "itemprop",
    "itemscope",
    "itemtype",
    "property",
    "typeof",
    "resource",
    "vocab",
    "prefix",
}

PROTECTED_BLOCK_RE = re.compile(
    r"<!--.*?-->|<script\b[^>]*>.*?</script\s*>|<style\b[^>]*>.*?</style\s*>",
    re.IGNORECASE | re.DOTALL,
)
START_TAG_RE = re.compile(
    r"<(?P<name>[A-Za-z][A-Za-z0-9:_-]*)(?P<attrs>(?:[^\"'>]|\"[^\"]*\"|'[^']*')*)>",
    re.DOTALL,
)
ATTR_RE = re.compile(
    r"(?P<name>[^\s=/>]+)(?:\s*=\s*(?:\"(?P<double>[^\"]*)\"|'(?P<single>[^']*)'|(?P<bare>[^\s>]+)))?",
    re.DOTALL,
)
CANONICAL_TAG_RE = re.compile(
    r"<link\b(?=[^>]*\brel\s*=\s*[\"'][^\"']*\bcanonical\b[^\"']*[\"'])[^>]*>",
    re.IGNORECASE | re.DOTALL,
)
JSON_LD_RE = re.compile(
    r"<script\b(?=[^>]*\btype\s*=\s*[\"']application/ld\+json[\"'])[^>]*>.*?</script\s*>",
    re.IGNORECASE | re.DOTALL,
)
TITLE_RE = re.compile(r"<title\b[^>]*>.*?</title\s*>", re.IGNORECASE | re.DOTALL)
H1_RE = re.compile(r"<h1\b[^>]*>.*?</h1\s*>", re.IGNORECASE | re.DOTALL)
SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
UUID4_HEX_RE = re.compile(r"[0-9a-f]{32}\Z")

HUB_SECTION_OLD = '<section class="section academy-directory-section">'
HUB_SECTION_NEW = '<section class="section academy-directory-section" data-academy-directory>'
HUB_REGION_DIRECTORY_OLD = '<div class="academy-region-directory" data-academy-directory>'
HUB_REGION_DIRECTORY_NEW = '<div class="academy-region-directory">'
HUB_SEARCH_OLD = (
    '<label class="academy-search-box"><span>지역 검색</span>'
    '<input type="search" placeholder="예: 명일동, 강동구" data-academy-search></label>'
)
HUB_SEARCH_NEW = (
    '<div class="academy-directory-search">'
    '<label for="academy-local-search-elementary-math">동네 이름으로 찾기</label>'
    '<div class="academy-search-field"><span aria-hidden="true">⌕</span>'
    '<input id="academy-local-search-elementary-math" type="search" inputmode="search" '
    'autocomplete="off" placeholder="예: 명일동, 불당동" data-academy-search>'
    '<button type="button" data-academy-search-clear hidden>초기화</button></div></div>'
)
HUB_DESCRIPTION_OLD = "동네명이나 시군구를 검색한 뒤 지역 묶음을 열어 원하는 페이지로 이동하세요."
HUB_DESCRIPTION_NEW = "동네 이름을 검색한 뒤 지역 묶음을 열어 원하는 페이지로 이동하세요."
HUB_STATUS_OLD = (
    '<p class="academy-search-result" data-academy-result aria-live="polite">'
)
HUB_STATUS_NEW = (
    '<p class="academy-search-result academy-search-status" data-academy-result '
    'data-academy-search-status aria-live="polite">'
)
HUB_FIRST_REGION_OLD = (
    '<details class="academy-region-group academy-region-accordion" data-academy-region="서울">'
)
HUB_FIRST_REGION_NEW = (
    '<details class="academy-region-group academy-region-accordion" data-academy-region="서울" open>'
)

HUB_REPLACEMENTS: tuple[tuple[str, str, str], ...] = (
    ("directory scope", HUB_SECTION_OLD, HUB_SECTION_NEW),
    ("legacy nested directory marker", HUB_REGION_DIRECTORY_OLD, HUB_REGION_DIRECTORY_NEW),
    ("search controls", HUB_SEARCH_OLD, HUB_SEARCH_NEW),
    ("search instruction", HUB_DESCRIPTION_OLD, HUB_DESCRIPTION_NEW),
    ("search status hook", HUB_STATUS_OLD, HUB_STATUS_NEW),
    ("initial Seoul region", HUB_FIRST_REGION_OLD, HUB_FIRST_REGION_NEW),
)

REQUIRED_SHARED_JS_SNIPPETS = (
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


class ContractError(RuntimeError):
    """Raised when an input or output violates the release contract."""


@dataclass(frozen=True)
class AttributeSpan:
    name: str
    value: str | None
    value_start: int
    value_end: int


@dataclass(frozen=True)
class HrefChange:
    tag: str
    old: str
    new: str


@dataclass(frozen=True)
class PlannedDocument:
    path: Path
    relative_path: str
    before: bytes
    after: bytes
    href_changes: tuple[HrefChange, ...]
    hub_changed: bool


@dataclass
class BuildPlan:
    root: Path
    documents: list[PlannedDocument]
    state: str
    branch: str
    head: str
    target_manifest_before: str
    target_manifest_after: str
    external_manifest: str
    sitemap_sha256: str
    sitemap_order_sha256: str
    canonical_set_sha256: str
    hub_before: dict[str, object]
    hub_after: dict[str, object]
    stats: dict[str, object] = field(default_factory=dict)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def hash_strings(values: Iterable[str]) -> str:
    digest = hashlib.sha256()
    for value in values:
        encoded = value.encode("utf-8")
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
    return digest.hexdigest()


def clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def normalize_query(value: str) -> str:
    return re.sub(r"\s+", "", value).lower()


def relative_posix(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def run_git(root: Path, *args: str, check: bool = True) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=root,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if check and completed.returncode != 0:
        raise ContractError(
            f"git {' '.join(args)} failed ({completed.returncode}): {completed.stderr.strip()}"
        )
    return completed.stdout.strip()


def verify_git_context(root: Path) -> tuple[str, str]:
    head = run_git(root, "rev-parse", "HEAD")
    branch = run_git(root, "branch", "--show-current")
    ancestor = subprocess.run(
        ["git", "merge-base", "--is-ancestor", BASELINE_REF, head],
        cwd=root,
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if ancestor.returncode != 0:
        raise ContractError(f"HEAD {head} does not descend from baseline {BASELINE_REF}")
    return branch, head


def discover_pages(root: Path) -> list[Path]:
    pages = [
        path
        for path in root.rglob("index.html")
        if not any(part in EXCLUDED_DIR_NAMES for part in path.relative_to(root).parts)
    ]
    pages.sort(key=lambda path: relative_posix(path, root))
    if len(pages) != EXPECTED_PAGE_COUNT:
        raise ContractError(f"page count: expected {EXPECTED_PAGE_COUNT}, got {len(pages)}")
    root_index = root / "index.html"
    if root_index not in pages:
        raise ContractError("root index.html is missing")
    return pages


def iter_unprotected_segments(source: str) -> Iterator[tuple[bool, str]]:
    cursor = 0
    for match in PROTECTED_BLOCK_RE.finditer(source):
        if match.start() > cursor:
            yield False, source[cursor : match.start()]
        yield True, match.group(0)
        cursor = match.end()
    if cursor < len(source):
        yield False, source[cursor:]


def parse_attribute_spans(tag: str) -> list[AttributeSpan]:
    match = START_TAG_RE.fullmatch(tag)
    if not match:
        return []
    raw_attributes = match.group("attrs")
    offset = match.start("attrs")
    spans: list[AttributeSpan] = []
    cursor = 0
    for attr in ATTR_RE.finditer(raw_attributes):
        gap = raw_attributes[cursor : attr.start()]
        if gap.strip() not in {"", "/"}:
            raise ContractError(f"cannot tokenize start-tag attributes near {gap[:80]!r}")
        name = attr.group("name").lower()
        value: str | None = None
        value_start = value_end = -1
        for group_name in ("double", "single", "bare"):
            candidate = attr.group(group_name)
            if candidate is not None:
                value = candidate
                value_start = offset + attr.start(group_name)
                value_end = offset + attr.end(group_name)
                break
        spans.append(
            AttributeSpan(
                name=name,
                value=value,
                value_start=value_start,
                value_end=value_end,
            )
        )
        cursor = attr.end()
    tail = raw_attributes[cursor:]
    if tail.strip() not in {"", "/"}:
        raise ContractError(f"cannot tokenize trailing start-tag attributes near {tail[:80]!r}")
    return spans


def parse_attributes(tag: str) -> dict[str, str]:
    return {
        attribute.name: "" if attribute.value is None else attribute.value
        for attribute in parse_attribute_spans(tag)
    }


def is_canonical_link(tag_name: str, tag: str) -> bool:
    if tag_name.lower() != "link":
        return False
    rel = parse_attributes(tag).get("rel", "")
    return "canonical" in {token.lower() for token in rel.split()}


def is_internal_index_href(value: str) -> bool:
    decoded = html_module.unescape(value)
    if decoded != decoded.strip() or not decoded:
        return False
    parts = urlsplit(decoded)
    if parts.scheme and parts.scheme.lower() not in {"http", "https"}:
        return False
    if parts.hostname:
        try:
            hostname = parts.hostname.encode("idna").decode("ascii").lower()
        except UnicodeError:
            return False
        if hostname != BASE_HOST:
            return False
    elif parts.netloc:
        return False
    path = parts.path
    return path == "index.html" or path.endswith("/index.html")


def rewrite_index_href(value: str) -> str | None:
    if not is_internal_index_href(value):
        return None
    delimiter_positions = [position for position in (value.find("?"), value.find("#")) if position >= 0]
    path_end = min(delimiter_positions) if delimiter_positions else len(value)
    raw_path = value[:path_end]
    if not raw_path.endswith("index.html"):
        raise ContractError(f"decoded index href cannot be safely sliced: {value!r}")
    new_path = raw_path[: -len("index.html")]
    if not new_path:
        new_path = "./"
    return new_path + value[path_end:]


def normalize_destination(base_url: str, value: str) -> str:
    resolved = urljoin(base_url, html_module.unescape(value))
    parts = urlsplit(resolved)
    hostname = (parts.hostname or "").encode("idna").decode("ascii").lower()
    if hostname != BASE_HOST:
        return resolved
    path = parts.path
    if path == "/index.html":
        path = "/"
    elif path.endswith("/index.html"):
        path = path[: -len("index.html")]
    path = quote(unquote(path), safe="/~._-")
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, parts.query, parts.fragment))


def transform_markup_segment(segment: str) -> tuple[str, list[HrefChange]]:
    changes: list[HrefChange] = []

    def replace_tag(match: re.Match[str]) -> str:
        tag = match.group(0)
        tag_name = match.group("name").lower()
        href_attributes = [
            attribute for attribute in parse_attribute_spans(tag) if attribute.name == "href"
        ]
        if not href_attributes:
            return tag
        if len(href_attributes) != 1:
            raise ContractError(f"tag has {len(href_attributes)} href attributes: {tag[:160]!r}")
        href_attribute = href_attributes[0]
        if href_attribute.value is None:
            return tag
        old = href_attribute.value
        value_start = href_attribute.value_start
        value_end = href_attribute.value_end
        new = rewrite_index_href(old)
        if new is None:
            return tag
        if is_canonical_link(tag_name, tag):
            return tag
        if tag_name not in {"a", "area"}:
            raise ContractError(f"unexpected internal index href on <{tag_name}>: {old!r}")
        schema_attributes = SCHEMA_BEARING_ATTRIBUTES.intersection(parse_attributes(tag))
        if schema_attributes:
            raise ContractError(
                f"refusing schema-bearing <{tag_name}> href rewrite: {sorted(schema_attributes)}"
            )
        changes.append(HrefChange(tag=tag_name, old=old, new=new))
        return tag[:value_start] + new + tag[value_end:]

    return START_TAG_RE.sub(replace_tag, segment), changes


def transform_index_hrefs(source: str) -> tuple[str, tuple[HrefChange, ...]]:
    output: list[str] = []
    changes: list[HrefChange] = []
    for protected, segment in iter_unprotected_segments(source):
        if protected:
            output.append(segment)
            continue
        transformed, segment_changes = transform_markup_segment(segment)
        output.append(transformed)
        changes.extend(segment_changes)
    return "".join(output), tuple(changes)


class IndexHrefAuditParser(HTMLParser):
    """Independent residual audit; deliberately does not reuse HREF_RE."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.found: list[tuple[str, str, bool]] = []
        self.duplicate_href_tags: list[str] = []

    def _inspect(self, tag: str, attributes: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        hrefs = [value or "" for name, value in attributes if name.lower() == "href"]
        if len(hrefs) > 1:
            self.duplicate_href_tags.append(tag)
        rel_values = [value or "" for name, value in attributes if name.lower() == "rel"]
        canonical = tag == "link" and any(
            "canonical" in {token.lower() for token in rel.split()} for rel in rel_values
        )
        for value in hrefs:
            if is_internal_index_href(value):
                self.found.append((tag, value, canonical))

    def handle_starttag(self, tag: str, attributes: list[tuple[str, str | None]]) -> None:
        self._inspect(tag, attributes)

    def handle_startendtag(self, tag: str, attributes: list[tuple[str, str | None]]) -> None:
        self._inspect(tag, attributes)


def scan_internal_index_hrefs(source: str) -> list[tuple[str, str, bool]]:
    parser = IndexHrefAuditParser()
    parser.feed(source)
    parser.close()
    if parser.duplicate_href_tags:
        raise ContractError(
            f"duplicate href attributes detected on tags: {parser.duplicate_href_tags[:3]}"
        )
    return parser.found


def replace_hub_contract(source: str) -> tuple[str, bool, str]:
    old_counts = [source.count(old) for _, old, _ in HUB_REPLACEMENTS]
    new_counts = [source.count(new) for _, _, new in HUB_REPLACEMENTS]
    if old_counts == [1] * len(HUB_REPLACEMENTS) and new_counts == [0] * len(HUB_REPLACEMENTS):
        transformed = source
        for label, old, new in HUB_REPLACEMENTS:
            if transformed.count(old) != 1:
                raise ContractError(f"hub {label} baseline marker cardinality changed")
            transformed = transformed.replace(old, new, 1)
        return transformed, True, "baseline"
    if old_counts == [0] * len(HUB_REPLACEMENTS) and new_counts == [1] * len(HUB_REPLACEMENTS):
        return source, False, "applied"
    details = ", ".join(
        f"{label}=old{old_count}/new{new_count}"
        for (label, _, _), old_count, new_count in zip(
            HUB_REPLACEMENTS, old_counts, new_counts, strict=True
        )
    )
    raise ContractError(f"hub contract is partial or unknown: {details}")


def extract_canonical(source: str, relative_path: str) -> str:
    tags = CANONICAL_TAG_RE.findall(source)
    if len(tags) != 1:
        raise ContractError(f"{relative_path}: expected one canonical tag, got {len(tags)}")
    attributes = parse_attributes(tags[0])
    href = attributes.get("href")
    if not href:
        raise ContractError(f"{relative_path}: canonical href missing")
    if "index.html" in urlsplit(href).path.lower() or not urlsplit(href).path.endswith("/"):
        raise ContractError(f"{relative_path}: non-canonical public URL spelling: {href}")
    if (urlsplit(href).hostname or "").lower() != BASE_HOST:
        raise ContractError(f"{relative_path}: canonical host mismatch: {href}")
    return href


def expected_canonical_for_path(path: Path, root: Path) -> str:
    relative = path.relative_to(root)
    directory_parts = relative.parts[:-1]
    encoded_path = "/".join(quote(part, safe="%~._-") for part in directory_parts)
    return f"{BASE_URL}/{encoded_path}/" if encoded_path else f"{BASE_URL}/"


def assert_stable_page_contract(before: str, after: str, relative_path: str) -> None:
    checks = (
        ("canonical", CANONICAL_TAG_RE),
        ("JSON-LD", JSON_LD_RE),
        ("title", TITLE_RE),
        ("H1", H1_RE),
    )
    for label, pattern in checks:
        before_values = pattern.findall(before)
        after_values = pattern.findall(after)
        if before_values != after_values:
            raise ContractError(f"{relative_path}: {label} changed")


class HubContractParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[dict[str, object]] = []
        self.directory_count = 0
        self.search_count = 0
        self.clear_count = 0
        self.status_count = 0
        self.region_count = 0
        self.city_count = 0
        self.link_grid_count = 0
        self.open_region_count = 0
        self.first_region_name = ""
        self.links: list[dict[str, str]] = []
        self._current_link: dict[str, str] | None = None
        self._current_city: dict[str, str] | None = None
        self._capture_city_heading = False
        self._directory_depth = 0
        self._link_grid_depth = 0

    @staticmethod
    def _attrs(attributes: list[tuple[str, str | None]]) -> dict[str, str]:
        return {name.lower(): "" if value is None else value for name, value in attributes}

    def handle_starttag(self, tag: str, attributes: list[tuple[str, str | None]]) -> None:
        attrs = self._attrs(attributes)
        tag = tag.lower()
        starts_directory = "data-academy-directory" in attrs
        if starts_directory:
            self.directory_count += 1
        inside = self._directory_depth > 0 or starts_directory
        entry: dict[str, object] = {
            "tag": tag,
            "starts_directory": starts_directory,
            "starts_city": False,
            "starts_link_grid": False,
            "starts_link": False,
            "starts_city_heading": False,
        }
        if starts_directory:
            self._directory_depth += 1
        if inside:
            if "data-academy-search" in attrs:
                self.search_count += 1
            if "data-academy-search-clear" in attrs:
                self.clear_count += 1
            if "data-academy-search-status" in attrs:
                self.status_count += 1
            if "data-academy-region" in attrs:
                self.region_count += 1
                if not self.first_region_name:
                    self.first_region_name = attrs["data-academy-region"]
                if "open" in attrs:
                    self.open_region_count += 1
            classes = set(attrs.get("class", "").split())
            if "academy-link-grid" in classes:
                self.link_grid_count += 1
                self._link_grid_depth += 1
                entry["starts_link_grid"] = True
            if "academy-city-group" in classes:
                self.city_count += 1
                self._current_city = {"name": ""}
                entry["starts_city"] = True
            if tag in {"h2", "h3", "h4"} and "academy-city-title" in classes:
                self._capture_city_heading = True
                entry["starts_city_heading"] = True
            if tag == "a" and self._current_city is not None and self._link_grid_depth > 0:
                self._current_link = {
                    "href": attrs.get("href", ""),
                    "text": "",
                    "city": self._current_city.get("name", ""),
                }
                entry["starts_link"] = True
        self.stack.append(entry)

    def handle_startendtag(self, tag: str, attributes: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attributes)
        self.handle_endtag(tag)

    def handle_data(self, data: str) -> None:
        if self._capture_city_heading and self._current_city is not None:
            self._current_city["name"] += data
        if self._current_link is not None:
            self._current_link["text"] += data

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if not self.stack:
            return
        index = len(self.stack) - 1
        while index >= 0 and self.stack[index]["tag"] != tag:
            index -= 1
        if index < 0:
            return
        closing = self.stack[index:]
        self.stack = self.stack[:index]
        for entry in reversed(closing):
            if entry["starts_link"] and self._current_link is not None:
                self._current_link["text"] = clean_text(self._current_link["text"])
                if self._current_city is not None:
                    self._current_link["city"] = clean_text(self._current_city.get("name", ""))
                self.links.append(self._current_link)
                self._current_link = None
            if entry["starts_city_heading"]:
                self._capture_city_heading = False
                if self._current_city is not None:
                    self._current_city["name"] = clean_text(self._current_city.get("name", ""))
            if entry["starts_city"]:
                self._current_city = None
            if entry["starts_link_grid"]:
                self._link_grid_depth -= 1
            if entry["starts_directory"]:
                self._directory_depth -= 1


def parse_hub_contract(source: str, canonical: str) -> dict[str, object]:
    parser = HubContractParser()
    parser.feed(source)
    parser.close()
    hrefs = [link["href"] for link in parser.links]
    texts = [link["text"] for link in parser.links]
    destinations = [normalize_destination(canonical, href) for href in hrefs]
    query = normalize_query(EXPECTED_QUERY)
    query_matches = sum(query in normalize_query(text) for text in texts)
    query_status = f"‘{EXPECTED_QUERY}’ 검색 결과 {query_matches}개의 동네 페이지를 찾았습니다."
    return {
        "directory_count": parser.directory_count,
        "search_count": parser.search_count,
        "clear_count": parser.clear_count,
        "status_count": parser.status_count,
        "region_count": parser.region_count,
        "city_count": parser.city_count,
        "link_grid_count": parser.link_grid_count,
        "link_count": len(parser.links),
        "unique_href_count": len(set(hrefs)),
        "unique_destination_count": len(set(destinations)),
        "open_region_count": parser.open_region_count,
        "first_region_name": parser.first_region_name,
        "query": EXPECTED_QUERY,
        "query_match_count": query_matches,
        "query_visible_count": query_matches,
        "query_status": query_status,
        "reset_count": len(parser.links),
        "clear_visible_count": len(parser.links),
        "link_text_sha256": hash_strings(texts),
        "destination_sha256": hash_strings(destinations),
    }


def verify_projected_hub(metrics: dict[str, object]) -> None:
    expected = {
        "directory_count": 1,
        "search_count": 1,
        "clear_count": 1,
        "status_count": 1,
        "region_count": EXPECTED_HUB_REGION_COUNT,
        "city_count": EXPECTED_HUB_CITY_COUNT,
        "link_grid_count": EXPECTED_HUB_CITY_COUNT,
        "link_count": EXPECTED_HUB_LINK_COUNT,
        "unique_href_count": EXPECTED_HUB_LINK_COUNT,
        "unique_destination_count": EXPECTED_HUB_LINK_COUNT,
        "open_region_count": 1,
        "first_region_name": "서울",
        "query_match_count": 1,
        "query_visible_count": 1,
        "query_status": f"‘{EXPECTED_QUERY}’ 검색 결과 1개의 동네 페이지를 찾았습니다.",
        "reset_count": EXPECTED_HUB_LINK_COUNT,
        "clear_visible_count": EXPECTED_HUB_LINK_COUNT,
    }
    for key, expected_value in expected.items():
        actual = metrics.get(key)
        if actual != expected_value:
            raise ContractError(f"hub {key}: expected {expected_value!r}, got {actual!r}")


def verify_shared_js(root: Path) -> str:
    path = root / "assets" / "script.js"
    source = path.read_text(encoding="utf-8")
    missing = [snippet for snippet in REQUIRED_SHARED_JS_SNIPPETS if snippet not in source]
    if missing:
        raise ContractError(f"shared search JS contract missing: {missing}")
    return sha256_bytes(path.read_bytes())


def sitemap_contract(root: Path) -> tuple[bytes, list[str], str]:
    path = root / "sitemap.xml"
    data = path.read_bytes()
    try:
        xml_root = ET.fromstring(data)
    except ET.ParseError as error:
        raise ContractError(f"sitemap parse error: {error}") from error
    namespace = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    url_nodes = xml_root.findall("sm:url", namespace)
    if len(url_nodes) != EXPECTED_SITEMAP_COUNT:
        raise ContractError(
            f"sitemap URL count: expected {EXPECTED_SITEMAP_COUNT}, got {len(url_nodes)}"
        )
    locations: list[str] = []
    for index, node in enumerate(url_nodes):
        loc_nodes = node.findall("sm:loc", namespace)
        if len(loc_nodes) != 1 or not loc_nodes[0].text:
            raise ContractError(f"sitemap url[{index}] must contain exactly one loc")
        locations.append(loc_nodes[0].text.strip())
    if len(set(locations)) != len(locations):
        raise ContractError("sitemap contains duplicate URLs")
    return data, locations, hash_strings(locations)


def manifest_for_documents(
    documents: Sequence[tuple[str, bytes]] | Sequence[PlannedDocument], *, after: bool = False
) -> str:
    digest = hashlib.sha256()
    if documents and isinstance(documents[0], PlannedDocument):
        pairs = [
            (document.relative_path, document.after if after else document.before)
            for document in documents  # type: ignore[union-attr]
        ]
    else:
        pairs = list(documents)  # type: ignore[arg-type]
    for relative_path, data in sorted(pairs, key=lambda item: item[0]):
        encoded_path = relative_path.encode("utf-8")
        digest.update(len(encoded_path).to_bytes(8, "big"))
        digest.update(encoded_path)
        digest.update(len(data).to_bytes(8, "big"))
        digest.update(hashlib.sha256(data).digest())
    return digest.hexdigest()


def should_ignore_path(path: Path, root: Path) -> bool:
    relative = path.relative_to(root)
    if any(part in EXCLUDED_DIR_NAMES for part in relative.parts):
        return True
    return path.name.startswith(TEMP_NAME_PREFIX)


def external_manifest(root: Path, authorized_paths: set[Path]) -> str:
    entries: list[tuple[str, bytes]] = []
    for path in root.rglob("*"):
        if not path.is_file() or should_ignore_path(path, root):
            continue
        resolved = path.resolve()
        if resolved in authorized_paths:
            continue
        entries.append((relative_posix(path, root), path.read_bytes()))
    return manifest_for_documents(entries)


def run_self_tests() -> None:
    synthetic = (
        '<a href="index.html">root</a>'
        '<a href="../index.html#section">up</a>'
        '<a href=child/index.html>unquoted</a>'
        '<a data-href="shadow/index.html" href="real/index.html">exact-name</a>'
        '<a title=" href=\'nested/index.html\'" data-href="shadow-only/index.html">attribute-text</a>'
        '<a href="case/INDEX.HTML">case-sensitive</a>'
        f'<area href="{BASE_URL}/guide/index.html?q=1">'
        '<a href="https://example.com/index.html">external</a>'
        f'<link rel="canonical" href="{BASE_URL}/index.html">'
        '<script>const sample = \'<a href="index.html">x</a>\';</script>'
    )
    transformed, changes = transform_index_hrefs(synthetic)
    expected_fragments = (
        'href="./"',
        'href="../#section"',
        'href=child/',
        'data-href="shadow/index.html" href="real/"',
        f'href="{BASE_URL}/guide/?q=1"',
    )
    if len(changes) != 5 or any(fragment not in transformed for fragment in expected_fragments):
        raise ContractError("synthetic href rewrite self-test failed")
    if 'https://example.com/index.html' not in transformed:
        raise ContractError("synthetic external href was modified")
    if 'href="case/INDEX.HTML"' not in transformed:
        raise ContractError("synthetic case-sensitive path was modified")
    if 'title=" href=\'nested/index.html\'" data-href="shadow-only/index.html"' not in transformed:
        raise ContractError("synthetic href-like attribute text was modified")
    if f'<link rel="canonical" href="{BASE_URL}/index.html">' not in transformed:
        raise ContractError("synthetic canonical was modified")
    if "const sample = '<a href=\"index.html\">x</a>';" not in transformed:
        raise ContractError("synthetic script text was modified")
    second, second_changes = transform_index_hrefs(transformed)
    if second != transformed or second_changes:
        raise ContractError("synthetic href rewrite is not idempotent")
    for change in changes:
        if normalize_destination(f"{BASE_URL}/a/b/", change.old) != normalize_destination(
            f"{BASE_URL}/a/b/", change.new
        ):
            raise ContractError("synthetic href destination changed")


def build_plan(root: Path) -> BuildPlan:
    run_self_tests()
    branch, head = verify_git_context(root)
    pages = discover_pages(root)
    authorized_paths = {path.resolve() for path in pages}
    external_before = external_manifest(root, authorized_paths)
    sitemap_before, sitemap_locations, sitemap_order_hash = sitemap_contract(root)
    sitemap_hash = sha256_bytes(sitemap_before)
    shared_js_hash = verify_shared_js(root)

    documents: list[PlannedDocument] = []
    canonical_before: list[str] = []
    canonical_after: list[str] = []
    total_href_changes = 0
    href_changed_files = 0
    semantic_checks = 0
    semantic_routes: list[str] = []
    hub_state = ""
    hub_before: dict[str, object] = {}
    hub_after: dict[str, object] = {}

    hub_path = (root / Path(HUB_RELATIVE_PATH)).resolve()
    if hub_path not in authorized_paths:
        raise ContractError(f"hub path not found: {HUB_RELATIVE_PATH}")

    for path in pages:
        relative_path = relative_posix(path, root)
        before_bytes = path.read_bytes()
        try:
            before = before_bytes.decode("utf-8")
        except UnicodeDecodeError as error:
            raise ContractError(f"{relative_path}: not valid UTF-8: {error}") from error
        canonical = extract_canonical(before, relative_path)
        expected_canonical = expected_canonical_for_path(path, root)
        if canonical != expected_canonical:
            raise ContractError(
                f"{relative_path}: canonical/filesystem mismatch: "
                f"expected {expected_canonical}, got {canonical}"
            )
        canonical_before.append(canonical)

        after_hub = before
        hub_changed = False
        if path.resolve() == hub_path:
            hub_before = parse_hub_contract(before, canonical)
            after_hub, hub_changed, hub_state = replace_hub_contract(before)

        after, href_changes = transform_index_hrefs(after_hub)
        after_bytes = after.encode("utf-8")
        if href_changes:
            href_changed_files += 1
        total_href_changes += len(href_changes)
        for change in href_changes:
            before_destination = normalize_destination(canonical, change.old)
            after_destination = normalize_destination(canonical, change.new)
            if before_destination != after_destination:
                raise ContractError(
                    f"{relative_path}: href destination changed: {change.old!r} -> {change.new!r}"
                )
            semantic_checks += 1
            destination_parts = urlsplit(after_destination)
            semantic_routes.append(
                urlunsplit(
                    (
                        destination_parts.scheme,
                        destination_parts.netloc,
                        destination_parts.path,
                        "",
                        "",
                    )
                )
            )

        residual = scan_internal_index_hrefs(after)
        residual_navigation = [item for item in residual if not item[2]]
        if residual_navigation:
            raise ContractError(
                f"{relative_path}: residual internal index href: {residual_navigation[0]!r}"
            )

        assert_stable_page_contract(before, after, relative_path)
        after_canonical = extract_canonical(after, relative_path)
        if after_canonical != canonical:
            raise ContractError(f"{relative_path}: canonical value changed")
        canonical_after.append(after_canonical)

        if path.resolve() == hub_path:
            hub_after = parse_hub_contract(after, after_canonical)
            verify_projected_hub(hub_after)
            if hub_before.get("link_text_sha256") != hub_after.get("link_text_sha256"):
                raise ContractError("hub locality link text/order changed")
            if hub_before.get("destination_sha256") != hub_after.get("destination_sha256"):
                raise ContractError("hub locality link destinations changed")

        if path.resolve() == hub_path:
            second_hub, second_hub_changed, second_hub_state = replace_hub_contract(after)
        else:
            second_hub, second_hub_changed, second_hub_state = after, False, "applied"
        second, second_changes = transform_index_hrefs(second_hub)
        if second != after or second_changes or second_hub_changed or second_hub_state != "applied":
            raise ContractError(f"{relative_path}: second pass is not a no-op")

        documents.append(
            PlannedDocument(
                path=path,
                relative_path=relative_path,
                before=before_bytes,
                after=after_bytes,
                href_changes=href_changes,
                hub_changed=hub_changed,
            )
        )

    if len(set(canonical_before)) != EXPECTED_PAGE_COUNT:
        raise ContractError("canonical URL set is not unique")
    if canonical_before != canonical_after:
        raise ContractError("per-file canonical sequence changed")
    if set(canonical_before) != set(sitemap_locations):
        missing = sorted(set(canonical_before) - set(sitemap_locations))[:3]
        extra = sorted(set(sitemap_locations) - set(canonical_before))[:3]
        raise ContractError(f"canonical/sitemap parity failed: missing={missing}, extra={extra}")
    unknown_routes = sorted(set(semantic_routes) - set(canonical_before))
    if unknown_routes:
        raise ContractError(f"internal index href points outside canonical set: {unknown_routes[:3]}")

    if total_href_changes == EXPECTED_INDEX_HREF_COUNT and href_changed_files == EXPECTED_INDEX_HREF_FILE_COUNT:
        if hub_state != "baseline" or not any(document.hub_changed for document in documents):
            raise ContractError("href baseline state and hub baseline state disagree")
        state = "baseline"
    elif total_href_changes == 0 and href_changed_files == 0:
        if hub_state != "applied" or any(document.hub_changed for document in documents):
            raise ContractError("href applied state and hub applied state disagree")
        state = "applied"
    else:
        raise ContractError(
            "partial href state: "
            f"expected {EXPECTED_INDEX_HREF_COUNT}/{EXPECTED_INDEX_HREF_FILE_COUNT} or 0/0, "
            f"got {total_href_changes}/{href_changed_files}"
        )

    changed_documents = [document for document in documents if document.before != document.after]
    expected_changed = EXPECTED_PAGE_COUNT if state == "baseline" else 0
    if len(changed_documents) != expected_changed:
        raise ContractError(
            f"changed HTML count: expected {expected_changed}, got {len(changed_documents)}"
        )

    if (root / "sitemap.xml").read_bytes() != sitemap_before:
        raise ContractError("sitemap changed during dry-run")
    external_after = external_manifest(root, authorized_paths)
    if external_after != external_before:
        raise ContractError("external repository manifest changed during dry-run")
    actual_target_after = manifest_for_documents(
        [(relative_posix(path, root), path.read_bytes()) for path in pages]
    )
    target_before = manifest_for_documents(documents, after=False)
    if actual_target_after != target_before:
        raise ContractError("target HTML changed during dry-run")

    plan = BuildPlan(
        root=root,
        documents=documents,
        state=state,
        branch=branch,
        head=head,
        target_manifest_before=target_before,
        target_manifest_after=manifest_for_documents(documents, after=True),
        external_manifest=external_before,
        sitemap_sha256=sitemap_hash,
        sitemap_order_sha256=sitemap_order_hash,
        canonical_set_sha256=hash_strings(sorted(canonical_before)),
        hub_before=hub_before,
        hub_after=hub_after,
        stats={
            "page_count": len(documents),
            "changed_html_count": len(changed_documents),
            "href_replacement_count": total_href_changes,
            "href_changed_file_count": href_changed_files,
            "href_semantic_checks": semantic_checks,
            "residual_internal_index_hrefs": 0,
            "canonical_count": len(canonical_before),
            "sitemap_count": len(sitemap_locations),
            "shared_js_sha256": shared_js_hash,
            "hub_changed": any(document.hub_changed for document in documents),
            "second_pass_changes": 0,
        },
    )
    return plan


class RepositoryLock:
    """Non-blocking process lock used only by mutating/recovery mode."""

    def __init__(self, root: Path) -> None:
        self.path = root / TRANSACTION_LOCK_NAME
        self.stream: object | None = None

    def __enter__(self) -> "RepositoryLock":
        stream = self.path.open("a+b")
        if self.path.stat().st_size == 0:
            stream.write(b"0")
            stream.flush()
            os.fsync(stream.fileno())
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:  # pragma: no cover - production host is Windows
                import fcntl

                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            stream.close()
            raise ContractError("another Phase 4 apply/recovery process holds the lock") from error
        self.stream = stream
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        stream = self.stream
        if stream is None:
            return
        try:
            stream.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:  # pragma: no cover - production host is Windows
                import fcntl

                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        finally:
            stream.close()
            self.stream = None
            try:
                self.path.unlink(missing_ok=True)
            except OSError:
                pass


def fsync_directory(path: Path) -> None:
    """Best-effort directory metadata flush (unsupported on some Windows filesystems)."""

    try:
        descriptor = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)


def write_named_file(path: Path, data: bytes, mode: int) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def write_transaction_journal(root: Path, payload: dict[str, object]) -> None:
    journal = root / TRANSACTION_JOURNAL_NAME
    token = str(payload.get("token", "unknown"))
    data = (json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f"{TEMP_NAME_PREFIX}{token}-journal-", suffix=".tmp", dir=root
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, journal)
        fsync_directory(root)
    finally:
        temporary.unlink(missing_ok=True)


def load_transaction_journal(root: Path) -> dict[str, object] | None:
    journal = root / TRANSACTION_JOURNAL_NAME
    if not journal.exists():
        return None
    try:
        payload = json.loads(journal.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ContractError(f"transaction journal is unreadable: {error}") from error
    if payload.get("version") != TRANSACTION_VERSION:
        raise ContractError(f"unsupported transaction journal version: {payload.get('version')!r}")
    if payload.get("phase") not in {"preparing", "prepared", "committed"}:
        raise ContractError(f"invalid transaction journal phase: {payload.get('phase')!r}")
    if not isinstance(payload.get("entries"), list):
        raise ContractError("transaction journal entries are missing")
    validate_transaction_journal(root, payload)
    return payload


def resolve_transaction_path(root: Path, value: object) -> Path:
    if not isinstance(value, str) or not value:
        raise ContractError(f"invalid transaction path: {value!r}")
    path = (root / Path(value)).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ContractError(f"transaction path escapes repository: {value!r}")
    return path


def validate_transaction_journal(root: Path, payload: dict[str, object]) -> None:
    """Validate the complete journal before recovery is allowed to mutate anything."""

    token = payload.get("token")
    if not isinstance(token, str) or not UUID4_HEX_RE.fullmatch(token):
        raise ContractError("transaction journal token is not a UUID4 hex value")
    try:
        parsed_token = uuid.UUID(hex=token)
    except ValueError as error:
        raise ContractError("transaction journal token is invalid") from error
    if parsed_token.version != 4 or parsed_token.hex != token:
        raise ContractError("transaction journal token is not canonical UUID4 hex")
    if payload.get("baseline_ref") != BASELINE_REF:
        raise ContractError("transaction journal baseline ref mismatch")
    for key in ("target_manifest_before", "target_manifest_after"):
        value = payload.get(key)
        if not isinstance(value, str) or not SHA256_RE.fullmatch(value):
            raise ContractError(f"transaction journal {key} is not a SHA-256 digest")

    raw_entries = payload.get("entries")
    if not isinstance(raw_entries, list) or not raw_entries:
        raise ContractError("transaction journal must contain at least one entry")
    authorized_destinations = {
        path.resolve()
        for path in root.rglob("index.html")
        if not any(part in EXCLUDED_DIR_NAMES for part in path.relative_to(root).parts)
    }
    seen_destinations: set[Path] = set()
    seen_transaction_files: set[Path] = set()
    for index, raw_entry in enumerate(raw_entries):
        if not isinstance(raw_entry, dict):
            raise ContractError(f"transaction entry {index} is not an object")
        destination = resolve_transaction_path(root, raw_entry.get("path"))
        if destination not in authorized_destinations or destination.name != "index.html":
            raise ContractError(
                f"transaction entry {index} destination is not an authorized index.html"
            )
        if raw_entry.get("path") != relative_posix(destination, root):
            raise ContractError(f"transaction entry {index} destination spelling is not canonical")
        if destination in seen_destinations:
            raise ContractError(f"transaction entry {index} duplicates a destination")
        seen_destinations.add(destination)

        expected_stage = destination.parent / f"{TEMP_NAME_PREFIX}{token}-{index:04d}.after"
        expected_backup = destination.parent / f"{TEMP_NAME_PREFIX}{token}-{index:04d}.before"
        for key, expected_path in (("stage", expected_stage), ("backup", expected_backup)):
            actual_path = resolve_transaction_path(root, raw_entry.get(key))
            if actual_path != expected_path.resolve():
                raise ContractError(f"transaction entry {index} {key} path mismatch")
            if raw_entry.get(key) != relative_posix(expected_path, root):
                raise ContractError(f"transaction entry {index} {key} spelling mismatch")
            if actual_path in seen_transaction_files:
                raise ContractError(f"transaction entry {index} reuses a transaction file")
            seen_transaction_files.add(actual_path)

        for key in ("before_sha256", "after_sha256"):
            value = raw_entry.get(key)
            if not isinstance(value, str) or not SHA256_RE.fullmatch(value):
                raise ContractError(f"transaction entry {index} {key} is not a SHA-256 digest")
        if raw_entry["before_sha256"] == raw_entry["after_sha256"]:
            raise ContractError(f"transaction entry {index} does not describe a changed file")


def recover_pending_transaction(root: Path) -> str | None:
    payload = load_transaction_journal(root)
    if payload is None:
        return None
    phase = str(payload["phase"])
    entries = payload["entries"]
    assert isinstance(entries, list)

    if phase == "prepared":
        for raw_entry in reversed(entries):
            if not isinstance(raw_entry, dict):
                raise ContractError("invalid transaction entry")
            destination = resolve_transaction_path(root, raw_entry.get("path"))
            backup = resolve_transaction_path(root, raw_entry.get("backup"))
            expected_before = raw_entry.get("before_sha256")
            if backup.exists():
                os.replace(backup, destination)
            if not destination.exists() or sha256_bytes(destination.read_bytes()) != expected_before:
                raise ContractError(
                    f"transaction rollback could not restore {raw_entry.get('path')!r}"
                )
    elif phase == "committed":
        for raw_entry in entries:
            if not isinstance(raw_entry, dict):
                raise ContractError("invalid transaction entry")
            destination = resolve_transaction_path(root, raw_entry.get("path"))
            expected_after = raw_entry.get("after_sha256")
            if not destination.exists() or sha256_bytes(destination.read_bytes()) != expected_after:
                raise ContractError(
                    f"committed transaction target drifted: {raw_entry.get('path')!r}"
                )

    for raw_entry in entries:
        if not isinstance(raw_entry, dict):
            continue
        resolve_transaction_path(root, raw_entry.get("stage")).unlink(missing_ok=True)
        resolve_transaction_path(root, raw_entry.get("backup")).unlink(missing_ok=True)
    (root / TRANSACTION_JOURNAL_NAME).unlink(missing_ok=True)
    fsync_directory(root)
    return phase


def assert_no_pending_transaction(root: Path) -> None:
    if (root / TRANSACTION_JOURNAL_NAME).exists():
        raise ContractError(
            "a pending Phase 4 transaction exists; run --apply to recover it before checking"
        )


def apply_plan(plan: BuildPlan) -> int:
    changed = [document for document in plan.documents if document.before != document.after]
    authorized = {document.path.resolve() for document in plan.documents}
    if external_manifest(plan.root, authorized) != plan.external_manifest:
        raise ContractError("external repository manifest drifted before apply")
    if manifest_for_documents(
        [(document.relative_path, document.path.read_bytes()) for document in plan.documents]
    ) != plan.target_manifest_before:
        raise ContractError("target HTML drifted before apply")
    if not changed:
        return 0
    if load_transaction_journal(plan.root) is not None:
        raise ContractError("pending transaction must be recovered before apply")

    token = uuid.uuid4().hex
    entries: list[dict[str, object]] = []
    for index, document in enumerate(changed):
        stage = document.path.parent / f"{TEMP_NAME_PREFIX}{token}-{index:04d}.after"
        backup = document.path.parent / f"{TEMP_NAME_PREFIX}{token}-{index:04d}.before"
        entries.append(
            {
                "path": document.relative_path,
                "stage": relative_posix(stage, plan.root),
                "backup": relative_posix(backup, plan.root),
                "before_sha256": sha256_bytes(document.before),
                "after_sha256": sha256_bytes(document.after),
            }
        )
    journal: dict[str, object] = {
        "version": TRANSACTION_VERSION,
        "token": token,
        "phase": "preparing",
        "baseline_ref": BASELINE_REF,
        "target_manifest_before": plan.target_manifest_before,
        "target_manifest_after": plan.target_manifest_after,
        "entries": entries,
    }
    write_transaction_journal(plan.root, journal)

    try:
        for document, entry in zip(changed, entries, strict=True):
            stage = resolve_transaction_path(plan.root, entry["stage"])
            backup = resolve_transaction_path(plan.root, entry["backup"])
            mode = stat.S_IMODE(document.path.stat().st_mode)
            write_named_file(stage, document.after, mode)
            write_named_file(backup, document.before, mode)

        if external_manifest(plan.root, authorized) != plan.external_manifest:
            raise ContractError("external repository manifest drifted during staging")
        if manifest_for_documents(
            [(document.relative_path, document.path.read_bytes()) for document in plan.documents]
        ) != plan.target_manifest_before:
            raise ContractError("target HTML drifted during staging")

        journal["phase"] = "prepared"
        write_transaction_journal(plan.root, journal)
        for document, entry in zip(changed, entries, strict=True):
            if document.path.read_bytes() != document.before:
                raise ContractError(f"target drifted before replace: {document.relative_path}")
            stage = resolve_transaction_path(plan.root, entry["stage"])
            os.replace(stage, document.path)

        actual = manifest_for_documents(
            [(document.relative_path, document.path.read_bytes()) for document in plan.documents]
        )
        if actual != plan.target_manifest_after:
            raise ContractError("applied target manifest does not match projected manifest")
        if external_manifest(plan.root, authorized) != plan.external_manifest:
            raise ContractError("external repository manifest changed during apply")

        journal["phase"] = "committed"
        write_transaction_journal(plan.root, journal)
        recovered_phase = recover_pending_transaction(plan.root)
        if recovered_phase != "committed":
            raise ContractError("transaction commit cleanup did not complete")
        return len(changed)
    except BaseException as error:
        try:
            recovered_phase = recover_pending_transaction(plan.root)
        except BaseException as recovery_error:  # pragma: no cover - emergency path
            raise ContractError(
                f"apply failed ({error}); durable recovery also failed ({recovery_error})"
            ) from error
        if recovered_phase == "committed":
            raise ContractError(
                f"apply committed but final cleanup raised an error: {error}"
            ) from error
        raise


def report_for_plan(plan: BuildPlan, mode: str, written_files: int) -> dict[str, object]:
    state_after = "applied" if mode == "apply" and written_files else plan.state
    return {
        "ok": True,
        "mode": mode,
        "state": state_after,
        "state_before": plan.state,
        "state_after": state_after,
        "baseline_ref": BASELINE_REF,
        "branch": plan.branch,
        "head": plan.head,
        "written_files": written_files,
        "stats": plan.stats,
        "hub": {"before": plan.hub_before, "after": plan.hub_after},
        "manifests": {
            "target_before": plan.target_manifest_before,
            "target_projected": plan.target_manifest_after,
            "external": plan.external_manifest,
            "sitemap_sha256": plan.sitemap_sha256,
            "sitemap_order_sha256": plan.sitemap_order_sha256,
            "canonical_set_sha256": plan.canonical_set_sha256,
        },
        "contracts": {
            "url_set_unchanged": True,
            "canonical_unchanged": True,
            "sitemap_unchanged": True,
            "json_ld_unchanged": True,
            "shared_js_unchanged": True,
            "href_destinations_unchanged": True,
            "href_attribute_tokenization_independent": True,
            "second_pass_idempotent": True,
            "external_scope_unchanged": True,
            "crash_recoverable_transaction": True,
        },
    }


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="read-only full-site dry run (default)")
    mode.add_argument("--apply", action="store_true", help="atomically write the verified projection")
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="repository root (defaults to the script's parent repository)",
    )
    parser.add_argument("--json", action="store_true", help="emit the report as JSON")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    root = args.root.resolve()
    mode = "apply" if args.apply else "check"
    try:
        if args.apply:
            with RepositoryLock(root):
                recover_pending_transaction(root)
                plan = build_plan(root)
                written_files = apply_plan(plan)
        else:
            assert_no_pending_transaction(root)
            plan = build_plan(root)
            written_files = 0
        report = report_for_plan(plan, mode, written_files)
    except (ContractError, OSError, UnicodeError) as error:
        report = {"ok": False, "mode": mode, "error": str(error)}
        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        else:
            print(f"HOLD: {error}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        stats = report["stats"]
        print(
            "PASS "
            f"mode={mode} state={report['state']} pages={stats['page_count']} "
            f"changed={stats['changed_html_count']} hrefs={stats['href_replacement_count']} "
            f"hub_query={plan.hub_after['query_match_count']} "
            f"hub_reset={plan.hub_after['reset_count']} written={written_files}"
        )
        print(
            f"target_before={plan.target_manifest_before} "
            f"target_projected={plan.target_manifest_after} external={plan.external_manifest}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
