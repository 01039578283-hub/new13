from __future__ import annotations

"""Strict, read-only release audit for 소수정예학원.com phase 1.

The auditor never rewrites the website.  Its only optional write is a JSON
report explicitly directed outside the repository.  It verifies that the
existing 3,378 public URLs stay immutable while facts and schema are aligned
with the five authoritative CSV files in ``참고자료/공통자료``.
"""

import argparse
import csv
import hashlib
import html
import json
import os
import re
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import quote, unquote, urljoin, urlsplit


ROOT = Path(__file__).resolve().parents[1]
COMMON = ROOT.parent / "참고자료" / "공통자료"
CENTER_CSV = COMMON / "센터정보 정리.csv"
ORG_CSV = COMMON / "EducationalOrganization.csv"
BASE_URL = "https://xn--9l4b2jy4h4wan0chw4b.com"
ROOT_ORG_ID = f"{BASE_URL}/#organization"
ROOT_SCHEMA_PHONE = "+82-10-6839-8283"
ACADEMY_ROOT = ROOT / "전국학원"


PROFILES = {
    "고등수학학원": {
        "subject": "수학", "subjects": ("수학",), "prefix": "고", "level": "고등",
        "unsupported": 17,
    },
    "고등영어학원": {
        "subject": "영어", "subjects": ("영어",), "prefix": "고", "level": "고등",
        "unsupported": 9,
    },
    "수학학원": {
        "subject": "수학", "subjects": ("수학",), "prefix": None, "level": "전체",
        "unsupported": 13,
    },
    "영수학원": {
        "subject": "영어·수학", "subjects": ("영어", "수학"), "prefix": None, "level": "전체",
        "unsupported": 17,
    },
    "영어학원": {
        "subject": "영어", "subjects": ("영어",), "prefix": None, "level": "전체",
        "unsupported": 8,
    },
    "중등수학학원": {
        "subject": "수학", "subjects": ("수학",), "prefix": "중", "level": "중등",
        "unsupported": 13,
    },
    "중등영어학원": {
        "subject": "영어", "subjects": ("영어",), "prefix": "중", "level": "중등",
        "unsupported": 8,
    },
    "초등수학학원": {
        "subject": "수학", "subjects": ("수학",), "prefix": "초", "level": "초등",
        "unsupported": 13,
    },
    "초등영어학원": {
        "subject": "영어", "subjects": ("영어",), "prefix": "초", "level": "초등",
        "unsupported": 8,
    },
}

# ``read_csv`` normalizes the source header's embedded newline to one space.
GRADE_COLUMNS = {"영어": "가능학년 (영어)", "수학": "가능학년 (수학)"}
SCHOOL_COLUMNS = ("타깃학교 (초)", "타깃학교 (중)", "타깃학교 (고)")
REGION_NAMES = {
    "서울": "서울특별시", "서울특별시": "서울특별시",
    "경기": "경기도", "경기도": "경기도",
    "인천": "인천광역시", "인천광역시": "인천광역시",
    "부산": "부산광역시", "부산광역시": "부산광역시",
    "대구": "대구광역시", "대구광역시": "대구광역시",
    "대전": "대전광역시", "대전광역시": "대전광역시",
    "광주": "광주광역시", "광주광역시": "광주광역시",
    "울산": "울산광역시", "울산광역시": "울산광역시",
    "세종": "세종특별자치시", "세종시": "세종특별자치시",
    "세종특별자치시": "세종특별자치시",
    "강원": "강원특별자치도", "강원도": "강원특별자치도",
    "강원특별자치도": "강원특별자치도",
    "충북": "충청북도", "충청북도": "충청북도",
    "충남": "충청남도", "충청남도": "충청남도",
    "전북": "전북특별자치도", "전라북도": "전북특별자치도",
    "전북특별자치도": "전북특별자치도",
    "전남": "전라남도", "전라남도": "전라남도",
    "경북": "경상북도", "경상북도": "경상북도",
    "경남": "경상남도", "경상남도": "경상남도",
    "제주": "제주특별자치도", "제주도": "제주특별자치도",
    "제주특별자치도": "제주특별자치도",
}
OFFICIAL_REGION_DISTRICT_RE = re.compile(
    rf"(?:{'|'.join(sorted({re.escape(value) for value in REGION_NAMES.values()}, key=len, reverse=True))})"
    r"\s+[가-힣0-9]+(?:시|군|구)\s+평상시에는"
)

KNOWN_BAD_TEXT = (
    "중등 영어은", "초등 영어은", "고등 영어은", "사대부고을", "여고을", "외고을",
    "초을 확인", "학교을 확인", "요일를", "시간를", "범위을", "결과을", "진도을",
    "분류을", "학원로", "과제 완료 기준을 기준으로", "상담 준비에서는 다음 기준을 적용합니다:",
    "검색엔진", "상위노출", "SEO", "지역명을 바꾼 홍보 문구", "정보성 페이지 형태",
    "지역 페이지는 검색어에 바로 답해야 합니다", "고등 과정 과정", "초등초등학생",
    "학부모 상담 준비 상담 전에", "학원 상담 질문 상담 전에",
    "내신 수학은 학교 수업 기록에서 시작합니다이 실제 수업에서 지켜지는지",
    "검색한 학부모가", "검색어가 강조되더라도",
    "확인 가능한 학교 참고 자료 중 하나로",
    "나누는 방식이 동시에 확인해 두는 편이 좋습니다.",
    "상담 안내와 관계없이",
    "보다 실제로 시작하는 데 걸리는 시간이 실제로 확인되는지",
    "보다 실제 완료율과 수정 기록이 실제로 확인되는지",
    "상담에서는 제공 자료에는",
)
KNOWN_BAD_REGEX = (
    re.compile(r"[가-힣]+(?:고|여고|외고|초)을(?:\s|<)"),
    re.compile(r"(?:영어학원|영어 수업)[^.!?]{0,100}수학 학습 설계"),
    re.compile(r"수학학원(?:\s*[·,]\s*수학학원){2,}"),
    re.compile(r"(?:초등|중등|고등)\s+영어은"),
    re.compile(r"[가-힣]+관리을(?![가-힣])"),
    re.compile(
        r"(?:상담|(?:[가-힣]+(?:\s+[가-힣]+){0,4})?학원)에서는\s+제공\s+자료에는"
    ),
)
MANUSCRIPT_META_REGEX = re.compile(
    r"검색|(?<![A-Za-z])SEO(?![A-Za-z])|키워드",
    re.IGNORECASE,
)
IRRELEVANT_SEED_REGEX = re.compile(
    r"학원\s*(?:"
    r"개원|창업|전자\s*계약|매니저|공지|운영(?:자)?|개인\s*정보\s*관리|"
    r"매출\s*관리|수납\s*관리|고객\s*관리(?:\s*시스템)?|회원\s*관리|"
    r"수강생\s*관리|문서\s*관리|데이터\s*관리|예약\s*관리|상담\s*관리|"
    r"결제\s*(?:관리|시스템)|미납\s*관리|관리\s*(?:프로그램|솔루션|앱)|"
    r"출결\s*앱|직원|상담\s*직원|코디네이터|데스크|행정|브랜드|"
    r"프로모션|이벤트|모집|온라인\s*등록|재\s*등록|휴원|이전|"
    r"문자\s*발송|자료\s*실|소식|알림\s*톡|할인|혜택|환불"
    r")",
    re.IGNORECASE,
)
BROKEN_QUOTE_TAILS = (
    "상담에서 어떤 내용을 확인해야 하나요?”라고 확인하면 됩니다.",
    "상담 전에 준비할 자료는 무엇인가요?”라고 확인하면 됩니다.",
    "학습 계획은 어떤 기준으로 비교해야 하나요?”라고 확인하면 됩니다.",
    "상담 안내는 어떤 기준으로 확인해야 하나요?”라고 확인하면 됩니다.",
)
UNCONFIRMED_CUES = (
    "제공된 센터 자료", "기재되어 있지 않", "실제 개설 여부", "대상 학년", "상담 전",
)
PARTICLE_TERMS = (
    "와와학습코칭학원", "학습관리", "오답노트", "서술형", "학부모", "남기기",
    "이해도", "그래프", "교습비", "관리", "시험", "오답", "목적", "영어", "태그",
    "안내", "공부", "찾기", "암기", "준비", "비교", "설계", "순서", "지표", "이유",
    "학원", "학교", "층",
)
PHYSICAL_FORBIDDEN = (
    "telephone", "contactPoint", "openingHours", "openingHoursSpecification", "sameAs",
)
PHYSICAL_ALLOWED = {
    "@type", "@id", "name", "address", "identifier", "teaches", "makesOffer", "areaServed",
}
INFORMATIONAL_CODES = {
    "og_image_absolute", "og_meta", "twitter_meta", "rss_discovery",
    "internal_redirect_link", "image_alt", "image_dimensions", "hidden_image",
    "hub_script", "hub_directory", "hub_search_input", "hub_search_clear",
    "hub_search_status", "hub_search_scope",
    "browser_hub_search",
}


def clean(value: object) -> str:
    return re.sub(r"\s+", " ", html.unescape(str(value or ""))).strip()


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def set_sha(values: list[str] | set[str]) -> str:
    return sha256_bytes(("\n".join(sorted(values)) + "\n").encode("utf-8"))


def as_list(value: object) -> list:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def split_values(value: object) -> list[str]:
    return list(dict.fromkeys(
        clean(part) for part in re.split(r"[,/·，\n]+", clean(value)) if clean(part)
    ))


def split_schools(value: object) -> list[str]:
    normalized = clean(value)
    if not normalized or "지역내 모든 고등학교 가능" in normalized:
        return []
    parts = [
        clean(part) for part in re.split(r"[,/·.，\r\n]+", normalized) if clean(part)
    ]
    suffix = re.compile(r"(?:초등학교|중학교|고등학교|초|중|고)$")
    result: list[str] = []
    for part in parts:
        tokens = part.split()
        result.extend(tokens if len(tokens) > 1 and all(suffix.search(token) for token in tokens)
                      else [part])
    return list(dict.fromkeys(result))


def normalize_locality(value: object) -> str:
    return re.sub(r"\s+", "", clean(value))


def has_batchim(value: str) -> bool:
    for char in reversed(clean(value)):
        if "가" <= char <= "힣":
            return (ord(char) - ord("가")) % 28 != 0
    return False


