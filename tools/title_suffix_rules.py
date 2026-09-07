"""Site13 titles describe its existing source-fact guides, not invented lessons."""
from __future__ import annotations
import re

GRADE_ORDER = [f"초{i}" for i in range(1,7)] + [f"중{i}" for i in range(1,4)] + [f"고{i}" for i in range(1,4)]
GRADE_LIST = r"[초중고][1-6](?:·[초중고][1-6])*"
SINGLE_GRADE = re.compile(r"(?P<scope>(?:고등 |중등 |초등 )?(?:영어|수학)) 학년값은 (?P<grades>"+GRADE_LIST+r")(?=이며)")
COMBINED_GRADE = re.compile(r"영어 학년은 (?P<english>"+GRADE_LIST+r"), 수학 학년은 (?P<math>"+GRADE_LIST+r")(?=이며)")
MISSING_GRADE = "대상 학년 항목이 원자료에 기재되어 있지 않"

# Each cue is actually in the page's introduction. These are descriptions of
# questions to confirm, NEVER claims that a class/schedule/benefit is offered.
CONSULTATION_TOPICS = [
    ("시간표·교재·반 구성처럼 적혀 있지 않은 내용", "시간표·반 구성 확인"),
    ("개설 일정과 수업 방식 등 미기재 항목", "일정·수업 방식 확인"),
    ("요일·시간·교재처럼 현재 답변이 필요한 조건", "요일·교재 확인"),
    ("정원·일정·과제처럼 원자료 밖의 정보", "정원·과제 확인"),
    ("수업 횟수와 반 편성 등 별도 확인 사항", "횟수·반 편성 확인"),
    ("등록 시점에 달라질 수 있는 일정과 비용", "일정·비용 확인"),
    ("구체적인 시간표와 교재처럼 표에 없는 항목", "시간표·교재 확인"),
    ("개설 여부·수업 방식·일정에 관한 질문", "개설·수업 방식 확인"),
    ("현재 적용 조건과 세부 비용 같은 미확인 내용", "적용 조건·비용 확인"),
    ("과제·보강·상담 주기 등 추가 질문", "과제·보강 확인"),
    ("반 배정과 시작일처럼 자료만으로 모르는 정보", "반 배정·시작일 확인"),
    ("수업 시간과 비용의 최신 적용 조건", "수업 시간·비용 확인"),
    ("교재·횟수·결석 처리 같은 상담 항목", "교재·결석 처리 확인"),
    ("신청 가능 여부와 세부 일정", "신청·일정 확인"),
    ("표에 없는 수업 조건과 변경 기준", "수업 조건·변경 기준 확인"),
    ("현재 운영 여부를 포함한 추가 정보", "현재 운영 여부 확인"),
]
MISSING_TOPICS = [
    ("실제 개설 여부와 대상 학년", "개설 여부 확인"),
    ("현재 운영 여부", "현재 운영 여부 확인"),
    ("대상 학년·요일·시간", "요일·시간 확인"),
    ("수업 조건은 상담에서 확인", "수업 조건 확인"),
    ("다른 과목이나 센터의 학년 정보", "과목별 정보 구분"),
    ("답변 주체·확인일", "답변 주체·확인일 점검"),
    ("담당자 답변을 기다리", "담당자 답변 확인"),
    ("이후 상담 답변을 서로 다른 칸", "자료와 상담 답변 구분"),
    ("현재 신청 가능 여부", "신청 가능 여부 확인"),
    ("수업 시간과 교재", "수업 시간·교재 확인"),
    ("어떤 학년도 제공 대상으로 단정하지", "대상 범위 확인"),
    ("대상 학년과 시작일", "시작일 확인"),
    ("반 편성이나 운영 범위", "반 편성·운영 범위 확인"),
    ("현재 대상과 일정을 문의", "현재 일정 확인"),
    ("대상 학년 칸을 미확인 상태로", "학년 확인 상태 점검"),
    ("실제 수업 조건은 센터 확인 뒤", "센터 확인 후 조건 기록"),
]

