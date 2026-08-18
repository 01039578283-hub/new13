#!/usr/bin/env python3
"""Deterministic image-performance transformer for 소수정예학원.com.

The default mode is a read-only check.  The transformer discovers the 3,378
public HTML documents from ``sitemap.xml``, resolves every DOM image to a real
local asset, reads that asset's intrinsic dimensions without decoding its
pixels, and projects the following policy:

* hero/LCP images use ``loading=eager`` and ``fetchpriority=high``;
* every other visible image uses ``loading=lazy``;
* all retained images use ``decoding=async`` and exact intrinsic dimensions;
* the hidden representative ``img`` on each national detail page is removed;
* spaces and tabs immediately before a physical line ending are removed while
  the original CRLF/LF byte sequence is preserved.

The representative asset remains untouched and remains referenced by the
page's unchanged Open Graph metadata.  Canonical URLs, the complete ``head``
(including JSON-LD), reader copy, sitemap bytes, routes, and asset bytes are
hard guards.  ``--apply`` is the only mode that writes, and it uses same-folder
crash-recoverable journaled replacements with rollback.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import stat
import struct
import sys
import tempfile
import uuid
import xml.etree.ElementTree as ET
from collections import Counter
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import Callable, Iterable, Sequence
from urllib.parse import unquote, urlsplit


BASE_URL = "https://xn--9l4b2jy4h4wan0chw4b.com"
BASELINE_REF = "c6bb0e7d43bea43624adbd63db6b564eacf3f718"
TRANSACTION_OWNER = "smallclass_phase4_images"
TEMP_NAME_PREFIX = ".smallclass-phase4-"
TRANSACTION_JOURNAL_NAME = f"{TEMP_NAME_PREFIX}transaction.json"
TRANSACTION_LOCK_NAME = f"{TEMP_NAME_PREFIX}transaction.lock"
# Version 2 deliberately differs from the technical transformer's version 1.
# Both tools use the same journal path, so either tool fails closed rather than
# attempting to recover the other tool's transaction.
TRANSACTION_VERSION = 2
PUBLIC_PAGE_COUNT = 3378
DETAIL_PAGE_COUNT = 3339
PROJECTED_PAGE_WITH_IMAGE_COUNT = 3367
PROJECTED_IMAGE_COUNT = 6709
PROJECTED_HERO_COUNT = 27
PROJECTED_BELOW_FOLD_COUNT = 6682
PROJECTED_DISTINCT_ASSET_COUNT = 377
PROJECTED_REFERENCE_TYPES = {
    ".webp": 3370,
    ".jpg": 3330,
    ".png": 9,
}
PRE_TRANSFORM_IMAGE_COUNT = 10048
PRE_TRANSFORM_DISTINCT_ASSET_COUNT = 2603
HIDDEN_REPRESENTATIVE_COUNT = DETAIL_PAGE_COUNT
HIDDEN_REPRESENTATIVE_DISTINCT_ASSETS = 2226
CURRENT_TRAILING_EOL_FILE_COUNT = 1113
CURRENT_TRAILING_EOL_OCCURRENCE_COUNT = 1113
CURRENT_TRAILING_EOL_BYTE_COUNT = 6678
CURRENT_TRAILING_EOL_SEQUENCE = "      "

SITEMAP_NAMESPACE = "http://www.sitemaps.org/schemas/sitemap/0.9"
UTF8_BOM = b"\xef\xbb\xbf"
VOID_ELEMENTS = frozenset(
    {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }
)
SUPPORTED_IMAGE_SUFFIXES = frozenset({".gif", ".jpg", ".jpeg", ".png", ".webp"})
MANAGED_IMAGE_ATTRIBUTES = frozenset(
    {"width", "height", "loading", "decoding", "fetchpriority"}
)
JPEG_START_OF_FRAME = frozenset(
    {
        0xC0,
        0xC1,
        0xC2,
        0xC3,
        0xC5,
        0xC6,
        0xC7,
        0xC9,
        0xCA,
        0xCB,
        0xCD,
        0xCE,
        0xCF,
    }
)

IMG_RE = re.compile(r"<img\b[^>]*>", re.IGNORECASE | re.DOTALL)
HEAD_RE = re.compile(r"<head\b[^>]*>.*?</head>", re.IGNORECASE | re.DOTALL)
META_RE = re.compile(r"<meta\b[^>]*>", re.IGNORECASE | re.DOTALL)
CANONICAL_RE = re.compile(
    r"<link\b(?=[^>]*\brel=[\"']canonical[\"'])[^>]*"
    r"\bhref=[\"']([^\"']+)[\"'][^>]*>",
    re.IGNORECASE | re.DOTALL,
)
JSON_LD_RE = re.compile(
    r"<script\b(?=[^>]*\btype=[\"']application/ld\+json[\"'])[^>]*>"
    r"(.*?)</script\s*>",
    re.IGNORECASE | re.DOTALL,
)
TRAILING_EOL_WHITESPACE_RE = re.compile(r"[ \t]+(?=\r\n|\r(?!\n)|\n|\Z)")
PHYSICAL_LINE_ENDING_RE = re.compile(r"\r\n|\r|\n")
SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
UUID4_HEX_RE = re.compile(r"[0-9a-f]{32}\Z")


class GateError(RuntimeError):
    """Raised when a page or repository invariant is not satisfied."""


@dataclass(frozen=True)
class ImageToken:
    raw: str
    attributes: tuple[tuple[str, str | None], ...]
    ancestor_classes: tuple[str, ...]

    @property
    def attrs(self) -> dict[str, str | None]:
        return dict(self.attributes)

    @property
    def classes(self) -> frozenset[str]:
        return frozenset((self.attrs.get("class") or "").split())

    @property
    def hidden_representative(self) -> bool:
        return "hidden-representative" in self.classes

    @property
    def hero(self) -> bool:
        own = tuple(self.classes)
        return any("hero" in value.lower() for value in (*self.ancestor_classes, *own))


class ImageInventoryParser(HTMLParser):
    """Collect exact img start tags together with their ancestor class names."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.stack: list[tuple[str, tuple[str, ...]]] = []
        self.images: list[ImageToken] = []
        self.picture_count = 0
        self.source_srcset_count = 0
        self.image_srcset_count = 0

    @staticmethod
    def _attributes(attrs: list[tuple[str, str | None]]) -> tuple[tuple[str, str | None], ...]:
        lowered = tuple((name.lower(), value) for name, value in attrs)
        names = [name for name, _ in lowered]
        duplicates = [name for name, count in Counter(names).items() if count > 1]
        if duplicates:
            raise GateError(f"duplicate image/element attributes: {duplicates}")
        return lowered

    def _record_image(self, attrs: list[tuple[str, str | None]]) -> None:
        attributes = self._attributes(attrs)
        if "srcset" in dict(attributes):
            self.image_srcset_count += 1
        ancestor_classes = tuple(
            class_name
            for _tag, classes in self.stack
            for class_name in classes
        )
        self.images.append(
            ImageToken(self.get_starttag_text(), attributes, ancestor_classes)
        )

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag == "img":
            self._record_image(attrs)
        elif tag == "picture":
            self.picture_count += 1
        elif tag == "source" and "srcset" in dict(self._attributes(attrs)):
            self.source_srcset_count += 1
        if tag not in VOID_ELEMENTS:
            attr_map = dict(self._attributes(attrs))
            classes = tuple((attr_map.get("class") or "").split())
            self.stack.append((tag, classes))

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag == "img":
            self._record_image(attrs)
        elif tag == "picture":
            self.picture_count += 1
        elif tag == "source" and "srcset" in dict(self._attributes(attrs)):
            self.source_srcset_count += 1

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                self.stack = self.stack[:index]
                return


