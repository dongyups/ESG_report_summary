# 전체 흐름 조합 (retrieve → context 구성 → LLM(extended thinking) → SSE 스트리밍)을 담당하는 비즈니스 로직 파일
import asyncio
import json
from typing import List, Dict, AsyncGenerator
from anthropic import AsyncAnthropic
# local
from app.core.config import settings
from app.modules.rag.retrieval import retrieve


# 클로드
_client = AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
_SYSTEM = """당신은 SK하이닉스의 ESG 및 기업 정보 전문 어시스턴트입니다.
제공된 검색 컨텍스트를 기반으로 사용자 질문에 정확하고 간결하게 답변하세요.
컨텍스트에 근거가 없는 내용은 반드시 그 사실을 명시하세요.
사무적이고 정중한 어투를 유지하세요."""


# ──────────────────────────────────────────────
# 내부 헬퍼
# ──────────────────────────────────────────────
def _build_context(docs: List[Dict]) -> str:
    if not docs:
        return "관련 문서를 찾을 수 없습니다."

    parts = []
    for i, doc in enumerate(docs, 1):
        meta = doc["metadata"]
        src  = meta.get("source_db", "")
        if src == "sk_hynix_press":
            hdr = f"[출처 {i}] 보도자료 | 날짜: {meta.get('date','')} | 해시태그: {meta.get('hashtag','')} | 제목: {meta.get('title','')}"
        elif src == "sk_hynix_newsroom":
            hdr = f"[출처 {i}] 뉴스룸 | 날짜: {meta.get('date','')} | 해시태그: {meta.get('hashtag','')} | 제목: {meta.get('title','')}"
        elif src == "sk_hynix_report":
            hdr = (f"[출처 {i}] ESG보고서 | p.{meta.get('page_num','')} c.{meta.get('chunk_index','')} | 섹션: {meta.get('section','')} — "
                   f"항목: {meta.get('heading_level_1','')} > {meta.get('heading_level_2','')} > {meta.get('heading_level_3','')} | {meta.get('table_title','')}")
        elif src == "sk_hynix_esg_data":
            hdr = f"[출처 {i}] ESG데이터 | {doc['document']}"
        else:
            hdr = f"[출처 {i}]"

        ### 수치 데이터만 순서를 바꿔서 아래와 같이 변형 ###
        if src != "sk_hynix_esg_data":
            parts.append(f"{hdr}\n{doc['document']}")
        else:
            parts.append(
                f"{hdr}\n"
                f"2019년: {meta.get('value_2019','')}\n"
                f"2020년: {meta.get('value_2020','')}\n"
                f"2021년: {meta.get('value_2021','')}\n"
                f"2022년: {meta.get('value_2022','')}\n"
                f"2023년: {meta.get('value_2023','')}\n"
                f"2024년: {meta.get('value_2024','')}"
            )
    return "\n\n---\n\n".join(parts)


def _format_sources(docs: List[Dict]) -> List[Dict]:
    out = []
    for doc in docs:
        meta = doc["metadata"]
        src  = meta.get("source_db", "")
        base = {"distance": round(doc["distance"], 4)}

        if src == "sk_hynix_press":
            out.append({**base, "type": "보도자료",
                        "date":  meta.get("date", ""),
                        "title": meta.get("title", ""),
                        "url":   meta.get("url", "")})
        elif src == "sk_hynix_newsroom":
            out.append({**base, "type": "뉴스룸",
                        "date":  meta.get("date", ""),
                        "title": meta.get("title", ""),
                        "url":   meta.get("url", "")})
        elif src == "sk_hynix_report":
            out.append({**base, "type": "ESG보고서",
                        "esg_category":    meta.get("esg_category", ""),
                        "company":         meta.get("company", ""),
                        "report_year":     meta.get("report_year", ""),
                        "section":         meta.get("section", ""),
                        "heading_level_1": meta.get("heading_level_1", ""),
                        "heading_level_2": meta.get("heading_level_2", ""),
                        "heading_level_3": meta.get("heading_level_3", ""),
                        "table_title":     meta.get("table_title", ""),
                        "page_num":        meta.get("page_num", ""),
                        "chunk_index":     meta.get("chunk_index", "")})
        elif src == "sk_hynix_esg_data":
            out.append({**base, "type": "ESG데이터",
                        "esg_category":    meta.get("esg_category", ""),
                        "company":         meta.get("company", ""),
                        "site":            meta.get("site", ""),
                        "category_level_1": meta.get("category_level_1", ""),
                        "category_level_2": meta.get("category_level_2", ""),
                        "category_level_3": meta.get("category_level_3", ""),
                        "category_level_4": meta.get("category_level_4", ""),
                        "unit":            meta.get("unit", "")})
    return out


# ──────────────────────────────────────────────
# 메인 제너레이터
# ──────────────────────────────────────────────
def _sse(obj: dict) -> str:
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"

async def generate_rag_response(
    query: str,
    history: List[Dict],
) -> AsyncGenerator[str, None]:
    """
    SSE 이벤트 타입:
      sources        — 출처 목록 (LLM 호출 전 즉시)
      thinking_start — thinking 시작 신호 (로딩 표시용)
      thinking       — 전체 thinking 텍스트 (LLM 완료 후)
      text           — 응답 텍스트 청크 (fake-stream)
      done           — 완료
      error          — 오류
    """
    # 1. 검색
    docs    = await retrieve(query, n_per_collection=3)
    sources = _format_sources(docs)
    context = _build_context(docs)

    yield _sse({"type": "sources", "sources": sources})
    yield _sse({"type": "thinking_start"})

    # 2. 메시지 구성 (최근 2쌍 히스토리 + 현재 질문)
    user_msg = f"[검색된 컨텍스트]\n{context}\n\n[사용자 질문]\n{query}"
    messages = list(history[-4:]) + [{"role": "user", "content": user_msg}]

    # 3. Extended thinking 시도 → 실패 시 일반 스트리밍 폴백
    try:
        response = await _client.messages.create(
            model=settings.RAG_LLM_MODEL,
            max_tokens=16000,
            thinking={"type": "enabled", "budget_tokens": 8000},
            system=_SYSTEM,
            messages=messages,
        )

        # thinking 블록 전송
        for block in response.content:
            if block.type == "thinking":
                yield _sse({"type": "thinking", "content": block.thinking})

        # text 블록 fake-stream
        for block in response.content:
            if block.type == "text":
                text = block.text
                for i in range(0, len(text), 15):
                    yield _sse({"type": "text", "chunk": text[i : i + 15]})
                    await asyncio.sleep(0.005)

        yield _sse({"type": "done"})

    except Exception as primary_err:
        # extended thinking 미지원 모델 등 → 일반 스트리밍 폴백
        try:
            async with _client.messages.stream(
                model=settings.RAG_LLM_MODEL,
                max_tokens=8096,
                system=_SYSTEM,
                messages=messages,
            ) as stream:
                async for chunk in stream.text_stream:
                    yield _sse({"type": "text", "chunk": chunk})

            yield _sse({"type": "done"})

        except Exception as fallback_err:
            yield _sse({"type": "error", "message": str(fallback_err)})
