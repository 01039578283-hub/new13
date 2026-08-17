#!/usr/bin/env python3
"""Safe phase-1 content postprocessor for 소수정예학원.com.

The command is deliberately a dry-run unless ``--apply`` is supplied.  It
uses the authoritative ``센터정보 정리.csv`` and never changes paths, titles,
H1s, links, canonical URLs, robots directives, or sitemap files.

Public integration points for the following schema pass are
``load_inputs()``, ``transform_document()`` and ``build_plan()``.  In
particular, visible FAQ copy is made byte-semantically equal to FAQPage JSON-LD
before this pass returns a document.
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
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence


CATEGORY_CONFIG: dict[str, dict[str, Any]] = {
    "고등수학학원": {"label": "고등 수학학원", "subject": "수학", "prefix": "고", "school": "고등"},
    "고등영어학원": {"label": "고등 영어학원", "subject": "영어", "prefix": "고", "school": "고등"},
    "수학학원": {"label": "수학학원", "subject": "수학", "prefix": "", "school": "전체"},
    "영수학원": {"label": "영수학원", "subject": "영수", "prefix": "", "school": "전체"},
    "영어학원": {"label": "영어학원", "subject": "영어", "prefix": "", "school": "전체"},
    "중등수학학원": {"label": "중등 수학학원", "subject": "수학", "prefix": "중", "school": "중등"},
    "중등영어학원": {"label": "중등 영어학원", "subject": "영어", "prefix": "중", "school": "중등"},
    "초등수학학원": {"label": "초등 수학학원", "subject": "수학", "prefix": "초", "school": "초등"},
    "초등영어학원": {"label": "초등 영어학원", "subject": "영어", "prefix": "초", "school": "초등"},
}

EXPECTED_UNSUPPORTED = {
    "고등수학학원": 17, "고등영어학원": 9, "수학학원": 13,
    "영수학원": 17, "영어학원": 8, "중등수학학원": 13,
    "중등영어학원": 8, "초등수학학원": 13, "초등영어학원": 8,
}

OFFICIAL_ADDRESS_REGIONS = {
    "서울": "서울특별시", "서울특별시": "서울특별시",
    "부산": "부산광역시", "부산광역시": "부산광역시",
    "대구": "대구광역시", "대구광역시": "대구광역시",
    "인천": "인천광역시", "인천광역시": "인천광역시",
    "광주": "광주광역시", "광주광역시": "광주광역시",
    "대전": "대전광역시", "대전광역시": "대전광역시",
    "울산": "울산광역시", "울산광역시": "울산광역시",
    "세종": "세종특별자치시", "세종특별자치시": "세종특별자치시",
    "경기": "경기도", "경기도": "경기도",
    "강원": "강원특별자치도", "강원도": "강원특별자치도",
    "강원특별자치도": "강원특별자치도",
    "충북": "충청북도", "충청북도": "충청북도",
    "충남": "충청남도", "충청남도": "충청남도",
    "경북": "경상북도", "경상북도": "경상북도",
    "경남": "경상남도", "경상남도": "경상남도",
    "전북": "전북특별자치도", "전북특별자치도": "전북특별자치도",
    "전남": "전라남도", "전라남도": "전라남도",
    "제주": "제주특별자치도", "제주특별자치도": "제주특별자치도",
}

OFFICIAL_SOURCE_REGIONS = {
    "서울": "서울특별시", "서울특별시": "서울특별시",
    "부산": "부산광역시", "부산광역시": "부산광역시",
    "대구": "대구광역시", "대구광역시": "대구광역시",
    "인천": "인천광역시", "인천광역시": "인천광역시",
    "광주": "광주광역시", "광주광역시": "광주광역시",
    "대전": "대전광역시", "대전광역시": "대전광역시",
    "울산": "울산광역시", "울산광역시": "울산광역시",
    "경기": "경기도", "경기도": "경기도",
    "강원": "강원특별자치도", "강원도": "강원특별자치도",
    "강원특별자치도": "강원특별자치도",
    "제주": "제주특별자치도", "제주특별자치도": "제주특별자치도",
}

ROOT_META_OLD = (
    "소수정예학원 전국학원 허브입니다. 영어학원·영어학원·영어학원·수학학원과 "
    "고등·중등·초등 영어·수학학원 등 과목·학년별 지역 페이지를 연결해 상담 기준과 "
    "학습관리 방향을 안내합니다."
)
ROOT_META_NEW = (
    "소수정예학원 전국학원 허브입니다. 영수학원·영어학원·수학학원과 고등·중등·초등 "
    "영어·수학 지역 페이지를 연결해 상담 기준과 학습관리 방향을 안내합니다."
)
ROOT_COPY_OLD = (
    "전국학원 페이지는 동네별 학습 환경과 과목별 상담 기준을 연결하는 허브입니다. "
    "수학학원·수학학원·수학학원·수학학원·수학학원·영어학원·영어학원·영어학원·수학학원·"
    "고등 영어·고등 수학·중등 영어·중등 수학·초등 영어처럼 검색 의도가 분명한 페이지를 "
    "중심으로 진단, 학교 자료 반영, 오답관리 기준을 정리합니다."
)
ROOT_COPY_NEW = (
    "전국학원 페이지는 동네별 학습 환경과 과목별 상담 기준을 연결하는 허브입니다. "
    "영수학원·영어학원·수학학원과 고등·중등·초등 영어·수학 페이지를 중심으로 진단, "
    "학교 자료 반영, 오답관리 기준을 정리합니다."
)

# Normalized concepts recovered from the full ``학원...`` / ``학원 ...``
# inventory of all 3,339 rendered detail pages.  These terms describe matters
# a student or parent can reasonably compare.  Keeping the allow-list explicit
# prevents a future cleanup from treating every compound beginning with
# ``학원`` as operator-facing boilerplate.
ACADEMY_ALLOWED_INTENT_TERMS = (
    "학원안내", "학원선택", "학원비교", "학원상담", "학원문의", "학원방문",
    "학원체험", "학원등록", "학원수강신청", "학원예약", "학원대기",
    "학원오리엔테이션", "학원설명회", "학원비", "학원수강료",
    "학원위치", "학원교통", "학원주차", "학원차량", "학원셔틀",
    "학원등원", "학원하원", "학원시간표", "학원일정", "학원수업",
    "학원강의", "학원대면수업", "학원온라인수업", "학원화상수업",
    "학원실시간수업", "학원일대일", "학원맞춤수업", "학원개별지도",
    "학원수준별수업", "학원소수정예", "학원정규반", "학원집중반",
    "학원특강", "학원보충", "학원보강", "학원교재", "학원숙제",
    "학원과제", "학원진도", "학원커리큘럼", "학원프로그램",
    "학원자습실", "학원스터디룸", "학원사물함", "학원강의실",
    "학원교재실", "학원상담실", "학원시설", "학원환경", "학원분위기",
    "학원휴게실", "학원출결", "학원알림장", "학원강사", "학원원장",
    "학원정원", "학원안전관리", "학원보안관리", "학원청결관리",
    "학원방역관리", "학원출입관리", "학원평판", "학원리뷰", "학원후기",
    "학원관리",
)

# These remaining inventory families are either operator/admin tooling or
# unsupported promotional/policy seeds.  They are removed from visible copy,
# FAQ and JSON-LD; source-backed tuition links remain untouched.
ADDITIONAL_IRRELEVANT_SEEDS = (
    "학원개인정보관리", "학원문자발송", "학원자료실", "학원소식",
    "학원알림톡", "학원할인", "학원혜택", "학원환불",
)

DETAIL_META_TOPICS = {
    "고등수학학원": "개념·유형·서술형·오답 재풀이",
    "고등영어학원": "어휘·문법·독해·서술형",
    "수학학원": "개념 이해·조건 해석·계산·검산",
    "영수학원": "영어·수학 진단·주간 계획·오답 재풀이",
    "영어학원": "어휘·문법·독해·학교 시험",
    "중등수학학원": "개념·유형 적용·서술형·오답 재풀이",
    "중등영어학원": "어휘·문법·독해·학교 시험",
    "초등수학학원": "계산·개념·문장 해석·설명 연습",
    "초등영어학원": "듣기·낭독·문장 쓰기·지문 이해",
}

KNOWN_COPY_REPLACEMENTS = {
    ROOT_META_OLD: ROOT_META_NEW,
    ROOT_COPY_OLD: ROOT_COPY_NEW,
    "지역 페이지는 검색어에 바로 답해야 합니다": "지역 페이지는 학부모의 질문에 구체적으로 답해야 합니다",
    "고등 과정 과정": "고등 과정",
    "초등초등학생": "초등학생",
    "학부모 상담 준비 상담 전에": "학부모 상담 전에",
    "학원 상담 질문 상담 전에": "학원 상담 전에",
    "내신 수학은 학교 수업 기록에서 시작합니다이 실제 수업에서 지켜지는지":
        "내신 수학은 학교 수업 기록에서 시작한다는 기준이 실제 수업에서 지켜지는지",
    "등록된 센터별 교습비 자료를 새 창에서 확인합니다.":
        "센터에서 제공한 공통 교습비 자료이며, 최신 금액은 상담 전에 확인합니다.",
    "센터별 교습비 링크를 확인할 수 있으며":
        "센터에서 제공한 공통 교습비 자료 링크를 확인할 수 있으며",
    "학습집중관리을 중시하는": "학습 집중도를 중시하는",
    "학원출입관리을 중시하는": "안전한 출입 절차를 중시하는",
    "나누는 방식이 동시에 확인해 두는 편이 좋습니다.":
        "나누는 방식을 함께 확인해 두는 편이 좋습니다.",
    "상담 안내와 관계없이 ": "",
    "보다 실제로 시작하는 데 걸리는 시간이 실제로 확인되는지":
        "보다 시작하는 데 걸리는 시간이 실제로 확인되는지",
    "보다 실제 완료율과 수정 기록이 실제로 확인되는지":
        "보다 완료율과 수정 기록이 실제로 확인되는지",
    "학원에서는 제공 자료에는": "학원의 제공 자료에는",
}

PARTICLE_TERMS = (
    "와와학습코칭학원", "학습관리", "오답노트", "서술형", "학부모",
    "남기기", "이해도", "그래프", "교습비", "관리", "시험", "오답",
    "목적", "영어", "태그", "안내", "공부", "찾기", "암기", "준비",
    "비교", "설계", "순서", "지표", "이유", "학원", "학교", "층",
    # Legacy seed copy contains one-off topic+particle combinations.  Keep the
    # repair whitelist explicit so ordinary Korean prose is never rewritten by
    # a broad suffix heuristic.
    "학습우선순위", "개별학습관리", "학습목표", "학습진도관리",
    "학원방역관리", "학습일정관리", "시험범위관리", "학습노트",
    "학습문제관리", "내신과제관리", "학습준비도검사", "학습태도관리",
    "시험준비", "학원진도", "내신집중관리", "학습결과", "학습관리표",
    "학습유형검사", "입시설명회", "학습보고서", "성적관리",
    "학원분위기", "학습진척도", "학습통계", "학습설계", "학원주차",
    "학습심화", "입시준비", "학원비", "학원소수정예", "학습태도",
    "학습효율", "학원자습실", "입시상담", "학습강점",
    "입시컨설팅학원", "온라인수업", "오후수업", "학원등원",
    "학원환불", "개별관리반", "소그룹수업", "학원방문",
    *ACADEMY_ALLOWED_INTENT_TERMS,
)

SEO_SEARCH_RE = re.compile(
    r"“(?P<term>[^”]+)”[을를] 검색한 학부모가 (?P<context>[^.]+?) 안내 페이지에서 "
    r"먼저 확인할 답은 용어의 크기보다 자녀 학습에 적용되는 구체적인 절차입니다\."
)
SEO_EMPHASIS_RE = re.compile(
    r"(?P<term>[^.!?]{1,50}?)이라는 검색어가 강조되더라도 "
    r"(?P<geo>[^.!?]{2,80}?) 학부모의 결정 기준은 "
    r"(?P<basis>[^.!?]{2,120}?)[이가] 수업 안에서 어떻게 구현되는지여야 합니다\."
)
SEO_GENERIC_RE = re.compile(
    r"(?P<term>[가-힣A-Za-z0-9· ]{1,40}?)(?:으로|로) 검색한 학부모가 "
    r"상담 전에 확인할 질문과 학습 기록을 함께 정리했습니다\."
)
SEO_INFO_RE = re.compile(
    r"(?P<topic>[^.!?<>]{2,100}?) 정보를 검색한 학부모가 "
    r"(?:가장 먼저|먼저|우선|처음에는) 확인할 내용은"
)
SCHOOL_REFERENCE_RE = re.compile(
    r"(?P<lead>상담에서는 )(?P<school>[^<>]{1,80}?)[을를] "
    r"확인 가능한 학교 참고 자료 중 하나로 살펴보며, "
    r"과거 경향만으로 현재 시험을 단정하지 않습니다\."
)
GRADE_TOKEN_RE = re.compile(
    r"(?:초[1-6]|[중고][1-3]|초등\s*[1-6]학년|중등\s*[1-3]학년|고등\s*[1-3]학년)"
)
GRADE_SERVICE_CLAIM_RE = re.compile(
    r"(?:학생.{0,24}(?:맞는|대상|위한).{0,24}(?:수업|학습|관리)|"
    r"(?:수업|학습)\s*(?:가능|대상)\s*학년|"
    r"학년.{0,16}(?:수업|학습).{0,16}(?:가능|제공|운영))"
)
# Local detail copy should answer the reader directly.  Any use of ``검색``
# here is manuscript narration (including variants such as 검색 후/검색자/
# 검색 결과); education articles are outside this detail-only gate.
META_LANGUAGE_RE = re.compile(r"검색|SEO|키워드", re.I)
FALLBACK_QUOTE_TAIL_RE = re.compile(
    r"[가-힣 ]{1,60}(?:"
    r"상담에서 어떤 내용을 확인해야 하나요|"
    r"상담 전에 준비할 자료는 무엇인가요|"
    r"학습 계획은 어떤 기준으로 비교해야 하나요|"
    r"상담 안내는 어떤 기준으로 확인해야 하나요"
    r")\?”라고 확인하면 됩니다\."
)
QUOTE_TAIL_RE = re.compile(r"[?.]”라고")
# Three legacy paragraph families can become syntactically stranded after a
# search-narration sentence is removed.  Keep their signatures narrow: these
# are copy repairs, not a general attempt to rewrite every use of ``특히`` or
# ``답은``.
TEACHER_EXAMPLE_RE = re.compile(
    r"여기서는 (?P<grade>초등 [1-6]학년)의 교사가 보여 준 예시는 "
    r"차분히 따라가는 편이면서도 (?P<traits>[^.!?<>]{1,360}?) "
    r"학생을 사례로 삼았습니다\."
)
ORPHAN_ESPECIALLY_RE = re.compile(
    r"특히 (?P<body>[^.!?<>]{1,360}?학생이라면 문제 수를 늘리기 전에 "
    r"틀린 이유를 분류하고, 한 주 안에 다시 풀 수 있는 복습 간격을 "
    r"만드는 것이 먼저입니다\.)"
)
ORPHAN_ANSWER_RE = re.compile(
    r"답은 (?P<grade>[^.!?<>]{1,24}?) 학생의 현재 상태가 "
    r"(?P<condition>[^.!?<>]{1,360}?) 모습이라면 한 과목씩 따로 보기보다 "
    r"영어·수학 학습 기록을 함께 비교해 보는 것이 좋다는 것입니다\."
)
# Terms aimed at academy owners/operators rather than a student or parent
# choosing lessons.  They came from legacy keyword seeds and must not leak
# into reader-facing local guidance.
IRRELEVANT_SEEDS = (
    "학원개원", "학원창업", "학원운영", "학원운영자", "학원매출관리", "학원수납관리",
    "학원고객관리시스템", "학원고객관리", "학원회원관리", "학원수강생관리",
    "학원문서관리", "학원데이터관리", "학원예약관리", "학원상담관리",
    "학원결제시스템", "학원결제관리", "학원미납관리", "학원관리프로그램",
    "학원관리솔루션", "학원관리앱", "학원출결앱", "학원직원",
    "학원상담직원", "학원코디네이터", "학원데스크", "학원행정",
    "학원브랜드", "학원프로모션", "학원이벤트", "학원모집",
    "학원온라인등록", "학원이전", "학원전자계약", "학원매니저", "학원공지",
    "학원재등록", "학원휴원",
    *ADDITIONAL_IRRELEVANT_SEEDS,
)
IRRELEVANT_SEED_RE = re.compile(
    r"학원\s*(?:"
    r"개원|창업|운영(?:자)?|매출\s*관리|수납\s*관리|"
    r"고객\s*관리(?:\s*시스템)?|회원\s*관리|수강생\s*관리|"
    r"문서\s*관리|데이터\s*관리|예약\s*관리|상담\s*관리|"
    r"결제\s*(?:시스템|관리)|미납\s*관리|관리\s*(?:프로그램|솔루션|앱)|"
    r"출결\s*앱|직원|상담\s*직원|코디네이터|데스크|행정|브랜드|"
    r"프로모션|이벤트|모집|온라인\s*등록|이전|전자\s*계약|매니저|공지|"
    r"재\s*등록|휴원|개인\s*정보\s*관리|문자\s*발송|자료실|소식|"
    r"알림\s*톡|할인|혜택|환불"
    r")",
    re.I,
)
if set(ACADEMY_ALLOWED_INTENT_TERMS) & set(IRRELEVANT_SEEDS):
    raise RuntimeError("학원 용어 allow/irrelevant 분류가 겹칩니다")
SCRIPT_JSON_RE = re.compile(
    r'(<script\b[^>]*type=["\']application/ld\+json["\'][^>]*>)(.*?)(</script>)',
    re.I | re.S,
)
URL_KEYS = {"@id", "url", "item", "contentUrl", "sameAs", "image"}
SOURCE_MARKER = 'data-source-status="unconfirmed-grade"'


@dataclass(frozen=True)
class Center:
    locality: str
    slug: str
    region: str
    district: str
    official_region: str
    display_district: str
    display_locality: str
    center_name: str
    tuition_url: str
    registration: str
    address: str
    grades: Mapping[str, tuple[str, ...]]
    schools: Mapping[str, tuple[str, ...]]

    @property
    def all_schools(self) -> tuple[str, ...]:
        return unique((*self.schools["고등"], *self.schools["중등"], *self.schools["초등"]))

    @property
    def display_geo(self) -> str:
        return " ".join(x for x in (self.official_region, self.display_district, self.display_locality) if x)


@dataclass(frozen=True)
class Profile:
    kind: str
    path: Path
    category: str = ""
    locality: str = ""


@dataclass
class Stats:
    counts: Counter[str] = field(default_factory=Counter)

    def add(self, key: str, amount: int = 1) -> None:
        if amount:
            self.counts[key] += amount


@dataclass(frozen=True)
class PlannedDocument:
    path: Path
    before: bytes
    after: bytes


@dataclass
class BuildPlan:
    root: Path
    documents: list[PlannedDocument]
    scanned: Counter[str]
    stats: Stats
    unsupported: Counter[str]
    idempotent: bool

    @property
    def changes(self) -> list[PlannedDocument]:
        return [item for item in self.documents if item.before != item.after]

    def output_bytes(self, path: Path) -> bytes:
        resolved = path.resolve()
        for item in self.documents:
            if item.path.resolve() == resolved:
                return item.after
        raise KeyError(path)


def clean(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def unique(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(value for value in values if value))


def compact_slug(value: str) -> str:
    return unicodedata.normalize("NFC", re.sub(r"\s+", "", clean(value)))


def canonical_headers(row: Mapping[str, str]) -> dict[str, str]:
    return {re.sub(r"\s+", "", key): value for key, value in row.items()}


def split_values(value: str) -> tuple[str, ...]:
    return unique(clean(item) for item in re.split(r"[,/\n]+", value or ""))


def school_values(value: str) -> tuple[str, ...]:
    """Parse explicit names while preserving the worksheet's original order."""
    result: list[str] = []
    suffix = re.compile(r"(?:초등학교|중학교|고등학교|여중|여고|외고|부고|초|중|고)$")
    for group in re.split(r"[,/|·.;\r\n]+", value or ""):
        group = clean(group)
        if not group or re.fullmatch(r"지역\s*내\s*모든\s*(?:초등|중|고등)?학교\s*가능", group):
            continue
        tokens = group.split()
        if len(tokens) > 1 and all(suffix.search(token) for token in tokens):
            result.extend(tokens)
        else:
            result.append(group)
    return unique(result)


