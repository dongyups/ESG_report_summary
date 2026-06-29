# LangGraph 기반 오케스트레이션 모듈
#
# 세 개의 그래프를 제공한다:
#
#  [QA_GRAPH] 챗봇 Q&A 플로우
#    START → analyze_query → retrieve_docs → END
#    - analyze_query: Haiku로 쿼리 의도(qa/data_lookup/report_section)와
#                     E/S/G 카테고리를 추론. 검색 전략 파라미터 결정.
#    - retrieve_docs:  분석 결과에 맞는 컬렉션/k값/임계값으로 검색.
#    - 최종 생성(LLM 호출)은 service.py에서 SSE 스트리밍으로 수행.
#
#  [REPORT_GRAPH] ESG 보고서 자동 작성 플로우 (Map-Reduce 패턴, 사람 개입 없음)
#    START → plan → [process_section × N, 병렬] → synthesize → END
#    - plan:            전년도 보고서를 검색하여 섹션 목록 수립.
#    - process_section: 섹션별 검색 + 초안 생성. Send API로 병렬 실행.
#    - synthesize:      모든 섹션 초안을 취합하여 최종 보고서 생성.
#
#  [SECTION_GRAPH] 단일 섹션 작성/수정 플로우 (HITL, interrupt + AsyncSqliteSaver)
#    검색 → Self-RAG 평가 → 문서 검토(사람) → 초안 생성 → 초안 검토(사람) →
#    (반려/수정/추가검색에 따라 앞 단계로 순환) → 승인 시 END.
#    REPORT_GRAPH와 달리 "섹션 하나"를 사람이 매 단계 승인하며 다듬는 대화형
#    워크플로우라 컴파일 시점에 checkpointer가 필요하다. 자세한 설계는
#    파일 하단 SECTION_GRAPH 섹션의 주석 참조. FastAPI 엔드포인트는
#    section_api.py에 있다.

from __future__ import annotations

import asyncio
import json
from typing import Annotated, Any, Dict, List, Optional, TypedDict
import operator

# from langchain_anthropic import ChatAnthropic
from langchain_aws import ChatAnthropicBedrock
from langchain_core.documents import Document
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.types import Send, interrupt

from app.core.config import settings
from app.modules.rag.retriever import (
    detect_esg_category,
    docs_to_context,
    docs_to_sources,
    retrieve,
    retrieve_sync,
)

### 추후 업데이트가 필요한 부분 ###
# langchain_anthropic의 with_structured_output을 활용하여 — 구조화 출력 (tool-use 강제, 파싱 실패 자체를 제거) 으로 바꾸는게 좋다.
# analyze_query와 plan_report와 node_grade_docs는 find("[")…rfind("]") 추출로 1차 방어가 되어있다. 
# 구조화 출력 방식으로 가기로 하면 일관성을 위해 그 둘도 리스트 스키마(list[...])로 바꾸는 걸 권한다 — 거기도 "괄호 안쪽이 깨지는" 케이스는 똑같이 못 막으니까.
############################

# ──────────────────────────────────────────────
# LLM 인스턴스
# ──────────────────────────────────────────────

# 쿼리 분석·섹션 계획 전용 (빠른 응답, 낮은 비용)
_llm_fast = ChatAnthropicBedrock(
    model=settings.LLM_MODEL,
    # api_key=settings.ANTHROPIC_API_KEY,
    region_name=settings.AWS_REGION,
    temperature=0,
    max_tokens=1000,
)

# 섹션 초안·보고서 합성 전용 (높은 품질)
_llm_main = ChatAnthropicBedrock(
    model=settings.RAG_LLM_MODEL,
    # api_key=settings.ANTHROPIC_API_KEY,
    region_name=settings.AWS_REGION,
    temperature=0,
    max_tokens=8000,
)


# ════════════════════════════════════════════════════════════
# QA Graph
# ════════════════════════════════════════════════════════════

class QAState(TypedDict):
    """QA 그래프 전체 공유 상태."""
    # 입력
    query:   str
    history: List[Dict]

    # analyze_query 노드에서 채움
    query_intent:    str            # "qa" | "data_lookup" | "report_section"
    esg_category:    Optional[str]  # "E" | "S" | "G" | "I" | None(미지정→키워드 추론)
    use_multi_query: bool

    # retrieve_docs 노드에서 채움 (service.py에서 소비)
    docs: List[Document]


_ANALYZE_PROMPT = """다음 쿼리를 분석하여 JSON만 출력하라. 다른 텍스트나 마크다운 없이.

쿼리: {query}

출력 형식:
{{
  "query_intent": "qa" | "data_lookup" | "report_section",
  "esg_category": "E" | "S" | "G" | "I",
  "use_multi_query": true | false
}}

분류 기준:
- data_lookup:    특정 수치·통계 조회 (예: "온실가스 배출량", "여성 임원 비율")
- report_section: 보고서 섹션 작성·초안 요청 (예: "환경 섹션 작성해줘")
- qa:             그 외 일반 질의응답
- esg_category:   명확한 E/S/G 분류가 가능할 때만 해당 알파벳 사용.
                  CEO/대표이사 인사말, 회사·사업 개요, ESG 추진 체계 소개,
                  ESG 핵심성과 요약처럼 특정 pillar에 속하지 않는 내용은
                  "I"로 분류. ("주주", "이사회" 등의 단어가 포함되어도
                  내용이 인사말·개요라면 I 분류를 우선한다.)
- use_multi_query: 쿼리가 광범위하거나 다면적이면 true

다시 한번 언급한다. 위의 출력 형식과 같이 JSON만 출력하라. 다른 텍스트나 마크다운 없이."""


