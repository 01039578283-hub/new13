#!/usr/bin/env python3
"""Add deterministic anchor contents to national-academy detail pages.

Only the 3,339 regional detail pages in the nine national-academy categories
are changed.  Labels come from each page's existing H2 headings after the
media section.  The national and category hubs are deliberately excluded.

Run this idempotent postprocessor after any bulk copy refresh.
"""

from __future__ import annotations

import argparse
import html
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
NATIONAL_ROOT = ROOT / "전국학원"
CATEGORIES = (
    "고등수학학원",
    "고등영어학원",
    "수학학원",
    "영수학원",
    "영어학원",
    "중등수학학원",
    "중등영어학원",
    "초등수학학원",
    "초등영어학원",
)
EXPECTED_PER_CATEGORY = 371
EXPECTED_TARGETS = 8

STYLE_MARKER = "<!-- national-page-anchor-toc:style:start -->"
STYLE_END_MARKER = "<!-- national-page-anchor-toc:style:end -->"
STYLE_HREF = "/assets/national-anchor-toc.css"
STYLE_LINK = f'<link rel="stylesheet" href="{STYLE_HREF}">'
STYLE_BLOCK = STYLE_MARKER + STYLE_LINK + STYLE_END_MARKER
TOC_START = "<!-- national-page-anchor-toc:start -->"
TOC_END = "<!-- national-page-anchor-toc:end -->"
TARGET_CLASS = "national-page-anchor-target"

STYLE_BLOCK_RE = re.compile(
    rf"{re.escape(STYLE_MARKER)}"
    rf"<link\s+rel=[\"']stylesheet[\"']\s+"
    rf"href=[\"']{re.escape(STYLE_HREF)}[\"']\s*>"
    rf"{re.escape(STYLE_END_MARKER)}",
    re.IGNORECASE,
)
SITE_CSS_RE = re.compile(
    r'<link\s+rel=["\']stylesheet["\']\s+'
    r'href=["\'][^"\']*assets/styles\.css(?:\?v=[^"\']*)?["\']\s*>',
    re.IGNORECASE,
)
TOC_BLOCK_RE = re.compile(
    rf"{re.escape(TOC_START)}\r?\n.*?"
    rf"{re.escape(TOC_END)}\r?\n[ \t]*",
    re.IGNORECASE | re.DOTALL,
)
TOC_CAPTURE_RE = re.compile(
    rf"{re.escape(TOC_START)}.*?{re.escape(TOC_END)}",
    re.IGNORECASE | re.DOTALL,
)
HERO_RE = re.compile(
    r'<section\b(?=[^>]*\bclass=["\'][^"\']*\bacademy-hero\b[^"\']*["\'])'
    r"[^>]*>.*?</section>",
    re.IGNORECASE | re.DOTALL,
)
MEDIA_RE = re.compile(
    r'<section\b(?=[^>]*\bclass=["\'][^"\']*'
    r'\bacademy-media-section\b[^"\']*["\'])[^>]*>',
    re.IGNORECASE,
)
H2_RE = re.compile(
    r"<h2\b(?P<attrs>[^>]*)>(?P<body>.*?)</h2>",
    re.IGNORECASE | re.DOTALL,
)
ID_RE = re.compile(r'\bid\s*=\s*(["\'])(?P<id>[^"\']+)\1', re.IGNORECASE)
CLASS_RE = re.compile(
    r'\bclass\s*=\s*(["\'])(?P<classes>[^"\']*)\1',
    re.IGNORECASE,
)
ANY_ID_RE = re.compile(
    r'\bid\s*=\s*(["\'])(?P<id>[^"\']+)\1',
    re.IGNORECASE,
)
TOC_LINK_RE = re.compile(
    r'<a\s+href=["\']#(?P<id>[^"\']+)["\']>.*?'
    r'<span\s+class=["\']national-page-toc-text["\']>'
    r"(?P<label>.*?)</span>\s*</a>",
    re.IGNORECASE | re.DOTALL,
)


@dataclass(frozen=True)
class TocTarget:
    target_id: str
    text: str


def visible_text(fragment: str) -> str:
    value = re.sub(r"<[^>]+>", " ", fragment)
    return " ".join(html.unescape(value).split())


def detect_newline(source: str) -> str:
    if "\r\n" in source:
        if "\n" in source.replace("\r\n", ""):
            raise ValueError("Mixed newline styles")
        return "\r\n"
    return "\n"