def wrong_particles(value: str, terms: list[str] | tuple[str, ...]) -> list[str]:
    pairs = {
        "을": ("을", "를"), "를": ("을", "를"),
        "이": ("이", "가"), "가": ("이", "가"),
        "은": ("은", "는"), "는": ("은", "는"),
        "과": ("과", "와"), "와": ("과", "와"),
        "이라는": ("이라는", "라는"), "라는": ("이라는", "라는"),
    }
    errors: list[str] = []
    for term in sorted(set(clean(item) for item in terms if clean(item)), key=len, reverse=True):
        pattern = re.compile(
            rf"(?<![가-힣A-Za-z0-9]){re.escape(term)}"
            rf"(?P<p>이라는|라는|을|를|이|가|은|는|과|와)(?![가-힣A-Za-z0-9])"
        )
        expected_index = 0 if has_batchim(term) else 1
        for match in pattern.finditer(value):
            if match.group("p") != pairs[match.group("p")][expected_index]:
                errors.append(match.group(0))
                if len(errors) == 5:
                    return errors
    return errors


def node_types(node: dict) -> set[str]:
    value = node.get("@type", [])
    return {clean(item) for item in as_list(value) if clean(item)}


def ref_id(value: object) -> str:
    return clean(value.get("@id")) if isinstance(value, dict) else clean(value)


def semantic_url(value: str) -> str:
    parsed = urlsplit(value)
    path = re.sub(r"/+", "/", unquote(parsed.path or "/"))
    if path.endswith("/index.html"):
        path = path[:-10]
    elif path.endswith(".html"):
        path = path[:-5] + "/"
    if path != "/":
        path = path.rstrip("/") + "/"
    return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}{quote(path, safe='/')}"


def schema_id(base: str, value: object) -> str:
    """Resolve and normalize a JSON-LD identifier without discarding its fragment."""
    raw = clean(value)
    if not raw:
        return ""
    parsed = urlsplit(urljoin(base, raw))
    normalized = semantic_url(
        f"{parsed.scheme or urlsplit(base).scheme}://{parsed.netloc or urlsplit(base).netloc}"
        f"{parsed.path or '/'}"
    )
    return f"{normalized}#{parsed.fragment}" if parsed.fragment else normalized


def schema_ref(base: str, value: object) -> str:
    return schema_id(base, value.get("@id")) if isinstance(value, dict) else schema_id(base, value)


def page_url(path: Path, root: Path = ROOT) -> str:
    rel = path.relative_to(root).as_posix()
    if rel == "index.html":
        return f"{BASE_URL}/"
    assert rel.endswith("/index.html")
    return f"{BASE_URL}/{quote(rel[:-10], safe='/')}"


def all_index_pages(root: Path = ROOT) -> list[Path]:
    pages = [root / "index.html"]
    pages.extend(sorted(path for path in root.rglob("index.html") if path != root / "index.html"))
    return [path for path in pages if ".git" not in path.parts and "tmp" not in path.parts]


def detail_pages(root: Path = ROOT) -> list[Path]:
    result: list[Path] = []
    for slug in PROFILES:
        result.extend(sorted((root / ACADEMY_ROOT.name / slug).glob("*/index.html")))
    return result


def sitemap_urls(root: Path = ROOT) -> list[str]:
    xml = ET.parse(root / "sitemap.xml").getroot()
    return [clean(node.text) for node in xml.iter() if node.tag.endswith("loc")]


@dataclass
class Node:
    tag: str
    attrs: dict[str, str] = field(default_factory=dict)
    parent: "Node | None" = None
    children: list["Node | str"] = field(default_factory=list)

    def text(self) -> str:
        pieces: list[str] = []

        def visit(item: Node | str) -> None:
            if isinstance(item, str):
                pieces.append(item)
            elif item.tag not in {"style"}:
                for child in item.children:
                    visit(child)

        visit(self)
        return clean(" ".join(pieces))

    def descendants(self, include_self: bool = False):
        if include_self:
            yield self
        for child in self.children:
            if isinstance(child, Node):
                yield child
                yield from child.descendants()

    def has_class(self, name: str) -> bool:
        return name in self.attrs.get("class", "").split()

    def find_all(self, tag: str | None = None, cls: str | None = None,
                 attr: str | None = None, value: str | None = None) -> list["Node"]:
        result = []
        for node in self.descendants(include_self=True):
            if tag and node.tag != tag:
                continue
            if cls and not node.has_class(cls):
                continue
            if attr and attr not in node.attrs:
                continue
            if attr and value is not None and node.attrs.get(attr) != value:
                continue
            result.append(node)
        return result

    def closest(self, tag: str) -> "Node | None":
        node: Node | None = self
        while node:
            if node.tag == tag:
                return node
            node = node.parent
        return None


class DOMParser(HTMLParser):
    VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = Node("document")
        self.stack = [self.root]

    def handle_starttag(self, tag: str, attrs) -> None:
        node = Node(tag.lower(), {clean(k).lower(): clean(v) for k, v in attrs}, self.stack[-1])
        self.stack[-1].children.append(node)
        if node.tag not in self.VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag: str, attrs) -> None:
        self.handle_starttag(tag, attrs)
        if self.stack[-1].tag == tag.lower():
            self.stack.pop()

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                break

    def handle_data(self, data: str) -> None:
        self.stack[-1].children.append(data)


def parse_dom(source: str) -> Node:
    parser = DOMParser()
    parser.feed(source)
    return parser.root


def rendered_text(dom: Node) -> str:
    pieces: list[str] = []

    def visit(item: Node | str) -> None:
        if isinstance(item, str):
            pieces.append(item)
        elif item.tag not in {"script", "style"}:
            for child in item.children:
                visit(child)

    visit(dom)
    return clean(" ".join(pieces))


def detail_main_text(dom: Node, *, exclude_source_markers: bool = False,
                     exclude_classes: tuple[str, ...] = ()) -> tuple[str, int]:
    """Return rendered ``main`` copy, never attributes, URLs, scripts, or styles.

    Detail-only callers keep education-information pages outside the scope.  The
    Optional exclusions keep visibly marked source disclaimers, or structured
    fact cards that have their own exact gates, from tripping prose semantics.
    """
    mains = dom.find_all("main")
    pieces: list[str] = []

    def visit(item: Node | str) -> None:
        if isinstance(item, str):
            pieces.append(item)
            return
        if item.tag in {"script", "style"}:
            return
        if (exclude_source_markers and
                item.attrs.get("data-source-status") == "unconfirmed-grade"):
            return
        if any(item.has_class(class_name) for class_name in exclude_classes):
            return
        for child in item.children:
            visit(child)

    for main in mains:
        visit(main)
    return clean(" ".join(pieces)), len(mains)


def standalone_quote_tails(text: str) -> list[str]:
    """Return ``[?.]”라고`` tails that have no opener in their sentence."""
    failures: list[str] = []
    for match in re.finditer(r"[?.]”라고", text):
        before = text[:match.start()]
        boundary = max(before.rfind(". "), before.rfind("! "), before.rfind("? "))
        prefix = before[boundary + 2:] if boundary >= 0 else before
        if prefix.count("“") <= prefix.count("”"):
            start = max(0, match.start() - 100)
            end = min(len(text), match.end() + 80)
            failures.append(clean(text[start:end]))
            if len(failures) == 5:
                break
    return failures


def first(nodes: list[Node]) -> Node | None:
    return nodes[0] if nodes else None


def meta_values(dom: Node, key: str, name: str) -> list[str]:
    return [node.attrs.get("content", "") for node in dom.find_all("meta")
            if node.attrs.get(key, "").lower() == name.lower()]


def link_values(dom: Node, rel: str) -> list[str]:
    return [node.attrs.get("href", "") for node in dom.find_all("link")
            if rel.lower() in node.attrs.get("rel", "").lower().split()]


def parse_jsonld(dom: Node) -> tuple[list[dict], list[str]]:
    roots: list[dict] = []
    errors: list[str] = []
    for script in dom.find_all("script"):
        if script.attrs.get("type", "").lower() != "application/ld+json":
            continue
        try:
            value = json.loads(script.text())
            roots.extend(item for item in as_list(value) if isinstance(item, dict))
        except Exception as exc:
            errors.append(str(exc))
    return roots, errors


def top_nodes(roots: list[dict]) -> list[dict]:
    result: list[dict] = []
    for root in roots:
        graph = root.get("@graph")
        if isinstance(graph, list):
            result.extend(item for item in graph if isinstance(item, dict))
        else:
            result.append(root)
    return result


def walk_dicts(value: object):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from walk_dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk_dicts(child)