async def analyze_query(state: QAState) -> QAState:
    """Haiku로 쿼리 의도를 분석하여 최적 검색 전략을 결정한다."""
    try:
        resp = await _llm_fast.ainvoke([
            HumanMessage(content=_ANALYZE_PROMPT.format(query=state["query"]))
        ])
        raw = resp.content.strip()
        start, end = raw.find("{"), raw.rfind("}") + 1
        data = json.loads(raw[start:end])
    except Exception:
        # 폴백: 키워드 기반 최소 분석
        data = {
            "query_intent":    "qa",
            "esg_category":    detect_esg_category(state["query"]),
            "use_multi_query": False,
        }

    return {
        **state,
        "query_intent":    data.get("query_intent", "qa"),
        "esg_category":    data.get("esg_category"),
        "use_multi_query": data.get("use_multi_query", False),
    }


# # 쿼리 의도별 검색 전략 (profile, target_collections)
# # 컬렉션별 실제 n/threshold는 retriever.COLLECTION_PROFILES에서 확정 관리됨
# #   data_lookup    → profile="qa"        : esg_data n=8/th0.45, report n=5/th0.40
# #   report_section → profile="report_gen": 전체 4개 컬렉션, 재현율 우선
# #   qa             → profile="qa"        : 전체 4개 컬렉션, 정밀도 우선
# _QA_STRATEGY: Dict[str, Dict[str, Any]] = {
#     "data_lookup":    {"profile": "qa",         "cols": ["esg_data", "report"]},
#     "report_section": {"profile": "report_gen", "cols": None},   # 전체 컬렉션
#     "qa":             {"profile": "qa",          "cols": None},
# }
### 수정 후 — 인터랙티브 챗봇은 사람이 바로 읽을 shortlist가 필요 → 정밀도 + 상한 ###
_QA_STRATEGY: Dict[str, Dict[str, Any]] = {
    "data_lookup":    {"profile": "qa", "cols": ["esg_data", "report"], "final_k": 12},
    "report_section": {"profile": "qa", "cols": None,                   "final_k": 15},
    "qa":             {"profile": "qa", "cols": None,                   "final_k": 10},
}

async def retrieve_docs(state: QAState) -> QAState:
    """분석 결과를 기반으로 최적화된 컬렉션·프로파일로 검색한다.

    컬렉션별 실제 n/threshold는 retriever.COLLECTION_PROFILES 참조:
    - data_lookup: esg_data + report만 타겟, profile="qa"
                   (esg_data n=8/th0.45, report n=5/th0.40)
    - report_section: 전체 4개 컬렉션, profile="report_gen" (재현율 우선)
    - qa: 전체 4개 컬렉션, profile="qa" (정밀도 우선)
    """
    strategy = _QA_STRATEGY.get(
        state.get("query_intent", "qa"),
        _QA_STRATEGY["qa"],
    )

    docs = await asyncio.to_thread(
        retrieve_sync,
        state["query"],
        strategy["profile"],
        state.get("esg_category"),
        strategy["cols"],
        ### state.get("use_multi_query", False) ###
        False,                       # ← MultiQuery 비활성: 실제 score 보존 + 랭킹 복원
        strategy.get("final_k"),     # ← 최종 상한 (retrieve_sync 6번째 인자)
    )

    return {**state, "docs": docs}


def build_qa_graph():
    g = StateGraph(QAState)
    g.add_node("analyze_query", analyze_query)
    g.add_node("retrieve_docs", retrieve_docs)
    g.add_edge(START, "analyze_query")
    g.add_edge("analyze_query", "retrieve_docs")
    g.add_edge("retrieve_docs", END)
    return g.compile()


QA_GRAPH = build_qa_graph()


# ════════════════════════════════════════════════════════════
# ESG Report Generation Graph  (Map-Reduce 패턴)
# ════════════════════════════════════════════════════════════

class SectionState(TypedDict):
    """개별 섹션의 처리 상태 (Send API로 process_section 노드에 전달)."""
    section_title: str
    esg_category:  str        # "E" | "S" | "G" | "I"
    keywords:      List[str]
    target_year:   str
    # process_section 노드에서 채움
    docs:          List[Document]
    draft:         str


