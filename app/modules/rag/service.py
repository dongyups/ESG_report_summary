# RAG 서비스 모듈 (업데이트)
#
# 기존 service.py와의 차이:
#   - generate_rag_response(): 직접 retrieve() 호출 → QA_GRAPH.ainvoke() 사용
#     QA_GRAPH가 쿼리 분석 + 전략별 검색을 수행하므로 서비스는 생성에만 집중.
#   - generate_esg_report(): 신규 추가.
#     REPORT_GRAPH.astream_events()로 섹션 단위 진행 상황을 SSE로 스트리밍.
#
# SSE 이벤트 타입 (기존 유지):
#   sources, thinking_start, thinking, text, done, error
# 보고서 생성 추가 SSE 이벤트:
#   plan, section_start, section_done, synthesis

from __future__ import annotations

import asyncio
import json
from typing import Any, AsyncGenerator, Dict, List

# from anthropic import AsyncAnthropic
from anthropic import AsyncAnthropicBedrock
#local
from app.core.config import settings
from app.modules.rag.graph import QA_GRAPH, REPORT_GRAPH, QAState
from app.modules.rag.retriever import docs_to_context, docs_to_sources


# _client = AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
_client  = AsyncAnthropicBedrock(aws_region=settings.AWS_REGION)

_QA_SYSTEM = """당신은 SK하이닉스의 ESG 및 기업 정보 전문 어시스턴트입니다.
제공된 검색 컨텍스트를 기반으로 사용자 질문에 정확하고 간결하게 답변하세요.
컨텍스트에 근거가 없는 내용은 반드시 그 사실을 명시하세요.
사무적이고 정중한 어투를 유지하세요."""

# report_section 의도일 때 사용하는 별도 시스템 프롬프트.
# _QA_SYSTEM과 분리한 이유: 일반 Q&A 프레이밍("컨텍스트에 근거 없으면 명시")만
# 주어지면, 검색 결과가 모두 작년/올해 자료뿐이고 "내년도" 섹션을 요청받았을 때
# 모델이 "미래 문서를 대신 작성하는 것"이라 판단해 작성을 회피하는 경향이 있다.
# 이는 매년 반복되는 정상적인 보고서 개정 작업이라는 맥락을 명시해 해결한다.
_DRAFT_SYSTEM = """당신은 SK하이닉스의 ESG 보고서 작성을 보조하는 내부 초안 작성 어시스턴트입니다.

작업 맥락:
- ESG 보고서는 매년 갱신되는 정기 발행물입니다. 작성 방식은 전년도 보고서의
  구조·문체를 토대로, 올해 누적된 뉴스·보도자료·실적 데이터를 반영해 다음
  보고서의 초안을 만드는 것입니다. 이는 모든 기업이 매년 반복하는 표준
  업무이며, "미래를 예측"하거나 "존재하지 않는 문서를 만드는" 것이 아닙니다.
- 검색 컨텍스트에 올해 날짜의 자료가 없더라도, 작년 자료 + 올해 데이터를
  근거로 갱신된 초안을 작성하는 것이 정상적인 작업 범위입니다.
- 결과물은 사내 검토·승인을 거치는 내부 초안입니다. 최종 발행 전 실제
  담당자(IR/지속경영팀, 경영진)가 검토·수정·승인합니다.

CEO/대표이사 메시지, 주주 서신처럼 특정 인물에게 귀속되는 섹션을 작성할 때:
- 실제로 그 인물이 이미 발언했다고 단정하는 표현은 피하고, 검토·승인을
  거칠 초안임을 전제로 작성하세요.
- 전년도 메시지의 구조·어조를 참고해 올해 성과·데이터를 반영한 내용으로
  갱신하되, 사실에 근거하지 않은 구체적 일화나 개인적 발언은 만들지 마세요.

컨텍스트에 특정 수치나 사실이 없으면 그 사실을 명시하고, 가능한 범위에서
구조와 흐름을 갖춘 완성된 초안을 제공하세요. 사무적이고 전문적인 어투를
유지하세요."""