def walk_string_values(value: object):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for child in value.values():
            yield from walk_string_values(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk_string_values(child)


def normalized_topic(value: object) -> str:
    return re.sub(r"[^0-9A-Za-z가-힣]", "", clean(value)).lower()


def visible_faq(dom: Node) -> list[tuple[str, str]]:
    result: list[tuple[str, str]] = []
    for details in dom.find_all("details"):
        if not details.has_class("academy-faq-item"):
            continue
        question = first(details.find_all("summary"))
        answer = first(details.find_all("p"))
        if question and answer:
            result.append((question.text(), answer.text()))
    return result


def schema_faq(nodes: list[dict]) -> list[tuple[str, str]]:
    page = next((node for node in nodes if "FAQPage" in node_types(node)), {})
    result = []
    for question in as_list(page.get("mainEntity")):
        if not isinstance(question, dict):
            continue
        answer = question.get("acceptedAnswer", {})
        result.append((clean(question.get("name")), clean(answer.get("text") if isinstance(answer, dict) else "")))
    return result


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return [{clean(key): clean(value) for key, value in row.items()} for row in csv.DictReader(handle)]


def grades_for(row: dict[str, str], subject: str, prefix: str | None = None) -> list[str]:
    values = split_values(row.get(GRADE_COLUMNS[subject], ""))
    return [grade for grade in values if not prefix or grade.startswith(prefix)]


def schools_for(row: dict[str, str], config: dict | None = None) -> list[str]:
    columns = SCHOOL_COLUMNS
    if config and config.get("prefix") in {"초", "중", "고"}:
        columns = (SCHOOL_COLUMNS[{"초": 0, "중": 1, "고": 2}[config["prefix"]]],)
    values: list[str] = []
    for column in columns:
        values.extend(split_schools(row.get(column, "")))
    return list(dict.fromkeys(values))


def profile_support(row: dict[str, str], config: dict) -> dict[str, list[str]]:
    return {subject: grades_for(row, subject, config["prefix"]) for subject in config["subjects"]}


def unsupported_grade_claims(text: str, support: dict[str, list[str]]) -> list[str]:
    result: list[str] = []
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        tokens = set(re.findall(r"(?:초[1-6]|[중고][1-3])", sentence))
        if not tokens or not re.search(
            r"(?:학생.{0,24}(?:맞는|대상|위한).{0,24}(?:수업|학습|관리)|"
            r"(?:수업|학습)\s*(?:가능|대상)\s*학년|"
            r"학년.{0,16}(?:수업|학습).{0,16}(?:가능|제공|운영))",
            sentence,
        ):
            continue
        named = [subject for subject in support if subject in sentence]
        if len(support) > 1 and ("영수" in sentence or len(named) != 1):
            allowed = set.intersection(*(set(values) for values in support.values()))
        elif named:
            allowed = set(support[named[0]])
        else:
            allowed = set().union(*(set(values) for values in support.values()))
        extra = tokens - allowed
        if extra:
            result.append(f"{sorted(extra)}: {clean(sentence)[:180]}")
            if len(result) == 5:
                break
    return result


def duplicate_school_names(text: str, schools: set[str]) -> set[str]:
    return {
        school for school in schools
        if re.search(
            rf"학교 정보에는[^.!?]{{0,100}}{re.escape(school)}(?:과|와)\s*"
            rf"{re.escape(school)}(?:이|가)\s*포함",
            text,
        )
    }


def unsupported_service_certainty_claims(text: str, row: dict[str, str]) -> list[str]:
    """Find affirmative service copy on a source-unsupported detail page.

    The text passed here has already excluded all visibly marked source-status
    notices.  Only the conservative, source-unconfirmed manuscript is allowed;
    locality/centre-specific lesson operations and affirmative provision verbs
    are not, because the source has no grades substantiating that service.
    """
    scope_terms = {
        clean(row.get("근처 수업가능 동네")), clean(row.get("센터명")),
        clean(row.get("시or구")), clean(row.get("지역")),
    }
    scope_pattern = "|".join(
        sorted((re.escape(value) for value in scope_terms if value), key=len, reverse=True)
    ) or r"(?!)"
    patterns = (
        ("scoped_lesson", re.compile(rf"(?:{scope_pattern}|지역|센터)\s*수업에서는")),
        ("lesson_possessive", re.compile(r"수업의")),
        ("lesson_started", re.compile(r"수업이\s*시작되면")),
        ("actual_lesson", re.compile(r"실제\s*수업에서")),
        ("school_service_range", re.compile(
            r"학교\s*(?:목록|정보)(?:은|는)?[^.!?]{0,32}수업\s*가능\s*범위"
        )),
        ("student_service_target", re.compile(
            r"학생[^.!?]{0,24}(?:맞는|위한)[^.!?]{0,24}(?:수업|학습|관리)"
        )),
        ("affirmative_operation", re.compile(
            r"(?:제공|운영|진행)(?:한다|합니다|된다|됩니다|"
            r"하고\s*있(?:다|습니다)|되고\s*있(?:다|습니다))"
        )),
        ("affirmative_service_operation", re.compile(
            r"(?:(?:수업|서비스|교습|과정|프로그램|과목)[^.!?]{0,80}"
            r"(?:제공|운영|진행)(?:한다|합니다|된다|됩니다|하는|되는)|"
            r"(?:제공|운영|진행)(?:한다|합니다|된다|됩니다|하는|되는)"
            r"[^.!?]{0,80}(?:수업|서비스|교습|과정|프로그램|과목))"
        )),
    )
    found: list[str] = []
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        for label, pattern in patterns:
            if pattern.search(sentence):
                found.append(f"{label}: {clean(sentence)[:180]}")
                break
        if len(found) == 5:
            break
    return found


def source_group_key(row: dict[str, str]) -> tuple[str, str, str, str]:
    return (
        row.get("센터명", ""), row.get("센터 주소", ""),
        row.get("교육지원청명칭", ""), row.get("교육지원청 등록번호", ""),
    )


def stable_center_id(row: dict[str, str]) -> str:
    key = "|".join((row.get("센터명", ""), row.get("센터 주소", ""),
                    row.get("교육지원청 등록번호", "")))
    token = hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]
    return f"{BASE_URL}/#center-{token}"


def official_address_parts(address: str, areas: list[str] | tuple[str, ...]) -> tuple[str, str]:
    tokens = clean(address).replace(",", " ").split()
    region = REGION_NAMES.get(tokens[0], "") if tokens else ""
    if region == "세종특별자치시":
        matches = [area for area in areas if area.removesuffix("동") in address]
        locality = matches[0] if matches else (areas[0] if areas else "")
    else:
        locality = tokens[1] if len(tokens) > 1 else ""
    return region, locality


class Audit:
    def __init__(self) -> None:
        self.errors: list[str] = []
        self.counts: Counter[str] = Counter()
        self.warnings: list[str] = []
        self.warning_counts: Counter[str] = Counter()
        self.metrics: Counter[str] = Counter()
        self.samples: defaultdict[str, list[str]] = defaultdict(list)
        self.warning_samples: defaultdict[str, list[str]] = defaultdict(list)

    def check(self, condition: bool, code: str, message: str) -> None:
        if condition:
            return
        if code in INFORMATIONAL_CODES:
            self.warnings.append(f"[{code}] {message}")
            self.warning_counts[code] += 1
            if len(self.warning_samples[code]) < 5:
                self.warning_samples[code].append(message)
            return
        self.errors.append(f"[{code}] {message}")
        self.counts[code] += 1
        if len(self.samples[code]) < 5:
            self.samples[code].append(message)


def load_sources(audit: Audit, baseline: dict) -> tuple[list[dict], dict[str, dict], dict]:
    csv_hashes = {item["name"]: item["sha256"] for item in baseline.get("authoritative_csvs", [])}
    for path in sorted(COMMON.glob("*.csv")):
        audit.check(path.name in csv_hashes, "csv_set", f"unexpected CSV {path.name}")
        if path.name in csv_hashes:
            audit.check(sha256_file(path) == csv_hashes[path.name], "csv_hash", path.name)

    rows = read_csv(CENTER_CSV)
    org_rows = read_csv(ORG_CSV)
    audit.check(len(rows) == 371, "source_rows", f"center rows={len(rows)}")
    audit.check(len(org_rows) == 371, "source_org_rows", f"org rows={len(org_rows)}")
    by_locality: dict[str, dict] = {}
    for row in rows:
        key = normalize_locality(row.get("근처 수업가능 동네"))
        audit.check(bool(key) and key not in by_locality, "source_locality", key)
        by_locality[key] = row

    groups: defaultdict[tuple, list[dict]] = defaultdict(list)
    for row in rows:
        groups[source_group_key(row)].append(row)
    audit.check(len(groups) == 188, "source_org_count", f"groups={len(groups)}")

    org_map = {(normalize_locality(row.get("서비스 제공 지역"))): row for row in org_rows}
    audit.check(len(org_map) == 371, "source_org_locality", f"org localities={len(org_map)}")
    for key, row in by_locality.items():
        org = org_map.get(key, {})
        audit.check(org.get("실제 센터명") == row.get("센터명"), "source_org_name", key)
        audit.check(org.get("도로명 주소") == row.get("센터 주소"), "source_org_address", key)

    unsupported = Counter()
    for slug, config in PROFILES.items():
        unsupported[slug] = sum(
            not all(profile_support(row, config).values()) for row in rows
        )
        audit.check(unsupported[slug] == config["unsupported"], "unsupported_source",
                    f"{slug}={unsupported[slug]} expected={config['unsupported']}")
    audit.check(sum(unsupported.values()) == 106, "unsupported_total", str(dict(unsupported)))
    return rows, by_locality, {"groups": groups, "org_map": org_map, "unsupported": unsupported}


def baseline_gate(audit: Audit, baseline: dict, pages: list[Path]) -> None:
    expected = {item["url"] for item in baseline.get("html", [])}
    actual_files = {page_url(path) for path in pages}
    actual_sitemap = set(sitemap_urls())
    canonicals: list[str] = []
    for path in pages:
        dom = parse_dom(path.read_text(encoding="utf-8"))
        canonicals.extend(link_values(dom, "canonical"))
    actual_canonical = set(canonicals)
    audit.check(len(pages) == 3378, "page_count", f"pages={len(pages)}")
    audit.check(len(actual_sitemap) == 3378, "sitemap_count", f"sitemap={len(actual_sitemap)}")
    audit.check(len(canonicals) == 3378, "canonical_count", f"canonicals={len(canonicals)}")
    audit.check(actual_files == expected, "url_file_immutable",
                f"missing={len(expected-actual_files)} extra={len(actual_files-expected)}")
    audit.check(actual_sitemap == expected, "url_sitemap_immutable",
                f"missing={len(expected-actual_sitemap)} extra={len(actual_sitemap-expected)}")
    audit.check(actual_canonical == expected, "url_canonical_immutable",
                f"missing={len(expected-actual_canonical)} extra={len(actual_canonical-expected)}")
    expected_sha = baseline.get("sets", {}).get("sitemap_urls_sha256")
    audit.check(set_sha(actual_sitemap) == expected_sha, "url_set_sha", set_sha(actual_sitemap))

    expected_assets = {item["path"]: item for item in baseline.get("assets", [])}
    actual_asset_paths = {
        path.relative_to(ROOT).as_posix()
        for path in (ROOT / "assets").rglob("*") if path.is_file()
    }
    audit.check(actual_asset_paths == set(expected_assets), "asset_set_immutable",
                f"missing={len(set(expected_assets)-actual_asset_paths)} "
                f"extra={len(actual_asset_paths-set(expected_assets))}")
    immutable_assets = [
        name for name in sorted(set(expected_assets) & actual_asset_paths)
        if name not in {"assets/styles.css", "assets/script.js"}
    ]
    if immutable_assets:
        proc = subprocess.run(
            ["git", "hash-object", "--stdin-paths"], cwd=ROOT,
            input="\n".join(immutable_assets) + "\n", text=True,
            capture_output=True, encoding="utf-8",
        )
        actual_oids = proc.stdout.splitlines() if proc.returncode == 0 else []
        audit.check(proc.returncode == 0 and len(actual_oids) == len(immutable_assets),
                    "asset_hash_command", proc.stderr.strip())
        changed = [
            name for name, oid in zip(immutable_assets, actual_oids)
            if oid != expected_assets[name].get("git_oid")
        ]
        audit.check(not changed, "asset_content_immutable",
                    f"changed={len(changed)} sample={changed[:5]}")

    for name in ("vercel.json", "llms.txt", "404.html"):
        expected_item = baseline.get("auxiliary", {}).get(name, {})
        path = ROOT / name
        audit.check(path.is_file(), "auxiliary_missing", name)
        if path.is_file() and expected_item.get("working_sha256"):
            audit.check(sha256_file(path) == expected_item["working_sha256"],
                        "auxiliary_immutable", name)