def official_region(region: str, address: str) -> str:
    """Expand the service taxonomy without conflating it with physical address.

    The worksheet's specific buckets (서울/경기/metropolitan cities/강원/제주)
    are authoritative service-area taxonomy even when a linked centre sits
    across an administrative border.  Only the deliberately aggregated
    충청/경상/전라 buckets need disambiguation from the physical CSV address;
    this also resolves 세종 rows grouped under 충청.
    """
    if region not in {"충청", "경상", "전라"}:
        result = OFFICIAL_SOURCE_REGIONS.get(region)
        if result:
            return result
        raise ValueError(f"알 수 없는 원자료 지역 taxonomy: {region!r}")
    prefix = clean(address).split(" ", 1)[0]
    result = OFFICIAL_ADDRESS_REGIONS.get(prefix)
    if not result:
        raise ValueError(f"광역 지역을 주소에서 판별할 수 없습니다: {region!r} / {address!r}")
    return result


def display_locality(locality: str, region: str, district: str) -> str:
    prefixes = (re.sub(r"(?:시|군|구)$", "", clean(district)), clean(region))
    for prefix in unique(prefixes):
        match = re.fullmatch(rf"{re.escape(prefix)}\s+(.+)", clean(locality))
        if match:
            return clean(match.group(1))
    return clean(locality)


def load_centers(reference_dir: Path) -> dict[str, Center]:
    source = reference_dir / "센터정보 정리.csv"
    if not source.is_file():
        raise FileNotFoundError(source)
    with source.open("r", encoding="utf-8-sig", newline="") as handle:
        raw_rows = list(csv.DictReader(handle))
    if len(raw_rows) != 371:
        raise ValueError(f"센터정보 행은 371개여야 합니다: {len(raw_rows)}")
    result: dict[str, Center] = {}
    for raw in raw_rows:
        row = canonical_headers(raw)
        locality = clean(row["근처수업가능동네"])
        region = clean(row["지역"])
        district = clean(row["시or구"])
        address = clean(row["센터주소"])
        official = official_region(region, address)
        center = Center(
            locality=locality,
            slug=compact_slug(locality),
            region=region,
            district=district,
            official_region=official,
            display_district="" if official == "세종특별자치시" else district,
            display_locality=display_locality(locality, region, district),
            center_name=clean(row["센터명"]),
            tuition_url=clean(row["센터교습비"]),
            registration=clean(row["교육지원청등록번호"]),
            address=address,
            grades={
                "영어": split_values(row["가능학년(영어)"]),
                "수학": split_values(row["가능학년(수학)"]),
            },
            schools={
                "초등": school_values(row["타깃학교(초)"]),
                "중등": school_values(row["타깃학교(중)"]),
                "고등": school_values(row["타깃학교(고)"]),
            },
        )
        if center.slug in result:
            raise ValueError(f"중복 동네 slug: {center.slug}")
        result[center.slug] = center
    return result


def discover_profiles(root: Path, centers: Mapping[str, Center]) -> list[Profile]:
    national = root / "전국학원"
    actual_categories = {p.name for p in national.iterdir() if p.is_dir()}
    expected_categories = set(CATEGORY_CONFIG)
    if actual_categories != expected_categories:
        raise ValueError(f"전국학원 category 불일치: {sorted(actual_categories ^ expected_categories)}")
    result = [Profile("national-root", national / "index.html")]
    expected_slugs = set(centers)
    for category in CATEGORY_CONFIG:
        category_dir = national / category
        result.append(Profile("hub", category_dir / "index.html", category))
        detail_dirs = {p.name for p in category_dir.iterdir() if p.is_dir()}
        if detail_dirs != expected_slugs:
            raise ValueError(f"{category} 상세 경로 불일치: {sorted(detail_dirs ^ expected_slugs)[:10]}")
        for slug in sorted(expected_slugs):
            result.append(Profile("detail", category_dir / slug / "index.html", category, slug))
    education = root / "교육정보"
    education_dirs = sorted(p for p in education.iterdir() if p.is_dir())
    if len(education_dirs) != 25:
        raise ValueError(f"교육정보 상세는 25개여야 합니다: {len(education_dirs)}")
    result.extend(Profile("education", p / "index.html") for p in education_dirs)
    missing = [str(profile.path) for profile in result if not profile.path.is_file()]
    if missing:
        raise FileNotFoundError("필수 HTML 누락: " + ", ".join(missing[:5]))
    if len(result) != 3374:
        raise ValueError(f"대상 HTML은 3,374개여야 합니다: {len(result)}")
    return result


def has_batchim(value: str) -> bool:
    for char in reversed(clean(value)):
        if "가" <= char <= "힣":
            return (ord(char) - ord("가")) % 28 != 0
    return False


def final_jongseong(value: str) -> int:
    """Return the Hangul jongseong index of the final syllable (0 if none)."""
    for char in reversed(clean(value)):
        if "가" <= char <= "힣":
            return (ord(char) - ord("가")) % 28
    return 0


def correct_particle(value: str, particle: str) -> str:
    batchim = has_batchim(value)
    if particle in {"으로", "로"}:
        # A vowel or final ㄹ (jongseong index 8) takes 로; every other final
        # consonant takes 으로: 학원으로, 관리로, 길로.
        return "로" if final_jongseong(value) in {0, 8} else "으로"
    pairs = {
        "을": ("을", "를"), "를": ("을", "를"),
        "이": ("이", "가"), "가": ("이", "가"),
        "은": ("은", "는"), "는": ("은", "는"),
        "과": ("과", "와"), "와": ("과", "와"),
        "이라는": ("이라는", "라는"), "라는": ("이라는", "라는"),
    }
    return pairs[particle][0 if batchim else 1]


PARTICLES = ("이라는", "라는", "으로", "로", "을", "를", "이", "가", "은", "는", "과", "와")
SCHOOL_SUFFIXES = (
    *PARTICLES, "처럼", "에서", "에게", "까지", "부터", "도", "만", "의", "등",
)


def fix_term_particles(text: str, terms: Iterable[str], stats: Stats | None) -> str:
    for term in sorted(unique(terms), key=len, reverse=True):
        pattern = re.compile(
            rf"(?<![가-힣A-Za-z0-9]){re.escape(term)}"
            rf"(?P<p>{'|'.join(map(re.escape, PARTICLES))})(?![가-힣A-Za-z0-9])"
        )
        def replace(match: re.Match[str], term: str = term) -> str:
            particle = match.group("p")
            fixed = correct_particle(term, particle)
            if fixed == particle:
                return match.group(0)
            if stats:
                stats.add("문법_조사")
            return term + fixed
        text = pattern.sub(replace, text)
    return text