class ReportState(TypedDict):
    """보고서 그래프 전체 공유 상태."""
    # 입력
    target_year: str   # 작성 연도 (예: "2025")
    scope:       str   # "전체" | "E" | "S" | "G" | "I"

    # plan 노드에서 채움
    sections: List[Dict]

    # process_section 노드에서 누적 (operator.add = 병렬 결과 합산)
    completed_sections: Annotated[List[SectionState], operator.add]

    # synthesize 노드에서 채움
    final_report: str


# ──────────────────────────────────────────────
# plan 노드
# ──────────────────────────────────────────────
### "{year}년" 제거 ###
_PLAN_PROMPT = """SK하이닉스 ESG 보고서의 '{scope}' 부분을 구성할 섹션 목록을 작성하라.
보고서 내용을 참고하여 현실적인 구성으로 만들어라.

보고서 참고 내용:
{prev_context}

출력 형식: JSON 배열만. 다른 텍스트 없이.
[
  {{
    "section_title": "섹션명",
    "esg_category":  "E" | "S" | "G" | "I",
    "keywords":      ["검색 키워드 3-5개"]
  }}
]

esg_category 분류 기준:
- E/S/G : 해당 pillar에 명확히 속하는 섹션
- I     : CEO/대표이사 인사말, 회사·사업 개요, ESG 추진 체계·프레임워크 소개,
          ESG 핵심성과 요약처럼 특정 pillar에 속하지 않는 도입부·총괄 섹션
- scope이 "전체"가 아니라 특정 범위("I"/"E"/"S"/"G")로 지정된 경우, 해당 범위에
  속하는 섹션만 작성하라. "E"/"S"/"G"이면 그 pillar 섹션만(I섹션 제외),
  "I"이면 CEO 인사말·회사 개요·ESG 추진 체계 같은 도입부·총괄 섹션만 작성하라.

다시 한번 언급한다. 위의 출력 형식과 같이 JSON 배열만 출력하라. 다른 텍스트 없이."""


def _default_sections(scope: str) -> List[Dict]:
    """전년도 보고서 검색 실패 시 사용할 기본 섹션 구조."""
    base: Dict[str, List[Dict]] = {
        "I": [
            {"section_title": "주주들을 위한 CEO의 메시지", "esg_category": "I",
             "keywords": ["CEO", "대표이사", "인사말", "주주", "경영진 메시지"]},
            {"section_title": "회사 개요 및 ESG 추진 체계", "esg_category": "I",
             "keywords": ["회사 개요", "ESG 전략", "PRISM", "추진 체계"]},
        ],
        "E": [
            {"section_title": "기후변화 대응 및 탄소중립",   "esg_category": "E",
             "keywords": ["온실가스", "탄소중립", "Net Zero", "Scope 1", "Scope 2"]},
            {"section_title": "에너지 효율화 및 재생에너지", "esg_category": "E",
             "keywords": ["에너지 소비", "재생에너지", "에너지 효율화"]},
            {"section_title": "수자원 및 폐수 관리",         "esg_category": "E",
             "keywords": ["수자원", "용수 사용량", "폐수", "수질"]},
            {"section_title": "폐기물 및 유해화학물질 관리", "esg_category": "E",
             "keywords": ["폐기물", "화학물질", "재활용", "순환경제"]},
        ],
        "S": [
            {"section_title": "임직원 안전·보건",       "esg_category": "S",
             "keywords": ["산업재해", "안전", "보건", "LTIR", "무재해"]},
            {"section_title": "인재 육성 및 다양성",    "esg_category": "S",
             "keywords": ["교육훈련", "인재개발", "다양성", "여성 임원", "임직원"]},
            {"section_title": "인권 및 노동 기준",      "esg_category": "S",
             "keywords": ["인권", "노동", "결사의 자유", "강제노동"]},
            {"section_title": "공급망 ESG 관리",        "esg_category": "S",
             "keywords": ["공급망", "협력사", "ESG 평가", "공급망 실사"]},
        ],
        "G": [
            {"section_title": "이사회 구성 및 운영",    "esg_category": "G",
             "keywords": ["이사회", "사외이사", "지배구조", "위원회"]},
            {"section_title": "윤리 및 컴플라이언스",   "esg_category": "G",
             "keywords": ["윤리", "컴플라이언스", "부패방지", "공정거래"]},
            {"section_title": "정보보호 및 사이버보안", "esg_category": "G",
             "keywords": ["정보보호", "개인정보", "사이버보안", "ISMS"]},
        ],
    }

    if scope == "전체":
        result: List[Dict] = []
        for key in ("I", "E", "S", "G"):
            result.extend(base[key])
        return result
    return base.get(scope, base["E"])