def resolve_internal(canonical: str, href: str) -> str:
    return semantic_url(urljoin(canonical, href.split("#", 1)[0]))


def internal_file(value: str, base: str) -> Path | None:
    target = urljoin(base, value)
    if urlsplit(target).hostname not in {"xn--9l4b2jy4h4wan0chw4b.com", "소수정예학원.com", None}:
        return None
    path = unquote(urlsplit(target).path)
    candidate = ROOT / path.lstrip("/")
    return candidate / "index.html" if path.endswith("/") else candidate


def physical_org_node(nodes: list[dict], row: dict) -> dict:
    return next((node for node in nodes
                 if "EducationalOrganization" in node_types(node)
                 and clean(node.get("name")) == row.get("센터명")), {})


def page_service_nodes(nodes: list[dict], canonical: str) -> list[dict]:
    result = []
    for node in nodes:
        if "Service" not in node_types(node):
            continue
        identifier = urljoin(canonical, clean(node.get("@id")))
        if identifier.endswith("#service") or canonical.rstrip("/") in identifier:
            result.append(node)
    return result


def validate_root_org_scope(audit: Audit, rel: str, nodes: list[dict],
                            canonical: str, is_home: bool) -> None:
    roots = [
        node for node in nodes
        if {"Organization", "EducationalOrganization"} & node_types(node)
        and schema_id(canonical, node.get("@id")) == ROOT_ORG_ID
    ]
    for article in (node for node in nodes if "Article" in node_types(node)):
        audit.check(schema_ref(canonical, article.get("author")) == ROOT_ORG_ID,
                    "article_author_role", rel)
        audit.check(schema_ref(canonical, article.get("publisher")) == ROOT_ORG_ID,
                    "article_publisher_role", rel)
    if not is_home:
        audit.check(not roots, "root_org_redefined", f"{rel}: {len(roots)}")
        telephone_nodes = [node for node in walk_dicts(nodes) if clean(node.get("telephone"))]
        audit.check(not telephone_nodes, "schema_telephone_outside_home",
                    f"{rel}: {len(telephone_nodes)}")
        return
    audit.check(len(roots) == 1, "root_org_count", f"{rel}: {len(roots)}")
    if len(roots) != 1:
        return
    root = roots[0]
    audit.check(not root.get("telephone"), "root_top_level_telephone", rel)
    for field_name in ("openingHours", "openingHoursSpecification"):
        audit.check(not root.get(field_name), "root_unsupported_field", f"{rel}: {field_name}")
    points = [item for item in as_list(root.get("contactPoint")) if isinstance(item, dict)]
    audit.check(len(points) == 1, "root_contactpoint_count", f"{rel}: {len(points)}")
    phones = [clean(point.get("telephone")) for point in points]
    audit.check(phones == [ROOT_SCHEMA_PHONE], "root_contactpoint_phone", f"{rel}: {phones}")
    all_phones = [clean(node.get("telephone")) for node in walk_dicts(nodes)
                  if clean(node.get("telephone"))]
    audit.check(all_phones == [ROOT_SCHEMA_PHONE], "root_phone_scope", f"{rel}: {all_phones}")
    websites = [node for node in nodes if "WebSite" in node_types(node)]
    audit.check(len(websites) == 1, "root_website_count", f"{rel}: {len(websites)}")
    if len(websites) == 1:
        audit.check(schema_id(canonical, websites[0].get("@id")) == f"{BASE_URL}/#website",
                    "root_website_id", rel)
        audit.check(schema_ref(canonical, websites[0].get("publisher")) == ROOT_ORG_ID,
                    "root_website_publisher", rel)


def claim_text(node: dict) -> str:
    fields = [node.get("name"), node.get("serviceType"), node.get("description")]
    offered = node.get("itemOffered")
    if isinstance(offered, dict):
        fields.extend((offered.get("name"), offered.get("serviceType"), offered.get("description")))
    return clean(" ".join(clean(item) for item in fields))


def validate_detail_graph_shape(audit: Audit, rel: str, nodes: list[dict],
                                canonical: str, supported: bool) -> None:
    expected = {
        "EducationalOrganization": 1, "LocalBusiness": 1, "WebPage": 1,
        "BreadcrumbList": 1, "Article": 1, "FAQPage": 1, "ItemList": 1,
        "Service": 1 if supported else 0,
    }
    for kind, count in expected.items():
        actual = sum(kind in node_types(node) for node in nodes)
        audit.check(actual == count, "detail_schema_shape",
                    f"{rel}: {kind}={actual} expected={count}")
    identifiers = {
        "WebPage": "#webpage", "BreadcrumbList": "#breadcrumb",
        "Article": "#article", "FAQPage": "#faq",
    }
    if supported:
        identifiers["Service"] = "#service"
    for kind, fragment in identifiers.items():
        found = [node for node in nodes if kind in node_types(node)]
        if len(found) == 1:
            audit.check(schema_id(canonical, found[0].get("@id")) == schema_id(canonical, fragment),
                        "detail_schema_id", f"{rel}: {kind}")
    webpages = [node for node in nodes if "WebPage" in node_types(node)]
    if len(webpages) == 1:
        audit.check(semantic_url(clean(webpages[0].get("url"))) == semantic_url(canonical),
                    "webpage_url", rel)
        audit.check(schema_ref(canonical, webpages[0].get("mainEntity")) == schema_id(canonical, "#article"),
                    "webpage_main_entity", rel)
    articles = [node for node in nodes if "Article" in node_types(node)]
    if len(articles) == 1:
        audit.check(schema_ref(canonical, articles[0].get("mainEntityOfPage")) == schema_id(canonical, "#webpage"),
                    "article_main_entity", rel)


def validate_physical_org(audit: Audit, rel: str, row: dict, org: dict,
                          canonical: str, group: list[dict], seen_ids: dict) -> str:
    audit.check(bool(org), "physical_org_missing", rel)
    if not org:
        return ""
    audit.check(clean(org.get("name")) == row.get("센터명"), "physical_org_name", rel)
    extra_fields = set(org) - PHYSICAL_ALLOWED
    audit.check(not extra_fields, "physical_org_extra_claims",
                f"{rel}: {sorted(extra_fields)}")
    oid = schema_id(canonical, org.get("@id"))
    expected_oid = stable_center_id(row)
    audit.check(oid == expected_oid, "physical_org_id",
                f"{rel}: {oid} expected={expected_oid}")
    group_key = source_group_key(row)
    if group_key in seen_ids:
        audit.check(seen_ids[group_key] == oid, "physical_org_id_stability", rel)
    else:
        seen_ids[group_key] = oid
    for field_name in PHYSICAL_FORBIDDEN:
        audit.check(not org.get(field_name), "physical_org_unsupported_field",
                    f"{rel}: {field_name}")
    address = org.get("address", {}) if isinstance(org.get("address"), dict) else {}
    audit.check(clean(address.get("streetAddress")) == row.get("센터 주소"),
                "physical_org_address", rel)
    identifier = org.get("identifier", {})
    id_text = clean(identifier.get("value")) if isinstance(identifier, dict) else clean(identifier)
    audit.check(id_text == row.get("교육지원청 등록번호"), "physical_org_registration", rel)
    audit.check(isinstance(identifier, dict) and
                clean(identifier.get("name")) == row.get("교육지원청명칭"),
                "physical_org_registration_authority", rel)

    expected_areas = {item.get("근처 수업가능 동네", "") for item in group}
    actual_areas = {clean(item.get("name")) if isinstance(item, dict) else clean(item)
                    for item in as_list(org.get("areaServed"))}
    audit.check(actual_areas == expected_areas, "physical_org_area", f"{rel}: {actual_areas}")
    address_region, address_locality = official_address_parts(
        row.get("센터 주소", ""), sorted(expected_areas),
    )
    audit.check(clean(address.get("addressRegion")) == address_region and
                clean(address.get("addressLocality")) == address_locality and
                clean(address.get("addressCountry")) == "KR",
                "physical_org_postal_address",
                f"{rel}: {address.get('addressRegion')}/{address.get('addressLocality')}")

    support = {subject: sorted(set(
        grade for item in group for grade in grades_for(item, subject)
    )) for subject in ("영어", "수학")}
    expected_teaches = {subject for subject, values in support.items() if values}
    actual_teaches = {clean(item.get("name")) if isinstance(item, dict) else clean(item)
                      for item in as_list(org.get("teaches"))}
    audit.check(actual_teaches == expected_teaches, "physical_org_teaches",
                f"{rel}: actual={actual_teaches} expected={expected_teaches}")

    offer_subjects: dict[str, set[str]] = defaultdict(set)
    physical_offers = as_list(org.get("makesOffer"))
    audit.check(len(physical_offers) == len(expected_teaches), "physical_org_offer_count", rel)
    for offer in physical_offers:
        if not isinstance(offer, dict):
            continue
        audit.check(not (set(offer) - {"@type", "name", "itemOffered"}),
                    "physical_org_offer_extra_claims",
                    f"{rel}: {sorted(set(offer)-{'@type', 'name', 'itemOffered'})}")
        text = claim_text(offer)
        item = offer.get("itemOffered", {}) if isinstance(offer.get("itemOffered"), dict) else {}
        audit.check("Service" in node_types(item), "physical_org_offer_service", rel)
        audit.check(not (set(item) - {
            "@type", "@id", "name", "serviceType", "provider", "areaServed",
            "educationalLevel", "audience",
        }),
                    "physical_org_offer_service_extra_claims", rel)
        levels = {clean(value) for value in as_list(item.get("educationalLevel")) if clean(value)}
        audit.check(schema_ref(canonical, item.get("provider")) == oid,
                    "physical_org_offer_provider", rel)
        offer_areas = {clean(value.get("name")) if isinstance(value, dict) else clean(value)
                       for value in as_list(item.get("areaServed"))}
        audit.check(offer_areas == expected_areas, "physical_org_offer_area", rel)
        audience_tokens = set(re.findall(
            r"[초중고][1-6]", json.dumps(item.get("audience", {}), ensure_ascii=False)
        ))
        audit.check(audience_tokens == levels, "physical_org_offer_audience", rel)
        for subject in ("영어", "수학"):
            if subject in text:
                offer_subjects[subject].update(levels)
    audit.check(set(offer_subjects) == expected_teaches, "physical_org_offer_subjects", rel)
    for subject in expected_teaches:
        audit.check(offer_subjects[subject] == set(support[subject]), "physical_org_offer_grades",
                    f"{rel}: {subject} {offer_subjects[subject]} != {set(support[subject])}")
    return oid