def replace_counted(text: str, old: str, new: str, key: str, stats: Stats | None) -> str:
    count = text.count(old)
    if count and old != new:
        text = text.replace(old, new)
        if stats:
            stats.add(key, count)
    return text


def protect_facts(text: str, facts: Iterable[str]) -> tuple[str, dict[str, str]]:
    placeholders: dict[str, str] = {}
    raw_facts = unique(clean(x) for x in facts)
    # Visible HTML encodes ampersands while JSON-LD keeps the same CSV literal
    # raw.  Protect both byte forms so geography normalization cannot rewrite
    # ``제공 주소``/FAQ address text such as ``경기 ... P&amp;P...``.
    protected_forms = unique(
        form
        for fact in raw_facts
        for form in (fact, html.escape(fact, quote=False))
    )
    for index, fact in enumerate(sorted(protected_forms, key=len, reverse=True)):
        if not fact or fact not in text:
            continue
        token = f"\ue000FACT{index:03d}\ue001"
        text = text.replace(fact, token)
        placeholders[token] = fact
    return text, placeholders


def restore_facts(text: str, placeholders: Mapping[str, str]) -> str:
    for token, fact in placeholders.items():
        text = text.replace(token, fact)
    return text


def region_replacements(center: Center) -> tuple[tuple[str, str], ...]:
    raw_full = " ".join(x for x in (center.region, center.district, center.locality) if x)
    official_full = " ".join(x for x in (center.official_region, center.display_district, center.display_locality) if x)
    interim = " ".join(x for x in (center.official_region, center.district, center.locality) if x)
    # Some legacy templates leaked the whitespace-compacted URL locality into
    # copy (e.g. ``당진읍내동``).  Normalize the administrative join without
    # changing that URL directory or the protected title/H1.
    raw_compact_full = " ".join(x for x in (center.region, center.district, center.slug) if x)
    interim_compact = " ".join(x for x in (center.official_region, center.district, center.slug) if x)
    raw_short = " ".join(x for x in (center.region, center.district) if x)
    official_short = " ".join(x for x in (center.official_region, center.display_district) if x)
    pairs = [
        (raw_full, official_full), (interim, official_full),
        (raw_compact_full, official_full), (interim_compact, official_full),
        (raw_short, official_short),
    ]
    return tuple((old, new) for old, new in pairs if old and old != new)


def replace_region(text: str, center: Center, isolated: bool, stats: Stats | None) -> str:
    for old, new in sorted(region_replacements(center), key=lambda pair: len(pair[0]), reverse=True):
        text = replace_counted(text, old, new, "지역_공식표기", stats)
    if isolated and center.region != center.official_region:
        pattern = re.compile(rf"(?<![가-힣]){re.escape(center.region)}(?![가-힣])")
        text, count = pattern.subn(center.official_region, text)
        if stats:
            stats.add("지역_공식표기", count)
    return text


def school_order(center: Center, config: Mapping[str, Any]) -> tuple[str, ...]:
    stage = config["school"]
    if stage == "전체":
        return center.all_schools
    return center.schools[stage]


def school_join_pairs(center: Center) -> tuple[tuple[str, str], ...]:
    result: list[tuple[str, str]] = []
    groups = (center.schools["고등"], center.schools["중등"], center.schools["초등"])
    for left in groups:
        for right in groups:
            if left is right or not left or not right:
                continue
            result.append((left[-1] + right[0], left[-1] + "·" + right[0]))
            result.append((left[-1] + " " + right[0], left[-1] + "·" + right[0]))
    return tuple(dict.fromkeys(result))


def replace_school_token(text: str, old: str, new: str, stats: Stats | None) -> str:
    pattern = re.compile(
        rf"(?<![가-힣A-Za-z0-9]){re.escape(old)}"
        rf"(?P<p>{'|'.join(map(re.escape, SCHOOL_SUFFIXES))})?(?![가-힣A-Za-z0-9])"
    )
    def replace(match: re.Match[str]) -> str:
        particle = match.group("p") or ""
        if stats:
            stats.add("학교_학년혼합")
        suffix = correct_particle(new, particle) if particle in PARTICLES else particle
        return new + suffix
    return pattern.sub(replace, text)


def replace_schools(text: str, center: Center, config: Mapping[str, Any], stats: Stats | None) -> str:
    for old, new in school_join_pairs(center):
        text = replace_counted(text, old, new, "학교_붙임", stats)
    allowed = school_order(center, config)
    allowed_set = set(allowed)
    forbidden = [school for school in center.all_schools if school not in allowed_set]
    for index, old in enumerate(sorted(unique(forbidden), key=len, reverse=True)):
        if allowed:
            digest = hashlib.sha256((old + center.slug).encode("utf-8")).digest()
            new = allowed[int.from_bytes(digest[:2], "big") % len(allowed)]
        else:
            new = "해당 학교"
        text = replace_school_token(text, old, new, stats)
    text = fix_term_particles(text, (*center.all_schools, *allowed), stats)
    for school in allowed:
        # A wrong-level school can be source-mapped to a school already named
        # beside it, producing copy such as ``문현고와 문현고가 포함``.
        # Collapse the pair while retaining the suffix carried by the second
        # occurrence.  This is deliberately limited to authoritative names.
        pair_pattern = re.compile(
            rf"(?<![가-힣A-Za-z0-9]){re.escape(school)}(?:과|와)\s*"
            rf"{re.escape(school)}(?P<suffix>{'|'.join(map(re.escape, SCHOOL_SUFFIXES))})?"
            rf"(?![가-힣A-Za-z0-9])"
        )
        def collapse_pair(match: re.Match[str], school: str = school) -> str:
            suffix = match.group("suffix") or ""
            if suffix in PARTICLES:
                suffix = correct_particle(school, suffix)
            if stats:
                stats.add("학교_중복제거")
            return school + suffix
        text = pair_pattern.sub(collapse_pair, text)
        text = re.sub(
            rf"{re.escape(school)}(?:(?:·|\s)+{re.escape(school)})+",
            school,
            text,
        )
    return text


def canonical_grade(token: str) -> str:
    token = clean(token)
    short = re.fullmatch(r"([초중고])([1-6])", token)
    if short:
        return short.group(1) + short.group(2)
    long = re.fullmatch(r"(초등|중등|고등)\s*([1-6])학년", token)
    if long:
        return {"초등": "초", "중등": "중", "고등": "고"}[long.group(1)] + long.group(2)
    return token


def render_grade_like(original: str, grade: str) -> str:
    if "학년" not in original:
        return grade
    label = {"초": "초등", "중": "중등", "고": "고등"}[grade[0]]
    return f"{label} {grade[1:]}학년"


def closest_grade(original: str, allowed: Sequence[str]) -> str:
    same_stage = [grade for grade in allowed if grade[:1] == original[:1]]
    candidates = same_stage or list(allowed)
    if not candidates:
        return ""
    try:
        number = int(original[1:])
        return min(candidates, key=lambda grade: (abs(int(grade[1:]) - number), int(grade[1:])))
    except (TypeError, ValueError):
        return candidates[0]


def support_for_sentence(
    sentence: str, center: Center, config: Mapping[str, Any],
) -> tuple[str, ...]:
    subjects = ("영어", "수학") if config["subject"] == "영수" else (config["subject"],)
    support = {subject: requested_grades(center, config, subject) for subject in subjects}
    named = [subject for subject in subjects if subject in sentence]
    if len(subjects) > 1 and ("영수" in sentence or len(named) != 1):
        values = set(support[subjects[0]])
        for subject in subjects[1:]:
            values &= set(support[subject])
        return tuple(grade for grade in support[subjects[0]] if grade in values)
    if named:
        return support[named[0]]
    return unique(grade for subject in subjects for grade in support[subject])


def align_visible_grades(
    text: str,
    center: Center,
    config: Mapping[str, Any],
    stats: Stats | None,
    *,
    page_has_claim_error: bool = False,
) -> str:
    """Keep every reader-visible student grade inside the CSV support set.

    Unsupported profiles are rebuilt wholesale later and are intentionally
    skipped here.  For supported profiles, subject wording in each sentence
    selects the same union/intersection contract as the release auditor.
    """
    if unsupported_page(center, config) or not GRADE_TOKEN_RE.search(text):
        return text
    parts = re.split(r"(?<=[.!?])", text)
    changed = 0
    for index, sentence in enumerate(parts):
        if not GRADE_TOKEN_RE.search(sentence):
            continue
        if not page_has_claim_error and not GRADE_SERVICE_CLAIM_RE.search(sentence):
            continue
        allowed = support_for_sentence(sentence, center, config)
        allowed_set = set(allowed)

        def replace(match: re.Match[str]) -> str:
            nonlocal changed
            original = match.group(0)
            grade = canonical_grade(original)
            if grade in allowed_set:
                return original
            replacement = closest_grade(grade, allowed)
            changed += 1
            if not replacement:
                # A combined English-math sentence can have no grade in the
                # intersection even though both subjects exist.  Removing the
                # grade qualifier is safer than asserting either subject.
                return ""
            return render_grade_like(original, replacement)

        parts[index] = GRADE_TOKEN_RE.sub(replace, sentence)
    result = "".join(parts)
    # Replacement of two unsupported tokens by the same nearest supported
    # grade must not leave ``고2·고2`` in the sentence.
    def dedupe_list(match: re.Match[str]) -> str:
        values = unique(GRADE_TOKEN_RE.findall(match.group(0)))
        return "·".join(values)
    grade_list = rf"{GRADE_TOKEN_RE.pattern}(?:·{GRADE_TOKEN_RE.pattern})+"
    result = re.sub(grade_list, dedupe_list, result)
    result = re.sub(r"\s{2,}", " ", result)
    if changed and stats:
        stats.add("학년_본문원자료동기화", changed)
    return result


def reader_language_cleanup(
    text: str,
    center: Center,
    config: Mapping[str, Any],
    stats: Stats | None,
) -> str:
    """Remove search-engine narration and academy-operator keyword seeds."""
    # Preserve the useful part of a few FAQ sentences instead of deleting the
    # entire sentence merely because a legacy seed was used as its label.
    # These substitutions state only a consultation/checking action; they do
    # not invent a service, policy or source fact.
    seed = IRRELEVANT_SEED_RE.pattern
    contextual_replacements = (
        # ``확인 전에는`` introduced an otherwise useful sentence in the
        # legacy seed copy.  Removing only that label preserves the concrete
        # school/study advice instead of leaving a one-sentence paragraph
        # without its example.
        (rf"(?:{seed})\s+확인\s+전에는,?\s*", ""),
        (rf"(?:{seed})\s+확인\s+시\s*", "상담 시 "),
        (rf"(?:{seed})[을를]\s+함께\s+살펴보는", "상담을 준비하는"),
        (rf"(?:{seed})[을를]\s+(?:물어볼|문의할)\s+때는?", "상담할 때는"),
        (rf"(?:{seed})[을를]\s+확인하는\s+과정도", "상담을 진행할 때도"),
        (rf"(?:{seed})\s+안내와\s+관계없이\s*", ""),
        (rf"(?:{seed})[을를]\s+강조하는\s+안내", "등록 조건을 강조하는 안내"),
        (
            rf"(?:{seed})(?:과|와)\s+학교\s+대비를\s+함께\s+설명할\s+때는",
            "학교 대비를 설명할 때는",
        ),
        (rf"(?:{seed})까지\s+포함한", "등록 조건까지 포함한"),
        (rf"(?:{seed})(?:과|와)\s+관련된\s+설명", "상담에서 들은 설명"),
        (
            rf"(?P<academy>(?:(?:해당|이)\s+)?[가-힣· ]{{1,30}}학원)의\s+"
            rf"(?:{seed})\s+상담\s+기준으로\s+보면,\s*",
            r"\g<academy> 안내에서는 ",
        ),
        (rf"[‘'“\"]?(?:{seed})[’'”\"]?\s+관련\s+설명", "상담 조건 설명"),
        (rf"[‘'“\"]?(?:{seed})[’'”\"]?\s+설명", "상담 조건 설명"),
        (rf"[‘'“\"]?(?:{seed})[’'”\"]?\s+단계", "등록 상담 단계"),
        (rf"(?:{seed})[을를]\s+문의할\s+때", "상담 전에"),
        (rf"(?:{seed})\s+안내", "상담 안내"),
        (rf"(?:{seed})만\s+따로", "특정 홍보 문구만 따로"),
    )
    contextual_count = 0
    for pattern, replacement in contextual_replacements:
        text, count = re.subn(pattern, replacement, text, flags=re.I)
        contextual_count += count
    if contextual_count and stats:
        stats.add("무관seed_문맥자연화", contextual_count)
    if not (META_LANGUAGE_RE.search(text) or IRRELEVANT_SEED_RE.search(text)):
        return text
    bad = re.compile(
        rf"(?:{META_LANGUAGE_RE.pattern})|(?:{IRRELEVANT_SEED_RE.pattern})",
        re.I,
    )
    reader_locality = center.display_locality
    headings = (
        f"{reader_locality} {config['label']} 상담에서 확인할 내용",
        f"{reader_locality} 학습 관리 흐름을 확인하는 질문",
        f"{reader_locality} 등록 전에 기록할 상담 기준",
        f"{reader_locality} 상담 답변을 비교하는 기준",
        f"{reader_locality} 학습 계획을 확인하는 순서",
    )
    sentences = (
        f"{reader_locality} {config['label']} 상담에서는 학생의 현재 자료를 바탕으로 "
        "진단·과제·재점검이 어떻게 이어지는지 확인해야 합니다.",
        f"{reader_locality} 상담 설명이 실제 주간 계획과 피드백에 반영되는 시점과 "
        "담당자를 구체적으로 물어보세요.",
        f"{reader_locality}에서 등록 전에는 학생의 학습 기록과 학교 일정을 기준으로 "
        "확인 항목을 정리하는 편이 좋습니다.",
        f"{reader_locality} 상담에는 최근 시험지와 교재를 준비하고 첫 계획과 "
        "재확인 날짜가 구체적으로 남는지 살펴보세요.",
        f"{reader_locality}에서는 학교 일정과 과제량을 함께 놓고 학생이 실천할 수 있는 "
        "주간 계획인지 확인하는 것이 중요합니다.",
        f"{reader_locality}에서 설명받은 내용은 날짜와 담당자를 기록해 실제 운영과 "
        "차이가 없는지 다음 상담에서 다시 확인하세요.",
        f"{reader_locality} 학습 계획은 수업 전후에 복습할 시간까지 포함해 "
        "현실적으로 이어질 수 있는지 살펴보아야 합니다.",
    )
    questions = (
        f"{reader_locality} {config['label']} 상담에서 어떤 내용을 확인해야 하나요?",
        f"{reader_locality} 상담 전에 준비할 자료는 무엇인가요?",
        f"{reader_locality} 학습 계획은 어떤 기준으로 비교해야 하나요?",
    )
    pieces = re.split(r"(?<=[.!?])", text)
    has_good_sentence = any(
        piece.strip() and not bad.search(piece) and re.search(r"[.!?]", piece)
        for piece in pieces
    )
    replaced = 0
    emitted_fallback = False
    for index, piece in enumerate(pieces):
        if not bad.search(piece):
            continue
        leading = re.match(r"\s*", piece).group(0)
        trailing = re.search(r"\s*$", piece).group(0)
        core = piece[len(leading):len(piece) - len(trailing) if trailing else None]
        choice = hashlib.sha256(core.encode("utf-8")).digest()[0]
        if core.rstrip().endswith("?"):
            replacement = (
                f"{reader_locality} 상담 안내는 어떤 기준으로 확인해야 하나요?"
                if IRRELEVANT_SEED_RE.search(core)
                else questions[choice % len(questions)]
            )
        elif not re.search(r"[.!?]", core):
            replacement = headings[choice % len(headings)]
        elif has_good_sentence:
            replacement = ""
        elif not emitted_fallback:
            replacement = sentences[choice % len(sentences)]
            emitted_fallback = True
        else:
            replacement = ""
        pieces[index] = leading + replacement + trailing
        replaced += 1
    result = "".join(pieces)
    for sentence in sentences:
        result = re.sub(rf"(?:{re.escape(sentence)}\s*){{2,}}", sentence + " ", result)
    # A legacy quoted question can be split at ``?`` before cleanup.  If its
    # first half is replaced by one of our fallback questions, retaining the
    # old closing quote (``?”라고 ...``) produces a malformed hybrid sentence.
    # Replace only the exact fallback questions generated above.
    self_quote_count = 0
    for fallback_question in (*questions, f"{reader_locality} 상담 안내는 어떤 기준으로 확인해야 하나요?"):
        result, count = re.subn(
            re.escape(fallback_question) + r"”라고 확인하면 됩니다\.",
            "적용 기간과 포함 항목을 문서로 받은 뒤 변경 조건도 함께 확인하세요.",
            result,
        )
        self_quote_count += count
    result = re.sub(r"[ \t]{2,}", " ", result)
    if replaced and stats:
        stats.add("검색메타_독자문장제거", replaced)
    if self_quote_count and stats:
        stats.add("상담질문_자연화", self_quote_count)
    return result