async def plan_report(state: ReportState) -> ReportState:
    """전년도 보고서를 검색하여 이번 연도의 섹션 구성을 결정한다."""
    year  = state["target_year"]
    scope = state.get("scope", "전체")

    # 전년도 보고서에서 목차·구성 검색 (report 컬렉션, report_gen 프로파일 → n=8/th0.35)
    prev_docs = await asyncio.to_thread(
        retrieve_sync,
        f"SK하이닉스 ESG 보고서 구성 목차 {scope}",
        "report_gen", None, ["report"], False,
    )
    prev_context = "\n\n".join(d.page_content[:400] for d in prev_docs[:6])

    try:
        resp = await _llm_fast.ainvoke([
            HumanMessage(content=_PLAN_PROMPT.format(
                year=year, scope=scope, prev_context=prev_context,
            ))
        ])
        raw = resp.content.strip()
        start, end = raw.find("["), raw.rfind("]") + 1
        sections_data: List[Dict] = json.loads(raw[start:end])
    except Exception:
        sections_data = _default_sections(scope)

    sections: List[Dict] = [
        {
            "section_title": s["section_title"],
            # LLM이 esg_category를 누락하거나 null로 준 경우 "E"로 강제하면
            # 잘못된 카테고리 필터가 걸려 정작 관련 문서가 배제될 수 있다.
            # "I"로 두면 필터 없이 검색되어 안전하다 (retriever.py 참조).
            "esg_category":  s.get("esg_category") or "I",
            "keywords":      s.get("keywords", []),
            "target_year":   year,
            "docs":          [],
            "draft":         "",
        }
        for s in sections_data
    ]

    return {**state, "sections": sections, "completed_sections": []}


# ──────────────────────────────────────────────
# process_section 노드 (Send API로 병렬 실행)
# ──────────────────────────────────────────────
### "{year}년" 제거 ###
_DRAFT_PROMPT = """SK하이닉스 ESG 보고서 '{section}' 섹션을 작성하라.

이 작업은 매년 반복되는 보고서 갱신 업무다. 보고서의 형식·구조를
토대로 누적된 자료(보도자료, 뉴스, 실적 데이터)를 반영해 갱신된
초안을 만드는 것이며, 결과물은 사내 검토·승인을 거치는 내부 초안이다.

[참고 자료]
{context}

[작성 기준]
- 보고서 형식과 전문적·사무적 어투 유지
- 현황 → 주요 성과 → 목표 및 이행 계획 흐름으로 서술
- 600~800자 내외

[수치·근거 규칙 — 엄수]
- [참고 자료]에 제시된 수치·사실만 사용한다. 자료에 없는 수치는 추정하거나
  만들어내지 말고, 서술에 필요하면 '(데이터 확인 필요)'로 표기한다.
- 수치·실적·구체적 사실을 담은 문장 끝에는 근거 자료를 [출처 N] 형식으로
  단다(N은 [참고 자료]의 출처 번호, 복수면 [출처 2, 5]). 일반 연결·서술
  문장에는 달지 않는다. 이 표기는 검토용 근거 표시다.
{extra_guidance}"""

# I(CEO 메시지/주주 서신 등 특정 인물 귀속 섹션) 전용 추가 지침.
# 없으면 모델이 "실제 인물의 발언을 대신 작성/예측"한다고 판단해
# 작성을 회피하거나 과도하게 머뭇거리는 경향이 있다.
_I_SECTION_GUIDANCE = """- 이 섹션은 CEO/대표이사 등 특정 인물에게 귀속되는 내용이다. 실제로 그
  인물이 이미 발언했다고 단정하는 표현은 피하고, 검토·승인 전 초안임을 전제로 작성한다. 
  메시지의 구조·어조는 참고하되, 사실에 근거하지 않은 구체적 일화나 개인적 발언은 만들지 않는다."""


async def process_section(state: Dict[str, Any]) -> Dict[str, Any]:
    """단일 섹션 처리: 검색 → 초안 생성.
    
    Send API로 병렬 호출됨. 반환값은 ReportState.completed_sections에
    operator.add로 누적됨 (Map-Reduce의 Map 단계).
    """
    section = dict(state)  # SectionState

    # 검색: 섹션명 + 키워드로 쿼리 구성
    search_query = f"{section['section_title']} {' '.join(section.get('keywords', []))}"

    # report_gen 프로파일 → 컬렉션별 차등 n/threshold 적용
    # (press n=4/th0.30, newsroom n=8/th0.30, report n=8/th0.35, esg_data n=12/th0.40)
    docs = await asyncio.to_thread(
        retrieve_sync,
        search_query,
        "report_gen",
        section.get("esg_category"),
        None,  # 전체 컬렉션
        False,
    )
    section["docs"] = docs

    context = docs_to_context(docs)

    try:
        extra_guidance = (
            _I_SECTION_GUIDANCE if section.get("esg_category") == "I" else ""
        )
        resp = await _llm_main.ainvoke([
            HumanMessage(content=_DRAFT_PROMPT.format(
                year=section.get("target_year", "2025"),
                section=section["section_title"],
                context=context,
                extra_guidance=extra_guidance,
            ))
        ])
        section["draft"] = resp.content
    except Exception as e:
        section["draft"] = f"[초안 생성 실패: {e}]"

    # completed_sections에 누적 (operator.add)
    return {"completed_sections": [section]}


# ──────────────────────────────────────────────
# fan_out: plan → process_section (병렬 분기)
# ──────────────────────────────────────────────

