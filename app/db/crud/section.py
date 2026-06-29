# section_drafts 테이블 CRUD
#
# SECTION_GRAPH(HITL)에서 최종 승인(approve)으로 'done'에 도달한 섹션 초안을
# MySQL에 영구 저장·조회·삭제한다.
# RagConversation/RagMessage CRUD(db/crud/rag.py)와 동일한 패턴을 따른다.

from __future__ import annotations

from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.section import SectionDraft


async def save_draft(
    db: AsyncSession,
    *,
    thread_id: str,
    user_id: int,
    section_title: str,
    target_year: str,
    esg_category: Optional[str],
    draft: str,
    sources: Optional[List[Dict[str, Any]]] = None,
) -> SectionDraft:
    """완성된 섹션 초안을 저장한다 (thread_id 기준 upsert).

    같은 thread_id로 재호출되면(예: 같은 스레드를 다시 approve했을 때)
    INSERT 대신 UPDATE를 수행해 중복을 방지한다.
    """
    existing = await get_draft_by_thread(db, thread_id=thread_id, user_id=user_id)

    if existing:
        # 이미 저장된 초안 → 내용만 갱신 (re-approve 케이스)
        existing.draft   = draft
        existing.sources = sources or []
        await db.commit()
        await db.refresh(existing)
        return existing

    record = SectionDraft(
        thread_id     = thread_id,
        user_id       = user_id,
        section_title = section_title,
        target_year   = target_year,
        esg_category  = esg_category,
        draft         = draft,
    )
    record.sources = sources or []   # property setter가 JSON 직렬화
    db.add(record)
    await db.commit()
    await db.refresh(record)
    return record


async def get_draft_by_thread(
    db: AsyncSession,
    *,
    thread_id: str,
    user_id: int,
) -> Optional[SectionDraft]:
    """thread_id + user_id 로 초안 단건 조회 (소유자 검증 포함)."""
    result = await db.execute(
        select(SectionDraft).where(
            SectionDraft.thread_id == thread_id,
            SectionDraft.user_id   == user_id,
        )
    )
    return result.scalar_one_or_none()


async def get_drafts(
    db: AsyncSession,
    *,
    user_id: int,
) -> List[SectionDraft]:
    """사용자의 완성 초안 목록을 최신 순으로 반환한다."""
    result = await db.execute(
        select(SectionDraft)
        .where(SectionDraft.user_id == user_id)
        .order_by(SectionDraft.created_at.desc())
    )
    return result.scalars().all()


async def get_draft(
    db: AsyncSession,
    *,
    draft_id: int,
    user_id: int,
) -> Optional[SectionDraft]:
    """PK + user_id 로 초안 단건 조회 (소유자 검증 포함)."""
    result = await db.execute(
        select(SectionDraft).where(
            SectionDraft.id      == draft_id,
            SectionDraft.user_id == user_id,
        )
    )
    return result.scalar_one_or_none()


async def delete_draft(
    db: AsyncSession,
    *,
    draft_id: int,
    user_id: int,
) -> bool:
    """초안을 삭제한다. 성공 시 True, 존재하지 않으면 False."""
    record = await get_draft(db, draft_id=draft_id, user_id=user_id)
    if not record:
        return False
    await db.delete(record)
    await db.commit()
    return True