def apply_copy_fixes(text: str, stats: Stats | None) -> str:
    for old, new in KNOWN_COPY_REPLACEMENTS.items():
        text = replace_counted(text, old, new, "확정_문장", stats)

    def seo_search(match: re.Match[str]) -> str:
        if stats:
            stats.add("메타SEO_자연화")
        return (
            f"{match.group('context')} 안내를 살펴볼 때는 “{match.group('term')}”이라는 명칭보다 "
            "자녀 학습에 적용되는 구체적인 절차를 먼저 확인해야 합니다."
        )
    text = SEO_SEARCH_RE.sub(seo_search, text)

    def seo_emphasis(match: re.Match[str]) -> str:
        if stats:
            stats.add("메타SEO_자연화")
        return (
            f"{match.group('geo')} 학부모는 “{match.group('term')}”이라는 표현보다 "
            f"{match.group('basis')}의 실제 수업 반영 방식을 확인해야 합니다."
        )
    text = SEO_EMPHASIS_RE.sub(seo_emphasis, text)

    def seo_generic(match: re.Match[str]) -> str:
        if stats:
            stats.add("메타SEO_자연화")
        return f"{match.group('term')} 관련 상담 전에 확인할 질문과 학습 기록을 함께 정리했습니다."
    text = SEO_GENERIC_RE.sub(seo_generic, text)

    def seo_info(match: re.Match[str]) -> str:
        if stats:
            stats.add("메타SEO_자연화")
        return f"{match.group('topic')} 정보를 살펴볼 때 먼저 확인할 내용은"
    text = SEO_INFO_RE.sub(seo_info, text)

    def school_reference(match: re.Match[str]) -> str:
        if stats:
            stats.add("학교_FAQ_자연화")
        return (
            f"{match.group('lead')}{match.group('school')} 관련 자료를 참고하되, "
            "과거 경향만으로 현재 시험을 단정하지 않습니다."
        )
    text = SCHOOL_REFERENCE_RE.sub(school_reference, text)
    text, count = re.subn(
        r"(?:상담 전에 꼭 확인할 핵심은|학부모가 가장 먼저 볼 기준은),\s*",
        "", text,
    )
    if count and stats:
        stats.add("중복도입부_자연화", count)
    text, count = re.subn(
        r"(?P<locality>[가-힣 ]{1,30}) 학부모는 담당자마다 "
        r"(?P=locality) 학부모는",
        r"\g<locality> 학부모는 상담 담당자가 달라도",
        text,
    )
    text, count_other = re.subn(
        r"(?P<locality>[가-힣 ]{1,30}) 학부모는 담당자마다 ",
        r"\g<locality> 학부모는 상담 담당자가 달라도 ",
        text,
    )
    if (count + count_other) and stats:
        stats.add("상담문장_자연화", count + count_other)
    text, count = re.subn(r"습니다\.(?=[가-힣])", "습니다. ", text)
    if count and stats:
        stats.add("문장경계_띄어쓰기", count)
    text, count = re.subn(r"(?<=[가-힣])\.(?=[가-힣])", ". ", text)
    if count and stats:
        stats.add("문장경계_띄어쓰기", count)
    text, count = re.subn(r"안내에서는,\s*", "안내에서는 ", text)
    if count and stats:
        stats.add("불필요쉼표_자연화", count)
    text, count = FALLBACK_QUOTE_TAIL_RE.subn(
        " 적용 기간과 포함 항목을 문서로 받은 뒤 변경 조건도 함께 확인하세요.",
        text,
    )
    if count and stats:
        stats.add("상담질문_자연화", count)

    def teacher_example(match: re.Match[str]) -> str:
        return (
            "여기서는 교사가 보여 준 예시를 차분히 따라가면서도 "
            f"{match.group('traits')} 모습을 보이는 {match.group('grade')} 학생의 "
            "최근 풀이를 사례로 삼았습니다."
        )

    text, count = TEACHER_EXAMPLE_RE.subn(teacher_example, text)
    if count and stats:
        stats.add("학생사례_문법자연화", count)

    text = fix_term_particles(text, PARTICLE_TERMS, stats)
    return text


def semantic_transform(
    value: str,
    profile: Profile,
    center: Center | None,
    region_catalog: Sequence[Center],
    stats: Stats | None,
) -> str:
    facts = () if center is None else (
        center.address, center.center_name, center.registration, center.tuition_url,
    )
    value, placeholders = protect_facts(value, facts)
    if center is not None:
        value = replace_region(value, center, isolated=True, stats=stats)
        geo_prefix = " ".join(x for x in (center.official_region, center.display_district) if x)
        locality_forms = unique((
            center.locality,
            center.display_locality,
            f"{center.official_region} {center.display_locality}",
        ))
        for locality_form in sorted(locality_forms, key=len, reverse=True):
            value = replace_counted(
                value,
                f"{geo_prefix} 범위가 정해지기 전 {locality_form} 같은 학교 학생이라도",
                f"{center.display_geo}에서는 같은 학교 학생이라도",
                "학교문장_자연화",
                stats,
            )
        value = replace_schools(value, center, CATEGORY_CONFIG[profile.category], stats)
    elif profile.kind in {"hub", "national-root"}:
        for item in sorted(region_catalog, key=lambda row: len(row.locality), reverse=True):
            value = replace_region(value, item, isolated=False, stats=stats)
    value = apply_copy_fixes(value, stats)
    value = restore_facts(value, placeholders)
    if center is not None:
        # Locality labels are source-backed literals, but legacy templates
        # occasionally appended a vowel-form particle mechanically
        # (``갈산동가``).  Repair only the exact center forms in this page.
        value, locality_fact_placeholders = protect_facts(value, facts)
        value = fix_term_particles(
            value,
            (center.locality, center.display_locality, center.display_geo),
            stats,
        )
        value = restore_facts(value, locality_fact_placeholders)
        value = fix_term_particles(value, facts, stats)
        # A legacy school-preparation template joined an administrative label
        # directly to an adverb (``서울특별시 마포구 평상시에는``).  The
        # source-backed label remains byte-for-byte intact; only the missing
        # locative particle and adverb ending are repaired.
        admin_geo = " ".join(
            item for item in (center.official_region, center.display_district) if item
        )
        if admin_geo:
            value = replace_counted(
                value,
                f"{admin_geo} 평상시에는",
                f"{admin_geo}에서는 평상시에",
                "지역부사_문법교정",
                stats,
            )
        value = replace_counted(
            value,
            f"{center.display_locality} 상담에서는 제공 자료에는",
            f"{center.display_locality}의 제공 자료에는",
            "상담자료_문법교정",
            stats,
        )
    return value


def replace_visible_text_nodes(text: str, transformer: Callable[[str], str]) -> str:
    """Transform visible text only; attributes, scripts and styles are protected."""
    result: list[str] = []
    placeholders: dict[str, str] = {}
    protected_depth = 0
    for part in re.split(r"(<[^>]+>)", text):
        if part.startswith("<"):
            lowered = part.lower()
            if re.match(r"<\s*/\s*(?:script|style|title|h1)\b", lowered):
                protected_depth = max(0, protected_depth - 1)
            token = f"\ue100MARKUP{len(placeholders):05d}\ue101"
            placeholders[token] = part
            result.append(token)
            if re.match(r"<\s*(?:script|style|title|h1)\b", lowered):
                protected_depth += 1
        else:
            if protected_depth and part:
                token = f"\ue100MARKUP{len(placeholders):05d}\ue101"
                placeholders[token] = part
                result.append(token)
            else:
                result.append(part)
    transformed = transformer("".join(result))
    for token, original in placeholders.items():
        transformed = transformed.replace(token, original)
    return transformed


def replace_visible_text_chunks(text: str, transformer: Callable[[str], str]) -> str:
    """Apply a reader-copy transform to individual text nodes only.

    The older semantic pass intentionally joins nodes behind markup
    placeholders so administrative names can be normalized consistently.
    Sentence-level grade/search cleanup must not cross an element boundary,
    hence this second, deliberately local pass.
    """
    result: list[str] = []
    protected_depth = 0
    for part in re.split(r"(<[^>]+>)", text):
        if part.startswith("<"):
            lowered = part.lower()
            if re.match(r"<\s*/\s*(?:script|style|title|h1)\b", lowered):
                protected_depth = max(0, protected_depth - 1)
            result.append(part)
            if re.match(r"<\s*(?:script|style|title|h1)\b", lowered):
                protected_depth += 1
        elif protected_depth or not part:
            result.append(part)
        else:
            result.append(transformer(part))
    return "".join(result)


def repair_main_quote_balance(text: str, stats: Stats | None) -> str:
    """Remove only unmatched curly quotes from rendered ``main`` copy."""
    def repair_visible(value: str) -> str:
        # A known legacy fragment lost the quoted keyword but retained its
        # introductory topic.  Dropping that topic is more natural than merely
        # deleting the orphan opening quote (``학부모는 지역 학습 계획은``).
        value, topic_count = re.subn(
            r"(?:[가-힣]+\s+){1,6}학부모는\s*“?\s*"
            r"(?P<plan>(?:[가-힣]+\s+){1,3}학습 계획은)",
            r"\g<plan>",
            value,
        )
        chars = list(value)
        removals: set[int] = set()
        for opening, closing in (("“", "”"), ("‘", "’")):
            stack: list[int] = []
            for index, char in enumerate(chars):
                if char == opening:
                    stack.append(index)
                elif char == closing:
                    if stack:
                        stack.pop()
                    else:
                        removals.add(index)
            removals.update(stack)
        if removals:
            value = "".join(char for index, char in enumerate(chars) if index not in removals)
            value = re.sub(r"(?<=[가-힣]) {2,}(?=[가-힣])", " ", value)
        if stats:
            if topic_count:
                stats.add("인용문맥_자연화", topic_count)
            if removals:
                stats.add("인용부호_균형", len(removals))
        return value

    def replace_main(match: re.Match[str]) -> str:
        inner, across_node_count = re.subn(
            r"(?:[가-힣]+\s+){1,6}학부모는\s*“\s*</p>\s*"
            r"(?=<p\b[^>]*>(?:[가-힣]+\s+){1,3}학습 계획은)",
            "</p>\n",
            match.group(2),
            flags=re.I,
        )
        if across_node_count and stats:
            stats.add("인용문맥_자연화", across_node_count)
        repaired = replace_visible_text_nodes(inner, repair_visible)
        return match.group(1) + repaired + match.group(3)

    result, count = re.subn(
        r"(<main\b[^>]*>)(.*?)(</main>)", replace_main, text,
        count=1, flags=re.I | re.S,
    )
    if count != 1:
        raise ValueError("main 영역을 정확히 하나 찾지 못했습니다")
    return result


