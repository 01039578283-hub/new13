"""Source-gated phase-1 JSON-LD repair for 소수정예학원.com.

Run ``smallclass_phase1_content.py --apply`` first.  This fixer then treats
the content phase's visible grade/school/status/FAQ markup as a hard input
contract.  Its default mode is read-only; ``--apply`` atomically replaces
only the payload of the single existing JSON-LD script in each target HTML.
The repository ``tmp/`` tree is excluded from both discovery and hashing.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import os
import re
import tempfile
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable
from urllib.parse import unquote, urljoin, urlsplit


ROOT = Path(__file__).resolve().parents[1]
COMMON = ROOT.parent / "참고자료" / "공통자료"
CENTER_CSV = COMMON / "센터정보 정리.csv"

BASE_URL = "https://xn--9l4b2jy4h4wan0chw4b.com"
ROOT_URL = f"{BASE_URL}/"
ROOT_ORGANIZATION_ID = f"{BASE_URL}/#organization"
ROOT_WEBSITE_ID = f"{BASE_URL}/#website"
ROOT_WEBPAGE_ID = f"{BASE_URL}/#webpage"

EXPECTED_LOCALITIES = 371
EXPECTED_PHYSICAL_CENTERS = 188
EXPECTED_DETAIL_PAGES = EXPECTED_LOCALITIES * 9
EXPECTED_NON_DETAIL_PAGES = 39
EXPECTED_UNSUPPORTED_PAGES = 106
EXPECTED_UNSUPPORTED_BY_PROFILE = {
    "고등수학학원": 17,
    "고등영어학원": 9,
    "수학학원": 13,
    "영수학원": 17,
    "영어학원": 8,
    "중등수학학원": 13,
    "중등영어학원": 8,
    "초등수학학원": 13,
    "초등영어학원": 8,
}

GRAPH_RE = re.compile(
    r'(<script\b(?=[^>]*\btype=["\']application/ld\+json["\'])[^>]*>)'
    r'(.*?)(</script\s*>)',
    re.IGNORECASE | re.DOTALL,
)
CANONICAL_RE = re.compile(
    r'<link\b(?=[^>]*\brel=["\']canonical["\'])[^>]*'
    r'\bhref=["\']([^"\']+)["\'][^>]*>',
    re.IGNORECASE,
)
MANUSCRIPT_META_RE = re.compile(
    r"검색|(?<![A-Za-z])SEO(?![A-Za-z])|키워드",
    re.IGNORECASE,
)
IRRELEVANT_SEED_RE = re.compile(
    r"학원\s*(?:"
    r"개원|창업|전자\s*계약|매니저|공지|운영(?:자)?|"
    r"매출\s*관리|수납\s*관리|고객\s*관리(?:\s*시스템)?|회원\s*관리|"
    r"수강생\s*관리|문서\s*관리|데이터\s*관리|예약\s*관리|상담\s*관리|"
    r"결제\s*(?:관리|시스템)|미납\s*관리|관리\s*(?:프로그램|솔루션|앱)|"
    r"출결\s*앱|직원|상담\s*직원|코디네이터|데스크|행정|브랜드|"
    r"프로모션|이벤트|모집|온라인\s*등록|재\s*등록|휴원|이전"
    r")",
    re.IGNORECASE,
)


class GateError(RuntimeError):
    pass


@dataclass(frozen=True)
class Profile:
    slug: str
    category: str
    subjects: tuple[str, ...]
    grade_prefix: str
    school_levels: tuple[str, ...]
    school_label: str

    @property
    def level_label(self) -> str:
        return {"초": "초등", "중": "중등", "고": "고등", "": ""}[self.grade_prefix]


PROFILES = (
    Profile("고등수학학원", "고등 수학학원", ("수학",), "고", ("고",), "고등학교 참고"),
    Profile("고등영어학원", "고등 영어학원", ("영어",), "고", ("고",), "고등학교 참고"),
    Profile("수학학원", "수학학원", ("수학",), "", ("고", "중", "초"), "제공 학교 참고"),
    Profile("영수학원", "영수학원", ("영어", "수학"), "", ("고", "중", "초"), "제공 학교 참고"),
    Profile("영어학원", "영어학원", ("영어",), "", ("고", "중", "초"), "제공 학교 참고"),
    Profile("중등수학학원", "중등 수학학원", ("수학",), "중", ("중",), "중학교 참고"),
    Profile("중등영어학원", "중등 영어학원", ("영어",), "중", ("중",), "중학교 참고"),
    Profile("초등수학학원", "초등 수학학원", ("수학",), "초", ("초",), "초등학교 참고"),
    Profile("초등영어학원", "초등 영어학원", ("영어",), "초", ("초",), "초등학교 참고"),
)
PROFILE_BY_SLUG = {profile.slug: profile for profile in PROFILES}

SUBJECT_COLUMNS = {
    "영어": "가능학년\n(영어)",
    "수학": "가능학년\n(수학)",
}
SUBJECT_CODES = {"영어": "english", "수학": "math"}
SCHOOL_COLUMNS = {
    "초": "타깃학교\n(초)",
    "중": "타깃학교\n(중)",
    "고": "타깃학교\n(고)",
}
SCHOOL_SUFFIX_RE = re.compile(r"(?:초등학교|중학교|고등학교|초|중|고)$")

REGION_NAMES = {
    "서울": "서울특별시",
    "서울특별시": "서울특별시",
    "경기": "경기도",
    "경기도": "경기도",
    "인천": "인천광역시",
    "인천광역시": "인천광역시",
    "부산": "부산광역시",
    "부산광역시": "부산광역시",
    "대구": "대구광역시",
    "대구광역시": "대구광역시",
    "대전": "대전광역시",
    "대전광역시": "대전광역시",
    "광주": "광주광역시",
    "광주광역시": "광주광역시",
    "울산": "울산광역시",
    "울산광역시": "울산광역시",
    "세종": "세종특별자치시",
    "세종시": "세종특별자치시",
    "세종특별자치시": "세종특별자치시",
    "강원": "강원특별자치도",
    "강원도": "강원특별자치도",
    "강원특별자치도": "강원특별자치도",
    "충북": "충청북도",
    "충청북도": "충청북도",
    "충남": "충청남도",
    "충청남도": "충청남도",
    "전북": "전북특별자치도",
    "전라북도": "전북특별자치도",
    "전북특별자치도": "전북특별자치도",
    "전남": "전라남도",
    "전라남도": "전라남도",
    "경북": "경상북도",
    "경상북도": "경상북도",
    "경남": "경상남도",
    "경상남도": "경상남도",
    "제주": "제주특별자치도",
    "제주도": "제주특별자치도",
    "제주특별자치도": "제주특별자치도",
}


@dataclass(frozen=True)
class SourceRow:
    locality: str
    slug: str
    region: str
    district: str
    center_name: str
    fee_url: str
    office_name: str
    registration: str
    address: str
    schools: dict[str, tuple[str, ...]]
    school_legacy: dict[str, tuple[str, ...]]
    grades: dict[str, tuple[str, ...]]

    @property
    def center_key(self) -> str:
        return "|".join((self.center_name, self.address, self.registration))


@dataclass(frozen=True)
class PhysicalCenter:
    key: str
    center_name: str
    address: str
    office_name: str
    registration: str
    areas: tuple[str, ...]
    grades: dict[str, tuple[str, ...]]
    fee_urls: tuple[str, ...]
    address_region: str
    address_locality: str

    @property
    def entity_id(self) -> str:
        token = hashlib.sha256(self.key.encode("utf-8")).hexdigest()[:16]
        return f"{BASE_URL}/#center-{token}"


@dataclass
class Candidate:
    path: Path
    relative_path: str
    original: str
    transformed: str
    source_sha256: str
    non_schema_sha256: str
    canonical: str
    is_detail: bool
    profile: Profile | None = None
    source: SourceRow | None = None
    physical: PhysicalCenter | None = None
    supported: bool | None = None


def clean(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def clean_html(value: str) -> str:
    without_tags = re.sub(r"<[^>]+>", "", value or "")
    return clean(html.unescape(without_tags))


def ordered_unique(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(value for value in values if value))


def list_values(value: str) -> tuple[str, ...]:
    return ordered_unique(
        item.strip()
        for item in re.split(r"[,/\r\n]+", value or "")
        if item.strip()
    )


def split_schools(value: str) -> tuple[str, ...]:
    normalized = clean(value)
    if not normalized or "지역내 모든 고등학교 가능" in normalized:
        return ()
    parts = [
        clean(part)
        for part in re.split(r"[,/·.，\r\n]+", normalized)
        if clean(part)
    ]
    result: list[str] = []
    for part in parts:
        tokens = part.split()
        if len(tokens) > 1 and all(SCHOOL_SUFFIX_RE.search(token) for token in tokens):
            result.extend(tokens)
        else:
            result.append(part)
    return ordered_unique(result)


def locality_slug(value: str) -> str:
    return re.sub(r"\s+", "", value)


def grade_sort_key(value: str) -> tuple[int, str]:
    order = {
        **{f"초{number}": number for number in range(1, 7)},
        **{f"중{number}": 10 + number for number in range(1, 4)},
        **{f"고{number}": 20 + number for number in range(1, 4)},
    }
    return order.get(value, 99), value


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def read_utf8_exact(path: Path) -> str:
    return path.read_bytes().decode("utf-8")


def normalized_url_path(value: str) -> str:
    parsed = urlsplit(value)
    return unquote(parsed.path).rstrip("/") + "/"


def official_address_parts(address: str, areas: tuple[str, ...]) -> tuple[str, str]:
    tokens = address.replace(",", " ").split()
    region = REGION_NAMES.get(tokens[0], "") if tokens else ""
    if not region:
        raise GateError(f"unknown official address region: {address!r}")
    if region == "세종특별자치시":
        matches = [area for area in areas if area.removesuffix("동") in address]
        locality = matches[0] if matches else areas[0]
    elif len(tokens) > 1:
        locality = tokens[1]
    else:
        locality = ""
    if not locality:
        raise GateError(f"official address locality missing: {address!r}")
    return region, locality


def expected_visible_region(source: SourceRow) -> str:
    address_region, _ = official_address_parts(source.address, (source.locality,))
    if source.region in {"충청", "경상", "전라"}:
        display_region = address_region
    else:
        display_region = REGION_NAMES.get(source.region, "")
    if not display_region:
        raise GateError(
            f"unknown visible region for {source.locality}: "
            f"{source.region!r} / {source.address!r}"
        )
    display_district = "" if display_region == "세종특별자치시" else source.district
    display_locality = source.locality
    prefixes = (
        re.sub(r"(?:시|군|구)$", "", clean(source.district)),
        clean(source.region),
    )
    for prefix in dict.fromkeys(value for value in prefixes if value):
        match = re.fullmatch(rf"{re.escape(prefix)}\s+(.+)", clean(source.locality))
        if match:
            display_locality = clean(match.group(1))
            break
    return " ".join(
        value
        for value in (display_region, display_district, display_locality)
        if value
    )


def load_sources() -> tuple[dict[str, SourceRow], dict[str, PhysicalCenter]]:
    if not CENTER_CSV.exists():
        raise GateError(f"authoritative source missing: {CENTER_CSV}")
    with CENTER_CSV.open(encoding="utf-8-sig", newline="") as handle:
        raw_rows = list(csv.DictReader(handle))
    if len(raw_rows) != EXPECTED_LOCALITIES:
        raise GateError(
            f"expected {EXPECTED_LOCALITIES} source rows, found {len(raw_rows)}"
        )
    required_columns = {
        "근처 수업가능 동네",
        "지역",
        "시or구",
        "센터명",
        "센터 교습비",
        "교육지원청명칭",
        "교육지원청 등록번호",
        "센터 주소",
        *SUBJECT_COLUMNS.values(),
        *SCHOOL_COLUMNS.values(),
    }
    missing_columns = required_columns - set(raw_rows[0])
    if missing_columns:
        raise GateError(f"source columns missing: {sorted(missing_columns)}")

    rows: list[SourceRow] = []
    for raw in raw_rows:
        locality = clean(raw["근처 수업가능 동네"])
        row = SourceRow(
            locality=locality,
            slug=locality_slug(locality),
            region=clean(raw["지역"]),
            district=clean(raw["시or구"]),
            center_name=clean(raw["센터명"]),
            fee_url=clean(raw["센터 교습비"]),
            office_name=clean(raw["교육지원청명칭"]),
            registration=clean(raw["교육지원청 등록번호"]),
            address=clean(raw["센터 주소"]),
            schools={
                level: split_schools(raw[column])
                for level, column in SCHOOL_COLUMNS.items()
            },
            school_legacy={
                level: list_values(raw[column])
                for level, column in SCHOOL_COLUMNS.items()
            },
            grades={
                subject: list_values(raw[column])
                for subject, column in SUBJECT_COLUMNS.items()
            },
        )
        if not all(
            (
                row.locality,
                row.center_name,
                row.office_name,
                row.address,
                row.registration,
            )
        ):
            raise GateError(f"source identity fact missing for {row.locality!r}")
        for subject, grades in row.grades.items():
            unknown = [grade for grade in grades if grade_sort_key(grade)[0] == 99]
            if unknown:
                raise GateError(
                    f"unknown {subject} grades for {row.locality}: {unknown}"
                )
        rows.append(row)

    by_slug = {row.slug: row for row in rows}
    if len(by_slug) != EXPECTED_LOCALITIES:
        raise GateError("source locality slugs are not unique")

    grouped: dict[str, list[SourceRow]] = defaultdict(list)
    for row in rows:
        grouped[row.center_key].append(row)
    if len(grouped) != EXPECTED_PHYSICAL_CENTERS:
        raise GateError(
            f"expected {EXPECTED_PHYSICAL_CENTERS} physical centers, found {len(grouped)}"
        )

    physical_by_key: dict[str, PhysicalCenter] = {}
    for key, group in grouped.items():
        office_names = {row.office_name for row in group}
        if len(office_names) != 1:
            raise GateError(
                f"conflicting education-office names for physical center {key}: "
                f"{sorted(office_names)}"
            )
        areas = ordered_unique(row.locality for row in group)
        address_region, address_locality = official_address_parts(group[0].address, areas)
        physical_by_key[key] = PhysicalCenter(
            key=key,
            center_name=group[0].center_name,
            address=group[0].address,
            office_name=group[0].office_name,
            registration=group[0].registration,
            areas=areas,
            grades={
                subject: tuple(
                    sorted(
                        {grade for row in group for grade in row.grades[subject]},
                        key=grade_sort_key,
                    )
                )
                for subject in SUBJECT_COLUMNS
            },
            fee_urls=ordered_unique(row.fee_url for row in group),
            address_region=address_region,
            address_locality=address_locality,
        )

    ids = {physical.entity_id for physical in physical_by_key.values()}
    if len(ids) != EXPECTED_PHYSICAL_CENTERS:
        raise GateError("physical-center stable @id collision")
    sejong = [
        physical
        for physical in physical_by_key.values()
        if physical.address_region == "세종특별자치시"
    ]
    if len(sejong) != 1 or sejong[0].address_locality != "새롬동":
        raise GateError(
            "Sejong physical-address override must resolve to 새롬동: "
            f"{[(item.areas, item.address_locality) for item in sejong]}"
        )
    return by_slug, physical_by_key


def discover_detail_pages(
    by_slug: dict[str, SourceRow],
) -> list[tuple[Path, Profile, SourceRow]]:
    expected_slugs = set(by_slug)
    pages: list[tuple[Path, Profile, SourceRow]] = []
    for profile in PROFILES:
        category = ROOT / "전국학원" / profile.slug
        actual = {
            path.parent.name: path
            for path in category.glob("*/index.html")
            if path.parent != category
        }
        missing = expected_slugs - set(actual)
        extra = set(actual) - expected_slugs
        if missing or extra:
            raise GateError(
                f"{profile.slug}: URL/locality manifest mismatch; "
                f"missing={sorted(missing)[:8]}, extra={sorted(extra)[:8]}"
            )
        if len(actual) != EXPECTED_LOCALITIES:
            raise GateError(
                f"{profile.slug}: expected {EXPECTED_LOCALITIES} details, "
                f"found {len(actual)}"
            )
        pages.extend(
            (actual[slug], profile, by_slug[slug])
            for slug in sorted(actual)
        )
    if len(pages) != EXPECTED_DETAIL_PAGES:
        raise GateError(
            f"expected {EXPECTED_DETAIL_PAGES} detail pages, found {len(pages)}"
        )
    return pages


def discover_non_detail_pages() -> list[Path]:
    pages = [
        ROOT / "index.html",
        ROOT / "학원소개" / "index.html",
        ROOT / "상담문의" / "index.html",
        ROOT / "전국학원" / "index.html",
    ]
    pages.extend(ROOT / "전국학원" / profile.slug / "index.html" for profile in PROFILES)
    pages.extend(sorted((ROOT / "교육정보").glob("**/index.html")))
    unique = list(dict.fromkeys(pages))
    missing = [path for path in unique if not path.is_file()]
    if missing:
        raise GateError(f"non-detail schema page missing: {missing[:5]}")
    if len(unique) != EXPECTED_NON_DETAIL_PAGES:
        raise GateError(
            f"expected {EXPECTED_NON_DETAIL_PAGES} non-detail pages, found {len(unique)}"
        )
    return unique


def extract_graph(text: str, path: Path) -> dict:
    matches = list(GRAPH_RE.finditer(text))
    if len(matches) != 1:
        raise GateError(f"{path}: expected one JSON-LD script, found {len(matches)}")
    try:
        graph = json.loads(matches[0].group(2))
    except json.JSONDecodeError as error:
        raise GateError(f"{path}: invalid JSON-LD: {error}") from error
    if not isinstance(graph, dict) or not isinstance(graph.get("@graph"), list):
        raise GateError(f"{path}: JSON-LD @graph missing")
    if graph.get("@context") != "https://schema.org":
        raise GateError(f"{path}: JSON-LD @context is not https://schema.org")
    if not all(isinstance(node, dict) for node in graph["@graph"]):
        raise GateError(f"{path}: JSON-LD @graph contains a non-object node")
    return graph


def replace_graph(text: str, graph: dict, path: Path) -> str:
    payload = json.dumps(graph, ensure_ascii=False, separators=(",", ":")).replace(
        "</", "<\\/"
    )
    updated, count = GRAPH_RE.subn(
        lambda match: match.group(1) + payload + match.group(3),
        text,
        count=1,
    )
    if count != 1:
        raise GateError(f"{path}: JSON-LD replacement failed")
    return updated


def html_without_graph(text: str, path: Path) -> str:
    stripped, count = GRAPH_RE.subn(
        lambda match: match.group(1) + "__JSON_LD_PAYLOAD__" + match.group(3),
        text,
        count=1,
    )
    if count != 1:
        raise GateError(f"{path}: JSON-LD isolation failed")
    return stripped


def canonical_url(text: str, path: Path) -> str:
    matches = CANONICAL_RE.findall(text)
    if len(matches) != 1:
        raise GateError(f"{path}: expected one canonical URL, found {len(matches)}")
    value = html.unescape(matches[0])
    parsed = urlsplit(value)
    if parsed.scheme != "https" or parsed.netloc != urlsplit(BASE_URL).netloc:
        raise GateError(f"{path}: canonical is not on the production origin: {value!r}")
    if parsed.query or parsed.fragment:
        raise GateError(f"{path}: canonical contains query/fragment: {value!r}")
    return value


def first_tag_text(text: str, tag: str, path: Path) -> str:
    matches = re.findall(
        rf"<{tag}\b[^>]*>(.*?)</{tag}>", text, re.IGNORECASE | re.DOTALL
    )
    if len(matches) != 1:
        raise GateError(f"{path}: expected one {tag}, found {len(matches)}")
    value = clean_html(matches[0])
    if not value:
        raise GateError(f"{path}: empty {tag}")
    return value


def meta_description(text: str, path: Path) -> str:
    tags = re.findall(r"<meta\b[^>]*>", text, re.IGNORECASE)
    values: list[str] = []
    for tag in tags:
        name = re.search(r'\bname=["\']([^"\']+)["\']', tag, re.IGNORECASE)
        content = re.search(r'\bcontent=["\']([^"\']*)["\']', tag, re.IGNORECASE)
        if name and content and name.group(1).lower() == "description":
            values.append(clean(html.unescape(content.group(1))))
    if len(values) != 1 or not values[0]:
        raise GateError(f"{path}: expected one non-empty meta description")
    return values[0]


def normalized_visible_text(value: str) -> str:
    return re.sub(r"[^0-9A-Za-z가-힣]+", "", clean_html(value)).lower()


def visible_main_contract(text: str, path: Path) -> tuple[str, list[str]]:
    main_matches = re.findall(
        r"<main\b[^>]*>(.*?)</main>", text, re.IGNORECASE | re.DOTALL
    )
    if len(main_matches) != 1:
        raise GateError(f"{path}: expected one visible main element")
    main_html = main_matches[0]
    article_matches = re.findall(
        r'<article\b[^>]*class=["\'][^"\']*\bacademy-main-article\b[^"\']*["\'][^>]*>'
        r"(.*?)</article>",
        main_html,
        re.IGNORECASE | re.DOTALL,
    )
    if len(article_matches) != 1:
        raise GateError(f"{path}: expected one academy-main-article")
    headings = [
        clean_html(value)
        for value in re.findall(
            r"<h2\b[^>]*>(.*?)</h2>",
            article_matches[0],
            re.IGNORECASE | re.DOTALL,
        )
    ]
    if not headings or any(not heading for heading in headings):
        raise GateError(f"{path}: academy-main-article H2 contract missing")
    return normalized_visible_text(main_html), headings


def visible_named_items(value: object, visible_main: str) -> list[dict]:
    if not isinstance(value, list):
        return []
    result: list[dict] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, dict):
            continue
        name = clean(item.get("name"))
        normalized = normalized_visible_text(name)
        if (
            not name
            or not normalized
            or normalized not in visible_main
            or normalized in seen
            or MANUSCRIPT_META_RE.search(name)
            or IRRELEVANT_SEED_RE.search(name)
        ):
            continue
        item_type = item.get("@type", "Thing")
        if not isinstance(item_type, (str, list)):
            item_type = "Thing"
        result.append({"@type": item_type, "name": name})
        seen.add(normalized)
    return result


def node_types(node: dict) -> set[str]:
    value = node.get("@type", [])
    return set(value if isinstance(value, list) else [value])


def nodes_of_type(nodes: list[dict], expected_type: str) -> list[dict]:
    return [node for node in nodes if expected_type in node_types(node)]


def one_node(nodes: list[dict], expected_type: str, path: Path) -> dict:
    matches = nodes_of_type(nodes, expected_type)
    if len(matches) != 1:
        raise GateError(
            f"{path}: expected one {expected_type} node, found {len(matches)}"
        )
    return matches[0]


def strip_contact_claims(value: object) -> None:
    if isinstance(value, dict):
        value.pop("telephone", None)
        value.pop("contactPoint", None)
        for child in value.values():
            strip_contact_claims(child)
    elif isinstance(value, list):
        for child in value:
            strip_contact_claims(child)


def iter_key_values(value: object, key: str) -> Iterable[object]:
    if isinstance(value, dict):
        for current_key, child in value.items():
            if current_key == key:
                yield child
            yield from iter_key_values(child, key)
    elif isinstance(value, list):
        for child in value:
            yield from iter_key_values(child, key)


def iter_string_values(
    value: object, json_path: str = "$"
) -> Iterable[tuple[str, str]]:
    if isinstance(value, dict):
        for key, child in value.items():
            yield from iter_string_values(child, f"{json_path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from iter_string_values(child, f"{json_path}[{index}]")
    elif isinstance(value, str):
        yield json_path, value


def validate_schema_string_hard_zero(graph: dict, path: Path) -> None:
    failures: list[str] = []
    for json_path, value in iter_string_values(graph):
        terms = ordered_unique(
            match.group(0)
            for regex in (MANUSCRIPT_META_RE, IRRELEVANT_SEED_RE)
            for match in regex.finditer(value)
        )
        if terms:
            failures.append(f"{json_path}: {', '.join(terms)}")
    if failures:
        raise GateError(
            f"{path}: JSON-LD manuscript/irrelevant strings remain: "
            + "; ".join(failures[:5])
        )


def visible_fact_pairs(text: str, path: Path) -> dict[str, str]:
    pairs = re.findall(
        r"<dt\b[^>]*>(.*?)</dt>\s*<dd\b[^>]*>(.*?)</dd>",
        text,
        re.IGNORECASE | re.DOTALL,
    )
    if not pairs:
        raise GateError(f"{path}: visible dt/dd facts missing")
    facts: dict[str, str] = {}
    for raw_label, raw_value in pairs:
        label = clean_html(raw_label)
        if label in facts:
            raise GateError(f"{path}: duplicate visible fact label: {label!r}")
        facts[label] = raw_value
    return facts


def fact_value(
    facts: dict[str, str], labels: tuple[str, ...], path: Path, description: str
) -> str:
    matches = [facts[label] for label in labels if label in facts]
    if len(matches) != 1:
        raise GateError(
            f"{path}: expected one {description} fact from {labels}, found {len(matches)}"
        )
    return matches[0]


def profile_grades(source: SourceRow, profile: Profile) -> dict[str, tuple[str, ...]]:
    return {
        subject: tuple(
            grade
            for grade in source.grades[subject]
            if not profile.grade_prefix or grade.startswith(profile.grade_prefix)
        )
        for subject in profile.subjects
    }


def page_supported(source: SourceRow, profile: Profile) -> bool:
    return all(profile_grades(source, profile).values())


def expected_page_levels(source: SourceRow, profile: Profile) -> list[str]:
    return sorted(
        {
            grade
            for grades in profile_grades(source, profile).values()
            for grade in grades
        },
        key=grade_sort_key,
    )


def expected_schools(source: SourceRow, profile: Profile) -> tuple[str, ...]:
    return ordered_unique(
        school
        for level in profile.school_levels
        for school in source.schools[level]
    )


def all_source_school_names(source: SourceRow) -> set[str]:
    return {
        school
        for mapping in (source.schools, source.school_legacy)
        for schools in mapping.values()
        for school in schools
    }


def visible_faq(text: str, path: Path) -> list[tuple[str, str, str]]:
    blocks = re.findall(
        r'<div\b[^>]*class=["\'][^"\']*\bacademy-faq-list\b[^"\']*["\'][^>]*>'
        r"(.*?)</div>",
        text,
        re.IGNORECASE | re.DOTALL,
    )
    if len(blocks) != 1:
        raise GateError(f"{path}: expected one academy FAQ list, found {len(blocks)}")
    items = [
        (attributes, clean_html(question), clean_html(answer))
        for attributes, question, answer in re.findall(
            r"<details\b([^>]*)>\s*<summary\b[^>]*>(.*?)</summary>\s*"
            r"<p\b[^>]*>(.*?)</p>\s*</details>",
            blocks[0],
            re.IGNORECASE | re.DOTALL,
        )
    ]
    if len(items) < 4 or any(not question or not answer for _, question, answer in items):
        raise GateError(f"{path}: malformed or incomplete visible FAQ ({len(items)} items)")
    return items


def validate_detail_route_and_visible(
    text: str,
    path: Path,
    profile: Profile,
    source: SourceRow,
) -> tuple[str, str, str, str, list[tuple[str, str, str]]]:
    canonical = canonical_url(text, path)
    expected_route = f"/전국학원/{profile.slug}/{source.slug}/"
    if normalized_url_path(canonical) != expected_route:
        raise GateError(
            f"{path}: canonical path mismatch: {normalized_url_path(canonical)!r} "
            f"!= {expected_route!r}"
        )
    expected_relative = Path("전국학원") / profile.slug / source.slug / "index.html"
    if path.relative_to(ROOT) != expected_relative:
        raise GateError(f"{path}: filesystem route mismatch")

    title = first_tag_text(text, "title", path)
    h1 = first_tag_text(text, "h1", path)
    expected_h1 = f"{source.locality} {profile.category}"
    if h1 != expected_h1 or title != f"{expected_h1} | 소수정예학원":
        raise GateError(
            f"{path}: title/H1 route identity mismatch: {title!r} / {h1!r}"
        )
    description = meta_description(text, path)
    facts = visible_fact_pairs(text, path)
    expected_values = {
        "지역": expected_visible_region(source),
        "센터 기준": source.center_name,
        "제공 주소": source.address,
    }
    for label, expected in expected_values.items():
        actual = clean_html(facts.get(label, ""))
        if actual != expected:
            raise GateError(
                f"{path}: visible {label} mismatch: {actual!r} != {expected!r}"
            )
    registration_html = fact_value(
        facts,
        ("교육지원청 등록번호", "등록 정보"),
        path,
        "registration",
    )
    if clean_html(registration_html) != source.registration:
        raise GateError(f"{path}: visible registration mismatch")

    expected_grade_map = profile_grades(source, profile)
    actual_grade_labels = {
        label for label in facts if label.endswith("수업 가능 학년")
    }
    expected_grade_labels = {f"{subject} 수업 가능 학년" for subject in profile.subjects}
    if actual_grade_labels != expected_grade_labels:
        raise GateError(
            f"{path}: grade row set mismatch: "
            f"{sorted(actual_grade_labels)} != {sorted(expected_grade_labels)}"
        )
    for subject, expected_grades in expected_grade_map.items():
        raw = facts[f"{subject} 수업 가능 학년"]
        actual_grades = tuple(
            clean_html(value)
            for value in re.findall(
                r"<span\b[^>]*>(.*?)</span>", raw, re.IGNORECASE | re.DOTALL
            )
            if clean_html(value) and "원자료 미기재" not in clean_html(value)
        )
        if expected_grades:
            if actual_grades != expected_grades:
                raise GateError(
                    f"{path}: visible {subject} grades mismatch: "
                    f"{actual_grades!r} != {expected_grades!r}"
                )
            if re.search(
                r'data-source-status\s*=\s*["\']unconfirmed-grade["\']', raw
            ):
                raise GateError(f"{path}: confirmed {subject} row has unconfirmed marker")
        else:
            if actual_grades:
                raise GateError(f"{path}: unsupported {subject} row still claims grades")
            if (
                not re.search(
                    r'data-source-status\s*=\s*["\']unconfirmed-grade["\']', raw
                )
                or "원자료 미기재" not in clean_html(raw)
                or "상담 전 확인" not in clean_html(raw)
            ):
                raise GateError(f"{path}: unsupported {subject} row disclosure missing")

    school_labels = [label for label in facts if "학교" in label and "참고" in label]
    if school_labels != [profile.school_label]:
        raise GateError(
            f"{path}: school-reference row mismatch: "
            f"{school_labels!r} != {[profile.school_label]!r}"
        )
    raw_schools = facts[school_labels[0]]
    actual_schools = tuple(
        clean_html(value)
        for value in re.findall(
            r"<span\b[^>]*>(.*?)</span>", raw_schools, re.IGNORECASE | re.DOTALL
        )
        if clean_html(value)
        and "원자료 미기재" not in clean_html(value)
        and "정보 없음" not in clean_html(value)
        and "제공 목록 없음" not in clean_html(value)
    )
    schools = expected_schools(source, profile)
    if actual_schools != schools:
        raise GateError(
            f"{path}: visible schools mismatch: {actual_schools!r} != {schools!r}"
        )
    if not schools and not any(
        phrase in clean_html(raw_schools)
        for phrase in (
            "원자료 미기재",
            "제공 목록 없음",
            "정보 없음",
            "상담 전 확인",
            "상담 시",
        )
    ):
        raise GateError(f"{path}: blank-school disclosure missing")

    tuition_links = re.findall(
        r'<a\b(?=[^>]*\bacademy-tuition-link\b)[^>]*\bhref=["\']([^"\']+)["\']',
        text,
        re.IGNORECASE,
    )
    expected_fee_links = [source.fee_url] if source.fee_url else []
    if tuition_links != expected_fee_links:
        raise GateError(
            f"{path}: fee source-link mismatch: {tuition_links!r} != {expected_fee_links!r}"
        )

    faq = visible_faq(text, path)
    supported = page_supported(source, profile)
    marker_re = re.compile(
        r'data-source-status\s*=\s*["\']unconfirmed-grade["\']',
        re.IGNORECASE,
    )
    status_rows = re.findall(
        r'<div\b(?=[^>]*\bacademy-source-status-row\b)(?=[^>]*'
        r'data-source-status=["\']unconfirmed-grade["\'])[^>]*>',
        text,
        re.IGNORECASE,
    )
    status_faqs = [attributes for attributes, _, _ in faq if marker_re.search(attributes)]
    all_markers = marker_re.findall(text)
    if supported:
        if all_markers or status_rows or status_faqs:
            raise GateError(f"{path}: supported page has an unconfirmed-grade marker")
    else:
        if len(all_markers) < 3:
            raise GateError(f"{path}: unsupported marker count is {len(all_markers)}")
        if len(status_rows) != 1:
            raise GateError(f"{path}: unsupported status row count is {len(status_rows)}")
        if not status_faqs:
            raise GateError(f"{path}: unsupported FAQ disclosure marker missing")
        for class_name in (
            "academy-source-status-row",
            "academy-grade-empty",
            "academy-source-unconfirmed",
            "academy-source-status-faq",
        ):
            if not re.search(
                rf'class=["\'][^"\']*\b{re.escape(class_name)}\b[^"\']*["\']',
                text,
                re.IGNORECASE,
            ):
                raise GateError(f"{path}: unsupported class marker missing: {class_name}")
        if not any(
            ("원자료" in answer or "제공된 센터 자료" in answer) and "상담" in answer
            for attributes, _, answer in faq
            if marker_re.search(attributes)
        ):
            raise GateError(f"{path}: unsupported FAQ disclosure text missing")
    return canonical, title, h1, description, faq


def expected_physical_teaches(physical: PhysicalCenter) -> list[str]:
    return [
        subject
        for subject in SUBJECT_COLUMNS
        if physical.grades[subject]
    ]


def physical_subject_offer(
    physical: PhysicalCenter,
    subject: str,
    grades: tuple[str, ...],
) -> dict:
    name = f"{subject} 학습 상담"
    service_id = f"{physical.entity_id}-{SUBJECT_CODES[subject]}"
    return {
        "@type": "Offer",
        "name": name,
        "itemOffered": {
            "@type": "Service",
            "@id": service_id,
            "name": name,
            "provider": {"@id": physical.entity_id},
            "areaServed": list(physical.areas),
            "educationalLevel": list(grades),
            "audience": {
                "@type": "EducationalAudience",
                "educationalRole": "student",
                "audienceType": "·".join(grades),
            },
        },
    }


def expected_physical_offers(physical: PhysicalCenter) -> list[dict]:
    return [
        physical_subject_offer(physical, subject, physical.grades[subject])
        for subject in SUBJECT_COLUMNS
        if physical.grades[subject]
    ]


def expected_physical_node(physical: PhysicalCenter) -> dict:
    node: dict[str, object] = {
        "@type": ["EducationalOrganization", "LocalBusiness"],
        "@id": physical.entity_id,
        "name": physical.center_name,
        "address": {
            "@type": "PostalAddress",
            "streetAddress": physical.address,
            "addressLocality": physical.address_locality,
            "addressRegion": physical.address_region,
            "addressCountry": "KR",
        },
        "areaServed": list(physical.areas),
        "identifier": {
            "@type": "PropertyValue",
            "name": physical.office_name,
            "value": physical.registration,
        },
    }
    teaches = expected_physical_teaches(physical)
    offers = expected_physical_offers(physical)
    if teaches:
        node["teaches"] = teaches
    if offers:
        node["makesOffer"] = offers
    return node


def expected_page_offer(
    canonical: str,
    source: SourceRow,
    physical: PhysicalCenter,
    subject: str,
    grades: tuple[str, ...],
) -> dict:
    code = SUBJECT_CODES[subject]
    name = f"{subject} 학습 상담"
    return {
        "@type": "Offer",
        "@id": f"{canonical}#offer-{code}",
        "name": name,
        "itemOffered": {
            "@type": "Service",
            "@id": f"{canonical}#service-{code}",
            "name": name,
            "provider": {"@id": physical.entity_id},
            "areaServed": [source.locality],
            "educationalLevel": list(grades),
            "audience": {
                "@type": "EducationalAudience",
                "educationalRole": "student",
                "audienceType": "·".join(grades),
            },
        },
    }


def expected_service_node(
    canonical: str,
    h1: str,
    description: str,
    profile: Profile,
    source: SourceRow,
    physical: PhysicalCenter,
) -> dict:
    grades = profile_grades(source, profile)
    if not all(grades.values()):
        raise GateError(f"cannot build unsupported page Service: {canonical}")
    levels = expected_page_levels(source, profile)
    return {
        "@type": "Service",
        "@id": f"{canonical}#service",
        "name": f"{h1} 학습 안내",
        "serviceType": profile.category,
        "provider": {"@id": physical.entity_id},
        "areaServed": [source.locality],
        "description": description,
        "educationalLevel": levels,
        "audience": {
            "@type": "EducationalAudience",
            "educationalRole": "student",
            "audienceType": "·".join(levels),
        },
        "offers": [
            expected_page_offer(
                canonical,
                source,
                physical,
                subject,
                grades[subject],
            )
            for subject in profile.subjects
        ],
    }


def expected_breadcrumb_node(current: dict, canonical: str, path: Path) -> dict:
    elements = json.loads(json.dumps(current.get("itemListElement", []), ensure_ascii=False))
    if not isinstance(elements, list) or not elements:
        raise GateError(f"{path}: BreadcrumbList items missing")
    for index, element in enumerate(elements, start=1):
        if not isinstance(element, dict):
            raise GateError(f"{path}: BreadcrumbList item is not an object")
        element["@type"] = "ListItem"
        element["position"] = index
        key = "item" if "item" in element else "url" if "url" in element else ""
        if not key:
            raise GateError(f"{path}: BreadcrumbList item URL missing")
        element[key] = urljoin(canonical, str(element[key]))
    last = elements[-1]
    last_url = str(last.get("item", last.get("url", "")))
    if normalized_url_path(last_url) != normalized_url_path(canonical):
        raise GateError(f"{path}: breadcrumb terminal URL mismatch")
    return {
        "@type": "BreadcrumbList",
        "@id": f"{canonical}#breadcrumb",
        "itemListElement": elements,
    }


def expected_item_list_node(current: dict, canonical: str) -> dict:
    node = json.loads(json.dumps(current, ensure_ascii=False))
    node["@type"] = "ItemList"
    node["@id"] = f"{canonical}#related"
    items = node.get("itemListElement", [])
    if isinstance(items, list):
        for index, item in enumerate(items, start=1):
            if not isinstance(item, dict):
                continue
            item["@type"] = "ListItem"
            item["position"] = index
            for key in ("url", "item"):
                if key in item:
                    item[key] = urljoin(canonical, str(item[key]))
    return node


def expected_faq_node(
    canonical: str, faq: list[tuple[str, str, str]]
) -> dict:
    return {
        "@type": "FAQPage",
        "@id": f"{canonical}#faq",
        "mainEntity": [
            {
                "@type": "Question",
                "name": question,
                "acceptedAnswer": {"@type": "Answer", "text": answer},
            }
            for _, question, answer in faq
        ],
    }


def expected_article_mentions(
    current: dict,
    profile: Profile,
    source: SourceRow,
    physical: PhysicalCenter,
    visible_main: str,
) -> list[dict]:
    excluded = all_source_school_names(source) | {
        source.region,
        source.district,
        source.locality,
        physical.address_region,
        expected_visible_region(source),
    }
    topics: list[dict] = []
    seen: set[str] = set()
    mentions = current.get("mentions", [])
    if isinstance(mentions, list):
        for item in mentions:
            if not isinstance(item, dict):
                continue
            name = clean(item.get("name"))
            if not name or name in excluded or name in seen:
                continue
            seen.add(name)
            topics.append(json.loads(json.dumps(item, ensure_ascii=False)))
    administrative_names = [physical.address_region]
    if physical.address_region != "세종특별자치시":
        administrative_names.append(source.district)
    administrative_names.append(source.locality)
    candidates = [
        {"@type": "Thing", "name": name}
        for name in ordered_unique(administrative_names)
    ] + [
        {"@type": "School", "name": school}
        for school in expected_schools(source, profile)
    ] + topics
    return visible_named_items(candidates, visible_main)


def expected_article_node(
    current: dict,
    canonical: str,
    h1: str,
    description: str,
    profile: Profile,
    source: SourceRow,
    physical: PhysicalCenter,
    supported: bool,
    visible_main: str,
    visible_headings: list[str],
) -> dict:
    article = json.loads(json.dumps(current, ensure_ascii=False))
    for key in (
        "audience",
        "educationalLevel",
        "offers",
        "makesOffer",
        "provider",
        "abstract",
    ):
        article.pop(key, None)
    article["@type"] = "Article"
    article["@id"] = f"{canonical}#article"
    article["headline"] = h1
    article["description"] = description
    article["inLanguage"] = "ko-KR"
    article["mainEntityOfPage"] = {"@id": f"{canonical}#webpage"}
    article["isPartOf"] = {"@id": ROOT_WEBSITE_ID}
    article["author"] = {"@id": ROOT_ORGANIZATION_ID}
    article["publisher"] = {"@id": ROOT_ORGANIZATION_ID}
    sections = [physical.address_region]
    if physical.address_region != "세종특별자치시":
        sections.append(source.district)
    sections.append(source.locality)
    article["articleSection"] = [profile.category, *ordered_unique(sections)]
    article["identifier"] = {
        "@type": "PropertyValue",
        "name": physical.office_name,
        "value": physical.registration,
    }
    article["hasPart"] = [
        {"@type": "WebPageElement", "name": heading}
        for heading in visible_headings
    ]
    about = visible_named_items(current.get("about"), visible_main)
    if about:
        article["about"] = about
    else:
        article.pop("about", None)
    mentions = expected_article_mentions(
        current, profile, source, physical, visible_main
    )
    if mentions:
        article["mentions"] = mentions
    else:
        article.pop("mentions", None)
    if supported:
        article["educationalLevel"] = expected_page_levels(source, profile)
    return article


def expected_webpage_node(
    canonical: str,
    h1: str,
    description: str,
    supported: bool,
) -> dict:
    node: dict[str, object] = {
        "@type": "WebPage",
        "@id": f"{canonical}#webpage",
        "url": canonical,
        "name": h1,
        "description": description,
        "inLanguage": "ko-KR",
        "isPartOf": {"@id": ROOT_WEBSITE_ID},
        "breadcrumb": {"@id": f"{canonical}#breadcrumb"},
        "mainEntity": {"@id": f"{canonical}#article"},
    }
    if supported:
        node["about"] = {"@id": f"{canonical}#service"}
    return node


MANAGED_DETAIL_TYPES = {
    "EducationalOrganization",
    "LocalBusiness",
    "WebPage",
    "BreadcrumbList",
    "Article",
    "Service",
    "FAQPage",
    "ItemList",
}


def transform_detail_graph(
    graph: dict,
    path: Path,
    canonical: str,
    h1: str,
    description: str,
    faq: list[tuple[str, str, str]],
    profile: Profile,
    source: SourceRow,
    physical: PhysicalCenter,
    visible_main: str,
    visible_headings: list[str],
) -> dict:
    nodes = graph["@graph"]
    article = one_node(nodes, "Article", path)
    breadcrumb = one_node(nodes, "BreadcrumbList", path)
    faq_nodes = nodes_of_type(nodes, "FAQPage")
    item_lists = nodes_of_type(nodes, "ItemList")
    if len(faq_nodes) != 1 or len(item_lists) != 1:
        raise GateError(
            f"{path}: expected one FAQPage and ItemList before repair; "
            f"found {len(faq_nodes)}/{len(item_lists)}"
        )
    supported = page_supported(source, profile)
    retained = [
        json.loads(json.dumps(node, ensure_ascii=False))
        for node in nodes
        if not (node_types(node) & MANAGED_DETAIL_TYPES)
    ]
    strip_contact_claims(retained)
    rebuilt = [
        expected_physical_node(physical),
        expected_breadcrumb_node(breadcrumb, canonical, path),
        expected_webpage_node(canonical, h1, description, supported),
        expected_article_node(
            article,
            canonical,
            h1,
            description,
            profile,
            source,
            physical,
            supported,
            visible_main,
            visible_headings,
        ),
    ]
    if supported:
        rebuilt.append(
            expected_service_node(
                canonical,
                h1,
                description,
                profile,
                source,
                physical,
            )
        )
    rebuilt.extend(
        (
            expected_faq_node(canonical, faq),
            expected_item_list_node(item_lists[0], canonical),
        )
    )
    return {"@context": "https://schema.org", "@graph": retained + rebuilt}


def contains_schema_type(value: object, expected_type: str) -> bool:
    if isinstance(value, dict):
        types = value.get("@type", [])
        if expected_type in (types if isinstance(types, list) else [types]):
            return True
        return any(contains_schema_type(child, expected_type) for child in value.values())
    if isinstance(value, list):
        return any(contains_schema_type(child, expected_type) for child in value)
    return False


def validate_detail_schema(
    graph: dict,
    text: str,
    path: Path,
    canonical: str,
    h1: str,
    description: str,
    faq: list[tuple[str, str, str]],
    profile: Profile,
    source: SourceRow,
    physical: PhysicalCenter,
    visible_main: str,
    visible_headings: list[str],
) -> None:
    validate_schema_string_hard_zero(graph, path)
    nodes = graph["@graph"]
    supported = page_supported(source, profile)
    required = {
        "EducationalOrganization": 1,
        "LocalBusiness": 1,
        "WebPage": 1,
        "BreadcrumbList": 1,
        "Article": 1,
        "FAQPage": 1,
        "ItemList": 1,
        "Service": 1 if supported else 0,
    }
    for expected_type, count in required.items():
        actual = len(nodes_of_type(nodes, expected_type))
        if actual != count:
            raise GateError(
                f"{path}: expected {count} {expected_type} node(s), found {actual}"
            )
    organization = one_node(nodes, "EducationalOrganization", path)
    if organization != expected_physical_node(physical):
        raise GateError(f"{path}: physical organization is not the stable source projection")
    webpage = one_node(nodes, "WebPage", path)
    if webpage != expected_webpage_node(canonical, h1, description, supported):
        raise GateError(f"{path}: WebPage mismatch")
    current_article = one_node(nodes, "Article", path)
    if current_article.get("author") != {"@id": ROOT_ORGANIZATION_ID}:
        raise GateError(f"{path}: Article.author is not the site-root organization")
    if current_article.get("publisher") != {"@id": ROOT_ORGANIZATION_ID}:
        raise GateError(f"{path}: Article.publisher is not the site-root organization")
    if current_article.get("mainEntityOfPage") != {"@id": f"{canonical}#webpage"}:
        raise GateError(f"{path}: Article.mainEntityOfPage mismatch")
    expected_article = expected_article_node(
        current_article,
        canonical,
        h1,
        description,
        profile,
        source,
        physical,
        supported,
        visible_main,
        visible_headings,
    )
    if current_article != expected_article:
        raise GateError(f"{path}: Article school/region/grade parity mismatch")
    expected_levels = expected_page_levels(source, profile)
    if supported:
        if current_article.get("educationalLevel") != expected_levels:
            raise GateError(f"{path}: Article.educationalLevel mismatch")
        service = one_node(nodes, "Service", path)
        expected_service = expected_service_node(
            canonical, h1, description, profile, source, physical
        )
        if service != expected_service:
            raise GateError(f"{path}: page Service/provider/source-grade mismatch")
    else:
        forbidden = ("audience", "educationalLevel", "offers", "makesOffer")
        for node in nodes:
            if node is organization:
                continue
            for key in forbidden:
                if list(iter_key_values(node, key)):
                    raise GateError(
                        f"{path}: unsupported page retains page-specific {key}"
                    )
            if contains_schema_type(node, "Offer"):
                raise GateError(f"{path}: unsupported page retains a page-specific Offer")
    schema_faq = one_node(nodes, "FAQPage", path)
    if schema_faq != expected_faq_node(canonical, faq):
        raise GateError(f"{path}: visible FAQ / FAQPage parity mismatch")
    if list(iter_key_values(graph, "telephone")) or list(
        iter_key_values(graph, "contactPoint")
    ):
        raise GateError(f"{path}: detail graph contains central contact data")
    address = organization.get("address", {})
    if (
        address.get("addressRegion") != physical.address_region
        or address.get("addressLocality") != physical.address_locality
    ):
        raise GateError(f"{path}: official physical PostalAddress mismatch")
    validate_detail_route_and_visible(text, path, profile, source)


WEBPAGE_LIKE_TYPES = {"WebPage", "CollectionPage", "AboutPage", "ContactPage"}


def normalize_reference_ids(value: object, canonical: str) -> None:
    if isinstance(value, dict):
        identifier = value.get("@id")
        if isinstance(identifier, str):
            fragment = identifier.rsplit("#", 1)[1] if "#" in identifier else ""
            if fragment == "organization":
                value["@id"] = ROOT_ORGANIZATION_ID
            elif fragment == "website":
                value["@id"] = ROOT_WEBSITE_ID
            elif fragment:
                value["@id"] = f"{canonical}#{fragment}"
            elif not urlsplit(identifier).scheme:
                value["@id"] = urljoin(canonical, identifier)
        for child in value.values():
            normalize_reference_ids(child, canonical)
    elif isinstance(value, list):
        for child in value:
            normalize_reference_ids(child, canonical)


def expected_site_route(path: Path) -> str:
    relative = path.relative_to(ROOT)
    if relative == Path("index.html"):
        return "/"
    return "/" + relative.parent.as_posix().strip("/") + "/"


def validate_site_route(text: str, path: Path) -> tuple[str, str, str, str]:
    canonical = canonical_url(text, path)
    expected = expected_site_route(path)
    if normalized_url_path(canonical) != expected:
        raise GateError(
            f"{path}: non-detail canonical path mismatch: "
            f"{normalized_url_path(canonical)!r} != {expected!r}"
        )
    return (
        canonical,
        first_tag_text(text, "title", path),
        first_tag_text(text, "h1", path),
        meta_description(text, path),
    )


def normalize_breadcrumb_in_place(node: dict, canonical: str, path: Path) -> None:
    replacement = expected_breadcrumb_node(node, canonical, path)
    node.clear()
    node.update(replacement)


def transform_non_detail_graph(
    graph: dict,
    path: Path,
    canonical: str,
    h1: str,
    description: str,
) -> dict:
    updated = json.loads(json.dumps(graph, ensure_ascii=False))
    nodes = updated["@graph"]
    strip_contact_claims(nodes)
    root_page = path == ROOT / "index.html"

    organizations = nodes_of_type(nodes, "EducationalOrganization")
    if len(organizations) > 1:
        raise GateError(f"{path}: multiple site organization definitions")
    source_organization = organizations[0] if organizations else None
    nodes[:] = [
        node for node in nodes if "EducationalOrganization" not in node_types(node)
    ]

    normalize_reference_ids(nodes, canonical)
    for node in nodes:
        types = node_types(node)
        if "WebSite" in types:
            node["@id"] = ROOT_WEBSITE_ID
            node["url"] = ROOT_URL
            node["publisher"] = {"@id": ROOT_ORGANIZATION_ID}
            node["inLanguage"] = "ko-KR"
        if types & WEBPAGE_LIKE_TYPES:
            node["@id"] = f"{canonical}#webpage"
            node["url"] = canonical
            node["isPartOf"] = {"@id": ROOT_WEBSITE_ID}
            node.setdefault("inLanguage", "ko-KR")
        if "BreadcrumbList" in types:
            normalize_breadcrumb_in_place(node, canonical, path)
        if "Article" in types:
            node["@id"] = f"{canonical}#article"
            node["mainEntityOfPage"] = {"@id": f"{canonical}#webpage"}
            node["isPartOf"] = {"@id": ROOT_WEBSITE_ID}
            node["author"] = {"@id": ROOT_ORGANIZATION_ID}
            node["publisher"] = {"@id": ROOT_ORGANIZATION_ID}
            node["inLanguage"] = "ko-KR"
        if "FAQPage" in types:
            node["@id"] = f"{canonical}#faq"
        if "ItemList" in types:
            node["@id"] = f"{canonical}#related"

    articles = nodes_of_type(nodes, "Article")
    page_nodes = [node for node in nodes if node_types(node) & WEBPAGE_LIKE_TYPES]
    if articles and not page_nodes:
        if len(articles) != 1:
            raise GateError(f"{path}: cannot attach multiple Articles to one WebPage")
        nodes.append(
            {
                "@type": "WebPage",
                "@id": f"{canonical}#webpage",
                "url": canonical,
                "name": h1,
                "description": description,
                "inLanguage": "ko-KR",
                "isPartOf": {"@id": ROOT_WEBSITE_ID},
                "mainEntity": {"@id": f"{canonical}#article"},
            }
        )

    if root_page:
        if source_organization is None:
            raise GateError(f"{path}: root organization definition missing")
        organization = json.loads(json.dumps(source_organization, ensure_ascii=False))
        strip_contact_claims(organization)
        organization["@type"] = "EducationalOrganization"
        organization["@id"] = ROOT_ORGANIZATION_ID
        organization["url"] = ROOT_URL
        organization["contactPoint"] = {
            "@type": "ContactPoint",
            "telephone": "+82-10-6839-8283",
            "contactType": "학습 상담",
            "availableLanguage": "Korean",
        }
        nodes.insert(0, organization)

    updated["@context"] = "https://schema.org"
    return updated


def validate_non_detail_schema(
    graph: dict,
    text: str,
    path: Path,
    canonical: str,
) -> None:
    validate_schema_string_hard_zero(graph, path)
    nodes = graph["@graph"]
    root_page = path == ROOT / "index.html"
    organizations = nodes_of_type(nodes, "EducationalOrganization")
    if root_page:
        if len(organizations) != 1:
            raise GateError(f"{path}: root organization definition count mismatch")
        organization = organizations[0]
        if organization.get("@id") != ROOT_ORGANIZATION_ID:
            raise GateError(f"{path}: root organization @id mismatch")
        if "telephone" in organization:
            raise GateError(f"{path}: direct root organization telephone remains")
        expected_contact = {
            "@type": "ContactPoint",
            "telephone": "+82-10-6839-8283",
            "contactType": "학습 상담",
            "availableLanguage": "Korean",
        }
        if organization.get("contactPoint") != expected_contact:
            raise GateError(f"{path}: root ContactPoint mismatch")
        websites = nodes_of_type(nodes, "WebSite")
        if len(websites) != 1 or websites[0].get("@id") != ROOT_WEBSITE_ID:
            raise GateError(f"{path}: root WebSite identity mismatch")
    else:
        if organizations:
            raise GateError(f"{path}: repeated site organization definition remains")
        if list(iter_key_values(graph, "telephone")) or list(
            iter_key_values(graph, "contactPoint")
        ):
            raise GateError(f"{path}: central contact data leaked outside root")

    articles = nodes_of_type(nodes, "Article")
    for article in articles:
        if article.get("author") != {"@id": ROOT_ORGANIZATION_ID}:
            raise GateError(f"{path}: Article.author is not the site root")
        if article.get("publisher") != {"@id": ROOT_ORGANIZATION_ID}:
            raise GateError(f"{path}: Article.publisher is not the site root")
        if article.get("isPartOf") != {"@id": ROOT_WEBSITE_ID}:
            raise GateError(f"{path}: Article.isPartOf is not the root WebSite")
        if article.get("mainEntityOfPage") != {"@id": f"{canonical}#webpage"}:
            raise GateError(f"{path}: Article.mainEntityOfPage mismatch")
    if articles:
        page_nodes = [node for node in nodes if node_types(node) & WEBPAGE_LIKE_TYPES]
        if len(page_nodes) != 1:
            raise GateError(f"{path}: Article page WebPage count mismatch")
    for node in nodes:
        identifier = node.get("@id")
        if isinstance(identifier, str) and not identifier.startswith(BASE_URL):
            raise GateError(f"{path}: top-level relative/foreign @id remains: {identifier}")
    validate_site_route(text, path)


def build_detail_candidate(
    path: Path,
    profile: Profile,
    source: SourceRow,
    physical: PhysicalCenter,
    original_text: str | None = None,
) -> Candidate:
    original = read_utf8_exact(path) if original_text is None else original_text
    canonical, _, h1, description, faq = validate_detail_route_and_visible(
        original, path, profile, source
    )
    visible_main, visible_headings = visible_main_contract(original, path)
    graph = extract_graph(original, path)
    transformed_graph = transform_detail_graph(
        graph,
        path,
        canonical,
        h1,
        description,
        faq,
        profile,
        source,
        physical,
        visible_main,
        visible_headings,
    )
    transformed = replace_graph(original, transformed_graph, path)
    if html_without_graph(transformed, path) != html_without_graph(original, path):
        raise GateError(f"{path}: bytes outside JSON-LD changed in memory")
    reparsed = extract_graph(transformed, path)
    validate_detail_schema(
        reparsed,
        transformed,
        path,
        canonical,
        h1,
        description,
        faq,
        profile,
        source,
        physical,
        visible_main,
        visible_headings,
    )
    second_graph = transform_detail_graph(
        reparsed,
        path,
        canonical,
        h1,
        description,
        faq,
        profile,
        source,
        physical,
        visible_main,
        visible_headings,
    )
    second = replace_graph(transformed, second_graph, path)
    if second != transformed:
        raise GateError(f"{path}: detail schema transform is not idempotent")
    return Candidate(
        path=path,
        relative_path=path.relative_to(ROOT).as_posix(),
        original=original,
        transformed=transformed,
        source_sha256=sha256_text(original),
        non_schema_sha256=sha256_text(html_without_graph(original, path)),
        canonical=canonical,
        is_detail=True,
        profile=profile,
        source=source,
        physical=physical,
        supported=page_supported(source, profile),
    )


def build_non_detail_candidate(
    path: Path,
    original_text: str | None = None,
) -> Candidate:
    original = read_utf8_exact(path) if original_text is None else original_text
    canonical, _, h1, description = validate_site_route(original, path)
    graph = extract_graph(original, path)
    transformed_graph = transform_non_detail_graph(
        graph, path, canonical, h1, description
    )
    transformed = replace_graph(original, transformed_graph, path)
    if html_without_graph(transformed, path) != html_without_graph(original, path):
        raise GateError(f"{path}: bytes outside JSON-LD changed in memory")
    reparsed = extract_graph(transformed, path)
    validate_non_detail_schema(reparsed, transformed, path, canonical)
    second_graph = transform_non_detail_graph(
        reparsed, path, canonical, h1, description
    )
    second = replace_graph(transformed, second_graph, path)
    if second != transformed:
        raise GateError(f"{path}: site schema transform is not idempotent")
    return Candidate(
        path=path,
        relative_path=path.relative_to(ROOT).as_posix(),
        original=original,
        transformed=transformed,
        source_sha256=sha256_text(original),
        non_schema_sha256=sha256_text(html_without_graph(original, path)),
        canonical=canonical,
        is_detail=False,
    )


def graph_from_candidate(candidate: Candidate) -> dict:
    return extract_graph(candidate.transformed, candidate.path)


def validate_global_contract(
    candidates: list[Candidate],
    physical_by_key: dict[str, PhysicalCenter],
) -> dict[str, object]:
    details = [candidate for candidate in candidates if candidate.is_detail]
    site_pages = [candidate for candidate in candidates if not candidate.is_detail]
    if len(details) != EXPECTED_DETAIL_PAGES:
        raise GateError(f"global detail count mismatch: {len(details)}")
    if len(site_pages) != EXPECTED_NON_DETAIL_PAGES:
        raise GateError(f"global non-detail count mismatch: {len(site_pages)}")

    unsupported_by_profile = {
        profile.slug: sum(
            candidate.supported is False and candidate.profile == profile
            for candidate in details
        )
        for profile in PROFILES
    }
    if unsupported_by_profile != EXPECTED_UNSUPPORTED_BY_PROFILE:
        raise GateError(
            "unsupported source contract mismatch: "
            f"{unsupported_by_profile} != {EXPECTED_UNSUPPORTED_BY_PROFILE}"
        )
    unsupported_total = sum(unsupported_by_profile.values())
    if unsupported_total != EXPECTED_UNSUPPORTED_PAGES:
        raise GateError(f"unsupported page total mismatch: {unsupported_total}")

    physical_projections: dict[str, set[str]] = defaultdict(set)
    telephone_values: list[object] = []
    contact_points: list[object] = []
    article_count = 0
    detail_webpage_count = 0
    service_count = 0
    for candidate in candidates:
        graph = graph_from_candidate(candidate)
        telephone_values.extend(iter_key_values(graph, "telephone"))
        contact_points.extend(iter_key_values(graph, "contactPoint"))
        for article in nodes_of_type(graph["@graph"], "Article"):
            article_count += 1
            if article.get("author") != {"@id": ROOT_ORGANIZATION_ID}:
                raise GateError(f"{candidate.path}: global Article.author mismatch")
            if article.get("publisher") != {"@id": ROOT_ORGANIZATION_ID}:
                raise GateError(f"{candidate.path}: global Article.publisher mismatch")
        if candidate.is_detail:
            detail_webpage_count += len(nodes_of_type(graph["@graph"], "WebPage"))
            service_count += len(nodes_of_type(graph["@graph"], "Service"))
            organization = one_node(
                graph["@graph"], "EducationalOrganization", candidate.path
            )
            physical_projections[str(organization.get("@id"))].add(
                json.dumps(
                    organization,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
            )

    if telephone_values != ["+82-10-6839-8283"]:
        raise GateError(
            f"central telephone must occur once inside root ContactPoint: {telephone_values}"
        )
    if len(contact_points) != 1:
        raise GateError(f"root ContactPoint occurrence count is {len(contact_points)}")
    if detail_webpage_count != EXPECTED_DETAIL_PAGES:
        raise GateError(f"detail WebPage count mismatch: {detail_webpage_count}")
    expected_services = EXPECTED_DETAIL_PAGES - EXPECTED_UNSUPPORTED_PAGES
    if service_count != expected_services:
        raise GateError(f"supported page Service count mismatch: {service_count}")
    if set(physical_projections) != {
        physical.entity_id for physical in physical_by_key.values()
    }:
        raise GateError("physical organization @id coverage mismatch")
    conflicts = {
        entity_id: len(projections)
        for entity_id, projections in physical_projections.items()
        if len(projections) != 1
    }
    if conflicts:
        raise GateError(f"stable physical @id has conflicting projections: {conflicts}")
    return {
        "unsupported_by_category": unsupported_by_profile,
        "unsupported_pages": unsupported_total,
        "supported_service_pages": expected_services,
        "physical_center_ids": len(physical_projections),
        "article_root_reference_pages": article_count,
    }


SKIPPED_TREE_NAMES = {".git", ".vercel", "tmp", "__pycache__"}


def external_manifest(target_paths: set[Path]) -> dict[str, str]:
    manifest: dict[str, str] = {}
    for current_root, directory_names, file_names in os.walk(ROOT, topdown=True):
        directory_names[:] = [
            name for name in directory_names if name not in SKIPPED_TREE_NAMES
        ]
        directory = Path(current_root)
        for file_name in sorted(file_names):
            path = directory / file_name
            if path in target_paths:
                continue
            relative = path.relative_to(ROOT).as_posix()
            manifest[relative] = sha256_bytes(path.read_bytes())
    return manifest


def target_source_manifest(candidates: list[Candidate]) -> dict[str, str]:
    return {
        candidate.relative_path: sha256_bytes(candidate.path.read_bytes())
        for candidate in candidates
    }


def digest_manifest(manifest: object) -> str:
    payload = json.dumps(
        manifest,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return sha256_text(payload)


def candidate_contract_manifest(candidates: list[Candidate]) -> dict[str, dict[str, str]]:
    return {
        candidate.relative_path: {
            "canonical": candidate.canonical,
            "source_sha256": candidate.source_sha256,
            "non_schema_sha256": candidate.non_schema_sha256,
            "result_sha256": sha256_text(candidate.transformed),
        }
        for candidate in candidates
    }


def atomic_write(path: Path, text: str) -> None:
    descriptor, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.smallclass-schema-",
        suffix=".tmp",
        dir=path.parent,
    )
    temp_path = Path(temp_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(text.encode("utf-8"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    finally:
        if temp_path.exists():
            temp_path.unlink()


def apply_candidates(
    candidates: list[Candidate],
    external_before: dict[str, str],
    authoritative_source_sha256: str,
) -> int:
    target_paths = {candidate.path for candidate in candidates}
    if sha256_bytes(CENTER_CSV.read_bytes()) != authoritative_source_sha256:
        raise GateError("authoritative center CSV changed before apply")
    if external_manifest(target_paths) != external_before:
        raise GateError("external file changed between dry-run build and apply")
    for candidate in candidates:
        if sha256_text(read_utf8_exact(candidate.path)) != candidate.source_sha256:
            raise GateError(f"concurrent target edit detected: {candidate.path}")

    changed = [
        candidate for candidate in candidates
        if candidate.original != candidate.transformed
    ]
    written: list[Candidate] = []
    try:
        for candidate in changed:
            atomic_write(candidate.path, candidate.transformed)
            written.append(candidate)
        for candidate in candidates:
            current = read_utf8_exact(candidate.path)
            if current != candidate.transformed:
                raise GateError(f"post-apply target hash mismatch: {candidate.path}")
            if sha256_text(html_without_graph(current, candidate.path)) != candidate.non_schema_sha256:
                raise GateError(f"post-apply non-JSON bytes changed: {candidate.path}")
        if external_manifest(target_paths) != external_before:
            raise GateError("external file manifest changed during apply")
        if sha256_bytes(CENTER_CSV.read_bytes()) != authoritative_source_sha256:
            raise GateError("authoritative center CSV changed during apply")
    except Exception:
        for candidate in reversed(written):
            atomic_write(candidate.path, candidate.original)
        raise
    return len(changed)


def build_all_candidates(
    by_slug: dict[str, SourceRow],
    physical_by_key: dict[str, PhysicalCenter],
) -> list[Candidate]:
    candidates = [
        build_detail_candidate(
            path,
            profile,
            source,
            physical_by_key[source.center_key],
        )
        for path, profile, source in discover_detail_pages(by_slug)
    ]
    candidates.extend(
        build_non_detail_candidate(path) for path in discover_non_detail_pages()
    )
    relative_paths = [candidate.relative_path for candidate in candidates]
    if len(relative_paths) != len(set(relative_paths)):
        raise GateError("duplicate target path in candidate plan")
    return candidates


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Phase-1 JSON-LD accuracy repair for 소수정예학원.com. "
            "Default mode is a read-only dry-run; --apply writes only the "
            "single existing JSON-LD payload in each validated HTML target."
        )
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="atomically write validated JSON-LD-only changes (default: dry-run)",
    )
    args = parser.parse_args()

    authoritative_source_sha256 = sha256_bytes(CENTER_CSV.read_bytes())
    by_slug, physical_by_key = load_sources()
    detail_discovery = discover_detail_pages(by_slug)
    non_detail_discovery = discover_non_detail_pages()
    target_paths = {
        path for path, _, _ in detail_discovery
    } | set(non_detail_discovery)
    external_before = external_manifest(target_paths)
    candidates = build_all_candidates(by_slug, physical_by_key)
    global_report = validate_global_contract(candidates, physical_by_key)
    target_before = {
        candidate.relative_path: candidate.source_sha256 for candidate in candidates
    }
    if target_source_manifest(candidates) != target_before:
        raise GateError("target file changed during in-memory dry-run")
    if external_manifest(target_paths) != external_before:
        raise GateError("external file changed during in-memory dry-run")
    if sha256_bytes(CENTER_CSV.read_bytes()) != authoritative_source_sha256:
        raise GateError("authoritative center CSV changed during in-memory dry-run")

    changed = [
        candidate for candidate in candidates
        if candidate.original != candidate.transformed
    ]
    changed_by_category = {
        profile.slug: sum(
            candidate.original != candidate.transformed
            and candidate.profile == profile
            for candidate in candidates
        )
        for profile in PROFILES
    }
    written_pages = 0
    if args.apply:
        written_pages = apply_candidates(
            candidates,
            external_before,
            authoritative_source_sha256,
        )

    report = {
        "status": "pass",
        "mode": "apply" if args.apply else "dry-run",
        "target_pages": len(candidates),
        "detail_pages": EXPECTED_DETAIL_PAGES,
        "non_detail_pages": EXPECTED_NON_DETAIL_PAGES,
        "physical_centers": len(physical_by_key),
        "changed_pages": len(changed),
        "unchanged_pages": len(candidates) - len(changed),
        "written_pages": written_pages,
        "changed_detail_by_category": changed_by_category,
        **global_report,
        "candidate_contract_manifest_sha256": digest_manifest(
            candidate_contract_manifest(candidates)
        ),
        "external_untouched_manifest_sha256": digest_manifest(external_before),
        "authoritative_center_csv_sha256": authoritative_source_sha256,
        "json_ld_outside_bytes_gate": "pass",
        "url_title_h1_visible_source_gate": "pass",
        "article_visible_parity_gate": "pass",
        "schema_manuscript_irrelevant_hard_zero_gate": "pass",
        "faq_school_grade_parity_gate": "pass",
        "stable_physical_id_gate": "pass",
        "central_contact_root_only_gate": "pass",
        "unsupported_claim_removal_gate": "pass",
        "idempotency_gate": "pass",
        "tmp_tree": "excluded-from-discovery-and-hashing",
    }
    if args.apply:
        report["post_apply_gate"] = "pass"
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except GateError as error:
        print(
            json.dumps(
                {"status": "fail", "error": str(error)},
                ensure_ascii=False,
                indent=2,
            )
        )
        raise SystemExit(1)