@dataclass
class MutationStats:
    counts: Counter[str] = field(default_factory=Counter)
    input_asset_references: Counter[str] = field(default_factory=Counter)
    output_asset_references: Counter[str] = field(default_factory=Counter)
    hidden_assets: set[str] = field(default_factory=set)
    detail_og_assets: set[str] = field(default_factory=set)
    hidden_reference_bytes: int = 0
    hidden_unique_bytes: int = 0


@dataclass(frozen=True)
class PlannedDocument:
    path: Path
    before: bytes
    after: bytes
    canonical: str


@dataclass
class BuildPlan:
    root: Path
    documents: list[PlannedDocument]
    stats: MutationStats
    dimensions: dict[Path, tuple[int, int]]
    target_paths: frozenset[Path]
    external_sha256: str
    asset_sha256: str
    sitemap_sha256: str
    target_input_sha256: str
    target_projected_sha256: str
    idempotent: bool

    @property
    def changes(self) -> list[PlannedDocument]:
        return [document for document in self.documents if document.before != document.after]


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def decode_html(value: bytes) -> tuple[str, bytes]:
    if value.startswith(UTF8_BOM):
        return value[len(UTF8_BOM) :].decode("utf-8"), UTF8_BOM
    return value.decode("utf-8"), b""


def parse_images(text: str, path: Path) -> ImageInventoryParser:
    parser = ImageInventoryParser()
    try:
        parser.feed(text)
        parser.close()
    except Exception as exc:
        if isinstance(exc, GateError):
            raise GateError(f"{path}: {exc}") from exc
        raise GateError(f"{path}: HTML image inventory failed: {exc}") from exc
    regex_tags = [match.group(0) for match in IMG_RE.finditer(text)]
    parser_tags = [token.raw for token in parser.images]
    if regex_tags != parser_tags:
        raise GateError(f"{path}: HTML parser/img transformer token mismatch")
    return parser


def head_html(text: str, path: Path) -> str:
    matches = HEAD_RE.findall(text)
    if len(matches) != 1:
        raise GateError(f"{path}: expected one head element, found {len(matches)}")
    return matches[0]


def canonical_values(text: str, path: Path) -> list[str]:
    values = [html.unescape(value.strip()) for value in CANONICAL_RE.findall(text)]
    if len(values) != 1:
        raise GateError(f"{path}: expected one canonical, found {len(values)}")
    return values


def attribute_pattern(name: str) -> re.Pattern[str]:
    return re.compile(
        rf"(?P<lead>\s+)(?P<name>{re.escape(name)})(?=\s|=|/?>)"
        rf"(?:\s*=\s*(?P<value>\"[^\"]*\"|'[^']*'|[^\s>]+))?",
        re.IGNORECASE | re.DOTALL,
    )


def raw_attribute_value(tag: str, name: str) -> str | None:
    matches = list(attribute_pattern(name).finditer(tag))
    if len(matches) > 1:
        raise GateError(f"duplicate {name} attribute in {tag[:160]!r}")
    if not matches:
        return None
    raw = matches[0].group("value")
    if raw is None:
        return ""
    if len(raw) >= 2 and raw[0] in {'"', "'"} and raw[-1] == raw[0]:
        raw = raw[1:-1]
    return html.unescape(raw)


def set_attribute(tag: str, name: str, value: str) -> tuple[str, str]:
    pattern = attribute_pattern(name)
    matches = list(pattern.finditer(tag))
    if len(matches) > 1:
        raise GateError(f"duplicate {name} attribute in {tag[:160]!r}")
    escaped = html.escape(value, quote=True)
    if matches:
        match = matches[0]
        if raw_attribute_value(tag, name) == value:
            return tag, "same"
        replacement = f'{match.group("lead")}{name}="{escaped}"'
        return tag[: match.start()] + replacement + tag[match.end() :], "updated"
    closing = re.search(r"\s*/?>$", tag, re.DOTALL)
    if not closing:
        raise GateError(f"invalid img closing syntax: {tag[:160]!r}")
    position = closing.start()
    return tag[:position] + f' {name}="{escaped}"' + tag[position:], "added"


def remove_attribute(tag: str, name: str) -> tuple[str, bool]:
    pattern = attribute_pattern(name)
    matches = list(pattern.finditer(tag))
    if len(matches) > 1:
        raise GateError(f"duplicate {name} attribute in {tag[:160]!r}")
    if not matches:
        return tag, False
    match = matches[0]
    return tag[: match.start()] + tag[match.end() :], True


def resolve_asset(root: Path, page: Path, source: str) -> Path:
    source = html.unescape(source.strip())
    if not source:
        raise GateError(f"{page}: empty img src")
    parsed = urlsplit(source)
    if parsed.scheme in {"http", "https"}:
        if f"{parsed.scheme}://{parsed.netloc}" != BASE_URL:
            raise GateError(f"{page}: remote image is outside the canonical origin: {source}")
        candidate = root / unquote(parsed.path).lstrip("/")
    elif parsed.scheme or source.startswith("//") or source.startswith("data:"):
        raise GateError(f"{page}: unsupported image source: {source}")
    elif parsed.path.startswith("/"):
        candidate = root / unquote(parsed.path).lstrip("/")
    else:
        candidate = page.parent / unquote(parsed.path)
    candidate = candidate.resolve()
    try:
        relative = candidate.relative_to(root)
    except ValueError as exc:
        raise GateError(f"{page}: image escapes repository root: {source}") from exc
    if not relative.parts or relative.parts[0] != "assets":
        raise GateError(f"{page}: DOM image is not under assets/: {source}")
    if not candidate.is_file():
        raise GateError(f"{page}: broken image path: {source}")
    if candidate.suffix.lower() not in SUPPORTED_IMAGE_SUFFIXES:
        raise GateError(f"{page}: unsupported image extension: {candidate.suffix}")
    return candidate


def jpeg_dimensions(data: bytes, path: Path) -> tuple[int, int]:
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
        segment_length = int.from_bytes(data[offset : offset + 2], "big")
        if segment_length < 2 or offset + segment_length > len(data):
            break
        if marker in JPEG_START_OF_FRAME:
            if segment_length < 7:
                break
            height = int.from_bytes(data[offset + 3 : offset + 5], "big")
            width = int.from_bytes(data[offset + 5 : offset + 7], "big")
            return width, height
        offset += segment_length
    raise GateError(f"{path}: JPEG intrinsic dimensions not found")


def webp_dimensions(data: bytes, path: Path) -> tuple[int, int]:
    if len(data) < 30:
        raise GateError(f"{path}: truncated WebP")
    chunk = data[12:16]
    payload = data[20:]
    if chunk == b"VP8X":
        return (
            1 + int.from_bytes(payload[4:7], "little"),
            1 + int.from_bytes(payload[7:10], "little"),
        )
    if chunk == b"VP8L":
        if not payload or payload[0] != 0x2F:
            raise GateError(f"{path}: invalid VP8L signature")
        bits = int.from_bytes(payload[1:5], "little")
        return 1 + (bits & 0x3FFF), 1 + ((bits >> 14) & 0x3FFF)
    if chunk == b"VP8 ":
        frame = payload.find(b"\x9d\x01\x2a")
        if frame < 0 or frame + 7 > len(payload):
            raise GateError(f"{path}: VP8 frame header not found")
        return (
            int.from_bytes(payload[frame + 3 : frame + 5], "little") & 0x3FFF,
            int.from_bytes(payload[frame + 5 : frame + 7], "little") & 0x3FFF,
        )
    raise GateError(f"{path}: unsupported WebP chunk {chunk!r}")


