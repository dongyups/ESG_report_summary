# 단일 섹션 작성/수정 API (HITL 기반)
#
# 기존 api.py(채팅형 Q&A, 보고서 일괄 생성)와는 별도 라우터로 분리했다.
# 이 기능은 LangGraph의 interrupt()/AsyncSqliteSaver로 "그래프 실행을 멈췄다가
# 사람의 입력을 받아 재개"하는 완전히 다른 실행 모델을 쓰기 때문에, 기존
# SSE 스트리밍 엔드포인트들과 같은 파일에 두면 관심사가 섞인다.
#
# 이 파일은 라우팅만 담당한다. 스키마는 schemas/section.py, 체크포인터
# lifespan·그래프 헬퍼·action 검증 규칙은 section_service.py에 있다
# (modules/rag/api.py가 schemas/rag.py·service.py를 쓰는 것과 동일한 분리).
#
# 엔드포인트 설계 (요구사항의 "엔드포인트 분리 또는 상태 분기 처리"에 대한 결정):
#   - URL은 start / feedback / status 3개로 분리한다 (REST 관례상 자연스러움).
#   - 다만 "문서 반려" vs "초안 수정" vs "추가 검색"을 별도 URL로 또 나누지는
#     않았다 — LangGraph의 재개 메커니즘(Command(resume=...))은 현재 멈춰있는
#     인터럽트가 HITL1인지 HITL2인지와 무관하게 동일하게 동작하므로, URL을
#     더 쪼개는 건 실질적 이득 없이 코드만 중복시킨다. 대신 feedback 엔드포인트
#     내부에서 "현재 단계(stage)"를 조회해 action이 그 단계에서 허용되는
#     값인지 검증하는 상태 분기 처리를 둔다 (section_service.VALID_ACTIONS).
#
# 통합 방법: 이 라우터를 기존 main 앱에 등록만 하면 된다.
#   from app.modules.rag import section_api
#   app.include_router(section_api.router, prefix="/rag")   # 기존 rag.router와 동일한 방식
#
# 체크포인터 수명주기: AsyncSqliteSaver는 비동기 컨텍스트 매니저로 열어야 하므로,
# 매 요청마다 열고 닫는 대신 이 라우터의 lifespan(section_service.section_lifespan)
# 에서 앱 구동 시 한 번만 열고 끝날 때 닫는다. FastAPI는 APIRouter(lifespan=...)로
# 등록된 서브 라우터의 lifespan을 상위 앱 lifespan에 합쳐서 실행해주므로, 메인 앱
# 파일을 따로 수정하지 않아도 된다(이 부분이 동작하려면 메인 앱도 lifespan
# 스타일이어야 한다 — 구버전 @app.on_event 방식이면 메인 앱에 별도 통합이 필요하다).

from __future__ import annotations

from typing import Any, Dict
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request
from langgraph.types import Command
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.database import get_db
from app.db.models.user import User
from app.db.crud import section as section_crud       # ← 추가
from app.modules.auth.dependency import get_current_user
from app.modules.rag import section_service as svc
from app.schemas.section import (
    SectionDraftListResponse,                          # ← 추가
    SectionDraftResponse,                              # ← 추가
    SectionFeedbackRequest,
    SectionStartRequest,
    SectionStatusResponse,
)

router = APIRouter(prefix="/sections", lifespan=svc.section_lifespan)


# ──────────────────────────────────────────────────────────────────────
# 완성 초안 CRUD
# 주의: GET /{thread_id} 보다 반드시 먼저 정의해야 한다.
# 순서가 뒤바뀌면 FastAPI가 /drafts 를 thread_id="drafts" 로 매칭해 404 발생.
# ──────────────────────────────────────────────────────────────────────