def validate_truthfulness(audit: Audit, rel: str, source: str, dom: Node,
                          nodes: list[dict], row: dict, config: dict,
                          physical_id: str, canonical: str,
                          baseline_item: dict | None = None) -> None:
    support = profile_support(row, config)
    is_supported = all(support.values())
    markers = dom.find_all(attr="data-source-status", value="unconfirmed-grade")
    text = dom.text()
    services = page_service_nodes(nodes, canonical)
    articles = [node for node in nodes if "Article" in node_types(node)]
    offers = [node for node in walk_dicts(nodes) if "Offer" in node_types(node)]

    audit.check(len(articles) == 1, "article_count", f"{rel}: {len(articles)}")
    if is_supported:
        audit.check(not markers, "supported_marked_unconfirmed", rel)
        audit.check(len(services) == 1, "supported_service_count", f"{rel}: {len(services)}")
        allowed = set().union(*(set(values) for values in support.values()))
        for service in services:
            audit.check(schema_ref(canonical, service.get("provider")) == physical_id,
                        "service_provider_role", rel)
            levels = {clean(item) for item in as_list(service.get("educationalLevel")) if clean(item)}
            audit.check(levels == allowed, "service_grade_source", f"{rel}: {levels}")
            service_areas = {clean(value.get("name")) if isinstance(value, dict) else clean(value)
                             for value in as_list(service.get("areaServed"))}
            audit.check(service_areas == {row.get("근처 수업가능 동네", "")},
                        "service_area_source", rel)
            audience_tokens = set(re.findall(
                r"[초중고][1-6]", json.dumps(service.get("audience", {}), ensure_ascii=False)
            ))
            audit.check(audience_tokens == allowed, "service_audience_grade_source",
                        f"{rel}: actual={audience_tokens} expected={allowed}")
            page_offers = as_list(service.get("offers")) + as_list(service.get("makesOffer"))
            audit.check(len(page_offers) == len(config["subjects"]),
                        "service_offer_count", f"{rel}: {len(page_offers)}")
            offer_support: dict[str, set[str]] = defaultdict(set)
            for offer in page_offers:
                if not isinstance(offer, dict):
                    continue
                audit.check(not offer.get("url") and not offer.get("price"),
                            "service_offer_fee_claim", rel)
                item = offer.get("itemOffered", {}) if isinstance(offer.get("itemOffered"), dict) else {}
                item_levels = {clean(value) for value in as_list(item.get("educationalLevel")) if clean(value)}
                audit.check(schema_ref(canonical, item.get("provider")) == physical_id,
                            "service_offer_provider", rel)
                item_areas = {clean(value.get("name")) if isinstance(value, dict) else clean(value)
                              for value in as_list(item.get("areaServed"))}
                audit.check(item_areas == {row.get("근처 수업가능 동네", "")},
                            "service_offer_area", rel)
                item_audience = set(re.findall(
                    r"[초중고][1-6]", json.dumps(item.get("audience", {}), ensure_ascii=False)
                ))
                audit.check(item_audience == item_levels, "service_offer_audience", rel)
                offer_text = claim_text(offer)
                for subject in config["subjects"]:
                    if subject in offer_text:
                        offer_support[subject].update(item_levels)
            audit.check(set(offer_support) == set(config["subjects"]),
                        "service_offer_subjects", rel)
            for subject in config["subjects"]:
                audit.check(offer_support[subject] == set(support[subject]),
                            "service_offer_grades", f"{rel}: {subject}")
        for article in articles:
            levels = {clean(item) for item in as_list(article.get("educationalLevel")) if clean(item)}
            audit.check(levels == allowed, "article_grade_source", f"{rel}: {levels-allowed}")
            audience_tokens = set(re.findall(
                r"[초중고][1-6]", json.dumps(article.get("audience", {}), ensure_ascii=False)
            ))
            audit.check(audience_tokens <= allowed, "article_audience_grade_source",
                        f"{rel}: {audience_tokens-allowed}")
    else:
        audit.metrics["unsupported_seen"] += 1
        unmarked_main, _ = detail_main_text(dom, exclude_source_markers=True)
        certainty_claims = unsupported_service_certainty_claims(unmarked_main, row)
        audit.check(not certainty_claims, "unsupported_visible_service_certainty",
                    f"{rel}: {certainty_claims}")
        audit.check(len(markers) >= 3, "unsupported_markers", f"{rel}: markers={len(markers)}")
        required_classes = (
            "academy-source-status-row", "academy-grade-empty",
            "academy-source-unconfirmed", "academy-source-status-faq",
        )
        for class_name in required_classes:
            audit.check(bool(dom.find_all(cls=class_name)), "unsupported_structure",
                        f"{rel}: {class_name}")
        top_notes = dom.find_all(cls="academy-source-status-row")
        audit.check(len(top_notes) == 1 and
                    top_notes[0].attrs.get("data-source-status") == "unconfirmed-grade",
                    "unsupported_top_marker", rel)
        if top_notes:
            top_text = top_notes[0].text()
            audit.check(all(cue in top_text for cue in UNCONFIRMED_CUES),
                        "unsupported_top_semantics", rel)
        for class_name in ("academy-grade-empty", "academy-source-unconfirmed",
                           "academy-source-status-faq"):
            marked = dom.find_all(cls=class_name)
            if class_name == "academy-grade-empty":
                marked = [node for node in marked if node.has_class("academy-source-unconfirmed-note")]
            audit.check(bool(marked) and all(
                node.attrs.get("data-source-status") == "unconfirmed-grade" for node in marked
            ), "unsupported_marker_contract", f"{rel}: {class_name}")
        for cue in UNCONFIRMED_CUES:
            audit.check(cue in text, "unsupported_semantic_cue", f"{rel}: {cue}")
        for subject, values in support.items():
            if not values:
                audit.check(subject in text, "unsupported_subject_named", f"{rel}: {subject}")
        if len(config["subjects"]) == 2:
            for subject in config["subjects"]:
                raw = set(grades_for(row, subject))
                audit.check(subject in text, "combined_subject_split", f"{rel}: {subject}")
                if raw:
                    audit.check(raw <= set(re.findall(r"(?:초[1-6]|[중고][1-3])", text)),
                                "combined_visible_grades", f"{rel}: {subject}")
        audit.check(not services, "unsupported_service_claim", rel)
        physical = physical_org_node(nodes, row)
        for top in nodes:
            if top is physical:
                continue
            forbidden = [
                key for key in ("audience", "educationalLevel", "offers", "makesOffer")
                if any(value.get(key) is not None for value in walk_dicts(top))
            ]
            audit.check(not forbidden, "unsupported_schema_claim",
                        f"{rel}: {forbidden}")
            audit.check(not any("Offer" in node_types(value) for value in walk_dicts(top)),
                        "unsupported_offer_type", rel)
        profile_terms = set(config["subjects"])
        for offer in offers:
            if offer in as_list(physical_org_node(nodes, row).get("makesOffer")):
                continue
            audit.check(not any(term in claim_text(offer) for term in profile_terms),
                        "unsupported_subject_offer", f"{rel}: {claim_text(offer)[:100]}")

        if baseline_item:
            proc = subprocess.run(
                ["git", "cat-file", "blob", clean(baseline_item.get("git_oid"))],
                cwd=ROOT, capture_output=True,
            )
            audit.check(proc.returncode == 0, "baseline_blob", rel)
            if proc.returncode == 0:
                original = parse_dom(proc.stdout.decode("utf-8"))
                original_title = first(original.find_all("title"))
                current_title = first(dom.find_all("title"))
                audit.check(bool(original_title and current_title) and
                            original_title.text() == current_title.text(),
                            "unsupported_title_immutable", rel)
                original_h1 = [node.text() for node in original.find_all("h1")]
                current_h1 = [node.text() for node in dom.find_all("h1")]
                audit.check(original_h1 == current_h1, "unsupported_h1_immutable", rel)
                audit.check(link_values(original, "canonical") == [canonical],
                            "unsupported_canonical_immutable", rel)

