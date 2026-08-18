#!/usr/bin/env python3
"""Strict, read-only Phase 3 release gate for 소수정예학원.com.

This auditor intentionally complements, rather than replaces, the frozen
Phase 1 release auditor and ``smallclass_phase3_fact_copy_audit.py``.  It
checks the corpus-wide diversity contract on the complete reader manuscript,
the four source-bound FAQ slots, the immutable Git baseline, and sitemap /
Article freshness parity.  A projected content plan and the Phase 1 schema
fixer are applied only in memory.

Strict mode is the release gate.  ``--observe`` keeps immutable facts hard but
reports not-yet-applied Phase 3 targets as observations, which is useful for a
baseline measurement.  The script never writes repository files.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import html
import importlib.util
import json
import re
import subprocess
import sys
import unicodedata
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any, Iterable, Mapping, Sequence
from urllib.parse import quote


sys.dont_write_bytecode = True


ROOT = Path(__file__).resolve().parents[1]
COMMON = ROOT.parent / "참고자료" / "공통자료"
CENTER_CSV = COMMON / "센터정보 정리.csv"
PHASE1_CONTENT_SCRIPT = ROOT / "scripts" / "smallclass_phase1_content.py"
SCHEMA_SCRIPT = ROOT / "scripts" / "smallclass_phase1_schema.py"

BASE_URL = "https://xn--9l4b2jy4h4wan0chw4b.com"
BASELINE_REF = "71d2f3f484233822ca4299d99002b07bb2848e16"
PHASE3_DATE = "2026-08-18"
AUTHORITATIVE_CSV_SHA256 = "3ffbd7b70273b6dc1c8435c53a3a25e32d2a173ba1bf51840654389bd8954e1a"
EXPECTED_DETAILS = 3339
EXPECTED_INDEXABLE = 3378
EXPECTED_NON_DETAILS = 39
EXPECTED_UNSUPPORTED = 106

MAX_WITHIN_DUPLICATE_EXCESS = 0
MAX_CROSS_DOCUMENT_FREQUENCY = 34
MAX_HEADING_DOCUMENT_FREQUENCY = 40
MAX_FAQ_QUESTION_DOCUMENT_FREQUENCY = 34
MAX_MANUSCRIPT_LOCALITY = 12
MAX_ARTICLE_LOCALITY = 12
MAX_MANUSCRIPT_QUERY = 3
MAX_PARAGRAPH_CHARS = 180
MAX_SENTENCE_CHARS = 180

SOURCE_MARKER_VALUE = "unconfirmed-grade"
LOCATION_GUIDE_FIELD = "location-guide"
GRADE_TOKEN_RE = re.compile(
    r"(?<![가-힣A-Za-z0-9])(?:초[1-6]|[중고][1-3])"
)
COARSE_GRADE_CLAIM_RE = re.compile(
    r"(?:초등|중등|고등)\s*(?:학생|전\s*학년|전체\s*학년|모든\s*학년)"
)
SOURCE_NOTE_SENTENCE_RE = re.compile(
    r"(?:원자료|제공(?:된)?\s*센터\s*자료|제공\s*자료).{0,80}"
    r"(?:미기재|기재되어\s*있지\s*않|공란|비어\s*있|확인되지\s*않).{0,100}"
    r"(?:개설|대상\s*학년|가능\s*학년|수업\s*시간|요일|상담)|"
    r"(?:개설|대상\s*학년|가능\s*학년|수업\s*시간|요일).{0,100}"
    r"(?:원자료|제공\s*자료).{0,50}(?:미기재|공란|비어\s*있)",
)
FEE_BLANK_RE = re.compile(
    r"(?:교습비|비용|금액).{0,30}(?:링크가?\s*없|자료가?\s*없|미기재|비어|"
    r"제공되지\s*않|알\s*수\s*없)|(?:링크|자료|원문|비용\s*칸).{0,30}"
    r"(?:없|미기재|비어|제공되지\s*않)",
)

# Source-aware editorial truthfulness gates.  These patterns deliberately
# describe a value being *used as though it exists*, rather than merely
# mentioning a missing source field.  Source-disclosure sentences therefore
# remain valid while a blank field cannot silently inherit a positive template.
BLANK_GUIDE_PREMISE_RE = re.compile(
    r"(?:"
    r"제공(?:된)?\s*(?:위치\s*)?(?:안내|문구)|"
    r"위치\s*(?:안내(?:\s*원문|값)?|문구|원문|값)"
    r")"
    r"\s*(?:을|를|와|과|은|는|이|가)?\s*"
    r"(?:참고|확인(?:한\s*뒤)?|대조|읽|활용|기준(?:으로)?|사용|일정에\s*넣)",
)
UNSUPPORTED_GRADE_PREMISE_RE = re.compile(
    r"(?:"
    r"이\s*학년\s*표기를\s*사용|"
    r"상담\s*메모에\s*학년(?:값)?을\s*옮기|"
    r"가능\s*학년\s*자료를\s*읽|"
    r"학년\s*범위를\s*비교|"
    r"등록\s*판단에\s*학년값|"
    r"제공(?:된)?\s*학년값을\s*기록|"
    r"학년\s*목록과|"
    r"확인된\s*학년값|"
    r"학년\s*정보를\s*계획에|"
    r"원문\s*학년값|"
    r"학년값과\s*실제|"
    r"학년\s*표기만으로|"
    r"(?:센터|과목별|상단)\s*학년값|"
    r"제공(?:된)?\s*학년\s*목록|"
    r"학년\s*목록\s*뒤|"
    r"상단\s*학년\s*항목을\s*기준|"
    r"학년값을"
    r")",
)
GRADE_ABSENCE_RE = re.compile(
    r"(?:미기재|공란|비어\s*있|비어\s*있지|"
    r"기재(?:되어)?\s*있지\s*않|적혀\s*있지\s*않|"
    r"확인되지\s*않|제시되지\s*않|제공되지\s*않|"
    r"(?:학년|목록|항목|값)[^.!?]{0,30}없)"
)
MISSING_SOURCE_RE = re.compile(
    r"미기재|공란|비어|빈\s*항목|없|확인되지|제시되지|기재되지|"
    r"기재되어\s*있지|적혀\s*있지|제공되지\s*(?:않|못)|"
    r"상담\s*전\s*확인\s*항목"
)
FEE_DOUBLE_TOPIC_RE = re.compile(
    r"최신\s*금액을\s*확인할\s*때는\s*현재\s*금액은"
)

FAQ_TOPIC_RE = {
    "grades": re.compile(r"학년|초[1-6]|[중고][1-3]|초등|중등|고등"),
    "schools": re.compile(r"학교|학교명"),
    "location": re.compile(r"주소|위치|방문|동선|경로|입구|건물|서비스\s*지역"),
    "fee": re.compile(r"교습비|비용|금액|교재비|총액|적용액"),
}
FAQ_TOPIC_ORDER = ("grades", "schools", "location", "fee")
FEE_AMOUNT_CLAIM_RE = re.compile(
    r"\d[\d,]*(?:\.\d+)?\s*(?:만원|천원|원)(?![가-힣])"
)

# Frozen high-confidence positive pools found by the unsupported-page red team.
# The source-fact/disclosure sentence itself is handled separately and may
# contain subject-qualified values or an explicit missing-source statement.
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
UNSUPPORTED_GRADE_HEADING_PREMISES = ("확인된 학년 범위",)
UNSUPPORTED_GRADE_GUIDANCE_LEADS = (
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
)
UNSUPPORTED_GRADE_GUIDANCE_ENDS = (
    "학년값이 실제 반 편성을 뜻한다고 단정하지 마세요",
    "학년 목록 밖의 내용은 상담 답변으로 보완하세요",
    "현재 답변과 원문 학년값을 한 문장에 섞지 마세요",
)
UNSUPPORTED_FAQ_GRADE_QUESTION_LEADS = (
    "센터 학년값을 검토하며",
    "제공된 학년 목록을 읽고",
    "원문 학년값을 메모하면서",
    "확인된 학년값을 바탕으로",
    "과목별 범위를 비교하며",
)
UNSUPPORTED_FAQ_GRADE_QUESTION_ENDS = (
    "과목별 학년값은 어디에 적혀 있나요?",
    "학년 목록 뒤에 어떤 질문을 더해야 하나요?",
    "학년 범위는 현재 수업과 같은 뜻인가요?",
    "학년 정보를 등록 판단에 어떻게 쓰나요?",
    "원자료 학년값은 무엇을 뜻하나요?",
    "학년 범위를 상담에서 어떻게 대조하나요?",
)
UNSUPPORTED_FAQ_GRADE_GUIDANCE_ENDS = (
    "학년값과 실제 시간표를 서로 다른 항목으로 적으세요",
    "현재 운영 조건은 원문 학년값과 구분하세요",
)
UNSUPPORTED_FAQ_PREP_ENDS = (
    "원자료의 센터·학년·학교·주소 사실과 개설·일정·비용 상담 답변을 나누어 쓰세요",
    "학년·학교·주소의 원자료와 실제 개설·시간표·교재·비용 조건을 분리하세요",
    "원자료 학년·학교·주소와 상담에서 받은 일정·교재·비용의 기준일을 함께 적으세요",
    "확인된 센터·학년·학교·주소 사실만 옮기고 일정·교재·비용은 질문으로 남기세요",
)

ARTICLE_RE = re.compile(
    r'(<article\b[^>]*class=["\'][^"\']*\bacademy-main-article\b[^"\']*["\'][^>]*>)'
    r'.*?(</article>)',
    re.I | re.S,
)
FAQ_SECTION_RE = re.compile(
    r'<section\b[^>]*>\s*<div\b[^>]*class=["\'][^"\']*\bacademy-faq-card\b'
    r'[^"\']*["\'][^>]*>.*?</div>\s*</div>\s*</section>',
    re.I | re.S,
)
PREP_SECTION_RE = re.compile(
    r'<section\b[^>]*>\s*<div\b[^>]*class=["\'][^"\']*\bacademy-review-card\b'
    r'[^"\']*["\'][^>]*>.*?</div>\s*</div>\s*</section>',
    re.I | re.S,
)
BLOCK_RE = re.compile(
    r'<(?P<tag>p|blockquote)\b(?P<attrs>[^>]*)>(?P<body>.*?)</(?P=tag)>',
    re.I | re.S,
)
HEADING_RE = re.compile(r"<h[23]\b[^>]*>(.*?)</h[23]>", re.I | re.S)
CANONICAL_RE = re.compile(
    r'<link\b(?=[^>]*\brel=["\']canonical["\'])[^>]*\bhref=["\']([^"\']+)["\']',
    re.I,
)
JSONLD_RE = re.compile(
    r'(<script\b[^>]*type=["\']application/ld\+json["\'][^>]*>)(.*?)(</script>)',
    re.I | re.S,
)
FAQ_LIST_RE = re.compile(
    r'<div\b[^>]*class=["\'][^"\']*\bacademy-faq-list\b[^"\']*["\'][^>]*>'
    r'(.*?)</div>',
    re.I | re.S,
)
DETAIL_RE = re.compile(r"<details\b(?P<attrs>[^>]*)>(?P<body>.*?)</details>", re.I | re.S)
SUMMARY_RE = re.compile(r"<summary\b[^>]*>(.*?)</summary>", re.I | re.S)
ANSWER_RE = re.compile(r"<p\b[^>]*>(.*?)</p>", re.I | re.S)


def clean(value: object) -> str:
    return re.sub(r"\s+", " ", html.unescape(str(value or ""))).strip()


def fragment_text(value: str) -> str:
    return clean(re.sub(r"<[^>]+>", " ", value))


def sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def line_sha(values: Iterable[str]) -> str:
    return sha256(("\n".join(values) + "\n").encode("utf-8"))


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


def normalize_path(path: Path) -> str:
    return path.resolve().relative_to(ROOT).as_posix()


def page_url(path: Path) -> str:
    relative = normalize_path(path)
    if relative == "index.html":
        return BASE_URL + "/"
    if not relative.endswith("/index.html"):
        raise ValueError(f"not an index route: {relative}")
    return BASE_URL + "/" + quote(relative[:-10], safe="/")


@dataclass(frozen=True)
class ExtraFact:
    registered_name: str
    location_guide: str


@dataclass
class Audit:
    observe: bool = False
    errors: list[dict[str, str]] = field(default_factory=list)
    observations: list[dict[str, str]] = field(default_factory=list)
    error_counts: Counter[str] = field(default_factory=Counter)
    observation_counts: Counter[str] = field(default_factory=Counter)

    def hard(self, condition: bool, code: str, detail: str) -> None:
        if condition:
            return
        self.error_counts[code] += 1
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


class GitBatch:
    """Read baseline blobs through one persistent, read-only Git process."""

    def __init__(self, root: Path) -> None:
        self.process = subprocess.Popen(
            ["git", "cat-file", "--batch"],
            cwd=root,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    def get(self, spec: str) -> bytes:
        assert self.process.stdin is not None and self.process.stdout is not None
        self.process.stdin.write(spec.encode("utf-8") + b"\n")
        self.process.stdin.flush()
        header = self.process.stdout.readline().decode("utf-8", "replace").rstrip("\n")
        if header.endswith(" missing"):
            raise KeyError(spec)
        parts = header.split()
        if len(parts) != 3 or parts[1] != "blob":
            raise RuntimeError(f"unexpected git cat-file header for {spec}: {header}")
        size = int(parts[2])
        value = self.process.stdout.read(size)
        if self.process.stdout.read(1) != b"\n":
            raise RuntimeError(f"git cat-file framing error: {spec}")
        return value

    def close(self) -> None:
        if self.process.stdin:
            self.process.stdin.close()
        self.process.wait(timeout=30)
        if self.process.returncode:
            stderr = self.process.stderr.read().decode("utf-8", "replace") if self.process.stderr else ""
            raise RuntimeError(f"git cat-file failed: {stderr}")


def sanitize_location_guide(raw: str) -> str:
    """Independent copy of the approved, narrow source-guide cleanup."""
    value = html.unescape(raw or "")
    value = re.sub(r"(?:https?://|www\.)[^\s<>()]+", " ", value, flags=re.I)
    value = re.sub(r"학원\s*위치\s*안내드립니다", " ", value)
    value = re.sub(r"\^{2,}", "", value)
    value = value.replace("엘레베이터", "엘리베이터")
    value = re.sub("[\U0001F000-\U0001FAFF\u2600-\u27BF]", " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    value = re.sub(r"^[~\s]+|[~\s]+$", "", value)
    value = re.sub(r"\s+([,.!?;:])", r"\1", value)
    value = re.sub(r"([,.!?;:])(?=[가-힣A-Za-z])", r"\1 ", value)
    value = re.sub(r"\(\s*\)", " ", value)
    return clean(value)


def load_extras(phase1: ModuleType) -> dict[str, ExtraFact]:
    with CENTER_CSV.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if len(rows) != 371:
        raise RuntimeError(f"source row count: {len(rows)}")
    result: dict[str, ExtraFact] = {}
    for raw in rows:
        row = phase1.canonical_headers(raw)
        slug = phase1.compact_slug(clean(row["근처수업가능동네"]))
        result[slug] = ExtraFact(
            registered_name=clean(row["교육지원청명칭"]),
            location_guide=sanitize_location_guide(row.get("위치안내", "")),
        )
    if len(result) != 371:
        raise RuntimeError(f"source locality count: {len(result)}")
    return result


def repository_manifest(paths: Iterable[Path]) -> tuple[str, dict[str, str]]:
    values = {
        normalize_path(path): sha256(path.read_bytes())
        for path in sorted({path.resolve() for path in paths}, key=lambda item: item.as_posix())
        if path.is_file()
    }
    return line_sha(f"{path}\t{digest}" for path, digest in sorted(values.items())), values


def repository_freeze_paths() -> list[Path]:
    excluded = {".git", "tmp", "__pycache__"}
    return [
        path.resolve()
        for path in ROOT.rglob("*")
        if path.is_file() and not (set(path.relative_to(ROOT).parts) & excluded)
    ]


def tracked_delta() -> list[str]:
    completed = subprocess.run(
        ["git", "diff", "--name-only", "--no-renames", BASELINE_REF, "--"],
        cwd=ROOT,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )
    return [line.strip().replace("\\", "/") for line in completed.stdout.splitlines() if line.strip()]


def read_projection(content_script: Path | None, audit: Audit) -> tuple[dict[Path, bytes], dict[str, Any]]:
    if content_script is None:
        return {}, {"enabled": False, "documents": 0, "changed": 0}
    content_script = content_script.resolve()
    expected_script = (ROOT / "scripts" / "smallclass_phase3_content.py").resolve()
    if content_script != expected_script:
        raise RuntimeError(f"projected content script must be {expected_script}")
    module = import_script("smallclass_phase3_release_projection", content_script)
    build_plan = getattr(module, "build_plan", None)
    if not callable(build_plan):
        raise RuntimeError(f"{content_script}: build_plan(root, reference_dir) missing")
    plan = build_plan(ROOT, COMMON)
    documents = list(getattr(plan, "documents", []))
    audit.hard(bool(getattr(plan, "idempotent", False)), "projection_idempotency", str(content_script))
    overrides: dict[Path, bytes] = {}
    stale: list[str] = []
    duplicate_paths: list[str] = []
    for item in documents:
        path = item.path.resolve()
        if path in overrides:
            duplicate_paths.append(normalize_path(path))
        overrides[path] = item.after
        if item.before != path.read_bytes():
            stale.append(normalize_path(path))
    audit.hard(not stale, "projection_input_stale", repr(stale[:5]))
    audit.hard(not duplicate_paths, "projection_duplicate_path", repr(duplicate_paths[:5]))
    audit.hard(
        len(documents) == EXPECTED_DETAILS + 1,
        "projection_document_count",
        str(len(documents)),
    )
    plan_authorized = {Path(path).resolve() for path in getattr(plan, "authorized_paths", ())}
    audit.hard(
        plan_authorized == set(overrides),
        "projection_plan_authorized_scope",
        f"authorized={len(plan_authorized)} documents={len(overrides)}",
    )
    return overrides, {
        "enabled": True,
        "script": str(content_script.resolve()),
        "documents": len(documents),
        "changed": sum(item.before != item.after for item in documents),
        "idempotent": bool(getattr(plan, "idempotent", False)),
    }


def bytes_for(path: Path, overrides: Mapping[Path, bytes]) -> bytes:
    return overrides.get(path.resolve(), path.read_bytes())


def article_html(text: str) -> str:
    match = ARTICLE_RE.search(text)
    return match.group(0) if match else ""


def manuscript_html(text: str) -> str:
    values: list[str] = []
    for pattern in (ARTICLE_RE, FAQ_SECTION_RE, PREP_SECTION_RE):
        match = pattern.search(text)
        if match:
            values.append(match.group(0))
    return " ".join(values)


def manuscript_blocks(text: str) -> list[str]:
    result: list[str] = []
    for match in BLOCK_RE.finditer(manuscript_html(text)):
        attrs = match.group("attrs")
        if re.search(
            rf"\bdata-source-field\s*=\s*([\"']){re.escape(LOCATION_GUIDE_FIELD)}\1",
            attrs,
            re.I,
        ):
            continue
        value = fragment_text(match.group("body"))
        if value:
            result.append(value)
    return result


def cross_document_blocks(text: str) -> list[str]:
    """Return authored blocks, excluding only a block's own source-note marker.

    Unsupported pages also carry status markers on broad Article/FAQ/prep
    wrappers.  Those wrappers must not remove the remaining authored copy from
    corpus diversity checks, so ancestor markers are deliberately ignored.
    """
    result: list[str] = []
    for match in BLOCK_RE.finditer(manuscript_html(text)):
        attrs = match.group("attrs")
        if re.search(
            rf"\bdata-source-field\s*=\s*([\"']){re.escape(LOCATION_GUIDE_FIELD)}\1",
            attrs,
            re.I,
        ):
            continue
        if re.search(
            rf"\bdata-source-status\s*=\s*([\"']){re.escape(SOURCE_MARKER_VALUE)}\1",
            attrs,
            re.I,
        ) or re.search(r"\bacademy-source-unconfirmed-note\b", attrs, re.I):
            continue
        value = fragment_text(match.group("body"))
        if value:
            result.append(value)
    return result


def cross_document_sentences(blocks: Sequence[str]) -> list[str]:
    return [
        sentence
        for block in blocks
        for sentence in sentences(block)
        if not SOURCE_NOTE_SENTENCE_RE.search(sentence)
    ]


def sentences(value: str) -> list[str]:
    return [clean(item) for item in re.split(r"(?<=[.!?])\s+", value) if clean(item)]


def blank_guide_premise_sentences(value: str) -> list[str]:
    """Find prose that treats a missing location-guide value as present."""
    return [
        sentence
        for sentence in sentences(value)
        if BLANK_GUIDE_PREMISE_RE.search(sentence)
    ]


def unsupported_grade_premise_sentences(value: str) -> list[str]:
    """Find affirmative grade-value premises outside source disclosures."""
    return [
        sentence
        for sentence in sentences(value)
        if UNSUPPORTED_GRADE_PREMISE_RE.search(sentence)
        and not GRADE_ABSENCE_RE.search(sentence)
    ]


def fee_double_topic_sentences(value: str) -> list[str]:
    return [
        sentence
        for sentence in sentences(value)
        if FEE_DOUBLE_TOPIC_RE.search(sentence)
    ]


def unsupported_high_confidence_premise_blocks(text: str) -> list[str]:
    """Return red-team-confirmed positive blocks on an unsupported page."""
    result: list[str] = []
    article = article_html(text)
    article_cues = (
        *UNSUPPORTED_INTRO_PREMISES,
        *UNSUPPORTED_GRADE_GUIDANCE_LEADS,
        *UNSUPPORTED_GRADE_GUIDANCE_ENDS,
    )
    for match in BLOCK_RE.finditer(article):
        attrs = match.group("attrs")
        value = fragment_text(match.group("body"))
        if not value:
            continue
        if re.search(
            rf"\bdata-source-status\s*=\s*([\"']){re.escape(SOURCE_MARKER_VALUE)}\1",
            attrs,
            re.I,
        ) or re.search(r"\bacademy-source-unconfirmed-note\b", attrs, re.I):
            continue
        if any(cue in value for cue in article_cues):
            result.append(f"article: {value}")
    for heading in (fragment_text(value) for value in HEADING_RE.findall(article)):
        if any(cue in heading for cue in UNSUPPORTED_GRADE_HEADING_PREMISES):
            result.append(f"heading: {heading}")

    items = visible_faq_with_attrs(text)
    if items:
        grade_question = items[0][1]
        if any(cue in grade_question for cue in UNSUPPORTED_FAQ_GRADE_QUESTION_LEADS) or any(
            grade_question.endswith(cue) for cue in UNSUPPORTED_FAQ_GRADE_QUESTION_ENDS
        ):
            result.append(f"faq-grade-question: {grade_question}")
        for sentence in sentences(items[0][2]):
            if any(sentence.endswith(cue + ".") or sentence.endswith(cue) for cue in UNSUPPORTED_FAQ_GRADE_GUIDANCE_ENDS):
                result.append(f"faq-grade-answer: {sentence}")
        # The legacy fourth FAQ was a broad preparation item.  Phase 3 removes
        # it in favour of a dedicated fee item, but this catches the confirmed
        # positive family until that source-neutral migration is applied.
        if len(items) >= 4:
            for sentence in sentences(items[3][2]):
                if any(sentence.endswith(cue + ".") or sentence.endswith(cue) for cue in UNSUPPORTED_FAQ_PREP_ENDS):
                    result.append(f"faq-legacy-prepare: {sentence}")
    return result


def marked_grade_fact_paragraphs(text: str) -> list[str]:
    result: list[str] = []
    for match in BLOCK_RE.finditer(article_html(text)):
        attrs = match.group("attrs")
        has_status = bool(re.search(
            rf"\bdata-source-status\s*=\s*([\"']){re.escape(SOURCE_MARKER_VALUE)}\1",
            attrs,
            re.I,
        ))
        has_note_class = bool(re.search(r"\bacademy-source-unconfirmed-note\b", attrs, re.I))
        if not (has_status and has_note_class):
            continue
        value = fragment_text(match.group("body"))
        if value:
            result.append(value)
    return result


def validate_combined_subject_grades(
    audit: Audit,
    relative: str,
    value: str,
    center: object,
    code_prefix: str,
    unsupported: bool,
) -> None:
    expected_sequence: list[str] = []
    for subject in ("영어", "수학"):
        grades = tuple(center.grades[subject])
        expected_sequence.extend(grades)
        expected = "·".join(grades) if grades else "원자료 미기재"
        matches = re.findall(
            rf"{re.escape(subject)}(?:\s*학년(?:값)?)?[은는]\s*{re.escape(expected)}",
            value,
        )
        audit.hard(
            len(matches) == 1,
            f"{code_prefix}_subject_exact",
            f"{relative}: {subject}={expected!r} count={len(matches)}",
        )
    actual_sequence = GRADE_TOKEN_RE.findall(value)
    audit.hard(
        actual_sequence == expected_sequence,
        f"{code_prefix}_subject_sequence",
        f"{relative}: {actual_sequence} != {expected_sequence}",
    )
    if unsupported:
        combined_sentences = [
            sentence
            for sentence in sentences(value)
            if "영어·수학" in sentence and "대상 학년" in sentence
        ]
        audit.hard(
            bool(combined_sentences)
            and all(MISSING_SOURCE_RE.search(sentence) for sentence in combined_sentences),
            f"{code_prefix}_combined_missing",
            f"{relative}: {combined_sentences!r}",
        )


def faq_topic_matches(
    expected_topic: str,
    question: str,
    answer: str,
    address: str = "",
) -> tuple[bool, list[str]]:
    value = clean(question + " " + answer)
    if expected_topic == "location" and address:
        value = value.replace(address, " ")
    own = bool(FAQ_TOPIC_RE[expected_topic].search(value))
    foreign = [
        topic
        for topic, pattern in FAQ_TOPIC_RE.items()
        if topic != expected_topic and pattern.search(value)
    ]
    return own, foreign


def fee_source_state_valid(answer: str, tuition_url: str) -> bool:
    if tuition_url:
        return (
            answer.count("센터 공통 교습비 링크") == 1
            and bool(re.search(r"상단|핵심정보|옆의", answer))
            and not FEE_BLANK_RE.search(answer)
            and "미기재 상태" not in answer
        )
    return (
        answer.count("교습비 링크") == 1
        and "미기재 상태" in answer
        and "센터 공통 교습비 링크" not in answer
    )


def semantic_self_test_errors() -> list[str]:
    """Exercise every semantic gate with synthetic positive and negative cases."""
    blank_bad = (
        "제공된 안내를 참고하면서 출발 경로를 정하세요.",
        "제공된 위치 문구를 확인한 뒤에는 입구를 다시 물어보세요.",
        "제공 위치 안내를 참고하되 주소와 구분하세요.",
        "위치 안내 원문을 사용할 때는 최신 여부를 확인하세요.",
        "위치 안내값을 읽을 때는 이동 시간을 추정하지 마세요.",
        "주소와 위치 안내를 대조하면서 동선을 정리하세요.",
        "위치 문구를 일정에 넣기 전에는 상담 답변을 받으세요.",
    )
    blank_good = (
        "원자료에 별도 위치 안내가 없으므로 제공 주소를 확인하고 방문 전 문의하세요.",
        "위치 문구가 미기재라 출입 방법은 센터에 직접 물어보세요.",
        "제공 주소만 기록하고 실제 이동 경로는 상담에서 확인하세요.",
    )
    grade_bad = (
        "확인된 학년값을 기준으로 일정을 비교하세요.",
        "제공 학년값을 기록하면서 현재 시간표를 물어보세요.",
        "이 학년 표기를 사용할 때 신청 가능 여부를 단정하지 마세요.",
        "원문 학년값은 무엇을 뜻하나요?",
        "제공된 학년 목록을 읽고 상담 질문을 준비하세요.",
        "학년 표기만으로 현재 신청 가능 여부를 단정하지 마세요.",
    )
    grade_good = (
        "제공된 센터 자료에는 고등 수학 가능 학년이 기재되어 있지 않습니다.",
        "원자료의 학년값이 미기재라 상담 전에는 대상 범위를 비워 두세요.",
        "미기재 학년은 실제 개설 여부와 함께 상담에서 확인하세요.",
    )
    fee_bad = (
        "최신 금액을 확인할 때는 현재 금액은 등록 전에 센터 답변으로 대조하세요.",
        "최신  금액을 확인할 때는  현재 금액은 서면으로 확인하세요.",
    )
    fee_good = (
        "최신 금액을 확인할 때는 센터 답변과 자료일을 대조하세요.",
        "현재 금액은 등록 전에 센터 답변으로 확인하세요.",
    )

    structured_bad = (
        '<article class="academy-main-article">'
        '<p>센터명·주소·등록값과 가능 학년 자료를 구분했습니다.</p>'
        '<h2>확인된 학년 범위</h2>'
        '<p>제공 학년값을 기록하면서 시간표를 물어보세요.</p>'
        '</article>'
        '<div class="academy-faq-list">'
        '<details><summary>센터 학년값을 검토하며, 원자료 학년값은 무엇을 뜻하나요?</summary>'
        '<p>학년값과 실제 시간표를 서로 다른 항목으로 적으세요.</p></details>'
        '<details><summary>학교 질문</summary><p>학교 답변.</p></details>'
        '<details><summary>주소 질문</summary><p>주소 답변.</p></details>'
        '<details><summary>준비 질문</summary><p>원자료 학년·학교·주소와 상담에서 받은 일정·교재·비용의 기준일을 함께 적으세요.</p></details>'
        '</div>'
    )
    structured_good = (
        '<article class="academy-main-article">'
        '<p class="academy-source-unconfirmed-note" data-source-status="unconfirmed-grade">'
        '제공 자료에는 가능 학년이 기재되어 있지 않습니다.</p>'
        '<h2>미기재 학년 확인</h2>'
        '<p>대상 학년 답변을 받기 전에는 범위를 비워 두세요.</p>'
        '</article>'
        '<div class="academy-faq-list">'
        '<details><summary>미기재 학년은 어떻게 확인하나요?</summary>'
        '<p>원자료 학년값이 미기재 상태입니다.</p></details>'
        '</div>'
    )
    single_source_fact = (
        '<article class="academy-main-article" data-source-status="unconfirmed-grade">'
        '<p class="academy-source-unconfirmed-note" data-source-status="unconfirmed-grade">'
        '원자료상 고등 수학 대상 학년은 미기재 상태입니다.</p></article>'
    )
    combined_source_fact = (
        '<article class="academy-main-article" data-source-status="unconfirmed-grade">'
        '<p class="academy-source-unconfirmed-note" data-source-status="unconfirmed-grade">'
        '영어 학년값은 중1이고 수학 학년값은 원자료 미기재입니다.</p></article>'
    )
    wrapper_only_source_fact = (
        '<article class="academy-main-article" data-source-status="unconfirmed-grade">'
        '<p>원자료상 고등 수학 대상 학년은 미기재 상태입니다.</p>'
        '</article>'
    )

    errors: list[str] = []
    for index, value in enumerate(blank_bad):
        if not blank_guide_premise_sentences(value):
            errors.append(f"blank_bad[{index}] missed: {value}")
    for index, value in enumerate(blank_good):
        if blank_guide_premise_sentences(value):
            errors.append(f"blank_good[{index}] false positive: {value}")
    for index, value in enumerate(grade_bad):
        if not unsupported_grade_premise_sentences(value):
            errors.append(f"grade_bad[{index}] missed: {value}")
    for index, value in enumerate(grade_good):
        if unsupported_grade_premise_sentences(value):
            errors.append(f"grade_good[{index}] false positive: {value}")
    for index, value in enumerate(fee_bad):
        if not fee_double_topic_sentences(value):
            errors.append(f"fee_bad[{index}] missed: {value}")
    for index, value in enumerate(fee_good):
        if fee_double_topic_sentences(value):
            errors.append(f"fee_good[{index}] false positive: {value}")
    if len(unsupported_high_confidence_premise_blocks(structured_bad)) != 6:
        errors.append(
            "structured unsupported bad coverage: "
            + repr(unsupported_high_confidence_premise_blocks(structured_bad))
        )
    if unsupported_high_confidence_premise_blocks(structured_good):
        errors.append(
            "structured unsupported good false positive: "
            + repr(unsupported_high_confidence_premise_blocks(structured_good))
        )
    if len(marked_grade_fact_paragraphs(single_source_fact)) != 1:
        errors.append("single unsupported source-fact marker rejected")
    if len(marked_grade_fact_paragraphs(combined_source_fact)) != 1:
        errors.append("combined unsupported source-fact marker rejected")
    if marked_grade_fact_paragraphs(wrapper_only_source_fact):
        errors.append("wrapper-only unsupported source fact accepted")
    missing_source_negatives = (
        "초등 수학 대상 학년은 제공되지 않은 상태입니다.",
        "초등 수학 대상 학년은 제공되지 않습니다.",
        "초등 수학 대상 학년은 제공되지 못한 상태입니다.",
    )
    for index, value in enumerate(missing_source_negatives):
        if not MISSING_SOURCE_RE.search(value):
            errors.append(f"missing source negative[{index}] rejected: {value}")
    if MISSING_SOURCE_RE.search("초등 수학 대상 학년은 원자료에서 제공된 상태입니다."):
        errors.append("provided source positive misclassified as missing")
    topic_good = {
        "grades": faq_topic_matches("grades", "가능 학년은 무엇인가요?", "중1·중2입니다."),
        "schools": faq_topic_matches("schools", "학교 참고값은 무엇인가요?", "상단 학교 태그를 확인하세요."),
        "location": faq_topic_matches(
            "location",
            "센터 주소는 무엇인가요?",
            "제공 주소는 “서울시 학교로 1”입니다.",
            "서울시 학교로 1",
        ),
        "fee": faq_topic_matches("fee", "교습비 자료는 어디에 있나요?", "최신 금액은 상담에서 확인하세요."),
    }
    for topic, (own, foreign) in topic_good.items():
        if not own or foreign:
            errors.append(f"faq topic good {topic}: own={own} foreign={foreign}")
    own, foreign = faq_topic_matches(
        "location",
        "센터 주소는 무엇인가요?",
        "주소와 교습비 금액을 함께 확인하세요.",
    )
    if not own or foreign != ["fee"]:
        errors.append(f"faq topic bad coverage: own={own} foreign={foreign}")
    if not fee_source_state_valid(
        "센터 공통 교습비 링크는 상단 핵심정보에서 확인하고 최신 금액은 상담에서 물어보세요.",
        "https://example.com/fee",
    ):
        errors.append("fee source positive synthetic rejected")
    if not fee_source_state_valid(
        "교습비 링크는 원자료 미기재 상태이므로 최신 금액은 상담에서 물어보세요.",
        "",
    ):
        errors.append("fee source blank synthetic rejected")
    if fee_source_state_valid(
        "센터 공통 교습비 링크는 상단에서 확인하지만 미기재 상태입니다.",
        "https://example.com/fee",
    ):
        errors.append("fee source contradictory synthetic accepted")
    return errors


def context_scope(config: Mapping[str, Any]) -> str:
    subject = "영어·수학" if config["subject"] == "영수" else str(config["subject"])
    level = {"고": "고등", "중": "중등", "초": "초등"}.get(str(config["prefix"]), "")
    return clean(f"{level} {subject}")


def normalized_template(
    value: str,
    center: object,
    config: Mapping[str, Any],
    extra: ExtraFact,
    schools: Sequence[str],
) -> str:
    """Normalize only page-specific facts; preserve punctuation and syntax."""
    result = unicodedata.normalize("NFC", clean(value)).lower()
    facts = [
        center.locality,
        center.display_locality,
        center.display_geo,
        center.official_region,
        center.display_district,
        center.center_name,
        center.address,
        center.registration,
        extra.registered_name,
        extra.location_guide,
        str(config["label"]),
        context_scope(config),
        *schools,
        *center.grades["영어"],
        *center.grades["수학"],
    ]
    normalized_facts = {unicodedata.normalize("NFC", clean(item)).lower() for item in facts if clean(item)}
    for fact in sorted(normalized_facts, key=lambda item: (-len(item), item)):
        result = result.replace(fact, "<사실>")
    result = re.sub(r"영어\s*·\s*수학|영수|영어|수학", "<과목>", result)
    result = re.sub(r"고등|중등|초등", "<단계>", result)
    result = re.sub(r"(?:초[1-6]|[중고][1-3])", "<학년>", result)
    result = re.sub(r"\d+", "<수>", result)
    return clean(result)


def visible_faq_with_attrs(text: str) -> list[tuple[str, str, str]]:
    match = FAQ_LIST_RE.search(text)
    if not match:
        return []
    result: list[tuple[str, str, str]] = []
    for item in DETAIL_RE.finditer(match.group(1)):
        question = SUMMARY_RE.search(item.group("body"))
        answer = ANSWER_RE.search(item.group("body"))
        if question and answer:
            result.append(
                (
                    item.group("attrs"),
                    fragment_text(question.group(1)),
                    fragment_text(answer.group(1)),
                )
            )
    return result


def schema_nodes(text: str) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for match in JSONLD_RE.finditer(text):
        value = json.loads(match.group(2))
        if isinstance(value, dict):
            graph = value.get("@graph", [])
            if isinstance(graph, list):
                result.extend(item for item in graph if isinstance(item, dict))
    return result


def node_has_type(node: Mapping[str, Any], expected: str) -> bool:
    value = node.get("@type", [])
    return expected in value if isinstance(value, list) else value == expected


def one_node(text: str, expected: str) -> dict[str, Any] | None:
    matches = [node for node in schema_nodes(text) if node_has_type(node, expected)]
    return matches[0] if len(matches) == 1 else None


def schema_faq(text: str) -> list[tuple[str, str]]:
    node = one_node(text, "FAQPage")
    if node is None:
        return []
    result: list[tuple[str, str]] = []
    for item in node.get("mainEntity", []):
        answer = item.get("acceptedAnswer", {}) if isinstance(item, dict) else {}
        result.append((clean(item.get("name")), clean(answer.get("text"))))
    return result


def non_json_skeleton(text: str) -> str:
    return JSONLD_RE.sub(lambda match: match.group(1) + "<JSON-LD>" + match.group(3), text)


def canonical_values(text: str) -> list[str]:
    return [clean(value) for value in CANONICAL_RE.findall(text)]


def sitemap_entries(value: bytes) -> list[tuple[str, str]]:
    root = ET.fromstring(value)
    result: list[tuple[str, str]] = []
    for node in root:
        if not str(node.tag).endswith("url"):
            continue
        url = ""
        lastmod = ""
        for child in node:
            if str(child.tag).endswith("loc"):
                url = clean(child.text)
            elif str(child.tag).endswith("lastmod"):
                lastmod = clean(child.text)
        result.append((url, lastmod))
    return result


def sitemap_shape_errors(value: bytes) -> list[str]:
    root = ET.fromstring(value)
    errors: list[str] = []
    for index, node in enumerate(root):
        if not str(node.tag).endswith("url"):
            errors.append(f"root child {index}: {node.tag}")
            continue
        locs = [child for child in node if str(child.tag).endswith("loc")]
        lastmods = [child for child in node if str(child.tag).endswith("lastmod")]
        if len(locs) != 1 or len(lastmods) != 1:
            errors.append(f"url {index}: loc={len(locs)} lastmod={len(lastmods)}")
        if len(errors) == 5:
            break
    return errors


def school_summary(schools: Sequence[str]) -> str:
    if not schools:
        return ""
    if len(schools) <= 3:
        return "·".join(schools)
    return f"{'·'.join(schools[:3])} 외 {len(schools) - 3}개 이름"


def extract_school_names(value: str, school_universe: Sequence[str]) -> list[str]:
    if not school_universe:
        return []
    pattern = re.compile("|".join(re.escape(item) for item in school_universe))
    return pattern.findall(value)


def validate_faq(
    audit: Audit,
    relative: str,
    text: str,
    center: object,
    config: Mapping[str, Any],
    phase1: ModuleType,
    school_universe: Sequence[str],
) -> tuple[list[str], bool]:
    items = visible_faq_with_attrs(text)
    visible = [(question, answer) for _attrs, question, answer in items]
    audit.target(len(items) == 4, "faq_exact_four", f"{relative}: {len(items)}")
    audit.target(visible == schema_faq(text), "faq_json_exact_parity", relative)
    if len(items) != 4:
        return [], False

    unsupported = phase1.unsupported_page(center, config)
    marker_states = [
        bool(
            re.search(
                rf"\bdata-source-status\s*=\s*([\"']){re.escape(SOURCE_MARKER_VALUE)}\1",
                attrs,
                re.I,
            )
        )
        for attrs, _question, _answer in items
    ]
    audit.target(
        marker_states == ([True] * 4 if unsupported else [False] * 4),
        "faq_source_marker_exact",
        f"{relative}: {marker_states}",
    )

    grade_answer = items[0][2]
    subject = str(config["subject"])
    if subject == "영수":
        validate_combined_subject_grades(
            audit,
            relative,
            grade_answer,
            center,
            "faq_grade",
            unsupported,
        )
    else:
        expected_grade_sequence = list(phase1.requested_grades(center, config, subject))
        if expected_grade_sequence:
            audit.target(
                GRADE_TOKEN_RE.findall(grade_answer) == expected_grade_sequence,
                "faq_grade_exact",
                f"{relative}: {GRADE_TOKEN_RE.findall(grade_answer)} != {expected_grade_sequence}",
            )
        else:
            audit.target(
                not GRADE_TOKEN_RE.search(grade_answer)
                and bool(
                    re.search(
                        r"원자료|자료상|미기재|공란|비어|없|알 수 없|확인|"
                        r"제시되지|표시되지|기재되지|기재되어 있지|적혀 있지|빈 항목",
                        grade_answer,
                    )
                ),
                "faq_grade_blank_exact",
                relative,
            )
    expected_schools = tuple(phase1.school_order(center, config))
    school_answer = items[1][2]
    actual_named = extract_school_names(school_answer, school_universe)
    if expected_schools:
        audit.target(
            "학교" in school_answer
            and bool(re.search(r"상단|옆의|핵심정보", school_answer))
            and bool(
                re.search(r"원문|원자료|사실|목록|태그|제공|자료|출처", school_answer)
                or "상단 학교명" in school_answer
            ),
            "faq_school_nonblank_source_cue",
            relative,
        )
        audit.target(
            not actual_named and "“" not in school_answer and "”" not in school_answer,
            "faq_school_no_literal_reenumeration",
            f"{relative}: {actual_named}",
        )
    else:
        audit.target(
            not actual_named
            and "학교" in school_answer
            and bool(
                re.search(
                    r"없|미기재|비어|공란|표시되어 있지|기재되어 있지|"
                    r"제공되지|확인되지 않|상담 전 확인",
                    school_answer,
                )
            ),
            "faq_school_blank_exact",
            f"{relative}: {actual_named}",
        )

    for index, expected_topic in enumerate(FAQ_TOPIC_ORDER):
        _attrs, question, answer = items[index]
        own_topic, foreign_topics = faq_topic_matches(
            expected_topic,
            question,
            answer,
            center.address,
        )
        audit.hard(
            own_topic,
            "faq_single_intent_own_topic",
            f"{relative}: slot={index} topic={expected_topic}",
        )
        audit.hard(
            not foreign_topics,
            "faq_single_intent_foreign_topic",
            f"{relative}: slot={index} topic={expected_topic} foreign={foreign_topics}",
        )

    location_answer = items[2][2]
    fee_answer = items[3][2]
    location_item_text = items[2][1] + " " + location_answer
    all_faq_text = " ".join(
        question + " " + answer for _attrs, question, answer in items
    )
    audit.hard(
        location_item_text.count(center.address) == 1
        and all_faq_text.count(center.address) == 1,
        "faq_address_exact",
        f"{relative}: slot={location_item_text.count(center.address)} "
        f"all={all_faq_text.count(center.address)}",
    )
    quoted_location_values = re.findall(r"“([^”]+)”", location_answer)
    audit.hard(
        quoted_location_values == [center.address],
        "faq_address_quoted_value_exact",
        f"{relative}: {quoted_location_values}",
    )
    audit.hard(
        fee_source_state_valid(fee_answer, center.tuition_url),
        "faq_fee_source_state",
        relative,
    )
    audit.hard(
        (center.tuition_url in text) if center.tuition_url else True,
        "faq_fee_source_url_presence",
        relative,
    )
    audit.hard(
        not FEE_AMOUNT_CLAIM_RE.search(fee_answer),
        "faq_fee_amount_claim",
        relative,
    )

    all_faq = " ".join(question + " " + answer for _attrs, question, answer in items)
    expected_page_grades = (
        {grade for key in ("영어", "수학") for grade in center.grades[key]}
        if subject == "영수"
        else set(phase1.requested_grades(center, config, subject))
    )
    foreign_grades = sorted(set(GRADE_TOKEN_RE.findall(all_faq)) - expected_page_grades)
    audit.target(
        not foreign_grades,
        "faq_out_of_source_grade",
        f"{relative}: {foreign_grades}",
    )
    coarse = sorted(set(COARSE_GRADE_CLAIM_RE.findall(all_faq)))
    audit.target(not coarse, "faq_coarse_grade_claim", f"{relative}: {coarse}")

    all_school_names = extract_school_names(school_answer, school_universe)
    allowed_school_names = set(expected_schools)
    foreign_schools = sorted(set(all_school_names) - allowed_school_names)
    audit.target(
        not foreign_schools,
        "faq_out_of_source_school",
        f"{relative}: {foreign_schools[:5]}",
    )
    return [question for _attrs, question, _answer in items], unsupported


def audit(args: argparse.Namespace) -> dict[str, Any]:
    phase1 = import_script("smallclass_phase3_release_phase1_content", PHASE1_CONTENT_SCRIPT)
    schema = import_script("smallclass_phase3_release_schema", SCHEMA_SCRIPT)
    result = Audit(observe=args.observe)
    semantic_self_test = semantic_self_test_errors()
    result.hard(
        not semantic_self_test,
        "semantic_detector_self_test",
        repr(semantic_self_test[:5]),
    )
    centers = phase1.load_centers(COMMON)
    extras = load_extras(phase1)
    content_profiles = {
        profile.path.resolve(): profile
        for profile in phase1.discover_profiles(ROOT, centers)
        if profile.kind == "detail"
    }
    by_slug, physical_by_key = schema.load_sources()
    details = schema.discover_detail_pages(by_slug)
    non_details = schema.discover_non_detail_pages()
    detail_paths = [path.resolve() for path, _profile, _source in details]
    sitemap_path = (ROOT / "sitemap.xml").resolve()
    authorized = set(detail_paths) | {sitemap_path}

    result.hard(len(details) == EXPECTED_DETAILS, "detail_count", str(len(details)))
    result.hard(len(non_details) == EXPECTED_NON_DETAILS, "non_detail_count", str(len(non_details)))
    result.hard(set(content_profiles) == set(detail_paths), "profile_path_parity", "content/schema paths differ")

    tracked_changes = tracked_delta()
    allowed_script_paths = {
        "scripts/smallclass_phase3_content.py",
        "scripts/smallclass_phase3_fact_copy_audit.py",
        "scripts/smallclass_phase3_release_audit.py",
    }
    unauthorized_tracked = [
        path
        for path in tracked_changes
        if (ROOT / path).resolve() not in authorized and path not in allowed_script_paths
    ]
    result.hard(
        not unauthorized_tracked,
        "baseline_external_tracked_delta",
        repr(unauthorized_tracked[:10]),
    )

    freeze_paths = repository_freeze_paths()
    source_csv_pre_sha = sha256(CENTER_CSV.read_bytes())
    result.hard(
        source_csv_pre_sha == AUTHORITATIVE_CSV_SHA256,
        "authoritative_csv_baseline",
        f"{source_csv_pre_sha} != {AUTHORITATIVE_CSV_SHA256}",
    )
    freeze_pre_sha, freeze_pre = repository_manifest(freeze_paths)
    html_manifest_pre_sha = line_sha(
        f"{path}\t{digest}"
        for path, digest in sorted(freeze_pre.items())
        if path.endswith(".html")
    )
    overrides, projection = read_projection(args.projected_content_script, result)
    expected_projection_paths = authorized if args.projected_content_script else set()
    result.hard(
        set(overrides) == expected_projection_paths,
        "projection_scope_exact",
        f"actual={len(overrides)} expected={len(expected_projection_paths)}",
    )

    school_universe = sorted(
        {
            school
            for center in centers.values()
            for values in center.schools.values()
            for school in values
        },
        key=lambda item: (-len(item), item),
    )

    sentence_docs: dict[str, set[str]] = defaultdict(set)
    paragraph_docs: dict[str, set[str]] = defaultdict(set)
    heading_docs: dict[str, set[str]] = defaultdict(set)
    faq_question_docs: dict[str, set[str]] = defaultdict(set)
    article_docs: dict[str, list[str]] = defaultdict(list)
    final_texts: dict[Path, str] = {}
    detail_canonicals: dict[Path, str] = {}
    article_dates: dict[str, tuple[str, str]] = {}
    unsupported_count = 0
    within_paragraph_excess = 0
    within_sentence_excess = 0
    max_paragraph = 0
    max_sentence = 0
    max_manuscript_locality = 0
    max_article_locality = 0
    max_query = 0
    cross_source_note_blocks_excluded = 0
    cross_source_note_sentences_excluded = 0
    blank_guide_premise_pages = 0
    blank_guide_premise_occurrences = 0
    unsupported_grade_premise_pages = 0
    unsupported_grade_premise_occurrences = 0
    unsupported_positive_block_pages = 0
    unsupported_positive_block_occurrences = 0
    fee_double_topic_pages = 0
    fee_double_topic_occurrences = 0
    schema_changes = 0
    baseline = GitBatch(ROOT)
    try:
        for path, schema_profile, source in details:
            path = path.resolve()
            relative = normalize_path(path)
            profile = content_profiles[path]
            center = centers[profile.locality]
            config = phase1.CATEGORY_CONFIG[profile.category]
            extra = extras[profile.locality]
            schools = tuple(phase1.school_order(center, config))
            page_unsupported = phase1.unsupported_page(center, config)
            baseline_text = decode_utf8(baseline.get(f"{BASELINE_REF}:{relative}"), path)
            content_bytes = bytes_for(path, overrides)
            content_text = decode_utf8(content_bytes, path)

            result.hard(
                phase1.stable_snapshot(baseline_text) == phase1.stable_snapshot(content_text),
                "content_stable_snapshot_baseline",
                relative,
            )
            try:
                physical = physical_by_key[source.center_key]
                candidate = schema.build_detail_candidate(
                    path,
                    schema_profile,
                    source,
                    physical,
                    original_text=content_text,
                )
                final_text = candidate.transformed
                schema_changes += final_text != content_text
                second = schema.build_detail_candidate(
                    path,
                    schema_profile,
                    source,
                    physical,
                    original_text=final_text,
                )
                result.hard(second.transformed == final_text, "schema_projection_idempotency", relative)
            except Exception as exc:
                result.hard(False, "schema_projection", f"{relative}: {exc}")
                continue
            if args.projected_content_script is None:
                result.hard(final_text == content_text, "schema_actual_dry_run", relative)
            result.hard(
                non_json_skeleton(content_text) == non_json_skeleton(final_text),
                "schema_non_json_bytes",
                relative,
            )
            result.hard(
                phase1.stable_snapshot(baseline_text) == phase1.stable_snapshot(final_text),
                "final_stable_snapshot_baseline",
                relative,
            )
            final_texts[path] = final_text

            canonicals = canonical_values(final_text)
            expected_url = page_url(path)
            result.hard(
                canonicals == [expected_url],
                "canonical_route_exact",
                f"{relative}: {canonicals} != {[expected_url]}",
            )
            if len(canonicals) == 1:
                detail_canonicals[path] = canonicals[0]

            manuscript = manuscript_html(final_text)
            article = article_html(final_text)
            result.target(bool(article), "article_selector", relative)
            result.target(
                len(list(ARTICLE_RE.finditer(final_text))) == 1
                and len(list(FAQ_SECTION_RE.finditer(final_text))) == 1
                and len(list(PREP_SECTION_RE.finditer(final_text))) == 1,
                "manuscript_container_coverage",
                relative,
            )
            blocks = manuscript_blocks(final_text)
            page_sentences = [sentence for block in blocks for sentence in sentences(block)]
            cross_blocks = cross_document_blocks(final_text)
            cross_all_sentences = [sentence for block in cross_blocks for sentence in sentences(block)]
            cross_sentences = cross_document_sentences(cross_blocks)
            cross_source_note_blocks_excluded += len(blocks) - len(cross_blocks)
            cross_source_note_sentences_excluded += len(cross_all_sentences) - len(cross_sentences)
            authored_text = " ".join(cross_blocks)
            guide_premises = (
                blank_guide_premise_sentences(authored_text)
                if not extra.location_guide
                else []
            )
            grade_premises = (
                unsupported_grade_premise_sentences(authored_text)
                if page_unsupported
                else []
            )
            positive_unsupported_blocks = (
                unsupported_high_confidence_premise_blocks(final_text)
                if page_unsupported
                else []
            )
            fee_double_topics = fee_double_topic_sentences(authored_text)
            blank_guide_premise_pages += bool(guide_premises)
            blank_guide_premise_occurrences += len(guide_premises)
            unsupported_grade_premise_pages += bool(grade_premises)
            unsupported_grade_premise_occurrences += len(grade_premises)
            unsupported_positive_block_pages += bool(positive_unsupported_blocks)
            unsupported_positive_block_occurrences += len(positive_unsupported_blocks)
            fee_double_topic_pages += bool(fee_double_topics)
            fee_double_topic_occurrences += len(fee_double_topics)
            result.hard(
                not guide_premises,
                "blank_location_guide_positive_premise",
                f"{relative}: {guide_premises[:3]!r}",
            )
            result.hard(
                not grade_premises,
                "unsupported_grade_value_premise",
                f"{relative}: {grade_premises[:3]!r}",
            )
            result.hard(
                not positive_unsupported_blocks,
                "unsupported_positive_premise_block",
                f"{relative}: {positive_unsupported_blocks[:3]!r}",
            )
            result.hard(
                not fee_double_topics,
                "fee_double_topic",
                f"{relative}: {fee_double_topics[:3]!r}",
            )
            paragraph_counter = Counter(blocks)
            sentence_counter = Counter(page_sentences)
            paragraph_excess = sum(count - 1 for count in paragraph_counter.values() if count > 1)
            sentence_excess = sum(count - 1 for count in sentence_counter.values() if count > 1)
            within_paragraph_excess += paragraph_excess
            within_sentence_excess += sentence_excess
            result.target(
                paragraph_excess == MAX_WITHIN_DUPLICATE_EXCESS,
                "within_page_paragraph_duplicate",
                f"{relative}: excess={paragraph_excess}",
            )
            result.target(
                sentence_excess == MAX_WITHIN_DUPLICATE_EXCESS,
                "within_page_sentence_duplicate",
                f"{relative}: excess={sentence_excess}",
            )
            page_max_paragraph = max((len(value) for value in blocks), default=0)
            page_max_sentence = max((len(value) for value in page_sentences), default=0)
            max_paragraph = max(max_paragraph, page_max_paragraph)
            max_sentence = max(max_sentence, page_max_sentence)
            result.target(
                page_max_paragraph <= MAX_PARAGRAPH_CHARS,
                "paragraph_length",
                f"{relative}: {page_max_paragraph}",
            )
            result.target(
                page_max_sentence <= MAX_SENTENCE_CHARS,
                "sentence_length",
                f"{relative}: {page_max_sentence}",
            )

            manuscript_text = fragment_text(manuscript)
            article_text = fragment_text(article)
            locality = center.display_locality
            query = f"{locality} {config['label']}"
            manuscript_locality = manuscript_text.count(locality)
            article_locality = article_text.count(locality)
            query_count = manuscript_text.count(query)
            max_manuscript_locality = max(max_manuscript_locality, manuscript_locality)
            max_article_locality = max(max_article_locality, article_locality)
            max_query = max(max_query, query_count)
            result.target(
                manuscript_locality <= MAX_MANUSCRIPT_LOCALITY,
                "manuscript_locality",
                f"{relative}: {manuscript_locality}",
            )
            result.target(
                article_locality <= MAX_ARTICLE_LOCALITY,
                "article_locality",
                f"{relative}: {article_locality}",
            )
            result.target(
                query_count <= MAX_MANUSCRIPT_QUERY,
                "manuscript_query",
                f"{relative}: {query_count}",
            )

            normalizer = lambda value: normalized_template(value, center, config, extra, schools)
            normalized_article = normalizer(article_text)
            if normalized_article:
                article_docs[normalized_article].append(relative)
            for value in {normalizer(item) for item in cross_blocks} - {""}:
                paragraph_docs[value].add(relative)
            for value in {normalizer(item) for item in cross_sentences} - {""}:
                sentence_docs[value].add(relative)
            headings = [fragment_text(value) for value in HEADING_RE.findall(manuscript)]
            for value in {normalizer(item) for item in headings} - {""}:
                heading_docs[value].add(relative)

            questions, unsupported = validate_faq(
                result,
                relative,
                final_text,
                center,
                config,
                phase1,
                school_universe,
            )
            unsupported_count += unsupported
            if page_unsupported:
                article_grade_facts = marked_grade_fact_paragraphs(final_text)
                result.hard(
                    len(article_grade_facts) == 1,
                    "unsupported_article_grade_fact_count",
                    f"{relative}: {article_grade_facts!r}",
                )
                if len(article_grade_facts) == 1:
                    grade_fact = article_grade_facts[0]
                    if str(config["subject"]) == "영수":
                        validate_combined_subject_grades(
                            result,
                            relative,
                            grade_fact,
                            center,
                            "article_grade",
                            True,
                        )
                    else:
                        result.hard(
                            not GRADE_TOKEN_RE.search(grade_fact)
                            and bool(MISSING_SOURCE_RE.search(grade_fact))
                            and context_scope(config) in grade_fact,
                            "unsupported_article_grade_fact_exact",
                            f"{relative}: {grade_fact!r}",
                        )
            for value in {normalizer(item) for item in questions} - {""}:
                faq_question_docs[value].add(relative)

            article_node = one_node(final_text, "Article")
            baseline_article = one_node(baseline_text, "Article")
            result.hard(article_node is not None, "article_schema_count", relative)
            result.hard(baseline_article is not None, "baseline_article_schema_count", relative)
            if article_node is not None and baseline_article is not None:
                published = article_node.get("datePublished")
                baseline_published = baseline_article.get("datePublished")
                modified = article_node.get("dateModified")
                result.hard(
                    ("datePublished" in article_node) == ("datePublished" in baseline_article)
                    and published == baseline_published,
                    "date_published_preservation",
                    f"{relative}: {published} != {baseline_published}",
                )
                result.target(
                    modified == PHASE3_DATE,
                    "date_modified_phase3",
                    f"{relative}: {modified}",
                )
                article_dates[expected_url] = (
                    published if isinstance(published, str) else "",
                    modified if isinstance(modified, str) else "",
                )

        baseline_sitemap = baseline.get(f"{BASELINE_REF}:sitemap.xml")
    finally:
        baseline.close()

    result.hard(unsupported_count == EXPECTED_UNSUPPORTED, "unsupported_count", str(unsupported_count))
    result.hard(
        len(detail_canonicals) == EXPECTED_DETAILS
        and len(set(detail_canonicals.values())) == EXPECTED_DETAILS,
        "canonical_coverage_unique",
        f"{len(detail_canonicals)}/{len(set(detail_canonicals.values()))}",
    )

    article_duplicate_groups = {
        key: paths for key, paths in article_docs.items() if len(paths) > 1
    }
    paragraph_over = {
        key: paths for key, paths in paragraph_docs.items()
        if len(paths) > MAX_CROSS_DOCUMENT_FREQUENCY
    }
    sentence_over = {
        key: paths for key, paths in sentence_docs.items()
        if len(paths) > MAX_CROSS_DOCUMENT_FREQUENCY
    }
    heading_over = {
        key: paths for key, paths in heading_docs.items()
        if len(paths) > MAX_HEADING_DOCUMENT_FREQUENCY
    }
    faq_over = {
        key: paths for key, paths in faq_question_docs.items()
        if len(paths) > MAX_FAQ_QUESTION_DOCUMENT_FREQUENCY
    }
    result.target(
        not article_duplicate_groups,
        "normalized_article_duplicate",
        f"groups={len(article_duplicate_groups)} pages={sum(len(v) for v in article_duplicate_groups.values())}",
    )
    result.target(
        not paragraph_over,
        "normalized_paragraph_df",
        f"groups={len(paragraph_over)} max={max((len(v) for v in paragraph_docs.values()), default=0)}",
    )
    result.target(
        not sentence_over,
        "normalized_sentence_df",
        f"groups={len(sentence_over)} max={max((len(v) for v in sentence_docs.values()), default=0)}",
    )
    result.target(
        not heading_over,
        "normalized_heading_df",
        f"groups={len(heading_over)} max={max((len(v) for v in heading_docs.values()), default=0)}",
    )
    result.target(
        not faq_over,
        "normalized_faq_question_df",
        f"groups={len(faq_over)} max={max((len(v) for v in faq_question_docs.values()), default=0)}",
    )

    sitemap_value = bytes_for(sitemap_path, overrides)
    entries = sitemap_entries(sitemap_value)
    baseline_entries = sitemap_entries(baseline_sitemap)
    sitemap_errors = sitemap_shape_errors(sitemap_value)
    baseline_sitemap_errors = sitemap_shape_errors(baseline_sitemap)
    urls = [url for url, _lastmod in entries]
    baseline_urls = [url for url, _lastmod in baseline_entries]
    detail_urls = set(detail_canonicals.values())
    result.hard(not sitemap_errors, "sitemap_xml_shape", repr(sitemap_errors))
    result.hard(
        not baseline_sitemap_errors,
        "baseline_sitemap_xml_shape",
        repr(baseline_sitemap_errors),
    )
    result.hard(len(entries) == EXPECTED_INDEXABLE, "sitemap_count", str(len(entries)))
    result.hard(len(set(urls)) == len(urls), "sitemap_unique", str(len(set(urls))))
    result.hard(urls == baseline_urls, "sitemap_url_order_baseline", "URL/order drift")
    result.hard(detail_urls <= set(urls), "sitemap_detail_parity", str(len(detail_urls & set(urls))))
    baseline_lastmod = dict(baseline_entries)
    current_lastmod = dict(entries)
    non_detail_drift = [
        url for url in urls if url not in detail_urls and current_lastmod[url] != baseline_lastmod.get(url)
    ]
    result.hard(not non_detail_drift, "sitemap_non_detail_lastmod", repr(non_detail_drift[:5]))
    freshness_mismatch = [
        url
        for url in detail_urls
        if current_lastmod.get(url) != PHASE3_DATE
        or article_dates.get(url, ("", ""))[1] != current_lastmod.get(url)
    ]
    result.target(
        not freshness_mismatch,
        "sitemap_article_freshness_parity",
        f"count={len(freshness_mismatch)} sample={freshness_mismatch[:3]}",
    )

    freeze_post_paths = repository_freeze_paths()
    freeze_post_sha, freeze_post = repository_manifest(freeze_post_paths)
    source_csv_post_sha = sha256(CENTER_CSV.read_bytes())
    html_manifest_post_sha = line_sha(
        f"{path}\t{digest}"
        for path, digest in sorted(freeze_post.items())
        if path.endswith(".html")
    )
    result.hard(freeze_pre == freeze_post, "read_only_freeze", "repository files changed during run")
    result.hard(
        source_csv_pre_sha == source_csv_post_sha,
        "authoritative_csv_freeze",
        "센터정보 정리.csv changed during run",
    )

    return {
        "ok": not result.errors,
        "mode": "observe" if args.observe else "strict",
        "baseline_ref": BASELINE_REF,
        "projection": {**projection, "schema_changes_in_memory": schema_changes},
        "errors": sum(result.error_counts.values()),
        "error_counts": dict(result.error_counts),
        "error_samples": result.errors,
        "observations": sum(result.observation_counts.values()),
        "observation_counts": dict(result.observation_counts),
        "observation_samples": result.observations,
        "coverage": {
            "details": len(final_texts),
            "non_details": len(non_details),
            "unsupported": unsupported_count,
            "canonicals": len(detail_canonicals),
            "sitemap": len(entries),
        },
        "quality": {
            "within_paragraph_excess": within_paragraph_excess,
            "within_sentence_excess": within_sentence_excess,
            "max_paragraph_chars": max_paragraph,
            "max_sentence_chars": max_sentence,
            "max_manuscript_locality": max_manuscript_locality,
            "max_article_locality": max_article_locality,
            "max_manuscript_query": max_query,
            "cross_source_note_blocks_excluded": cross_source_note_blocks_excluded,
            "cross_source_note_sentences_excluded": cross_source_note_sentences_excluded,
            "blank_guide_premise_pages": blank_guide_premise_pages,
            "blank_guide_premise_occurrences": blank_guide_premise_occurrences,
            "unsupported_grade_premise_pages": unsupported_grade_premise_pages,
            "unsupported_grade_premise_occurrences": unsupported_grade_premise_occurrences,
            "unsupported_positive_block_pages": unsupported_positive_block_pages,
            "unsupported_positive_block_occurrences": unsupported_positive_block_occurrences,
            "fee_double_topic_pages": fee_double_topic_pages,
            "fee_double_topic_occurrences": fee_double_topic_occurrences,
            "normalized_article_duplicate_groups": len(article_duplicate_groups),
            "normalized_paragraph_max_df": max((len(v) for v in paragraph_docs.values()), default=0),
            "normalized_sentence_max_df": max((len(v) for v in sentence_docs.values()), default=0),
            "normalized_heading_max_df": max((len(v) for v in heading_docs.values()), default=0),
            "normalized_faq_question_max_df": max((len(v) for v in faq_question_docs.values()), default=0),
        },
        "thresholds": {
            "within_paragraph_excess": MAX_WITHIN_DUPLICATE_EXCESS,
            "within_sentence_excess": MAX_WITHIN_DUPLICATE_EXCESS,
            "cross_paragraph_df": MAX_CROSS_DOCUMENT_FREQUENCY,
            "cross_sentence_df": MAX_CROSS_DOCUMENT_FREQUENCY,
            "heading_df": MAX_HEADING_DOCUMENT_FREQUENCY,
            "faq_question_df": MAX_FAQ_QUESTION_DOCUMENT_FREQUENCY,
            "manuscript_locality": MAX_MANUSCRIPT_LOCALITY,
            "article_locality": MAX_ARTICLE_LOCALITY,
            "manuscript_query": MAX_MANUSCRIPT_QUERY,
            "paragraph_chars": MAX_PARAGRAPH_CHARS,
            "sentence_chars": MAX_SENTENCE_CHARS,
        },
        "invariants": {
            "sitemap_url_order_sha256": line_sha(urls),
            "baseline_sitemap_url_order_sha256": line_sha(baseline_urls),
            "freeze_pre_sha256": freeze_pre_sha,
            "freeze_post_sha256": freeze_post_sha,
            "html_manifest_pre_sha256": html_manifest_pre_sha,
            "html_manifest_post_sha256": html_manifest_post_sha,
            "authoritative_csv_pre_sha256": source_csv_pre_sha,
            "authoritative_csv_post_sha256": source_csv_post_sha,
            "tracked_delta_count": len(tracked_changes),
            "tracked_unauthorized_count": len(unauthorized_tracked),
        },
    }


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--projected-content-script",
        type=Path,
        help="Import build_plan(root, reference_dir) and audit content -> schema in memory",
    )
    parser.add_argument(
        "--observe",
        action="store_true",
        help="Keep immutable gates hard; report Phase 3 targets as observations",
    )
    parser.add_argument("--json", action="store_true", help="Emit compact JSON")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        report = audit(args)
    except Exception as exc:
        report = {
            "ok": False,
            "mode": "observe" if args.observe else "strict",
            "fatal": f"{type(exc).__name__}: {exc}",
        }
    print(json.dumps(report, ensure_ascii=False, indent=None if args.json else 2, sort_keys=True))
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