def detail_pages() -> list[Path]:
    actual = tuple(sorted(path.name for path in NATIONAL_ROOT.iterdir() if path.is_dir()))
    if actual != tuple(sorted(CATEGORIES)):
        raise ValueError(f"National category folders differ from expected: {actual}")

    pages: list[Path] = []
    for category in CATEGORIES:
        category_pages = sorted(
            path / "index.html"
            for path in (NATIONAL_ROOT / category).iterdir()
            if path.is_dir() and (path / "index.html").is_file()
        )
        if len(category_pages) != EXPECTED_PER_CATEGORY:
            raise ValueError(
                f"{category}: expected {EXPECTED_PER_CATEGORY} detail pages, "
                f"found {len(category_pages)}"
            )
        pages.extend(category_pages)
    return sorted(pages, key=lambda path: path.as_posix())


def ensure_style_link(source: str) -> str:
    if STYLE_MARKER in source:
        if STYLE_END_MARKER not in source:
            raise ValueError("Existing anchor stylesheet end marker is missing")
        if len(STYLE_BLOCK_RE.findall(source)) != 1:
            raise ValueError("Existing anchor stylesheet marker is malformed")
        return source
    matches = list(SITE_CSS_RE.finditer(source))
    if len(matches) != 1:
        raise ValueError(f"Main stylesheet link count is {len(matches)}")
    match = matches[0]
    return source[: match.end()] + STYLE_BLOCK + source[match.end() :]


def add_target_attributes(opening: str, target_id: str) -> str:
    id_match = ID_RE.search(opening)
    if id_match:
        if id_match.group("id") != target_id:
            raise ValueError(
                f"H2 {target_id} already has unexpected id {id_match.group('id')!r}"
            )
    else:
        opening = opening[:-1] + f' id="{target_id}">'

    class_match = CLASS_RE.search(opening)
    if class_match:
        classes = class_match.group("classes").split()
        if TARGET_CLASS not in classes:
            opening = (
                opening[: class_match.start("classes")]
                + " ".join([*classes, TARGET_CLASS])
                + opening[class_match.end("classes") :]
            )
    else:
        opening = opening[:-1] + f' class="{TARGET_CLASS}">'
    return opening


def strip_owned_heading_attributes(source: str) -> str:
    replacements: list[tuple[int, int, str]] = []
    for heading in H2_RE.finditer(source):
        opening_end = heading.start("body")
        opening = source[heading.start() : opening_end]
        cleaned = re.sub(
            r'\s+id=["\']national-section-\d{2}["\']',
            "",
            opening,
            count=1,
            flags=re.IGNORECASE,
        )
        class_match = CLASS_RE.search(cleaned)
        if class_match:
            classes = [
                name
                for name in class_match.group("classes").split()
                if name != TARGET_CLASS
            ]
            if classes:
                cleaned = (
                    cleaned[: class_match.start("classes")]
                    + " ".join(classes)
                    + cleaned[class_match.end("classes") :]
                )
            else:
                left = cleaned[: class_match.start()].rstrip()
                right = cleaned[class_match.end() :]
                cleaned = left + (" " if right and not right.startswith(">") else "") + right
        cleaned = re.sub(r"<h2\s+>", "<h2>", cleaned, count=1, flags=re.IGNORECASE)
        if cleaned != opening:
            replacements.append((heading.start(), opening_end, cleaned))
    for start, end, replacement in reversed(replacements):
        source = source[:start] + replacement + source[end:]
    return source


def media_opening(source: str) -> re.Match[str]:
    matches = list(MEDIA_RE.finditer(source))
    if len(matches) != 1:
        raise ValueError(f"Academy media section count is {len(matches)}")
    return matches[0]


def ensure_heading_targets(source: str, media_position: int) -> tuple[str, list[TocTarget]]:
    matches = [heading for heading in H2_RE.finditer(source) if heading.start() > media_position]
    if len(matches) != EXPECTED_TARGETS:
        raise ValueError(f"Expected {EXPECTED_TARGETS} H2 headings after media, found {len(matches)}")

    replacements: list[tuple[int, int, str]] = []
    targets: list[TocTarget] = []
    for index, heading in enumerate(matches, start=1):
        target_id = f"national-section-{index:02d}"
        label = visible_text(heading.group("body"))
        if not label:
            raise ValueError(f"Empty H2 heading at position {index}")
        opening_end = heading.start("body")
        opening = source[heading.start() : opening_end]
        enhanced = add_target_attributes(opening, target_id)
        if enhanced != opening:
            replacements.append((heading.start(), opening_end, enhanced))
        targets.append(TocTarget(target_id, label))
    for start, end, replacement in reversed(replacements):
        source = source[:start] + replacement + source[end:]
    return source, targets