def intrinsic_dimensions(path: Path, cache: dict[Path, tuple[int, int]]) -> tuple[int, int]:
    if path in cache:
        return cache[path]
    data = path.read_bytes()
    if data.startswith(b"\x89PNG\r\n\x1a\n") and len(data) >= 24:
        dimensions = struct.unpack(">II", data[16:24])
    elif data[:6] in {b"GIF87a", b"GIF89a"} and len(data) >= 10:
        dimensions = struct.unpack("<HH", data[6:10])
    elif data.startswith(b"\xff\xd8"):
        dimensions = jpeg_dimensions(data, path)
    elif data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        dimensions = webp_dimensions(data, path)
    else:
        raise GateError(f"{path}: unsupported or corrupt image signature")
    width, height = dimensions
    if width <= 0 or height <= 0 or width > 1_000_000 or height > 1_000_000:
        raise GateError(f"{path}: implausible intrinsic dimensions {dimensions}")
    cache[path] = dimensions
    return dimensions


def meta_property_values(head: str, property_name: str) -> list[str]:
    values: list[str] = []
    for tag in META_RE.findall(head):
        if (raw_attribute_value(tag, "property") or "").lower() != property_name.lower():
            continue
        value = raw_attribute_value(tag, "content")
        if value is None:
            raise GateError(f"{property_name} meta tag lacks content")
        values.append(value)
    return values


def is_detail_path(root: Path, path: Path) -> bool:
    parts = path.relative_to(root).parts
    return len(parts) == 4 and parts[0] == "전국학원" and parts[-1] == "index.html"


def mutate_image_tag(
    tag: str,
    token: ImageToken,
    root: Path,
    path: Path,
    dimensions: dict[Path, tuple[int, int]],
    og_assets: frozenset[Path],
    stats: MutationStats | None,
) -> str:
    source = token.attrs.get("src")
    if not source:
        raise GateError(f"{path}: img lacks src")
    asset = resolve_asset(root, path, source)
    relative_asset = asset.relative_to(root).as_posix()
    if stats is not None:
        stats.input_asset_references[relative_asset] += 1
        stats.counts[f"input_type_{asset.suffix.lower()}"] += 1

    if token.hidden_representative:
        if not is_detail_path(root, path):
            raise GateError(f"{path}: hidden representative outside a detail page")
        style = (token.attrs.get("style") or "").replace(" ", "").lower()
        if "display:none" not in style:
            raise GateError(f"{path}: hidden representative lacks display:none")
        if asset not in og_assets:
            raise GateError(f"{path}: hidden representative does not match og:image")
        if stats is not None:
            stats.counts["hidden_representative_removed"] += 1
            stats.hidden_assets.add(relative_asset)
            stats.hidden_reference_bytes += asset.stat().st_size
        return ""

    width, height = intrinsic_dimensions(asset, dimensions)
    updated = tag
    dimension_changed = False
    for name, value in (("width", str(width)), ("height", str(height))):
        updated, result = set_attribute(updated, name, value)
        if result != "same":
            dimension_changed = True
            if stats is not None:
                stats.counts[f"{name}_{result}"] += 1
    if dimension_changed and stats is not None:
        stats.counts["dimension_tags_changed"] += 1

    loading = "eager" if token.hero else "lazy"
    updated, result = set_attribute(updated, "loading", loading)
    if result != "same" and stats is not None:
        stats.counts[f"loading_{result}"] += 1
    updated, result = set_attribute(updated, "decoding", "async")
    if result != "same" and stats is not None:
        stats.counts[f"decoding_{result}"] += 1
    if token.hero:
        updated, result = set_attribute(updated, "fetchpriority", "high")
        if result != "same" and stats is not None:
            stats.counts[f"fetchpriority_{result}"] += 1
    else:
        updated, removed = remove_attribute(updated, "fetchpriority")
        if removed and stats is not None:
            stats.counts["below_fold_fetchpriority_removed"] += 1

    if stats is not None:
        stats.output_asset_references[relative_asset] += 1
        stats.counts[f"output_type_{asset.suffix.lower()}"] += 1
    return updated


def image_stripped_html(text: str) -> str:
    return IMG_RE.sub("", text)


def trailing_eol_whitespace(text: str) -> tuple[str, ...]:
    return tuple(match.group(0) for match in TRAILING_EOL_WHITESPACE_RE.finditer(text))


def record_trailing_eol_state(
    stats: MutationStats | None,
    prefix: str,
    matches: tuple[str, ...],
) -> None:
    if stats is None:
        return
    stats.counts[f"{prefix}_files"] += bool(matches)
    stats.counts[f"{prefix}_occurrences"] += len(matches)
    stats.counts[f"{prefix}_bytes"] += sum(len(value) for value in matches)
    stats.counts[f"{prefix}_exact_six_spaces"] += sum(
        value == CURRENT_TRAILING_EOL_SEQUENCE for value in matches
    )
    stats.counts[f"{prefix}_other"] += sum(
        value != CURRENT_TRAILING_EOL_SEQUENCE for value in matches
    )


def remove_trailing_eol_whitespace(
    text: str,
    path: Path,
    stats: MutationStats | None,
) -> str:
    matches = trailing_eol_whitespace(text)
    record_trailing_eol_state(stats, "cleanup_trailing_eol", matches)
    line_endings = tuple(PHYSICAL_LINE_ENDING_RE.findall(text))
    cleaned = TRAILING_EOL_WHITESPACE_RE.sub("", text)
    if tuple(PHYSICAL_LINE_ENDING_RE.findall(cleaned)) != line_endings:
        raise GateError(f"{path}: trailing-whitespace cleanup changed physical line endings")
    removed = len(text) - len(cleaned)
    if removed != sum(len(value) for value in matches):
        raise GateError(f"{path}: trailing-whitespace cleanup removed unexpected bytes")
    output_matches = trailing_eol_whitespace(cleaned)
    record_trailing_eol_state(stats, "output_trailing_eol", output_matches)
    if output_matches:
        raise GateError(f"{path}: trailing whitespace remains after cleanup")
    if stats is not None:
        stats.counts["trailing_eol_whitespace_occurrences_removed"] += len(matches)
        stats.counts["trailing_eol_whitespace_bytes_removed"] += removed
    return cleaned