def repair_orphan_paragraph_openers(text: str, stats: Stats | None) -> str:
    """Repair only the two known families when they begin a visible paragraph.

    The same sentences also occur after a valid lead elsewhere in the corpus,
    where ``특히`` and ``답은`` remain meaningful.  Working inside individual
    ``main p`` elements prevents an unnecessary site-wide semantic rewrite.
    """
    especially_count = 0
    answer_count = 0

    def replace_paragraph(match: re.Match[str]) -> str:
        nonlocal especially_count, answer_count
        inner = match.group(2)
        leading_match = re.match(r"\s*", inner)
        leading = leading_match.group(0) if leading_match else ""
        core = inner[len(leading):]

        especially = ORPHAN_ESPECIALLY_RE.match(core)
        if especially:
            especially_count += 1
            core = especially.group("body") + core[especially.end():]
        else:
            answer = ORPHAN_ANSWER_RE.match(core)
            if answer:
                answer_count += 1
                replacement = (
                    f"{answer.group('grade')} 학생이 {answer.group('condition')} 모습을 보인다면 "
                    "한 과목씩 따로 보기보다 영어·수학 학습 기록을 함께 비교해 보세요."
                )
                core = replacement + core[answer.end():]
        return match.group(1) + leading + core + match.group(3)

    def replace_main(match: re.Match[str]) -> str:
        inner = re.sub(
            r"(<p\b[^>]*>)(.*?)(</p>)", replace_paragraph, match.group(2),
            flags=re.I | re.S,
        )
        return match.group(1) + inner + match.group(3)

    result, main_count = re.subn(
        r"(<main\b[^>]*>)(.*?)(</main>)", replace_main, text,
        count=1, flags=re.I | re.S,
    )
    if main_count != 1:
        raise ValueError("main 영역을 정확히 하나 찾지 못했습니다")
    if stats:
        stats.add("고아접속부사_자연화", especially_count)
        stats.add("고아답변_완결문", answer_count)
    return result


def repair_duplicate_main_headings(
    text: str,
    center: Center,
    config: Mapping[str, Any],
    stats: Stats | None,
) -> str:
    """Give fallback headings distinct reader-facing purposes within a page."""
    candidates = (
        f"{center.display_locality} {config['label']} 상담에서 확인할 내용",
        f"{center.display_locality} 학습 관리 흐름을 확인하는 질문",
        f"{center.display_locality} 등록 전에 기록할 상담 기준",
        f"{center.display_locality} 상담 답변을 비교하는 기준",
        f"{center.display_locality} 학습 계획을 확인하는 순서",
    )
    changed = 0

    def replace_main(match: re.Match[str]) -> str:
        nonlocal changed
        seen: set[str] = set()

        def replace_heading(heading: re.Match[str]) -> str:
            nonlocal changed
            value = fragment_text(heading.group(2))
            replacement = value
            if value in seen:
                replacement = next((item for item in candidates if item not in seen), "")
                if not replacement:
                    raise ValueError("중복 heading 대체 문구를 선택하지 못했습니다")
                changed += 1
            seen.add(replacement)
            if replacement == value:
                return heading.group(0)
            return heading.group(1) + html.escape(replacement) + heading.group(3)

        inner = re.sub(
            r"(<h[23]\b[^>]*>)(.*?)(</h[23]>)", replace_heading, match.group(2),
            flags=re.I | re.S,
        )
        return match.group(1) + inner + match.group(3)

    result, main_count = re.subn(
        r"(<main\b[^>]*>)(.*?)(</main>)", replace_main, text,
        count=1, flags=re.I | re.S,
    )
    if main_count != 1:
        raise ValueError("main 영역을 정확히 하나 찾지 못했습니다")
    if changed and stats:
        stats.add("중복heading_자연화", changed)
    return result


def transform_meta(text: str, transformer: Callable[[str], str]) -> str:
    def tag_replace(tag_match: re.Match[str]) -> str:
        tag = tag_match.group(0)
        if not re.search(r'(?:name|property)=["\'](?:description|og:description)["\']', tag, re.I):
            return tag
        def content_replace(match: re.Match[str]) -> str:
            return match.group(1) + match.group(2) + transformer(match.group(3)) + match.group(2)
        return re.sub(r'(\bcontent=)(["\'])(.*?)(?:\2)', content_replace, tag, count=1, flags=re.I | re.S)
    return re.sub(r"<meta\b[^>]*>", tag_replace, text, flags=re.I | re.S)


def requested_grades(center: Center, config: Mapping[str, Any], subject: str) -> tuple[str, ...]:
    grades = center.grades[subject]
    prefix = config["prefix"]
    return tuple(grade for grade in grades if not prefix or grade.startswith(prefix))


def unsupported_page(center: Center, config: Mapping[str, Any]) -> bool:
    if config["subject"] == "영수":
        return not center.grades["영어"] or not center.grades["수학"]
    return not requested_grades(center, config, config["subject"])


def grade_html(grades: Sequence[str], unconfirmed: bool = False) -> str:
    if grades:
        return '<div class="academy-grade-tags">' + "".join(
            f"<span>{html.escape(grade)}</span>" for grade in grades
        ) + "</div>"
    attrs = f' class="academy-grade-empty academy-source-unconfirmed-note" {SOURCE_MARKER}' if unconfirmed else ' class="academy-grade-empty"'
    label = "원자료 미기재 · 상담 전 확인" if unconfirmed else "제공 자료 없음 · 상담 전 확인"
    return f"<span{attrs}>{label}</span>"


def rebuild_grade_row(text: str, center: Center, config: Mapping[str, Any], stats: Stats | None) -> str:
    subject = config["subject"]
    if subject == "영수":
        pattern = re.compile(
            r'(?:<div><dt>영어·수학 수업 가능 학년</dt><dd>.*?</dd></div>|'
            r'<div><dt>영어 수업 가능 학년</dt><dd>.*?</dd></div>'
            r'<div><dt>수학 수업 가능 학년</dt><dd>.*?</dd></div>)', re.S
        )
        rows = []
        for item in ("영어", "수학"):
            grades = center.grades[item]
            rows.append(f"<div><dt>{item} 수업 가능 학년</dt><dd>{grade_html(grades, not grades)}</dd></div>")
        replacement = "".join(rows)
    else:
        pattern = re.compile(
            rf'<div><dt>{re.escape(subject)} 수업 가능 학년</dt><dd>.*?</dd></div>', re.S
        )
        grades = requested_grades(center, config, subject)
        replacement = (
            f"<div><dt>{subject} 수업 가능 학년</dt><dd>"
            f"{grade_html(grades, not grades)}</dd></div>"
        )
    text, count = pattern.subn(replacement, text, count=1)
    if count == 0:
        text, count = re.subn(
            r'(<div><dt>교육지원청 등록번호</dt>|<div class="academy-tuition-row">)',
            replacement + r"\1", text, count=1,
        )
    if count != 1:
        raise ValueError("학년 핵심정보 행을 교체하거나 삽입하지 못했습니다")
    if stats:
        stats.add("학년_원자료동기화", count)
    return text


def rebuild_school_row(text: str, center: Center, config: Mapping[str, Any], stats: Stats | None) -> str:
    schools = school_order(center, config)
    stage = config["school"]
    school_stage_label = {"고등": "고등학교", "중등": "중학교", "초등": "초등학교"}.get(stage, "학교")
    label = "제공 학교 참고" if stage == "전체" else f"{school_stage_label} 참고"
    if schools:
        value = '<div class="academy-school-tags">' + "".join(
            f"<span>{html.escape(school)}</span>" for school in schools
        ) + "</div>"
    else:
        scope = "학교" if stage == "전체" else school_stage_label
        value = f'<span class="academy-school-empty">제공된 {scope} 정보 없음 · 상담 시 실제 학교 자료 확인</span>'
    replacement = f"<div><dt>{label}</dt><dd>{value}</dd></div>"
    pattern = re.compile(
        r'<div><dt>(?:제공 학교 참고|제공 (?:고등|중|초등)학교 참고|고등학교 참고|중등학교 참고|중학교 참고|초등학교 참고)</dt><dd>.*?</dd></div>', re.S
    )
    text, count = pattern.subn(replacement, text, count=1)
    if count == 0:
        text, count = re.subn(
            r'(<div class="academy-tuition-row">)', replacement + r"\1", text, count=1,
        )
    if count != 1:
        raise ValueError("학교 핵심정보 행을 교체하거나 삽입하지 못했습니다")
    if stats:
        stats.add("학교_태그원자료동기화", count)
    return text


def source_status(center: Center, config: Mapping[str, Any]) -> str:
    prefix_label = {"고": "고등", "중": "중등", "초": "초등", "": ""}[config["prefix"]]
    if config["subject"] == "영수":
        english = "기재되어 있습니다" if center.grades["영어"] else "기재되어 있지 않습니다"
        math = "기재되어 있습니다" if center.grades["수학"] else "기재되어 있지 않습니다"
        return (
            f"제공된 센터 자료에는 영어 가능 학년이 {english}. "
            f"제공된 센터 자료에는 수학 가능 학년이 {math}. "
            "영어와 수학 각각의 실제 개설 여부·대상 학년·수업 시간은 상담 전에 확인해야 합니다."
        )
    course = " ".join(x for x in (prefix_label, config["subject"]) if x)
    return (
        f"제공된 센터 자료에는 {course} 가능 학년이 기재되어 있지 않습니다. "
        "실제 개설 여부·대상 학년·수업 시간은 상담 전에 확인해야 합니다."
    )


def strip_source_markers(text: str) -> str:
    text = re.sub(
        r'<div class="academy-source-status-row"\s+data-source-status="unconfirmed-grade">.*?</div>',
        "", text, flags=re.S,
    )
    text = re.sub(
        r'<details class="academy-faq-item academy-source-status-faq"\s+data-source-status="unconfirmed-grade">.*?</details>',
        "", text, flags=re.S,
    )
    return text


def unsupported_meta_description(center: Center, config: Mapping[str, Any]) -> str:
    if config["subject"] == "영수":
        course = "영어·수학"
    else:
        prefix = {"고": "고등 ", "중": "중등 ", "초": "초등 ", "": ""}[config["prefix"]]
        course = prefix + config["subject"]
    return (
        f"{center.locality} {config['label']} 원자료 확인 안내입니다. "
        f"{center.display_geo} 센터 자료만으로 {course}의 실제 개설 여부와 대상 학년을 "
        "확정할 수 없어 수업 시간과 함께 상담 전에 확인해야 합니다."
    )


def detail_meta_description(center: Center, config: Mapping[str, Any], category: str) -> str:
    """Build one source-backed, locality-specific reader description."""
    if config["subject"] == "영수":
        english = center.grades["영어"]
        math = center.grades["수학"]
        grade_summary = f"영어 {len(english)}개·수학 {len(math)}개 학년"
    else:
        grades = requested_grades(center, config, config["subject"])
        grade_summary = "·".join(grades) if grades else "상담 전 확인"
    topic = DETAIL_META_TOPICS[category]
    description = (
        f"{center.display_geo} {config['label']} 상담 안내입니다. "
        f"제공 자료에 기재된 가능 학년({grade_summary})을 확인하고, 학교 참고 정보와 "
        f"최근 학습 기록을 바탕으로 {topic} 점검, 주간 계획, 오답 재확인 질문을 정리했습니다."
    )
    if len(description) > 160 and config["subject"] != "영수":
        grades = requested_grades(center, config, config["subject"])
        grade_summary = f"{len(grades)}개 학년" if grades else "상담 전 확인"
        description = (
            f"{center.display_geo} {config['label']} 상담 안내입니다. "
            f"제공 자료의 가능 학년({grade_summary})과 학교 참고 정보를 바탕으로 "
            f"{topic} 점검, 주간 계획, 오답 재확인 질문을 정리했습니다."
        )
    if not 50 <= len(description) <= 160:
        raise ValueError(f"생성 meta description 길이 오류: {len(description)} / {description}")
    return description


def repair_detail_meta_description(
    text: str,
    center: Center,
    config: Mapping[str, Any],
    category: str,
    stats: Stats | None,
) -> str:
    """Replace only a scrub-damaged detail description outside 50–160 chars."""
    primary = re.search(
        r'<meta\b(?=[^>]*\bname=["\']description["\'])[^>]*'
        r'\bcontent=(["\'])(.*?)\1',
        text,
        flags=re.I | re.S,
    )
    if not primary:
        raise ValueError("meta description을 찾지 못했습니다")
    current = clean(html.unescape(primary.group(2)))
    if 50 <= len(current) <= 160:
        return text

    description = html.escape(
        detail_meta_description(center, config, category), quote=True,
    )
    changed = 0

    def replace_tag(match: re.Match[str]) -> str:
        nonlocal changed
        tag = match.group(0)
        if not re.search(
            r'(?:name|property)=["\'](?:description|og:description|twitter:description)["\']',
            tag,
            flags=re.I,
        ):
            return tag
        updated, count = re.subn(
            r'(\bcontent=)(["\']).*?\2',
            lambda item: item.group(1) + item.group(2) + description + item.group(2),
            tag,
            count=1,
            flags=re.I | re.S,
        )
        changed += count
        return updated

    result = re.sub(r"<meta\b[^>]*>", replace_tag, text, flags=re.I | re.S)
    if changed < 2:
        raise ValueError(f"description/og:description 갱신 수 부족: {changed}")
    if stats:
        stats.add("메타설명_원자료재생성")
    return result