def fan_out_sections(state: ReportState) -> List[Send]:
    """각 섹션에 Send 이벤트를 발행하여 process_section을 병렬로 실행한다.
    
    LangGraph Map-Reduce 패턴:
    - Send("노드명", 상태) → 해당 노드를 독립 실행
    - 모든 Send가 완료된 후 다음 엣지(synthesize)로 진행
    """
    return [Send("process_section", section) for section in state["sections"]]


# ──────────────────────────────────────────────
# synthesize 노드 (Reduce 단계)
# ──────────────────────────────────────────────

_SYNTHESIZE_PROMPT = """다음 ESG 보고서 초안을 최종 검토하고 다듬어라.

요구사항:
- 섹션 간 일관성 및 수치 정합성 확인
- 전문적인 보고서 어투 통일
- 표지·서론·결론 추가 (간략하게)
- 원본 섹션 내용은 최대한 유지
- 각 섹션 본문의 [출처 N] 표기는 해당 섹션의 근거 표시다. 번호를 바꾸거나
  삭제·통합하지 말고 그대로 유지한다(N은 섹션별로 독립적이다).

초안:
{draft}"""


async def synthesize_report(state: ReportState) -> ReportState:
    """모든 섹션 초안을 취합하여 최종 보고서를 생성한다 (Reduce 단계)."""
    completed = state.get("completed_sections", [])
    if not completed:
        return {**state, "final_report": ""}

    # I(인사말·개요) → E → S → G 순서로 정렬
    # 실제 ESG 보고서 구조상 CEO 메시지·회사 개요는 본문(E/S/G)보다 앞에 위치
    order = {"I": -1, "E": 0, "S": 1, "G": 2}
    completed.sort(key=lambda s: (
        order.get(s.get("esg_category") or "I", 3),
        s.get("section_title", ""),
    ))

    # 마크다운 구조로 조립
    parts: List[str] = []
    cur_cat: Optional[str] = None
    cat_labels = {
        "I": "인사말 및 개요",
        "E": "환경(E)",
        "S": "사회(S)",
        "G": "지배구조(G)",
    }

    for sec in completed:
        cat = sec.get("esg_category") or "I"
        if cat != cur_cat:
            parts.append(f"\n## {cat_labels.get(cat, cat)}\n")
            cur_cat = cat
        parts.append(f"### {sec['section_title']}\n{sec['draft']}\n")

    draft = "\n".join(parts)

    # 최종 LLM 검토 (어투 통일, 정합성)
    try:
        resp = await _llm_main.ainvoke([
            HumanMessage(content=_SYNTHESIZE_PROMPT.format(draft=draft))
        ])
        final = resp.content
    except Exception:
        final = draft  # LLM 실패 시 조립본 그대로 반환

    return {**state, "final_report": final}


# ──────────────────────────────────────────────
# 보고서 그래프 빌드
# ──────────────────────────────────────────────

def build_report_graph():
    g = StateGraph(ReportState)

    g.add_node("plan",            plan_report)
    g.add_node("process_section", process_section)
    g.add_node("synthesize",      synthesize_report)

    g.add_edge(START, "plan")
    # plan 완료 → 섹션별 Send 발행 (병렬 Map)
    g.add_conditional_edges("plan", fan_out_sections, ["process_section"])
    # 모든 process_section 완료 → synthesize (Reduce)
    g.add_edge("process_section", "synthesize")
    g.add_edge("synthesize", END)

    return g.compile()


REPORT_GRAPH = build_report_graph()


# ════════════════════════════════════════════════════════════
# Section Graph  (HITL 기반 단일 섹션 작성/수정 플로우)
#
#   START → node_retrieve → node_grade_docs → human_review_docs
#             ├─[반려: 새 키워드]→ node_retrieve로 복귀
#             └─[승인]→ node_generate_draft → human_review_draft
#                          ├─[수정 지시]→ node_generate_draft로 복귀
#                          ├─[추가 검색]→ node_retrieve로 복귀(filtered_docs 누적)
#                          └─[최종 승인]→ END
#
#   REPORT_GRAPH(plan→Map(Send)→Reduce)와는 별개의 그래프다. REPORT_GRAPH는
#   여러 섹션을 한 번에 병렬·결정적으로 생성하는 배치 작업이고, 이 그래프는
#   "섹션 하나"를 사람이 매 단계 승인/반려하며 다듬는 대화형 작업이라 문제의
#   모양이 다르다 — REPORT_GRAPH를 건드리지 않고 별도로 둔다.
#
#   인터럽트 설계 노트:
#   LangGraph의 interrupt()는 재개(resume) 시 "그 노드를 처음부터 다시 실행"한다.
#   따라서 node_grade_docs(LLM 평가 호출 포함) 안에 interrupt()를 직접 넣으면
#   재개될 때마다 평가 LLM 호출이 중복 실행된다. 이를 피하려고 human_review_docs/
#   human_review_draft를 별도의 "얇은" 노드로 분리했다 — 이 노드들은 interrupt()
#   호출 외에 비용이 드는 작업이 없으므로 재실행돼도 무해하다.
# ════════════════════════════════════════════════════════════