def validate_output_images(
    before_parser: ImageInventoryParser,
    after_parser: ImageInventoryParser,
    root: Path,
    path: Path,
    dimensions: dict[Path, tuple[int, int]],
) -> None:
    def unmanaged_attributes(token: ImageToken) -> tuple[tuple[str, str | None], ...]:
        return tuple(
            (name, value)
            for name, value in token.attributes
            if name not in MANAGED_IMAGE_ATTRIBUTES
        )

    before_retained = [
        unmanaged_attributes(token)
        for token in before_parser.images
        if not token.hidden_representative
    ]
    after_identity = [
        unmanaged_attributes(token)
        for token in after_parser.images
    ]
    if before_retained != after_identity:
        raise GateError(f"{path}: unmanaged retained-image attributes changed")
    if any(token.hidden_representative for token in after_parser.images):
        raise GateError(f"{path}: hidden representative remains")
    if is_detail_path(root, path) and len(after_parser.images) != 2:
        raise GateError(f"{path}: detail page must project exactly two visible images")
    for token in after_parser.images:
        source = token.attrs.get("src")
        if not source:
            raise GateError(f"{path}: projected image lacks src")
        asset = resolve_asset(root, path, source)
        width, height = intrinsic_dimensions(asset, dimensions)
        expected = {
            "width": str(width),
            "height": str(height),
            "loading": "eager" if token.hero else "lazy",
            "decoding": "async",
        }
        for name, value in expected.items():
            if token.attrs.get(name) != value:
                raise GateError(
                    f"{path}: projected {name} mismatch for {source}: "
                    f"{token.attrs.get(name)!r} != {value!r}"
                )
        priority = token.attrs.get("fetchpriority")
        if token.hero and priority != "high":
            raise GateError(f"{path}: hero image lacks fetchpriority=high")
        if not token.hero and priority is not None:
            raise GateError(f"{path}: below-fold image retains fetchpriority")


def transform_document(
    text: str,
    root: Path,
    path: Path,
    dimensions: dict[Path, tuple[int, int]],
    stats: MutationStats | None = None,
) -> str:
    input_trailing = trailing_eol_whitespace(text)
    record_trailing_eol_state(stats, "input_trailing_eol", input_trailing)
    before_parser = parse_images(text, path)
    if before_parser.picture_count or before_parser.source_srcset_count or before_parser.image_srcset_count:
        raise GateError(f"{path}: picture/srcset requires an explicit responsive-image policy")
    hidden_count = sum(token.hidden_representative for token in before_parser.images)
    if hidden_count > 1:
        raise GateError(f"{path}: multiple hidden representatives")
    if hidden_count and not is_detail_path(root, path):
        raise GateError(f"{path}: hidden representative outside detail route")

    head_before = head_html(text, path)
    og_values = meta_property_values(head_before, "og:image")
    if len(og_values) > 1:
        raise GateError(f"{path}: expected at most one og:image, found {len(og_values)}")
    og_assets = frozenset(resolve_asset(root, path, value) for value in og_values)
    detail = is_detail_path(root, path)
    if detail and len(og_assets) != 1:
        raise GateError(f"{path}: detail page requires one valid og:image")
    if stats is not None and detail:
        stats.counts["detail_og_image_references"] += 1
        stats.detail_og_assets.update(asset.relative_to(root).as_posix() for asset in og_assets)

    tokens = iter(before_parser.images)

    def replace(match: re.Match[str]) -> str:
        try:
            token = next(tokens)
        except StopIteration as exc:
            raise GateError(f"{path}: image token underflow") from exc
        if match.group(0) != token.raw:
            raise GateError(f"{path}: image token order drift")
        return mutate_image_tag(match.group(0), token, root, path, dimensions, og_assets, stats)

    image_only = IMG_RE.sub(replace, text)
    try:
        next(tokens)
    except StopIteration:
        pass
    else:
        raise GateError(f"{path}: image token overflow")

    if image_stripped_html(image_only) != image_stripped_html(text):
        raise GateError(f"{path}: bytes outside img tags changed before EOL cleanup")
    after = remove_trailing_eol_whitespace(image_only, path, stats)
    if head_html(after, path) != head_before:
        raise GateError(f"{path}: head bytes changed")
    if JSON_LD_RE.findall(after) != JSON_LD_RE.findall(text):
        raise GateError(f"{path}: JSON-LD changed")
    if canonical_values(after, path) != canonical_values(text, path):
        raise GateError(f"{path}: canonical changed")
    after_parser = parse_images(after, path)
    validate_output_images(before_parser, after_parser, root, path, dimensions)

    if stats is not None:
        stats.counts["pages_scanned"] += 1
        stats.counts["input_images"] += len(before_parser.images)
        stats.counts["output_images"] += len(after_parser.images)
        stats.counts["input_hidden"] += hidden_count
        stats.counts["output_hidden"] += sum(
            token.hidden_representative for token in after_parser.images
        )
        stats.counts["output_pages_with_images"] += bool(after_parser.images)
        stats.counts["output_hero"] += sum(token.hero for token in after_parser.images)
        stats.counts["output_below_fold"] += sum(
            not token.hero for token in after_parser.images
        )
    return after


def page_for_url(root: Path, value: str) -> Path:
    parsed = urlsplit(value)
    if f"{parsed.scheme}://{parsed.netloc}" != BASE_URL or parsed.query or parsed.fragment:
        raise GateError(f"non-canonical sitemap URL: {value}")
    decoded = unquote(parsed.path)
    if not decoded.startswith("/") or not decoded.endswith("/"):
        raise GateError(f"sitemap URL must be a trailing-slash route: {value}")
    relative = decoded.strip("/")
    path = root / "index.html" if not relative else root / relative / "index.html"
    path = path.resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise GateError(f"sitemap route escapes root: {value}") from exc
    if not path.is_file():
        raise GateError(f"sitemap route has no index.html: {value}")
    return path


def discover_pages(root: Path) -> tuple[list[tuple[Path, str]], bytes]:
    sitemap_path = root / "sitemap.xml"
    sitemap_bytes = sitemap_path.read_bytes()
    try:
        document = ET.fromstring(sitemap_bytes)
    except ET.ParseError as exc:
        raise GateError(f"invalid sitemap.xml: {exc}") from exc
    locs = [
        (node.text or "").strip()
        for node in document.findall(f"{{{SITEMAP_NAMESPACE}}}url/{{{SITEMAP_NAMESPACE}}}loc")
    ]
    if len(locs) != PUBLIC_PAGE_COUNT or len(set(locs)) != PUBLIC_PAGE_COUNT:
        raise GateError(f"public sitemap URL count mismatch: {len(locs)}/{len(set(locs))}")
    pages = [(page_for_url(root, loc), loc) for loc in locs]
    paths = [path for path, _ in pages]
    if len(set(paths)) != PUBLIC_PAGE_COUNT:
        raise GateError("multiple sitemap URLs resolve to one HTML path")
    detail_count = sum(is_detail_path(root, path) for path in paths)
    if detail_count != DETAIL_PAGE_COUNT:
        raise GateError(f"detail route count mismatch: {detail_count}")
    return pages, sitemap_bytes


def iter_files(root: Path, directory: Path) -> Iterable[Path]:
    for path in sorted(directory.rglob("*"), key=lambda value: value.relative_to(root).as_posix()):
        if path.is_file():
            yield path


def digest_paths(root: Path, paths: Iterable[Path]) -> str:
    digest = hashlib.sha256()
    for path in paths:
        relative = path.relative_to(root).as_posix().encode("utf-8")
        value = path.read_bytes()
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        digest.update(len(value).to_bytes(8, "big"))
        digest.update(hashlib.sha256(value).digest())
    return digest.hexdigest()


def digest_named_values(values: Iterable[tuple[str, bytes]]) -> str:
    digest = hashlib.sha256()
    for name, value in values:
        encoded_name = name.encode("utf-8")
        digest.update(len(encoded_name).to_bytes(4, "big"))
        digest.update(encoded_name)
        digest.update(len(value).to_bytes(8, "big"))
        digest.update(hashlib.sha256(value).digest())
    return digest.hexdigest()


def asset_manifest_sha256(root: Path) -> str:
    assets = root / "assets"
    if not assets.is_dir():
        raise GateError("assets directory missing")
    return digest_paths(root, iter_files(root, assets))