def _sse(obj: dict) -> str:
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"


# ──────────────────────────────────────────────
# Q&A 챗봇 (기존 기능, 내부 검색 개선)
# ──────────────────────────────────────────────

async def generate_rag_response(
    query:   str,
    history: List[Dict],
) -> AsyncGenerator[str, None]:
    """
    QA 챗봇 SSE 스트림.

    변경 사항:
    - 기존: retrieve(query, n_per_collection=3) → 모든 컬렉션 동일 전략
    - 신규: QA_GRAPH.ainvoke() → 쿼리 분석 후 의도별 profile + 컬렉션별
            차등 n/threshold(retriever.COLLECTION_PROFILES)로 검색

    SSE 이벤트:
      sources        — 출처 목록
      thinking_start — thinking 시작 신호 (프론트엔드 로딩 표시)
      thinking       — 전체 thinking 블록
      text           — 응답 텍스트 청크 (fake-stream)
      done           — 완료
      error          — 오류
    """
    # 1. LangGraph로 쿼리 분석 + 최적화 검색
    try:
        result = await QA_GRAPH.ainvoke(
            QAState(
                query=query,
                history=history,
                query_intent="qa",    # analyze_query 노드에서 덮어씀
                esg_category=None,
                use_multi_query=False,
                docs=[],
            )
        )
    except Exception as e:
        yield _sse({"type": "error", "message": f"검색 실패: {e}"})
        return

    docs         = result.get("docs", [])
    query_intent = result.get("query_intent", "qa")  # 기존엔 추출만 하고 버려졌음
    context      = docs_to_context(docs)
    sources      = docs_to_sources(docs)

    yield _sse({"type": "sources", "sources": sources})
    yield _sse({"type": "thinking_start"})

    # 2. 최근 히스토리 + 현재 쿼리로 메시지 구성
    user_msg = f"[검색된 컨텍스트]\n{context}\n\n[사용자 질문]\n{query}"
    messages = list(history[-4:]) + [{"role": "user", "content": user_msg}]

    # report_section(섹션 초안 요청)이면 _DRAFT_SYSTEM 사용 — "미래 문서"라는
    # 이유로 작성을 회피하지 않도록 보고서 개정 작업의 정상 맥락을 부여한다.
    system_prompt = _DRAFT_SYSTEM if query_intent == "report_section" else _QA_SYSTEM

    # # 3. Extended thinking 시도 → 일반 스트리밍 폴백 (기존 로직 유지)
    # try:
    #     response = await _client.messages.create(
    #         model=settings.RAG_LLM_MODEL,
    #         max_tokens=16000,
    #         thinking={"type": "enabled", "budget_tokens": 8000},
    #         system=system_prompt,
    #         messages=messages,
    #     )

    #     for block in response.content:
    #         if block.type == "thinking":
    #             yield _sse({"type": "thinking", "content": block.thinking})

    #     for block in response.content:
    #         if block.type == "text":
    #             text = block.text
    #             for i in range(0, len(text), 15):
    #                 yield _sse({"type": "text", "chunk": text[i:i + 15]})
    #                 await asyncio.sleep(0.005)

    #     yield _sse({"type": "done"})

    # except Exception:
    # 3. 진짜 스트리밍(extended thinking) → 일반 스트리밍 폴백
    try:
        async with _client.messages.stream(
            model=settings.RAG_LLM_MODEL,
            max_tokens=16000,
            thinking={"type": "enabled", "budget_tokens": 8000},
            system=system_prompt,
            messages=messages,
        ) as stream:
            thinking_buf = ""
            thinking_flushed = False
            async for event in stream:
                if event.type != "content_block_delta":
                    continue
                delta = event.delta
                if delta.type == "thinking_delta":
                    thinking_buf += delta.thinking
                elif delta.type == "text_delta":
                    # thinking 블록은 항상 text보다 먼저 끝나므로,
                    # 첫 text delta 직전에 thinking을 한 번만 방출한다.
                    if not thinking_flushed:
                        if thinking_buf:
                            yield _sse({"type": "thinking", "content": thinking_buf})
                        thinking_flushed = True
                    yield _sse({"type": "text", "chunk": delta.text})
            # text 없이 끝나는 예외적 경우 대비
            if not thinking_flushed and thinking_buf:
                yield _sse({"type": "thinking", "content": thinking_buf})

        yield _sse({"type": "done"})

    except Exception:
        try:
            async with _client.messages.stream(
                model=settings.RAG_LLM_MODEL,
                max_tokens=8096,
                system=system_prompt,
                messages=messages,
            ) as stream:
                async for chunk in stream.text_stream:
                    yield _sse({"type": "text", "chunk": chunk})
            yield _sse({"type": "done"})
        except Exception as fallback_err:
            yield _sse({"type": "error", "message": str(fallback_err)})


