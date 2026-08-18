#!/usr/bin/env python3
"""Deterministic phase-3 reader-copy renderer for 소수정예학원.com.

The command is a dry-run unless ``--apply`` is explicitly supplied.  It
rebuilds only the 3,339 national-academy detail manuscripts from authoritative
CSV fields and category facts, synchronizes the visible FAQ and Article
headings with JSON-LD, and updates only those URLs' sitemap ``lastmod`` values.

Public routes, title/H1/canonical values, links, source fact literals, phase-1
unsupported markers, sitemap URL order, and every non-target file are guarded
by in-memory snapshots and hashes before any write is possible.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import os
import re
import sys
import tempfile
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

try:  # direct CLI execution
    import smallclass_phase1_content as phase1
except ImportError:  # package import in integration tests
    from . import smallclass_phase1_content as phase1


FRESHNESS_DATE = "2026-08-18"
DETAIL_COUNT = 3339
EXPECTED_LOCATION_GUIDES = 253
EXPECTED_BLANK_GUIDES = 118
SOURCE_MARKER = 'data-source-status="unconfirmed-grade"'
MISSING_LOCATION_GUIDE_MARKER = 'data-source-status="missing-location-guide"'
LOCATION_GUIDE_ATTR = 'data-source-field="location-guide"'
REGISTRATION_ATTR = 'data-source-field="registration-number"'
REGISTERED_NAME_ATTR = 'data-source-field="registered-name"'

BASELINE = {
    "paragraphs": 59765,
    "sentences": 140943,
    "within_paragraph_pages": 7,
    "within_paragraph_excess": 7,
    "within_sentence_pages": 638,
    "within_sentence_excess": 2342,
    "normalized_sentence_excess": 100687,
    "normalized_paragraph_excess": 25641,
    "faq_normalized_excess": 12524,
    "locality_median": 40,
    "locality_p95": 49,
    "locality_max": 58,
    "query_median": 10,
    "query_p95": 22,
    "query_max": 27,
}

LEGACY_LABELS = (
    "입시합격전략",
    "입시성공전략",
    "입시지원전략",
    "학습우선순위",
    "학원리뷰",
)

ARTICLE_RE = re.compile(
    r'(<article\b[^>]*class=["\'][^"\']*\bacademy-main-article\b[^"\']*["\'][^>]*>)'
    r'.*?(</article>)',
    re.I | re.S,
)
FAQ_SECTION_RE = re.compile(
    r'<section class=["\']section["\']>\s*'
    r'<div class=["\']container academy-faq-card["\']>.*?'
    r'</div>\s*</div>\s*</section>'
    r'(?=\s*<section class=["\']section["\']>\s*'
    r'<div class=["\']container academy-review-card)',
    re.I | re.S,
)
PREP_SECTION_RE = re.compile(
    r'<section class=["\']section["\']>\s*'
    r'<div class=["\']container academy-review-card[^"\']*["\'][^>]*>.*?'
    r'</div>\s*</div>\s*</section>'
    r'(?=\s*<section class=["\']section academy-navigation-section["\'])',
    re.I | re.S,
)
HERO_RE = re.compile(
    r'<section\b[^>]*class=["\'][^"\']*\bacademy-hero\b[^"\']*["\'][^>]*>'
    r'.*?</section>',
    re.I | re.S,
)
HERO_LEAD_RE = re.compile(
    r'(<h1\b[^>]*>.*?</h1>)\s*<p\b[^>]*>.*?</p>',
    re.I | re.S,
)
HERO_PANEL_RE = re.compile(
    r'<aside\b[^>]*class=["\'][^"\']*\bacademy-hero-panel\b[^"\']*["\'][^>]*>'
    r'.*?</aside>',
    re.I | re.S,
)
TOP_ANSWER_RE = re.compile(
    r'(<div\b[^>]*class=["\'][^"\']*\bacademy-top-answer\b[^"\']*["\'][^>]*>'
    r'.*?<strong\b[^>]*>.*?</strong>)\s*<p\b[^>]*>.*?</p>(\s*</div>)',
    re.I | re.S,
)
MAP_CAPTION_RE = re.compile(
    r'(<figcaption\b[^>]*class=["\'][^"\']*\bacademy-map-caption\b[^"\']*["\'][^>]*>)'
    r'.*?(</figcaption>)',
    re.I | re.S,
)
NAV_SECTION_RE = re.compile(
    r'<section\b[^>]*class=["\'][^"\']*\bacademy-navigation-section\b[^"\']*["\'][^>]*>'
    r'.*?</section>',
    re.I | re.S,
)
NAV_HEAD_RE = re.compile(
    r'(<div\b[^>]*class=["\'][^"\']*\bacademy-navigation-head\b[^"\']*["\'][^>]*>'
    r'.*?<h2\b[^>]*>).*?(</h2>.*?</div>)\s*<p\b[^>]*>.*?</p>(\s*</div>)',
    re.I | re.S,
)
SUBJECT_STRONG_RE = re.compile(r'(<strong\b[^>]*>)(.*?)(</strong>)', re.I | re.S)
META_DESCRIPTION_RE = re.compile(
    r'(<meta\b(?=[^>]*\bname=["\']description["\'])[^>]*\bcontent=)(["\'])(.*?)\2',
    re.I | re.S,
)
OG_DESCRIPTION_RE = re.compile(
    r'(<meta\b(?=[^>]*\bproperty=["\']og:description["\'])[^>]*\bcontent=)(["\'])(.*?)\2',
    re.I | re.S,
)
JSONLD_RE = phase1.SCRIPT_JSON_RE
BLOCK_RE = re.compile(
    r'<(?P<tag>p|blockquote)\b(?P<attrs>[^>]*)>(?P<body>.*?)</(?P=tag)>',
    re.I | re.S,
)
HEADING_RE = re.compile(r'<h[23]\b[^>]*>(.*?)</h[23]>', re.I | re.S)
URL_RE = re.compile(r'(?:https?://|www\.)[^\s<>()]+', re.I)
EMOJI_RE = re.compile(
    "[\U0001F000-\U0001FAFF\u2600-\u27BF]",
    re.UNICODE,
)
FEE_DOUBLE_TOPIC_RE = re.compile(
    r"(?:최신\s+금액|금액\s+질문)[^.!?]{0,80}"
    r"(?:현재|최신|표시|상담)\s+(?:적용\s+)?금액"
)
MISSING_CUE_RE = re.compile(
    r"(?:미기재|공란|비어|없(?:습니다|으므로|어서|는|어)?|"
    r"제공되지\s*않|확인되지\s*않|기재되어\s*있지\s*않)"
)
CONCRETE_GRADE_RE = re.compile(r"(?:초[1-6]|[중고][1-3])")
UNSUPPORTED_POSITIVE_GRADE_PREMISE_RE = re.compile(
    r"(?:이\s*학년\s*표기를\s*사용|상담\s*메모에\s*학년(?:값)?을\s*옮기|"
    r"가능\s*학년\s*자료를\s*읽|학년\s*범위를\s*비교|"
    r"등록\s*판단에\s*학년값|제공(?:된)?\s*학년값을\s*기록|"
    r"학년\s*목록과|확인된\s*학년값|학년\s*정보를\s*계획에|"
    r"원문\s*학년값|학년값과\s*실제|학년\s*표기만으로|"
    r"(?:센터|과목별|상단)\s*학년값|제공(?:된)?\s*학년\s*목록|"
    r"학년\s*목록\s*뒤|상단\s*학년\s*항목을\s*기준|학년값을)"
)
FAQ_TOPIC_RE = {
    "grades": re.compile(r"(?:학년|초[1-6]|[중고][1-3]|초등|중등|고등)"),
    "schools": re.compile(r"(?:학교|학교명)"),
    "location": re.compile(r"(?:주소|위치|방문|동선|경로|입구|건물|서비스\s*지역)"),
    "fee": re.compile(r"(?:교습비|비용|금액|교재비|총액|적용액)"),
}
FEE_AMOUNT_CERTAINTY_RE = re.compile(
    r"(?:교습비|비용|금액|교재비|총액|적용액)[^.!?]{0,20}"
    r"(?:\d[\d,]*\s*원|확정(?:입니다|됩니다)?|고정(?:입니다|됩니다)?|항상\s*같)"
)
FAQ_LOCATION_DOUBLE_TOPIC_RE = re.compile(
    r"(?:때는|전에는|뒤에는|하면서|하려면)\s+[^.!?]{0,80}?"
    r"(?:주소|위치|안내|경로|동선|지역|방법|정보|문구)(?:은|는)\s"
)
FAQ_BLANK_GUIDE_POSITIVE_PREMISES = (
    "현재 위치 안내는",
    "위치 안내가 있더라도",
    "방문 전 위치 문구의 변경 여부",
    "제공된 위치 문구를 확인",
    "위치 안내 원문을 사용",
)

BLANK_GUIDE_EXISTENCE_PREMISES = (
    "위치 안내 원문을 사용할 때는",
    "제공된 위치 문구를 확인한 뒤에는",
    "위치 안내값을 읽을 때는",
    "제공된 안내를 참고하면서",
    "주소와 위치 안내를 대조하면서",
    "위치 문구를 일정에 넣기 전에는",
    "제공 위치 안내를 참고하되",
)

UNSUPPORTED_GRADE_PREMISE_CUES = (
    "이 학년 표기를 사용할",
    "상담 메모에 학년을 옮길",
    "가능 학년 자료를 읽은",
    "학년 범위를 비교",
    "등록 판단에 학년값",
    "제공 학년값을 기록",
    "학년 목록과",
    "확인된 학년값",
    "학년 정보를 계획에",
    "원문 학년값",
    "학년값과 실제",
    "학년 표기만으로",
)


@dataclass(frozen=True)
class SourceExtra:
    registered_name: str
    raw_location_guide: str
    location_guide: str


@dataclass(frozen=True)
class Context:
    profile: phase1.Profile
    center: phase1.Center
    config: Mapping[str, Any]
    extra: SourceExtra

    @property
    def key(self) -> str:
        return f"{self.profile.category}|{self.center.slug}"

    @property
    def label(self) -> str:
        return str(self.config["label"])

    @property
    def subject(self) -> str:
        value = str(self.config["subject"])
        return "영어·수학" if value == "영수" else value

    @property
    def scope(self) -> str:
        prefix = str(self.config["prefix"])
        level = {"고": "고등", "중": "중등", "초": "초등"}.get(prefix, "")
        return " ".join(item for item in (level, self.subject) if item)

    @property
    def unsupported(self) -> bool:
        return phase1.unsupported_page(self.center, self.config)

    @property
    def schools(self) -> tuple[str, ...]:
        return phase1.school_order(self.center, self.config)


@dataclass
class Stats:
    counts: Counter[str] = field(default_factory=Counter)

    def add(self, key: str, value: int = 1) -> None:
        if value:
            self.counts[key] += value


@dataclass(frozen=True)
class PlannedDocument:
    path: Path
    before: bytes
    after: bytes
    kind: str


@dataclass
class QualityMetrics:
    paragraph_count: int = 0
    sentence_count: int = 0
    within_paragraph_excess: int = 0
    within_sentence_excess: int = 0
    max_locality: int = 0
    max_query: int = 0
    max_article_locality: int = 0
    max_paragraph: int = 0
    max_sentence: int = 0
    normalized_article_duplicates: int = 0
    exact_sentence_max_df: int = 0
    exact_paragraph_max_df: int = 0
    exact_sentence_excess: int = 0
    exact_paragraph_excess: int = 0
    normalized_sentence_max_df: int = 0
    normalized_paragraph_max_df: int = 0
    normalized_heading_max_df: int = 0
    normalized_faq_max_df: int = 0
    normalized_sentence_excess: int = 0
    normalized_paragraph_excess: int = 0


@dataclass
class BuildPlan:
    root: Path
    documents: list[PlannedDocument]
    stats: Stats
    unsupported: Counter[str]
    metrics: QualityMetrics
    external_hash_before: str
    authorized_paths: frozenset[Path]
    idempotent: bool

    @property
    def changes(self) -> list[PlannedDocument]:
        return [item for item in self.documents if item.before != item.after]


def clean(value: Any) -> str:
    return re.sub(r"\s+", " ", html.unescape(str(value or ""))).strip()


def escape(value: Any) -> str:
    return html.escape(str(value), quote=False)


def compact_slug(value: str) -> str:
    return phase1.compact_slug(value)


def deterministic_index(context: Context, salt: str, size: int) -> int:
    digest = hashlib.sha256(f"{context.key}|{salt}".encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") % size


def pick(context: Context, salt: str, values: Sequence[str]) -> str:
    if not values:
        raise ValueError(f"empty deterministic choice: {salt}")
    return values[deterministic_index(context, salt, len(values))]


def pick_avoiding_stems(
    context: Context,
    salt: str,
    values: Sequence[str],
    blocked: str,
    stems: Sequence[str],
) -> str:
    """Choose deterministically while avoiding an obvious verb echo."""
    compatible = [
        candidate
        for candidate in values
        if not any(stem in blocked and stem in candidate for stem in stems)
    ]
    if not compatible:
        raise ValueError(f"no compatible deterministic choice: {salt}")
    return compatible[deterministic_index(context, salt, len(compatible))]


ECHO_STEMS = (
    "구성", "정리", "안내", "배치", "작성", "구분", "분리", "제시", "설명",
    "묶", "정돈", "보여", "나누", "기록", "구획", "마련", "표시", "대조",
    "확인", "비교", "읽", "묻", "준비", "남기", "활용", "기준", "질문", "전에",
)
HARD_ECHO_STEMS = (
    "정리", "구분", "기록", "나누", "제시", "설명", "구획", "마련", "표시", "대조",
)


def pick_pair(
    context: Context,
    salt: str,
    leads: Sequence[str],
    endings: Sequence[str],
) -> tuple[str, str]:
    lead = pick(context, f"{salt}-lead", leads)
    ending = pick_avoiding_stems(
        context, f"{salt}-end", endings, lead, ECHO_STEMS
    )
    return lead, ending


def pick_distinct(
    context: Context,
    salt: str,
    values: Sequence[str],
    blocked: str,
) -> str:
    start = deterministic_index(context, salt, len(values))
    for offset in range(len(values)):
        candidate = values[(start + offset) % len(values)]
        if candidate != blocked:
            return candidate
    raise ValueError(f"no distinct deterministic choice: {salt}")


def sanitize_location_guide(raw: str) -> str:
    """Apply the narrow, audited location-guide cleanup allowlist."""
    value = html.unescape(raw or "")
    value = URL_RE.sub(" ", value)
    value = re.sub(r"학원\s*위치\s*안내드립니다", " ", value)
    value = re.sub(r"\^{2,}", "", value)
    value = value.replace("엘레베이터", "엘리베이터")
    value = EMOJI_RE.sub(" ", value)
    value = re.sub(r"\s+", " ", value).strip()
    value = re.sub(r"^[~\s]+|[~\s]+$", "", value)
    value = re.sub(r"\s+([,.!?;:])", r"\1", value)
    value = re.sub(r"([,.!?;:])(?=[가-힣A-Za-z])", r"\1 ", value)
    value = re.sub(r"\(\s*\)", " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value


def load_source_extras(reference_dir: Path) -> dict[str, SourceExtra]:
    source = reference_dir / "센터정보 정리.csv"
    with source.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if len(rows) != 371:
        raise ValueError(f"센터정보 행 불일치: {len(rows)}")
    result: dict[str, SourceExtra] = {}
    physical_guides: dict[tuple[str, str, str], set[str]] = {}
    for raw in rows:
        row = phase1.canonical_headers(raw)
        locality = clean(row["근처수업가능동네"])
        slug = compact_slug(locality)
        guide_raw = clean(row.get("위치안내", ""))
        guide = sanitize_location_guide(guide_raw)
        registered_name = clean(row.get("교육지원청명칭", ""))
        registration = clean(row.get("교육지원청등록번호", ""))
        center_name = clean(row.get("센터명", ""))
        address = clean(row.get("센터주소", ""))
        if not registered_name or not registration:
            raise ValueError(f"등록 학원명/번호 누락: {locality}")
        if slug in result:
            raise ValueError(f"source extra locality duplicate: {slug}")
        result[slug] = SourceExtra(registered_name, guide_raw, guide)
        physical_guides.setdefault((center_name, address, registration), set()).add(guide)
    conflicts = {key: values for key, values in physical_guides.items() if len(values) > 1}
    if conflicts:
        raise ValueError(f"physical location guide conflict: {list(conflicts.items())[:2]}")
    nonblank = sum(bool(item.location_guide) for item in result.values())
    if nonblank != EXPECTED_LOCATION_GUIDES or len(result) - nonblank != EXPECTED_BLANK_GUIDES:
        raise ValueError(
            f"location guide distribution mismatch: {nonblank}/{len(result) - nonblank}"
        )
    return result


HEADING_PREFIXES = (
    "원문을 기준으로", "제공 자료를 토대로", "상담 전에", "표기 내용을 기준으로",
    "제공 항목에서", "기록을 시작하며", "자료 순서를 따라", "자료 범위를 기준으로",
    "기초 정보로", "등록 판단 전에", "첫 검토 단계에서", "상단 자료를 볼 때",
    "사실 항목을 따라", "상담 메모를 만들며", "제공 범위 안에서", "원문 표기를 토대로",
)

HEADING_PARTS: dict[str, tuple[str, ...]] = {
    "identity": (
        "구분할 센터 식별 정보", "확인하는 등록 항목", "살펴볼 이름과 등록값",
        "대조하는 센터 기초 정보", "읽어야 할 식별 자료", "기록할 등록 정보",
        "확인할 센터명과 번호", "검토할 등록 원자료", "나누어 보는 식별 항목",
        "먼저 적을 센터 정보", "확정된 등록 표기", "비교할 센터 자료",
    ),
    "grades": (
        "가능 학년 표기를 읽는 방법", "학년 자료와 미기재 항목", "과목별 학년 범위 확인",
        "학년 칸에서 확인되는 내용", "가능 학년과 별도 질문", "학년 표기의 한계",
        "대상 학년 범위를 읽는 법", "학년 정보와 실제 일정 구분", "과목·학년 자료 점검",
        "학년 표기를 상담에 옮기는 법", "확인된 학년 범위", "학년 항목을 기록하는 기준",
    ),
    "schools": (
        "학교 참고 목록을 읽는 기준", "학교명과 현재 자료 구분", "학교 참고 항목 확인",
        "학교 이름을 과장 없이 보는 법", "학교 자료의 확인 범위", "참고 학교와 상담 질문",
        "학교명 표기의 올바른 사용", "학교 참고값을 기록하는 순서", "현재 학교 자료와의 대조",
        "학교 목록에서 확인할 점", "학교 참고 정보의 한계", "학교명 뒤에 물을 내용",
    ),
    "location": (
        "주소와 위치 안내 확인", "센터 주소를 기록하는 방법", "방문 전 위치 정보 점검",
        "서비스 지역과 제공 주소 구분", "주소 원문과 방문 확인", "센터 위치 자료 읽기",
        "제공 주소와 위치 문구", "방문 준비에 필요한 주소", "물리적 위치 정보 확인",
        "주소·위치 항목의 범위", "센터 위치를 확인하는 순서", "방문 전에 볼 위치 자료",
    ),
    "consult": (
        "교습비와 상담 항목 정리", "확인 질문을 남기는 방법", "등록 전에 적을 답변",
        "상담 답변을 비교하는 순서", "비용·일정 확인 기준", "상담 메모의 필수 항목",
        "미기재 내용을 묻는 순서", "교습비 자료와 최신 답변", "확인할 수업 조건",
        "상담에서 구분할 사실", "답변 날짜까지 기록하는 법", "비용과 수업 조건 대조",
    ),
}

UNSUPPORTED_GRADE_HEADING_PREFIXES = (
    "미기재 상태부터 확인하는", "공란 사실을 먼저 밝히는", "확인 전 상태로 남기는",
    "원자료 부재를 구분하는", "대상 정보 미기재를 다루는", "상담 전 공란을 점검하는",
    "추정 없이 미확인을 적는", "기재되지 않은 항목을 보는", "원문 공란을 보존하는",
    "미확인 상태를 분명히 하는", "제공되지 않은 항목을 묻는", "학년 공란부터 살펴보는",
    "원자료 미기재를 기록하는", "대상 정보 부재를 확인하는", "답변 전 공란으로 두는",
    "학년 미확인을 상담으로 잇는",
)
UNSUPPORTED_GRADE_HEADING_ENDINGS = (
    "미기재 대상 학년 확인", "학년 공란과 상담 질문", "미기재 학년 항목",
    "대상 학년 미기재 상태", "학년 정보 미기재 안내", "공란 학년 확인 순서",
    "학년 미기재 사실", "미기재 대상 범위 확인 질문", "학년 공란 기록법",
    "미기재 학년 상담 항목", "학년 정보 공란 상태", "미기재 대상 학년 문의 기준",
    "원자료 학년 공란", "학년 미기재와 답변 구분", "대상 학년 미기재 재확인",
    "학년 공란 상담 준비",
)


def heading(context: Context, slot: str) -> str:
    if context.unsupported and slot == "grades":
        prefix = pick(
            context, "unsupported-heading-grade-prefix", UNSUPPORTED_GRADE_HEADING_PREFIXES
        )
        ending = pick_avoiding_stems(
            context,
            "unsupported-heading-grade-ending",
            UNSUPPORTED_GRADE_HEADING_ENDINGS,
            prefix,
            ECHO_STEMS,
        )
        return prefix + " " + ending
    slot_index = ("identity", "grades", "schools", "location", "consult").index(slot)
    prefix_start = deterministic_index(
        context, "heading-prefix-order", len(HEADING_PREFIXES)
    )
    # A coprime stride makes the five section prefixes unique on every page.
    prefix = HEADING_PREFIXES[
        (prefix_start + slot_index * 3) % len(HEADING_PREFIXES)
    ]
    part = pick_avoiding_stems(
        context, f"heading-{slot}-part", HEADING_PARTS[slot], prefix, ECHO_STEMS
    )
    return prefix + " " + part


INTRO_SOURCE = (
    "센터 식별 정보와 학년·학교 참고 항목", "원자료의 센터·학년·학교 항목",
    "센터명·주소·등록값과 가능 학년 자료", "제공 자료에 적힌 식별·학년·학교 정보",
    "센터 원자료와 과목별 학년 표기", "주소·등록 정보와 학교 참고 목록",
    "확인된 센터 사실과 학년 자료", "센터를 구분하는 값과 학교 참고 항목",
    "원문에 있는 주소·등록·학년 정보", "센터 자료의 사실 항목과 참고 목록",
    "상단에 제시된 센터·학년·학교 자료", "원자료로 확인되는 이름·주소·학년 값",
    "센터 기준 정보와 과목별 확인 항목", "주소와 등록값을 포함한 원자료 범위",
    "확인 가능한 센터 사실과 참고 학교", "학년 표기와 센터 식별 자료",
)
INTRO_VERBS = (
    "원문 표기를 따라 먼저 나누어 보여 주고", "확인된 범위 안에서 항목별로 정리하고",
    "원문과 뜻이 달라지지 않도록 구분해 제시하고", "사실값이 섞이지 않게 순서대로 배치하고",
    "상담 답변과 분리해 읽을 수 있도록 적고", "확인된 값만 기준으로 묶어 보여 주고",
    "자료의 출처가 드러나도록 차례로 정리하고", "원자료 항목을 바꾸지 않고 먼저 제시하고",
    "확정된 사실과 참고값으로 나누어 적고", "상담 전 확인 순서에 맞춰 펼쳐 보이고",
    "제공 범위를 넘지 않도록 구획해 설명하고", "식별값과 참고 정보를 따로 정돈하고",
    "기록 가능한 사실부터 앞에 제시하고", "센터 자료의 항목명을 따라 배열하고",
    "원자료와 추가 질문을 구별해 보여 주고", "검증된 값만 남기는 방식으로 정리하고",
)
INTRO_UNKNOWNS = (
    "시간표·교재·반 구성처럼 적혀 있지 않은 내용", "개설 일정과 수업 방식 등 미기재 항목",
    "요일·시간·교재처럼 현재 답변이 필요한 조건", "정원·일정·과제처럼 원자료 밖의 정보",
    "수업 횟수와 반 편성 등 별도 확인 사항", "등록 시점에 달라질 수 있는 일정과 비용",
    "구체적인 시간표와 교재처럼 표에 없는 항목", "개설 여부·수업 방식·일정에 관한 질문",
    "현재 적용 조건과 세부 비용 같은 미확인 내용", "과제·보강·상담 주기 등 추가 질문",
    "반 배정과 시작일처럼 자료만으로 모르는 정보", "수업 시간과 비용의 최신 적용 조건",
    "교재·횟수·결석 처리 같은 상담 항목", "신청 가능 여부와 세부 일정",
    "표에 없는 수업 조건과 변경 기준", "현재 운영 여부를 포함한 추가 정보",
)
INTRO_ACTIONS = (
    "답변 날짜와 함께 따로 남기도록", "상담 메모의 별도 칸에 기록하도록",
    "확인 주체와 시점을 붙여 적도록", "원자료 사실과 섞지 않고 확인하도록",
    "등록 판단 전에 다시 묻도록", "최신 답변으로 별도 보완하도록",
    "확인 전에는 사실처럼 쓰지 않도록", "상담에서 항목별로 대조하도록",
    "답변을 받은 뒤에만 계획에 넣도록", "날짜가 있는 답변으로 구분하도록",
    "센터 확인 전에는 미정으로 남기도록", "자료의 범위 밖임을 표시하도록",
    "질문과 답변을 한 줄씩 맞춰 적도록", "실제 적용 여부를 따로 점검하도록",
    "원문과 상담 답변을 분리해 보관하도록", "확인 결과가 없으면 단정하지 않도록",
)
INTRO_ENDINGS = (
    "구성했습니다", "정리했습니다", "안내합니다", "배치했습니다",
    "작성했습니다", "구분했습니다", "제시합니다", "설명합니다",
    "묶었습니다", "정돈했습니다", "보여 줍니다", "나누었습니다",
    "기록했습니다", "구획했습니다", "마련했습니다", "구성한 페이지입니다",
)

UNSUPPORTED_INTRO_LEADS = (
    "이 센터 자료는", "첫 안내에서는", "원자료 범위를 읽을 때는", "자료 첫머리에서는",
    "등록 전 검토에서는", "센터 안내를 시작하며", "제공 범위를 확인하면", "상단 사실을 보기 전에",
    "상담 준비의 첫 단계에서는", "자료상 확인 상태를 보면", "센터 정보를 정리하며", "원문 항목을 대조하면",
    "첫 확인 문장에서는", "원자료 사실을 나누며", "상담 질문을 만들기 전에", "현재 페이지는",
)
UNSUPPORTED_INTRO_ACTIONS = (
    "실제 개설 여부와 대상 학년은 상담 답변 전까지 미확인으로 둡니다",
    "임의의 학년을 더하지 않고 현재 운영 여부를 별도 질문으로 남깁니다",
    "대상 학년·요일·시간은 답변을 받은 뒤에만 기록하도록 구성했습니다",
    "공란 자체를 사실로 보존하고 수업 조건은 상담에서 확인하도록 안내합니다",
    "다른 과목이나 센터의 학년 정보를 대신 쓰지 않도록 구분했습니다",
    "개설 여부와 대상 학년의 답변 주체·확인일을 따로 남기도록 했습니다",
    "대상 범위를 추정하지 않고 담당자 답변을 기다리도록 정리했습니다",
    "미기재 사실과 이후 상담 답변을 서로 다른 칸에 적도록 안내합니다",
    "현재 신청 가능 여부는 확인 결과가 있을 때만 판단하도록 했습니다",
    "수업 시간과 교재도 원자료 밖의 확인 항목으로 분리했습니다",
    "상담 전에는 어떤 학년도 제공 대상으로 단정하지 않도록 구성했습니다",
    "대상 학년과 시작일을 각각 질문하고 답변 날짜를 붙이도록 했습니다",
    "확인되지 않은 반 편성이나 운영 범위를 만들지 않도록 정리했습니다",
    "원문 공란을 유지한 채 현재 대상과 일정을 문의하도록 안내합니다",
    "답변이 없으면 대상 학년 칸을 미확인 상태로 남기도록 했습니다",
    "실제 수업 조건은 센터 확인 뒤에만 사실란에 넣도록 구분했습니다",
)


def render_intro(context: Context) -> str:
    # The exact H1 already appears in navigation and source-facing summary
    # blocks.  Use a reader-facing locality lead here so the manuscript does
    # not echo the complete query merely for ranking signals.
    if context.unsupported:
        lead = pick(context, "unsupported-intro-lead", UNSUPPORTED_INTRO_LEADS)
        action = pick_avoiding_stems(
            context,
            "unsupported-intro-action",
            UNSUPPORTED_INTRO_ACTIONS,
            lead,
            ECHO_STEMS,
        )
        sentence = (
            f"{lead} {context.scope} 대상 학년 항목이 원자료에 기재되어 있지 않다는 "
            f"점을 먼저 밝힙니다. {action}."
        )
        return (
            f'<div class="academy-intro-answer academy-source-unconfirmed" '
            f'{SOURCE_MARKER}><p>{escape(sentence)}</p></div>'
        )
    query = "이 센터 자료"
    intro_source = pick(context, "intro-source", INTRO_SOURCE)
    intro_verb = pick_avoiding_stems(
        context, "intro-verb", INTRO_VERBS, intro_source, ECHO_STEMS
    )
    intro_unknown = pick_avoiding_stems(
        context,
        "intro-unknown",
        INTRO_UNKNOWNS,
        intro_source + " " + intro_verb,
        ECHO_STEMS,
    )
    intro_action = pick_avoiding_stems(
        context,
        "intro-action",
        INTRO_ACTIONS,
        intro_source + " " + intro_verb + " " + intro_unknown,
        ECHO_STEMS,
    )
    sentence = (
        f"{query} 안내는 "
        f"{intro_source}에 관해 "
        f"{intro_verb}, "
        f"{intro_unknown}의 경우 "
        f"{intro_action} 했습니다."
    )
    attrs = (
        f'class="academy-intro-answer academy-source-unconfirmed" {SOURCE_MARKER}'
        if context.unsupported
        else 'class="academy-intro-answer"'
    )
    return f"<div {attrs}><p>{escape(sentence)}</p></div>"


def supported_grade_summary(context: Context) -> str:
    if str(context.config["subject"]) == "영수":
        english = grade_list(context.center.grades["영어"])
        math = grade_list(context.center.grades["수학"])
        return f"영어 학년값은 {english}, 수학 학년값은 {math}"
    values = grade_list(grades_for(context, str(context.config["subject"])))
    return f"{context.scope} 가능 학년은 {values}"


def unsupported_disclosure(context: Context, variant: str) -> str:
    if str(context.config["subject"]) == "영수":
        english = grade_list(context.center.grades["영어"]) or "원자료 미기재"
        math = grade_list(context.center.grades["수학"]) or "원자료 미기재"
        if variant == "hero":
            fact = (
                f"제공된 센터 자료에서 영어 학년 항목은 “{english}”, "
                f"수학 학년 항목은 “{math}”로 확인됩니다."
            )
        else:
            fact = (
                f"영어 학년 항목의 원문 값은 “{english}”이며, "
                f"수학 학년 항목의 원문 값은 “{math}”입니다."
            )
        if variant == "hero":
            return (
                fact
                + " 두 과목을 함께 보는 실제 개설 여부·대상 학년·시간은 "
                "상담 전 확인해야 합니다."
            )
        return (
            fact
            + " 영어·수학 통합 대상의 실제 개설 여부와 적용 학년·시간은 "
            "상담 답변으로 따로 확인하세요."
        )
    if variant == "hero":
        return (
            f"센터 원자료의 {context.scope} 가능 학년 항목은 미기재 상태입니다. "
            "실제 개설 여부·대상 학년·시간은 상담 전 확인해야 합니다."
        )
    return (
        f"제공 자료에서 {context.scope} 가능 학년은 확인되지 않습니다. "
        "실제 개설 여부와 대상 학년·시간은 상담 답변으로 따로 확인하세요."
    )


def render_hero_lead(context: Context) -> str:
    if context.unsupported:
        return (
            f"{context.center.display_geo} 자료에서 센터·주소·학교 참고값과 "
            f"미기재된 {context.scope} 학년 항목을 구분합니다. "
            + unsupported_disclosure(context, "hero")
        )
    return (
        f"{context.center.display_geo} 센터 자료를 바탕으로 식별값·학년·학교·주소를 "
        "구분했습니다. 상담에서는 시간표·교재·비용의 "
        "현재 적용값을 원자료 사실과 따로 확인하세요."
    )


def render_hero_panel(context: Context) -> str:
    if context.unsupported:
        paragraph = (
            "이 자료의 센터 식별값과 학교·주소는 확인하되, "
            f"{context.scope} 개설 여부·대상 학년·시간은 상담 답변 전에 단정하지 않습니다."
        )
        paragraph_attrs = f' class="academy-source-unconfirmed-note" {SOURCE_MARKER}'
    else:
        paragraph = (
            f"이 자료의 {context.scope} 학년 표기, 참고 학교, 센터 주소를 "
            "원문 사실로 확인하고 시간표·교재·비용은 최신 답변으로 따로 기록합니다."
        )
        paragraph_attrs = ""
    return (
        '<aside class="academy-hero-panel">'
        '<strong>원자료 사실과 상담 확인 항목</strong>'
        f'<p{paragraph_attrs}>{escape(paragraph)}</p>'
        '<div class="academy-stat-grid">'
        '<div class="academy-stat"><b>01</b><span>센터 사실</span></div>'
        '<div class="academy-stat"><b>02</b><span>참고 정보</span></div>'
        '<div class="academy-stat"><b>03</b><span>별도 확인</span></div>'
        '</div></aside>'
    )


def render_top_answer(context: Context) -> str:
    if context.unsupported:
        return (
            f"{context.center.display_geo} 센터 자료입니다. "
            + unsupported_disclosure(context, "top")
            + " 센터명·주소·등록번호는 옆의 핵심정보에서 원문대로 확인할 수 있습니다."
        )
    return (
        f"{context.center.display_geo} 센터 자료입니다. 센터명·{context.scope} 가능 학년·"
        "학교 참고·주소·등록번호는 옆의 핵심정보에서 원문대로 확인하세요. "
        "시간표·교재·비용의 현재 적용 조건은 상담 답변과 확인일을 따로 기록하세요."
    )


MAP_LEADS = (
    "센터 방문을 준비할 때는", "제공 주소를 확인한 뒤에는", "위치 자료를 방문 메모에 옮길 때는", "출발 준비 단계에서는",
    "센터 위치를 다시 대조할 때는", "방문 날짜를 정하기 전에는", "주소와 위치 항목을 읽을 때는", "방문 경로를 확정하기 전에는",
    "원자료 위치 정보를 볼 때는", "센터 주소를 상담표에 적을 때는", "물리 위치 사실을 확인한 다음에는", "위치 안내와 주소를 나란히 볼 때는",
    "센터를 찾아갈 계획을 세우기 전에는", "주소 항목을 확인했다면", "방문 메모의 위치 칸에는", "센터 위치 자료를 활용할 때는",
)
MAP_BLANK_LEADS = (
    "위치 안내가 미기재된 상태에서는", "원자료 위치 칸이 비어 있으므로", "별도 위치 문구가 없을 때는",
    "위치 설명이 제공되지 않아", "방문 안내 공란을 확인한 뒤에는", "동선 정보가 기재되어 있지 않으므로",
    "위치 안내값이 없는 경우에는", "원문 위치 항목이 공란이라", "별도 경로 설명이 확인되지 않아",
    "주소 밖의 안내가 비어 있으므로", "위치 문구 미기재를 기록한 다음에는", "원자료에 방문 설명이 없어서",
    "위치 안내 공란 상태에서는", "제공 위치 문구가 없는 페이지에서는", "별도 동선 항목이 비어 있어",
    "위치 안내가 기재되어 있지 않은 자료에서는",
)
MAP_ENDS_GUIDE = (
    "원자료 위치 안내와 현재 출입 방법을 따로 대조하세요", "제공 위치 문구의 최신 적용 여부를 다시 확인하세요", "위치 안내는 출처로 두고 방문 당일 정보를 우선하세요", "주소 원문과 출입 답변의 확인일을 같이 적으세요",
    "위치 안내 밖의 거리나 소요 시간은 임의로 추정하지 마세요", "제공 문구와 실제 출입구 정보를 구분해 확인하세요", "원문 위치 안내에 변경이 없는지 출발 전에 물어보세요", "위치 문구를 예상 경로로 확장하지 말고 현재 안내를 확인하세요",
    "주소와 위치 안내를 서로 다른 원자료 항목으로 기록하세요", "건물·층·출입 정보의 현재 상태를 센터에 다시 확인하세요", "제공 위치 안내를 사실란에, 현재 답변을 상담란에 나누어 적으세요", "원자료 문구와 현재 방문 조건을 같은 사실로 단정하지 마세요",
    "방문 전 위치 안내의 적용 날짜와 출입 방법을 확인하세요", "위치 안내 어절은 그대로 읽고 추가 경로는 별도로 확인하세요", "제공 위치 자료와 방문 당일 안내가 같은지 점검하세요", "원문 위치 문구를 최신 경로 보장으로 해석하지 마세요",
)
MAP_ENDS_BLANK = (
    "별도 위치 안내가 없으므로 현재 출입 방법을 직접 확인하세요", "제공 주소 밖의 경로는 추정하지 말고 방문 전에 물어보세요", "원자료 위치 칸이 비어 있으므로 건물·층·입구를 별도로 확인하세요", "주소만 사실란에 적고 이동 경로는 확인 전까지 비워 두세요",
    "위치 안내 미기재 상태이므로 방문 당일 안내를 우선하세요", "별도 동선 정보가 없어 출발 전 최신 답변이 필요합니다", "주소와 실제 출입구 정보를 서로 다른 확인 항목으로 두세요", "원자료에 경로 설명이 없어 센터에 최신 방문 방법을 물어봐야 합니다",
    "제공 주소와 현재 출입 정보를 별도 메모로 나누어 기록하세요", "위치 문구가 빈 항목이므로 거리나 소요 시간을 임의로 만들지 마세요", "원자료에 없는 건물·층 정보는 상담 답변 뒤에만 기록하세요", "방문 계획은 제공 주소와 현재 안내를 모두 확인한 뒤에 세우세요",
    "별도 위치안내가 없으므로 출입 방법에 확인일을 붙여 두세요", "주소 원문은 보존하고 경로 정보는 별도 수단으로 확인하세요", "위치 항목이 비어 있으므로 입구와 층은 방문 전에 다시 물어보세요", "제공 주소를 최신 이동 경로로 단정하지 말고 현재 안내를 확인하세요",
)
NAV_HEAD_PREFIXES = (
    "원자료 범위를 이어 보는", "과목·학년을 나누어 보는", "현재 페이지와 구분할", "다른 자료 범위를 확인하는",
    "상담 주제를 나누기 위한", "학년·과목별로 이동하는", "제공 항목을 따로 읽는", "관련 페이지 범위를 보는",
    "센터 자료를 과목별로 나누는", "현재 범위와 인접 범위를 구분하는", "과목 표기를 대조하는", "학년 단계를 따로 확인하는",
    "동일 센터 자료를 더 보는", "페이지 범위를 바꿔 보는", "상단 표기와 관련 항목을 잇는", "현재 주제 밖 자료를 확인하는",
)
NAV_HEAD_ENDS = (
    "관련 페이지 안내", "과목·학년 페이지", "인접 자료 목록", "관련 범위 안내",
    "추가 자료 선택", "다른 과목·학년 범위", "센터 관련 페이지", "자료 이동 안내",
    "과목별 참고 페이지", "학년별 참고 범위", "관련 안내 페이지", "페이지 구분 목록",
    "과목·단계 이동 목록", "연결된 자료 범위", "다른 주제 확인", "자료 범위 선택",
)
NAV_PARAGRAPH_LEADS = (
    "링크를 선택할 때는", "다른 과목·학년 페이지를 볼 때는", "관련 자료로 이동한 뒤에는", "페이지 범위를 바꾸면",
    "인접 페이지를 대조할 때는", "과목 범위를 바꿔 읽을 때는", "학년 단계가 다른 자료에서는", "관련 안내를 열어 보면",
    "다른 페이지의 핵심정보에서는", "연결 목록을 활용할 때는", "과목·학년 조합이 달라지면", "현재 주제 밖의 안내에서는",
    "동일 센터의 다른 범위를 볼 때는", "새 페이지로 이동했다면", "자료 범위를 비교할 때는", "관련 페이지의 사실값을 볼 때는",
)
NAV_PARAGRAPH_ENDS = (
    "해당 페이지의 학년·학교·주소 항목을 다시 확인하세요", "현재 페이지의 사실값을 다른 범위에 그대로 적용하지 마세요", "각 페이지에 표시된 원자료 범위를 따로 읽으세요", "과목·학년 표기와 미기재 항목을 새로 대조하세요",
    "센터 식별값은 같더라도 과목별 학년 항목을 다시 보세요", "연결된 페이지의 상단 자료 확인 상태를 우선하세요", "현재 범위와 다른 표기를 섞지 말고 별도로 메모하세요", "페이지별 원자료 사실과 상담 질문을 처음부터 확인하세요",
    "같은 주소를 쓰더라도 학년·과목 자료를 각각 확인하세요", "이동한 페이지의 표시 학년과 학교 참고 범위를 다시 보세요", "링크 이름만으로 수업 가능 여부를 단정하지 마세요", "각 범위의 미기재 항목은 상담 확인 전까지 별도로 두세요",
    "관련 페이지마다 제공 항목의 뜻과 범위를 다시 확인하세요", "현재 자료에 없는 학년·과목 정보를 인접 페이지에서 가져오지 마세요", "페이지 범위가 바뀌면 주소·학년·학교 사실도 새로 대조하세요", "각 링크의 원자료 표기를 등록 판단 전에 따로 읽으세요",
)


def render_map_caption(context: Context) -> str:
    ending_pool = MAP_ENDS_GUIDE if context.extra.location_guide else MAP_ENDS_BLANK
    lead = pick(
        context,
        "map-caption-lead",
        MAP_LEADS if context.extra.location_guide else MAP_BLANK_LEADS,
    )
    ending = pick_avoiding_stems(
        context, "map-caption-end", ending_pool, lead, ECHO_STEMS
    )
    return f"{lead} {ending}."


def transform_navigation_copy(block: str, context: Context) -> str:
    heading_prefix = pick(context, "nav-heading-prefix", NAV_HEAD_PREFIXES)
    heading_end = pick_avoiding_stems(
        context, "nav-heading-end", NAV_HEAD_ENDS, heading_prefix, ECHO_STEMS
    )
    heading_text = heading_prefix + " " + heading_end
    paragraph_lead, paragraph_end = pick_pair(
        context, "nav-paragraph", NAV_PARAGRAPH_LEADS, NAV_PARAGRAPH_ENDS
    )
    paragraph = paragraph_lead + " " + paragraph_end + "."
    paragraph_attrs = (
        f' class="academy-source-unconfirmed-note" {SOURCE_MARKER}'
        if context.unsupported else ""
    )
    block, head_count = NAV_HEAD_RE.subn(
        rf'\1{escape(heading_text)}\2<p{paragraph_attrs}>{escape(paragraph)}</p>\3',
        block,
        count=1,
    )
    if head_count != 1:
        raise ValueError(f"navigation head cardinality: {head_count}")

    def strong_replace(match: re.Match[str]) -> str:
        value = fragment_text(match.group(2))
        prefix = context.center.display_locality + " "
        if value.startswith(prefix):
            value = value[len(prefix):]
        return match.group(1) + escape(value) + match.group(3)

    return SUBJECT_STRONG_RE.sub(strong_replace, block)


def transform_summary_copy(text: str, context: Context) -> str:
    def hero_replace(match: re.Match[str]) -> str:
        block = match.group(0)
        lead_attrs = (
            f' class="academy-source-unconfirmed-note" {SOURCE_MARKER}'
            if context.unsupported else ""
        )
        block, lead_count = HERO_LEAD_RE.subn(
            rf'\1<p{lead_attrs}>{escape(render_hero_lead(context))}</p>', block, count=1
        )
        block, panel_count = HERO_PANEL_RE.subn(render_hero_panel(context), block, count=1)
        if lead_count != 1 or panel_count != 1:
            raise ValueError(f"hero copy cardinality: {lead_count}/{panel_count}")
        return block

    text, hero_count = HERO_RE.subn(hero_replace, text, count=1)
    if hero_count != 1:
        raise ValueError(f"hero cardinality: {hero_count}")
    text, answer_count = TOP_ANSWER_RE.subn(
        rf'\1<p>{escape(render_top_answer(context))}</p>\2', text, count=1
    )
    if answer_count != 1:
        raise ValueError(f"top answer cardinality: {answer_count}")
    text, caption_count = MAP_CAPTION_RE.subn(
        rf'\1{escape(render_map_caption(context))}\2', text, count=1
    )
    if caption_count != 1:
        raise ValueError(f"map caption cardinality: {caption_count}")
    text, navigation_count = NAV_SECTION_RE.subn(
        lambda match: transform_navigation_copy(match.group(0), context), text, count=1
    )
    if navigation_count != 1:
        raise ValueError(f"navigation section cardinality: {navigation_count}")
    return text


def render_meta_description(context: Context) -> str:
    if context.unsupported:
        value = (
            f"{context.center.display_geo} {context.label} 원자료 확인 안내입니다. "
            f"{context.scope} 가능 학년이 미기재되어 센터·주소·등록정보와 실제 개설 여부·대상 학년·시간의 상담 확인 항목을 구분했습니다."
        )
    else:
        value = (
            f"{context.center.display_geo} {context.label} 안내입니다. "
            f"{context.center.center_name}의 {context.scope} 가능 학년, 참고 학교, 제공 주소, "
            "등록정보와 교습비·일정 상담 확인 항목을 원자료 범위에서 정리했습니다."
        )
    value = clean(value)
    if not 50 <= len(value) <= 160:
        raise ValueError(f"phase3 meta description length: {len(value)}")
    return value


def transform_meta_descriptions(text: str, context: Context) -> tuple[str, str]:
    description = render_meta_description(context)
    escaped = html.escape(description, quote=True)

    def replace(match: re.Match[str]) -> str:
        return match.group(1) + match.group(2) + escaped + match.group(2)

    text, description_count = META_DESCRIPTION_RE.subn(replace, text, count=1)
    text, og_count = OG_DESCRIPTION_RE.subn(replace, text, count=1)
    if description_count != 1 or og_count != 1:
        raise ValueError(f"meta description cardinality: {description_count}/{og_count}")
    return text, description


FACT_LEADS = (
    "원자료를 옮겨 적으면", "센터 자료를 확인하면", "등록 항목을 보면", "제공 표기를 따르면",
    "상단 사실값을 기준으로 하면", "확인된 센터 자료를 보면", "식별 정보를 대조하면", "원문 항목을 확인하면",
    "센터를 구분하는 자료를 보면", "등록 자료의 한 묶음을 보면", "사실 항목을 차례로 보면", "원자료 범위 안에서 확인하면",
    "센터 식별 단계에서 살펴보면", "제공된 식별값을 보면", "기록 가능한 등록 정보를 보면", "자료의 등록 칸을 확인하면",
)
FACT_ENDINGS = (
    "함께 제시되어 있습니다", "같은 확인 묶음으로 적혀 있습니다", "나란히 기재되어 있습니다",
    "한 항목 안에서 확인됩니다", "원문 값으로 기록되어 있습니다", "서로 구분해 표시되어 있습니다",
    "변경 없이 확인됩니다", "등록 사실로 함께 남아 있습니다", "짝을 이루어 있습니다",
    "동일한 자료 행에 적혀 있습니다", "센터 확인값으로 제시됩니다", "각각의 원문으로 확인됩니다",
    "등록 자료 안에 함께 있습니다", "식별용 사실로 나열되어 있습니다", "자료 안에서 확인됩니다",
    "상담 전 대조할 값으로 적혀 있습니다",
)
CENTER_LEADS = (
    "센터명 항목에는", "원자료의 센터명은", "제공된 센터 이름은", "센터 식별명에는",
    "상단 센터 기준에는", "자료에 기록된 센터명은", "센터 이름 칸에는", "확인된 센터 표기는",
    "원문 센터명으로는", "센터를 가리키는 이름은", "제공 자료의 이름 항목은", "센터 기준값은",
    "식별용 센터 이름은", "센터 자료 첫 항목은", "등록 전 확인할 센터명은", "자료상 센터명은",
)
CENTER_ENDINGS = (
    "기재되어 있습니다", "원문에 적혀 있습니다", "확인됩니다", "표시되어 있습니다",
    "센터 기준에 제시됩니다", "식별 항목에 남아 있습니다", "자료에 기록되어 있습니다", "상단 정보에서 확인됩니다",
    "확인용 이름에 쓰입니다", "제공 항목에서 볼 수 있습니다", "센터 구분 항목에 들어 있습니다", "원자료에 남아 있습니다",
    "사실 항목에 기록됩니다", "자료에서 확인할 수 있습니다", "변경 없이 제시됩니다", "확인 항목에 들어 있습니다",
)
CENTER_REFERENCE_LEADS = (
    "센터명을 다시 대조할 때는", "제공된 센터 이름을 확인하려면", "센터 식별명을 확인할 때는", "센터 이름 항목을 볼 때는",
    "제공 자료의 센터명을 확인할 때는", "센터를 이름으로 구분할 때는", "센터 기준 이름을 기록하려면", "등록 자료와 센터명을 나누어 볼 때는",
    "상단 센터 식별 항목을 읽을 때는", "센터 이름의 출처를 보려면", "센터명 원문을 확인할 때는", "센터 이름을 메모에 적을 때는",
    "센터 식별값 중 이름을 확인할 때는", "제공 센터명을 찾으려면", "센터 이름을 확인 항목으로 둘 때는", "원문 센터 표기를 활용할 때는",
)
CENTER_REFERENCE_ENDS = (
    "상단 ‘센터 기준’ 행에서 확인하세요", "옆의 핵심정보에서 원문대로 읽을 수 있습니다", "상단 식별 정보에서 원문 이름을 확인하세요", "등록 학원명·등록번호와 섞지 말고 별도로 기록하세요",
    "상단 센터 자료 칸에서 출처를 확인하세요", "핵심정보의 센터 기준값으로 따로 보세요", "원자료 이름 항목과 등록 식별값을 구분해 적으세요", "센터 기준 행의 표기를 변경 없이 활용하세요",
    "센터 식별 자료에서 확인한 뒤 등록값과 대조하세요", "상단 이름값을 그대로 옮기고 별도 추론을 더하지 마세요", "핵심정보의 원문 표기로만 확인하세요", "제공된 이름값을 센터 식별용으로만 사용하세요",
    "상단 센터 기준 항목에서 확인일과 함께 적으세요", "센터명을 등록번호를 대신하는 식별자로 쓰지 마세요", "원문 이름은 핵심정보에, 등록 값은 등록 문단에 나누어 두세요", "옆의 센터 기준 항목을 출처로 삼아 확인하세요",
    "상단 센터 이름을 원문 그대로 옮기세요", "핵심정보의 센터 이름만 쓰고 등록값은 다음 문단에 두세요",
    "제공 이름을 센터 식별값으로만 쓰세요", "상단 이름값과 등록 학원명·번호를 서로 다른 칸에 적으세요",
)


def render_identity_paragraphs(context: Context) -> list[str]:
    lead, end = pick_pair(context, "identity-registered", FACT_LEADS, FACT_ENDINGS)
    registered = (
        f'<p class="academy-registration-facts">{escape(lead)} 등록 학원명은 “'
        f'<span {REGISTERED_NAME_ATTR}>{escape(context.extra.registered_name)}</span>”이고 '
        f'등록번호는 “{escape(context.center.registration)}”이며, '
        f'두 값이 {escape(end)}.</p>'
    )
    center_lead, center_end = pick_pair(
        context, "identity-center-reference", CENTER_REFERENCE_LEADS, CENTER_REFERENCE_ENDS
    )
    center_sentence = f"{center_lead} {center_end}."
    return [registered, f"<p>{escape(center_sentence)}</p>"]


GRADE_FACT_LEADS = (
    "가능 학년 원문을 보면", "학년 항목을 확인하면", "센터 자료의 학년 칸을 보면", "제공된 학년 표기를 보면",
    "과목별 가능 학년 자료를 보면", "상단 학년 사실값을 확인하면", "원자료의 학년 범위를 보면", "학년 정보 행을 보면",
    "센터가 제공한 학년 목록을 확인하면", "확인된 가능 학년 표기를 보면", "학년 자료를 그대로 읽으면", "원문 학년 항목을 확인하면",
    "가능 학년 칸을 대조하면", "제공 자료의 대상 학년을 보면", "학년 범위 사실을 확인하면", "과목·학년 표기를 보면",
)
FAQ_GRADE_FACT_LEADS = (
    "학년 질문에 답할 원자료를 보면", "상단 학년값을 다시 대조하면",
    "학년 답변의 사실 범위를 확인하면", "질문과 학년 표기를 맞추어 보면",
    "가능 학년 문답의 원문 기준을 보면", "상담 전 학년 항목을 다시 읽으면",
    "학년 정보의 자료상 답을 확인하면", "과목별 학년 질문을 원문과 맞추면",
    "상단 가능 학년 행을 다시 보면", "학년 범위 질문의 확인 값을 보면",
    "제공 학년값의 답변 범위를 확인하면", "학년 질문을 사실 항목으로 나누면",
    "원자료 학년 표기를 답변에 옮기면", "학년 정보를 상단 자료에서 확인하면",
    "대상 학년 질문을 원문과 대조하면", "학년 값의 출처를 다시 확인하면",
)
GRADE_FACT_ENDINGS = (
    "원자료에 기재되어 있습니다", "원문대로 확인됩니다", "학년 목록에 적혀 있습니다", "제공 자료에 표시되어 있습니다",
    "확인된 범위로 제시됩니다", "상단 표기와 같게 확인됩니다", "제공 항목에 남아 있습니다", "학년 사실로 확인됩니다",
    "과목별 범위로 구분되어 있습니다", "자료의 학년 목록으로 남아 있습니다", "확인 가능한 목록에 제시됩니다", "원자료 순서대로 적혀 있습니다",
    "항목별로 적혀 있습니다", "제공 범위 안에서 확인됩니다", "그대로 표시되어 있습니다", "학년 표에 제시되어 있습니다",
)
UNSUPPORTED_ENDINGS = (
    "기재되어 있지 않습니다", "원자료에서 확인되지 않습니다", "미기재 상태로 남아 있습니다", "빈 항목으로 표시되어 있습니다",
    "제공 목록에 없습니다", "학년 범위로 적혀 있지 않습니다", "확정된 값으로 제시되지 않습니다", "자료에서 확인되지 않습니다",
    "원문에 적혀 있지 않습니다", "미기재로 표시됩니다", "제공 자료만으로 확인되지 않습니다", "확인값 없이 비어 있습니다",
    "학년 목록으로 제시되지 않습니다", "원자료상 공란으로 남아 있습니다", "기재된 범위로 확인되지 않습니다", "상담 전 확인 항목으로 남아 있습니다",
)
UNSUPPORTED_SINGLE_FACT_LEADS = (
    "원자료를 확인한 결과,", "제공 자료 기준으로,", "센터 원자료의 확인 범위에서는,", "상단 원자료 학년 항목을 보면,",
    "원자료의 학년 칸에서는,", "제공된 항목만 대조하면,", "센터 원자료상,", "원자료에 적힌 범위에서는,",
    "원자료 학년 정보 행을 확인하면,", "원문 상태와 제공 자료를 대조하면,", "제공 범위를 넘지 않으면,", "원자료상 학년 항목에서는,",
    "상단 원자료를 기준으로,", "센터의 제공 학년 칸에는,", "원자료 사실만 보면,", "확인 가능한 제공 자료 안에서는,",
)
UNSUPPORTED_SINGLE_FAQ_FACT_LEADS = (
    "학년 질문의 원자료를 확인한 결과,", "학년 문답의 원자료 기준으로,", "질문에 답할 센터 원자료 범위에서는,", "학년 질문과 상단 원자료를 맞추면,",
    "학년 문답에서 원자료 칸을 보면,", "질문의 원자료 제공 항목만 대조하면,", "학년 질문에 대한 센터 원자료상,", "질문과 원자료 범위를 맞추면,",
    "학년 문답의 원자료 정보 행을 확인하면,", "질문 앞에서 원자료 상태를 대조하면,", "학년 질문에서 원자료 범위를 넘지 않으면,", "문답의 원자료상 학년 항목에서는,",
    "학년 질문과 상단 원자료를 기준으로,", "문답에 쓸 센터 원자료 학년 칸에는,", "질문에 답할 원자료 사실만 보면,", "학년 문답의 원자료 안에서는,",
)
UNSUPPORTED_SINGLE_FACT_ENDINGS = (
    "기재되어 있지 않습니다", "원자료에서 확인되지 않습니다", "미기재 상태입니다", "공란으로 남아 있습니다",
    "제공되지 않은 상태입니다", "확인값 없이 비어 있습니다", "학년 정보로 기재되어 있지 않습니다", "원문에서 확인되지 않습니다",
    "기재된 내용이 없습니다", "제공 자료에 없습니다", "확인 가능한 값이 없습니다", "미기재로 표시됩니다",
    "원자료상 공란입니다", "기재되어 있지 않은 항목입니다", "제공 목록에서 확인되지 않습니다", "비어 있는 상태입니다",
)
GRADE_GUIDANCE_LEADS = (
    "이 학년 표기를 사용할 때", "상담 메모에 학년을 옮길 때", "가능 학년 자료를 읽은 뒤",
    "학년 범위를 비교할 때", "등록 판단에 학년값을 쓸 때", "과목별 학년을 확인한 다음",
    "제공 학년값을 기록하면서", "학년 목록과 상담 일정을 나눌 때", "학년 항목을 검토한 뒤",
    "상담 질문으로 바꿀 때", "학년 표기의 범위를 지키려면", "확인된 학년값을 기준으로 삼되",
    "센터 답변과 대조할 때", "학년 정보를 계획에 넣기 전에", "제공 목록을 확인한 다음", "학년 자료만으로 결정하지 말고",
)
GRADE_GUIDANCE_ENDINGS = (
    "실제 개설 여부·요일·시간을 답변 날짜와 함께 따로 확인하세요",
    "교재·반 구성·시작일은 별도 질문으로 남겨 두세요",
    "수업 일정과 현재 신청 가능 여부를 상담에서 다시 물어보세요",
    "표에 없는 시간표와 비용은 확인 전까지 미정으로 두세요",
    "실제 적용 학년과 시작 시점을 한 줄씩 대조하세요",
    "현재 개설 상태와 수업 조건을 원자료 사실과 구분하세요",
    "요일·횟수·교재 답변에는 확인 날짜를 붙여 두세요",
    "학년값이 실제 반 편성을 뜻한다고 단정하지 마세요",
    "개설 과목과 대상 학년의 최신 답변을 별도로 받으세요",
    "시간표와 교재 정보는 센터 확인 뒤에만 기록하세요",
    "학년 목록 밖의 내용은 상담 답변으로 보완하세요",
    "신청 가능 여부와 운영 시간은 같은 뜻으로 보지 마세요",
    "과목별 실제 일정은 등록 전에 한 번 더 대조하세요",
    "자료에 없는 수업 조건은 사실처럼 확장하지 마세요",
    "학년·시간·비용을 서로 다른 확인 항목으로 적으세요",
    "현재 답변과 원문 학년값을 한 문장에 섞지 마세요",
)

UNSUPPORTED_GRADE_GUIDANCE_LEADS = (
    "미기재 학년 항목을 상담 질문으로 바꿀 때는",
    "원자료 공란을 기록한 뒤에는",
    "대상 학년 정보가 비어 있는 상태에서는",
    "확인되지 않은 학년 범위를 다룰 때는",
    "학년 정보의 미기재 상태를 보존하면서",
    "상담 전 미기재 항목을 정리할 때는",
    "학년 공란의 답변을 받기 전에는",
    "학년 공란의 뜻을 해석할 때는",
    "원자료에 없는 학년 정보를 다룰 때는",
    "현재 미기재 대상 학년을 문의할 때는",
    "미기재 사실과 상담 답변을 나누려면",
    "등록 전에 학년 공란을 점검할 때는",
    "학년 공란을 상담표에 표시할 때는",
    "공란 상태를 메모할 때는",
    "대상 학년 공란 질문을 준비하면서",
    "원문에 없는 학년 범위를 기록할 때는",
)
UNSUPPORTED_GRADE_GUIDANCE_ENDINGS = (
    "실제 개설 여부·대상 학년·요일·시간을 각각 물어보세요",
    "답변 전에는 어떤 학년도 제공 대상으로 단정하지 마세요",
    "현재 대상 학년과 시작일을 상담에서 별도로 확인하세요",
    "공란 자체를 사실로 두고 운영 조건은 질문으로 남기세요",
    "다른 과목이나 센터의 학년 정보를 대신 쓰지 마세요",
    "개설 여부와 대상 학년 답변에 확인 날짜를 붙이세요",
    "상담 답변을 받기 전까지 대상 범위를 비워 두세요",
    "수업 시간·교재·비용도 미확인 항목으로 나누어 두세요",
    "현재 운영 범위는 담당자의 답변 뒤에만 기록하세요",
    "미기재 상태를 수강 불가나 개설로 해석하지 마세요",
    "대상 학년·일정·교재를 서로 다른 질문으로 만드세요",
    "원문 공란과 상담에서 받은 내용을 별도 칸에 적으세요",
    "확인되지 않은 학년이나 반 편성을 추정하지 마세요",
    "답변 주체와 적용 시점을 함께 받아 두세요",
    "실제 신청 가능 여부는 상담 결과로만 판단하세요",
    "대상 학년 답변이 없으면 사실란을 미확인으로 유지하세요",
)


def grades_for(context: Context, subject: str) -> tuple[str, ...]:
    return phase1.requested_grades(context.center, context.config, subject)


def grade_list(values: Sequence[str]) -> str:
    return "·".join(values)


def grade_fact_core(context: Context, faq: bool = False) -> str:
    config_subject = str(context.config["subject"])
    if context.unsupported:
        if config_subject == "영수":
            english = grade_list(context.center.grades["영어"]) or "원자료 미기재"
            math = grade_list(context.center.grades["수학"]) or "원자료 미기재"
            if faq:
                return (
                    f"이 질문에서 영어 학년값은 {english}이고 수학 학년값은 {math}입니다. "
                    "영어·수학을 함께 보는 대상 학년은 원자료에서 확인되지 않습니다."
                )
            return (
                f"센터 자료에서 영어 학년값은 {english}이고 수학 학년값은 {math}입니다. "
                "영어·수학을 함께 보는 대상 학년은 원자료에 기재되어 있지 않습니다."
            )
        fact_lead, fact_end = pick_pair(
            context,
            "unsupported-faq-single-fact" if faq else "unsupported-article-single-fact",
            UNSUPPORTED_SINGLE_FAQ_FACT_LEADS if faq else UNSUPPORTED_SINGLE_FACT_LEADS,
            UNSUPPORTED_SINGLE_FACT_ENDINGS,
        )
        return f"{fact_lead} {context.scope} 대상 학년은 {fact_end}."
    lead = pick(
        context,
        f"{'faq-' if faq else ''}grade-fact-lead",
        FAQ_GRADE_FACT_LEADS if faq else GRADE_FACT_LEADS,
    )
    ending_pool = GRADE_FACT_ENDINGS
    ending = pick_avoiding_stems(
        context,
        f"{'faq-' if faq else ''}grade-fact-ending",
        ending_pool,
        lead,
        ECHO_STEMS,
    )
    if config_subject == "영수":
        english = grade_list(context.center.grades["영어"]) or "원자료 미기재"
        math = grade_list(context.center.grades["수학"]) or "원자료 미기재"
        return (
            f"{lead} 영어 학년은 {english}, 수학 학년은 {math}이며, "
            f"두 값이 {ending}."
        )
    values = grades_for(context, config_subject)
    if values:
        return (
            f"{lead} {context.scope} 학년값은 {grade_list(values)}이며, "
            f"이 값이 {ending}."
        )
    return f"{lead} {context.scope} 가능 학년이 {ending}."


def render_grade_paragraphs(context: Context) -> list[str]:
    fact_attrs = (
        f' class="academy-source-unconfirmed-note" {SOURCE_MARKER}'
        if context.unsupported
        else ""
    )
    fact = f"<p{fact_attrs}>{escape(grade_fact_core(context))}</p>"
    if context.unsupported:
        guidance_lead, guidance_end = pick_pair(
            context,
            "unsupported-grade-guidance",
            UNSUPPORTED_GRADE_GUIDANCE_LEADS,
            UNSUPPORTED_GRADE_GUIDANCE_ENDINGS,
        )
    else:
        guidance_lead, guidance_end = pick_pair(
            context, "grade-guidance", GRADE_GUIDANCE_LEADS, GRADE_GUIDANCE_ENDINGS
        )
    guidance = f"{guidance_lead} {guidance_end}."
    return [fact, f"<p>{escape(guidance)}</p>"]


SCHOOL_LEADS = (
    "학교 참고 목록을 보면", "원자료 학교명 항목을 보면", "상단 학교 참고 항목을 확인하면", "제공된 학교 이름을 대조하면",
    "학교 자료 칸을 확인하면", "센터 자료의 학교 목록을 보면", "참고 학교 원문을 읽으면", "학교명 사실 항목을 보면",
    "제공 목록을 그대로 읽으면", "학교 참고 행을 대조하면", "원자료 학교명 범위를 보면", "상단 참고 목록을 확인하면",
    "학교 이름 자료를 대조하면", "센터 제공 학교 항목을 보면", "현재 표시된 참고 학교를 보면", "학교명 원문을 따르면",
)
FAQ_SCHOOL_LEADS = (
    "학교 질문에 원자료로 답하면", "학교명 문답의 출처를 확인하면",
    "학교 관련 사실 범위를 보면", "학교 이름의 자료 상태를 살피면",
    "상담 전 학교 사실을 다시 읽으면", "학교명에 관한 자료상 답을 확인하면",
    "학교 질문을 원문 사실과 맞추면", "학교 이름의 제공 상태를 보면",
    "학교 문답의 확인 범위를 살피면", "학교명 답변의 출처를 대조하면",
    "학교 질문을 사실과 의견으로 나누면", "학교 이름을 답변에 옮기기 전에는",
    "학교 정보의 자료 범위를 확인하면", "학교 질문에 원문 기준으로 답하면",
    "학교명 출처를 별도 항목으로 보면", "학교 관련 참고 정보의 한계를 보면",
)
SCHOOL_ENDINGS = (
    "기재되어 있습니다", "원문 이름으로 확인됩니다", "참고값으로 적혀 있습니다", "자료에 표시되어 있습니다",
    "학교명 목록으로 제시됩니다", "제공 항목에 남아 있습니다", "상단 사실값과 같습니다", "원자료 순서로 확인됩니다",
    "참고 정보에 해당합니다", "자료의 학교값입니다", "확인 가능한 이름입니다", "목록 항목으로 들어 있습니다",
    "변경 없이 표시됩니다", "센터 참고값으로 제시됩니다", "원문에서 볼 수 있습니다", "사실 항목으로 기록됩니다",
)
NO_SCHOOL_ENDINGS = (
    "기재된 학교명이 없습니다", "학교명이 원자료 미기재 상태입니다", "학교명 목록이 비어 있습니다", "확인할 학교 이름이 없습니다",
    "제공 학교 목록이 없습니다", "학교값이 적혀 있지 않습니다", "학교명이 자료에서 확인되지 않습니다", "학교 항목이 공란으로 남아 있습니다",
    "학교 참고값이 없습니다", "원문에 학교 이름이 없습니다", "기재된 학교 목록이 없습니다", "확인 가능한 학교명이 없습니다",
    "학교값이 미기재로 표시됩니다", "학교 항목이 비어 있습니다", "제공된 학교 이름이 없습니다", "학교명은 상담 전 확인이 필요한 상태입니다",
)
SCHOOL_GUIDANCE_LEADS = (
    "학교명을 상담에 활용할 때는", "참고 목록을 확인한 뒤에는", "이번 학기 자료와 대조할 때는", "학교 이름을 기록하면서",
    "해당 목록을 읽는 과정에서는", "학기별 자료를 준비할 때는", "제공 학교명을 기준으로 삼되", "학교 항목을 검토한 다음에는",
    "상담 질문을 만들 때는", "학교별 내용을 비교하기 전에는", "참고 학교와 적용 범위를 나눌 때는", "학교 이름의 뜻을 과장하지 않으려면",
    "학교 자료 반영 여부를 물을 때는", "이번 평가 범위를 살펴보면서", "학교명 목록을 본 다음", "참고값을 학습 계획에 넣기 전에는",
)
SCHOOL_GUIDANCE_ENDINGS = (
    "이번 학기 범위표와 배부 자료를 별도로 확인하세요", "학교명만으로 현재 시험 유형을 단정하지 마세요",
    "최근 공지와 실제 사용 교재를 함께 제시하세요", "현재 학년의 자료 반영 여부를 상담에서 물어보세요",
    "과거 경향과 이번 평가 범위를 분리해 기록하세요", "자료 제출 시점과 반영 방법을 구체적으로 확인하세요",
    "학교 이름을 수강 가능 보장으로 해석하지 마세요", "현재 시험 자료를 언제 확인하는지 질문하세요",
    "이번 학기 범위가 확정된 날짜를 함께 적으세요", "학교 자료의 종류와 제출 방법을 따로 물어보세요",
    "최근 평가지를 기준으로 답변을 다시 확인하세요", "학교명과 실제 수업 가능 범위를 같은 뜻으로 보지 마세요",
    "공지 범위·교과서·배부물을 서로 구분해 준비하세요", "현재 자료가 없는 내용은 확인 전까지 비워 두세요",
    "학교별 적용 여부에 답변 날짜를 붙이세요", "학교 참고값 밖의 내용은 추정하지 마세요",
)
NO_SCHOOL_GUIDANCE_LEADS = (
    "학교 참고값이 비어 있을 때는", "원자료 학교명이 없으므로", "제공 목록이 미기재된 경우에는", "학교 이름이 확인되지 않으면",
    "상단 학교 항목이 공란일 때는", "학교명 없이 상담을 준비할 때는", "확인된 학교 목록이 없으므로", "미기재 학교 항목을 다룰 때는",
    "학교 정보가 없는 상태에서는", "학교 참고값을 새로 확인하려면", "원문에 학교명이 없을 때는", "학교 목록을 추정하지 않으려면",
    "학교 정보를 새로 확인할 때는", "학교명 공란을 기록할 때는", "제공 이름이 없는 경우에는", "학교 자료 질문을 만들 때는",
)
NO_SCHOOL_GUIDANCE_ENDINGS = (
    "특정 학교와 연결해 해석하지 마세요", "현재 학교 자료는 상담에서 직접 확인하세요", "임의의 학교명을 목록에 추가하지 마세요", "학교별 자료 반영 여부를 별도 질문으로 남기세요",
    "적용 범위를 확인 전 상태로 남겨 두세요", "학교 공지와 배부 자료를 직접 준비해 대조하세요", "다른 센터의 학교 목록을 대신 사용하지 마세요", "현재 학교와 학년에는 상담 답변의 확인일을 붙이세요",
    "학교 참고값 부재를 수강 불가로 단정하지 마세요", "미기재 상태와 현재 자료 반영 여부를 구분하세요", "학교별 교재·범위는 최신 자료로 따로 확인하세요", "학교명 없이 시험 유형이나 범위를 추정하지 마세요",
    "상담에서 학교·학년·자료 종류를 각각 물어보세요", "학교 목록은 답변을 받기 전까지 미확인으로 두세요", "원자료 공란에 임의의 이름을 채우지 마세요", "현재 학교 자료의 적용 여부와 답변 날짜를 기록하세요",
)
SCHOOL_REFERENCE_LEADS = (
    "학교명 원문을 확인할 때는", "제공 학교 참고값을 볼 때는", "학교 이름 목록을 읽을 때는", "학교 참고 항목을 확인할 때는",
    "학교 참고 자료를 읽을 때는", "제공 학교명을 보려면", "학교 이름의 출처를 확인하려면", "참고 학교 표기를 활용할 때는",
    "학교 참고값을 메모에 적을 때는", "제공 목록에 있는 학교명을 확인할 때는", "학교 자료 범위를 확인하려면", "참고 이름을 볼 때는",
    "학교명을 사실 항목으로 둘 때는", "센터가 제공한 학교 항목을 읽을 때는", "학교 참고 행을 대조하려면", "학교 이름 자료를 확인할 때는",
)
SCHOOL_REFERENCE_ENDS = (
    "옆의 핵심정보 태그에서 원문 순서대로 확인하세요", "상단 ‘학교 참고’ 항목의 이름을 확인하세요", "핵심정보의 제공 학교 목록을 확인하세요", "상단 자료에서 이름만 확인하고 적용 범위는 따로 물어보세요",
    "옆의 학교 참고 행을 출처로 삼아 확인하세요", "태그의 원문 이름을 현재 수업 범위로 확장하지 마세요", "핵심정보에 보이는 이름값과 상담 답변을 나누어 적으세요", "상단 학교 태그를 보고 추가 학교를 임의로 더하지 마세요",
    "상단에는 제공 이름을, 상담란에는 현재 자료 반영 여부를 적으세요", "옆의 학교 정보에서 원문 표기를 변경 없이 확인하세요", "상단 참고 목록을 수강 가능 보장으로 해석하지 마세요", "핵심정보의 학교명과 이번 학기 자료를 별도로 대조하세요",
    "옆의 태그에서 확인하고 학교명만으로 시험 유형을 단정하지 마세요", "상단 학교 참고값을 원자료 범위로만 사용하세요", "핵심정보의 이름 목록과 현재 학교 공지를 구분해 보세요", "제공 학교명은 옆의 태그에서, 최신 적용 범위는 상담에서 확인하세요",
)


def school_summary(schools: Sequence[str]) -> str:
    if not schools:
        return ""
    if len(schools) <= 3:
        return "·".join(schools)
    return f"{'·'.join(schools[:3])} 외 {len(schools) - 3}개 이름"


def render_school_paragraphs(context: Context) -> list[str]:
    if context.schools:
        fact_lead, fact_end = pick_pair(
            context, "school-reference", SCHOOL_REFERENCE_LEADS, SCHOOL_REFERENCE_ENDS
        )
        guidance_lead, guidance_end = pick_pair(
            context, "school-guidance", SCHOOL_GUIDANCE_LEADS, SCHOOL_GUIDANCE_ENDINGS
        )
    else:
        fact_lead = pick(context, "school-reference-lead", SCHOOL_LEADS)
        fact_end = pick_avoiding_stems(
            context, "school-none-end", NO_SCHOOL_ENDINGS, fact_lead, ECHO_STEMS
        )
        guidance_lead, guidance_end = pick_pair(
            context,
            "school-guidance",
            NO_SCHOOL_GUIDANCE_LEADS,
            NO_SCHOOL_GUIDANCE_ENDINGS,
        )
    fact = f"{fact_lead} {fact_end}."
    guidance = f"{guidance_lead} {guidance_end}."
    return [f"<p>{escape(fact)}</p>", f"<p>{escape(guidance)}</p>"]


ADDRESS_LEADS = (
    "원자료의 제공 주소는", "센터 주소 항목에는", "방문 기준으로 적힌 주소는", "상단에 확인된 주소는",
    "물리적 센터 위치는", "제공 자료의 주소값은", "센터 위치 사실에는", "주소 원문에는",
    "방문 전에 대조할 주소는", "센터를 찾을 때 쓰는 주소는", "자료상 센터 주소는", "확인된 물리 주소는",
    "서비스 지역과 별도로 적힌 주소는", "센터 기준 주소값은", "원문에서 확인되는 주소는", "상단 주소 정보는",
)
ADDRESS_ENDINGS = (
    "원문에 기재되어 있습니다", "자료에 기재되어 있습니다", "상단에서 확인됩니다", "원문 항목에 남아 있습니다",
    "주소 자료에 표시되어 있습니다", "물리 위치 항목에 들어 있습니다", "제공 자료에서 확인됩니다", "사실값으로 기록되어 있습니다",
    "센터 자료에 적혀 있습니다", "위치 항목에 남아 있습니다", "주소 값으로 확인됩니다", "원자료에 제시됩니다",
    "상단 핵심정보에서 확인할 수 있습니다", "변경 없이 표시되어 있습니다", "주소 자료에 기록되어 있습니다", "제공 주소 항목과 같게 확인됩니다",
)
GUIDE_CONTEXT_LEADS = (
    "위치 안내 원문을 사용할 때는", "제공된 위치 문구를 확인한 뒤에는", "방문 동선을 정하기 전에는", "위치 안내값을 읽을 때는",
    "방문 정보를 검토하면서", "제공된 안내를 참고하면서", "주소와 위치 안내를 대조하면서", "방문 메모를 만들 때는",
    "위치 문구를 일정에 넣기 전에는", "출발 준비 단계에서는", "건물 입구를 확인할 때는", "제공 위치 안내를 참고하되",
    "방문 경로를 준비하면서", "위치 사실을 기록한 다음", "센터 위치를 다시 볼 때는", "원문 위치 항목을 확인하고",
)
GUIDE_CONTEXT_ENDINGS = (
    "현재도 같은 안내가 적용되는지 확인하세요", "주소와 건물 표기가 맞는지 다시 물어보세요",
    "출발 시점의 실제 경로를 별도로 점검하세요", "입구와 층 정보의 최신 여부를 확인하세요",
    "현재 출입 안내가 달라졌는지 상담에서 확인하세요", "서비스 지역명과 물리 주소를 섞지 마세요",
    "현장 안내가 달라졌는지 확인한 뒤 이동하세요", "주소 원문과 별개의 사실로 기록하세요",
    "현재 출입 방법을 센터에 다시 확인하세요", "위치 문구를 예상 소요 시간으로 확장하지 마세요",
    "제공 문구 밖의 거리나 경로를 추정하지 마세요", "방문 당일의 안내를 우선 확인하세요",
    "건물명과 층 표기를 다시 대조하세요", "출발 전 최신 위치 답변을 받아 두세요",
    "안내 문구는 원자료 범위로만 참고하세요", "지도 경로는 별도 수단으로 확인하세요",
)
NO_GUIDE_CONTEXT_LEADS = (
    "방문 정보를 확인할 때는",
    "방문 경로를 준비하면서",
    "건물 입구를 찾기 전에는",
    "주소 밖의 동선을 정하기 전에는",
    "방문 메모의 위치 항목에는",
    "출발 준비 단계에서는",
    "센터 위치를 다시 살펴볼 때는",
    "물리 주소를 기록한 다음에는",
    "방문 날짜를 정하기 전에는",
    "주소 사실을 상담표에 옮긴 뒤에는",
    "현재 출입 방법을 정리할 때는",
    "센터를 찾아갈 계획을 세우면서",
    "방문 전 위치 질문을 만들 때는",
    "주소와 방문 답변을 나누어 적을 때는",
    "이동 계획을 세우기 전에는",
    "물리 주소의 다음 확인 항목에는",
)
NO_GUIDE_ENDINGS = (
    "원자료 위치 안내가 비어 있어 방문 방법은 상담으로 확인하세요",
    "별도 위치 문구가 없으므로 주소 밖의 경로는 추정하지 마세요",
    "위치 안내 항목이 미기재라서 입구와 층은 방문 전에 물어보세요",
    "제공된 위치 문구가 없어 실제 이동 경로를 따로 확인해야 합니다",
    "주소 외 방문 안내가 비어 있으므로 최신 출입 방법을 확인하세요",
    "원자료에 위치 설명이 없어 건물 입구는 상담에서 다시 물어보세요",
    "위치 안내값이 없으므로 거리나 소요 시간을 임의로 적지 마세요",
    "별도 동선 정보가 없어 방문 당일 안내를 확인하는 편이 안전합니다",
    "원문 위치 칸이 비어 있으므로 주소와 입구를 각각 대조하세요",
    "제공 위치 안내가 없어서 출발 전 센터 답변이 필요합니다",
    "위치 설명이 미기재라 실제 경로는 확인 전까지 비워 두세요",
    "원자료에는 주소만 있고 위치 안내는 없어 현재 방문 방법을 확인해야 합니다",
    "위치 문구가 제공되지 않아 건물명과 층을 다시 점검하세요",
    "별도 위치안내가 없으므로 서비스 지역과 센터 주소만 구분해 두세요",
    "위치 안내 항목이 비어 있어 현재 출입 경로를 물어봐야 합니다",
    "주소 이외의 동선 정보는 원자료에 없어 별도 확인이 필요합니다",
)
ADDRESS_REFERENCE_LEADS = (
    "제공 주소 원문을 확인할 때는", "센터 주소값을 볼 때는", "방문 기준 주소를 확인할 때는", "물리적 센터 주소를 기록하려면",
    "주소 사실의 출처를 확인하려면", "센터 위치의 주소 항목을 볼 때는", "원자료 주소를 보려면", "방문 전에 주소를 적을 때는",
    "서비스 지역과 주소를 구분할 때는", "센터 주소를 메모에 옮길 때는", "제공 주소의 원문 표기를 확인하려면", "상단 주소 사실을 활용할 때는",
    "주소값을 다시 대조하려면", "물리 위치의 주소 원문을 볼 때는", "센터를 찾는 기준 주소를 확인하려면", "제공 주소 항목을 읽을 때는",
)
ADDRESS_REFERENCE_ENDS = (
    "옆의 핵심정보 ‘제공 주소’ 행에서 그대로 확인하세요", "상단 제공 주소 칸의 원문 표기를 확인하세요", "핵심정보의 주소 행을 출처로 삼아 확인하세요", "상단 주소값을 서비스 지역명과 구분해 기록하세요",
    "옆의 제공 주소 항목에서 확인하고 임의로 공식 표기로 바꾸지 마세요", "핵심정보에 적힌 원문 값과 방문 당일 안내를 따로 대조하세요", "상단 주소 행에서 읽고 추가 경로나 거리를 추정하지 마세요", "제공 주소 칸의 사실값을 변경 없이 메모에 옮기세요",
    "옆의 주소 정보에, 현재 출입 방법은 상담 답변에 나누어 적으세요", "상단 핵심정보에서 확인한 뒤 위치 안내와 별도 항목으로 두세요", "제공 주소 행을 원문 출처로 두고 서비스 지역과 섞지 마세요", "옆의 주소값을 확인하고 건물·층의 최신 여부는 다시 물어보세요",
    "상단 제공 주소에서 확인하되 이동 시간으로 확장하지 마세요", "핵심정보 주소 행의 원문을 보존하고 현재 경로는 따로 확인하세요", "옆의 제공 주소 항목을 기준으로 삼고 출발 전 안내를 다시 보세요", "상단 주소 원문과 실제 출입 정보를 같은 사실로 단정하지 마세요",
    "상단 주소 행의 원문을 그대로 옮기고 이동 정보는 따로 물어보세요", "핵심정보 주소값을 쓰고 당일 출입 정보는 별도 답변으로 받으세요",
    "제공 주소 칸의 값만 사실로 두고 경로는 상담에서 물어보세요", "원문 주소값과 당일 동선을 서로 다른 칸에 적으세요",
    "상단의 물리 주소를 쓰되 거리나 소요 시간은 더하지 마세요", "제공된 주소값을 보존하고 건물 출입 정보는 별도로 물어보세요",
    "핵심정보의 물리 주소만 옮기고 서비스 지역명은 따로 두세요", "원자료 주소값과 방문 당일 답변을 서로 다른 항목에 두세요",
)


def render_location_paragraphs(context: Context) -> list[str]:
    address_lead, address_end = pick_pair(
        context, "address-reference", ADDRESS_REFERENCE_LEADS, ADDRESS_REFERENCE_ENDS
    )
    address = f"{address_lead} {address_end}."
    result = [f"<p>{escape(address)}</p>"]
    if context.extra.location_guide:
        result.append(
            f'<p class="academy-location-guide" {LOCATION_GUIDE_ATTR}>'
            f'{escape(context.extra.location_guide)}</p>'
        )
        guide_lead, guide_end = pick_pair(
            context, "guide-context", GUIDE_CONTEXT_LEADS, GUIDE_CONTEXT_ENDINGS
        )
    else:
        guide_lead = pick(context, "guide-none-lead", NO_GUIDE_CONTEXT_LEADS)
        guide_end = pick_avoiding_stems(
            context, "guide-none-end", NO_GUIDE_ENDINGS, guide_lead, ECHO_STEMS
        )
    context_sentence = f"{guide_lead} {guide_end}."
    if context.extra.location_guide:
        result.append(f"<p>{escape(context_sentence)}</p>")
    else:
        result.append(
            '<p class="academy-location-guide-status academy-source-unconfirmed-note" '
            f'{MISSING_LOCATION_GUIDE_MARKER}>{escape(context_sentence)}</p>'
        )
    return result


FEE_LEADS = (
    "교습비 자료를 확인할 때는", "비용 항목을 상담에 옮길 때는", "상단 교습비 정보를 볼 때는", "등록 비용을 비교하기 전에는",
    "센터 공통 비용 자료를 볼 때", "교습비 링크와 상담 내용을 나눌 때는", "비용 사실을 기록하면서", "비용 자료의 최신성을 볼 때는",
    "상담 메모의 비용 칸에는", "교습비 제공 범위를 읽을 때는", "등록 판단의 비용 단계에서", "상단 비용 자료를 기준으로 삼되",
    "교습비 원문을 확인한 다음", "비용과 포함 항목을 나눌 때는", "센터별 교습비 자료에는", "비용 질문을 준비할 때는",
)
FEE_LINK_ENDINGS = (
    "센터가 제공한 공통 링크와 최신 상담 금액을 구분하세요",
    "링크의 자료일과 현재 적용 금액을 함께 확인하세요",
    "표시 금액·포함 항목·적용 시점을 각각 물어보세요",
    "공통 자료만으로 현재 총액을 단정하지 마세요",
    "교재비와 별도 비용의 포함 여부를 다시 확인하세요",
    "링크 존재를 최신 금액 보장으로 해석하지 마세요",
    "적용 기간과 변경 기준에 답변 날짜를 붙이세요",
    "현재 금액은 등록 전에 센터 답변으로 대조하세요",
    "수업 횟수와 비용을 같은 표에 따로 적으세요",
    "공통 링크의 범위 밖 조건은 별도 질문으로 남기세요",
    "금액과 수업 조건의 확인 시점을 나누어 기록하세요",
    "센터 공통 자료와 개인별 상담 내용을 섞지 마세요",
    "최신 금액과 포함 항목을 문서로 확인하세요",
    "교습비 링크를 출처로만 두고 적용값은 다시 물어보세요",
    "비용 답변의 담당자와 날짜도 함께 남겨 두세요",
    "공통 자료 뒤에 현재 적용 여부를 추가로 적으세요",
)
FEE_BLANK_ENDINGS = (
    "원자료에 교습비 링크가 없어 최신 금액과 포함 항목을 상담에서 확인하세요",
    "제공된 비용 링크가 없으므로 금액을 추정하지 말고 직접 물어보세요",
    "교습비 자료가 미기재라 적용 금액과 별도 비용을 확인해야 합니다",
    "원문 비용 링크가 비어 있어 등록 전에 서면 답변을 받아 두세요",
    "센터 교습비 링크가 없어 횟수·금액·포함 항목을 각각 확인하세요",
    "제공 비용 자료가 없으므로 다른 센터 금액을 대신 쓰지 마세요",
    "교습비 링크 미기재 상태라 현재 적용값을 상담에서 확인하세요",
    "원자료만으로 비용을 알 수 없어 확인 날짜가 있는 답변이 필요합니다",
    "비용 링크가 제공되지 않아 교재비와 별도 항목도 함께 물어보세요",
    "교습비 원문이 비어 있으므로 금액과 적용 기간을 따로 확인하세요",
    "자료에 비용 링크가 없어 현재 총액을 사실처럼 적을 수 없습니다",
    "교습비 제공 항목이 없으므로 상담 답변 전에는 금액을 비워 두세요",
    "비용 자료 미기재로 최신 금액 확인이 먼저 필요합니다",
    "센터 공통 링크가 없어 실제 교습비를 별도 확인해야 합니다",
    "원자료의 비용 칸이 비어 있어 등록 조건과 함께 물어봐야 합니다",
    "제공 링크가 없으므로 교습비와 추가 비용을 직접 대조하세요",
)
CHECK_LEADS = (
    "최종 상담 메모에는", "등록 판단을 마치기 전에는", "답변을 비교할 때는", "상담 내용을 한 장에 적을 때는",
    "확인 질문의 마지막에는", "사실값과 답변을 대조하면서", "등록 전 체크에는", "상담 기록을 정리한 뒤에는",
    "등록 조건을 살펴보려면", "결정을 내리기 앞서", "답변 누락을 막으려면", "상담 결과를 다시 볼 때는",
    "센터에 문의할 때는", "사실과 계획을 나누려면", "상담 날짜를 남기면서", "마지막 확인 단계에서는",
)
CHECK_ENDINGS = (
    "개설 과목·대상 학년·요일·시간·교재를 서로 다른 칸에 적으세요",
    "수업 횟수·시작일·비용·변경 기준을 항목별로 확인하세요",
    "원자료 사실과 상담 답변에 각각 출처와 날짜를 붙이세요",
    "확인되지 않은 정원·보강·과제 방식은 미정으로 남겨 두세요",
    "담당자·답변일·적용 시점을 함께 기록하세요",
    "학년·학교·주소와 실제 수업 조건을 분리해 적으세요",
    "교재·과제·피드백·결석 처리를 차례로 물어보세요",
    "가능 여부와 세부 일정을 같은 뜻으로 묶지 마세요",
    "주소 확인 뒤 실제 요일과 종료 시각을 별도로 대조하세요",
    "학교 참고값과 현재 시험 자료의 반영 범위를 나누세요",
    "비용 답변과 수업 조건이 같은 날짜 기준인지 확인하세요",
    "답을 받지 못한 항목은 추정하지 말고 재확인 날짜를 적으세요",
    "각 질문의 답변 주체와 변경 가능성을 함께 남기세요",
    "현재 개설 상태부터 비용까지 정해진 순서로 확인하세요",
    "원문에 없는 내용은 상담 확인 전까지 사실란에 넣지 마세요",
    "수업 조건의 최신 여부를 마지막으로 한 번 더 확인하세요",
)


def render_consult_paragraphs(context: Context) -> list[str]:
    fee_lead = pick(context, "fee-lead", FEE_LEADS)
    fee_ending = pick_avoiding_stems(
        context,
        "fee-end",
        FEE_LINK_ENDINGS if context.center.tuition_url else FEE_BLANK_ENDINGS,
        fee_lead,
        ECHO_STEMS,
    )
    fee = f"{fee_lead} {fee_ending}."
    check_lead, check_end = pick_pair(
        context, "check", CHECK_LEADS, CHECK_ENDINGS
    )
    checklist = f"{check_lead} {check_end}."
    return [f"<p>{escape(fee)}</p>", f"<p>{escape(checklist)}</p>"]


def render_section(context: Context, slot: str, paragraphs: Sequence[str]) -> str:
    return (
        '<section class="academy-prose-section">'
        f"<h2>{escape(heading(context, slot))}</h2>"
        + "".join(paragraphs)
        + "</section>"
    )


def render_article(context: Context) -> tuple[str, list[str]]:
    sections = (
        ("identity", render_identity_paragraphs(context)),
        ("grades", render_grade_paragraphs(context)),
        ("schools", render_school_paragraphs(context)),
        ("location", render_location_paragraphs(context)),
        ("consult", render_consult_paragraphs(context)),
    )
    headings = [heading(context, slot) for slot, _ in sections]
    attrs = (
        f'class="academy-main-article" {SOURCE_MARKER}'
        if context.unsupported
        else 'class="academy-main-article"'
    )
    body = render_intro(context) + "".join(
        render_section(context, slot, paragraphs) for slot, paragraphs in sections
    )
    return f"<article {attrs}>{body}</article>", headings


QUESTION_LEADS = (
    "제공 항목을 볼 때", "상담을 준비하면서", "등록 전에", "센터 자료를 읽을 때",
    "상담 내용을 기록하기 앞서", "제공 항목을 검토하면서", "사실과 질문을 나눌 때", "상담 메모를 만들 때",
    "제공 내용을 대조하면서", "확인 순서를 정할 때", "자료의 범위를 지키려면", "센터에 문의하기 전에",
    "등록 조건을 비교할 때", "원문과 상담 내용을 구분하면서", "확인 날짜를 남기려면", "상담에서 먼저",
)
QUESTION_QUALIFIERS = (
    "먼저", "과장 없이", "확인된 범위만 기준으로", "원문 표기대로", "항목을 나누어", "사실값 중심으로",
    "미기재 내용을 구분해", "답변 날짜와 함께", "최신 여부를 고려해", "추정하지 않고", "자료 출처를 붙여",
    "센터 사실과 분리해", "순서를 정해서", "한 항목씩", "현재 기준으로", "상담 답변과 대조해",
)
QUESTION_ENDINGS: dict[str, tuple[str, ...]] = {
    "grades": (
        "가능 학년은 어디까지 확인하면 되나요?", "학년 표기는 어떤 기준으로 읽는 것이 좋나요?",
        "대상 학년과 실제 일정은 어떻게 구분하나요?", "학년 자료에서 무엇을 다시 물어야 하나요?",
        "과목별 학년값은 어디에 적혀 있나요?", "가능 학년 항목의 한계는 무엇인가요?",
        "학년 목록 뒤에 어떤 질문을 더해야 하나요?", "학년 범위는 현재 수업과 같은 뜻인가요?",
        "미기재 학년은 어떻게 확인해야 하나요?", "학년 정보를 등록 판단에 어떻게 쓰나요?",
        "원자료 학년값은 무엇을 뜻하나요?", "학년과 시간표는 어떤 순서로 확인하나요?",
        "과목별 대상 학년은 확인됐나요?", "학년 범위를 상담에서 어떻게 대조하나요?",
        "가능 학년과 개설 여부를 어떻게 나누나요?", "학년 자료의 최신 적용 여부는 어떻게 묻나요?",
    ),
    "schools": (
        "학교 참고 목록은 어디까지 활용하면 되나요?", "학교 이름은 어떤 의미로 읽어야 하나요?",
        "학교명과 현재 시험 자료는 어떻게 구분하나요?", "참고 학교 뒤에 무엇을 확인해야 하나요?",
        "제공 학교명을 수강 가능으로 봐도 되나요?", "학교 자료 반영 범위는 어떻게 물어보나요?",
        "학교 참고 항목은 무엇을 뜻하나요?", "이번 학기 자료와 학교명을 어떻게 대조하나요?",
        "학교 목록에서 단정하면 안 되는 내용은 무엇인가요?", "학교명 외에 어떤 자료를 준비해야 하나요?",
        "참고 학교와 실제 범위를 어떻게 나누나요?", "현재 학교 평가 자료는 언제 확인해야 하나요?",
        "학교 이름만으로 시험 유형을 알 수 있나요?", "학교 참고값은 상담에서 어떻게 쓰나요?",
        "학교별 자료 확인은 어떤 순서가 좋나요?", "제공 학교명은 어디까지 사실인가요?",
    ),
    "location": (
        "센터 주소는 어디에서 확인하나요?", "서비스 지역과 실제 주소는 어떻게 구분하나요?",
        "방문 전 주소에서 무엇을 다시 봐야 하나요?", "주소 원문은 상단 어디에 있나요?",
        "방문 전에 주소의 어떤 사실을 확인해야 하나요?", "제공 주소와 현재 출입 안내를 어떻게 나누나요?",
        "센터 주소와 방문 당일 경로는 어떻게 대조하나요?", "주소 원문과 실제 동선은 어떻게 구분하나요?",
        "센터 위치 사실은 어디에서 보나요?", "건물과 입구 정보는 언제 다시 확인하나요?",
        "물리 주소를 확인한 뒤 어떤 방문 질문을 더 하나요?", "주소와 위치 안내의 최신 여부는 어떻게 확인하나요?",
        "방문 전에 주소 밖의 어떤 경로 답변이 필요한가요?", "센터 주소는 서비스 지역과 어떻게 다른가요?",
        "제공 주소는 어디까지 원문 사실인가요?", "주소와 실제 출입 방법은 어떻게 나누어 기록하나요?",
    ),
    "fee": (
        "교습비 링크는 어디에서 확인하나요?", "센터 공통 비용 자료는 어떻게 읽어야 하나요?",
        "등록 전 금액에서 무엇을 다시 물어보나요?", "교습비 자료와 최신 답변은 어떻게 구분하나요?",
        "비용 링크가 없으면 무엇을 확인해야 하나요?", "교재비와 별도 비용은 언제 묻나요?",
        "현재 적용 금액은 어떻게 확인하나요?", "교습비 자료의 기준일은 어디에 남기나요?",
        "센터 공통 링크만으로 총액을 판단해도 되나요?", "금액 답변에는 어떤 확인 정보를 붙이나요?",
        "교습비와 추가 비용은 어떻게 나누어 기록하나요?", "비용 자료의 최신 여부는 어떻게 대조하나요?",
        "적용액과 포함 항목을 어떤 순서로 물어보나요?", "교습비 링크의 미기재 상태는 무엇을 뜻하나요?",
        "등록 시점의 금액은 언제 다시 확인하나요?", "비용 답변과 공통 자료는 어떻게 구분하나요?",
    ),
    "prepare": (
        "상담 메모에는 어떤 항목을 적으면 좋나요?", "등록 전에 무엇을 순서대로 물어보나요?",
        "답변 날짜와 함께 무엇을 기록해야 하나요?", "상담 준비 자료는 어떤 순서로 정리하나요?",
        "확인되지 않은 내용은 어떻게 남겨 두나요?", "비용과 수업 조건을 어떻게 나누어 묻나요?",
        "센터 답변을 비교하려면 무엇이 필요한가요?", "상담 후 다시 확인할 항목은 무엇인가요?",
        "원자료와 답변을 한 장에 어떻게 정리하나요?", "등록 판단 전에 빠뜨리면 안 될 질문은 무엇인가요?",
        "수업 조건은 어떤 순서로 확인하는 것이 좋나요?", "미기재 항목에는 어떤 질문을 붙이나요?",
        "상담 답변의 최신 여부는 어떻게 기록하나요?", "학년·일정·비용을 어떻게 구분하면 되나요?",
        "첫 문의에서 어떤 사실부터 대조하나요?", "상담 결과를 다시 볼 때 무엇을 확인하나요?",
    ),
}

QUESTION_SLOT_LEADS: dict[str, tuple[str, ...]] = {
    "grades": (
        "과목·학년 자료를 살펴보며", "상단 학년 항목을 기준으로", "원자료와 상담 답변을 나누어", "현재 적용 여부를 확인하려고",
        "대상 범위를 기록하기 전에", "센터 학년값을 검토하며", "제공 사실과 적용 조건을 나누어", "과목별 표기를 대조하며",
        "제공된 학년 목록을 읽고", "등록 조건을 확인하려고", "원문 학년값을 메모하면서", "수업 조건을 별도로 묻기 위해",
        "학년 정보의 출처를 살피며", "센터 답변을 준비할 때", "확인된 학년값을 바탕으로", "과목별 범위를 비교하며",
    ),
    "schools": (
        "상단 사실 항목을 살펴보며", "상단 학교 태그를 기준으로", "제공 범위를 넘지 않도록", "제공 항목의 쓰임을 살피며",
        "제공 학교값을 기록하기 전에", "제공 범위를 검토하며", "학교명과 수업 조건을 구분해", "상단 이름값을 상담에 활용하며",
        "학교 이름의 출처를 확인하고", "이번 학기 자료를 준비하려고", "학교 참고값을 메모하면서", "현재 적용 범위를 별도로 묻기 위해",
        "제공 학교명의 한계를 살피며", "센터 답변을 준비할 때", "확인된 학교값을 바탕으로", "학교 자료를 비교하며",
    ),
    "location": (
        "센터 위치 자료를 살펴보며", "상단 사실값을 기준으로", "서비스 지역과 물리 위치를 나누어", "방문 준비에 필요한 사실을 확인하려고",
        "방문 정보를 기록하기 전에", "핵심정보의 관련 항목을 검토하며", "상단 사실과 방문 답변을 나누어", "위치 항목을 상담에 활용하며",
        "센터 위치의 출처를 확인하고", "방문 메모를 만들며", "물리 위치 사실을 적으면서", "방문과 등록 준비를 함께 하며",
        "위치 안내의 범위를 살피며", "방문 답변을 준비할 때", "확인된 사실값을 바탕으로", "원문 사실을 상담 답변과 대조하며",
    ),
    "fee": (
        "센터 비용 자료를 살펴보며", "상단 교습비 항목을 기준으로", "공통 자료와 현재 금액을 나누어", "등록 전 비용 답변을 준비하며",
        "교습비 정보를 기록하기 전에", "핵심정보의 비용 항목을 검토하며", "제공 자료와 적용액을 구분해", "비용 질문을 항목별로 만들며",
        "교습비 출처를 확인하고", "금액 답변의 기준일을 남기려고", "센터 공통 링크를 검토하면서", "교재비·별도 비용을 묻기 위해",
        "비용 자료의 한계를 살피며", "현재 총액을 확인하려고", "등록 시점의 금액을 대조하며", "공통 교습비와 상담 답변을 나누어",
    ),
    "prepare": (
        "상담 준비표를 만들며", "자료상 사실만 먼저 적고", "확인 순서를 세우려고", "미기재 조건을 질문으로 바꾸며",
        "센터 사실을 먼저 대조한 뒤", "현재 수업 조건을 확인하려고", "상담 답변을 빠짐없이 받기 위해", "확인일이 있는 메모를 만들며",
        "결정표에 쓸 자료를 정리하며", "센터 문의 항목을 정리하며", "사실과 계획을 구분하려고", "비용·일정 질문을 준비하며",
        "원문 범위를 지킨 기록을 만들 때", "수업 조건을 항목별로 나누어", "답변의 적용 시점을 남기려고", "최종 확인표를 검토하며",
    ),
}

UNSUPPORTED_GRADE_QUESTION_LEADS = (
    "미기재 학년 항목을 확인하며", "원자료 공란을 그대로 두고", "학년 공란의 답변을 받기 전에",
    "학년 정보 미기재를 기록하며", "확인되지 않은 대상 범위를 묻기 위해", "학년 공란을 상담 질문으로 바꾸며",
    "원자료에 없는 학년을 추정하지 않고", "미기재 대상 학년을 새로 확인하려고", "미기재 사실과 답변을 나누어",
    "대상 학년 미기재 상태를 기준으로", "공란 학년 항목의 다음 절차를 묻기 위해", "학년 정보가 비어 있음을 확인하고",
    "상담 전 대상 범위를 공란으로 두고", "원문에 기재되어 있지 않은 학년을 다루며", "학년 공란의 확인 순서를 정하며",
    "미기재 대상 학년을 임의로 채우지 않고",
)
UNSUPPORTED_GRADE_QUESTION_ENDINGS = (
    "대상 학년 공란에는 어떤 질문을 남겨야 하나요?", "학년 정보가 미기재이면 무엇을 확인해야 하나요?",
    "원자료에 없는 대상 학년은 언제 기록하나요?", "현재 개설 여부와 대상 학년을 어떻게 따로 묻나요?",
    "학년 공란을 수강 불가로 해석해도 되나요?", "미기재 학년 범위는 상담표에 어떻게 표시하나요?",
    "대상 학년 답변에는 어떤 확인 정보를 붙이나요?", "기재되지 않은 학년을 추정하지 않으려면 어떻게 하나요?",
    "학년 미기재 상태와 현재 답변을 어떻게 구분하나요?", "공란인 대상 학년을 누구에게 확인해야 하나요?",
    "학년 정보가 없을 때 신청 가능 여부는 어떻게 판단하나요?", "원자료 공란 뒤에 어떤 상담 답변이 필요한가요?",
    "미기재 대상 학년과 수업 시간을 어떻게 확인하나요?", "학년 범위가 확인되지 않으면 무엇을 비워 두나요?",
    "대상 학년 미기재를 다른 과목 자료로 채워도 되나요?", "학년 공란의 최신 상태는 언제 다시 물어보나요?",
)

NO_SCHOOL_QUESTION_ENDINGS = (
    "학교 참고값이 비어 있으면 무엇을 확인해야 하나요?", "원자료에 학교명이 없을 때 어떻게 기록하나요?",
    "학교 목록이 미기재된 경우 무엇을 물어보나요?", "제공 학교명이 없으면 현재 자료를 어떻게 확인하나요?",
    "학교 참고 항목이 공란이면 어디까지 추정할 수 있나요?", "학교 이름이 없을 때 어떤 질문을 준비하나요?",
    "학교명 미기재 상태는 수강 불가를 뜻하나요?", "확인된 학교 목록이 없으면 무엇을 대조하나요?",
    "학교 참고값이 없을 때 현재 범위는 어떻게 확인하나요?", "학교명이 제공되지 않으면 어떤 자료를 준비하나요?",
    "학교 목록 공란은 상담표에 어떻게 남기나요?", "미기재 학교 항목에 임의의 이름을 더해도 되나요?",
    "학교 이름 없이 자료 반영 여부를 어떻게 묻나요?", "제공 목록이 없을 때 학교 정보는 언제 확인하나요?",
    "원자료 학교명이 비어 있으면 어떤 답변이 필요한가요?", "학교 참고값 부재와 현재 적용 범위를 어떻게 나누나요?",
)
NO_SCHOOL_QUESTION_LEADS = (
    "원자료 미기재 상태를 기준으로", "제공 범위 밖을 추정하지 않고", "상단 공란 표시를 확인한 뒤", "현재 답변이 필요한 부분을 남겨 두고",
    "사실값이 없는 상태를 기록하며", "다른 센터 자료를 섞지 않도록", "확인 날짜를 남기기 위해", "상담에 물어볼 내용을 정리하며",
    "원문 공란을 그대로 두고", "등록 결정을 서두르지 않도록", "미확인 상태를 분명히 하며", "임의 정보를 더하지 않고",
    "현재 자료를 별도로 준비하며", "답변 주체를 기록하려고", "제공 항목의 한계를 지키며", "상담 답변 전 상태를 표시하며",
)


def faq_question(context: Context, slot: str) -> str:
    if context.unsupported and slot == "grades":
        lead = pick(
            context, "faq-unsupported-grades-lead", UNSUPPORTED_GRADE_QUESTION_LEADS
        )
        ending = pick_avoiding_stems(
            context,
            "faq-unsupported-grades-ending",
            UNSUPPORTED_GRADE_QUESTION_ENDINGS,
            lead,
            ECHO_STEMS,
        )
        return f"{lead}, {ending}"
    if slot == "fee":
        lead = pick(context, "faq-fee-lead", QUESTION_SLOT_LEADS["fee"])
        ending = pick_avoiding_stems(
            context,
            "faq-fee-ending",
            (
                FAQ_FEE_URL_QUESTION_ENDINGS
                if context.center.tuition_url
                else FAQ_FEE_BLANK_QUESTION_ENDINGS
            ),
            lead,
            ECHO_STEMS,
        )
        return f"{lead}, {ending}"
    no_school = slot == "schools" and not context.schools
    endings = NO_SCHOOL_QUESTION_ENDINGS if no_school else QUESTION_ENDINGS[slot]
    leads = NO_SCHOOL_QUESTION_LEADS if no_school else QUESTION_SLOT_LEADS[slot]
    lead = pick(context, f"faq-{slot}-lead", leads)
    ending = pick_avoiding_stems(
        context, f"faq-{slot}-ending", endings, lead, ECHO_STEMS
    )
    return f"{lead}, {ending}"


FAQ_FACT_ENDINGS = (
    "이 값이 원자료에서 확인되는 범위입니다", "상단 사실 항목과 같은 내용입니다", "제공 자료에 적힌 값만 옮긴 것입니다",
    "이 목록이 확인 가능한 원문 범위입니다", "자료에서 읽을 수 있는 값은 여기까지입니다", "상단 표기의 뜻을 바꾸지 않은 내용입니다",
    "확인된 사실값은 이 항목뿐입니다", "원자료 기준 목록은 이와 같습니다", "제공된 범위를 그대로 적은 값입니다",
    "상담 답변과 구분해야 할 원문 값입니다", "현재 페이지에서 확인되는 자료 범위입니다", "확정된 표기만 정리한 결과입니다",
    "원문 항목에 있는 값과 일치합니다", "별도 추정을 더하지 않은 사실입니다", "자료상 확인되는 내용은 여기까지입니다",
    "상단 원자료 행에서 대조할 수 있습니다",
)
FAQ_GUIDANCE_ENDINGS = (
    "실제 개설 여부와 요일·시간은 상담에서 따로 확인하세요", "교재·반 구성·시작일에는 답변 날짜를 붙이세요",
    "학년 표기만으로 현재 신청 가능 여부를 단정하지 마세요", "미기재 수업 조건은 확인 전까지 비워 두세요",
    "실제 적용 범위는 센터 답변으로 다시 대조하세요", "수업 시간과 시작일은 등록 전에 한 번 더 물어보세요",
    "학년값과 실제 시간표를 서로 다른 항목으로 적으세요", "현재 개설 상태를 원자료 사실과 섞지 마세요",
    "과목·학년·일정의 답변 시점을 각각 남기세요", "제공 목록 밖의 내용은 추정하지 마세요",
    "상담에서 확인한 담당자와 적용일도 기록하세요", "수업 가능 여부는 최신 답변을 받은 뒤 판단하세요",
    "제공된 학년 밖의 설명은 사실로 쓰지 마세요", "요일과 횟수는 별도의 확인 질문으로 두세요",
    "현재 운영 조건은 원문 학년값과 구분하세요", "확인 결과가 없으면 개설을 전제로 읽지 마세요",
)

UNSUPPORTED_FAQ_GRADE_ENDINGS = (
    "원자료 미기재인 실제 개설 여부·대상 학년·수업 시간은 상담에서 확인하세요",
    "대상 학년 답변을 받기 전에는 학년 범위를 비워 두세요",
    "현재 개설 여부와 대상 학년을 서로 다른 질문으로 남기세요",
    "공란인 학년 항목에 임의의 학년을 더하지 마세요",
    "미기재 상태와 상담 답변을 별도 항목으로 기록하세요",
    "실제 운영 범위는 담당자의 답변 뒤에만 적으세요",
    "대상 학년·요일·시간에는 각각 확인 날짜를 붙이세요",
    "다른 과목의 학년 범위를 이 항목에 대신 쓰지 마세요",
    "상담 전에는 신청 가능 학년을 추정하지 마세요",
    "개설 여부를 확인하지 않은 채 신청 범위를 단정하지 마세요",
    "학년 공란은 미확인 상태로 두고 현재 답변을 요청하세요",
    "답변 주체와 적용 시점까지 받은 뒤 사실란을 채우세요",
    "대상 학년과 시작일은 상담에서 각각 물어보세요",
    "원자료에 없는 학년 범위는 답변 전까지 추가하지 마세요",
    "수업 시간과 교재도 현재 조건으로 따로 확인하세요",
    "상담 결과가 없으면 대상 학년 칸을 미확인으로 유지하세요",
)

UNSUPPORTED_FAQ_GRADE_GUIDANCE_LEADS = (
    "상담 전 공란 상태를 유지하려면", "상담표에 미기재 항목을 기록할 때는", "상담 답변이 아직 없는 상태에서는",
    "상담 자료의 학년 정보가 비어 있으므로", "상담에서 확인되지 않은 범위를 다룰 때는", "미기재 대상을 상담에서 새로 물을 때는",
    "상담 전 원자료에 없는 학년은", "상담까지 공란 상태를 보존하면서", "공란과 상담 답변을 나누려면",
    "상담 전 공란인 대상 학년을 추정하지 않으려면", "학년 미기재 사실 뒤의 상담에서는", "상담 답변이 없는 동안에는",
    "원문 공란을 상담표에 옮기면", "상담 전 대상 범위가 확인되지 않아", "상담에서 학년 부재를 미기재 사실로 둘 때는",
    "상담 확인 전 공란 상태에서는",
)


def faq_grade_answer(context: Context) -> str:
    fact = grade_fact_core(context, faq=True)
    guidance_lead, guidance_end = pick_pair(
        context,
        "faq-grade-guidance",
        UNSUPPORTED_FAQ_GRADE_GUIDANCE_LEADS if context.unsupported else QUESTION_LEADS,
        UNSUPPORTED_FAQ_GRADE_ENDINGS if context.unsupported else FAQ_GUIDANCE_ENDINGS,
    )
    guidance = f"{guidance_lead} {guidance_end}."
    return f"{fact} {guidance}"


FAQ_SCHOOL_ENDINGS = (
    "이 이름들은 원자료의 참고값일 뿐 수강 가능 보장이 아닙니다",
    "학교명은 현재 수업 개설이나 시험 유형을 확정하지 않습니다",
    "참고 목록과 이번 학기 범위는 서로 다른 자료입니다",
    "학교 이름만으로 현재 반영 범위를 단정할 수 없습니다",
    "이 값은 학교 참고 항목이며 실제 적용 여부는 별도 확인입니다",
    "제공 이름을 특정 학교 학생의 수강 보장으로 읽으면 안 됩니다",
    "참고 학교와 현재 평가 자료는 따로 대조해야 합니다",
    "학교 목록은 센터 자료의 이름값만 보여 줍니다",
    "현재 시험 범위는 학교 공지와 배부 자료로 다시 확인해야 합니다",
    "학교명 뒤의 수업 조건은 원자료에 포함되지 않습니다",
    "제공 학교값은 과거 경향이나 현재 유형을 뜻하지 않습니다",
    "이 목록은 상담 질문을 준비하는 참고 자료입니다",
    "학교 이름과 실제 자료 반영 방식은 같은 사실이 아닙니다",
    "참고 항목 밖 내용은 현재 학교 자료로 확인해야 합니다",
    "학교명은 원문 값으로만 쓰고 적용 범위는 따로 물어야 합니다",
    "이번 학기 내용은 최근 범위표와 함께 확인해야 합니다",
)
FAQ_NO_SCHOOL_GUIDANCE_ENDINGS = (
    "특정 학교와 연결해 해석하지 마세요", "현재 학교 자료는 상담에서 직접 확인하세요",
    "임의의 학교명을 목록에 추가하지 마세요", "학교별 자료 반영 여부를 별도 질문으로 남기세요",
    "학교 적용 범위를 확인 전 상태로 두세요", "학교 공지와 배부 자료를 직접 준비해 대조하세요",
    "다른 센터의 학교 목록을 대신 사용하지 마세요", "현재 학교 정보에는 상담 답변의 확인일을 붙이세요",
    "학교 참고값 부재를 수강 불가로 단정하지 마세요", "학교 미기재 상태와 현재 자료 반영 여부를 구분하세요",
    "학교별 교재·범위는 최신 자료로 따로 확인하세요", "학교명 없이 시험 유형이나 범위를 추정하지 마세요",
    "상담에서 학교와 자료 종류를 각각 물어보세요", "학교 목록은 답변 전까지 미확인으로 두세요",
    "학교 공란에 임의의 이름을 채우지 마세요", "현재 학교 자료의 적용 여부와 답변 날짜를 기록하세요",
)
FAQ_SCHOOL_REFERENCE_ENDS = (
    "옆의 핵심정보 학교 태그가 원문 목록입니다", "상단 학교 참고 행에서 제공 이름을 확인할 수 있습니다", "핵심정보의 학교 태그에 원문 이름이 표시되어 있습니다", "옆의 제공 학교 목록이 확인 가능한 사실 범위입니다",
    "상단 학교 참고값만 원자료 사실로 사용할 수 있습니다", "핵심정보의 학교 행이 이름값의 출처입니다", "옆의 태그에 적힌 이름까지만 자료에서 확인됩니다", "상단 학교 목록을 현재 수강 가능 보장으로 읽으면 안 됩니다",
    "핵심정보의 제공 학교명이 원문 확인 범위입니다", "옆의 학교 태그와 이번 학기 자료는 서로 다른 항목입니다", "상단 학교 참고 행에서 이름만 사실로 확인됩니다", "핵심정보의 학교 목록 밖 내용은 원자료에 없습니다",
    "옆의 학교 태그를 출처로 두고 현재 반영 범위는 따로 물어야 합니다", "상단 학교명을 과거 경향이나 현재 시험 유형으로 확장하면 안 됩니다", "핵심정보의 이름 목록과 실제 적용 여부를 구분해야 합니다", "옆의 제공 학교 행이 이 질문에 답할 원문 범위입니다",
)
FAQ_NO_SCHOOL_REFERENCE_ENDS = (
    "상단 학교 참고 항목이 원자료 미기재 상태입니다", "옆의 핵심정보에 제공 학교명이 표시되어 있지 않습니다", "학교 이름 목록은 원자료에서 확인되지 않습니다", "상단 학교 참고 행에 확인할 이름이 없습니다",
    "핵심정보의 학교 태그가 비어 있는 상태입니다", "제공 학교명은 원자료에 기재되어 있지 않습니다", "옆의 학교 참고 항목에서 이름값을 확인할 수 없습니다", "학교 목록은 상담 전 확인 항목으로 남아 있습니다",
    "원자료 학교 참고값이 비어 있습니다", "상단 핵심정보에 학교명이 제공되지 않았습니다", "확인 가능한 학교 이름은 원자료에 없습니다", "학교 참고 항목은 미기재로 표시됩니다",
    "옆의 학교 행에 제공 목록이 없습니다", "원자료에서 학교명 범위를 확인할 수 없습니다", "상단 학교 참고값은 공란 상태입니다", "학교 이름은 별도 확인이 필요한 미기재 항목입니다",
)


def faq_school_answer(context: Context) -> str:
    lead = pick(context, "faq-school-lead", FAQ_SCHOOL_LEADS)
    if context.schools:
        reference_end = pick_avoiding_stems(
            context,
            "faq-school-reference-end",
            FAQ_SCHOOL_REFERENCE_ENDS,
            lead,
            ECHO_STEMS,
        )
        guidance_endings = FAQ_SCHOOL_ENDINGS
    else:
        reference_end = pick_avoiding_stems(
            context,
            "faq-school-none-reference",
            FAQ_NO_SCHOOL_REFERENCE_ENDS,
            lead,
            ECHO_STEMS,
        )
        guidance_endings = FAQ_NO_SCHOOL_GUIDANCE_ENDINGS
    first = f"{lead} {reference_end}."
    guidance_lead = pick(context, "faq-school-guidance-lead", QUESTION_LEADS)
    guidance_end = pick_avoiding_stems(
        context,
        "faq-school-guidance-end",
        guidance_endings,
        guidance_lead,
        ECHO_STEMS,
    )
    second = f"{guidance_lead} {guidance_end}."
    return f"{first} {second}"


FAQ_ADDRESS_ENDINGS = (
    "주소를 서비스 지역명과 구분해 기록하세요", "방문 전 건물·층 표기를 다시 확인하세요",
    "실제 이동 경로를 출발 전에 별도로 점검하세요", "주소 원문 밖의 거리나 시간을 추정하지 마세요",
    "현재 출입 방법을 센터 답변으로 확인하세요", "출발 전에 최신 출입 방법을 다시 물어보세요",
    "센터 위치와 상담 대상 지역을 같은 뜻으로 보지 마세요", "방문 날짜에 적용되는 안내를 확인하세요",
    "주소와 입구 정보를 서로 다른 확인 항목으로 두세요", "주소 원문을 그대로 메모해 두세요",
    "건물명과 호수의 최신 표기를 대조하세요", "서비스 지역 판단에 주소를 임의로 섞지 마세요",
    "방문 전 최신 위치 답변을 확인하세요", "주소 사실 뒤에 실제 경로 답변을 따로 적으세요",
    "제공 주소를 상단 핵심정보와 다시 대조하세요", "현재 출입 정보를 확인 날짜와 함께 남기세요",
    "주소 원문과 현재 출입 답변을 다른 칸에 적으세요", "방문 당일 건물 안내가 달라졌는지 확인하세요",
    "서비스 지역이 물리 주소를 대신하지 않는다고 표시하세요", "현재 동선을 센터 답변과 함께 확인하세요",
)
FAQ_LOCATION_GUIDANCE_LEADS = (
    "주소를 방문 메모에 옮길 때는", "현재 동선을 정하기 전에는", "제공 주소를 활용할 때는", "방문 계획을 세울 때는",
    "물리 주소를 확인한 뒤에는", "센터를 찾아가기 전에는", "주소 사실과 방문 답변을 나눌 때는", "실제 출입 방법을 정하기 전에는",
    "상단 주소를 기록하면서", "방문 날짜를 정하기 전에는", "주소 밖의 정보를 다룰 때는", "센터 위치를 다시 확인할 때는",
    "주소 원문을 보존하려면", "건물과 입구를 확인하기 전에는", "서비스 지역과 주소를 구분할 때는", "방문 전 위치 정보를 정리할 때는",
    "주소와 출입 답변을 비교할 때는", "방문 경로를 확정하기 전에는", "센터 주소를 일정에 넣기 전에는", "현재 건물 정보를 점검할 때는",
    "주소 기록을 마친 다음에는", "출발 전 방문 정보를 볼 때는", "물리 위치 사실을 사용할 때는", "주소와 실제 동선을 나눌 때는",
)
FAQ_BLANK_LOCATION_GUIDANCE_LEADS = (
    "원자료 위치 안내가 미기재 상태이므로", "제공 자료의 위치 문구가 비어 있으므로",
    "센터 자료에 별도 방문 안내가 없으므로", "원자료의 출입 안내가 기재되어 있지 않으므로",
    "제공된 위치 설명이 없는 상태이므로", "위치 안내 항목이 공란이므로",
    "원자료에서 방문 경로가 확인되지 않으므로", "센터 자료의 건물 안내가 미기재이므로",
    "위치 문구가 제공되지 않은 상태이므로", "원자료에는 주소 외 동선이 없으므로",
    "별도 입구 안내가 기재되어 있지 않으므로", "제공 자료에서 최신 출입 방법은 확인되지 않으므로",
    "방문 안내 원문이 비어 있으므로", "위치 안내 칸이 공란으로 남아 있으므로",
    "센터 원자료에 이동 경로가 없으므로", "제공 항목에 건물 출입 설명이 미기재이므로",
    "원자료상 별도 위치 설명이 없으므로", "방문 동선 자료가 제공되지 않았으므로",
    "주소 밖의 위치 안내가 비어 있으므로", "입구와 층 안내가 원자료에 없으므로",
    "제공 자료에 방문 방법이 기재되어 있지 않으므로", "원자료 위치 항목에서 경로는 확인되지 않으므로",
    "센터 자료의 위치 안내값이 공란이므로", "별도 건물 안내가 제공되지 않았으므로",
)
FAQ_BLANK_ADDRESS_ENDINGS = (
    "최신 출입 방법을 센터 답변으로 확인하세요", "방문 당일 건물명과 층을 다시 물어보세요",
    "실제 이동 경로를 출발 전에 별도로 점검하세요", "주소 밖의 거리나 시간을 추정하지 마세요",
    "주소와 현재 동선을 서로 다른 칸에 기록하세요", "출발 시점에 적용되는 방문 답변을 받아 두세요",
    "물리 주소만 사실로 두고 이동 경로는 비워 두세요", "건물명·호수와 실제 입구를 센터에 확인하세요",
    "서비스 지역과 물리 주소를 구분해 기록하세요", "방문 날짜와 답변 확인일을 함께 적으세요",
    "현재 출입 방법을 확인하기 전에는 이동을 확정하지 마세요", "주소 원문 뒤에 새 방문 답변을 따로 적으세요",
    "출발 전 건물 표기의 변경 여부를 물어보세요", "방문 당일 안내를 답변 날짜와 함께 남기세요",
    "실제 출입구를 확인한 뒤 방문 계획을 확정하세요", "물리 위치와 서비스 지역을 같은 뜻으로 쓰지 마세요",
    "주소 원문에 임의의 거리 정보를 덧붙이지 마세요", "현재 동선은 확인 답변을 받은 뒤 기록하세요",
    "주소 사실과 당일 출입 정보를 나누어 적으세요", "방문 경로를 확인하기 전에는 예상 시간을 더하지 마세요",
)
FAQ_ADDRESS_LEADS = (
    "위치 질문에 답할 제공 주소는", "상단 주소값을 다시 대조하면",
    "주소 문답의 원문 범위에서는", "방문 질문과 주소 항목을 맞추면",
    "상담 전 제공 주소를 다시 읽으면", "센터 위치에 대한 자료상 답은",
    "주소 질문을 원문과 맞추면", "상단 제공 주소 행을 다시 보면",
    "물리 주소 질문의 확인 값은", "제공 주소로 답할 수 있는 범위는",
    "위치 질문을 사실 항목으로 나누면", "원자료 주소를 답변에 옮기면",
    "위치 정보를 상단 자료에서 확인하면", "방문 주소 질문에 원문으로 답하면",
    "제공 주소의 출처를 다시 확인하면", "센터 주소와 서비스 지역을 나누면",
)
FAQ_ADDRESS_SHORT_LEADS = (
    "제공 주소는", "센터 주소 원문은", "상단 주소값은", "물리적 센터 주소는",
    "위치 질문의 원문 답은", "방문 기준 주소는", "핵심정보의 주소는", "센터 위치 사실은",
    "원자료 주소 항목은", "상단 제공 주소는", "주소 질문에서 확인되는 값은", "물리 위치의 주소값은",
    "센터를 찾을 때 쓸 주소는", "서비스 지역과 구분할 주소는", "제공 자료의 주소 원문은", "방문 전에 대조할 주소는",
)
FAQ_ADDRESS_SHORT_ENDS = (
    "입니다", "이며 위 핵심정보와 같은 값입니다", "이며 원자료에서 확인됩니다", "이며 별도 추정을 더하지 않은 값입니다",
    "이며 상단 사실 항목과 일치합니다", "이며 방문 전에 대조할 값입니다", "이며 서비스 지역명과 구분됩니다", "이며 물리 위치 확인에 쓰는 값입니다",
    "이며 원문 표기를 보존한 값입니다", "이며 센터 자료에서 확인됩니다", "이며 상단에 적힌 값입니다", "이며 주소 사실로만 활용해야 합니다",
    "이며 현재 경로와 별도로 봐야 합니다", "이며 추가 거리 정보를 포함하지 않습니다", "이며 출처가 있는 사실값입니다", "이며 방문 당일 안내와 대조해야 합니다",
)
FAQ_FEE_PLACES = (
    "상단 핵심정보", "상단 비용 행", "옆의 핵심정보", "핵심정보 교습비 항목",
    "상단 교습비 행", "옆의 비용 자료", "핵심정보의 비용 칸", "상단 센터 비용 항목",
    "옆의 교습비 정보", "핵심정보 비용 자료 행", "상단 공통 자료 칸", "옆의 센터 교습비 행",
    "핵심정보의 출처 항목", "상단 비용 정보", "옆의 비용 칸", "핵심정보의 교습비 자료 행",
)
FAQ_FEE_CONFIRM_ENDS = (
    "최신 금액은 상담에서 확인하세요", "현재 적용액은 등록 전에 다시 물어보세요", "포함 항목과 기준일을 별도로 확인하세요", "실제 금액에는 답변 날짜를 붙이세요",
    "교재비와 별도 비용을 함께 확인하세요", "링크 자료일과 현재 금액을 구분하세요", "적용 기간과 변경 기준을 다시 물어보세요", "등록 시점의 금액을 따로 대조하세요",
    "금액과 수업 횟수를 다른 칸에 적으세요", "현재 총액은 센터 답변으로 확인하세요", "비용 답변의 확인일을 남겨 두세요", "포함 범위와 최신 여부를 함께 보세요",
    "공통 자료와 실제 적용값을 나누어 기록하세요", "최신 비용은 상담 답변 뒤에 판단하세요", "별도 비용 유무를 등록 전에 확인하세요", "금액 답변의 적용 시점을 적어 두세요",
    "금액 답변에는 담당자와 적용일을 적어 두세요", "등록 시점의 금액은 센터에 다시 물어보세요",
    "공통 자료와 개인별 적용액을 별도 칸에 두세요", "교재비·추가 비용의 포함 범위도 문서로 받아 두세요",
)
FAQ_FEE_BLANK_SOURCES = (
    "센터 비용 원자료에", "상단 제공 자료에", "센터 공통 자료에", "핵심정보의 원자료에",
    "비용 출처 항목에", "센터 교습비 자료에", "상단 비용 원문에", "제공된 비용 자료에",
    "원자료 비용 칸에", "센터 자료의 링크 항목에", "핵심정보 비용 행에", "공통 교습비 원문에",
    "상단 원자료 범위에", "제공 링크 항목에", "비용 자료 원문에", "센터 공통 비용 칸에",
)
FAQ_FEE_BLANK_ENDS = (
    "현재 금액과 포함 항목은 상담에서 확인하세요", "금액을 추정하지 말고 직접 물어보세요", "등록 전 적용액을 별도로 확인하세요", "교재비와 추가 비용도 함께 물어보세요",
    "수업 횟수와 금액을 각각 확인하세요", "비용 답변에 확인일을 붙여 두세요", "다른 센터 금액을 대신 쓰지 마세요", "최신 총액은 상담 뒤에 판단하세요",
    "적용 기간과 변경 기준을 질문하세요", "금액과 포함 범위를 나누어 물어보세요", "현재 비용은 확인 전까지 비워 두세요", "등록 조건과 함께 최신 금액을 확인하세요",
    "별도 비용 유무를 서면으로 확인하세요", "현재 적용값에는 답변 날짜를 적으세요", "교습비와 교재비를 따로 대조하세요", "금액 확인을 등록 판단보다 먼저 두세요",
)
FAQ_FEE_ENDINGS = (
    "상단 교습비 링크는 센터 공통 자료이며 최신 금액은 상담에서 확인하세요",
    "공통 교습비 자료와 현재 적용 금액을 구분해 보세요",
    "교습비 링크의 자료일과 포함 항목을 다시 확인하세요",
    "최신 금액·교재비·별도 비용은 등록 전에 물어보세요",
    "센터 공통 링크만으로 현재 총액을 단정하지 마세요",
    "교습비 적용 기간과 변경 기준도 함께 기록하세요",
    "비용 링크는 출처로 두고 실제 금액은 별도 확인하세요",
    "현재 비용 답변에는 담당자와 날짜를 붙여 두세요",
    "금액과 수업 횟수는 서로 다른 칸에 적으세요",
    "교습비 자료 밖의 항목은 상담 질문으로 남기세요",
    "공통 비용 자료의 최신 적용 여부를 확인하세요",
    "교재·특강 등 포함 범위를 문서로 확인하세요",
    "교습비와 수업 조건이 같은 시점의 답변인지 보세요",
    "링크가 있어도 실제 적용 금액은 바뀔 수 있어 다시 확인하세요",
    "비용 답변과 원자료 링크를 나란히 기록하세요",
    "상단 자료 뒤에 현재 금액 확인일을 추가하세요",
)

FAQ_FEE_URL_QUESTION_ENDINGS = (
    "교습비 링크는 어디에서 확인하나요?", "센터 공통 비용 자료는 어떻게 읽어야 하나요?",
    "등록 전 금액에서 무엇을 다시 물어보나요?", "교습비 자료와 최신 답변은 어떻게 구분하나요?",
    "교재비와 별도 비용은 언제 묻나요?", "현재 적용 금액은 어떻게 확인하나요?",
    "교습비 자료의 기준일은 어디에 남기나요?", "센터 공통 링크만으로 총액을 판단해도 되나요?",
    "금액 답변에는 어떤 확인 정보를 붙이나요?", "교습비와 추가 비용은 어떻게 나누어 기록하나요?",
    "비용 자료의 최신 여부는 어떻게 대조하나요?", "적용액과 포함 항목을 어떤 순서로 물어보나요?",
    "등록 시점의 금액은 언제 다시 확인하나요?", "비용 답변과 공통 자료는 어떻게 구분하나요?",
    "공통 교습비 링크의 출처는 어디에서 보나요?", "비용 자료에서 포함 항목은 어떻게 확인하나요?",
)
FAQ_FEE_BLANK_QUESTION_ENDINGS = (
    "교습비 링크가 미기재이면 무엇을 확인하나요?", "비용 자료 공란은 상담표에 어떻게 남기나요?",
    "교습비 링크가 없을 때 금액은 언제 묻나요?", "미기재 비용 자료 대신 무엇을 확인해야 하나요?",
    "비용 링크가 비어 있으면 어떤 질문을 준비하나요?", "교습비 공란과 현재 금액을 어떻게 구분하나요?",
    "원자료에 비용 링크가 없으면 무엇을 기록하나요?", "교습비 링크 미기재 상태는 무엇을 뜻하나요?",
    "비용 자료가 제공되지 않으면 어디까지 비워 두나요?", "교습비 공란 뒤에 어떤 답변이 필요한가요?",
    "미기재 금액 항목은 누구에게 확인하나요?", "비용 링크가 없을 때 교재비도 함께 물어보나요?",
    "교습비 자료 공란과 실제 적용액을 어떻게 나누나요?", "비용 원문이 비어 있으면 총액을 추정해도 되나요?",
    "교습비 링크가 기재되어 있지 않으면 언제 재확인하나요?", "미기재 비용 항목에는 어떤 확인일을 붙이나요?",
)


def faq_location_answer(context: Context) -> str:
    address_lead = pick(context, "faq-address-short-lead", FAQ_ADDRESS_SHORT_LEADS)
    address_end = pick_avoiding_stems(
        context,
        "faq-address-short-end",
        FAQ_ADDRESS_SHORT_ENDS,
        address_lead,
        ECHO_STEMS,
    )
    first = (
        f"{address_lead} "
        f"“{context.center.address}”"
        f"{address_end}."
    )
    location_leads = (
        FAQ_LOCATION_GUIDANCE_LEADS
        if context.extra.location_guide
        else FAQ_BLANK_LOCATION_GUIDANCE_LEADS
    )
    location_endings = (
        FAQ_ADDRESS_ENDINGS
        if context.extra.location_guide
        else FAQ_BLANK_ADDRESS_ENDINGS
    )
    location_lead = pick(
        context, "faq-location-guidance-lead", location_leads
    )
    location_end = pick_avoiding_stems(
        context,
        "faq-location-guidance-end",
        location_endings,
        location_lead,
        ECHO_STEMS,
    )
    second = f"{location_lead} {location_end}."
    return f"{first} {second}"


def faq_fee_answer(context: Context) -> str:
    if context.center.tuition_url:
        fee_place = pick(context, "faq-fee-place", FAQ_FEE_PLACES)
        fee_prefix = f"센터 공통 교습비 링크는 {fee_place}에 제공되어 있으며,"
        fee_end = pick_avoiding_stems(
            context,
            "faq-fee-confirm",
            FAQ_FEE_CONFIRM_ENDS,
            fee_prefix,
            ECHO_STEMS,
        )
        return f"{fee_prefix} {fee_end}."
    blank_source = pick(context, "faq-fee-blank-source", FAQ_FEE_BLANK_SOURCES)
    blank_prefix = f"교습비 링크는 {blank_source} 미기재 상태이며,"
    blank_end = pick_avoiding_stems(
        context,
        "faq-fee-blank-confirm",
        FAQ_FEE_BLANK_ENDS,
        blank_prefix,
        ECHO_STEMS,
    )
    return f"{blank_prefix} {blank_end}."


FAQ_PREP_LEADS = (
    "상담표 첫 줄에는", "등록 전 메모에는", "상담을 마친 뒤에는", "확인 질문을 적을 때는",
    "상담 내용을 비교하려면", "센터에 문의하면서", "답변 누락을 막으려면", "제공 자료 옆 메모에는",
    "등록 판단을 서두르지 않으려면", "사실과 계획을 나누면서", "현재 조건을 확인할 때는", "상담 뒤 기록에는",
    "첫 문의를 시작할 때는", "질문 순서를 정할 때는", "확인 날짜를 남기면서", "최종 검토표에는",
)
FAQ_PREP_ENDINGS = (
    "센터명·주소·등록번호와 과목·학년·요일·시간·교재·비용을 서로 다른 칸에 적으세요",
    "원자료의 센터·학년·학교·주소 사실과 개설·일정·비용 상담 답변을 나누어 쓰세요",
    "개설 여부·대상 학년·요일·시간·교재·비용·변경 기준을 같은 순서로 물어보세요",
    "센터 사실·학교 참고값·수업 조건·비용 답변에 각각 출처나 확인일을 붙이세요",
    "원자료 미기재 항목은 질문으로 남기고 담당자·답변일·적용 시점을 함께 기록하세요",
    "수업 가능 여부·학년·횟수·시작일·교재·비용을 항목별로 대조하세요",
    "센터명·등록값·주소에 원자료 출처를 붙이고 일정·교재·비용에는 상담 확인일을 적으세요",
    "미기재 학년·시간·교재·비용 칸마다 문의할 내용을 남겨 두세요",
    "학년·학교·주소의 원자료와 실제 개설·시간표·교재·비용 조건을 분리하세요",
    "과목·대상 학년·일정·교재·비용의 현재 적용 여부와 재확인 날짜를 남기세요",
    "센터 사실을 먼저 대조하고 학년·일정·교재·과제·비용 질문을 차례로 확인하세요",
    "가능 여부·대상 학년·수업 일정·교재·비용을 서로 다른 질문으로 만드세요",
    "원자료 학년·학교·주소와 상담에서 받은 일정·교재·비용의 기준일을 함께 적으세요",
    "센터 식별값·학년·학교·주소를 준비하고 개설·시간·비용은 별도 질문으로 두세요",
    "확인된 센터·학년·학교·주소 사실만 옮기고 일정·교재·비용은 질문으로 남기세요",
    "개설·대상 학년·요일·시간·교재·비용 답변마다 담당자와 적용 시점을 표시하세요",
)


def faq_prepare_answer(context: Context) -> str:
    first_lead, first_end = pick_pair(
        context, "faq-prepare", FAQ_PREP_LEADS, FAQ_PREP_ENDINGS
    )
    first = f"{first_lead} {first_end}."
    if context.unsupported:
        unsupported_end = (
            "원자료에 미기재된 실제 개설 여부·대상 학년·수업 시간은 "
            "상담 답변을 받은 뒤에만 기록하세요"
        )
        unsupported_lead = pick_avoiding_stems(
            context,
            "faq-prepare-unsupported-lead",
            QUESTION_LEADS,
            unsupported_end,
            ECHO_STEMS,
        )
        second = (
            f"{unsupported_lead} {unsupported_end}."
        )
    else:
        confirm_lead, confirm_end = pick_pair(
            context, "faq-prepare-confirm", QUESTION_LEADS, CHECK_ENDINGS
        )
        second = f"{confirm_lead} {confirm_end}."
    return f"{first} {second}"


FAQ_HEADING_PREFIXES = (
    "원자료와 상담을 잇는", "검토 항목을 펼쳐 보는", "등록 전에 살펴볼", "센터 사실을 대조하는",
    "미기재 내용을 확인하는", "답변 날짜를 남기는", "과목·학년을 구분하는", "주소와 비용을 확인하는",
    "학교 참고값을 읽는", "상담 순서를 정하는", "제공 자료를 검토하는", "현재 조건을 묻는",
    "사실과 계획을 분리하는", "원문 범위를 지키는", "답변 누락을 막는", "등록 판단에 필요한",
)
FAQ_HEADING_ENDINGS = (
    "네 가지 질문", "질문과 확인 기준", "상담 질문 모음", "원자료 확인 질문",
    "핵심 질문 순서", "사실 확인 문답", "상담 전 질문", "답변 점검 문항",
    "확인 질문 네 항목", "상담 기록 질문", "등록 전 문답", "자료 대조 질문",
)


def faq_heading(context: Context) -> str:
    prefix = pick(context, "faq-heading-prefix", FAQ_HEADING_PREFIXES)
    ending = pick_avoiding_stems(
        context, "faq-heading-ending", FAQ_HEADING_ENDINGS, prefix, ECHO_STEMS
    )
    return prefix + " " + ending


def render_faq(context: Context) -> tuple[str, list[tuple[str, str]]]:
    pairs = [
        (faq_question(context, "grades"), faq_grade_answer(context)),
        (faq_question(context, "schools"), faq_school_answer(context)),
        (faq_question(context, "location"), faq_location_answer(context)),
        (faq_question(context, "fee"), faq_fee_answer(context)),
    ]
    details = []
    for index, (question, answer) in enumerate(pairs):
        attrs = ' class="academy-faq-item"'
        if context.unsupported:
            attrs = (
                ' class="academy-faq-item academy-source-status-faq" '
                + SOURCE_MARKER
            )
        open_attr = " open" if index == 0 else ""
        details.append(
            f"<details{attrs}{open_attr}><summary>{escape(question)}</summary>"
            f"<p>{escape(answer)}</p></details>"
        )
    section = (
        '<section class="section"><div class="container academy-faq-card">'
        f"<h2>{escape(faq_heading(context))}</h2>"
        '<div class="academy-faq-list">'
        + "".join(details)
        + "</div></div></section>"
    )
    return section, pairs


PREP_PREFIXES = (
    "원자료를 옮겨 적는", "확인 항목을 나누는", "답변 날짜를 남기는", "등록 전에 작성하는",
    "센터 사실을 대조하는", "미기재 내용을 표시하는", "질문 순서를 정하는", "비용과 일정을 분리하는",
    "학교 자료를 정리하는", "주소와 수업 조건을 나누는", "현재 답변을 기록하는", "사실 범위를 지키는",
    "과목·학년을 확인하는", "상담 누락을 줄이는", "등록 조건을 검토하는", "원문과 답변을 맞추는",
)
PREP_ENDINGS = (
    "상담 준비 예시", "상담 준비 예시와 순서", "상담 준비 예시 작성법", "상담 준비 예시 기록",
    "상담 준비 예시 항목", "상담 준비 예시 메모", "상담 준비 예시 기준", "상담 준비 예시 구성",
    "상담 준비 예시 점검", "상담 준비 예시 활용", "상담 준비 예시 목록", "상담 준비 예시 안내",
)
PREP_NOTE_LEADS = (
    "이 메모는", "아래 문안은", "다음 기록은", "이 준비 항목은", "아래 예시는", "다음 메모는",
    "이 확인 순서는", "아래 기록법은", "다음 준비안은", "이 상담표는", "아래 작성 예시는", "다음 확인안은",
    "이 기록 항목은", "아래 상담 메모는", "다음 정리 방식은", "이 준비 예시는",
)
PREP_NOTE_ENDINGS = (
    "실제 수강 후기가 아니라 원자료와 상담 답변을 구분하는 예시입니다",
    "특정 학생의 결과를 말하는 후기가 아니라 확인 순서를 보여 줍니다",
    "수강 경험을 인용한 글이 아니라 상담 전에 쓸 기록 예시입니다",
    "성적 결과를 보장하는 후기가 아니라 사실 항목을 나누는 문안입니다",
    "실제 이용자 발언이 아니라 원자료 확인용 메모 예시입니다",
    "수업 결과를 재구성한 후기가 아니라 등록 전 질문 순서입니다",
    "개인 사례를 뜻하지 않으며 상담 답변을 기록하는 형식입니다",
    "특정 성과를 암시하지 않고 사실과 미기재 내용을 구분합니다",
    "실제 후기 대신 확인할 항목을 순서대로 적은 예시입니다",
    "학생 사례를 만들지 않고 센터 자료와 질문만 정리합니다",
    "경험담이 아니라 확인된 값과 추가 질문을 나누는 메모입니다",
    "결과 보장 문안이 아니라 등록 판단 전에 쓰는 체크 예시입니다",
    "실제 수강자의 말이 아니라 원자료 범위를 지키는 기록 방식입니다",
    "후기 형식이 아닌 상담 준비용 사실 대조 예시입니다",
    "성과 사례가 아니라 질문·답변·확인일을 남기는 방법입니다",
    "개인 경험을 꾸미지 않고 센터 사실을 확인하는 준비안입니다",
)
MEMO_LEADS = (
    "첫 메모에는", "질문을 시작할 때는", "제공 자료 옆에는", "상담표 앞부분에는", "확인 순서의 첫 칸을 채울 때는",
    "등록 전 기록에는", "센터 답변을 받을 때는", "사실 항목 다음에는", "준비 메모를 만들면서", "문의 내용을 적을 때는",
    "상담 답변을 정리할 때는", "상담 조건을 비교할 때는", "확인표를 작성하면서", "상담 시점의 상태를 기록할 때는",
    "미기재 항목을 정리할 때는", "상담 준비의 첫 단계에서는",
)
MEMO_SECOND_ENDINGS = (
    "학교 참고값과 이번 학기 자료를 분리하고 주소·비용의 확인일을 적습니다",
    "센터 주소를 원문대로 옮기고 실제 이동 경로와 최신 금액은 따로 묻습니다",
    "학교명은 참고값으로만 두고 현재 범위·교재·비용을 별도 확인합니다",
    "제공 주소와 서비스 지역을 나누고 교습비 답변 날짜를 기록합니다",
    "등록 정보와 학교 목록을 대조한 뒤 미기재 조건에는 질문을 붙입니다",
    "학교 자료 반영 여부와 위치 안내의 최신 상태를 각각 확인합니다",
    "주소·학교·비용의 출처를 적고 실제 적용 조건은 상담 답변으로 남깁니다",
    "센터명과 등록값을 확인한 뒤 일정·교재·금액을 별도 칸에 적습니다",
    "학교 참고 목록 뒤에 현재 자료 제출 방법과 비용 적용일을 씁니다",
    "서비스 지역과 물리 주소를 섞지 않고 방문·비용 답변을 따로 기록합니다",
    "학교명만으로 수업을 단정하지 않고 주소와 비용의 최신 여부를 묻습니다",
    "원자료 위치 문구가 있으면 그대로 옮기고 현재 출입 방법을 재확인합니다",
    "등록번호와 센터명을 함께 확인하고 학교·주소·비용 질문을 순서대로 둡니다",
    "제공 학교와 현재 평가 자료를 나누며 방문 경로는 출발 전에 확인합니다",
    "센터 사실에 원자료 출처를 붙이고 수업 조건에는 답변일을 적습니다",
    "주소와 등록값을 먼저 대조하고 비용·일정의 미확인 칸을 남겨 둡니다",
)

UNSUPPORTED_PREP_PREFIXES = (
    "학년 공란을 기록하는", "미기재 항목을 질문으로 바꾸는", "대상 학년 미확인을 남기는",
    "원자료 부재와 답변을 나누는", "학년 정보가 없을 때 쓰는", "상담 전 공란을 보존하는",
    "대상 범위를 추정하지 않는", "미확인 학년을 점검하는", "학년 공란부터 시작하는",
    "기재되지 않은 항목을 묻는", "원문 미기재를 표시하는", "대상 학년 답변을 기다리는",
    "공란 사실을 먼저 적는", "학년 부재를 상담으로 잇는", "미확인 상태를 유지하는",
    "대상 정보 공란을 확인하는",
)
UNSUPPORTED_PREP_ENDINGS = (
    "미기재 상담 준비 예시", "미기재 상담 준비", "공란 확인 메모", "학년 미기재 질문표",
    "대상 학년 공란 확인 순서", "원자료 공란 기록 예시", "미기재 항목 점검표", "상담 전 공란 메모",
    "학년 미기재 확인안", "대상 정보 미기재 문의 예시", "공란 상태 기록법", "학년 공란 상담안",
    "원문 미기재 점검 예시", "대상 학년 공란 질문 순서", "미기재 사실 대조표", "학년 미기재 준비안",
)
UNSUPPORTED_PREP_NOTE_LEADS = (
    "이 미기재 메모는", "아래 공란 문안은", "다음 미확인 기록은", "이 학년 공란 예시는",
    "아래 대상 미기재 안내는", "다음 상담 전 메모는", "이 공란 확인 순서는", "아래 미확인 기록법은",
    "다음 학년 부재 준비안은", "이 미기재 상담표는", "아래 공란 작성 예시는", "다음 대상 확인안은",
    "이 미확인 항목은", "아래 학년 공란 메모는", "다음 미기재 정리 방식은", "이 대상 부재 예시는",
)
UNSUPPORTED_PREP_NOTE_ENDINGS = (
    "실제 수강 후기가 아니라 학년 공란과 상담 답변을 구분하는 예시입니다",
    "대상 학년을 가정하지 않고 미기재 사실을 기록하는 문안입니다",
    "학생 사례가 아니라 확인되지 않은 항목을 묻는 순서를 보여 줍니다",
    "성과를 암시하지 않고 학년 정보 미기재를 상담 질문으로 남깁니다",
    "실제 이용자 발언이 아니라 원자료 공란 확인용 메모입니다",
    "수업 결과를 만들지 않고 대상 학년 공란 상태를 기록합니다",
    "개인 경험이 아니라 미기재 사실과 이후 답변을 나누는 형식입니다",
    "확인되지 않은 학년을 추정하지 않는 상담 준비 문안입니다",
    "실제 후기 대신 학년 공란에 붙일 질문을 정리한 예시입니다",
    "학생 사례를 만들지 않고 미기재 대상 범위만 표시합니다",
    "경험담이 아니라 원자료에 없는 학년을 확인하는 메모입니다",
    "결과 보장이 아니라 대상 학년 미기재를 기록하는 점검 예시입니다",
    "실제 수강자의 말이 아니라 미기재 항목을 묻는 방식입니다",
    "후기 형식이 아닌 학년 공란 상담 준비 예시입니다",
    "성과 사례가 아니라 공란 상태와 답변일을 남기는 방법입니다",
    "개인 경험을 꾸미지 않고 대상 정보 미기재를 확인하는 준비안입니다",
)
UNSUPPORTED_PREP_MEMO_LEADS = (
    "학년 공란 메모에는", "미기재 질문을 시작할 때는", "원자료 공란 옆에는", "대상 미확인 상담표에는",
    "학년 정보 부재를 적을 때는", "등록 전 미기재 기록에는", "대상 학년 답변을 받을 때는", "공란 사실 다음에는",
    "미확인 준비 메모에는", "대상 정보 문의를 적을 때는", "학년 공란 답변을 정리할 때는", "미기재 상태를 비교할 때는",
    "공란 확인표를 작성하면서", "상담 전 미확인 상태에는", "대상 학년 부재를 정리할 때는", "원문 공란 확인 단계에서는",
)
UNSUPPORTED_PREP_MEMO_ENDINGS = (
    "원자료에 학년이 기재되어 있지 않음을 적고 실제 개설 여부를 따로 묻습니다",
    "대상 학년 공란을 유지하고 답변 주체와 확인일을 남깁니다",
    "미기재 학년 범위에 임의의 값을 더하지 않고 현재 상태를 문의합니다",
    "학년 정보 미기재와 상담에서 받은 대상 범위를 서로 다른 칸에 둡니다",
    "원문 미기재 사실을 먼저 적고 대상 학년·요일·시간을 질문합니다",
    "학년 공란 확인 전에는 어떤 학년도 신청 가능 대상으로 단정하지 않습니다",
    "학년 공란과 실제 운영 답변에 각각 출처와 날짜를 붙입니다",
    "대상 정보가 비어 있음을 표시하고 답변 뒤에만 사실란을 채웁니다",
    "학년 공란에 다른 과목의 값을 대신 쓰지 않고 현재 대상을 직접 확인합니다",
    "미기재 상태를 수강 불가로 해석하지 않고 개설 여부를 따로 묻습니다",
    "원자료에 없는 대상 학년은 상담 전까지 미확인으로 남깁니다",
    "학년 미기재와 수업 시간 공란을 한 줄씩 나누어 기록합니다",
    "공란 항목에 답변 담당자·적용 시점·재확인 날짜를 붙입니다",
    "미기재 대상 학년을 추정하지 않고 현재 운영 범위를 확인합니다",
    "미기재 학년과 교재·일정을 각각 별도 질문으로 남깁니다",
    "원문 공란을 보존한 뒤 실제 신청 가능 여부를 상담에서 판단합니다",
)


def render_prep(context: Context) -> str:
    if context.unsupported:
        heading_prefix = pick(
            context, "unsupported-prep-heading-prefix", UNSUPPORTED_PREP_PREFIXES
        )
        heading_end = pick_avoiding_stems(
            context,
            "unsupported-prep-heading-end",
            UNSUPPORTED_PREP_ENDINGS,
            heading_prefix,
            tuple(stem for stem in ECHO_STEMS if stem != "준비"),
        )
        heading_text = heading_prefix + " " + heading_end
        note_lead, note_end = pick_pair(
            context,
            "unsupported-prep-note",
            UNSUPPORTED_PREP_NOTE_LEADS,
            UNSUPPORTED_PREP_NOTE_ENDINGS,
        )
        first_lead, first_end = pick_pair(
            context,
            "unsupported-prep-first",
            UNSUPPORTED_PREP_MEMO_LEADS,
            UNSUPPORTED_PREP_MEMO_ENDINGS,
        )
        second_lead = pick_distinct(
            context,
            "unsupported-prep-second-lead",
            UNSUPPORTED_PREP_MEMO_LEADS,
            first_lead,
        )
        second_end = pick_avoiding_stems(
            context,
            "unsupported-prep-second-end",
            UNSUPPORTED_PREP_MEMO_ENDINGS,
            second_lead + " " + first_end,
            ECHO_STEMS,
        )
        note = f"{note_lead} {note_end}."
        first = f"{first_lead} {first_end}."
        second = f"{second_lead} {second_end}."
        attrs = (
            f'class="container academy-review-card academy-source-unconfirmed" '
            f'{SOURCE_MARKER}'
        )
        return (
            f'<section class="section"><div {attrs}>'
            f"<h2>{escape(heading_text)}</h2>"
            f'<p class="academy-review-note">{escape(note)}</p>'
            '<div class="academy-review-list">'
            f'<blockquote class="academy-review-item">{escape(first)}</blockquote>'
            f'<blockquote class="academy-review-item">{escape(second)}</blockquote>'
            "</div></div></section>"
        )
    heading_prefix = pick(context, "prep-heading-prefix", PREP_PREFIXES)
    heading_end = pick_avoiding_stems(
        context,
        "prep-heading-end",
        PREP_ENDINGS,
        heading_prefix,
        tuple(stem for stem in ECHO_STEMS if stem != "준비"),
    )
    heading_text = heading_prefix + " " + heading_end
    note_lead, note_end = pick_pair(
        context, "prep-note", PREP_NOTE_LEADS, PREP_NOTE_ENDINGS
    )
    note = f"{note_lead} {note_end}."
    first_lead = pick(context, "prep-memo-lead", MEMO_LEADS)
    first_end = pick_avoiding_stems(
        context, "prep-memo-first", CHECK_ENDINGS, first_lead, ECHO_STEMS
    )
    first = f"{first_lead} {first_end}."
    second_lead = pick_distinct(
        context, "prep-memo-second-lead", MEMO_LEADS, first_lead
    )
    second_end = pick_avoiding_stems(
        context,
        "prep-memo-second-end",
        MEMO_SECOND_ENDINGS,
        second_lead,
        ECHO_STEMS,
    )
    second = f"{second_lead} {second_end}."
    attrs = (
        f'class="container academy-review-card academy-source-unconfirmed" {SOURCE_MARKER}'
        if context.unsupported
        else 'class="container academy-review-card"'
    )
    return (
        f'<section class="section"><div {attrs}>'
        f"<h2>{escape(heading_text)}</h2>"
        f'<p class="academy-review-note">{escape(note)}</p>'
        '<div class="academy-review-list">'
        f'<blockquote class="academy-review-item">{escape(first)}</blockquote>'
        f'<blockquote class="academy-review-item">{escape(second)}</blockquote>'
        "</div></div></section>"
    )


def add_registration_selector(text: str, context: Context) -> str:
    pattern = re.compile(
        r'(<dt>교육지원청 등록번호</dt><dd)(?![^>]*data-source-field)([^>]*)>',
        re.I,
    )
    text, count = pattern.subn(
        rf'\1 {REGISTRATION_ATTR}\2>', text, count=1,
    )
    if count == 0 and REGISTRATION_ATTR not in text:
        raise ValueError("quick-info registration dd not found")
    return text


def update_jsonld(
    text: str,
    headings: Sequence[str],
    faqs: Sequence[tuple[str, str]],
    context: Context,
    description: str,
) -> str:
    def replace(match: re.Match[str]) -> str:
        data = json.loads(match.group(2))
        graph = data.get("@graph")
        if not isinstance(graph, list):
            raise ValueError("JSON-LD @graph missing")
        articles = [node for node in graph if isinstance(node, dict) and phase1.node_has_type(node, "Article")]
        faq_pages = [node for node in graph if isinstance(node, dict) and phase1.node_has_type(node, "FAQPage")]
        if len(articles) != 1 or len(faq_pages) != 1:
            raise ValueError("Article/FAQPage cardinality mismatch")
        article = articles[0]
        article["hasPart"] = [
            {"@type": "WebPageElement", "name": name} for name in headings
        ]
        article["dateModified"] = FRESHNESS_DATE
        article["abstract"] = description
        for node in graph:
            if not isinstance(node, dict):
                continue
            if any(
                phase1.node_has_type(node, node_type)
                for node_type in ("WebPage", "Article", "Service")
            ):
                node["description"] = description
        faq_pages[0]["mainEntity"] = [
            {
                "@type": "Question",
                "name": question,
                "acceptedAnswer": {"@type": "Answer", "text": answer},
            }
            for question, answer in faqs
        ]
        payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
        return match.group(1) + payload + match.group(3)

    result, count = JSONLD_RE.subn(replace, text)
    if count != 1:
        raise ValueError(f"JSON-LD cardinality mismatch: {count}")
    return result


def transform_document(text: str, context: Context, stats: Stats | None = None) -> str:
    text, description = transform_meta_descriptions(text, context)
    text = transform_summary_copy(text, context)
    article, headings = render_article(context)
    text, article_count = ARTICLE_RE.subn(article, text, count=1)
    if article_count != 1:
        raise ValueError(f"article block cardinality: {article_count}")
    faq_section, faqs = render_faq(context)
    text, faq_count = FAQ_SECTION_RE.subn(faq_section, text, count=1)
    if faq_count != 1:
        raise ValueError(f"FAQ section cardinality: {faq_count}")
    text, prep_count = PREP_SECTION_RE.subn(render_prep(context), text, count=1)
    if prep_count != 1:
        raise ValueError(f"prep section cardinality: {prep_count}")
    text = add_registration_selector(text, context)
    text = update_jsonld(text, headings, faqs, context, description)
    if stats:
        stats.add("hero_and_top_answer_rebuilt")
        stats.add("detail_reader_copy_rebuilt")
        stats.add("faq_rebuilt", 4)
        stats.add("article_sections_rebuilt", 5)
        stats.add("location_guide_visible", bool(context.extra.location_guide))
        stats.add("location_guide_blank", not context.extra.location_guide)
        stats.add("unsupported_neutral_renderer", context.unsupported)
    return text


def fragment_text(value: str) -> str:
    return phase1.fragment_text(value)


def article_html(text: str) -> str:
    match = ARTICLE_RE.search(text)
    return match.group(0) if match else ""


def manuscript_html(text: str) -> str:
    parts = []
    article = ARTICLE_RE.search(text)
    faq = FAQ_SECTION_RE.search(text)
    prep = PREP_SECTION_RE.search(text)
    for match in (article, faq, prep):
        if match:
            parts.append(match.group(0))
    return " ".join(parts)


def editorial_blocks(text: str) -> list[str]:
    result: list[str] = []
    for match in BLOCK_RE.finditer(manuscript_html(text)):
        attrs = match.group("attrs")
        if LOCATION_GUIDE_ATTR in attrs:
            continue
        value = fragment_text(match.group("body"))
        if value:
            result.append(value)
    return result


def sentences(value: str) -> list[str]:
    return [item.strip() for item in re.split(r"(?<=[.!?])\s+", value) if item.strip()]


def normalized_template(value: str, context: Context) -> str:
    value = clean(value).lower()
    facts: list[str] = [
        context.center.locality,
        context.center.display_locality,
        context.center.display_geo,
        context.center.official_region,
        context.center.display_district,
        context.center.center_name,
        context.center.address,
        context.center.registration,
        context.extra.registered_name,
        context.extra.location_guide,
        context.label,
        context.scope,
    ]
    facts.extend(context.schools)
    facts.extend(context.center.grades["영어"])
    facts.extend(context.center.grades["수학"])
    for fact in sorted({item for item in facts if item}, key=len, reverse=True):
        value = value.replace(fact.lower(), "<사실>")
    value = re.sub(r"영어\s*·\s*수학|영수|영어|수학", "<과목>", value)
    value = re.sub(r"고등|중등|초등", "<단계>", value)
    value = re.sub(r"(?:초[1-6]|[중고][1-3])", "<학년>", value)
    value = re.sub(r"\d+", "<수>", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value


def schema_nodes(text: str) -> list[dict[str, Any]]:
    match = JSONLD_RE.search(text)
    if not match:
        return []
    data = json.loads(match.group(2))
    graph = data.get("@graph", [])
    return [node for node in graph if isinstance(node, dict)]


def one_schema_node(text: str, node_type: str) -> dict[str, Any]:
    nodes = [node for node in schema_nodes(text) if phase1.node_has_type(node, node_type)]
    if len(nodes) != 1:
        raise ValueError(f"{node_type} cardinality: {len(nodes)}")
    return nodes[0]


def immutable_article_published(text: str) -> str:
    return clean(one_schema_node(text, "Article").get("datePublished"))


def validate_document(before: str, after: str, context: Context) -> None:
    # Phase 3 intentionally changes how often source literals appear in the
    # manuscript.  Keep phase 1's full post-render quality/source validator,
    # while guarding the immutable route/title/H1/link surface against the
    # real baseline separately.  Passing ``after`` as both arguments avoids
    # phase 1's phase-specific literal-count invariant without weakening any
    # value, marker, school, grade, grammar, or schema check on the result.
    if phase1.stable_snapshot(before) != phase1.stable_snapshot(after):
        raise ValueError("URL/title/H1/canonical/robots invariant changed")
    # Phase 1 predates the audited location-guide field.  Mask that one
    # source-exact node only while reusing its broad grammar/school validator:
    # road names such as ``석동로`` and factual landmarks such as ``두정초``
    # are otherwise mistaken for particle errors or cross-grade claims.  The
    # unmasked exact value is checked below against the CSV sanitizer output.
    phase1_copy = re.sub(
        r'(<p\b[^>]*data-source-field=["\']location-guide["\'][^>]*>).*?(</p>)',
        r"\1\2",
        after,
        flags=re.I | re.S,
    )
    phase1.validate_document(phase1_copy, phase1_copy, context.profile, context.center)
    if immutable_article_published(before) != immutable_article_published(after):
        raise ValueError("Article.datePublished changed")
    article = one_schema_node(after, "Article")
    if article.get("dateModified") != FRESHNESS_DATE:
        raise ValueError("Article.dateModified mismatch")
    description = render_meta_description(context)
    description_match = META_DESCRIPTION_RE.search(after)
    og_match = OG_DESCRIPTION_RE.search(after)
    if not description_match or html.unescape(description_match.group(3)) != description:
        raise ValueError("meta description source renderer mismatch")
    if not og_match or html.unescape(og_match.group(3)) != description:
        raise ValueError("og:description parity mismatch")
    for node in schema_nodes(after):
        if any(
            phase1.node_has_type(node, node_type)
            for node_type in ("WebPage", "Article", "Service")
        ) and node.get("description") != description:
            raise ValueError("schema description parity mismatch")
    if article.get("abstract") != description:
        raise ValueError("Article.abstract source renderer mismatch")
    article_fragment = article_html(after)
    article_sections = re.findall(
        r'<section\b[^>]*class=["\'][^"\']*academy-prose-section[^"\']*["\'][^>]*>'
        r'(.*?)</section>',
        article_fragment,
        re.I | re.S,
    )
    section_headings = [fragment_text(item) for item in HEADING_RE.findall(article_fragment)]
    if (
        len(article_sections) != 5
        or len(section_headings) != 5
        or len(set(section_headings)) != 5
    ):
        raise ValueError(f"article five-section invariant: {section_headings}")
    has_part = article.get("hasPart")
    if not isinstance(has_part, list) or [clean(item.get("name")) for item in has_part] != section_headings:
        raise ValueError("Article.hasPart / visible H2 mismatch")
    faqs = phase1.visible_faqs(after)
    if len(faqs) != 4 or len({q for q, _ in faqs}) != 4:
        raise ValueError("visible FAQ four-item/unique invariant")
    if phase1.schema_faqs(after) != faqs:
        raise ValueError("FAQPage parity mismatch")
    faq_slots = ("grades", "schools", "location", "fee")
    for index, slot in enumerate(faq_slots):
        faq_text = " ".join(faqs[index])
        topic_text = (
            faq_text.replace(context.center.address, "")
            if slot == "location"
            else faq_text
        )
        if not FAQ_TOPIC_RE[slot].search(topic_text):
            raise ValueError(f"FAQ {slot} topic cue missing")
        foreign = [
            other
            for other in faq_slots
            if other != slot and FAQ_TOPIC_RE[other].search(topic_text)
        ]
        if foreign:
            raise ValueError(f"FAQ {slot} topic overlap: {foreign}")
    location_answer = faqs[2][1]
    fee_answer = faqs[3][1]
    quoted_address = f"“{context.center.address}”"
    if location_answer.count(quoted_address) != 1:
        raise ValueError("location FAQ source address exact/cardinality mismatch")
    if sum(answer.count(context.center.address) for _question, answer in faqs) != 1:
        raise ValueError("FAQ source address must occur only in location answer")
    if FAQ_LOCATION_DOUBLE_TOPIC_RE.search(location_answer):
        raise ValueError("location FAQ double-topic particle mismatch")
    if not context.extra.location_guide:
        if not MISSING_CUE_RE.search(location_answer):
            raise ValueError("blank-guide location FAQ missing negative source cue")
        if any(
            cue in location_answer
            for cue in FAQ_BLANK_GUIDE_POSITIVE_PREMISES
        ):
            raise ValueError("blank-guide location FAQ retains positive premise")
    if FAQ_TOPIC_RE["fee"].search(location_answer):
        raise ValueError("location FAQ contains fee lexeme")
    if FAQ_TOPIC_RE["location"].search(fee_answer) or context.center.address in fee_answer:
        raise ValueError("fee FAQ contains address/location lexeme")
    if context.center.tuition_url:
        if (
            fee_answer.count("센터 공통 교습비 링크") != 1
            or not any(cue in fee_answer for cue in ("상단", "핵심정보", "옆의"))
            or MISSING_CUE_RE.search(fee_answer)
        ):
            raise ValueError("fee FAQ provided-link source state mismatch")
        if not faqs[3][0].endswith(FAQ_FEE_URL_QUESTION_ENDINGS):
            raise ValueError("fee FAQ question contradicts provided-link state")
    else:
        if (
            fee_answer.count("교습비 링크") != 1
            or "미기재 상태" not in fee_answer
            or "센터 공통 교습비 링크" in fee_answer
            or not MISSING_CUE_RE.search(fee_answer)
        ):
            raise ValueError("fee FAQ missing-link source state mismatch")
        if not faqs[3][0].endswith(FAQ_FEE_BLANK_QUESTION_ENDINGS):
            raise ValueError("fee FAQ question omits missing-link state")
    if FEE_AMOUNT_CERTAINTY_RE.search(fee_answer):
        raise ValueError("fee FAQ asserts an unsupported amount")

    grade_paragraphs = [
        fragment_text(item)
        for item in re.findall(r"<p\b[^>]*>(.*?)</p>", article_sections[1], re.I | re.S)
    ]
    location_paragraphs = [
        fragment_text(item)
        for item in re.findall(r"<p\b[^>]*>(.*?)</p>", article_sections[3], re.I | re.S)
    ]
    if len(grade_paragraphs) < 2 or len(location_paragraphs) < 2:
        raise ValueError("article fact/guidance paragraph invariant")
    if not context.extra.location_guide:
        blank_guide_guidance = location_paragraphs[-1]
        if any(
            cue in blank_guide_guidance
            for cue in BLANK_GUIDE_EXISTENCE_PREMISES
        ):
            raise ValueError("blank location guide existence premise remains")
        if not any(
            blank_guide_guidance.startswith(lead)
            for lead in NO_GUIDE_CONTEXT_LEADS
        ):
            raise ValueError("blank location guide neutral lead missing")
        if not MISSING_CUE_RE.search(blank_guide_guidance):
            raise ValueError("blank location guide negative state cue missing")
        if after.count(MISSING_LOCATION_GUIDE_MARKER) != 1:
            raise ValueError("blank location guide status marker cardinality")
        missing_guide_match = re.search(
            r'<p\b(?=[^>]*academy-location-guide-status)'
            r'(?=[^>]*data-source-status=["\']missing-location-guide["\'])'
            r'[^>]*>(.*?)</p>',
            after,
            re.I | re.S,
        )
        if (
            not missing_guide_match
            or fragment_text(missing_guide_match.group(1)) != blank_guide_guidance
        ):
            raise ValueError("blank location guide status selector mismatch")
        map_caption = fragment_text(
            phase1.extract_one(
                after,
                r'<figcaption\b[^>]*class=["\'][^"\']*academy-map-caption[^"\']*'
                r'["\'][^>]*>(.*?)</figcaption>',
            )
        )
        if (
            not map_caption.startswith(MAP_BLANK_LEADS)
            or not MISSING_CUE_RE.search(map_caption)
        ):
            raise ValueError("blank map caption is not a negative-only renderer")
    elif MISSING_LOCATION_GUIDE_MARKER in after:
        raise ValueError("nonblank location guide has missing-state marker")
    if context.unsupported:
        article_grade_guidance = grade_paragraphs[-1]
        faq_grade_guidance = sentences(faqs[0][1])[-1]
        if any(
            cue in value
            for value in (article_grade_guidance, faq_grade_guidance)
            for cue in UNSUPPORTED_GRADE_PREMISE_CUES
        ):
            raise ValueError("unsupported grade-value existence premise remains")
        if not article_grade_guidance.startswith(UNSUPPORTED_GRADE_GUIDANCE_LEADS):
            raise ValueError("unsupported neutral article guidance missing")
        if not any(
            faq_grade_guidance.endswith(ending + ".")
            for ending in UNSUPPORTED_FAQ_GRADE_ENDINGS
        ):
            raise ValueError("unsupported neutral FAQ guidance missing")
        intro_match = re.search(
            r'<div\b[^>]*class=["\'][^"\']*academy-intro-answer[^"\']*["\']'
            r'[^>]*>.*?<p\b[^>]*>(.*?)</p>',
            article_fragment,
            re.I | re.S,
        )
        prep_match = PREP_SECTION_RE.search(after)
        if not intro_match or not prep_match:
            raise ValueError("unsupported intro/prep structure missing")
        intro_text = fragment_text(intro_match.group(1))
        prep_fragment = prep_match.group(0)
        prep_parts = [fragment_text(item) for item in HEADING_RE.findall(prep_fragment)]
        prep_parts.extend(
            fragment_text(match.group("body")) for match in BLOCK_RE.finditer(prep_fragment)
        )
        authored_grade_parts = [
            intro_text,
            section_headings[1],
            article_grade_guidance,
            faqs[0][0],
            faq_grade_guidance,
            *prep_parts,
        ]
        if any(
            UNSUPPORTED_POSITIVE_GRADE_PREMISE_RE.search(value)
            for value in authored_grade_parts
        ):
            raise ValueError("unsupported positive grade-value premise remains")
        if any(not MISSING_CUE_RE.search(value) for value in authored_grade_parts):
            raise ValueError("unsupported negative-only authored pool missing state cue")
        article_grade_fact = grade_paragraphs[0]
        faq_grade_fact = grade_fact_core(context, faq=True)
        config_subject = str(context.config["subject"])
        if not faqs[0][1].startswith(faq_grade_fact + " "):
            raise ValueError("unsupported FAQ grade fact renderer mismatch")
        if config_subject == "영수":
            english = grade_list(context.center.grades["영어"]) or "원자료 미기재"
            math = grade_list(context.center.grades["수학"]) or "원자료 미기재"
            subject_pair = f"영어 학년값은 {english}이고 수학 학년값은 {math}"
            for value in (article_grade_fact, faq_grade_fact):
                if subject_pair not in value:
                    raise ValueError("unsupported partial-subject grade facts mismatch")
                collective = [
                    sentence
                    for sentence in sentences(value)
                    if "영어·수학을 함께 보는 대상 학년" in sentence
                ]
                if len(collective) != 1 or not MISSING_CUE_RE.search(collective[0]):
                    raise ValueError("unsupported combined-subject missing cue mismatch")
        else:
            for value in (article_grade_fact, faq_grade_fact):
                if CONCRETE_GRADE_RE.search(value) or not MISSING_CUE_RE.search(value):
                    raise ValueError("unsupported single-subject grade fact is not missing-only")

    manuscript = manuscript_html(after)
    visible = fragment_text(manuscript)
    article_visible = fragment_text(article_fragment)
    query = f"{context.center.display_locality} {context.label}"
    if article_visible.count(context.center.display_locality) > 12:
        raise ValueError("Article locality literal exceeds 12")
    if visible.count(context.center.display_locality) > 12:
        raise ValueError("manuscript locality literal exceeds 12")
    if visible.count(query) > 3:
        raise ValueError("manuscript exact query exceeds 3")
    if any(label in visible for label in LEGACY_LABELS):
        raise ValueError("legacy label remains in visible manuscript")
    if FEE_DOUBLE_TOPIC_RE.search(visible):
        raise ValueError("fee double-topic wording remains")
    if any(label in json.dumps(schema_nodes(after), ensure_ascii=False) for label in LEGACY_LABELS):
        raise ValueError("legacy label remains in JSON-LD")
    if phase1.IRRELEVANT_SEED_RE.search(visible):
        raise ValueError("operator/irrelevant seed remains")
    if any(term in visible for term in ("가상 학부모", "대표 사례", "학생을 가정", "상담 후기")):
        raise ValueError("faux case/review language remains")

    blocks = editorial_blocks(after)
    paragraph_counter = Counter(blocks)
    if any(count > 1 for count in paragraph_counter.values()):
        raise ValueError("within-page exact paragraph duplicate")
    all_sentences = [sentence for block in blocks for sentence in sentences(block)]
    sentence_counter = Counter(all_sentences)
    if any(count > 1 for count in sentence_counter.values()):
        raise ValueError("within-page exact sentence duplicate")
    echoed = [
        (stem, sentence)
        for sentence in all_sentences
        for stem in HARD_ECHO_STEMS
        if sentence.count(stem) > 1
    ]
    if echoed:
        raise ValueError(f"editorial verb echo remains: {echoed[:2]}")
    if any(len(block) > 180 for block in blocks):
        sample = [(len(block), block[:80]) for block in blocks if len(block) > 180][:2]
        raise ValueError(f"editorial paragraph exceeds 180: {sample}")
    if any(len(sentence) > 180 for sentence in all_sentences):
        raise ValueError("editorial sentence exceeds 180")

    main_text = phase1.rendered_main_text(after)
    main_html = phase1.extract_one(after, r"<main\b[^>]*>(.*?)</main>")
    main_sentences = sentences(main_text)
    if any(count > 1 for count in Counter(main_sentences).values()):
        raise ValueError("full-main exact sentence duplicate")
    main_blocks: list[str] = []
    for match in BLOCK_RE.finditer(main_html):
        if LOCATION_GUIDE_ATTR in match.group("attrs"):
            continue
        value = fragment_text(match.group("body"))
        if value:
            main_blocks.append(value)
    if any(count > 1 for count in Counter(main_blocks).values()):
        raise ValueError("full-main exact paragraph duplicate")
    if any(label in main_text for label in LEGACY_LABELS):
        raise ValueError("legacy label remains in full visible main")
    h1 = fragment_text(phase1.extract_one(after, r"<h1\b[^>]*>(.*?)</h1>"))
    if main_text.count(context.center.display_locality) > 30:
        raise ValueError("full main locality literal exceeds 30")
    if main_text.count(h1) > 8:
        raise ValueError("full main exact H1 exceeds 8")
    per_thousand = max(1.0, len(main_text) / 1000)
    if main_text.count(context.center.display_locality) / per_thousand > 6:
        raise ValueError("full main locality density exceeds 6/1000")
    if main_text.count(h1) / per_thousand > 2:
        raise ValueError("full main H1 density exceeds 2/1000")

    if after.count(LOCATION_GUIDE_ATTR) != (1 if context.extra.location_guide else 0):
        raise ValueError("location-guide selector cardinality")
    if context.extra.location_guide:
        match = re.search(
            r'<[^>]*data-source-field=["\']location-guide["\'][^>]*>(.*?)</[^>]+>',
            after,
            re.I | re.S,
        )
        if not match or fragment_text(match.group(1)) != context.extra.location_guide:
            raise ValueError("clean location guide mismatch")
        if URL_RE.search(fragment_text(match.group(1))) or EMOJI_RE.search(fragment_text(match.group(1))):
            raise ValueError("raw URL/emoticon remains in location guide")
    if after.count(REGISTRATION_ATTR) != 1:
        raise ValueError("registration-number selector cardinality")
    if after.count(REGISTERED_NAME_ATTR) != 1:
        raise ValueError("registered-name selector cardinality")
    registered_match = re.search(
        r'<span\b[^>]*data-source-field=["\']registered-name["\'][^>]*>(.*?)</span>',
        after,
        re.I | re.S,
    )
    if not registered_match or fragment_text(registered_match.group(1)) != context.extra.registered_name:
        raise ValueError("registered-name source mismatch")
    registration_block = re.search(
        r'<p\b[^>]*class=["\'][^"\']*academy-registration-facts[^"\']*["\'][^>]*>'
        r'(.*?)</p>',
        after,
        re.I | re.S,
    )
    if not registration_block or context.center.registration not in fragment_text(registration_block.group(1)):
        raise ValueError("registered name used without registration number")
    if context.unsupported:
        if not all(SOURCE_MARKER in attrs for attrs, _q, _a in _visible_faq_with_attrs(after)):
            raise ValueError("unsupported FAQ marker missing")
        if not any(("원자료" in answer or "제공" in answer) and "상담" in answer for _q, answer in faqs):
            raise ValueError("unsupported neutral FAQ disclosure missing")


def _visible_faq_with_attrs(text: str) -> list[tuple[str, str, str]]:
    block = phase1.extract_one(
        text,
        r'<div\b[^>]*class=["\'][^"\']*academy-faq-list[^"\']*["\'][^>]*>(.*?)</div>',
    )
    result = []
    for attrs, body in re.findall(r"<details\b([^>]*)>(.*?)</details>", block, re.I | re.S):
        question = fragment_text(phase1.extract_one(body, r"<summary\b[^>]*>(.*?)</summary>"))
        answer = fragment_text(phase1.extract_one(body, r"<p\b[^>]*>(.*?)</p>"))
        result.append((attrs, question, answer))
    return result


def canonical_url(text: str) -> str:
    return phase1.extract_one(
        text,
        r'<link\b(?=[^>]*rel=["\']canonical["\'])[^>]*href=["\']([^"\']+)["\']',
    )


def sitemap_entries(text: str) -> list[tuple[str, str]]:
    return [
        (html.unescape(url), date)
        for url, date in re.findall(
            r"<url><loc>(.*?)</loc><lastmod>(.*?)</lastmod></url>", text, re.I | re.S
        )
    ]


def transform_sitemap(text: str, target_urls: frozenset[str]) -> str:
    matched: set[str] = set()

    def replace(match: re.Match[str]) -> str:
        url = html.unescape(match.group(1))
        date = match.group(2)
        if url not in target_urls:
            return match.group(0)
        matched.add(url)
        if date == FRESHNESS_DATE:
            return match.group(0)
        return f"<url><loc>{match.group(1)}</loc><lastmod>{FRESHNESS_DATE}</lastmod></url>"

    result = re.sub(
        r"<url><loc>(.*?)</loc><lastmod>(.*?)</lastmod></url>", replace, text,
        flags=re.I | re.S,
    )
    if matched != set(target_urls):
        missing = sorted(set(target_urls) - matched)[:3]
        raise ValueError(f"sitemap detail URL mismatch: {len(matched)} / {missing}")
    return result


def validate_sitemap(before: str, after: str, target_urls: frozenset[str]) -> None:
    original = sitemap_entries(before)
    updated = sitemap_entries(after)
    if [url for url, _ in original] != [url for url, _ in updated]:
        raise ValueError("sitemap URL set/order changed")
    original_map = dict(original)
    updated_map = dict(updated)
    for url, date in original_map.items():
        if url in target_urls:
            if updated_map[url] != FRESHNESS_DATE:
                raise ValueError(f"target sitemap lastmod mismatch: {url}")
        elif updated_map[url] != date:
            raise ValueError(f"non-target sitemap lastmod changed: {url}")


def excluded_from_scope(path: Path, root: Path, authorized: frozenset[Path]) -> bool:
    resolved = path.resolve()
    if resolved in authorized:
        return True
    relative = resolved.relative_to(root)
    parts = set(relative.parts)
    return bool(parts & {".git", "tmp", "__pycache__"})


def external_scope_hash(root: Path, authorized: frozenset[Path]) -> str:
    digest = hashlib.sha256()
    for path in sorted((item for item in root.rglob("*") if item.is_file()), key=lambda item: item.as_posix()):
        if excluded_from_scope(path, root, authorized):
            continue
        relative = path.resolve().relative_to(root).as_posix().encode("utf-8")
        digest.update(relative)
        digest.update(b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def collect_metrics(items: Sequence[tuple[str, Context]]) -> QualityMetrics:
    metrics = QualityMetrics()
    sentence_docs: Counter[str] = Counter()
    paragraph_docs: Counter[str] = Counter()
    exact_sentence_docs: Counter[str] = Counter()
    exact_paragraph_docs: Counter[str] = Counter()
    heading_docs: Counter[str] = Counter()
    faq_docs: Counter[str] = Counter()
    article_docs: Counter[str] = Counter()
    for text, context in items:
        blocks = editorial_blocks(text)
        all_sentences = [sentence for block in blocks for sentence in sentences(block)]
        metrics.paragraph_count += len(blocks)
        metrics.sentence_count += len(all_sentences)
        metrics.within_paragraph_excess += sum(
            count - 1 for count in Counter(blocks).values() if count > 1
        )
        metrics.within_sentence_excess += sum(
            count - 1 for count in Counter(all_sentences).values() if count > 1
        )
        metrics.max_paragraph = max(metrics.max_paragraph, *(map(len, blocks) or [0]))
        metrics.max_sentence = max(metrics.max_sentence, *(map(len, all_sentences) or [0]))
        manuscript = fragment_text(manuscript_html(text))
        article = fragment_text(article_html(text))
        query = f"{context.center.display_locality} {context.label}"
        metrics.max_locality = max(metrics.max_locality, manuscript.count(context.center.display_locality))
        metrics.max_query = max(metrics.max_query, manuscript.count(query))
        metrics.max_article_locality = max(
            metrics.max_article_locality, article.count(context.center.display_locality)
        )
        norm_article = normalized_template(article, context)
        article_docs[norm_article] += 1
        # Count supported and unsupported authored copy alike.  The only
        # repeated disclosure allowed by release policy lives in explicitly
        # marked source-note nodes outside these rebuilt editorial families;
        # neutral unsupported prose itself must meet the same DF threshold.
        for value in set(all_sentences):
            exact_sentence_docs[value] += 1
        for value in set(blocks):
            exact_paragraph_docs[value] += 1
        for value in set(normalized_template(item, context) for item in all_sentences):
            sentence_docs[value] += 1
        for value in set(normalized_template(item, context) for item in blocks):
            paragraph_docs[value] += 1
        headings = [fragment_text(item) for item in HEADING_RE.findall(manuscript_html(text))]
        for value in set(normalized_template(item, context) for item in headings):
            heading_docs[value] += 1
        for question, _answer in phase1.visible_faqs(text):
            faq_docs[normalized_template(question, context)] += 1
    metrics.normalized_article_duplicates = sum(count > 1 for count in article_docs.values())
    metrics.exact_sentence_max_df = max(exact_sentence_docs.values(), default=0)
    metrics.exact_paragraph_max_df = max(exact_paragraph_docs.values(), default=0)
    metrics.exact_sentence_excess = sum(
        count - 1 for count in exact_sentence_docs.values() if count > 1
    )
    metrics.exact_paragraph_excess = sum(
        count - 1 for count in exact_paragraph_docs.values() if count > 1
    )
    metrics.normalized_sentence_max_df = max(sentence_docs.values(), default=0)
    metrics.normalized_paragraph_max_df = max(paragraph_docs.values(), default=0)
    metrics.normalized_heading_max_df = max(heading_docs.values(), default=0)
    metrics.normalized_faq_max_df = max(faq_docs.values(), default=0)
    metrics.normalized_sentence_excess = sum(count - 1 for count in sentence_docs.values() if count > 1)
    metrics.normalized_paragraph_excess = sum(count - 1 for count in paragraph_docs.values() if count > 1)
    return metrics


def validate_global_metrics(metrics: QualityMetrics) -> None:
    if metrics.within_paragraph_excess or metrics.within_sentence_excess:
        raise ValueError(
            f"within-page duplicate remains: {metrics.within_paragraph_excess}/"
            f"{metrics.within_sentence_excess}"
        )
    if metrics.max_locality > 12 or metrics.max_query > 3 or metrics.max_article_locality > 12:
        raise ValueError(
            f"manuscript repetition threshold: locality={metrics.max_locality}, "
            f"query={metrics.max_query}, article={metrics.max_article_locality}"
        )
    if metrics.max_paragraph > 180 or metrics.max_sentence > 180:
        raise ValueError(
            f"editorial length threshold: p={metrics.max_paragraph}, s={metrics.max_sentence}"
        )
    if metrics.normalized_article_duplicates:
        raise ValueError(
            f"source-normalized whole Article duplicate groups: "
            f"{metrics.normalized_article_duplicates}"
        )
    if metrics.exact_sentence_max_df > 34:
        raise ValueError(f"exact sentence max doc frequency: {metrics.exact_sentence_max_df}")
    if metrics.exact_paragraph_max_df > 34:
        raise ValueError(f"exact paragraph max doc frequency: {metrics.exact_paragraph_max_df}")
    if metrics.normalized_sentence_max_df > 34:
        raise ValueError(f"normalized sentence max doc frequency: {metrics.normalized_sentence_max_df}")
    if metrics.normalized_paragraph_max_df > 34:
        raise ValueError(f"normalized paragraph max doc frequency: {metrics.normalized_paragraph_max_df}")
    if metrics.normalized_heading_max_df > 40:
        raise ValueError(f"normalized H2 max doc frequency: {metrics.normalized_heading_max_df}")
    if metrics.normalized_faq_max_df > 34:
        raise ValueError(f"normalized FAQ max doc frequency: {metrics.normalized_faq_max_df}")


def build_plan(root: Path, reference_dir: Path) -> BuildPlan:
    root = root.resolve()
    centers = phase1.load_centers(reference_dir.resolve())
    extras = load_source_extras(reference_dir.resolve())
    profiles = [
        profile for profile in phase1.discover_profiles(root, centers)
        if profile.kind == "detail"
    ]
    if len(profiles) != DETAIL_COUNT:
        raise ValueError(f"detail count mismatch: {len(profiles)}")
    sitemap_path = (root / "sitemap.xml").resolve()
    authorized = frozenset({*(profile.path.resolve() for profile in profiles), sitemap_path})
    outside_before = external_scope_hash(root, authorized)
    stats = Stats()
    unsupported: Counter[str] = Counter()
    documents: list[PlannedDocument] = []
    projected_for_metrics: list[tuple[str, Context]] = []
    target_urls: set[str] = set()
    descriptions: Counter[str] = Counter()
    for profile in profiles:
        before_bytes = profile.path.read_bytes()
        before, bom = phase1.decode_html(before_bytes)
        center = centers[profile.locality]
        context = Context(profile, center, phase1.CATEGORY_CONFIG[profile.category], extras[profile.locality])
        try:
            after = transform_document(before, context, stats)
            validate_document(before, after, context)
            second = transform_document(after, context, None)
        except Exception as exc:
            raise ValueError(f"{profile.path}: {exc}") from exc
        if second != after:
            raise ValueError(f"in-memory idempotency failed: {profile.path}")
        target_urls.add(canonical_url(after))
        descriptions[render_meta_description(context)] += 1
        projected_for_metrics.append((after, context))
        documents.append(
            PlannedDocument(profile.path, before_bytes, bom + after.encode("utf-8"), "detail")
        )
        if context.unsupported:
            unsupported[profile.category] += 1
    if dict(unsupported) != phase1.EXPECTED_UNSUPPORTED or sum(unsupported.values()) != 106:
        raise ValueError(f"unsupported distribution mismatch: {dict(unsupported)}")
    if len(target_urls) != DETAIL_COUNT:
        raise ValueError(f"detail canonical uniqueness mismatch: {len(target_urls)}")
    duplicate_descriptions = {
        value: count for value, count in descriptions.items() if count > 1
    }
    if duplicate_descriptions:
        raise ValueError(
            f"phase3 meta description exact duplicates: "
            f"{list(duplicate_descriptions.items())[:3]}"
        )
    if stats.counts["location_guide_visible"] != EXPECTED_LOCATION_GUIDES * 9:
        raise ValueError("projected nonblank location-guide page count mismatch")
    if stats.counts["location_guide_blank"] != EXPECTED_BLANK_GUIDES * 9:
        raise ValueError("projected blank location-guide page count mismatch")
    sitemap_bytes = sitemap_path.read_bytes()
    sitemap_text, sitemap_bom = phase1.decode_html(sitemap_bytes)
    sitemap_after = transform_sitemap(sitemap_text, frozenset(target_urls))
    validate_sitemap(sitemap_text, sitemap_after, frozenset(target_urls))
    if transform_sitemap(sitemap_after, frozenset(target_urls)) != sitemap_after:
        raise ValueError("sitemap in-memory idempotency failed")
    documents.append(
        PlannedDocument(sitemap_path, sitemap_bytes, sitemap_bom + sitemap_after.encode("utf-8"), "sitemap")
    )
    metrics = collect_metrics(projected_for_metrics)
    validate_global_metrics(metrics)
    outside_after = external_scope_hash(root, authorized)
    if outside_after != outside_before:
        raise ValueError("external scope changed during read-only preflight")
    return BuildPlan(
        root=root,
        documents=documents,
        stats=stats,
        unsupported=unsupported,
        metrics=metrics,
        external_hash_before=outside_before,
        authorized_paths=authorized,
        idempotent=True,
    )


def atomic_apply(plan: BuildPlan) -> None:
    if external_scope_hash(plan.root, plan.authorized_paths) != plan.external_hash_before:
        raise ValueError("external scope drifted before apply")
    staged: list[tuple[PlannedDocument, Path]] = []
    replaced: list[PlannedDocument] = []
    try:
        for item in plan.changes:
            descriptor, raw = tempfile.mkstemp(
                prefix=f".{item.path.name}.phase3-", dir=item.path.parent
            )
            temporary = Path(raw)
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(item.after)
                handle.flush()
                os.fsync(handle.fileno())
            staged.append((item, temporary))
        for item, temporary in staged:
            os.replace(temporary, item.path)
            replaced.append(item)
        if external_scope_hash(plan.root, plan.authorized_paths) != plan.external_hash_before:
            raise ValueError("external scope changed during apply")
    except Exception:
        for item in reversed(replaced):
            descriptor, raw = tempfile.mkstemp(
                prefix=f".{item.path.name}.phase3-rollback-", dir=item.path.parent
            )
            temporary = Path(raw)
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(item.before)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, item.path)
        raise
    finally:
        for _item, temporary in staged:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def report(plan: BuildPlan, applied: bool, as_json: bool) -> None:
    metrics = plan.metrics
    payload = {
        "mode": "apply" if applied else "check/dry-run",
        "detail_scanned": DETAIL_COUNT,
        "sitemap_scanned": 1,
        "changed_files": len(plan.changes),
        "html_changed": sum(item.kind == "detail" and item.before != item.after for item in plan.documents),
        "sitemap_changed": any(item.kind == "sitemap" and item.before != item.after for item in plan.documents),
        "html_written": bool(applied),
        "unsupported": dict(plan.unsupported),
        "unsupported_total": sum(plan.unsupported.values()),
        "mutations": dict(plan.stats.counts),
        "in_memory_idempotency": plan.idempotent,
        "external_scope_hash": plan.external_hash_before,
        "freshness_date": FRESHNESS_DATE,
        "baseline": BASELINE,
        "projected_quality": {
            "paragraphs": metrics.paragraph_count,
            "sentences": metrics.sentence_count,
            "within_paragraph_excess": metrics.within_paragraph_excess,
            "within_sentence_excess": metrics.within_sentence_excess,
            "max_locality": metrics.max_locality,
            "max_query": metrics.max_query,
            "max_article_locality": metrics.max_article_locality,
            "max_paragraph": metrics.max_paragraph,
            "max_sentence": metrics.max_sentence,
            "normalized_article_duplicate_groups": metrics.normalized_article_duplicates,
            "exact_sentence_max_doc_frequency": metrics.exact_sentence_max_df,
            "exact_paragraph_max_doc_frequency": metrics.exact_paragraph_max_df,
            "exact_sentence_excess": metrics.exact_sentence_excess,
            "exact_paragraph_excess": metrics.exact_paragraph_excess,
            "normalized_sentence_max_doc_frequency": metrics.normalized_sentence_max_df,
            "normalized_paragraph_max_doc_frequency": metrics.normalized_paragraph_max_df,
            "normalized_heading_max_doc_frequency": metrics.normalized_heading_max_df,
            "normalized_faq_max_doc_frequency": metrics.normalized_faq_max_df,
            "normalized_sentence_excess": metrics.normalized_sentence_excess,
            "normalized_paragraph_excess": metrics.normalized_paragraph_excess,
        },
    }
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return
    for key, value in payload.items():
        print(f"{key}: {value}")


def default_reference_dir(root: Path) -> Path:
    return root.parent / "참고자료" / "공통자료"


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    script_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="read-only validation (default)")
    mode.add_argument("--apply", action="store_true", help="atomically apply validated outputs")
    parser.add_argument("--root", type=Path, default=script_root)
    parser.add_argument("--reference-dir", type=Path)
    parser.add_argument("--json", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    root = args.root.resolve()
    reference_dir = (args.reference_dir or default_reference_dir(root)).resolve()
    try:
        plan = build_plan(root, reference_dir)
        if args.apply:
            atomic_apply(plan)
        report(plan, bool(args.apply), bool(args.json))
    except Exception as exc:  # fail closed for CLI use
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