def validate_source_facts(audit: Audit, rel: str, dom: Node, nodes: list[dict],
                          row: dict, config: dict) -> None:
    text = rendered_text(dom)
    main_text, main_count = detail_main_text(dom)
    audit.check(main_count == 1, "detail_main_scope", f"{rel}: main={main_count}")
    awkward_region_routine = sorted(set(OFFICIAL_REGION_DISTRICT_RE.findall(main_text)))
    audit.check(not awkward_region_routine, "awkward_region_routine",
                f"{rel}: {awkward_region_routine}")
    main_headings = [
        node.text() for main in dom.find_all("main")
        for tag in ("h2", "h3") for node in main.find_all(tag)
    ]
    duplicate_headings = sorted({
        heading for heading, count in Counter(main_headings).items() if count > 1
    })
    audit.check(not duplicate_headings, "main_heading_duplicate",
                f"{rel}: {duplicate_headings}")
    title_node = first(dom.find_all("title"))
    descriptions = meta_values(dom, "name", "description")
    audit.check(bool(title_node) and meta_values(dom, "property", "og:title") == [title_node.text()],
                "detail_og_title_parity", rel)
    audit.check(len(descriptions) == 1 and
                meta_values(dom, "property", "og:description") == descriptions,
                "detail_og_description_parity", rel)
    main_paragraphs = [
        node.text() for main in dom.find_all("main") for node in main.find_all("p")
    ]
    teacher_fragments = [
        text for text in main_paragraphs if "의 교사가 보여 준 예시는" in text
    ]
    audit.check(not teacher_fragments, "teacher_example_fragment",
                f"{rel}: {teacher_fragments[:3]}")
    orphan_paragraphs = [
        text for text in main_paragraphs
        if text.startswith("특히 ") or text.startswith("답은 ")
    ]
    audit.check(not orphan_paragraphs, "orphan_paragraph_start",
                f"{rel}: {orphan_paragraphs[:3]}")
    quote_imbalances = {
        f"{left}{right}": (main_text.count(left), main_text.count(right))
        for left, right in (("“", "”"), ("‘", "’"))
        if main_text.count(left) != main_text.count(right)
    }
    audit.check(not quote_imbalances, "visible_quote_balance",
                f"{rel}: {quote_imbalances}")
    quote_tail_failures = standalone_quote_tails(main_text)
    audit.check(not quote_tail_failures, "standalone_quote_tail",
                f"{rel}: {quote_tail_failures}")
    broken_quote_tails = [tail for tail in BROKEN_QUOTE_TAILS if tail in main_text]
    audit.check(not broken_quote_tails, "known_quote_tail",
                f"{rel}: {broken_quote_tails}")
    manuscript_terms = sorted({match.group(0) for match in MANUSCRIPT_META_REGEX.finditer(main_text)})
    audit.check(not manuscript_terms, "manuscript_meta_copy",
                f"{rel}: {manuscript_terms}")
    irrelevant_seeds = sorted({match.group(0) for match in IRRELEVANT_SEED_REGEX.finditer(main_text)})
    audit.check(not irrelevant_seeds, "irrelevant_seed_copy",
                f"{rel}: {irrelevant_seeds}")
    schema_values = [clean(value) for value in walk_string_values(nodes)]
    schema_manuscript = sorted({
        match.group(0) for value in schema_values
        for match in MANUSCRIPT_META_REGEX.finditer(value)
    })
    audit.check(not schema_manuscript, "schema_manuscript_meta_copy",
                f"{rel}: {schema_manuscript}")
    schema_seeds = sorted({
        match.group(0) for value in schema_values
        for match in IRRELEVANT_SEED_REGEX.finditer(value)
    })
    audit.check(not schema_seeds, "schema_irrelevant_seed_copy",
                f"{rel}: {schema_seeds}")
    prose_claim_text, _ = detail_main_text(
        dom, exclude_source_markers=True, exclude_classes=("academy-aside-card",),
    )
    grade_claim_errors = unsupported_grade_claims(prose_claim_text, profile_support(row, config))
    audit.check(not grade_claim_errors, "visible_grade_claim_source",
                f"{rel}: {grade_claim_errors}")
    locality = row.get("근처 수업가능 동네", "")
    audit.check(locality in text or normalize_locality(locality) in normalize_locality(text),
                "visible_locality", rel)
    audit.check(row.get("센터명", "") in text, "visible_center", rel)
    audit.check(row.get("센터 주소", "") in text, "visible_address", rel)
    region = row.get("지역", "")
    city = row.get("시or구", "")
    is_sejong = row.get("센터 주소", "").startswith("세종특별자치시")
    official_region, _ = official_address_parts(
        row.get("센터 주소", ""), [row.get("근처 수업가능 동네", "")]
    )
    if is_sejong:
        audit.check("세종특별자치시" in text, "sejong_visible_region", rel)
        audit.check("충청 새롬중앙로" not in text and "충청 · 새롬중앙로" not in text,
                    "sejong_wrong_region", rel)
    else:
        expected_region = official_region if region in {"충청", "경상", "전라"} else REGION_NAMES.get(region, "")
        audit.check(expected_region in text and city in text, "visible_region_city",
                    f"{rel}: {expected_region}/{city}")

    expected_schools = set(schools_for(row, config))
    repeated_schools = duplicate_school_names(text, expected_schools)
    audit.check(not repeated_schools, "duplicate_school_prose",
                f"{rel}: {sorted(repeated_schools)}")
    all_source_schools = set(schools_for(row))
    leaked_schools = {
        school for school in all_source_schools - expected_schools
        if re.search(rf"(?<![가-힣A-Za-z0-9]){re.escape(school)}(?![가-힣A-Za-z0-9])", text)
    }
    audit.check(not leaked_schools, "school_level_scope",
                f"{rel}: {sorted(leaked_schools)}")
    school_particle_failures = wrong_particles(text, tuple(expected_schools))
    audit.check(not school_particle_failures, "school_particle_error",
                f"{rel}: {school_particle_failures}")
    visible_school_nodes = dom.find_all(cls="academy-school-tags")
    visible_schools: set[str] = set()
    for parent in visible_school_nodes:
        visible_schools.update(node.text() for node in parent.find_all("span") if node.text())
    if expected_schools:
        audit.check(visible_schools == expected_schools, "visible_school_source",
                    f"{rel}: actual={visible_schools} expected={expected_schools}")
    else:
        audit.check(not visible_schools, "visible_school_unsupported", rel)
    schema_schools = {clean(node.get("name")) for node in walk_dicts(nodes)
                      if "School" in node_types(node) and clean(node.get("name"))}
    audit.check(schema_schools == expected_schools, "schema_school_source",
                f"{rel}: actual={schema_schools} expected={expected_schools}")

    grade_rows: list[tuple[Node, Node]] = []
    for term in dom.find_all("dt"):
        if "가능 학년" in term.text() and term.parent:
            grade_rows.append((term, term.parent))
    subject_row_ids: dict[str, int] = {}
    for subject, expected_values in profile_support(row, config).items():
        matches = [(term, info) for term, info in grade_rows if subject in term.text()]
        if len(config["subjects"]) == 1 and not matches and len(grade_rows) == 1:
            matches = grade_rows
        audit.check(len(matches) == 1, "visible_grade_row", f"{rel}: {subject} rows={len(matches)}")
        if len(matches) != 1:
            continue
        _, info = matches[0]
        subject_row_ids[subject] = id(info)
        actual_values = {
            node.text() for tags in info.find_all(cls="academy-grade-tags")
            for node in tags.find_all("span") if node.text()
        }
        expected_set = set(expected_values)
        audit.check(actual_values == expected_set, "visible_grade_source",
                    f"{rel}: {subject} actual={actual_values} expected={expected_set}")
        empty = info.find_all(cls="academy-grade-empty")
        if expected_set:
            audit.check(not empty, "supported_grade_empty", f"{rel}: {subject}")
        else:
            audit.check(bool(empty) and all(
                item.attrs.get("data-source-status") == "unconfirmed-grade" for item in empty
            ), "unsupported_grade_empty", f"{rel}: {subject}")
    if len(config["subjects"]) == 2:
        audit.check(len(set(subject_row_ids.values())) == 2, "combined_grade_rows_separate", rel)

    fee = row.get("센터 교습비", "")
    fee_links = [node.attrs.get("href", "") for node in dom.find_all("a", cls="academy-tuition-link")]
    if fee:
        audit.check(fee_links == [fee], "visible_fee_source", f"{rel}: {fee_links}")
        fee_context = " ".join(node.parent.text() if node.parent else node.text()
                               for node in dom.find_all("a", cls="academy-tuition-link"))
        audit.check("센터" in fee_context and "공통" in fee_context,
                    "visible_fee_context", rel)
    else:
        audit.check(not fee_links, "visible_fee_unsupported", rel)
        audit.check("교습비" in text and ("미기재" in text or "상담" in text),
                    "visible_fee_missing_note", rel)

    for node in nodes:
        if not ({"EducationalOrganization", "LocalBusiness"} & node_types(node)):
            continue
        address = node.get("address", {}) if isinstance(node.get("address"), dict) else {}
        if address:
            audit.check(clean(address.get("streetAddress")) == row.get("센터 주소"),
                        "schema_address_source", rel)
            if is_sejong:
                audit.check(clean(address.get("addressRegion")) == "세종특별자치시",
                            "sejong_schema_region", rel)


def validate_article_parity(audit: Audit, rel: str, dom: Node, nodes: list[dict]) -> None:
    articles = [node for node in nodes if "Article" in node_types(node)]
    visible_articles = dom.find_all(cls="academy-main-article")
    audit.check(len(articles) == 1 and len(visible_articles) == 1,
                "article_visible_scope",
                f"{rel}: schema={len(articles)} visible={len(visible_articles)}")
    if len(articles) != 1 or len(visible_articles) != 1:
        return
    article = articles[0]
    visible_article = visible_articles[0]
    h1s = [node.text() for node in dom.find_all("h1")]
    descriptions = meta_values(dom, "name", "description")
    audit.check(len(h1s) == 1 and clean(article.get("headline")) == h1s[0],
                "article_headline_parity", rel)
    audit.check(len(descriptions) == 1 and clean(article.get("description")) == descriptions[0],
                "article_description_parity", rel)
    audit.check(article.get("abstract") is None, "article_abstract_hidden", rel)

    visible_topics = [node.text() for node in visible_article.find_all("h2")]
    parts = as_list(article.get("hasPart"))
    part_shape = all(
        isinstance(item, dict)
        and node_types(item) == {"WebPageElement"}
        and set(item) == {"@type", "name"}
        and bool(clean(item.get("name")))
        for item in parts
    )
    part_names = [clean(item.get("name")) for item in parts if isinstance(item, dict)]
    audit.check(part_shape, "article_haspart_shape", rel)
    audit.check(part_names == visible_topics, "article_haspart_parity",
                f"{rel}: schema={part_names} visible={visible_topics}")

    normalized_main = normalized_topic(detail_main_text(dom)[0])
    topic_shape_failures: list[str] = []
    invisible_topics: list[str] = []
    for field_name in ("about", "mentions"):
        for item in as_list(article.get(field_name)):
            if (not isinstance(item, dict) or set(item) - {"@type", "name"}
                    or not clean(item.get("name"))):
                topic_shape_failures.append(f"{field_name}:{clean(item)}")
                continue
            name = clean(item.get("name"))
            if normalized_topic(name) not in normalized_main:
                invisible_topics.append(f"{field_name}:{name}")
    audit.check(not topic_shape_failures, "article_topic_shape",
                f"{rel}: {topic_shape_failures[:5]}")
    audit.check(not invisible_topics, "article_topic_visible",
                f"{rel}: {invisible_topics[:10]}")