def toc_markup(targets: list[TocTarget], indent: str, newline: str) -> str:
    child = indent + "  "
    grandchild = child + "  "
    item_indent = grandchild + "  "
    lines = [
        TOC_START,
        indent + '<nav class="national-page-toc" aria-labelledby="national-page-toc-title">',
        child + '<div class="national-page-toc-panel">',
        grandchild + '<div class="national-page-toc-heading">',
        item_indent + '<p class="eyebrow">PAGE CONTENTS</p>',
        item_indent + '<strong id="national-page-toc-title">페이지 목차</strong>',
        item_indent + '<p>원하는 항목을 누르면 해당 내용으로 바로 이동합니다.</p>',
        grandchild + "</div>",
        grandchild + '<ol class="national-page-toc-list">',
    ]
    for index, target in enumerate(targets, start=1):
        lines.append(
            item_indent
            + "<li>"
            + f'<a href="#{html.escape(target.target_id, quote=True)}">'
            + f'<span class="national-page-toc-number" aria-hidden="true">{index:02d}</span>'
            + f'<span class="national-page-toc-text">{html.escape(target.text)}</span>'
            + "</a></li>"
        )
    lines.extend(
        [
            grandchild + "</ol>",
            child + "</div>",
            indent + "</nav>",
            indent + TOC_END,
        ]
    )
    return newline.join(lines) + newline + indent


def render_page(original: str) -> tuple[str, int]:
    if original.count(TOC_START) != original.count(TOC_END):
        raise ValueError("Unbalanced anchor TOC markers")
    if original.count(TOC_START) > 1:
        raise ValueError("Multiple anchor TOC blocks found")

    source = TOC_BLOCK_RE.sub("", original, count=1)
    newline = detect_newline(source)
    source = ensure_style_link(source)
    source = strip_owned_heading_attributes(source)
    media = media_opening(source)
    source, targets = ensure_heading_targets(source, media.start())
    media = media_opening(source)

    line_start = source.rfind(newline, 0, media.start())
    line_start = 0 if line_start < 0 else line_start + len(newline)
    line_prefix = source[line_start : media.start()]
    # Some source families are minified around the media section while others
    # put it on an indented line.  In both cases the owned block is inserted
    # immediately before the section and strips back to the exact source.
    indent = line_prefix if not line_prefix.strip() else ""

    rendered = source[: media.start()] + toc_markup(targets, indent, newline) + source[media.start() :]
    return rendered, len(targets)


def current_targets(source: str) -> list[TocTarget]:
    media = media_opening(source)
    targets: list[TocTarget] = []
    for heading in H2_RE.finditer(source):
        if heading.start() >= media.start():
            continue
        own_id = ID_RE.search(heading.group("attrs"))
        class_match = CLASS_RE.search(heading.group("attrs"))
        classes = class_match.group("classes").split() if class_match else []
        if (own_id and own_id.group("id").startswith("national-section-")) or TARGET_CLASS in classes:
            raise ValueError("A heading before the media section has owned anchor attributes")

    headings = [heading for heading in H2_RE.finditer(source) if heading.start() > media.start()]
    if len(headings) != EXPECTED_TARGETS:
        raise ValueError(f"Expected {EXPECTED_TARGETS} target headings, found {len(headings)}")
    for index, heading in enumerate(headings, start=1):
        target_id = f"national-section-{index:02d}"
        id_match = ID_RE.search(heading.group("attrs"))
        class_match = CLASS_RE.search(heading.group("attrs"))
        classes = class_match.group("classes").split() if class_match else []
        if not id_match or id_match.group("id") != target_id:
            raise ValueError(f"H2 {index} target id is missing or incorrect")
        if TARGET_CLASS not in classes:
            raise ValueError(f"H2 {index} target class is missing")
        targets.append(TocTarget(target_id, visible_text(heading.group("body"))))
    return targets