def rewrite_unsupported_meta(
    text: str, center: Center, config: Mapping[str, Any], stats: Stats | None,
) -> str:
    if not unsupported_page(center, config):
        return text
    description = html.escape(unsupported_meta_description(center, config), quote=True)
    changed = 0
    def replace_tag(match: re.Match[str]) -> str:
        nonlocal changed
        tag = match.group(0)
        if not re.search(
            r'(?:name|property)=["\'](?:description|og:description|twitter:description)["\']',
            tag, re.I,
        ):
            return tag
        updated, count = re.subn(
            r'(\bcontent=)(["\']).*?\2',
            lambda item: item.group(1) + item.group(2) + description + item.group(2),
            tag, count=1, flags=re.I | re.S,
        )
        changed += count
        return updated
    result = re.sub(r"<meta\b[^>]*>", replace_tag, text, flags=re.I | re.S)
    if changed and stats:
        stats.add("unsupported_meta_정확화", changed)
    return result


def add_unsupported_notice(
    text: str, center: Center, config: Mapping[str, Any], stats: Stats | None,
) -> str:
    if not unsupported_page(center, config):
        return text
    text = strip_source_markers(text)
    status = source_status(center, config)
    label = config["label"]
    locality = center.locality
    if config["subject"] == "영수":
        subject_confirmation = (
            "영어와 수학의 실제 개설 여부·대상 학년·수업 시간을 과목별로 나누어 확인하세요."
        )
    else:
        prefix = {"고": "고등 ", "중": "중등 ", "초": "초등 ", "": ""}[config["prefix"]]
        subject_confirmation = (
            f"{prefix}{config['subject']}의 실제 개설 여부·대상 학년·수업 시간을 확인하세요."
        )

    hero_copy = (
        f'<p class="academy-source-unconfirmed-note" {SOURCE_MARKER}>'
        f'{html.escape(status)} 이 페이지는 {html.escape(locality)} {html.escape(label)} 상담 전에 '
        '원자료와 확인 질문을 정리하기 위한 안내입니다.</p>'
    )
    text, count = re.subn(
        r'(<section class="academy-hero".*?<h1\b[^>]*>.*?</h1>)\s*<p\b[^>]*>.*?</p>',
        lambda match: match.group(1) + hero_copy,
        text, count=1, flags=re.S,
    )
    if count != 1:
        raise ValueError("unsupported hero 안내를 찾지 못했습니다")
    panel_strong = f"{locality} {label}, 원자료 확인이 먼저입니다"
    panel_copy = "실제 개설 여부·대상 학년·수업 시간은 센터 상담에서 최신 내용을 확인해야 합니다."
    text, count = re.subn(
        r'(<aside class="academy-hero-panel"[^>]*>)\s*<strong>.*?</strong>\s*<p\b[^>]*>.*?</p>',
        lambda match: (
            match.group(1)
            + f'<strong>{html.escape(panel_strong)}</strong>'
            + f'<p class="academy-source-unconfirmed-note" {SOURCE_MARKER}>{html.escape(panel_copy)}</p>'
        ),
        text, count=1, flags=re.S,
    )
    if count != 1:
        raise ValueError("unsupported hero panel을 찾지 못했습니다")

    summary = (
        f'<div class="academy-summary-box academy-top-answer academy-source-unconfirmed" {SOURCE_MARKER}>'
        f'<strong>30초 핵심 안내</strong><p>{html.escape(center.display_geo)}의 {html.escape(config["label"])} 안내입니다. '
        f'{html.escape(status)} 확인된 센터 주소는 {html.escape(center.address, quote=False)}이며, '
        '센터 공통 교습비 자료는 아래 핵심정보에서 확인할 수 있습니다.</p></div>'
    )
    text, count = re.subn(
        r'<div class="[^"]*academy-summary-box[^"]*academy-top-answer[^"]*"(?:\s+[^>]*)?>.*?</div>',
        summary, text, count=1, flags=re.S,
    )
    if count != 1:
        raise ValueError("unsupported 상단 안내를 찾지 못했습니다")
    intro = (
        f'<div class="academy-intro-answer academy-source-unconfirmed" {SOURCE_MARKER}><p>'
        f'{html.escape(center.locality)} {html.escape(config["label"])} 페이지는 현재 원자료에서 대상 학년 확인이 필요한 상태입니다. '
        f'{html.escape(status)} 이 페이지의 학습 점검 항목은 상담 질문을 준비하기 위한 일반 기준이며 '
        '해당 센터의 수업 제공을 뜻하지 않습니다.</p></div>'
    )
    text, count = re.subn(
        r'<div class="[^"]*academy-intro-answer[^"]*"(?:\s+[^>]*)?>.*?</div>',
        intro, text, count=1, flags=re.S,
    )
    if count != 1:
        raise ValueError("unsupported intro를 찾지 못했습니다")

    school_scope = {
        "고등": "고등학교", "중등": "중학교", "초등": "초등학교", "전체": "학교",
    }[config["school"]]
    article = (
        f'<article class="academy-main-article" {SOURCE_MARKER}>'
        f'<div class="academy-intro-answer academy-source-unconfirmed" {SOURCE_MARKER}><p>'
        f'{html.escape(locality)} {html.escape(label)} 페이지는 현재 원자료에서 대상 학년 확인이 필요한 상태입니다. '
        f'{html.escape(status)} 아래 내용은 개설을 전제로 한 소개가 아니라 상담 전에 확인할 사실과 질문입니다.'
        '</p></div>'
        f'<section class="academy-prose-section"><h2>{html.escape(locality)} {html.escape(label)} 자료에서 확인되는 범위</h2>'
        f'<p>{html.escape(center.display_geo)} 센터의 명칭·주소·등록 정보는 상단 핵심정보에서 확인할 수 있습니다. '
        f'이 센터 식별 정보만으로 {html.escape(label)} 개설 여부나 대상 학년을 확정할 수는 없습니다.</p>'
        f'<p>{html.escape(status)} 상담 답변을 받은 날짜와 담당자, 적용 시작 시점을 함께 기록하면 나중에 변경 여부를 확인하기 쉽습니다.</p></section>'
        f'<section class="academy-prose-section"><h2>{html.escape(school_scope)} 참고 정보의 올바른 사용</h2>'
        f'<p>상단의 {html.escape(school_scope)} 참고 항목은 제공된 센터 자료에 적힌 학교명을 보여 줍니다. '
        f'학교명이 있다는 사실은 {html.escape(label)} 개설이나 해당 학교 학생의 수강 가능 여부를 뜻하지 않습니다.</p>'
        '<p>상담에서는 현재 학기의 학교 자료를 실제로 다루는지, 자료 제출 시점과 반영 방법은 무엇인지 확인해야 합니다. '
        '과거 경향이나 학교명만으로 현재 시험 유형을 단정해서는 안 됩니다.</p></section>'
        f'<section class="academy-prose-section"><h2>{html.escape(locality)} 상담 전에 확인할 항목</h2>'
        '<p>실제 개설 여부, 대상 학년, 과목, 요일과 시간, 교재, 과제 방식, 비용을 항목별로 확인하세요. '
        f'{html.escape(subject_confirmation)}</p>'
        '<p>센터 주소와 공통 교습비 자료는 방문과 문의를 준비하는 참고 정보입니다. '
        '최신 운영 내용과 금액은 등록을 결정하기 전에 센터에 다시 확인하세요.</p></section>'
        '</article>'
    )
    text, count = re.subn(
        r'<article class="academy-main-article"[^>]*>.*?</article>',
        article, text, count=1, flags=re.S,
    )
    if count != 1:
        raise ValueError("unsupported article을 찾지 못했습니다")

    row = (
        f'<div class="academy-source-status-row" {SOURCE_MARKER}><dt>자료 확인 상태</dt>'
        f'<dd class="academy-source-unconfirmed-note">{html.escape(status)}</dd></div>'
    )
    text, count = re.subn(
        r'(<aside class="academy-aside-card">.*?<dl>)', rf"\1{row}", text, count=1, flags=re.S,
    )
    if count != 1:
        raise ValueError("unsupported 핵심정보 dl을 찾지 못했습니다")

    faq_items = (
        (
            f"{locality} {label}의 가능 학년은 원자료에서 확인되나요?",
            status,
        ),
        (
            f"{locality} {label} 상담에서 무엇을 먼저 확인해야 하나요?",
            f"{subject_confirmation} 답변 날짜와 적용 시점도 기록하세요.",
        ),
        (
            f"제공된 {school_scope} 이름으로 수업 가능 여부를 판단해도 되나요?",
            f"제공된 {school_scope} 참고 항목은 센터 자료에 적힌 학교명입니다. 이 정보만으로 "
            f"{label} 개설 여부나 특정 학교 학생의 수강 가능 여부를 단정할 수 없으므로 상담에서 현재 자료 반영 범위를 확인하세요.",
        ),
        (
            f"{locality} 센터 주소와 교습비 자료는 어디에서 확인하나요?",
            f"제공된 주소는 {center.address}입니다. 상단 핵심정보에서 센터가 제공한 공통 교습비 자료 링크를 "
            "확인할 수 있으며, 실제 운영 시간과 최신 금액은 상담 전에 다시 확인하세요.",
        ),
    )
    faq = "".join(
        f'<details class="academy-faq-item academy-source-status-faq" {SOURCE_MARKER}>'
        f'<summary>{html.escape(question)}</summary><p>{html.escape(answer, quote=False)}</p></details>'
        for question, answer in faq_items
    )
    text, count = re.subn(
        r'(<div class="academy-faq-list"[^>]*>)(.*?)(</div></div></section>)',
        lambda match: match.group(1) + faq + match.group(3),
        text, count=1, flags=re.S,
    )
    if count != 1:
        raise ValueError("unsupported FAQ 목록을 찾지 못했습니다")

    review = (
        f'<section class="section"><div class="container academy-review-card academy-source-unconfirmed" {SOURCE_MARKER}>'
        f'<h2>{html.escape(locality)} {html.escape(label)} 상담 전 확인 메모</h2>'
        f'<p class="academy-review-note">{html.escape(status)} 아래 메모는 실제 수강 사례가 아니라 상담 답변을 확인하기 위한 기록 기준입니다.</p>'
        '<div class="academy-review-list">'
        '<blockquote class="academy-review-item">개설 과목과 대상 학년, 요일·시간, 비용을 같은 순서로 질문하고 답변 날짜를 적습니다.</blockquote>'
        '<blockquote class="academy-review-item">센터 식별 정보와 학교 참고 정보만으로 개설 여부를 판단하지 않고, 최신 운영 내용은 별도로 확인합니다.</blockquote>'
        '</div></div></section>'
    )
    text, count = re.subn(
        r'<section class="section"><div class="[^"]*academy-review-card[^"]*"[^>]*>.*?</div></div></section>',
        review, text, count=1, flags=re.S,
    )
    if count != 1:
        raise ValueError("unsupported review 영역을 찾지 못했습니다")
    navigation_copy = (
        f'<p class="academy-source-unconfirmed-note" {SOURCE_MARKER}>'
        '같은 동네의 다른 학년·과목 페이지도 각 페이지의 원자료 확인 상태를 먼저 확인하세요.</p>'
    )
    text, count = re.subn(
        r'(<div class="academy-navigation-head"><div>.*?</div>)\s*<p\b[^>]*>.*?</p>',
        lambda match: match.group(1) + navigation_copy,
        text, count=1, flags=re.S,
    )
    if count != 1:
        raise ValueError("unsupported 관련 페이지 안내를 찾지 못했습니다")
    if stats:
        stats.add("unsupported_투명안내")
    return text


def repair_preparation_faqs(
    text: str, center: Center, stats: Stats | None,
) -> str:
    """Keep a generated preparation question aligned with its answer.

    Earlier cleanup runs may already have replaced a legacy seed question in
    the checked-in HTML.  If its surviving answer contains no preparation
    material at all, add conservative items a family can actually bring to a
    consultation; this does not assert that a center provides a service.
    """
    preparation_cues = (
        "시험지", "단원평가", "현재 교재", "오답", "학습 기록", "학교 자료",
    )

    def replace(match: re.Match[str]) -> str:
        block = match.group(0)
        question_match = re.search(r"<summary\b[^>]*>(.*?)</summary>", block, re.I | re.S)
        answer_match = re.search(r"(<p\b[^>]*>)(.*?)(</p>)", block, re.I | re.S)
        if not question_match or not answer_match:
            return block
        question = fragment_text(question_match.group(1))
        answer = fragment_text(answer_match.group(2))
        if not (
            "상담 전에 준비할 자료" in question
            or ("상담 전에" in question and "무엇을 준비" in question)
        ):
            return block
        if any(cue in answer for cue in preparation_cues):
            return block
        prefix = (
            f"{center.display_locality} 상담에는 최근 시험지나 단원평가, 현재 교재, "
            "오답 표시와 가능한 등원 요일을 준비하세요. "
        )
        updated_answer = answer_match.group(1) + html.escape(prefix, quote=False) + answer_match.group(2) + answer_match.group(3)
        if stats:
            stats.add("FAQ_질문답변정합")
        return block[:answer_match.start()] + updated_answer + block[answer_match.end():]

    return re.sub(
        r'<details class="[^"]*academy-faq-item[^"]*"[^>]*>.*?</details>',
        replace,
        text,
        flags=re.I | re.S,
    )