@router.get("/drafts", response_model=SectionDraftListResponse)
async def list_drafts(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """MySQL에 저장된 완성 초안 목록을 반환한다."""
    drafts = await section_crud.get_drafts(db, user_id=user.id)
    return SectionDraftListResponse(
        drafts=[SectionDraftResponse.model_validate(d) for d in drafts],
        total=len(drafts),
    )


@router.delete("/drafts/{draft_id}", status_code=204)
async def delete_draft(
    draft_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """완성 초안을 삭제한다."""
    ok = await section_crud.delete_draft(db, draft_id=draft_id, user_id=user.id)
    if not ok:
        raise HTTPException(status_code=404, detail="초안을 찾을 수 없다.")


# ──────────────────────────────────────────────────────────────────────
# HITL 그래프 엔드포인트
# ──────────────────────────────────────────────────────────────────────

@router.post("", response_model=SectionStatusResponse)
async def start_section(
    body: SectionStartRequest,
    request: Request,
    user: User = Depends(get_current_user),
):
    """새 섹션 작업 스레드를 시작하고, 첫 검색·평가를 거쳐 문서 검토(HITL1) 단계까지 실행한다."""
    graph = await svc.get_graph(request)
    thread_id = str(uuid4())

    initial_state: Dict[str, Any] = {
        "user_query":     body.user_query,
        "retrieved_docs": [],
        "filtered_docs":  [],
        "draft":          "",
        "messages":       [],
        "target_year":    body.target_year,
        "section_title":  body.section_title,
        "esg_category":   body.esg_category,
        "owner_user_id":  user.id,
        "next_action":    None,
        "retrieval_mode": "replace",
    }

    await graph.ainvoke(initial_state, config=svc.build_config(thread_id))
    snapshot = await graph.aget_state(svc.build_config(thread_id))
    return svc.response_from_snapshot(thread_id, snapshot)


@router.post("/{thread_id}/feedback", response_model=SectionStatusResponse)
async def section_feedback(
    thread_id: str,
    body: SectionFeedbackRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),              # ← 추가
    user: User = Depends(get_current_user),
):
    """대기 중인 HITL 단계에 사용자 피드백을 전달해 그래프를 재개한다."""
    graph = await svc.get_graph(request)
    snapshot = await svc.load_owned_snapshot(graph, thread_id, user)

    if not snapshot.interrupts:
        raise HTTPException(status_code=409, detail="이미 완료되어 더 이상 피드백을 반영할 수 없다.")

    stage = snapshot.interrupts[0].value.get("stage")
    allowed = svc.VALID_ACTIONS.get(stage, set())
    if body.action not in allowed:
        raise HTTPException(
            status_code=400,
            detail=f"'{stage}' 단계에서는 action이 {sorted(allowed)} 중 하나여야 한다.",
        )
    if body.action in ("reject", "edit", "search") and not body.content:
        raise HTTPException(status_code=400, detail=f"action='{body.action}'에는 content가 필요하다.")

    resume_value = {"action": body.action, "content": body.content}
    await graph.ainvoke(Command(resume=resume_value), config=svc.build_config(thread_id))

    new_snapshot = await graph.aget_state(svc.build_config(thread_id))
    response = svc.response_from_snapshot(thread_id, new_snapshot)

    # ── 그래프가 END에 도달(stage == "done")하면 MySQL에 최종 초안을 저장한다 ──
    if response.stage == "done":
        values = new_snapshot.values
        await section_crud.save_draft(
            db,
            thread_id=thread_id,
            user_id=user.id,
            section_title=values.get("section_title", ""),
            target_year=values.get("target_year", ""),
            esg_category=values.get("esg_category"),
            draft=values.get("draft", ""),
            sources=response.sources,
        )

    return response


@router.get("/{thread_id}", response_model=SectionStatusResponse)
async def get_section_status(
    thread_id: str,
    request: Request,
    user: User = Depends(get_current_user),
):
    """그래프를 진행시키지 않고 현재 대기 중인 단계/내용만 조회한다 (재접속·새로고침 대비)."""
    graph = await svc.get_graph(request)
    snapshot = await svc.load_owned_snapshot(graph, thread_id, user)
    return svc.response_from_snapshot(thread_id, snapshot)