def validate_page(source: str) -> list[str]:
    errors: list[str] = []
    if (
        source.count(STYLE_MARKER) != 1
        or source.count(STYLE_END_MARKER) != 1
        or source.count(STYLE_HREF) != 1
    ):
        errors.append("Anchor stylesheet marker or link count is not exactly one")
    if source.count(TOC_START) != 1 or source.count(TOC_END) != 1:
        errors.append("Anchor TOC marker count is not exactly one")
    toc = TOC_CAPTURE_RE.search(source)
    if not toc:
        errors.append("Anchor TOC block missing")
        return errors

    try:
        targets = current_targets(source)
    except Exception as exc:  # noqa: BLE001
        errors.append(str(exc))
        return errors
    expected = [(target.target_id, target.text) for target in targets]
    links = [
        (match.group("id"), visible_text(match.group("label")))
        for match in TOC_LINK_RE.finditer(toc.group(0))
    ]
    if links != expected:
        errors.append("Anchor links or labels do not match the page H2 headings")

    all_ids = [match.group("id") for match in ANY_ID_RE.finditer(source)]
    duplicates = sorted(target_id for target_id, count in Counter(all_ids).items() if count > 1)
    if duplicates:
        errors.append(f"Duplicate IDs found: {duplicates}")
    if all_ids.count("national-page-toc-title") != 1:
        errors.append("TOC title ID count is not exactly one")
    for target_id, _ in links:
        if all_ids.count(target_id) != 1:
            errors.append(f"Anchor target count for {target_id!r} is {all_ids.count(target_id)}")

    hero = HERO_RE.search(source)
    media = media_opening(source)
    if not hero or not (hero.end() < toc.start() < media.start()):
        errors.append("Anchor TOC is not between the hero and media section")
    if toc.end() > media.start():
        errors.append("Anchor TOC overlaps the media section")
    elif source[toc.end() : media.start()].strip():
        errors.append("Unexpected content appears between TOC and media section")
    return errors


def strip_enhancement(source: str) -> str:
    source, style_count = STYLE_BLOCK_RE.subn("", source, count=1)
    source, toc_count = TOC_BLOCK_RE.subn("", source, count=1)
    if style_count != 1 or toc_count != 1:
        raise ValueError(f"Could not strip owned enhancement style={style_count} toc={toc_count}")
    return strip_owned_heading_attributes(source)


def validate_hubs() -> list[str]:
    hubs = [NATIONAL_ROOT / "index.html"] + [
        NATIONAL_ROOT / category / "index.html" for category in CATEGORIES
    ]
    errors: list[str] = []
    for hub in hubs:
        source = hub.read_text(encoding="utf-8")
        if any(
            token in source
            for token in (STYLE_MARKER, STYLE_END_MARKER, STYLE_HREF, TOC_START, TOC_END)
        ):
            errors.append("Hub unexpectedly contains a detail TOC: " + hub.relative_to(ROOT).as_posix())
    return errors


def enhance_detail_html(source: str) -> str:
    rendered, _ = render_page(source)
    errors = validate_page(rendered)
    if errors:
        raise ValueError("; ".join(errors))
    return rendered


def process(write: bool) -> int:
    try:
        pages = detail_pages()
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR {exc}")
        return 1

    changed = 0
    validated = 0
    distribution: Counter[int] = Counter()
    category_counts: Counter[str] = Counter()
    failures = validate_hubs()
    if not (ROOT / "assets" / "national-anchor-toc.css").is_file():
        failures.append("Anchor TOC stylesheet is missing")

    for path in pages:
        try:
            raw = path.read_bytes()
            original = raw.decode("utf-8")
            rendered, target_count = render_page(original)
            page_errors = validate_page(rendered)
            if page_errors:
                raise ValueError("; ".join(page_errors))
            changed += int(rendered != original)
            distribution[target_count] += 1
            category_counts[path.relative_to(NATIONAL_ROOT).parts[0]] += 1
            validated += 1
        except Exception as exc:  # noqa: BLE001
            failures.append(f"{path.relative_to(ROOT).as_posix()}: {exc}")

    print(f"pages={len(pages)} validated={validated}")
    print(
        "toc_link_distribution="
        + ",".join(f"{count}:{page_count}" for count, page_count in sorted(distribution.items()))
    )
    print("toc_links_total=" + str(sum(count * page_count for count, page_count in distribution.items())))
    print(
        "categories="
        + ",".join(f"{name}:{count}" for name, count in sorted(category_counts.items()))
    )
    print(f"changed={changed} mode={'write' if write else 'check'}")
    for failure in failures[:50]:
        print("ERROR", failure)
    if len(failures) > 50:
        print(f"ERROR ... and {len(failures) - 50} more")
    if failures:
        return 1
    if not write and changed:
        print("ERROR check mode found pages that need updating")
        return 1

    if write:
        for path in pages:
            original = path.read_bytes().decode("utf-8")
            rendered, _ = render_page(original)
            if rendered != original:
                path.write_bytes(rendered.encode("utf-8"))
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true", help="Apply or refresh TOCs")
    mode.add_argument("--check", action="store_true", help="Validate idempotence")
    args = parser.parse_args()
    raise SystemExit(process(write=args.write))


if __name__ == "__main__":
    main()