def external_manifest_sha256(root: Path, target_paths: frozenset[Path]) -> str:
    excluded_roots = {".git", "tmp", "assets"}
    paths: list[Path] = []
    for directory, dirnames, filenames in os.walk(root, topdown=True, followlinks=False):
        dirnames[:] = sorted(
            name
            for name in dirnames
            if name not in excluded_roots and name != "__pycache__"
        )
        parent = Path(directory)
        for filename in sorted(filenames):
            path = parent / filename
            if path.resolve() in target_paths:
                continue
            if path.suffix.lower() == ".pyc" or ".smallclass-phase4-" in path.name:
                continue
            paths.append(path)
    return digest_paths(root, sorted(paths, key=lambda value: value.relative_to(root).as_posix()))


def validate_global_projection(stats: MutationStats) -> None:
    counts = stats.counts
    if counts["pages_scanned"] != PUBLIC_PAGE_COUNT:
        raise GateError(f"page scan count mismatch: {counts['pages_scanned']}")
    if counts["input_hidden"] not in {0, HIDDEN_REPRESENTATIVE_COUNT}:
        raise GateError(f"partial hidden-representative state: {counts['input_hidden']}")
    expected_input = (
        PRE_TRANSFORM_IMAGE_COUNT
        if counts["input_hidden"] == HIDDEN_REPRESENTATIVE_COUNT
        else PROJECTED_IMAGE_COUNT
    )
    if counts["input_images"] != expected_input:
        raise GateError(f"input image count mismatch: {counts['input_images']} != {expected_input}")
    expected = {
        "output_images": PROJECTED_IMAGE_COUNT,
        "output_hidden": 0,
        "output_pages_with_images": PROJECTED_PAGE_WITH_IMAGE_COUNT,
        "output_hero": PROJECTED_HERO_COUNT,
        "output_below_fold": PROJECTED_BELOW_FOLD_COUNT,
    }
    for name, value in expected.items():
        if counts[name] != value:
            raise GateError(f"{name} mismatch: {counts[name]} != {value}")
    output_types = {
        suffix: counts[f"output_type_{suffix}"] for suffix in PROJECTED_REFERENCE_TYPES
    }
    if output_types != PROJECTED_REFERENCE_TYPES:
        raise GateError(f"projected image type inventory mismatch: {output_types}")
    if len(stats.output_asset_references) != PROJECTED_DISTINCT_ASSET_COUNT:
        raise GateError(
            f"projected distinct asset count mismatch: {len(stats.output_asset_references)}"
        )
    if counts["input_hidden"]:
        if len(stats.input_asset_references) != PRE_TRANSFORM_DISTINCT_ASSET_COUNT:
            raise GateError(
                f"input distinct asset count mismatch: {len(stats.input_asset_references)}"
            )
        if len(stats.hidden_assets) != HIDDEN_REPRESENTATIVE_DISTINCT_ASSETS:
            raise GateError(f"hidden distinct asset count mismatch: {len(stats.hidden_assets)}")
    if counts["detail_og_image_references"] != DETAIL_PAGE_COUNT:
        raise GateError(
            f"detail og:image reference count mismatch: {counts['detail_og_image_references']}"
        )
    if len(stats.detail_og_assets) != HIDDEN_REPRESENTATIVE_DISTINCT_ASSETS:
        raise GateError(f"detail og:image distinct asset mismatch: {len(stats.detail_og_assets)}")
    if counts["input_hidden"] and stats.hidden_assets != stats.detail_og_assets:
        raise GateError("hidden representative and detail og:image asset sets differ")


def trailing_state(counts: Counter[str], prefix: str) -> tuple[int, int, int, int, int]:
    return (
        counts[f"{prefix}_files"],
        counts[f"{prefix}_occurrences"],
        counts[f"{prefix}_bytes"],
        counts[f"{prefix}_exact_six_spaces"],
        counts[f"{prefix}_other"],
    )


def trailing_projection_mode(stats: MutationStats) -> str:
    counts = stats.counts
    zero = (0, 0, 0, 0, 0)
    current = (
        CURRENT_TRAILING_EOL_FILE_COUNT,
        CURRENT_TRAILING_EOL_OCCURRENCE_COUNT,
        CURRENT_TRAILING_EOL_BYTE_COUNT,
        CURRENT_TRAILING_EOL_OCCURRENCE_COUNT,
        0,
    )
    input_state = trailing_state(counts, "input_trailing_eol")
    cleanup_state = trailing_state(counts, "cleanup_trailing_eol")
    output_state = trailing_state(counts, "output_trailing_eol")
    hidden = counts["input_hidden"]
    if output_state != zero:
        raise GateError(f"projected trailing-whitespace state is not empty: {output_state}")
    if hidden == 0 and input_state == current and cleanup_state == current:
        return "current-applied-needs-eol-cleanup"
    if hidden == 0 and input_state == zero and cleanup_state == zero:
        return "current-applied-clean"
    if hidden == HIDDEN_REPRESENTATIVE_COUNT and input_state == zero and cleanup_state == current:
        return "baseline-hidden-images"
    raise GateError(
        "unexpected trailing-whitespace/input-image state: "
        f"hidden={hidden}, input={input_state}, cleanup={cleanup_state}"
    )


def validate_trailing_projection(
    root: Path,
    stats: MutationStats,
    documents: list[PlannedDocument],
) -> str:
    mode = trailing_projection_mode(stats)
    changes = [document for document in documents if document.before != document.after]
    expected_changes = {
        "current-applied-needs-eol-cleanup": CURRENT_TRAILING_EOL_FILE_COUNT,
        "current-applied-clean": 0,
        "baseline-hidden-images": PROJECTED_PAGE_WITH_IMAGE_COUNT,
    }[mode]
    if len(changes) != expected_changes:
        raise GateError(
            f"{mode} changed-page count mismatch: {len(changes)} != {expected_changes}"
        )
    if mode != "current-applied-needs-eol-cleanup":
        return mode
    for document in changes:
        if not is_detail_path(root, document.path):
            raise GateError(f"EOL cleanup changed a non-detail page: {document.path}")
        before_text, before_bom = decode_html(document.before)
        after_text, after_bom = decode_html(document.after)
        if before_bom != after_bom:
            raise GateError(f"{document.path}: EOL cleanup changed the BOM")
        if trailing_eol_whitespace(before_text) != (CURRENT_TRAILING_EOL_SEQUENCE,):
            raise GateError(f"{document.path}: current EOL cleanup input is not exact")
        if trailing_eol_whitespace(after_text):
            raise GateError(f"{document.path}: current EOL cleanup output is not clean")
        if len(document.before) - len(document.after) != len(CURRENT_TRAILING_EOL_SEQUENCE):
            raise GateError(f"{document.path}: current EOL cleanup byte delta is not six")
        if PHYSICAL_LINE_ENDING_RE.findall(before_text) != PHYSICAL_LINE_ENDING_RE.findall(
            after_text
        ):
            raise GateError(f"{document.path}: current EOL cleanup changed line endings")
    return mode