# ──────────────────────────────────────────────
# ESG 보고서 자동 생성 (신규 기능)
# ──────────────────────────────────────────────

async def generate_esg_report(
    target_year: str,
    scope:       str = "전체",
) -> AsyncGenerator[str, None]:
    """
    ESG 보고서 자동 작성 SSE 스트림.

    REPORT_GRAPH.astream_events()로 섹션 단위 진행 상황을 실시간 스트리밍.
    astream_events: 그래프 각 노드의 시작/종료 시점에 이벤트를 발행하므로
    섹션별 진행률을 프론트엔드에 표시할 수 있음.

    SSE 이벤트:
      plan          — 생성할 섹션 목록 [{title, category}, ...]
      section_start — 섹션 처리 시작 {section_title, esg_category}
      section_done  — 섹션 초안 완료 {section_title, esg_category, draft, sources}
      synthesis     — 최종 합성 시작 신호
      text          — 최종 보고서 텍스트 청크 (fake-stream)
      done          — 완료
      error         — 오류

    호출 예시 (api.py에서):
      return StreamingResponse(
          generate_esg_report("2025", "전체"),
          media_type="text/event-stream"
      )
    """
    initial_state: Dict[str, Any] = {
        "target_year":        target_year,
        "scope":              scope,
        "sections":           [],
        "completed_sections": [],
        "final_report":       "",
    }

    try:
        async for event in REPORT_GRAPH.astream_events(initial_state, version="v2"):
            kind = event.get("event", "")
            name = event.get("name",  "")
            data = event.get("data",  {})

            # plan 완료: 섹션 목록 전달
            if kind == "on_chain_end" and name == "plan":
                sections = data.get("output", {}).get("sections", [])
                yield _sse({
                    "type": "plan",
                    "sections": [
                        {"title": s["section_title"], "category": s.get("esg_category", "")}
                        for s in sections
                    ],
                })

            # 섹션 처리 시작
            elif kind == "on_chain_start" and name == "process_section":
                inp = data.get("input", {})
                yield _sse({
                    "type":          "section_start",
                    "section_title": inp.get("section_title", ""),
                    "esg_category":  inp.get("esg_category", ""),
                })

            # 섹션 초안 완료
            elif kind == "on_chain_end" and name == "process_section":
                completed = data.get("output", {}).get("completed_sections", [])
                if completed:
                    sec = completed[0]
                    yield _sse({
                        "type":          "section_done",
                        "section_title": sec.get("section_title", ""),
                        "esg_category":  sec.get("esg_category", ""),
                        "draft":         sec.get("draft", ""),
                        "sources":       docs_to_sources(sec.get("docs", [])),
                    })

            # 합성 시작 신호
            elif kind == "on_chain_start" and name == "synthesize":
                yield _sse({"type": "synthesis"})

            # 최종 보고서 완성 → fake-stream
            elif kind == "on_chain_end" and name == "synthesize":
                final = data.get("output", {}).get("final_report", "")
                for i in range(0, len(final), 20):
                    yield _sse({"type": "text", "chunk": final[i:i + 20]})
                    await asyncio.sleep(0.003)
                yield _sse({"type": "done"})

    except Exception as e:
        yield _sse({"type": "error", "message": str(e)})