def validate_faq(audit: Audit, rel: str, dom: Node, nodes: list[dict], unsupported: bool) -> None:
    visible = visible_faq(dom)
    schema = schema_faq(nodes)
    audit.check(len(visible) >= 3, "faq_visible_count", f"{rel}: {len(visible)}")
    audit.check(visible == schema, "faq_parity", rel)
    if unsupported:
        joined = clean(" ".join(answer for _, answer in visible))
        audit.check(all(cue in joined for cue in UNCONFIRMED_CUES), "faq_unsupported_cue", rel)


def validate_common_page(audit: Audit, path: Path, expected_urls: set[str],
                         title_seen: dict, desc_seen: dict, h1_seen: dict) -> dict:
    rel = path.relative_to(ROOT).as_posix()
    source = path.read_text(encoding="utf-8")
    dom = parse_dom(source)
    canonical_values = link_values(dom, "canonical")
    canonical = canonical_values[0] if len(canonical_values) == 1 else ""
    title_node = first(dom.find_all("title"))
    title = title_node.text() if title_node else ""
    descriptions = meta_values(dom, "name", "description")
    description = descriptions[0] if len(descriptions) == 1 else ""
    h1s = [node.text() for node in dom.find_all("h1")]
    robots = meta_values(dom, "name", "robots")
    audit.check(bool(canonical) and canonical == page_url(path), "canonical_exact", rel)
    audit.check(canonical in expected_urls, "canonical_expected", rel)
    audit.check(len(dom.find_all("title")) == 1 and 10 <= len(title) <= 60, "title", rel)
    audit.check(len(descriptions) == 1 and 50 <= len(description) <= 160, "description", rel)
    audit.check(len(h1s) == 1 and bool(h1s[0]), "h1", rel)
    audit.check(len(robots) == 1 and "index" in robots[0].lower() and "noindex" not in robots[0].lower(),
                "robots_meta", rel)
    for value, seen, code in ((title, title_seen, "title_duplicate"),
                              (description, desc_seen, "description_duplicate"),
                              (h1s[0] if h1s else "", h1_seen, "h1_duplicate")):
        if value:
            audit.check(value not in seen, code, f"{rel} == {seen.get(value)}")
            seen[value] = rel

    og_url = meta_values(dom, "property", "og:url")
    og_image = meta_values(dom, "property", "og:image")
    audit.check(og_url == [canonical], "og_url", rel)
    audit.check(len(og_image) == 1 and og_image[0].startswith(f"{BASE_URL}/"), "og_image_absolute", rel)
    for prop in ("og:title", "og:description", "og:type", "og:image:width", "og:image:height", "og:image:type"):
        audit.check(len(meta_values(dom, "property", prop)) == 1, "og_meta", f"{rel}: {prop}")
    for name in ("twitter:card", "twitter:title", "twitter:description", "twitter:image"):
        audit.check(len(meta_values(dom, "name", name)) == 1, "twitter_meta", f"{rel}: {name}")
    rss = [node for node in dom.find_all("link") if "alternate" in node.attrs.get("rel", "").split()
           and node.attrs.get("type") == "application/rss+xml"]
    audit.check(len(rss) == 1 and rss[0].attrs.get("href") == f"{BASE_URL}/rss.xml", "rss_discovery", rel)

    for phrase in KNOWN_BAD_TEXT:
        audit.check(phrase not in source, "known_copy_error", f"{rel}: {phrase}")
    plain = dom.text()
    for pattern in KNOWN_BAD_REGEX:
        audit.check(not pattern.search(plain), "known_copy_pattern", f"{rel}: {pattern.pattern}")
    particle_failures = wrong_particles(plain, PARTICLE_TERMS)
    audit.check(not particle_failures, "known_particle_error",
                f"{rel}: {particle_failures}")

    for link in dom.find_all("a"):
        href = link.attrs.get("href", "")
        if not href or href.startswith(("#", "tel:", "mailto:", "javascript:", "data:")):
            continue
        audit.check(not href.lower().startswith("http://"), "mixed_http_link", f"{rel}: {href}")
        target = urljoin(canonical or page_url(path), href)
        if urlsplit(target).hostname in {"xn--9l4b2jy4h4wan0chw4b.com", "소수정예학원.com", None}:
            audit.check("index.html" not in urlsplit(href).path, "internal_redirect_link", f"{rel}: {href}")
            normalized = resolve_internal(canonical or page_url(path), href)
            if "." not in unquote(urlsplit(normalized).path).rsplit("/", 1)[-1]:
                audit.check(normalized in expected_urls, "broken_internal_link", f"{rel}: {href}")
            else:
                local_link = internal_file(href, canonical or page_url(path))
                audit.check(bool(local_link and local_link.is_file()),
                            "broken_internal_file_link", f"{rel}: {href}")

    for node, attribute in [
        *((item, "href") for item in dom.find_all("link") if item.attrs.get("href")),
        *((item, "src") for item in dom.find_all("script") if item.attrs.get("src")),
    ]:
        value = node.attrs.get(attribute, "")
        local_asset = internal_file(value, canonical or page_url(path))
        if local_asset is not None:
            audit.check(local_asset.is_file(), "broken_head_asset", f"{rel}: {value}")

    for image in dom.find_all("img"):
        src = image.attrs.get("src", "")
        audit.check(bool(src), "image_src", rel)
        audit.check(bool(image.attrs.get("alt")), "image_alt", f"{rel}: {src}")
        audit.check(bool(image.attrs.get("width")) and bool(image.attrs.get("height")),
                    "image_dimensions", f"{rel}: {src}")
        audit.check("display:none" not in image.attrs.get("style", "").replace(" ", "")
                    and not image.has_class("hidden-representative"), "hidden_image", f"{rel}: {src}")
        target = urljoin(canonical or page_url(path), src)
        if urlsplit(target).hostname in {"xn--9l4b2jy4h4wan0chw4b.com", "소수정예학원.com", None}:
            local = ROOT / unquote(urlsplit(target).path).lstrip("/")
            audit.check(local.is_file(), "image_missing", f"{rel}: {src}")

    roots, json_errors = parse_jsonld(dom)
    audit.check(not json_errors, "jsonld_parse", f"{rel}: {json_errors[:1]}")
    nodes = top_nodes(roots)
    schema_assets: set[str] = set()
    for schema_node in walk_dicts(nodes):
        for key in ("image", "contentUrl", "thumbnailUrl"):
            for value in as_list(schema_node.get(key)):
                if isinstance(value, str) and value:
                    schema_assets.add(value)
    schema_assets.update(og_image)
    for value in schema_assets:
        local_asset = internal_file(value, canonical or page_url(path))
        if local_asset is not None:
            audit.check(local_asset.is_file(), "broken_schema_image", f"{rel}: {value}")
    faq_nodes = [node for node in nodes if "FAQPage" in node_types(node)]
    audit.check(len(faq_nodes) <= 1, "faq_schema_count", rel)
    return {"source": source, "dom": dom, "canonical": canonical, "nodes": nodes, "rel": rel}


def validate_hubs(audit: Audit, records: dict[str, dict], by_locality: dict[str, dict]) -> None:
    script = (ROOT / "assets" / "script.js").read_text(encoding="utf-8")
    for token in ("[data-academy-directory]", "[data-academy-search]", "applySearch", "resetDirectory"):
        audit.check(token in script, "hub_script", token)
    for slug in PROFILES:
        path = ACADEMY_ROOT / slug / "index.html"
        record = records.get(path.relative_to(ROOT).as_posix())
        audit.check(bool(record), "hub_missing", slug)
        if not record:
            continue
        dom = record["dom"]
        directories = dom.find_all(attr="data-academy-directory")
        audit.check(len(directories) == 1, "hub_directory", f"{slug}: {len(directories)}")
        if directories:
            directory = directories[0]
            section = directory.closest("section")
            audit.check(bool(directory.find_all(attr="data-academy-search")), "hub_search_input", slug)
            audit.check(bool(directory.find_all(attr="data-academy-search-clear")), "hub_search_clear", slug)
            audit.check(bool(directory.find_all(attr="data-academy-search-status")), "hub_search_status", slug)
            audit.check(bool(section) and len(section.find_all(attr="data-academy-region")) == 13,
                        "hub_search_scope", slug)
        expected = {semantic_url(f"{BASE_URL}/전국학원/{slug}/{quote(name, safe='')}/")
                    for name in by_locality}
        actual = set()
        for link in dom.find_all("a"):
            href = link.attrs.get("href", "")
            target = resolve_internal(record["canonical"], href) if href else ""
            if target.startswith(semantic_url(f"{BASE_URL}/전국학원/{slug}/")) and target != semantic_url(record["canonical"]):
                actual.add(target)
        audit.check(actual == expected, "hub_detail_links",
                    f"{slug}: actual={len(actual)} expected={len(expected)}")