class SectionState(TypedDict):
    """단일 섹션 작성/수정 그래프의 상태.

    필수 명세 필드: user_query, retrieved_docs, filtered_docs, draft, messages.
    그 외 필드(target_year ~ retrieval_mode)는 HITL 분기와 검색 전략(전체교체 vs
    누적)을 구현하는 데 반드시 필요해 추가했다 — "어떤 결정이 내려졌는지",
    "이번 검색이 이전 결과를 대체하는지 보강하는지"를 상태 어딘가에 저장하지
    않으면 조건부 라우팅 자체가 불가능하기 때문이다.
    """
    # --- 명세 필드 ---
    user_query:     str
    retrieved_docs: List[Document]
    filtered_docs:  List[Document]
    draft:          str
    messages:       Annotated[List[BaseMessage], add_messages]

    # --- 입력/설정 (그래프 시작 시 한 번 채워짐) ---
    target_year:    str
    section_title:  str
    esg_category:   Optional[str]
    owner_user_id:  int   # thread_id를 알아낸 다른 사용자의 접근을 막기 위한 소유자 검증용

    # --- HITL 분기 / 검색 전략 제어용 ---
    next_action:    Optional[str]   # "approve" | "reject" | "edit" | "search"
    retrieval_mode: str             # "replace" | "append" — node_grade_docs의 병합 방식


# ──────────────────────────────────────────────
# 프롬프트
# ──────────────────────────────────────────────

_GRADE_PROMPT = """다음 검색 질의에 대해 각 문서가 실제로 답변 근거로 쓸만큼 관련 있는지 평가하라(Self-RAG 방식의 관련성 채점).

[검색 질의]
{query}

[문서 후보]
{docs_block}

각 문서에 대해 관련 있으면 true, 관련 없으면 false로 판단하라.
출력 형식: JSON 배열만. 다른 텍스트나 마크다운 없이.
[{{"index": 0, "relevant": true}}, {{"index": 1, "relevant": false}}]

다시 한번 언급한다. 위의 출력 형식과 같이 JSON 배열만 출력하라. 다른 텍스트나 마크다운 없이."""


# 기존 초안을 사용자 피드백에 맞춰 갱신할 때 쓰는 프롬프트.
# _DRAFT_PROMPT(최초 생성용)와 분리한 이유: 여기서는 "기존 초안 중 어디를
# 고칠지"를 사용자 피드백과 최근 대화 이력에서 직접 해석해야 한다. 이력 없이
# 현재 피드백 문장만 주면 "이 부분", "위 문단"같은 지시어를 모델이 풀 수 없다.
### "{year}년" 제거 ###
_SECTION_REVISE_PROMPT = """다음은 SK하이닉스 ESG 보고서 '{section}' 섹션의 기존 초안이다.
아래 사용자 피드백과 (있다면) 새로 추가된 참고 자료를 반영하여 초안을 갱신하라.

[기존 초안]
{draft}

[최근 대화 이력 — "이 부분", "위 문단" 같은 지시어 해석에 사용]
{history}

[사용자 피드백]
{feedback}

[참고 자료]
{context}

[작성 기준]
- 사용자 피드백이 가리키는 부분만 정확히 수정하고, 나머지 문단은 가능한 원문을 유지한다.
- 피드백이 특정 문단·표현을 가리키는 경우 기존 초안과 최근 대화 이력을 근거로 어디를 말하는지 판단한다.
- 전문적·사무적 어투를 유지한다.
- [참고 자료]에 제시된 수치·사실만 사용한다. 자료에 없는 수치는 추정하지 말고
  '(데이터 확인 필요)'로 표기한다. 수치·실적·구체적 사실을 담은 문장 끝에는
  근거를 [출처 N] 형식으로 단다(복수면 [출처 2, 5]). 기존 초안의 [출처] 표기는
  내용이 유지되는 한 함께 보존한다.
- 수정된 전체 섹션 본문만 출력하고, "수정했습니다" 같은 메타 발언은 출력하지 않는다.
{extra_guidance}"""


# ──────────────────────────────────────────────
# node_retrieve
# ──────────────────────────────────────────────

async def node_retrieve(state: SectionState) -> Dict[str, Any]:
    """user_query 기반 ChromaDB 검색.

    user_query는 최초 요청이거나, human_review_docs의 반려 피드백(새 키워드),
    또는 human_review_draft의 추가 검색 요청일 수 있다 — 호출 시점에 따라
    의미가 다르지만 "지금 검색해야 할 문자열"이라는 역할은 동일하다.

    section_title을 함께 붙이는 이유: 사용자 피드백이 "탄소중립 쪽 자료 추가해줘"
    처럼 짧을 때도 검색이 섹션 주제에서 벗어나지 않도록 앵커 역할을 한다.
    """
    search_query = f"{state.get('section_title', '')} {state['user_query']}".strip()

    docs = await retrieve(
        search_query,
        profile="report_gen",            # 재현율 우선 — 사람이 다음 단계에서 직접 검토하므로
        esg_category=state.get("esg_category"),
        target_collections=None,
        use_multi_query=False,
        final_k=20,                      # 컬렉션 4개 합(최대 32건)을 그대로 넘기지 않도록 컷
    )
    return {"retrieved_docs": docs}


