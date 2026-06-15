# RAG api
import json
from typing import List
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
# local
from app.db.models.database import get_db
from app.db.models.user import User
from app.db.crud import rag as rag_crud
from app.schemas import rag as schemas
from app.modules.rag import service, indexer
from app.modules.auth.dependency import get_current_user

router = APIRouter()


# ──────────────────────────────────────────────
# 인덱싱
# ──────────────────────────────────────────────
@router.post("/index")
async def trigger_index(db: AsyncSession = Depends(get_db), _: User = Depends(get_current_user)):
    """MySQL → ChromaDB 인덱싱. 진행 상황을 SSE로 스트리밍."""
    async def _progress():
        def _sse(obj):
            return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"

        yield _sse({"message": "보도자료 인덱싱 중...", "done": False})
        press = await indexer.index_press(db)
        yield _sse({"message": f"보도자료 {press}건 완료", "done": False})

        yield _sse({"message": "뉴스룸 인덱싱 중...", "done": False})
        newsroom = await indexer.index_newsroom(db)
        yield _sse({"message": f"뉴스룸 {newsroom}건 완료", "done": False})

        yield _sse({"message": "보고서 인덱싱 중...", "done": False})
        report = await indexer.index_report(db)
        yield _sse({"message": f"보고서 chunk {report}개 완료", "done": False})

        yield _sse({"message": "데이터 인덱싱 중...", "done": False})
        esgdata = await indexer.index_esgdata(db)
        yield _sse({"message": f"데이터 row {esgdata}개 완료", "done": False})

        yield _sse({
            "message": f"인덱싱 완료 — 보도자료 {press}건 / 뉴스룸 {newsroom}건 / 보고서 chunk {report}개 / 데이터 row {esgdata}개",
            "done": True,
            "counts": {"press": press, "newsroom": newsroom, "report": report, "esgdata": esgdata},
        })

    return StreamingResponse(_progress(), media_type="text/event-stream")


@router.get("/status")
async def get_status(_: User = Depends(get_current_user)):
    """ChromaDB 컬렉션별 문서 수 반환."""
    return indexer.get_index_status()


# ──────────────────────────────────────────────
# 대화 CRUD
# ──────────────────────────────────────────────
@router.get("/conversations", response_model=List[schemas.RagConversationResponse])
async def list_conversations(db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    convs = await rag_crud.get_conversations(db, user.id)
    return [
        schemas.RagConversationResponse(
            id=c.id, title=c.title,
            created_at=c.created_at.isoformat(),
            updated_at=c.updated_at.isoformat(),
        )
        for c in convs
    ]


@router.post("/conversations", response_model=schemas.RagConversationResponse)
async def create_conversation(data: schemas.RagConversationCreate, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    c = await rag_crud.create_conversation(db, user.id, data.title)
    return schemas.RagConversationResponse(
        id=c.id, title=c.title,
        created_at=c.created_at.isoformat(),
        updated_at=c.updated_at.isoformat(),
    )


@router.get("/conversations/{conv_id}", response_model=schemas.RagConversationDetailResponse)
async def get_conversation(conv_id: int, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    c = await rag_crud.get_conversation(db, conv_id, user.id)
    if not c:
        raise HTTPException(status_code=404, detail="대화를 찾을 수 없습니다.")
    msgs = await rag_crud.get_messages(db, conv_id)
    return schemas.RagConversationDetailResponse(
        id=c.id, title=c.title,
        created_at=c.created_at.isoformat(),
        updated_at=c.updated_at.isoformat(),
        messages=[
            schemas.RagMessageResponse(
                id=m.id, role=m.role, content=m.content,
                thinking=m.thinking, sources=m.sources,
                created_at=m.created_at.isoformat(),
            )
            for m in msgs
        ],
    )


@router.put("/conversations/{conv_id}", response_model=schemas.RagConversationResponse)
async def update_conversation(conv_id: int, data: schemas.RagConversationUpdate, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    c = await rag_crud.update_conversation_title(db, conv_id, user.id, data.title)
    if not c:
        raise HTTPException(status_code=404)
    return schemas.RagConversationResponse(
        id=c.id, title=c.title,
        created_at=c.created_at.isoformat(),
        updated_at=c.updated_at.isoformat(),
    )


@router.delete("/conversations/{conv_id}")
async def delete_conversation(conv_id: int, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    ok = await rag_crud.delete_conversation(db, conv_id, user.id)
    if not ok:
        raise HTTPException(status_code=404)
    return {"message": "삭제됨"}


# ──────────────────────────────────────────────
# 메시지 전송 (스트리밍)
# ──────────────────────────────────────────────
@router.post("/conversations/{conv_id}/messages")
async def send_message(conv_id: int, message: schemas.RagMessageRequest, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    conv = await rag_crud.get_conversation(db, conv_id, user.id)
    if not conv:
        raise HTTPException(status_code=404, detail="대화를 찾을 수 없습니다.")

    # 사용자 메시지 저장
    await rag_crud.create_message(db, conv_id, "user", message.content)

    # 히스토리: 방금 저장한 user 메시지 제외 (서비스에서 RAG context 삽입)
    all_msgs = await rag_crud.get_messages(db, conv_id)
    history  = [{"role": m.role, "content": m.content} for m in all_msgs[:-1]]

    # 스트리밍 중 누적 후 DB 저장
    full_thinking = ""
    full_text     = ""
    full_sources  = []

    async def _stream():
        nonlocal full_thinking, full_text, full_sources

        async for sse in service.generate_rag_response(message.content, history):
            if sse.startswith("data: "):
                try:
                    p = json.loads(sse[6:].strip())
                    t = p.get("type")
                    if t == "thinking":
                        full_thinking = p.get("content", "")
                    elif t == "text":
                        full_text += p.get("chunk", "")
                    elif t == "sources":
                        full_sources = p.get("sources", [])
                except Exception:
                    pass
            yield sse

        # 응답 저장
        await rag_crud.create_message(
            db, conv_id, "assistant", full_text,
            thinking=full_thinking or None,
            sources=json.dumps(full_sources, ensure_ascii=False) if full_sources else None,
        )

    return StreamingResponse(_stream(), media_type="text/event-stream")