def audit_site(baseline: dict) -> tuple[Audit, dict]:
    audit = Audit()
    rows, by_locality, source = load_sources(audit, baseline)
    pages = all_index_pages()
    baseline_gate(audit, baseline, pages)
    expected_urls = {item["url"] for item in baseline.get("html", [])}
    baseline_html = {item["path"]: item for item in baseline.get("html", [])}
    title_seen: dict[str, str] = {}
    desc_seen: dict[str, str] = {}
    h1_seen: dict[str, str] = {}
    records: dict[str, dict] = {}
    seen_org_ids: dict[tuple, str] = {}
    html_manifest: list[str] = []

    for path in pages:
        record = validate_common_page(audit, path, expected_urls, title_seen, desc_seen, h1_seen)
        relative_name = path.relative_to(ROOT).as_posix()
        html_manifest.append(f"{relative_name}\0{sha256_file(path)}")
        validate_root_org_scope(
            audit, record["rel"], record["nodes"], record["canonical"],
            relative_name == "index.html",
        )
        if any(relative_name == f"전국학원/{slug}/index.html" for slug in PROFILES):
            records[record["rel"]] = record
        try:
            relative = path.relative_to(ACADEMY_ROOT)
        except ValueError:
            continue
        if len(relative.parts) != 3 or relative.parts[-1] != "index.html":
            continue
        slug, locality_dir, _ = relative.parts
        if slug not in PROFILES:
            continue
        row = by_locality.get(normalize_locality(locality_dir))
        audit.check(bool(row), "detail_source_row", record["rel"])
        if not row:
            continue
        config = PROFILES[slug]
        nodes = record["nodes"]
        supported = all(profile_support(row, config).values())
        validate_detail_graph_shape(
            audit, record["rel"], nodes, record["canonical"], supported,
        )
        org = physical_org_node(nodes, row)
        group = source["groups"][source_group_key(row)]
        physical_id = validate_physical_org(
            audit, record["rel"], row, org, record["canonical"], group, seen_org_ids,
        )
        validate_truthfulness(
            audit, record["rel"], record["source"], record["dom"], nodes,
            row, config, physical_id, record["canonical"], baseline_html.get(record["rel"]),
        )
        validate_source_facts(audit, record["rel"], record["dom"], nodes, row, config)
        unsupported = not supported
        validate_article_parity(audit, record["rel"], record["dom"], nodes)
        validate_faq(audit, record["rel"], record["dom"], nodes, unsupported)

    audit.check(len(detail_pages()) == 3339, "detail_count", f"details={len(detail_pages())}")
    audit.check(audit.metrics["unsupported_seen"] == 106, "unsupported_actual",
                f"seen={audit.metrics['unsupported_seen']}")
    audit.check(len(seen_org_ids) == 188, "physical_org_groups_seen", str(len(seen_org_ids)))
    audit.check(len(set(seen_org_ids.values())) == 188, "physical_org_ids_unique",
                f"ids={len(set(seen_org_ids.values()))}")
    validate_hubs(audit, records, by_locality)

    robots = (ROOT / "robots.txt").read_text(encoding="utf-8")
    audit.check("User-agent: *" in robots and "Allow: /" in robots, "robots_file", "allow")
    audit.check(f"Sitemap: {BASE_URL}/sitemap.xml" in robots, "robots_file", "sitemap")
    try:
        rss = ET.parse(ROOT / "rss.xml").getroot()
        items = rss.findall("./channel/item")
        rss_links = [clean(item.findtext("link")) for item in items]
        audit.check(len(items) == 25 and len(set(rss_links)) == 25, "rss_items", str(len(items)))
        audit.check(set(rss_links) <= expected_urls, "rss_links", str(set(rss_links)-expected_urls))
    except Exception as exc:
        audit.check(False, "rss_parse", str(exc))

    html_manifest_sha256 = set_sha(set(html_manifest))
    release_manifest = list(html_manifest)
    for relative_name in (
        "sitemap.xml", "robots.txt", "rss.xml", "vercel.json",
        "assets/styles.css", "assets/script.js",
    ):
        candidate = ROOT / relative_name
        release_manifest.append(f"{relative_name}\0{sha256_file(candidate)}")
    release_manifest_sha256 = set_sha(set(release_manifest))
    report = {
        "ok": not audit.errors,
        "errors": len(audit.errors),
        "error_counts": dict(audit.counts),
        "samples": dict(audit.samples),
        "informational": len(audit.warnings),
        "informational_counts": dict(audit.warning_counts),
        "informational_samples": dict(audit.warning_samples),
        "summary": {
            "pages": len(pages), "details": len(detail_pages()), "profiles": len(PROFILES),
            "source_rows": len(rows), "source_orgs": len(source["groups"]),
            "unsupported_pages": audit.metrics["unsupported_seen"],
            "url_set_sha256": set_sha(set(sitemap_urls())),
            "html_manifest_sha256": html_manifest_sha256,
            "release_manifest_sha256": release_manifest_sha256,
        },
    }
    return audit, report


def run_browser(base: str) -> tuple[bool, dict]:
    """Run an optional Chromium preview/production smoke test without writing files."""
    base = base.rstrip("/")
    rows = read_csv(CENTER_CSV)
    cases: list[dict] = []
    for slug, config in PROFILES.items():
        hub_path = "/" + "/".join(quote(value, safe="") for value in ("전국학원", slug)) + "/"
        cases.append({"kind": "hub", "slug": slug, "path": hub_path, "unsupported": False})
        for unsupported in (False, True):
            row = next(
                item for item in rows
                if (not all(profile_support(item, config).values())) == unsupported
            )
            detail_path = "/" + "/".join(
                quote(value, safe="") for value in (
                    "전국학원", slug, normalize_locality(row.get("근처 수업가능 동네")),
                )
            ) + "/"
            cases.append({
                "kind": "detail", "slug": slug, "path": detail_path,
                "unsupported": unsupported,
            })
    node_path = os.environ.get("NODE_PATH", "")
    if not node_path:
        candidates = list(ROOT.parent.glob("*/node_modules/playwright"))
        if candidates:
            node_path = str(candidates[0].parent)
    script = r'''
const { chromium } = require('playwright');
const base = process.argv[2].replace(/\/$/, '');
const production = process.argv[3].replace(/\/$/, '');
const cases = JSON.parse(process.argv[4]);
(async()=>{
 const browser=await chromium.launch({headless:true}); const rows=[];
 for(const width of [320,390,1440]){
  const context=await browser.newContext({viewport:{width,height:844},locale:'ko-KR'});
  for(const testCase of cases){
   const url=base+testCase.path;
   const p=await context.newPage(); const errors=[]; const failed=[];
   p.on('pageerror',e=>errors.push(e.message)); p.on('console',m=>m.type()==='error'&&errors.push(m.text()));
   p.on('requestfailed',r=>failed.push(r.url())); p.on('response',r=>r.status()>=400&&failed.push(`${r.status()} ${r.url()}`)); const response=await p.goto(url,{waitUntil:'load',timeout:30000});
   const input=p.locator('[data-academy-search]'); let search={all:0,shown:0,reset:0,status:''};
   if(testCase.kind==='hub' && await input.count()){
    const q=await p.locator('.academy-link-grid a').first().innerText(); await input.fill(q); await p.waitForTimeout(80);
    search=await p.evaluate(()=>({all:document.querySelectorAll('.academy-link-grid a').length,shown:[...document.querySelectorAll('.academy-link-grid a')].filter(x=>!x.hidden).length,reset:0,status:document.querySelector('[data-academy-search-status]')?.textContent||''}));
    const clear=p.locator('[data-academy-search-clear]'); if(await clear.count()) await clear.click(); else await input.fill('');
    await p.waitForTimeout(80); search.reset=await p.evaluate(()=>[...document.querySelectorAll('.academy-link-grid a')].filter(x=>!x.hidden).length);
   }
   await p.evaluate(async()=>{
    const images=[...document.images].filter(x=>{const s=getComputedStyle(x);return s.display!=='none'&&s.visibility!=='hidden'});
    images.forEach(x=>x.loading='eager');
    await Promise.race([Promise.all(images.map(x=>x.complete?Promise.resolve():new Promise(resolve=>{x.addEventListener('load',resolve,{once:true});x.addEventListener('error',resolve,{once:true})}))),new Promise(resolve=>setTimeout(resolve,3000))]);
   });
   const d=await p.evaluate(()=>({h1:document.querySelectorAll('h1').length,overflow:document.documentElement.scrollWidth>document.documentElement.clientWidth+1,badImages:[...document.images].filter(x=>{const s=getComputedStyle(x);return s.display!=='none'&&s.visibility!=='hidden'&&(!x.complete||!x.naturalWidth)}).length,canonical:document.querySelector('link[rel="canonical"]')?.href||'',markers:document.querySelectorAll('[data-source-status="unconfirmed-grade"]').length}));
   rows.push({width,...testCase,status:response&&response.status(),errors,failed,search,d}); await p.close();
  } await context.close();
 }
 await browser.close();
 const bad=rows.filter(x=>x.status!==200||x.errors.length||x.failed.length||x.d.h1!==1||x.d.overflow||x.d.badImages||x.d.canonical!==production+x.path||(x.kind==='detail'&&((x.unsupported&&x.d.markers<3)||(!x.unsupported&&x.d.markers))));
 const searchBad=rows.filter(x=>x.kind==='hub'&&(x.search.all!==371||x.search.shown!==1||x.search.reset!==371||!x.search.status.includes('검색 결과')));
 process.stdout.write(JSON.stringify({tests:rows.length,failures:bad.length,informationalFailures:searchBad.length,bad:bad.slice(0,20),searchBad:searchBad.slice(0,20)}));
})().catch(e=>{process.stderr.write(String(e));process.exit(2)});
'''
    env = os.environ.copy()
    if node_path:
        env["NODE_PATH"] = node_path
    try:
        proc = subprocess.run(
            ["node", "-", base, BASE_URL, json.dumps(cases, ensure_ascii=False)],
            input=script, text=True, capture_output=True, encoding="utf-8", env=env,
            timeout=360,
        )
    except subprocess.TimeoutExpired:
        return False, {"error": "browser smoke timed out", "returncode": 124}
    if proc.returncode:
        return False, {"error": proc.stderr.strip(), "returncode": proc.returncode}
    try:
        result = json.loads(proc.stdout)
    except Exception:
        return False, {"error": proc.stdout[-1000:]}
    return result.get("failures") == 0, result


def outside_repo(path: Path) -> bool:
    try:
        path.resolve().relative_to(ROOT.resolve())
        return False
    except ValueError:
        return True


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--browser-base", help="Optional Preview/Production base URL")
    args = parser.parse_args()

    baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
    audit, report = audit_site(baseline)
    if args.browser_base:
        ok, browser = run_browser(args.browser_base)
        report["browser"] = browser
        if browser.get("informationalFailures"):
            audit.check(False, "browser_hub_search",
                        f"failures={browser['informationalFailures']}")
        if not ok:
            audit.check(False, "browser", json.dumps(browser, ensure_ascii=False)[:1000])
        report["ok"] = not audit.errors
        report["errors"] = len(audit.errors)
        report["error_counts"] = dict(audit.counts)
        report["samples"] = dict(audit.samples)
        report["informational"] = len(audit.warnings)
        report["informational_counts"] = dict(audit.warning_counts)
        report["informational_samples"] = dict(audit.warning_samples)

    if args.report:
        if not outside_repo(args.report):
            raise SystemExit("--report must be outside the repository")
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps({
        "ok": report["ok"], "errors": report["errors"],
        "informational": report["informational"],
        "summary": report["summary"], "error_counts": report["error_counts"],
        "samples": report["samples"],
        "informational_counts": report["informational_counts"],
        "informational_samples": report["informational_samples"],
        "browser": report.get("browser"),
    }, ensure_ascii=False, indent=2))
    raise SystemExit(0 if report["ok"] else 1)


if __name__ == "__main__":
    main()
