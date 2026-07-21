# 단일 섹션 작성/수정(HITL) API의 서비스 레이어
#
# section_api.py(라우팅)에서 다음 책임들을 분리해 모아둔다:
#   - AsyncSqliteSaver 체크포인터의 lifespan 관리 + section_graph 빌드
#   - thread_id ↔ config 변환, 그래프 조회, 소유자 검증 같은 공통 헬퍼
#   - HITL 단계(stage)별로 허용되는 action 검증 규칙
#
# stage 식별자(STAGE_REVIEW_DOCS/STAGE_REVIEW_DRAFT)는 graph.py에서 그대로
# import한다 — graph.py의 interrupt() 호출이 보내는 stage 값과 여기서 검증에
# 쓰는 값이 같은 상수를 참조하므로, 둘 중 하나만 바뀌어 어긋나는 일이 없다.

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, Dict

from fastapi import HTTPException, Request
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from app.core.config import settings
from app.db.models.user import User
from app.modules.rag.graph import STAGE_REVIEW_DOCS, STAGE_REVIEW_DRAFT, build_section_graph
from app.modules.rag.retriever import docs_to_sources
from app.schemas.section import SectionStatusResponse

# 각 HITL 단계에서 허용되는 action 값.
# feedback 엔드포인트가 그래프를 재개(resume)하기 전에, 지금 멈춰있는 단계와
# 사용자가 보낸 action이 맞는 조합인지 여기서 먼저 걸러낸다.
VALID_ACTIONS = {
    STAGE_REVIEW_DOCS:  {"approve", "reject"},
    STAGE_REVIEW_DRAFT: {"approve", "edit", "search"},
}


@asynccontextmanager
async def section_lifespan(app):
    """section_api.router의 lifespan으로 등록한다.

    AsyncSqliteSaver는 비동기 컨텍스트 매니저로 열어야 하므로, 매 요청마다
    열고 닫는 대신 앱 구동 시 한 번만 열고 끝날 때 닫는다.
    """
    async with AsyncSqliteSaver.from_conn_string(settings.SECTION_CHECKPOINT_PATH) as saver:
        await saver.setup()  # 체크포인트 테이블이 없으면 생성. 이미 있으면 안전하게 no-op.
        section_graph = build_section_graph(saver)
        # with open("./assets/GRAPH_SECTION.png", "wb") as f:
        #     f.write(section_graph.get_graph().draw_mermaid_png())
        yield {"section_graph": section_graph}


def build_config(thread_id: str) -> Dict[str, Any]:
    # recursion_limit을 넉넉히 둔다 — 사용자가 검색/수정 루프를 여러 차례
    # 반복할 수 있는 워크플로우라 기본값(LangGraph 기본 25)으로는 부족할 수 있다.
    return {"configurable": {"thread_id": thread_id}, "recursion_limit": 50}


async def get_graph(request: Request):
    graph = getattr(request.state, "section_graph", None)
    if graph is None:
        raise HTTPException(status_code=500, detail="섹션 그래프가 초기화되지 않았다.")
    return graph


async def load_owned_snapshot(graph, thread_id: str, user: User):
    """thread_id의 현재 상태를 가져오고 소유자를 검증한다.

    존재하지 않는 thread_id는 values={}로 돌아온다(LangGraph 동작) — 이 경우와
    "다른 사용자의 thread_id"를 구분하지 않고 동일하게 404를 반환해, thread_id
    존재 여부 자체가 추측되지 않도록 한다.
    """
    snapshot = await graph.aget_state(build_config(thread_id))
    if not snapshot.values or snapshot.values.get("owner_user_id") != user.id:
        raise HTTPException(status_code=404, detail="섹션 작업을 찾을 수 없다.")
    return snapshot


def response_from_snapshot(thread_id: str, snapshot) -> SectionStatusResponse:
    if snapshot.interrupts:
        payload = snapshot.interrupts[0].value
        return SectionStatusResponse(
            thread_id=thread_id,
            stage=payload.get("stage"),
            message=payload.get("message"),
            draft=payload.get("draft"),
            sources=payload.get("sources"),
        )

    # next가 비어있으면 END까지 도달한 것 — 완료 상태로 응답한다.
    values = snapshot.values
    return SectionStatusResponse(
        thread_id=thread_id,
        stage="done",
        message="작성이 완료되었다.",
        draft=values.get("draft"),
        sources=docs_to_sources(values.get("filtered_docs", [])),
    )