HUB_SUFFIXES = {
    "고등수학학원": ("개념 이해·조건 해석과 반복 오답", ["개념 이해","조건 해석","반복 오답"]),
    "고등영어학원": ("단어·문법·독해와 서술형 확인", ["단어·문법·독해·서술형"]),
    "수학학원": ("개념·계산·서술형과 오답 재확인", ["개념 이해","계산","서술형 풀이","오답 재확인"]),
    "영수학원": ("두 과목 우선순위·진단과 복습 시간", ["복습 시간","우선순위","두 과목의 진단"]),
    "영어학원": ("어휘 유지·문법 적용과 지문 독해", ["어휘 유지","문법 적용","지문 독해"]),
    "중등수학학원": ("개념 연결·서술형과 오답 재학습", ["개념 연결","서술형 풀이","오답 재학습"]),
    "중등영어학원": ("어휘·문법 연결과 서술형 쓰기", ["어휘·문법 연결","서술형 쓰기"]),
    "초등수학학원": ("계산 정확도·문장제와 오답 재풀이", ["계산 정확도","문장제 해석","오답 재풀이"]),
    "초등영어학원": ("읽기 정확도·짧은 쓰기와 복습 습관", ["읽기 정확도","짧은 쓰기","복습 습관"]),
}

def grade_tokens(raw):
    tokens=raw.split("·")
    if not tokens or any(t not in GRADE_ORDER for t in tokens):
        raise ValueError("Invalid source grade list: "+raw)
    if tokens != sorted(set(tokens),key=GRADE_ORDER.index):
        raise ValueError("Unordered or duplicated source grades: "+raw)
    return tokens

def compact_grades(raw):
    """Use a range only when EVERY intervening grade is explicitly present."""
    tokens=grade_tokens(raw)
    groups=[]
    current=[]
    for token in tokens:
        if current and GRADE_ORDER.index(token)!=GRADE_ORDER.index(current[-1])+1:
            groups.append(current);current=[]
        current.append(token)
    groups.append(current)
    return "·".join(group[0]+"~"+group[-1] if len(group)>=3 else "·".join(group) for group in groups)

def evidence(label,match,text,role,**extra):
    if match not in text:
        raise ValueError("Evidence must be verbatim in its body paragraph")
    return dict(label=label,match=match,excerpt=text,role=role,**extra)

def select_detail(page):
    intro=[b["text"] for b in page["blocks"] if b["role"]=="intro"]
    if len(intro)!=1:
        raise ValueError("Expected exactly one factual introduction: "+page["rel"])
    intro=intro[0]
    missing=MISSING_GRADE in intro
    if missing:
        primary=evidence("학년 자료 미기재",MISSING_GRADE,intro,"grade-status",gradeValues={})
    elif page["subject"]=="combined":
        found=[(b["text"],m) for b in page["blocks"] if b["role"]=="prose"
               for m in COMBINED_GRADE.finditer(b["text"])]
        if len(found)!=1:
            raise ValueError("Expected one English/math grade pair: "+page["rel"])
        text,match=found[0]
        eng,math=match["english"],match["math"]
        label=(f"영어·수학 {compact_grades(eng)} 표기" if eng==math else
               f"영어 {compact_grades(eng)}·수학 {compact_grades(math)} 표기")
        primary=evidence(label,match[0],text,"grade-values",gradeValues={"english":eng,"math":math})
    else:
        found=[(b["text"],m) for b in page["blocks"] if b["role"]=="prose"
               for m in SINGLE_GRADE.finditer(b["text"])]
        if len(found)!=1:
            raise ValueError("Expected one exact subject-grade fact: "+page["rel"])
        text,match=found[0]
        if match["scope"].replace(" ","")!=page["group"].removesuffix("학원"):
            raise ValueError("Source grade scope differs from page category: "+page["rel"])
        raw=match["grades"]
        stage={"elementary":"초","middle":"중","high":"고"}.get(page["stage"])
        if stage and any(not token.startswith(stage) for token in grade_tokens(raw)):
            raise ValueError("Source grade list crosses the page's school level")
        primary=evidence(compact_grades(raw)+" 학년 표기",match[0],text,"grade-values",
                         gradeValues={page["subject"]:raw})
    topics=MISSING_TOPICS if missing else CONSULTATION_TOPICS
    secondary=[evidence(label,cue,intro,"consultation-question") for cue,label in topics if cue in intro]
    if len(secondary)!=1:
        raise ValueError("Expected one supported consultation focus: "+page["rel"])
    selected=[primary,secondary[0]]
    return "·".join(e["label"] for e in selected),selected,missing