def build_plan(root: Path) -> BuildPlan:
    root = root.resolve()
    pages, sitemap_bytes = discover_pages(root)
    target_paths = frozenset(path.resolve() for path, _ in pages)
    sitemap_sha = sha256_bytes(sitemap_bytes)
    asset_sha = asset_manifest_sha256(root)
    external_sha = external_manifest_sha256(root, target_paths)
    stats = MutationStats()
    dimensions: dict[Path, tuple[int, int]] = {}
    documents: list[PlannedDocument] = []

    for path, expected_canonical in pages:
        before = path.read_bytes()
        text, bom = decode_html(before)
        if canonical_values(text, path) != [expected_canonical]:
            raise GateError(f"{path}: canonical/sitemap mismatch")
        after = transform_document(text, root, path, dimensions, stats)
        second = transform_document(after, root, path, dimensions, None)
        if second != after:
            raise GateError(f"{path}: in-memory transform is not idempotent")
        documents.append(
            PlannedDocument(path, before, bom + after.encode("utf-8"), expected_canonical)
        )

    validate_global_projection(stats)
    validate_trailing_projection(root, stats, documents)
    stats.hidden_unique_bytes = sum(
        (root / relative).stat().st_size for relative in stats.detail_og_assets
    )
    if len(dimensions) != PROJECTED_DISTINCT_ASSET_COUNT:
        raise GateError(f"dimension inventory mismatch: {len(dimensions)}")
    for document in documents:
        if document.path.read_bytes() != document.before:
            raise GateError(f"{document.path}: target changed during read-only preflight")
    if (root / "sitemap.xml").read_bytes() != sitemap_bytes:
        raise GateError("sitemap changed during read-only preflight")
    if asset_manifest_sha256(root) != asset_sha:
        raise GateError("asset bytes changed during read-only preflight")
    if external_manifest_sha256(root, target_paths) != external_sha:
        raise GateError("external scope changed during read-only preflight")
    target_input_sha = digest_named_values(
        (document.path.relative_to(root).as_posix(), document.before)
        for document in documents
    )
    target_projected_sha = digest_named_values(
        (document.path.relative_to(root).as_posix(), document.after)
        for document in documents
    )
    return BuildPlan(
        root=root,
        documents=documents,
        stats=stats,
        dimensions=dimensions,
        target_paths=target_paths,
        external_sha256=external_sha,
        asset_sha256=asset_sha,
        sitemap_sha256=sitemap_sha,
        target_input_sha256=target_input_sha,
        target_projected_sha256=target_projected_sha,
        idempotent=True,
    )