# ──────────────────────────────────────────────
# node_grade_docs (Self-RAG)
# ──────────────────────────────────────────────

async def node_grade_docs(state: SectionState) -> Dict[str, Any]:
    """검색된 문서를 LLM으로 평가해 관련 없는 것을 제거한다.

    retrieval_mode에 따라 filtered_docs를 다루는 방식이 다르다:
    - "replace": 이번 결과로 완전히 교체 (HITL1에서 "다른 키워드로 다시 찾아줘"
      처럼 이전 검색이 통째로 틀렸다고 반려된 경우)
    - "append" : 기존 filtered_docs를 유지한 채 새로 통과한 문서만 추가
      (HITL2에서 "관련 데이터 더 찾아서 추가해줘"처럼 기존 초안의 근거는
      그대로 두고 보강하는 경우)
    """
    candidates = state.get("retrieved_docs", [])

    if not candidates:
        relevant_new: List[Document] = []
    else:
        docs_block = "\n".join(
            f"[{i}] {d.page_content[:450]}" for i, d in enumerate(candidates)
        )
        try:
            resp = await _llm_fast.ainvoke([
                HumanMessage(content=_GRADE_PROMPT.format(
                    query=state["user_query"], docs_block=docs_block,
                ))
            ])
            raw = resp.content.strip()
            start, end = raw.find("["), raw.rfind("]") + 1
            grades = json.loads(raw[start:end])
            relevant_idx = {g["index"] for g in grades if g.get("relevant")}
            relevant_new = [d for i, d in enumerate(candidates) if i in relevant_idx]
        except Exception:
            # 평가 실패 시 전부 통과시킨다(재현율 우선 폴백) — 사람이 다음
            # HITL 단계에서 직접 보고 걸러낼 수 있으므로 여기서 과도하게
            # 버리는 것보다 안전하다.
            relevant_new = candidates

    mode = state.get("retrieval_mode", "replace")
    if mode == "append":
        existing = state.get("filtered_docs", [])
        seen = {
            d.metadata.get("chunk_id") or hash(d.page_content) #[:80]
            for d in existing
        }
        merged = list(existing)
        for d in relevant_new:
            key = d.metadata.get("chunk_id") or hash(d.page_content) #[:80]
            if key not in seen:
                seen.add(key)
                merged.append(d)
        filtered = merged
    else:
        filtered = relevant_new

    return {"filtered_docs": filtered}


# ──────────────────────────────────────────────
# HITL stage 식별자
#
# section_service.py의 액션 검증 로직(VALID_ACTIONS)이 이 상수를 그대로
# import해서 쓴다. 여기서 stage 이름을 바꾸면 검증 쪽도 함께 따라가므로,
# 두 파일에 같은 문자열이 따로따로 박혀 있다가 어긋나는 일을 막는다.
# ──────────────────────────────────────────────

STAGE_REVIEW_DOCS = "review_docs"
STAGE_REVIEW_DRAFT = "review_draft"


# ──────────────────────────────────────────────
# human_review_docs  [HITL 1]
# ──────────────────────────────────────────────

async def human_review_docs(state: SectionState) -> Dict[str, Any]:
    """검색 문서 검토용 인터럽트.

    재개 시 받는 입력(Command(resume=...)의 값) 형식:
      {"action": "approve"}
      {"action": "reject", "content": "<새 검색 키워드>"}
    """
    decision = interrupt({
        "stage":    STAGE_REVIEW_DOCS,
        "sources":  docs_to_sources(state.get("filtered_docs", [])),
        "message":  ("검색된 문서를 검토하라. 승인하려면 action='approve', "
                     "다른 키워드로 재검색하려면 action='reject'와 함께 "
                     "content에 새 검색어를 보내라."),
    }) or {}

    action = decision.get("action", "approve")

    if action == "reject":
        new_query = decision.get("content") or state["user_query"]
        return {
            "next_action":    "reject",
            "user_query":     new_query,
            "retrieval_mode": "replace",
            "messages":       [HumanMessage(content=f"[문서 반려, 재검색 요청] {new_query}")],
        }

    return {
        "next_action": "approve",
        "messages":    [HumanMessage(content="[검색 문서 승인]")],
    }


def route_after_doc_review(state: SectionState) -> str:
    return "node_retrieve" if state.get("next_action") == "reject" else "node_generate_draft"


# ──────────────────────────────────────────────
# node_generate_draft
# ──────────────────────────────────────────────

