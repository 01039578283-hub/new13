"""Independent, read-only phase-3 fact and copy audit for 소수정예학원.com.

The phase-1 release auditor remains the release-wide static checker.  This
separate auditor focuses on the 3,339 academy detail pages and deliberately
re-derives its fact boundaries from ``참고자료/공통자료/센터정보 정리.csv``.
It never writes repository files.  With ``--projected-content-script`` it
loads that generator's ``build_plan`` result, applies the schema fixer only in
memory, and audits the resulting content -> schema projection.

Strict mode makes the phase-3 improvement targets release-blocking.  Use
``--observe`` to establish a baseline: immutable facts and schema still fail
closed, while not-yet-applied phase-3 copy/freshness/markup targets are
reported as observations.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import html
import importlib.util
import json
import re
import sys
import unicodedata
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Iterable, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
COMMON = ROOT.parent / "참고자료" / "공통자료"
CENTER_CSV = COMMON / "센터정보 정리.csv"
SCHEMA_SCRIPT = ROOT / "scripts" / "smallclass_phase1_schema.py"
RELEASE_SCRIPT = ROOT / "scripts" / "smallclass_phase1_release_audit.py"

BASE_URL = "https://xn--9l4b2jy4h4wan0chw4b.com"
BASELINE_REF = "71d2f3f484233822ca4299d99002b07bb2848e16"
PHASE3_DATE = "2026-08-18"
BASELINE_PUBLISHED_MANIFEST_SHA256 = (
    "ab8bc66de47985102b6fc18df95bfe4e1a5db4cc8db239cd9d096c89dbb9b8c7"
)
BASELINE_SITEMAP_URL_ORDER_SHA256 = (
    "28934132586afc0896b624d20b3cea78e8dd79f48b268abd034e11828c268e42"
)
BASELINE_SITEMAP_NONDETAIL_LASTMOD_SHA256 = (
    "4865bd66864b62551c5cc03351f1dfefc1cc708849c51ea6565632233f8d8e27"
)

EXPECTED_SOURCE_ROWS = 371
EXPECTED_PHYSICAL_CENTERS = 188
EXPECTED_DETAILS = 3339
EXPECTED_NON_DETAILS = 39
EXPECTED_SUPPORTED = 3233
EXPECTED_UNSUPPORTED = 106
EXPECTED_GUIDE_ROWS = 253
EXPECTED_BLANK_GUIDE_ROWS = 118
EXPECTED_GUIDE_PHYSICAL = 131
EXPECTED_BLANK_GUIDE_PHYSICAL = 57

# Targets are based on the independently measured phase-1 corpus.  Counts are
# restricted to supported pages where source uncertainty is not the reason for
# repetition.  The cross-document ceiling is approximately 1% of 3,339.
MAX_ARTICLE_LOCALITY = 12
MAX_MAIN_LOCALITY = 30
MAX_MAIN_EXACT_H1 = 8
MAX_LOCALITY_PER_1000 = 6.0
MAX_H1_PER_1000 = 2.0
MAX_EDITORIAL_SENTENCE_CHARS = 180
MAX_CROSS_DOCUMENT_FREQUENCY = 34
MAX_NORMALIZED_H2_FREQUENCY = 40

QUERY_LABELS = (
    "입시합격전략",
    "입시성공전략",
    "입시지원전략",
    "학습우선순위",
)
MANUSCRIPT_RE = re.compile(r"검색|(?<![A-Za-z])SEO(?![A-Za-z])|키워드", re.I)
IRRELEVANT_SEED_RE = re.compile(
    r"학원\s*(?:"
    r"개원|창업|전자\s*계약|매니저|공지|운영(?:자)?|개인\s*정보\s*관리|"
    r"매출\s*관리|수납\s*관리|고객\s*관리(?:\s*시스템)?|회원\s*관리|"
    r"수강생\s*관리|문서\s*관리|데이터\s*관리|예약\s*관리|상담\s*관리|"
    r"결제\s*(?:관리|시스템)|미납\s*관리|관리\s*(?:프로그램|솔루션|앱)|"
    r"출결\s*앱|직원|상담\s*직원|코디네이터|데스크|행정|브랜드|"
    r"프로모션|이벤트|모집|온라인\s*등록|재\s*등록|휴원|이전|"
    r"문자\s*발송|자료\s*실|소식|알림\s*톡|할인|혜택|환불"
    r")",
    re.I,
)
KNOWN_BAD_TEXT = (
    "중등 영어은",
    "초등 영어은",
    "고등 영어은",
    "사대부고을",
    "여고을",
    "외고을",
    "초을 확인",
    "학교을 확인",
    "요일를",
    "시간를",
    "범위을",
    "결과을",
    "진도을",
    "분류을",
    "학원로",
    "과제 완료 기준을 기준으로",
    "고등 과정 과정",
    "초등초등학생",
    "상담에서는 제공 자료에는",
)
KNOWN_BAD_REGEX = (
    re.compile(r"[\uac00-\ud7a3]+(?:고|여고|외고|초)을(?![\uac00-\ud7a3])"),
    re.compile(r"(?:초등|중등|고등)\s+영어은"),
    re.compile(r"[\uac00-\ud7a3]+관리을(?![\uac00-\ud7a3])"),
    re.compile(r"(?:상담|(?:[\uac00-\ud7a3]+(?:\s+[\uac00-\ud7a3]+){0,4})?학원)에서는\s+제공\s+자료에는"),
)

MISSING_LOCATION_GUIDE_STATUS = "missing-location-guide"
UNCONFIRMED_GRADE_STATUS = "unconfirmed-grade"
MISSING_SOURCE_CUE_RE = re.compile(
    r"미기재|공란|비어|빈\s*(?:항목|칸|값)|없|확인되지|제시되지|"
    r"표시되지|기재되지|기재되어\s*있지|적혀\s*있지|제공되지|"
    r"상담\s*전\s*확인\s*항목"
)
GUIDE_POSITIVE_PREMISE_RE = re.compile(
    r"(?:"
    r"제공(?:된)?\s*(?:위치\s*)?(?:안내|문구)|"
    r"(?:현재\s*)?위치\s*(?:안내(?:\s*원문|값)?|문구|원문|값)"
    r")"
    r"\s*(?:을|를|와|과|은|는|이|가)?\s*"
    r"(?:참고|확인(?:한\s*뒤)?|대조|읽|활용|기준(?:으로)?|사용|일정에\s*넣)"
)
GRADE_TOKEN_RE = re.compile(r"(?<![가-힣A-Za-z0-9])(?:초[1-6]|[중고][1-3])")
MONEY_AMOUNT_RE = re.compile(r"\d[\d,]*(?:\.\d+)?\s*(?:만원|천원|원)(?![가-힣])")

FAQ_TOPIC_PATTERNS: dict[str, re.Pattern[str]] = {
    "grades": re.compile(
        r"학년|(?<![가-힣A-Za-z0-9])(?:초[1-6]|[중고][1-3])|초등|중등|고등"
    ),
    "schools": re.compile(r"학교|학교명"),
    "location": re.compile(r"주소|위치|방문|동선|경로|입구|건물|서비스\s*지역"),
    "fee": re.compile(r"교습비|비용|금액|교재비|총액|적용액"),
}
FAQ_SLOT_TOPICS = ("grades", "schools", "location", "fee")

# These are semantic positive-premise families, copied independently into the
# auditor rather than imported from the content generator.  They are forbidden
# only on source-unsupported pages and outside the exact, subject-qualified
# source-state sentences checked below.
UNSUPPORTED_INTRO_PREMISES = (
    "센터명·주소·등록값과 가능 학년 자료",
    "제공 자료에 적힌 식별·학년·학교 정보",
    "센터 원자료와 과목별 학년 표기",
    "확인된 센터 사실과 학년 자료",
    "원문에 있는 주소·등록·학년 정보",
    "상단에 제시된 센터·학년·학교 자료",
    "원자료로 확인되는 이름·주소·학년 값",
    "학년 표기와 센터 식별 자료",
)
UNSUPPORTED_ARTICLE_GUIDANCE_PREMISES = (
    "상담 메모에 학년을 옮길 때",
    "가능 학년 자료를 읽은 뒤",
    "학년 범위를 비교할 때",
    "등록 판단에 학년값을 쓸 때",
    "과목별 학년을 확인한 다음",
    "제공 학년값을 기록하면서",
    "학년 목록과 상담 일정을 나눌 때",
    "확인된 학년값을 기준으로 삼되",
    "학년 정보를 계획에 넣기 전에",
    "제공 목록을 확인한 다음",
    "학년값이 실제 반 편성을 뜻한다고 단정하지 마세요",
    "학년 목록 밖의 내용은 상담 답변으로 보완하세요",
    "현재 답변과 원문 학년값을 한 문장에 섞지 마세요",
)
UNSUPPORTED_FAQ_QUESTION_PREMISES = (
    "센터 학년값을 검토하며",
    "제공된 학년 목록을 읽고",
    "원문 학년값을 메모하면서",
    "확인된 학년값을 바탕으로",
    "과목별 범위를 비교하며",
    "과목별 학년값은 어디에 적혀 있나요?",
    "학년 목록 뒤에 어떤 질문을 더해야 하나요?",
    "학년 범위는 현재 수업과 같은 뜻인가요?",
    "학년 정보를 등록 판단에 어떻게 쓰나요?",
    "원자료 학년값은 무엇을 뜻하나요?",
    "학년 범위를 상담에서 어떻게 대조하나요?",
)
UNSUPPORTED_FAQ_GUIDANCE_PREMISES = (
    "학년값과 실제 시간표를 서로 다른 항목으로 적으세요",
    "현재 운영 조건은 원문 학년값과 구분하세요",
)
UNSUPPORTED_FAQ_PREP_PREMISES = (
    "원자료의 센터·학년·학교·주소 사실과 개설·일정·비용 상담 답변을 나누어 쓰세요",
    "학년·학교·주소의 원자료와 실제 개설·시간표·교재·비용 조건을 분리하세요",
    "원자료 학년·학교·주소와 상담에서 받은 일정·교재·비용의 기준일을 함께 적으세요",
    "확인된 센터·학년·학교·주소 사실만 옮기고 일정·교재·비용은 질문으로 남기세요",
)


def clean(value: object) -> str:
    return re.sub(r"\s+", " ", html.unescape(str(value or ""))).strip()


def contains_any(value: str, phrases: Sequence[str]) -> bool:
    return any(phrase in value for phrase in phrases)


def topic_hits(value: str, address: str = "") -> set[str]:
    """Return FAQ intent families after masking the authoritative address."""
    candidate = clean(value)
    if address:
        candidate = candidate.replace(address, " ")
    return {
        topic for topic, pattern in FAQ_TOPIC_PATTERNS.items()
        if pattern.search(candidate)
    }


def fee_source_state_ok(value: str, fee_url: str) -> bool:
    """Validate only the source state, never a page-specific fee amount."""
    value = clean(value)
    if fee_url:
        return (
            value.count("센터 공통 교습비 링크") == 1
            and bool(re.search(r"상단|핵심정보|옆의", value))
            and not MISSING_SOURCE_CUE_RE.search(value)
        )
    return (
        value.count("교습비 링크") == 1
        and "센터 공통 교습비 링크" not in value
        and "미기재 상태" in value
        and bool(MISSING_SOURCE_CUE_RE.search(value))
    )


def unsupported_grade_state_ok(
    value: str,
    subjects: Sequence[str],
    grade_map: Mapping[str, Sequence[str]],
) -> bool:
    """Require a negative state, allowing only subject-qualified partial facts."""
    value = clean(value)
    if len(subjects) == 1:
        subject = subjects[0]
        return (
            not tuple(grade_map.get(subject, ()))
            and not GRADE_TOKEN_RE.search(value)
            and bool(MISSING_SOURCE_CUE_RE.search(value))
        )
    if tuple(subjects) != ("영어", "수학"):
        return False
    expected_tokens: list[str] = []
    last_index = -1
    for subject in subjects:
        grades = tuple(clean(item) for item in grade_map.get(subject, ()) if clean(item))
        expected_tokens.extend(grades)
        expected = "·".join(grades) if grades else "원자료 미기재"
        phrase = f"{subject} 학년값은 {expected}"
        index = value.find(phrase)
        if value.count(phrase) != 1 or index <= last_index:
            return False
        last_index = index
    if GRADE_TOKEN_RE.findall(value) != expected_tokens:
        return False
    collective = [
        sentence for sentence in sentences(value)
        if re.search(r"영어\s*·\s*수학.{0,40}대상\s*학년", sentence)
    ]
    return bool(collective) and all(MISSING_SOURCE_CUE_RE.search(item) for item in collective)


def source_aware_self_test() -> dict[str, object]:
    """Synthetic fail-closed tests for the three source-aware contracts."""
    checks = {
        "blank_guide_premise_detected": bool(
            GUIDE_POSITIVE_PREMISE_RE.search(
                "제공된 위치 문구를 확인한 뒤에는 위치 안내가 미기재라 다시 물어보세요."
            )
        ),
        "blank_guide_current_premise_detected": bool(
            GUIDE_POSITIVE_PREMISE_RE.search(
                "센터 주소를 일정에 넣기 전에는 현재 위치 안내는 확인 날짜와 함께 남기세요."
            )
        ),
        "blank_guide_negative_allowed": not GUIDE_POSITIVE_PREMISE_RE.search(
            "원자료 위치 안내가 미기재라 방문 방법은 상담에서 확인하세요."
        ),
        "blank_guide_negative_cue": bool(
            MISSING_SOURCE_CUE_RE.search(
                "원자료 위치 안내가 미기재라 방문 방법은 상담에서 확인하세요."
            )
        ),
        "dual_topic_detected": topic_hits(
            "센터 주소와 교습비 링크는 어디에서 확인하나요?"
        ) == {"location", "fee"},
        "single_location_detected": topic_hits(
            "센터 주소는 어디에서 확인하나요?"
        ) == {"location"},
        "single_fee_detected": topic_hits(
            "교습비 링크는 어디에서 확인하나요?"
        ) == {"fee"},
        "fee_link_state": fee_source_state_ok(
            "센터 공통 교습비 링크는 상단 핵심정보에서 확인하세요.",
            "https://example.invalid/fee",
        ),
        "fee_blank_state": fee_source_state_ok(
            "교습비 링크는 원자료 미기재 상태이므로 직접 확인하세요.", ""
        ),
        "fee_state_cross_rejected": not fee_source_state_ok(
            "교습비 링크는 원자료 미기재 상태입니다.",
            "https://example.invalid/fee",
        ),
        "fee_state_duplicate_rejected": not fee_source_state_ok(
            "센터 공통 교습비 링크는 상단에 있고 센터 공통 교습비 링크를 확인하세요.",
            "https://example.invalid/fee",
        ),
        "single_blank_grade": unsupported_grade_state_ok(
            "고등 영어 가능 학년은 원자료 미기재 상태입니다.",
            ("영어",),
            {"영어": ()},
        ),
        "partial_combined_grade": unsupported_grade_state_ok(
            "영어 학년값은 중1·중2이고 수학 학년값은 원자료 미기재입니다. "
            "영어·수학을 함께 보는 대상 학년은 원자료에서 확인되지 않습니다.",
            ("영어", "수학"),
            {"영어": ("중1", "중2"), "수학": ()},
        ),
        "combined_collective_rejected": not unsupported_grade_state_ok(
            "영어 학년값은 중1·중2이고 수학 학년값은 원자료 미기재입니다. "
            "영어·수학을 함께 보는 대상 학년은 중1입니다.",
            ("영어", "수학"),
            {"영어": ("중1", "중2"), "수학": ()},
        ),
        "combined_duplicate_subject_rejected": not unsupported_grade_state_ok(
            "영어 학년값은 중1·중2이고 영어 학년값은 중1·중2이며 "
            "수학 학년값은 원자료 미기재입니다. "
            "영어·수학을 함께 보는 대상 학년은 원자료에서 확인되지 않습니다.",
            ("영어", "수학"),
            {"영어": ("중1", "중2"), "수학": ()},
        ),
        "positive_grade_premise_detected": contains_any(
            "원자료로 확인되는 이름·주소·학년 값을 정리했습니다.",
            UNSUPPORTED_INTRO_PREMISES,
        ),
        "negative_grade_copy_allowed": not contains_any(
            "학년 항목은 미기재이며 실제 대상은 상담에서 확인하세요.",
            UNSUPPORTED_INTRO_PREMISES
            + UNSUPPORTED_ARTICLE_GUIDANCE_PREMISES
            + UNSUPPORTED_FAQ_QUESTION_PREMISES
            + UNSUPPORTED_FAQ_GUIDANCE_PREMISES
            + UNSUPPORTED_FAQ_PREP_PREMISES,
        ),
    }
    failed = sorted(key for key, passed in checks.items() if not passed)
    if failed:
        raise AssertionError(f"source-aware synthetic self-test failed: {failed}")
    return {"status": "pass", "cases": len(checks)}


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def line_sha(values: Iterable[str]) -> str:
    payload = "\n".join(values) + "\n"
    return sha256_bytes(payload.encode("utf-8"))


def import_script(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def decode_utf8(value: bytes, path: Path) -> str:
    if value.startswith(b"\xef\xbb\xbf"):
        value = value[3:]
    try:
        return value.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise RuntimeError(f"{path}: not UTF-8: {exc}") from exc


def clean_location_guide(raw: str) -> str:
    """Apply only the approved deterministic source-guide corrections."""
    value = html.unescape(raw or "")
    value = re.sub(r"(?:https?://|www\.)[^\s<>()]+", " ", value, flags=re.I)
    value = re.sub(r"학원\s*위치\s*안내드립니다", " ", value)
    value = re.sub(r"\^{2,}", "", value)
    value = value.replace("엘레베이터", "엘리베이터")
    value = re.sub("[\U0001F000-\U0001FAFF\u2600-\u27BF]", " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    value = re.sub(r"^[~\s]+|[~\s]+$", "", value)
    value = re.sub(r"\s+([,.;:!?])", r"\1", value)
    value = re.sub(r"([,.!?;:])(?=[가-힣A-Za-z])", r"\1 ", value)
    value = re.sub(r"\(\s*\)", " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value


@dataclass(frozen=True)
class RawFact:
    locality: str
    guide_raw: str
    guide_clean: str
    registered_name: str
    registration: str
    address: str


@dataclass
class Audit:
    observe: bool = False
    errors: list[dict[str, str]] = field(default_factory=list)
    observations: list[dict[str, str]] = field(default_factory=list)
    counts: Counter[str] = field(default_factory=Counter)
    observation_counts: Counter[str] = field(default_factory=Counter)

    def hard(self, condition: bool, code: str, detail: str) -> None:
        if condition:
            return
        self.counts[code] += 1
        if sum(item["code"] == code for item in self.errors) < 5:
            self.errors.append({"code": code, "detail": detail})

    def target(self, condition: bool, code: str, detail: str) -> None:
        if condition:
            return
        if not self.observe:
            self.hard(False, code, detail)
            return
        self.observation_counts[code] += 1
        if sum(item["code"] == code for item in self.observations) < 5:
            self.observations.append({"code": code, "detail": detail})


def load_raw_facts() -> tuple[dict[str, RawFact], list[dict[str, str]]]:
    with CENTER_CSV.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if len(rows) != EXPECTED_SOURCE_ROWS:
        raise RuntimeError(f"source rows {len(rows)} != {EXPECTED_SOURCE_ROWS}")
    required = {
        "근처 수업가능 동네",
        "센터명",
        "센터 교습비",
        "교육지원청명칭",
        "교육지원청 등록번호",
        "센터 주소",
        "위치안내",
    }
    missing = required - set(rows[0])
    if missing:
        raise RuntimeError(f"source fields missing: {sorted(missing)}")
    result: dict[str, RawFact] = {}
    for row in rows:
        locality = clean(row["근처 수업가능 동네"])
        fact = RawFact(
            locality=locality,
            guide_raw=clean(row["위치안내"]),
            guide_clean=clean_location_guide(row["위치안내"]),
            registered_name=clean(row["교육지원청명칭"]),
            registration=clean(row["교육지원청 등록번호"]),
            address=clean(row["센터 주소"]),
        )
        if locality in result:
            raise RuntimeError(f"duplicate source locality: {locality}")
        result[locality] = fact
    return result, rows


def source_boundary_metrics(
    rows: list[dict[str, str]], schema_rows: Mapping[str, object]
) -> dict[str, object]:
    physical: dict[tuple[str, str, str], list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        key = tuple(
            clean(row[column])
            for column in ("센터명", "센터 주소", "교육지원청 등록번호")
        )
        physical[key].append(row)
    guides = [clean(row["위치안내"]) for row in rows]
    clean_guides = [clean_location_guide(value) for value in guides]
    guide_physical = sum(
        bool({clean_location_guide(row["위치안내"]) for row in group} - {""})
        for group in physical.values()
    )
    multi_area = sum(len(group) > 1 for group in physical.values())
    row_locality_off_address = sum(
        clean(row["근처 수업가능 동네"]) not in clean(row["센터 주소"])
        for row in rows
    )
    physical_address_has_no_area = sum(
        not any(
            clean(row["근처 수업가능 동네"]) in clean(group[0]["센터 주소"])
            for row in group
        )
        for group in physical.values()
    )
    names_to_physical: dict[str, set[tuple[str, str, str]]] = defaultdict(set)
    for key, group in physical.items():
        names_to_physical[clean(group[0]["교육지원청명칭"])].add(key)
    shared_registered = {
        name: len(keys) for name, keys in names_to_physical.items() if len(keys) > 1
    }
    sejong = [
        {
            "locality": clean(row["근처 수업가능 동네"]),
            "raw_region": clean(row["지역"]),
            "raw_district": clean(row["시or구"]),
            "address": clean(row["센터 주소"]),
        }
        for row in rows
        if "세종특별자치시" in clean(row["센터 주소"])
    ]
    return {
        "source_rows": len(rows),
        "source_localities": len(schema_rows),
        "physical_centers": len(physical),
        "multi_area_physical_centers": multi_area,
        "single_area_physical_centers": len(physical) - multi_area,
        "guide_rows_nonblank": sum(bool(value) for value in clean_guides),
        "guide_rows_blank": sum(not value for value in clean_guides),
        "guide_unique_clean": len({value for value in clean_guides if value}),
        "guide_physical_nonblank": guide_physical,
        "guide_physical_blank": len(physical) - guide_physical,
        "raw_guide_url_rows": sum(bool(re.search(r"https?://", value, re.I)) for value in guides),
        "raw_guide_emoticon_or_tilde_rows": sum(bool(re.search(r"\^\^|~", value)) for value in guides),
        "raw_guide_typo_rows": sum("엘레베이터" in value for value in guides),
        "row_locality_absent_from_physical_address": row_locality_off_address,
        "physical_address_contains_no_served_area_alias": physical_address_has_no_area,
        "registered_name_values": len(names_to_physical),
        "registered_names_shared_across_physical_centers": len(shared_registered),
        "registered_name_max_physical_reuse": max(shared_registered.values(), default=1),
        "sejong_rows": sejong,
    }


def read_projection(content_script: Path | None) -> tuple[dict[Path, bytes], dict[str, object]]:
    if content_script is None:
        return {}, {"enabled": False}
    module = import_script("smallclass_phase3_projected_content", content_script.resolve())
    build_plan = getattr(module, "build_plan", None)
    if not callable(build_plan):
        raise RuntimeError(f"{content_script}: build_plan(root, reference_dir) missing")
    plan = build_plan(ROOT, COMMON)
    documents = list(getattr(plan, "documents", []))
    if not getattr(plan, "idempotent", False):
        raise RuntimeError("projected content plan is not idempotent")
    overrides = {item.path.resolve(): item.after for item in documents}
    return overrides, {
        "enabled": True,
        "script": str(content_script.resolve()),
        "documents": len(documents),
        "changed": sum(item.before != item.after for item in documents),
        "idempotent": True,
    }


def bytes_for(path: Path, overrides: Mapping[Path, bytes]) -> bytes:
    return overrides.get(path.resolve(), path.read_bytes())


def target_manifest(paths: Iterable[Path]) -> dict[str, str]:
    return {
        path.relative_to(ROOT).as_posix(): sha256_bytes(path.read_bytes())
        for path in sorted(set(paths))
    }


def node_types(node: dict) -> set[str]:
    value = node.get("@type", [])
    if not isinstance(value, list):
        value = [value]
    return {clean(item) for item in value if clean(item)}


def one_type(nodes: list[dict], expected: str) -> dict | None:
    matches = [node for node in nodes if expected in node_types(node)]
    return matches[0] if len(matches) == 1 else None


def selector_nodes(dom: object, field: str) -> list[object]:
    return dom.find_all(attr="data-source-field", value=field)


def within(node: object, tag: str | None = None, cls: str | None = None) -> bool:
    current = node
    while current is not None:
        if (tag is None or current.tag == tag) and (cls is None or current.has_class(cls)):
            return True
        current = current.parent
    return False


def descends_from(node: object, ancestor: object) -> bool:
    current = node
    while current is not None:
        if current is ancestor:
            return True
        current = current.parent
    return False


def authored_sections(article: object) -> list[object]:
    return [section for section in article.find_all("section") if section.find_all("h2")]


def editorial_paragraphs(article: object) -> list[str]:
    result: list[str] = []
    for node in article.find_all("p"):
        if node.attrs.get("data-source-field"):
            continue
        if within(node, cls="academy-registration-facts"):
            continue
        if node.attrs.get("data-source-status") == "unconfirmed-grade":
            continue
        text = node.text()
        if text:
            result.append(text)
    return result


def sentences(paragraph: str) -> list[str]:
    result = []
    for value in re.split(r"(?<=[.!?])(?:[”’\"']*)\s+", clean(paragraph)):
        value = clean(value)
        if value:
            result.append(value)
    return result


def normalized_literal(value: str) -> str:
    value = unicodedata.normalize("NFC", clean(value)).lower()
    return re.sub(r"[^0-9a-z가-힣]", "", value)


def source_neutral(
    value: str,
    source: object,
    physical: object,
    profile: object,
    schema: ModuleType,
) -> str:
    substitutions: list[tuple[str, str]] = []
    for item in physical.areas:
        substitutions.append((item, " LOCALITY "))
    substitutions.extend(
        (
            (source.locality, " LOCALITY "),
            (source.center_name, " CENTER "),
            (source.address, " ADDRESS "),
            (source.office_name, " REGISTEREDNAME "),
            (source.registration, " REGISTRATION "),
            (source.region, " REGION "),
            (source.district, " DISTRICT "),
            (physical.address_region, " REGION "),
            (physical.address_locality, " ADDRESSLOCALITY "),
            (profile.category, " CATEGORY "),
            (f"{source.locality} {profile.category}", " HONE "),
        )
    )
    for schools in source.schools.values():
        substitutions.extend((school, " SCHOOL ") for school in schools)
    for grades in source.grades.values():
        substitutions.extend((grade, " GRADE ") for grade in grades)
    result = clean(value)
    for raw, token in sorted(
        {(clean(raw), token) for raw, token in substitutions if clean(raw)},
        key=lambda item: len(item[0]),
        reverse=True,
    ):
        result = result.replace(raw, token)
    result = re.sub(r"(?:초[1-6]|[중고][1-3])", " GRADE ", result)
    result = re.sub(r"\d+(?:[.,]\d+)?", " NUMBER ", result)
    return normalized_literal(result)


def quantiles(values: list[int | float]) -> dict[str, float | int]:
    if not values:
        return {"min": 0, "p50": 0, "p95": 0, "p99": 0, "max": 0}
    ordered = sorted(values)

    def at(fraction: float) -> int | float:
        return ordered[min(len(ordered) - 1, int((len(ordered) - 1) * fraction))]

    return {
        "min": ordered[0],
        "p50": at(0.50),
        "p95": at(0.95),
        "p99": at(0.99),
        "max": ordered[-1],
    }


def sitemap_entries(value: bytes) -> list[tuple[str, str]]:
    root = ET.fromstring(value)
    result: list[tuple[str, str]] = []
    for url in root:
        if not str(url.tag).endswith("url"):
            continue
        loc = ""
        lastmod = ""
        for child in url:
            if str(child.tag).endswith("loc"):
                loc = clean(child.text)
            elif str(child.tag).endswith("lastmod"):
                lastmod = clean(child.text)
        result.append((loc, lastmod))
    return result


def audit(args: argparse.Namespace) -> dict[str, object]:
    schema = import_script("smallclass_phase3_schema_dependency", SCHEMA_SCRIPT)
    release = import_script("smallclass_phase3_release_dependency", RELEASE_SCRIPT)
    self_test = source_aware_self_test()
    audit_result = Audit(observe=args.observe)
    raw_facts, raw_rows = load_raw_facts()
    by_slug, physical_by_key = schema.load_sources()
    source_metrics = source_boundary_metrics(raw_rows, by_slug)

    audit_result.hard(len(by_slug) == EXPECTED_SOURCE_ROWS, "source_rows", str(len(by_slug)))
    audit_result.hard(
        len(physical_by_key) == EXPECTED_PHYSICAL_CENTERS,
        "physical_centers",
        str(len(physical_by_key)),
    )
    for key, expected in (
        ("guide_rows_nonblank", EXPECTED_GUIDE_ROWS),
        ("guide_rows_blank", EXPECTED_BLANK_GUIDE_ROWS),
        ("guide_physical_nonblank", EXPECTED_GUIDE_PHYSICAL),
        ("guide_physical_blank", EXPECTED_BLANK_GUIDE_PHYSICAL),
    ):
        audit_result.hard(source_metrics[key] == expected, f"source_{key}", str(source_metrics[key]))
    sejong_physical = [
        item for item in physical_by_key.values() if item.address_region == "세종특별자치시"
    ]
    audit_result.hard(
        len(sejong_physical) == 1
        and sejong_physical[0].address_locality == "새롬동"
        and set(sejong_physical[0].areas) == {"새롬동", "다정동"},
        "sejong_boundary",
        repr([(item.areas, item.address_locality) for item in sejong_physical]),
    )

    details = schema.discover_detail_pages(by_slug)
    non_details = schema.discover_non_detail_pages()
    target_paths = [path for path, _profile, _source in details] + non_details
    sitemap_path = ROOT / "sitemap.xml"
    pre_targets = target_manifest(target_paths + [sitemap_path])
    pre_external = schema.external_manifest(set(target_paths))
    overrides, projection = read_projection(args.projected_content_script)

    final_texts: dict[Path, str] = {}
    final_candidates: list[object] = []
    schema_projection_changes = 0
    for path, profile, source in details:
        original = decode_utf8(bytes_for(path, overrides), path)
        physical = physical_by_key[source.center_key]
        try:
            first_candidate = schema.build_detail_candidate(
                path, profile, source, physical, original_text=original
            )
            if args.projected_content_script is None:
                audit_result.hard(
                    first_candidate.transformed == original,
                    "schema_dry_run_changed",
                    path.relative_to(ROOT).as_posix(),
                )
                final_text = original
            else:
                final_text = first_candidate.transformed
                schema_projection_changes += final_text != original
            second_candidate = schema.build_detail_candidate(
                path, profile, source, physical, original_text=final_text
            )
            audit_result.hard(
                second_candidate.transformed == final_text,
                "schema_final_idempotency",
                path.relative_to(ROOT).as_posix(),
            )
            final_candidates.append(second_candidate)
            final_texts[path] = final_text
        except Exception as exc:  # continue to expose the full failing set
            audit_result.hard(False, "detail_schema_fact", f"{path.relative_to(ROOT)}: {exc}")

    for path in non_details:
        original = decode_utf8(bytes_for(path, overrides), path)
        try:
            first_candidate = schema.build_non_detail_candidate(path, original_text=original)
            if args.projected_content_script is None:
                audit_result.hard(
                    first_candidate.transformed == original,
                    "schema_dry_run_changed",
                    path.relative_to(ROOT).as_posix(),
                )
                final_text = original
            else:
                final_text = first_candidate.transformed
                schema_projection_changes += final_text != original
            second_candidate = schema.build_non_detail_candidate(path, original_text=final_text)
            audit_result.hard(
                second_candidate.transformed == final_text,
                "schema_final_idempotency",
                path.relative_to(ROOT).as_posix(),
            )
            final_candidates.append(second_candidate)
            final_texts[path] = final_text
        except Exception as exc:
            audit_result.hard(False, "non_detail_schema_role", f"{path.relative_to(ROOT)}: {exc}")

    if len(final_candidates) == EXPECTED_DETAILS + EXPECTED_NON_DETAILS:
        try:
            global_contract = schema.validate_global_contract(final_candidates, physical_by_key)
        except Exception as exc:
            global_contract = {}
            audit_result.hard(False, "global_schema_role", str(exc))
    else:
        global_contract = {}
        audit_result.hard(
            False,
            "candidate_coverage",
            f"{len(final_candidates)} != {EXPECTED_DETAILS + EXPECTED_NON_DETAILS}",
        )

    supported_count = 0
    unsupported_count = 0
    guide_present = 0
    guide_blank = 0
    registration_pages = 0
    core_fact_parity_pages = 0
    grade_parity_pages = 0
    school_parity_pages = 0
    fee_parity_pages = 0
    physical_org_parity_pages = 0
    faq_parity_pages = 0
    faq_out_of_source_grade_pages = 0
    guide_missing_status_pages = 0
    guide_positive_premise_pages = 0
    unsupported_premise_pages: set[str] = set()
    unsupported_premise_occurrences = 0
    unsupported_grade_state_pages = 0
    faq_single_intent_pages = 0
    faq_location_source_pages = 0
    faq_fee_source_pages = 0
    faq_topic_failures: Counter[str] = Counter()
    faq_topic_failure_categories: dict[str, Counter[str]] = {
        topic: Counter() for topic in FAQ_SLOT_TOPICS
    }
    faq_location_source_failure_categories: Counter[str] = Counter()
    faq_fee_source_failure_categories: Counter[str] = Counter()
    guide_positive_premise_categories: Counter[str] = Counter()
    unsupported_premise_page_categories: Counter[str] = Counter()
    unsupported_premise_occurrence_categories: Counter[str] = Counter()
    published_manifest: list[str] = []
    modified_values: Counter[str] = Counter()
    faq_counts: list[int] = []
    main_locality_counts: list[int] = []
    article_locality_counts: list[int] = []
    main_h1_counts: list[int] = []
    locality_densities: list[float] = []
    h1_densities: list[float] = []
    article_lengths: list[int] = []
    paragraph_lengths: list[int] = []
    sentence_lengths: list[int] = []
    whole_article_documents: dict[str, list[str]] = defaultdict(list)
    paragraph_documents: dict[str, set[str]] = defaultdict(set)
    sentence_documents: dict[str, set[str]] = defaultdict(set)
    heading_documents: dict[str, set[str]] = defaultdict(set)
    within_page_duplicate_sentence_pages = 0
    query_page_counts: Counter[str] = Counter()
    query_occurrences: Counter[str] = Counter()

    for path, profile, source in details:
        text = final_texts.get(path)
        if text is None:
            continue
        relative = path.relative_to(ROOT).as_posix()
        physical = physical_by_key[source.center_key]
        fact = raw_facts[source.locality]
        supported = schema.page_supported(source, profile)
        supported_count += supported
        unsupported_count += not supported
        dom = release.parse_dom(text)
        mains = dom.find_all("main")
        articles = dom.find_all("article", cls="academy-main-article")
        audit_result.hard(len(mains) == 1, "main_count", f"{relative}: {len(mains)}")
        audit_result.target(len(articles) == 1, "article_count", f"{relative}: {len(articles)}")
        if len(mains) != 1 or len(articles) != 1:
            continue
        main = mains[0]
        article = articles[0]
        sections = authored_sections(article)
        audit_result.target(
            len(sections) == 5,
            "source_aware_article_sections",
            f"{relative}: {len(sections)}",
        )
        main_text = main.text()
        article_text = article.text()
        h1_nodes = dom.find_all("h1")
        h1 = h1_nodes[0].text() if len(h1_nodes) == 1 else ""
        audit_result.hard(
            h1 == f"{source.locality} {profile.category}",
            "h1_identity",
            f"{relative}: {h1!r}",
        )

        graph = schema.extract_graph(text, path)
        nodes = graph["@graph"]
        article_schema = one_type(nodes, "Article")
        faq_schema = one_type(nodes, "FAQPage")
        physical_schema = one_type(nodes, "EducationalOrganization")
        physical_ok = physical_schema == schema.expected_physical_node(physical)
        physical_org_parity_pages += physical_ok
        audit_result.hard(
            physical_ok,
            "physical_org_source_parity",
            relative,
        )

        # Recompute reader-visible CSV parity here even though the schema
        # fixer also validates it.  This keeps the phase-3 audit independent
        # from a false positive in either implementation.
        try:
            visible_facts = schema.visible_fact_pairs(text, path)
        except Exception as exc:
            visible_facts = {}
            audit_result.hard(False, "visible_fact_parse", f"{relative}: {exc}")
        core_ok = (
            schema.clean_html(visible_facts.get("센터 기준", "")) == source.center_name
            and schema.clean_html(visible_facts.get("제공 주소", "")) == source.address
        )
        core_fact_parity_pages += core_ok
        audit_result.hard(core_ok, "visible_core_fact_parity", relative)

        expected_grade_map = schema.profile_grades(source, profile)
        grade_labels = {label for label in visible_facts if label.endswith("수업 가능 학년")}
        expected_grade_labels = {f"{subject} 수업 가능 학년" for subject in profile.subjects}
        grade_ok = grade_labels == expected_grade_labels
        if grade_ok:
            for subject, expected_values in expected_grade_map.items():
                raw_grade = visible_facts[f"{subject} 수업 가능 학년"]
                actual_values = tuple(
                    schema.clean_html(value)
                    for value in re.findall(
                        r"<span\b[^>]*>(.*?)</span>", raw_grade, re.I | re.S
                    )
                    if schema.clean_html(value)
                    and "원자료 미기재" not in schema.clean_html(value)
                )
                if actual_values != expected_values:
                    grade_ok = False
                    break
        grade_parity_pages += grade_ok
        audit_result.hard(grade_ok, "visible_grade_source_parity", relative)

        school_labels = [label for label in visible_facts if "학교" in label and "참고" in label]
        expected_schools = schema.expected_schools(source, profile)
        school_ok = school_labels == [profile.school_label]
        if school_ok:
            actual_schools = tuple(
                schema.clean_html(value)
                for value in re.findall(
                    r"<span\b[^>]*>(.*?)</span>",
                    visible_facts[profile.school_label],
                    re.I | re.S,
                )
                if schema.clean_html(value)
                and "원자료 미기재" not in schema.clean_html(value)
                and "정보 없음" not in schema.clean_html(value)
                and "제공 목록 없음" not in schema.clean_html(value)
            )
            school_ok = actual_schools == expected_schools
        school_parity_pages += school_ok
        audit_result.hard(school_ok, "visible_school_source_parity", relative)

        tuition_links = re.findall(
            r'<a\b(?=[^>]*\bacademy-tuition-link\b)[^>]*\bhref=["\']([^"\']+)["\']',
            text,
            re.I,
        )
        fee_ok = tuition_links == ([source.fee_url] if source.fee_url else [])
        fee_parity_pages += fee_ok
        audit_result.hard(fee_ok, "visible_fee_source_parity", relative)

        visible_faq = release.visible_faq(dom)
        schema_faq = release.schema_faq(nodes)
        faq_counts.append(len(visible_faq))
        faq_parity_pages += visible_faq == schema_faq
        audit_result.hard(
            visible_faq == schema_faq,
            "faq_visible_schema_parity",
            f"{relative}: visible={len(visible_faq)} schema={len(schema_faq)}",
        )
        audit_result.hard(
            len(visible_faq) == 4,
            "faq_count",
            f"{relative}: {len(visible_faq)}",
        )
        faq_details = [
            node for node in dom.find_all("details", cls="academy-faq-item")
            if descends_from(node, main)
        ]
        grade_paragraphs = sections[1].find_all("p") if len(sections) == 5 else []
        grade_fact_node = grade_paragraphs[0] if grade_paragraphs else None
        expected_marker = UNCONFIRMED_GRADE_STATUS if not supported else None
        marker_ok = (
            article.attrs.get("data-source-status") == expected_marker
            and grade_fact_node is not None
            and grade_fact_node.attrs.get("data-source-status") == expected_marker
            and len(faq_details) == 4
            and all(
                node.attrs.get("data-source-status") == expected_marker
                for node in faq_details
            )
        )
        audit_result.target(
            marker_ok,
            "source_status_marker_exact",
            f"{relative}: article={article.attrs.get('data-source-status')!r} "
            f"grade={(grade_fact_node.attrs.get('data-source-status') if grade_fact_node else None)!r} "
            f"faq={[node.attrs.get('data-source-status') for node in faq_details]}",
        )

        if len(visible_faq) == 4:
            faq_topics_ok = True
            for index, (question, answer) in enumerate(visible_faq):
                expected_topic = FAQ_SLOT_TOPICS[index]
                question_topics = topic_hits(question, source.address)
                answer_topics = topic_hits(answer, source.address)
                slot_ok = (
                    question_topics == {expected_topic}
                    and answer_topics == {expected_topic}
                )
                faq_topics_ok &= slot_ok
                if not slot_ok:
                    faq_topic_failures[expected_topic] += 1
                    faq_topic_failure_categories[expected_topic][profile.category] += 1
                audit_result.target(
                    slot_ok,
                    f"faq_{expected_topic}_single_intent",
                    f"{relative}: q={sorted(question_topics)} a={sorted(answer_topics)}",
                )
            faq_single_intent_pages += faq_topics_ok

            location_question, location_answer = visible_faq[2]
            all_faq_text = " ".join(
                question + " " + answer for question, answer in visible_faq
            )
            quoted_addresses = re.findall(r"“([^”]+)”", location_answer)
            location_source_ok = (
                location_answer.count(source.address) == 1
                and all_faq_text.count(source.address) == 1
                and quoted_addresses == [source.address]
                and not FAQ_TOPIC_PATTERNS["fee"].search(
                    location_question + " " + location_answer
                )
            )
            faq_location_source_pages += location_source_ok
            if not location_source_ok:
                faq_location_source_failure_categories[profile.category] += 1
            audit_result.hard(
                location_source_ok,
                "faq_location_source_state",
                f"{relative}: address={location_answer.count(source.address)}/"
                f"{all_faq_text.count(source.address)} quoted={quoted_addresses}",
            )

            fee_question, fee_answer = visible_faq[3]
            fee_state_ok = (
                fee_source_state_ok(fee_answer, source.fee_url)
                and source.address not in fee_answer
                and not FAQ_TOPIC_PATTERNS["location"].search(
                    fee_question + " " + fee_answer
                )
                and not MONEY_AMOUNT_RE.search(fee_answer)
            )
            faq_fee_source_pages += fee_state_ok
            if not fee_state_ok:
                faq_fee_source_failure_categories[profile.category] += 1
            audit_result.hard(
                fee_state_ok,
                "faq_fee_source_state",
                f"{relative}: fee_url={bool(source.fee_url)} answer={fee_answer!r}",
            )

            if not supported and grade_fact_node is not None:
                grade_state_ok = (
                    unsupported_grade_state_ok(
                        grade_fact_node.text(), profile.subjects, expected_grade_map
                    )
                    and unsupported_grade_state_ok(
                        visible_faq[0][1], profile.subjects, expected_grade_map
                    )
                )
                unsupported_grade_state_pages += grade_state_ok
                audit_result.hard(
                    grade_state_ok,
                    "unsupported_subject_grade_state",
                    f"{relative}: subjects={profile.subjects}",
                )

                premise_blocks: list[tuple[str, str]] = []
                intro_nodes = article.find_all("div", cls="academy-intro-answer")
                if len(intro_nodes) == 1 and contains_any(
                    intro_nodes[0].text(), UNSUPPORTED_INTRO_PREMISES
                ):
                    premise_blocks.append(("article_intro", intro_nodes[0].text()))
                grade_heading_nodes = sections[1].find_all("h2")
                if (
                    len(grade_heading_nodes) == 1
                    and "확인된 학년 범위" in grade_heading_nodes[0].text()
                ):
                    premise_blocks.append(("article_h2", grade_heading_nodes[0].text()))
                if len(grade_paragraphs) >= 2 and contains_any(
                    grade_paragraphs[1].text(),
                    UNSUPPORTED_ARTICLE_GUIDANCE_PREMISES,
                ):
                    premise_blocks.append(("article_guidance", grade_paragraphs[1].text()))
                if contains_any(
                    visible_faq[0][0], UNSUPPORTED_FAQ_QUESTION_PREMISES
                ):
                    premise_blocks.append(("faq_grade_question", visible_faq[0][0]))
                if contains_any(
                    visible_faq[0][1], UNSUPPORTED_FAQ_GUIDANCE_PREMISES
                ):
                    premise_blocks.append(("faq_grade_guidance", visible_faq[0][1]))
                for question, answer in visible_faq:
                    if contains_any(answer, UNSUPPORTED_FAQ_PREP_PREMISES):
                        premise_blocks.append(("faq_prepare", question + " " + answer))
                all_unsupported_premises = (
                    UNSUPPORTED_INTRO_PREMISES
                    + UNSUPPORTED_ARTICLE_GUIDANCE_PREMISES
                    + UNSUPPORTED_FAQ_QUESTION_PREMISES
                    + UNSUPPORTED_FAQ_GUIDANCE_PREMISES
                    + UNSUPPORTED_FAQ_PREP_PREMISES
                )
                # Preserve the independently reproduced block count, while
                # still failing closed if a known affirmative premise is moved
                # outside its legacy block shape.
                if (
                    not premise_blocks
                    and contains_any(article_text, all_unsupported_premises)
                ):
                    premise_blocks.append(("authored_copy", article_text))
                if premise_blocks:
                    unsupported_premise_pages.add(relative)
                    unsupported_premise_page_categories[profile.category] += 1
                    unsupported_premise_occurrence_categories[
                        profile.category
                    ] += len(premise_blocks)
                unsupported_premise_occurrences += len(premise_blocks)
                for scope, value in premise_blocks:
                    audit_result.target(
                        False,
                        "unsupported_positive_grade_premise",
                        f"{relative}: {scope}: {value!r}",
                    )
        expected_levels = set(schema.expected_page_levels(source, profile))
        faq_grade_tokens = set(
            re.findall(
                r"(?:초[1-6]|[중고][1-3])",
                " ".join(question + " " + answer for question, answer in visible_faq),
            )
        )
        foreign_faq_grades = sorted(faq_grade_tokens - expected_levels)
        faq_out_of_source_grade_pages += bool(foreign_faq_grades)
        audit_result.target(
            not foreign_faq_grades,
            "faq_out_of_source_grade",
            f"{relative}: {foreign_faq_grades}",
        )
        if article_schema is None:
            audit_result.hard(False, "article_schema_count", relative)
            continue
        published_manifest.append(
            relative
            + "\t"
            + json.dumps(
                article_schema.get("datePublished"),
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )
        modified = article_schema.get("dateModified")
        modified_values[str(modified) if modified is not None else "<absent>"] += 1
        audit_result.target(
            modified == PHASE3_DATE,
            "article_date_modified",
            f"{relative}: {modified!r}",
        )

        guide_nodes = selector_nodes(dom, "location-guide")
        missing_guide_nodes = [
            node
            for node in dom.find_all(
                attr="data-source-status", value=MISSING_LOCATION_GUIDE_STATUS
            )
            if descends_from(node, main)
        ]
        location_paragraphs = (
            sections[3].find_all("p") if len(sections) == 5 else []
        )
        location_context_node = location_paragraphs[-1] if location_paragraphs else None
        if fact.guide_clean:
            guide_present += 1
            audit_result.target(
                len(guide_nodes) == 1,
                "location_guide_selector",
                f"{relative}: expected1 found{len(guide_nodes)}",
            )
            if len(guide_nodes) == 1:
                node = guide_nodes[0]
                audit_result.target(
                    node.tag == "p"
                    and node.has_class("academy-location-guide")
                    and node.text() == fact.guide_clean,
                    "location_guide_exact",
                    f"{relative}: {node.text()!r} != {fact.guide_clean!r}",
                )
            audit_result.target(
                not missing_guide_nodes,
                "location_guide_unexpected_missing_status",
                f"{relative}: {len(missing_guide_nodes)}",
            )
        else:
            guide_blank += 1
            audit_result.target(
                not guide_nodes,
                "location_guide_blank",
                f"{relative}: found{len(guide_nodes)}",
            )
            missing_status_ok = (
                len(missing_guide_nodes) == 1
                and location_context_node is missing_guide_nodes[0]
                and missing_guide_nodes[0].tag == "p"
                and missing_guide_nodes[0].has_class("academy-location-guide-status")
                and missing_guide_nodes[0].has_class("academy-source-unconfirmed-note")
            )
            guide_missing_status_pages += missing_status_ok
            audit_result.target(
                missing_status_ok,
                "location_guide_missing_status",
                f"{relative}: markers={len(missing_guide_nodes)}",
            )
            context_text = location_context_node.text() if location_context_node else ""
            # The marker itself must be negative, and no other authored Article
            # block may imply that a blank guide value was actually supplied.
            # Keep nodes separate and exclude headings: the neutral section
            # title ``주소와 위치 안내 확인`` is not a value-existence premise,
            # and joining it to a following paragraph also creates matches no
            # reader-visible sentence actually makes.
            guide_authored_blocks = [
                node.text()
                for node in article.find_all("p")
            ] + [
                node.text()
                for node in article.find_all("div", cls="academy-intro-answer")
            ]
            positive_premise = any(
                GUIDE_POSITIVE_PREMISE_RE.search(value)
                for value in guide_authored_blocks
            )
            guide_positive_premise_pages += positive_premise
            if positive_premise:
                guide_positive_premise_categories[profile.category] += 1
            audit_result.target(
                bool(MISSING_SOURCE_CUE_RE.search(context_text)),
                "location_guide_missing_negative_cue",
                f"{relative}: {context_text!r}",
            )
            audit_result.target(
                not positive_premise,
                "location_guide_positive_premise",
                f"{relative}: {context_text!r}",
            )
        # A tilde can be legitimate range notation (for example ``3~4주``),
        # so only URL/emoticon artefacts are banned across all rendered copy.
        # Leading/trailing guide tildes are already excluded by exact equality
        # with ``clean_location_guide`` above.
        audit_result.target(
            not re.search(r"https?://|naver\.me|\^\^", main_text, re.I),
            "raw_guide_artifact_visible",
            relative,
        )

        number_nodes = selector_nodes(dom, "registration-number")
        name_nodes = selector_nodes(dom, "registered-name")
        registration_pages += len(number_nodes) == 1 and len(name_nodes) == 1
        audit_result.target(
            len(number_nodes) == 1
            and number_nodes[0].tag == "dd"
            and number_nodes[0].text() == source.registration,
            "registration_number_projection",
            f"{relative}: selectors={len(number_nodes)}",
        )
        audit_result.target(
            len(name_nodes) == 1 and name_nodes[0].text() == source.office_name,
            "registered_name_projection",
            f"{relative}: selectors={len(name_nodes)}",
        )
        if len(name_nodes) == 1:
            parent = name_nodes[0].closest("p")
            audit_result.target(
                bool(parent)
                and parent.has_class("academy-registration-facts")
                and source.registration in parent.text()
                and source.office_name in parent.text()
                and parent.text() != source.office_name,
                "registered_name_paired_context",
                relative,
            )
        headings = [node.text() for tag in ("h1", "h2", "h3", "h4") for node in main.find_all(tag)]
        audit_result.target(
            source.office_name not in headings,
            "registered_name_standalone_heading",
            relative,
        )

        # The schema fixer's ``iter_string_values`` intentionally yields
        # ``(path, value)`` pairs for diagnostics; the release parser exposes
        # the value-only walk required by this corpus-level text gate.
        all_json_strings = list(release.walk_string_values(graph))
        json_text = " ".join(all_json_strings)
        combined_reader_values = main_text + " " + json_text
        for label in QUERY_LABELS:
            occurrences = combined_reader_values.count(label)
            if occurrences:
                query_page_counts[label] += 1
                query_occurrences[label] += occurrences
            audit_result.target(
                occurrences == 0,
                "query_label_hard_zero",
                f"{relative}: {label} x{occurrences}",
            )
        audit_result.hard(
            not MANUSCRIPT_RE.search(combined_reader_values),
            "manuscript_meta_hard_zero",
            relative,
        )
        audit_result.hard(
            not IRRELEVANT_SEED_RE.search(combined_reader_values),
            "irrelevant_seed_hard_zero",
            relative,
        )
        bad_literals = [value for value in KNOWN_BAD_TEXT if value in main_text]
        bad_regex = [pattern.pattern for pattern in KNOWN_BAD_REGEX if pattern.search(main_text)]
        audit_result.target(
            not bad_literals and not bad_regex,
            "copy_naturality",
            f"{relative}: {(bad_literals + bad_regex)[:2]}",
        )
        audit_result.target(
            article_text.count("“") == article_text.count("”")
            and article_text.count("‘") == article_text.count("’"),
            "quote_balance",
            relative,
        )

        paragraphs = editorial_paragraphs(article)
        page_sentences = [sentence for paragraph in paragraphs for sentence in sentences(paragraph)]
        normalized_sentences = [normalized_literal(value) for value in page_sentences if len(clean(value)) >= 25]
        duplicates = [value for value, count in Counter(normalized_sentences).items() if value and count > 1]
        if duplicates:
            within_page_duplicate_sentence_pages += 1
        audit_result.target(
            not duplicates,
            "within_page_duplicate_sentence",
            f"{relative}: {len(duplicates)}",
        )
        too_long = [value for value in page_sentences if len(clean(value)) > MAX_EDITORIAL_SENTENCE_CHARS]
        audit_result.target(
            not too_long,
            "editorial_sentence_too_long",
            f"{relative}: max={max((len(clean(v)) for v in too_long), default=0)}",
        )

        article_lengths.append(len(article_text))
        paragraph_lengths.extend(len(value) for value in paragraphs)
        sentence_lengths.extend(len(value) for value in page_sentences)
        neutral_article = source_neutral(article_text, source, physical, profile, schema)
        whole_article_documents[neutral_article].append(relative)
        page_neutral_paragraphs = {
            source_neutral(value, source, physical, profile, schema)
            for value in paragraphs
            if len(clean(value)) >= 50
        }
        page_neutral_sentences = {
            source_neutral(value, source, physical, profile, schema)
            for value in page_sentences
            if len(clean(value)) >= 40
        }
        for value in page_neutral_paragraphs - {""}:
            paragraph_documents[value].add(relative)
        for value in page_neutral_sentences - {""}:
            sentence_documents[value].add(relative)
        for heading in article.find_all("h2"):
            value = source_neutral(heading.text(), source, physical, profile, schema)
            if value:
                heading_documents[value].add(relative)

        if supported:
            locality_main = main_text.count(source.locality)
            locality_article = article_text.count(source.locality)
            h1_main = main_text.count(h1)
            main_length = max(1, len(main_text))
            main_locality_counts.append(locality_main)
            article_locality_counts.append(locality_article)
            main_h1_counts.append(h1_main)
            locality_densities.append(locality_main * 1000 / main_length)
            h1_densities.append(h1_main * 1000 / main_length)
            audit_result.target(
                locality_article <= MAX_ARTICLE_LOCALITY,
                "article_locality_repetition",
                f"{relative}: {locality_article}>{MAX_ARTICLE_LOCALITY}",
            )
            audit_result.target(
                locality_main <= MAX_MAIN_LOCALITY,
                "main_locality_repetition",
                f"{relative}: {locality_main}>{MAX_MAIN_LOCALITY}",
            )
            audit_result.target(
                h1_main <= MAX_MAIN_EXACT_H1,
                "main_h1_repetition",
                f"{relative}: {h1_main}>{MAX_MAIN_EXACT_H1}",
            )
            audit_result.target(
                locality_main * 1000 / main_length <= MAX_LOCALITY_PER_1000,
                "locality_density",
                f"{relative}: {locality_main * 1000 / main_length:.2f}",
            )
            audit_result.target(
                h1_main * 1000 / main_length <= MAX_H1_PER_1000,
                "h1_density",
                f"{relative}: {h1_main * 1000 / main_length:.2f}",
            )

    published_sha = line_sha(sorted(published_manifest))
    audit_result.hard(
        published_sha == BASELINE_PUBLISHED_MANIFEST_SHA256,
        "date_published_preservation",
        f"{published_sha} != {BASELINE_PUBLISHED_MANIFEST_SHA256}",
    )
    audit_result.hard(
        supported_count == EXPECTED_SUPPORTED and unsupported_count == EXPECTED_UNSUPPORTED,
        "supported_distribution",
        f"{supported_count}/{unsupported_count}",
    )

    whole_duplicate_groups = {
        key: paths for key, paths in whole_article_documents.items() if key and len(paths) > 1
    }
    audit_result.target(
        not whole_duplicate_groups,
        "source_neutral_article_duplicates",
        f"groups={len(whole_duplicate_groups)} pages={sum(len(v) for v in whole_duplicate_groups.values())}",
    )
    paragraph_over = {key: value for key, value in paragraph_documents.items() if len(value) > MAX_CROSS_DOCUMENT_FREQUENCY}
    sentence_over = {key: value for key, value in sentence_documents.items() if len(value) > MAX_CROSS_DOCUMENT_FREQUENCY}
    heading_over = {key: value for key, value in heading_documents.items() if len(value) > MAX_NORMALIZED_H2_FREQUENCY}
    audit_result.target(
        not paragraph_over,
        "cross_page_paragraph_frequency",
        f"groups={len(paragraph_over)} max={max((len(v) for v in paragraph_documents.values()), default=0)}",
    )
    audit_result.target(
        not sentence_over,
        "cross_page_sentence_frequency",
        f"groups={len(sentence_over)} max={max((len(v) for v in sentence_documents.values()), default=0)}",
    )
    audit_result.target(
        not heading_over,
        "cross_page_h2_frequency",
        f"groups={len(heading_over)} max={max((len(v) for v in heading_documents.values()), default=0)}",
    )

    sitemap_value = bytes_for(sitemap_path, overrides)
    entries = sitemap_entries(sitemap_value)
    sitemap_urls = [url for url, _lastmod in entries]
    detail_urls = {
        schema.canonical_url(final_texts[path], path)
        for path, _profile, _source in details
        if path in final_texts
    }
    sitemap_url_sha = line_sha(sitemap_urls)
    non_detail_lastmod_lines = [
        f"{index}\t{url}\t{lastmod}"
        for index, (url, lastmod) in enumerate(entries)
        if url not in detail_urls
    ]
    nondetail_lastmod_sha = line_sha(non_detail_lastmod_lines)
    audit_result.hard(len(entries) == EXPECTED_DETAILS + EXPECTED_NON_DETAILS, "sitemap_count", str(len(entries)))
    audit_result.hard(len(set(sitemap_urls)) == len(sitemap_urls), "sitemap_unique", str(len(sitemap_urls)))
    audit_result.hard(
        sitemap_url_sha == BASELINE_SITEMAP_URL_ORDER_SHA256,
        "sitemap_url_order",
        f"{sitemap_url_sha} != {BASELINE_SITEMAP_URL_ORDER_SHA256}",
    )
    audit_result.hard(
        nondetail_lastmod_sha == BASELINE_SITEMAP_NONDETAIL_LASTMOD_SHA256,
        "sitemap_nondetail_lastmod",
        f"{nondetail_lastmod_sha} != {BASELINE_SITEMAP_NONDETAIL_LASTMOD_SHA256}",
    )
    detail_entry_map = {url: lastmod for url, lastmod in entries if url in detail_urls}
    audit_result.target(
        len(detail_entry_map) == EXPECTED_DETAILS
        and all(value == PHASE3_DATE for value in detail_entry_map.values()),
        "sitemap_detail_lastmod",
        f"coverage={len(detail_entry_map)} dates={dict(Counter(detail_entry_map.values()))}",
    )

    post_targets = target_manifest(target_paths + [sitemap_path])
    post_external = schema.external_manifest(set(target_paths))
    audit_result.hard(pre_targets == post_targets, "read_only_target_freeze", "target files changed during audit")
    audit_result.hard(pre_external == post_external, "read_only_external_freeze", "external files changed during audit")

    copy_metrics = {
        "supported_pages": supported_count,
        "unsupported_pages": unsupported_count,
        "guide_selector_expected_nonblank_pages": guide_present,
        "guide_selector_expected_blank_pages": guide_blank,
        "guide_missing_status_complete_pages": guide_missing_status_pages,
        "guide_blank_positive_premise_pages": guide_positive_premise_pages,
        "guide_blank_positive_premise_categories": dict(
            guide_positive_premise_categories
        ),
        "registration_selector_complete_pages": registration_pages,
        "visible_core_fact_parity_pages": core_fact_parity_pages,
        "visible_grade_parity_pages": grade_parity_pages,
        "visible_school_parity_pages": school_parity_pages,
        "visible_fee_parity_pages": fee_parity_pages,
        "physical_org_parity_pages": physical_org_parity_pages,
        "visible_faq_schema_parity_pages": faq_parity_pages,
        "faq_out_of_source_grade_pages": faq_out_of_source_grade_pages,
        "faq_single_intent_complete_pages": faq_single_intent_pages,
        "faq_single_intent_failures_by_slot": dict(faq_topic_failures),
        "faq_single_intent_failure_categories": {
            topic: dict(values)
            for topic, values in faq_topic_failure_categories.items()
        },
        "faq_location_source_complete_pages": faq_location_source_pages,
        "faq_location_source_failure_categories": dict(
            faq_location_source_failure_categories
        ),
        "faq_fee_source_complete_pages": faq_fee_source_pages,
        "faq_fee_source_failure_categories": dict(
            faq_fee_source_failure_categories
        ),
        "unsupported_grade_state_complete_pages": unsupported_grade_state_pages,
        "unsupported_positive_premise_pages": len(unsupported_premise_pages),
        "unsupported_positive_premise_occurrences": unsupported_premise_occurrences,
        "unsupported_positive_premise_page_categories": dict(
            unsupported_premise_page_categories
        ),
        "unsupported_positive_premise_occurrence_categories": dict(
            unsupported_premise_occurrence_categories
        ),
        "faq_count": quantiles(faq_counts),
        "article_chars": quantiles(article_lengths),
        "paragraph_chars": quantiles(paragraph_lengths),
        "sentence_chars": quantiles(sentence_lengths),
        "supported_main_locality_count": quantiles(main_locality_counts),
        "supported_article_locality_count": quantiles(article_locality_counts),
        "supported_main_exact_h1_count": quantiles(main_h1_counts),
        "supported_locality_per_1000": quantiles(locality_densities),
        "supported_h1_per_1000": quantiles(h1_densities),
        "within_page_duplicate_sentence_pages": within_page_duplicate_sentence_pages,
        "source_neutral_article_duplicate_groups": len(whole_duplicate_groups),
        "source_neutral_article_duplicate_pages": sum(len(value) for value in whole_duplicate_groups.values()),
        "cross_page_paragraph_max_documents": max((len(value) for value in paragraph_documents.values()), default=0),
        "cross_page_paragraph_groups_over_target": len(paragraph_over),
        "cross_page_sentence_max_documents": max((len(value) for value in sentence_documents.values()), default=0),
        "cross_page_sentence_groups_over_target": len(sentence_over),
        "normalized_h2_max_documents": max((len(value) for value in heading_documents.values()), default=0),
        "normalized_h2_groups_over_target": len(heading_over),
        "query_label_pages": dict(query_page_counts),
        "query_label_occurrences": dict(query_occurrences),
        "article_date_modified": dict(modified_values),
        "article_date_published_manifest_sha256": published_sha,
    }
    thresholds = {
        "article_locality_max": MAX_ARTICLE_LOCALITY,
        "main_locality_max": MAX_MAIN_LOCALITY,
        "main_exact_h1_max": MAX_MAIN_EXACT_H1,
        "locality_per_1000_max": MAX_LOCALITY_PER_1000,
        "h1_per_1000_max": MAX_H1_PER_1000,
        "editorial_sentence_chars_max": MAX_EDITORIAL_SENTENCE_CHARS,
        "within_page_exact_editorial_sentence_duplicates": 0,
        "source_neutral_whole_article_duplicate_groups": 0,
        "cross_page_paragraph_or_sentence_max_documents": MAX_CROSS_DOCUMENT_FREQUENCY,
        "normalized_h2_max_documents": MAX_NORMALIZED_H2_FREQUENCY,
        "query_labels_visible_and_json": 0,
        "blank_guide_positive_premises": 0,
        "unsupported_positive_grade_premises": 0,
        "faq_single_intent_slots": list(FAQ_SLOT_TOPICS),
    }
    return {
        "ok": not audit_result.errors,
        "mode": "observe" if args.observe else "strict",
        "baseline_ref": BASELINE_REF,
        "source_aware_self_test": self_test,
        "projection": {**projection, "schema_changed_in_projection": schema_projection_changes},
        "errors": len(audit_result.errors),
        "error_counts": dict(audit_result.counts),
        "error_samples": audit_result.errors,
        "observations": sum(audit_result.observation_counts.values()),
        "observation_counts": dict(audit_result.observation_counts),
        "observation_samples": audit_result.observations,
        "source_boundaries": source_metrics,
        "copy_metrics": copy_metrics,
        "thresholds": thresholds,
        "schema_global": global_contract,
        "invariants": {
            "sitemap_url_order_sha256": sitemap_url_sha,
            "sitemap_nondetail_lastmod_sha256": nondetail_lastmod_sha,
            "target_manifest_pre_sha256": schema.digest_manifest(pre_targets),
            "target_manifest_post_sha256": schema.digest_manifest(post_targets),
            "external_manifest_pre_sha256": schema.digest_manifest(pre_external),
            "external_manifest_post_sha256": schema.digest_manifest(post_external),
        },
    }


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--projected-content-script",
        type=Path,
        help="Import build_plan(root, reference_dir), then audit content -> schema in memory",
    )
    parser.add_argument(
        "--observe",
        action="store_true",
        help="Report phase-3 targets without failing; immutable fact/schema gates remain strict",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        report = audit(args)
    except Exception as exc:  # fail closed, without writing a report file
        print(json.dumps({"ok": False, "fatal": str(exc)}, ensure_ascii=False, indent=2))
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