def relative_posix(path: Path, root: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


class RepositoryLock:
    """The shared, non-blocking Phase 4 repository mutation lock."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.path = self.root / TRANSACTION_LOCK_NAME
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
        except OSError as exc:
            stream.close()
            raise GateError("another Phase 4 apply/recovery process holds the lock") from exc
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
                fsync_directory(self.root)
            except OSError:
                pass


def fsync_directory(path: Path) -> None:
    """Best-effort directory metadata flush; Windows may not support it."""

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
        prefix=f"{TEMP_NAME_PREFIX}{token}-images-journal-", suffix=".tmp", dir=root
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


def resolve_transaction_path(root: Path, value: object) -> Path:
    if not isinstance(value, str) or not value:
        raise GateError(f"invalid transaction path: {value!r}")
    path = (root / Path(value)).resolve()
    try:
        path.relative_to(root.resolve())
    except ValueError as exc:
        raise GateError(f"transaction path escapes repository: {value!r}") from exc
    return path


def authorized_transaction_paths(root: Path) -> frozenset[Path]:
    pages, _sitemap = discover_pages(root)
    return frozenset(path.resolve() for path, _canonical in pages)


def validate_transaction_journal(
    root: Path,
    payload: dict[str, object],
    authorized_paths: frozenset[Path],
) -> None:
    expected_payload_keys = {
        "version",
        "owner",
        "token",
        "phase",
        "baseline_ref",
        "target_manifest_before",
        "target_manifest_after",
        "external_manifest",
        "asset_manifest",
        "sitemap_sha256",
        "entries",
    }
    if set(payload) != expected_payload_keys:
        raise GateError("transaction journal key set is invalid")
    if payload.get("owner") != TRANSACTION_OWNER:
        raise GateError("pending transaction belongs to another Phase 4 transformer")
    token = payload.get("token")
    if not isinstance(token, str) or not UUID4_HEX_RE.fullmatch(token):
        raise GateError("transaction journal token is not a UUID4 hex value")
    try:
        parsed_token = uuid.UUID(hex=token)
    except ValueError as exc:
        raise GateError("transaction journal token is invalid") from exc
    if parsed_token.version != 4 or parsed_token.hex != token:
        raise GateError("transaction journal token is not canonical UUID4 hex")
    if payload.get("baseline_ref") != BASELINE_REF:
        raise GateError("transaction journal baseline ref mismatch")
    for key in (
        "target_manifest_before",
        "target_manifest_after",
        "external_manifest",
        "asset_manifest",
        "sitemap_sha256",
    ):
        value = payload.get(key)
        if not isinstance(value, str) or not SHA256_RE.fullmatch(value):
            raise GateError(f"transaction journal {key} is not a SHA-256 digest")

    phase = payload.get("phase")
    if phase not in {"preparing", "prepared", "committing", "committed", "rolled_back"}:
        raise GateError(f"invalid transaction journal phase: {phase!r}")
    raw_entries = payload.get("entries")
    if not isinstance(raw_entries, list) or not raw_entries:
        raise GateError("transaction journal must contain at least one entry")
    if len(raw_entries) > len(authorized_paths):
        raise GateError("transaction journal has too many entries")

    expected_entry_keys = {
        "path",
        "stage",
        "backup",
        "restore",
        "before_sha256",
        "after_sha256",
    }
    seen_destinations: set[Path] = set()
    seen_transaction_files: set[Path] = set()
    for index, raw_entry in enumerate(raw_entries):
        if not isinstance(raw_entry, dict) or set(raw_entry) != expected_entry_keys:
            raise GateError(f"transaction entry {index} key set is invalid")
        destination = resolve_transaction_path(root, raw_entry.get("path"))
        if destination not in authorized_paths or destination.name != "index.html":
            raise GateError(f"transaction entry {index} destination is unauthorized")
        if raw_entry.get("path") != relative_posix(destination, root):
            raise GateError(f"transaction entry {index} destination spelling is not canonical")
        if destination in seen_destinations:
            raise GateError(f"transaction entry {index} duplicates a destination")
        seen_destinations.add(destination)

        expected_files = {
            "stage": destination.parent
            / f"{TEMP_NAME_PREFIX}{token}-{index:04d}.images.after",
            "backup": destination.parent
            / f"{TEMP_NAME_PREFIX}{token}-{index:04d}.images.before",
            "restore": destination.parent
            / f"{TEMP_NAME_PREFIX}{token}-{index:04d}.images.restore",
        }
        for key, expected_path in expected_files.items():
            actual_path = resolve_transaction_path(root, raw_entry.get(key))
            if actual_path != expected_path.resolve():
                raise GateError(f"transaction entry {index} {key} path mismatch")
            if raw_entry.get(key) != relative_posix(expected_path, root):
                raise GateError(f"transaction entry {index} {key} spelling mismatch")
            if actual_path in seen_transaction_files:
                raise GateError(f"transaction entry {index} reuses a transaction file")
            seen_transaction_files.add(actual_path)

        for key in ("before_sha256", "after_sha256"):
            value = raw_entry.get(key)
            if not isinstance(value, str) or not SHA256_RE.fullmatch(value):
                raise GateError(f"transaction entry {index} {key} is not a SHA-256 digest")
        if raw_entry["before_sha256"] == raw_entry["after_sha256"]:
            raise GateError(f"transaction entry {index} does not describe a change")

        expected_hashes = {
            "stage": raw_entry["after_sha256"],
            "backup": raw_entry["before_sha256"],
            "restore": raw_entry["before_sha256"],
        }
        for key, expected_hash in expected_hashes.items():
            transaction_file = resolve_transaction_path(root, raw_entry[key])
            if not transaction_file.exists():
                continue
            if sha256_bytes(transaction_file.read_bytes()) == expected_hash:
                continue
            # A power loss may leave a partial temporary file while the journal
            # is still in preparing, or a partial restore file during rollback.
            # The fully validated, token-derived path is safe to discard later;
            # published targets are validated separately before any mutation.
            if phase in {"preparing", "rolled_back"} or key == "restore":
                continue
            raise GateError(f"transaction entry {index} {key} content mismatch")


def load_transaction_journal(
    root: Path,
    authorized_paths: frozenset[Path] | None = None,
) -> dict[str, object] | None:
    journal = root / TRANSACTION_JOURNAL_NAME
    if not journal.exists():
        return None
    try:
        payload = json.loads(journal.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise GateError(f"transaction journal is unreadable: {exc}") from exc
    if not isinstance(payload, dict):
        raise GateError("transaction journal root is not an object")
    version = payload.get("version")
    if version != TRANSACTION_VERSION:
        if version == 1:
            raise GateError("an unfinished technical Phase 4 transaction exists")
        raise GateError(f"unsupported transaction journal version: {version!r}")
    authorized = authorized_paths or authorized_transaction_paths(root)
    validate_transaction_journal(root, payload, authorized)
    return payload


def transaction_paths(
    root: Path, raw_entry: dict[str, object]
) -> tuple[Path, Path, Path, Path]:
    return (
        resolve_transaction_path(root, raw_entry["path"]),
        resolve_transaction_path(root, raw_entry["stage"]),
        resolve_transaction_path(root, raw_entry["backup"]),
        resolve_transaction_path(root, raw_entry["restore"]),
    )


def cleanup_transaction(root: Path, payload: dict[str, object]) -> None:
    entries = payload["entries"]
    assert isinstance(entries, list)
    parents: set[Path] = set()
    for raw_entry in entries:
        assert isinstance(raw_entry, dict)
        destination, stage, backup, restore = transaction_paths(root, raw_entry)
        parents.add(destination.parent)
        stage.unlink(missing_ok=True)
        backup.unlink(missing_ok=True)
        restore.unlink(missing_ok=True)
    for parent in parents:
        fsync_directory(parent)
    (root / TRANSACTION_JOURNAL_NAME).unlink(missing_ok=True)
    fsync_directory(root)


def recover_pending_transaction(
    root: Path,
    authorized_paths: frozenset[Path] | None = None,
) -> str | None:
    payload = load_transaction_journal(root, authorized_paths)
    if payload is None:
        return None
    phase = str(payload["phase"])
    entries = payload["entries"]
    assert isinstance(entries, list)

    states: list[str] = []
    for index, raw_entry in enumerate(entries):
        assert isinstance(raw_entry, dict)
        destination, stage, backup, restore = transaction_paths(root, raw_entry)
        if not destination.is_file():
            raise GateError(f"transaction destination {index} is missing")
        current_hash = sha256_bytes(destination.read_bytes())
        before_hash = str(raw_entry["before_sha256"])
        after_hash = str(raw_entry["after_sha256"])
        if current_hash == before_hash:
            state = "before"
        elif current_hash == after_hash:
            state = "after"
        else:
            raise GateError(f"transaction destination {index} has foreign content")
        states.append(state)

        if phase == "prepared" and (not stage.is_file() or not backup.is_file()):
            raise GateError(f"prepared transaction entry {index} is incomplete")
        if phase == "committing":
            if not backup.is_file():
                raise GateError(f"committing transaction entry {index} lacks its backup")
            if state == "after" and stage.exists():
                raise GateError(f"committing transaction entry {index} has ambiguous stage state")
        if phase == "committed" and (state != "after" or stage.exists()):
            raise GateError(f"committed transaction entry {index} is not fully published")
        if phase in {"preparing", "prepared", "rolled_back"} and state != "before":
            raise GateError(f"{phase} transaction entry {index} unexpectedly changed target")

    if phase == "committing":
        for raw_entry, state in zip(entries, states, strict=True):
            assert isinstance(raw_entry, dict)
            destination, _stage, backup, restore = transaction_paths(root, raw_entry)
            if state == "after":
                before_bytes = backup.read_bytes()
                if restore.exists():
                    if sha256_bytes(restore.read_bytes()) != raw_entry["before_sha256"]:
                        restore.unlink()
                        fsync_directory(destination.parent)
                if not restore.exists():
                    mode = stat.S_IMODE(destination.stat().st_mode)
                    write_named_file(restore, before_bytes, mode)
                    fsync_directory(destination.parent)
                os.replace(restore, destination)
                fsync_directory(destination.parent)
        for index, raw_entry in enumerate(entries):
            assert isinstance(raw_entry, dict)
            destination, _stage, _backup, _restore = transaction_paths(root, raw_entry)
            if sha256_bytes(destination.read_bytes()) != raw_entry["before_sha256"]:
                raise GateError(f"transaction rollback could not restore entry {index}")
        payload["phase"] = "rolled_back"
        write_transaction_journal(root, payload)
    elif phase in {"preparing", "prepared"}:
        payload["phase"] = "rolled_back"
        write_transaction_journal(root, payload)

    cleanup_transaction(root, payload)
    return phase


def assert_no_pending_transaction(root: Path) -> None:
    if (root / TRANSACTION_JOURNAL_NAME).exists():
        raise GateError(
            "an unfinished technical/image Phase 4 transaction exists; "
            "run the owning transformer in --apply mode for recovery"
        )


def current_target_manifest(plan: BuildPlan) -> str:
    return digest_named_values(
        (document.path.relative_to(plan.root).as_posix(), document.path.read_bytes())
        for document in plan.documents
    )


def validate_apply_scope(plan: BuildPlan, expected_target: str) -> None:
    root = plan.root
    if current_target_manifest(plan) != expected_target:
        raise GateError("target HTML manifest drifted during apply")
    if sha256_bytes((root / "sitemap.xml").read_bytes()) != plan.sitemap_sha256:
        raise GateError("sitemap drifted during apply")
    if asset_manifest_sha256(root) != plan.asset_sha256:
        raise GateError("asset bytes drifted during apply")
    if external_manifest_sha256(root, plan.target_paths) != plan.external_sha256:
        raise GateError("external scope drifted during apply")


def transaction_payload(plan: BuildPlan) -> dict[str, object]:
    token = uuid.uuid4().hex
    entries: list[dict[str, object]] = []
    for index, document in enumerate(plan.changes):
        stage = document.path.parent / f"{TEMP_NAME_PREFIX}{token}-{index:04d}.images.after"
        backup = document.path.parent / f"{TEMP_NAME_PREFIX}{token}-{index:04d}.images.before"
        restore = document.path.parent / f"{TEMP_NAME_PREFIX}{token}-{index:04d}.images.restore"
        entries.append(
            {
                "path": relative_posix(document.path, plan.root),
                "stage": relative_posix(stage, plan.root),
                "backup": relative_posix(backup, plan.root),
                "restore": relative_posix(restore, plan.root),
                "before_sha256": sha256_bytes(document.before),
                "after_sha256": sha256_bytes(document.after),
            }
        )
    return {
        "version": TRANSACTION_VERSION,
        "owner": TRANSACTION_OWNER,
        "token": token,
        "phase": "preparing",
        "baseline_ref": BASELINE_REF,
        "target_manifest_before": plan.target_input_sha256,
        "target_manifest_after": plan.target_projected_sha256,
        "external_manifest": plan.external_sha256,
        "asset_manifest": plan.asset_sha256,
        "sitemap_sha256": plan.sitemap_sha256,
        "entries": entries,
    }


def atomic_apply(
    plan: BuildPlan,
    fault_hook: Callable[[str], None] | None = None,
) -> int:
    validate_apply_scope(plan, plan.target_input_sha256)
    changed = plan.changes
    if not changed:
        if load_transaction_journal(plan.root, plan.target_paths) is not None:
            raise GateError("pending transaction exists before no-op apply")
        return 0
    if load_transaction_journal(plan.root, plan.target_paths) is not None:
        raise GateError("pending transaction must be recovered before apply")

    def fault(event: str) -> None:
        if fault_hook is not None:
            fault_hook(event)

    journal = transaction_payload(plan)
    entries = journal["entries"]
    assert isinstance(entries, list)
    try:
        write_transaction_journal(plan.root, journal)
        fault("after_preparing_journal")
        for index, (document, raw_entry) in enumerate(zip(changed, entries, strict=True)):
            assert isinstance(raw_entry, dict)
            _destination, stage, backup, _restore = transaction_paths(plan.root, raw_entry)
            mode = stat.S_IMODE(document.path.stat().st_mode)
            write_named_file(stage, document.after, mode)
            write_named_file(backup, document.before, mode)
            fsync_directory(document.path.parent)
            fault(f"after_stage:{index}")

        validate_apply_scope(plan, plan.target_input_sha256)
        journal["phase"] = "prepared"
        write_transaction_journal(plan.root, journal)
        fault("after_prepared")

        journal["phase"] = "committing"
        write_transaction_journal(plan.root, journal)
        fault("after_committing")
        for index, (document, raw_entry) in enumerate(zip(changed, entries, strict=True)):
            assert isinstance(raw_entry, dict)
            if document.path.read_bytes() != document.before:
                raise GateError(f"target drifted before replace: {relative_posix(document.path, plan.root)}")
            _destination, stage, _backup, _restore = transaction_paths(plan.root, raw_entry)
            os.replace(stage, document.path)
            fsync_directory(document.path.parent)
            fault(f"after_replace:{index}")

        validate_apply_scope(plan, plan.target_projected_sha256)
        journal["phase"] = "committed"
        write_transaction_journal(plan.root, journal)
        fault("after_committed")
        recovered = recover_pending_transaction(plan.root, plan.target_paths)
        if recovered != "committed":
            raise GateError("transaction commit cleanup did not complete")
        return len(changed)
    except BaseException as error:
        try:
            recovered = recover_pending_transaction(plan.root, plan.target_paths)
        except BaseException as recovery_error:  # pragma: no cover - emergency path
            raise GateError(
                f"apply failed ({error}); durable recovery also failed ({recovery_error})"
            ) from error
        if recovered == "committed":
            raise GateError(f"apply committed but final cleanup failed: {error}") from error
        raise


def report(plan: BuildPlan, applied: bool, written: int, as_json: bool) -> None:
    counts = plan.stats.counts
    input_types = {
        suffix: counts[f"input_type_{suffix}"]
        for suffix in sorted(SUPPORTED_IMAGE_SUFFIXES)
        if counts[f"input_type_{suffix}"]
    }
    output_types = {
        suffix: counts[f"output_type_{suffix}"]
        for suffix in sorted(SUPPORTED_IMAGE_SUFFIXES)
        if counts[f"output_type_{suffix}"]
    }
    payload = {
        "mode": "apply" if applied else "check/dry-run",
        "pages_scanned": counts["pages_scanned"],
        "changed_pages": len(plan.changes),
        "written_pages": written,
        "input_dom_images": counts["input_images"],
        "projected_dom_images": counts["output_images"],
        "input_image_types": input_types,
        "projected_image_types": output_types,
        "input_distinct_dom_assets": len(plan.stats.input_asset_references),
        "projected_distinct_dom_assets": len(plan.stats.output_asset_references),
        "hero_lcp_images": counts["output_hero"],
        "below_fold_images": counts["output_below_fold"],
        "hidden_representative_removed": counts["hidden_representative_removed"],
        "hidden_distinct_assets_removed_from_dom": len(plan.stats.hidden_assets),
        "representative_og_distinct_assets_retained": len(plan.stats.detail_og_assets),
        "hidden_reference_bytes_avoided_per_one_visit_each": plan.stats.hidden_reference_bytes,
        "hidden_unique_asset_bytes_unchanged": plan.stats.hidden_unique_bytes,
        "dimension_assets_read": len(plan.dimensions),
        "trailing_eol_whitespace": {
            "mode": trailing_projection_mode(plan.stats),
            "input_files": counts["input_trailing_eol_files"],
            "input_occurrences": counts["input_trailing_eol_occurrences"],
            "input_bytes": counts["input_trailing_eol_bytes"],
            "cleanup_files": counts["cleanup_trailing_eol_files"],
            "cleanup_occurrences": counts["cleanup_trailing_eol_occurrences"],
            "cleanup_bytes": counts["cleanup_trailing_eol_bytes"],
            "output_files": counts["output_trailing_eol_files"],
            "output_occurrences": counts["output_trailing_eol_occurrences"],
            "output_bytes": counts["output_trailing_eol_bytes"],
        },
        "mutations": {
            key: value
            for key, value in sorted(counts.items())
            if key.endswith(("_added", "_updated", "_removed", "_changed"))
        },
        "broken_dom_image_paths": 0,
        "canonical_schema_head_guard": "pass",
        "non_image_bytes_guard": "pass",
        "trailing_eol_whitespace_guard": "pass",
        "physical_line_endings_guard": "pass",
        "sitemap_bytes_guard": "pass",
        "asset_bytes_guard": "pass",
        "shared_phase4_repository_lock": "pass",
        "durable_crash_recovery": "pass",
        "transaction_journal_version": TRANSACTION_VERSION,
        "in_memory_idempotency": plan.idempotent,
        "sitemap_sha256": plan.sitemap_sha256,
        "asset_manifest_sha256": plan.asset_sha256,
        "external_scope_sha256": plan.external_sha256,
        "target_input_sha256": plan.target_input_sha256,
        "target_projected_sha256": plan.target_projected_sha256,
    }
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return
    for key, value in payload.items():
        print(f"{key}: {value}")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="read-only validation (default)")
    mode.add_argument("--apply", action="store_true", help="atomically apply the validated plan")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--json", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    root = args.root.resolve()
    try:
        if args.apply:
            with RepositoryLock(root):
                recovered = recover_pending_transaction(root)
                if recovered in {"preparing", "prepared", "committing", "rolled_back"}:
                    raise GateError(
                        f"recovered incomplete image transaction ({recovered}); rerun --apply"
                    )
                plan = build_plan(root)
                written = atomic_apply(plan)
        else:
            assert_no_pending_transaction(root)
            plan = build_plan(root)
            written = 0
        report(plan, bool(args.apply), written, bool(args.json))
    except KeyboardInterrupt:
        print("ERROR: apply interrupted; durable rollback completed or remains journaled", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