async def node_generate_draft(state: SectionState) -> Dict[str, Any]:
    """filtered_docs로 초안을 새로 쓰거나, 기존 초안을 피드백에 맞춰 재작성한다.

    messages(최근 대화 이력)를 재작성 프롬프트에 함께 넣는 이유: "이 부분",
    "위에서 언급한 내용"처럼 직전 피드백을 참조하는 지시어는 현재 피드백
    문장 하나만으로는 해석할 수 없다.
    """
    context = docs_to_context(state.get("filtered_docs", []))
    extra_guidance = (
        _I_SECTION_GUIDANCE if state.get("esg_category") == "I" else ""
    )

    if not state.get("draft"):
        prompt = _DRAFT_PROMPT.format(
            year=state.get("target_year", "2025"),
            section=state["section_title"],
            context=context,
            extra_guidance=extra_guidance,
        )
    else:
        history_lines = "\n".join(
            f"{m.type}: {str(m.content)[:200]}" for m in state.get("messages", [])[-6:]
        )
        prompt = _SECTION_REVISE_PROMPT.format(
            year=state.get("target_year", "2025"),
            section=state["section_title"],
            draft=state["draft"],
            history=history_lines or "(없음)",
            feedback=state.get("user_query", ""),
            context=context,
            extra_guidance=extra_guidance,
        )

    try:
        resp = await _llm_main.ainvoke([HumanMessage(content=prompt)])
        new_draft = resp.content
    except Exception as e:
        new_draft = state.get("draft") or f"[초안 생성 실패: {e}]"

    return {
        "draft":    new_draft,
        # 초안 전문을 messages에 또 쌓지 않는다 — draft 필드가 이미 최신 전문을
        # 보관하므로, messages는 "무엇을 요청했고 무엇이 승인됐는지"라는
        # 짧은 이력만 추적해 이후 프롬프트에 들어갈 토큰을 줄인다.
        "messages": [AIMessage(content="[초안 갱신됨]")],
    }


# ──────────────────────────────────────────────
# human_review_draft  [HITL 2]
# ──────────────────────────────────────────────

async def human_review_draft(state: SectionState) -> Dict[str, Any]:
    """초안 검토용 인터럽트.

    재개 시 받는 입력 형식:
      {"action": "approve"}
      {"action": "edit",   "content": "<텍스트 수정 지시>"}
      {"action": "search", "content": "<추가로 찾을 검색어>"}
    """
    decision = interrupt({
        "stage":    STAGE_REVIEW_DRAFT,
        "draft":    state.get("draft", ""),
        "sources":  docs_to_sources(state.get("filtered_docs", [])),
        "message":  ("초안을 검토하라. 승인하려면 action='approve', 텍스트 수정을 "
                     "요청하려면 action='edit'와 content에 수정 지시, 추가 자료를 "
                     "찾으려면 action='search'와 content에 검색어를 보내라."),
    }) or {}

    action  = decision.get("action", "approve")
    content = decision.get("content", "")

    if action == "edit":
        return {
            "next_action": "edit",
            "user_query":  content,
            "messages":    [HumanMessage(content=f"[수정 요청] {content}")],
        }

    if action == "search":
        return {
            "next_action":    "search",
            "user_query":     content,
            "retrieval_mode": "append",
            "messages":       [HumanMessage(content=f"[추가 검색 요청] {content}")],
        }

    return {
        "next_action": "approve",
        "messages":    [HumanMessage(content="[최종 승인]")],
    }


def route_after_draft_review(state: SectionState) -> str:
    action = state.get("next_action")
    if action == "edit":
        return "node_generate_draft"
    if action == "search":
        return "node_retrieve"
    return END


# ──────────────────────────────────────────────
# 섹션 그래프 빌드
#
# QA_GRAPH/REPORT_GRAPH와 달리 모듈 import 시점에 compile()하지 않는다.
# interrupt를 쓰려면 컴파일 시점에 checkpointer가 필요한데, AsyncSqliteSaver는
# 비동기 컨텍스트 매니저로 열어야 하므로 import 시점(동기)에는 만들 수 없다.
# 따라서 checkpointer는 FastAPI 앱 lifespan에서 열고, 그때 이 함수를 호출해
# 컴파일된 그래프를 한 번만 만들어 재사용한다 (section_api.py 참조).
# ──────────────────────────────────────────────

def build_section_graph(checkpointer):
    g = StateGraph(SectionState)

    g.add_node("node_retrieve",       node_retrieve)
    g.add_node("node_grade_docs",     node_grade_docs)
    g.add_node("human_review_docs",   human_review_docs)
    g.add_node("node_generate_draft", node_generate_draft)
    g.add_node("human_review_draft",  human_review_draft)

    g.add_edge(START, "node_retrieve")
    g.add_edge("node_retrieve", "node_grade_docs")
    g.add_edge("node_grade_docs", "human_review_docs")

    g.add_conditional_edges(
        "human_review_docs",
        route_after_doc_review,
        {"node_retrieve": "node_retrieve", "node_generate_draft": "node_generate_draft"},
    )

    g.add_edge("node_generate_draft", "human_review_draft")

    g.add_conditional_edges(
        "human_review_draft",
        route_after_draft_review,
        {
            "node_retrieve":       "node_retrieve",
            "node_generate_draft": "node_generate_draft",
            END: END,
        },
    )

    return g.compile(checkpointer=checkpointer)