def fragment_text(value: str) -> str:
    return clean(html.unescape(re.sub(r"<[^>]+>", " ", value)))


def visible_faqs(text: str) -> list[tuple[str, str]]:
    result: list[tuple[str, str]] = []
    for inner in re.findall(r"<details\b[^>]*>(.*?)</details>", text, re.I | re.S):
        question_match = re.search(r"<summary\b[^>]*>(.*?)</summary>", inner, re.I | re.S)
        answer_match = re.search(r"<p\b[^>]*>(.*?)</p>", inner, re.I | re.S)
        if question_match and answer_match:
            result.append((fragment_text(question_match.group(1)), fragment_text(answer_match.group(1))))
    return result


def node_has_type(node: Mapping[str, Any], kind: str) -> bool:
    value = node.get("@type")
    return kind in value if isinstance(value, list) else value == kind


def walk_semantic(value: Any, transformer: Callable[[str], str]) -> Any:
    """Transform every non-URL JSON string in one batch.

    Batching is materially faster than recompiling the same source-backed
    school/particle patterns for each of roughly one hundred schema strings.
    """
    strings: list[str] = []
    separator = "\ue200JSONLD-SEMANTIC-SEPARATOR\ue201"

    def collect(item: Any, key: str = "") -> None:
        if isinstance(item, list):
            for child in item:
                collect(child, key)
        elif isinstance(item, dict):
            for child_key, child in item.items():
                collect(child, child_key)
        elif isinstance(item, str) and key not in URL_KEYS:
            if separator in item:
                raise ValueError("JSON-LD semantic separator collision")
            strings.append(item)

    collect(value)
    if not strings:
        return value
    transformed = transformer(separator.join(strings)).split(separator)
    if len(transformed) != len(strings):
        raise ValueError("JSON-LD semantic batch split mismatch")
    iterator = iter(transformed)

    def rebuild(item: Any, key: str = "") -> Any:
        if isinstance(item, list):
            return [rebuild(child, key) for child in item]
        if isinstance(item, dict):
            return {child_key: rebuild(child, child_key) for child_key, child in item.items()}
        if isinstance(item, str) and key not in URL_KEYS:
            return next(iterator)
        return item

    return rebuild(value)


def walk_reader_json(value: Any, transformer: Callable[[str], str], key: str = "") -> Any:
    """Clean only JSON strings that still contain a classified irrelevant seed."""
    if isinstance(value, list):
        return [walk_reader_json(item, transformer, key) for item in value]
    if isinstance(value, dict):
        return {
            child_key: walk_reader_json(child, transformer, child_key)
            for child_key, child in value.items()
        }
    if isinstance(value, str) and key not in URL_KEYS and IRRELEVANT_SEED_RE.search(value):
        return transformer(value)
    return value


def sync_article_schools(data: Any, center: Center, config: Mapping[str, Any]) -> None:
    graph = data.get("@graph", []) if isinstance(data, dict) else []
    allowed = school_order(center, config)
    all_names = set(center.all_schools) | set(allowed)
    for node in graph:
        if not isinstance(node, dict) or not node_has_type(node, "Article"):
            continue
        mentions = node.get("mentions")
        if not isinstance(mentions, list):
            continue
        retained = [
            item for item in mentions
            if not (isinstance(item, dict) and clean(item.get("name")) in all_names)
        ]
        retained.extend({"@type": "Thing", "name": school} for school in allowed)
        node["mentions"] = retained


def sync_faq(data: Any, faqs: Sequence[tuple[str, str]]) -> None:
    if not faqs or not isinstance(data, dict):
        return
    graph = data.get("@graph", [])
    for node in graph:
        if isinstance(node, dict) and node_has_type(node, "FAQPage"):
            node["mainEntity"] = [
                {
                    "@type": "Question", "name": question,
                    "acceptedAnswer": {"@type": "Answer", "text": answer},
                }
                for question, answer in faqs
            ]
            return
    raise ValueError("visible FAQ가 있지만 FAQPage JSON-LD가 없습니다")


def transform_jsonld(
    text: str,
    transformer: Callable[[str], str],
    faqs: Sequence[tuple[str, str]],
    center: Center | None,
    config: Mapping[str, Any] | None,
    reader_transformer: Callable[[str], str] | None,
    stats: Stats | None,
) -> str:
    def replace(match: re.Match[str]) -> str:
        try:
            original = json.loads(match.group(2))
        except json.JSONDecodeError as exc:
            raise ValueError(f"JSON-LD parse 실패: {exc}") from exc
        updated = walk_semantic(original, transformer)
        if reader_transformer is not None:
            updated = walk_reader_json(updated, reader_transformer)
        if center is not None and config is not None:
            sync_article_schools(updated, center, config)
        sync_faq(updated, faqs)
        if updated == original:
            return match.group(0)
        if stats:
            stats.add("JSONLD_본문FAQ동기화")
        payload = json.dumps(updated, ensure_ascii=False, separators=(",", ":"))
        return match.group(1) + payload + match.group(3)
    result, count = SCRIPT_JSON_RE.subn(replace, text)
    if count != 1:
        raise ValueError(f"JSON-LD script는 정확히 하나여야 합니다: {count}")
    return result


def transform_document(
    text: str,
    profile: Profile,
    center: Center | None,
    region_catalog: Sequence[Center],
    stats: Stats | None = None,
) -> str:
    """Pure, deterministic document transform used by check/apply/schema tests."""
    transform = lambda value: semantic_transform(value, profile, center, region_catalog, stats)
    text = transform_meta(text, transform)
    text = replace_visible_text_nodes(text, transform)
    config: Mapping[str, Any] | None = None
    reader_transform: Callable[[str], str] | None = None
    if profile.kind == "detail":
        if center is None:
            raise ValueError("상세 페이지에는 Center가 필요합니다")
        config = CATEGORY_CONFIG[profile.category]
        page_has_grade_claim_error = bool(
            not unsupported_page(center, config)
            and visible_grade_claim_errors(rendered_grade_claim_text(text), center, config)
        )
        reader_transform = lambda value: reader_language_cleanup(
            align_visible_grades(
                value, center, config, stats,
                page_has_claim_error=page_has_grade_claim_error,
            ),
            center, config, stats,
        )
        text = transform_meta(text, reader_transform)
        text = replace_visible_text_chunks(text, reader_transform)
        text = repair_orphan_paragraph_openers(text, stats)
        text = repair_duplicate_main_headings(text, center, config, stats)
        text = repair_main_quote_balance(text, stats)
        text = rewrite_unsupported_meta(text, center, config, stats)
        text = repair_detail_meta_description(text, center, config, profile.category, stats)
        text = rebuild_grade_row(text, center, config, stats)
        text = rebuild_school_row(text, center, config, stats)
        text = add_unsupported_notice(text, center, config, stats)
        text = repair_preparation_faqs(text, center, stats)
    faqs = visible_faqs(text)
    text = transform_jsonld(
        text, transform, faqs, center, config, reader_transform, stats,
    )
    return text


def extract_one(text: str, pattern: str) -> str:
    match = re.search(pattern, text, re.I | re.S)
    return match.group(1) if match else ""


def json_url_values(text: str) -> tuple[tuple[str, str], ...]:
    result: list[tuple[str, str]] = []
    def collect(value: Any, path: str = "") -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                child_path = path + "/" + key
                if key in URL_KEYS and isinstance(child, str):
                    result.append((child_path, child))
                collect(child, child_path)
        elif isinstance(value, list):
            for index, child in enumerate(value):
                collect(child, f"{path}/{index}")
    for match in SCRIPT_JSON_RE.finditer(text):
        collect(json.loads(match.group(2)))
    return tuple(result)


def json_fact_values(text: str) -> tuple[tuple[str, str], ...]:
    """Capture schema fields whose source literals must never be rewritten."""
    result: list[tuple[str, str]] = []
    def collect(value: Any, path: str = "") -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                child_path = path + "/" + key
                if key in {"streetAddress", "telephone"} and isinstance(child, str):
                    result.append((child_path, child))
                collect(child, child_path)
        elif isinstance(value, list):
            for index, child in enumerate(value):
                collect(child, f"{path}/{index}")
    for match in SCRIPT_JSON_RE.finditer(text):
        collect(json.loads(match.group(2)))
    return tuple(result)


def stable_snapshot(text: str) -> tuple[Any, ...]:
    attributes = tuple(re.findall(r'\b(?:href|src|action)=(["\'])(.*?)\1', text, re.I | re.S))
    return (
        extract_one(text, r"<title>(.*?)</title>"),
        fragment_text(extract_one(text, r"<h1\b[^>]*>(.*?)</h1>")),
        extract_one(text, r'(<meta\b(?=[^>]*name=["\']robots["\'])[^>]*>)'),
        extract_one(text, r'<link\b(?=[^>]*rel=["\']canonical["\'])[^>]*href=["\']([^"\']+)["\']'),
        attributes,
        json_url_values(text),
        json_fact_values(text),
    )


def schema_faqs(text: str) -> list[tuple[str, str]]:
    for match in SCRIPT_JSON_RE.finditer(text):
        data = json.loads(match.group(2))
        for node in data.get("@graph", []):
            if isinstance(node, dict) and node_has_type(node, "FAQPage"):
                result = []
                for item in node.get("mainEntity", []):
                    answer = item.get("acceptedAnswer", {}) if isinstance(item, dict) else {}
                    result.append((clean(item.get("name")), clean(answer.get("text"))))
                return result
    return []


def rendered_main_text(text: str) -> str:
    main = extract_one(text, r"<main\b[^>]*>(.*?)</main>")
    main = re.sub(r"<(?:script|style)\b.*?</(?:script|style)>", " ", main, flags=re.I | re.S)
    return fragment_text(main)


def rendered_grade_claim_text(text: str) -> str:
    """Rendered main copy excluding the subject-specific quick-info rows.

    Those rows are validated independently against the CSV.  Flattening the
    English and math rows into one sentence would incorrectly apply the
    combined-course grade intersection to two separately labelled facts.
    """
    main = extract_one(text, r"<main\b[^>]*>(.*?)</main>")
    main = re.sub(
        r'<aside class="academy-aside-card"[^>]*>.*?</aside>', " ", main,
        flags=re.I | re.S,
    )
    main = re.sub(r"<(?:script|style)\b.*?</(?:script|style)>", " ", main, flags=re.I | re.S)
    return fragment_text(main)


def visible_grade_claim_errors(
    text: str, center: Center, config: Mapping[str, Any],
) -> list[str]:
    support = {
        subject: requested_grades(center, config, subject)
        for subject in (("영어", "수학") if config["subject"] == "영수" else (config["subject"],))
    }
    result: list[str] = []
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        tokens = {canonical_grade(item) for item in GRADE_TOKEN_RE.findall(sentence)}
        if not tokens or not GRADE_SERVICE_CLAIM_RE.search(sentence):
            continue
        subjects = tuple(support)
        named = [subject for subject in subjects if subject in sentence]
        if len(subjects) > 1 and ("영수" in sentence or len(named) != 1):
            allowed = set(support[subjects[0]])
            for subject in subjects[1:]:
                allowed &= set(support[subject])
        elif named:
            allowed = set(support[named[0]])
        else:
            allowed = set().union(*(set(values) for values in support.values()))
        extra = tokens - allowed
        if extra:
            result.append(f"{sorted(extra)}: {sentence[:160]}")
    return result


def duplicate_school_claims(
    text: str, center: Center, config: Mapping[str, Any],
) -> list[str]:
    result: list[str] = []
    for school in school_order(center, config):
        if re.search(
            rf"{re.escape(school)}(?:과|와)\s*{re.escape(school)}"
            rf"(?:이|가)?\s*(?:포함|등)",
            text,
        ):
            result.append(school)
    return result


def validate_document(before: str, after: str, profile: Profile, center: Center | None) -> None:
    if stable_snapshot(before) != stable_snapshot(after):
        raise ValueError(f"URL/title/H1/canonical/robots 불변식 위반: {profile.path}")
    if center is not None:
        for literal in (center.center_name, center.registration, center.tuition_url):
            if literal and before.count(literal) != after.count(literal):
                raise ValueError(f"authoritative fact literal 변경: {profile.path} / {literal}")
        info_region = fragment_text(extract_one(
            after, r"<dt>지역</dt><dd>(.*?)</dd>"
        ))
        if info_region != center.display_geo:
            raise ValueError(
                f"서비스 지역 taxonomy 불일치: {profile.path} / "
                f"{info_region!r} != {center.display_geo!r}"
            )
        info_address = fragment_text(extract_one(
            after, r"<dt>제공 주소</dt><dd>(.*?)</dd>"
        ))
        if info_address != center.address:
            raise ValueError(
                f"제공 주소 CSV 불일치: {profile.path} / {info_address!r} != {center.address!r}"
            )
        before_quick_answer = fragment_text(extract_one(
            before, r'<div class="[^"]*academy-top-answer[^"]*"[^>]*>(.*?)</div>'
        ))
        quick_answer = fragment_text(extract_one(
            after, r'<div class="[^"]*academy-top-answer[^"]*"[^>]*>(.*?)</div>'
        ))
        if center.address in before_quick_answer and center.address not in quick_answer:
            raise ValueError(f"상단 핵심 안내 주소 누락/변경: {profile.path}")
        street_addresses = [
            value for path, value in json_fact_values(after) if path.endswith("/streetAddress")
        ]
        if not street_addresses or any(value != center.address for value in street_addresses):
            raise ValueError(f"JSON-LD streetAddress CSV 불일치: {profile.path}")
    visible = visible_faqs(after)
    schema = schema_faqs(after)
    if visible and visible != schema:
        raise ValueError(f"visible/FAQPage 불일치: {profile.path}")
    if len({question for question, _ in visible}) != len(visible):
        raise ValueError(f"중복 FAQ 질문: {profile.path}")
    if center is not None:
        factual_address_answers = [
            answer for _question, answer in visible
            if any(marker in answer for marker in (
                "제공된 주소는", "확인된 주소는", "상담 위치로 확인된 주소는",
            ))
        ]
        if factual_address_answers and any(
            center.address not in answer for answer in factual_address_answers
        ):
            raise ValueError(f"FAQ 주소 CSV 불일치: {profile.path}")
        for question, answer in visible:
            is_preparation = (
                "상담 전에 준비할 자료" in question
                or ("상담 전에" in question and "무엇을 준비" in question)
            )
            if is_preparation and not any(
                cue in answer for cue in (
                    "시험지", "단원평가", "현재 교재", "오답", "학습 기록", "학교 자료",
                )
            ):
                raise ValueError(f"FAQ 준비자료 질문/답변 불일치: {profile.path}")
    for bad in (
        *KNOWN_COPY_REPLACEMENTS.keys(),
        "검색한 학부모가", "검색어가 강조되더라도", "와와학습코칭학원로",
        "학습집중관리을", "학원출입관리을",
        "확인 가능한 학교 참고 자료 중 하나로",
        "상담 전에 꼭 확인할 핵심은,", "학부모가 가장 먼저 볼 기준은,",
        "학부모는 담당자마다", "상담에서는 제공 자료에는",
    ):
        if bad and bad in after:
            raise ValueError(f"확정 오류 잔존: {profile.path} / {bad[:30]}")
    if profile.kind == "detail" and center is not None:
        config = CATEGORY_CONFIG[profile.category]
        description_match = re.search(
            r'<meta\b(?=[^>]*\bname=["\']description["\'])[^>]*'
            r'\bcontent=(["\'])(.*?)\1',
            after,
            flags=re.I | re.S,
        )
        og_description_match = re.search(
            r'<meta\b(?=[^>]*\bproperty=["\']og:description["\'])[^>]*'
            r'\bcontent=(["\'])(.*?)\1',
            after,
            flags=re.I | re.S,
        )
        if not description_match or not og_description_match:
            raise ValueError(f"detail meta description 누락: {profile.path}")
        description = clean(html.unescape(description_match.group(2)))
        og_description = clean(html.unescape(og_description_match.group(2)))
        if not 50 <= len(description) <= 160:
            raise ValueError(
                f"detail meta description 길이 오류: {profile.path} / {len(description)}"
            )
        if og_description != description:
            raise ValueError(f"description/og:description 불일치: {profile.path}")
        main_html = extract_one(after, r"<main\b[^>]*>(.*?)</main>")
        main_text = rendered_main_text(after)
        headings = [
            fragment_text(value)
            for value in re.findall(r"<h[23]\b[^>]*>(.*?)</h[23]>", main_html, re.I | re.S)
        ]
        if len(headings) != len(set(headings)):
            raise ValueError(f"detail 본문 중복 heading: {profile.path}")
        meta_match = META_LANGUAGE_RE.search(main_text)
        if meta_match:
            raise ValueError(f"독자 본문 검색 메타 언어 잔존: {profile.path} / {meta_match.group(0)}")
        seed_match = IRRELEVANT_SEED_RE.search(main_text)
        if seed_match:
            raise ValueError(f"독자 의도 무관 seed 잔존: {profile.path} / {seed_match.group(0)}")
        for script_match in SCRIPT_JSON_RE.finditer(after):
            json_seed = IRRELEVANT_SEED_RE.search(script_match.group(2))
            if json_seed:
                raise ValueError(
                    f"JSON-LD 무관 seed 잔존: {profile.path} / {json_seed.group(0)}"
                )
        fact_terms = (
            center.address, center.center_name, center.registration, center.tuition_url,
        )
        particle_text, _particle_facts = protect_facts(main_text, fact_terms)
        particle_terms = (
            *PARTICLE_TERMS, center.locality, center.display_locality, center.display_geo,
        )
        if fix_term_particles(particle_text, particle_terms, None) != particle_text:
            raise ValueError(f"확정 조사 오류 잔존: {profile.path}")
        if fix_term_particles(main_text, fact_terms, None) != main_text:
            raise ValueError(f"확정 조사 오류 잔존: {profile.path}")
        if re.search(r"습니다\.(?=[가-힣])", main_text):
            raise ValueError(f"문장 경계 띄어쓰기 오류 잔존: {profile.path}")
        if "의 교사가 보여 준 예시는" in main_text:
            raise ValueError(f"학생 사례 문법 오류 잔존: {profile.path}")
        orphan_paragraph = re.search(
            r"<p\b[^>]*>\s*(?P<lead>특히|답은)(?=\s)", main_html,
            flags=re.I,
        )
        if orphan_paragraph:
            raise ValueError(
                f"고아 문단 도입부 잔존: {profile.path} / "
                f"{orphan_paragraph.group('lead')}"
            )
        if FALLBACK_QUOTE_TAIL_RE.search(main_text):
            raise ValueError(f"상담 질문 인용부 오류 잔존: {profile.path}")
        for opening, closing in (("“", "”"), ("‘", "’")):
            if main_text.count(opening) != main_text.count(closing):
                raise ValueError(
                    f"본문 인용부호 불균형: {profile.path} / {opening}{closing}"
                )
        for tail in QUOTE_TAIL_RE.finditer(main_text):
            sentence_start = max(
                main_text.rfind(boundary, 0, tail.start())
                for boundary in (". ", "! ", "? ")
            )
            prefix = main_text[sentence_start + 2:tail.start()]
            if prefix.count("“") <= prefix.count("”"):
                raise ValueError(f"본문 단독 인용 tail 잔존: {profile.path} / {tail.group(0)}")
        if re.search(r"범위가 정해지기 전 [가-힣 ]+ 같은 학교 학생이라도", main_text):
            raise ValueError(f"지역/학교 문장 오류 잔존: {profile.path}")
        admin_geo = " ".join(
            item for item in (center.official_region, center.display_district) if item
        )
        if admin_geo and f"{admin_geo} 평상시에는" in main_text:
            raise ValueError(f"지역 부사 문법 오류 잔존: {profile.path}")
        if "상담 안내와 관계없이" in main_text:
            raise ValueError(f"무관 seed 치환 잔재: {profile.path}")
        if not unsupported_page(center, config):
            grade_errors = visible_grade_claim_errors(rendered_grade_claim_text(after), center, config)
            if grade_errors:
                raise ValueError(f"원자료 밖 학년 단정: {profile.path} / {grade_errors[:2]}")
        repeated = duplicate_school_claims(main_text, center, config)
        if repeated:
            raise ValueError(f"동일 학교 중복 문장: {profile.path} / {repeated}")
        allowed = set(school_order(center, config))
        for forbidden in (set(center.all_schools) - allowed):
            if re.search(rf"(?<![가-힣A-Za-z0-9]){re.escape(forbidden)}(?![가-힣A-Za-z0-9])", after):
                raise ValueError(f"타 학년 학교 잔존: {profile.path} / {forbidden}")
        marker_count = after.count(SOURCE_MARKER)
        if unsupported_page(center, config):
            if marker_count < 5:
                raise ValueError(f"unsupported 투명 안내 marker 부족: {profile.path} / {marker_count}")
            certainty_patterns = (
                rf"{re.escape(center.locality)}\s+수업(?:에서는|의|이\s*시작)",
                r"수업이\s*시작되면|실제\s*수업에서|학교\s*목록은\s*수업\s*가능\s*범위",
                r"학생.{0,24}(?:맞는|위한).{0,24}(?:수업|학습|관리)",
                r"(?:제공|운영|진행)한다(?:고|는|며|\.|\s)",
            )
            certainty = [pattern for pattern in certainty_patterns if re.search(pattern, main_text)]
            if certainty:
                raise ValueError(f"unsupported 본문 제공 단정 잔존: {profile.path} / {certainty}")
        elif marker_count:
            raise ValueError(f"supported 페이지에 미확인 marker 존재: {profile.path}")


def decode_html(data: bytes) -> tuple[str, bytes]:
    bom = b"\xef\xbb\xbf" if data.startswith(b"\xef\xbb\xbf") else b""
    payload = data[len(bom):]
    return payload.decode("utf-8"), bom


def build_plan(root: Path, reference_dir: Path) -> BuildPlan:
    """Build and validate all 3,374 outputs without writing any file."""
    root = root.resolve()
    centers = load_centers(reference_dir.resolve())
    profiles = discover_profiles(root, centers)
    stats = Stats()
    scanned: Counter[str] = Counter()
    unsupported: Counter[str] = Counter()
    detail_descriptions: Counter[str] = Counter()
    documents: list[PlannedDocument] = []
    catalog = tuple(centers.values())
    for profile in profiles:
        before_bytes = profile.path.read_bytes()
        before, bom = decode_html(before_bytes)
        center = centers.get(profile.locality) if profile.kind == "detail" else None
        try:
            after = transform_document(before, profile, center, catalog, stats)
            validate_document(before, after, profile, center)
            second = transform_document(after, profile, center, catalog, Stats())
        except Exception as exc:
            raise ValueError(f"{profile.path}: {exc}") from exc
        if second != after:
            raise ValueError(f"in-memory idempotency 실패: {profile.path}")
        after_bytes = bom + after.encode("utf-8")
        documents.append(PlannedDocument(profile.path, before_bytes, after_bytes))
        scanned[profile.kind] += 1
        if profile.kind == "detail":
            description_match = re.search(
                r'<meta\b(?=[^>]*\bname=["\']description["\'])[^>]*'
                r'\bcontent=(["\'])(.*?)\1',
                after,
                flags=re.I | re.S,
            )
            if not description_match:
                raise ValueError(f"detail meta description 누락: {profile.path}")
            detail_descriptions[clean(html.unescape(description_match.group(2)))] += 1
        if center is not None and unsupported_page(center, CATEGORY_CONFIG[profile.category]):
            unsupported[profile.category] += 1
    if dict(unsupported) != EXPECTED_UNSUPPORTED:
        raise ValueError(f"unsupported 106 분포 불일치: {dict(unsupported)}")
    if sum(unsupported.values()) != 106:
        raise ValueError(f"unsupported 합계 불일치: {sum(unsupported.values())}")
    duplicate_descriptions = {
        description: count
        for description, count in detail_descriptions.items()
        if count > 1
    }
    if duplicate_descriptions:
        sample = list(duplicate_descriptions.items())[:3]
        raise ValueError(f"detail meta description exact 중복: {sample}")
    return BuildPlan(root, documents, scanned, stats, unsupported, True)


def atomic_apply(plan: BuildPlan) -> None:
    """Write all changes atomically per file and roll back the batch on error."""
    staged: list[tuple[PlannedDocument, Path]] = []
    replaced: list[PlannedDocument] = []
    try:
        for item in plan.changes:
            fd, raw_temp = tempfile.mkstemp(prefix=f".{item.path.name}.phase1-", dir=item.path.parent)
            temp = Path(raw_temp)
            with os.fdopen(fd, "wb") as handle:
                handle.write(item.after)
                handle.flush()
                os.fsync(handle.fileno())
            staged.append((item, temp))
        for item, temp in staged:
            os.replace(temp, item.path)
            replaced.append(item)
    except Exception:
        for item in reversed(replaced):
            fd, raw_temp = tempfile.mkstemp(prefix=f".{item.path.name}.rollback-", dir=item.path.parent)
            temp = Path(raw_temp)
            with os.fdopen(fd, "wb") as handle:
                handle.write(item.before)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, item.path)
        raise
    finally:
        for _item, temp in staged:
            try:
                temp.unlink(missing_ok=True)
            except OSError:
                pass


def default_reference_dir(root: Path) -> Path:
    return root.parent / "참고자료" / "공통자료"


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    script_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="검증/dry-run (기본값)")
    mode.add_argument("--apply", action="store_true", help="검증된 결과를 명시적으로 적용")
    parser.add_argument("--root", type=Path, default=script_root, help="사이트 저장소 루트")
    parser.add_argument("--reference-dir", type=Path, help="공통자료 디렉터리")
    parser.add_argument("--json", action="store_true", help="요약을 JSON으로 출력")
    return parser.parse_args(argv)


def report(plan: BuildPlan, applied: bool, as_json: bool) -> None:
    payload = {
        "mode": "apply" if applied else "check/dry-run",
        "scanned": dict(plan.scanned),
        "scanned_total": sum(plan.scanned.values()),
        "changed_files": len(plan.changes),
        "changed_bytes": sum(abs(len(item.after) - len(item.before)) for item in plan.changes),
        "unsupported": dict(plan.unsupported),
        "unsupported_total": sum(plan.unsupported.values()),
        "mutations": dict(plan.stats.counts),
        "in_memory_idempotency": plan.idempotent,
        "html_written": bool(applied),
    }
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return
    print(f"mode: {payload['mode']}")
    print(f"scanned: {payload['scanned_total']} {payload['scanned']}")
    print(f"projected changed files: {payload['changed_files']}")
    print(f"unsupported: {payload['unsupported_total']} {payload['unsupported']}")
    print(f"mutations: {payload['mutations']}")
    print("in-memory idempotency: PASS")
    print("HTML write: " + ("APPLIED" if applied else "NONE (dry-run)"))


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    root = args.root.resolve()
    reference_dir = (args.reference_dir or default_reference_dir(root)).resolve()
    try:
        plan = build_plan(root, reference_dir)
        if args.apply:
            atomic_apply(plan)
        report(plan, args.apply, args.json)
    except Exception as exc:  # noqa: BLE001 - CLI must fail closed with context
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
