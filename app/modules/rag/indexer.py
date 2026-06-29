# MySQL 데이터를 읽어 Ollama bge-m3로 임베딩한 뒤 ChromaDB(로컬 persistent)에 저장하는 파일
import asyncio
from typing import List, Dict
import chromadb
import requests
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from langchain_text_splitters.character import RecursiveCharacterTextSplitter
# local
from app.core.config import settings


### 수치데이터 제외 인덱싱할 데이터는 2024년도 데이터만 date BETWEEN '2024-01-01' AND '2024-12-31'###
BATCH = 1  # Ollama 한 번에 처리할 최대 텍스트 수, 최적의 값

# ──────────────────────────────────────────────
# 텍스트 청킹
# press:    단신·공식 발표 형식 (700~2,000자) → chunk_size=500
# newsroom: 심층 분석 기사    (4,000~8,000자) → chunk_size=600
# separators 우선순위: 문단 > 줄바꿈 > 문장 부호 > 공백
# ──────────────────────────────────────────────
_PRESS_SPLITTER = RecursiveCharacterTextSplitter(
    chunk_size=500,
    chunk_overlap=100,
    separators=["\n\n", "\n", ".", " ", ""],
)
 
_NEWSROOM_SPLITTER = RecursiveCharacterTextSplitter(
    chunk_size=600,
    chunk_overlap=100,
    separators=["\n\n", "\n", ".", " ", ""],
)

# ──────────────────────────────────────────────
# 공통 유틸
# ──────────────────────────────────────────────
def _get_client() -> chromadb.PersistentClient:
    return chromadb.PersistentClient(path=settings.CHROMA_PATH)


def _embed_batch(texts: List[str]) -> List[List[float]]:
    """Ollama /api/embed (배치 지원). 구버전 폴백 포함."""
    try:
        resp = requests.post(
            f"{settings.OLLAMA_BASE_URL}/api/embed",
            json={"model": settings.OLLAMA_EMBED_MODEL, "input": texts},
            timeout=300,
        )
        if resp.status_code == 200:
            return resp.json()["embeddings"]
    except Exception:
        pass

    # 구버전 Ollama 폴백 (/api/embeddings, 단건)
    result = []
    for t in texts:
        r = requests.post(
            f"{settings.OLLAMA_BASE_URL}/api/embeddings",
            json={"model": settings.OLLAMA_EMBED_MODEL, "prompt": t},
            timeout=120,
        )
        r.raise_for_status()
        result.append(r.json()["embedding"])
    return result


# ──────────────────────────────────────────────
# 테이블별 인덱서
# ──────────────────────────────────────────────
async def index_press(db: AsyncSession) -> int:
    rows = (await db.execute(
        text("SELECT id, date, title, category, hashtag, content, url FROM sk_hynix_press "
             "WHERE date BETWEEN '2024-01-01' AND '2024-12-31' "
             "AND content IS NOT NULL AND content != ''")
    )).fetchall()

    col = _get_client().get_or_create_collection("sk_hynix_press", metadata={"hnsw:space": "cosine"})
    total = 0

    ### 청킹으로 BATCH 루프 삭제, 추후 변경 가능 ###
    for row in rows:
        # 기사 전문 → 청크 분할
        # 예상: 700자 → 2청크, 2,000자 → 4~5청크, 전체 3건 → 약 10~12청크 → 결과: 11개
        chunks = _PRESS_SPLITTER.split_text(row.content)
        if not chunks:
            continue

        # press_article1_chunk1
        chunk_ids   = [f"press_a{row.id}_c{i}" for i in range(len(chunks))]
        chunk_metas = [{
            "chunk_id":    f"press_a{row.id}_c{i}",
            "date":        int(row.date.strftime("%Y%m%d")) if row.date else 0,
            "title":       row.title or "",
            "category":    row.category or "",
            "hashtag":     row.hashtag or "",
            "url":         row.url or "",
            "article_num": row.id,
            "chunk_index": i,
            "source_db":   "sk_hynix_press",
        } for i in range(len(chunks))]
 
        embeds = await asyncio.to_thread(_embed_batch, chunks)
        col.upsert(ids=chunk_ids, documents=chunks, metadatas=chunk_metas, embeddings=embeds)
        total += len(chunks)

    return total


async def index_newsroom(db: AsyncSession) -> int:
    rows = (await db.execute(
        text("SELECT id, date, title, category, hashtag, content, url FROM sk_hynix_newsroom "
             "WHERE date BETWEEN '2024-01-01' AND '2024-12-31' "
             "AND content IS NOT NULL AND content != ''")
    )).fetchall()

    col = _get_client().get_or_create_collection("sk_hynix_newsroom", metadata={"hnsw:space": "cosine"})
    total = 0

    ### 청킹으로 BATCH 루프 삭제, 추후 변경 가능 ###
    for row in rows:
        # 기사 전문 → 청크 분할
        # 예상: 4,000자 → 7~8청크, 8,000자 → 15~16청크, 전체 19건 → 약 180~210청크 → 결과: 122개
        chunks = _NEWSROOM_SPLITTER.split_text(row.content)
        if not chunks:
            continue
        
        # newsroom_article1_chunk1
        chunk_ids   = [f"newsroom_a{row.id}_c{i}" for i in range(len(chunks))]
        chunk_metas = [{
            "chunk_id":    f"newsroom_a{row.id}_c{i}",
            "date":        int(row.date.strftime("%Y%m%d")) if row.date else 0,
            "title":       row.title or "",
            "category":    row.category or "",
            "hashtag":     row.hashtag or "",
            "url":         row.url or "",
            "article_num": row.id,
            "chunk_index": i,
            "source_db":   "sk_hynix_newsroom",
        } for i in range(len(chunks))]
 
        embeds = await asyncio.to_thread(_embed_batch, chunks)
        col.upsert(ids=chunk_ids, documents=chunks, metadatas=chunk_metas, embeddings=embeds)
        total += len(chunks)

    return total


### RAG 검색 품질 향상을 위해 content에 heading 및 table_title 붙이기 ###
def _build_report_doc(r) -> str:
    headings = " > ".join(
        h for h in [r.heading_level_1, r.heading_level_2, r.heading_level_3] if h
    )
    parts = []
    if headings:
        parts.append(headings)
    if r.table_title:
        parts.append(f"Table: {r.table_title}")
    prefix = f"[{' | '.join(parts)}]\n" if parts else ""
    return f"{prefix}{r.content}"

async def index_report(db: AsyncSession) -> int:
    rows = (await db.execute(
        text("""
            SELECT company, report_year, source, esg_category, section, page_num, chunk_index, chunk_id, 
                   heading_level_1, heading_level_2, heading_level_3, content_type, table_title, content
            FROM sk_hynix_report
            WHERE content IS NOT NULL AND content != ''
        """)
    )).fetchall()

    col = _get_client().get_or_create_collection("sk_hynix_report", metadata={"hnsw:space": "cosine"})
    total = 0

    for i in range(0, len(rows), BATCH):
        batch = rows[i : i + BATCH]
        ids = [r.chunk_id for r in batch]
        ### "[섹션 > 대분류 > 중분류 | 테이블명]\n내용내용" 이런 형식으로 저장됨 ###
        docs  = docs = [_build_report_doc(r) for r in batch] #[r.content for r in batch]
        metas = [{
            "chunk_id":        r.chunk_id or "", ### 추가
            "company":         r.company or "",
            "report_year":     r.report_year or "",
            "source":          r.source or "",
            "esg_category":    r.esg_category or "",
            "section":         r.section or "",
            "page_num":        int(r.page_num) if r.page_num is not None else 0,
            "chunk_index":     int(r.chunk_index) if r.chunk_index is not None else 0,
            "heading_level_1": r.heading_level_1 or "",
            "heading_level_2": r.heading_level_2 or "",
            "heading_level_3": r.heading_level_3 or "",
            "content_type":    r.content_type or "",
            "table_title":     r.table_title or "",
            "source_db": "sk_hynix_report",
        } for r in batch]

        embeds = await asyncio.to_thread(_embed_batch, docs)
        col.upsert(ids=ids, documents=docs, metadatas=metas, embeddings=embeds)
        total += len(batch)

    return total


async def index_esgdata(db: AsyncSession) -> int:
    rows = (await db.execute(
        text("""
            SELECT id, company, site, area, esg_category,
                   category_level_1, category_level_2, category_level_3, category_level_4,
                   unit, value_2019, value_2020, value_2021, value_2022, value_2023, value_2024
            FROM sk_hynix_e
            UNION ALL
            SELECT id, company, site, area, esg_category,
                   category_level_1, category_level_2, category_level_3, category_level_4,
                   unit, value_2019, value_2020, value_2021, value_2022, value_2023, value_2024
            FROM sk_hynix_s
            UNION ALL
            SELECT id, company, site, area, esg_category,
                   category_level_1, category_level_2, category_level_3, category_level_4,
                   unit, value_2019, value_2020, value_2021, value_2022, value_2023, value_2024
            FROM sk_hynix_g
        """)
    )).fetchall()

    col = _get_client().get_or_create_collection("sk_hynix_esg_data", metadata={"hnsw:space": "cosine"})
    total = 0

    for i in range(0, len(rows), BATCH):
        batch = rows[i : i + BATCH]
        ids   = [f"sk_hynix_{r.esg_category}_{r.id}" for r in batch]
        # 예시) documents: SK하이닉스 | 전사 | 환경(E) | 기후변화 > 온실가스 > Scope 1 > CO2 | 단위: tCO2eq
        docs  = [
            f"{r.company or ''} | {r.site or ''} | {r.area or ''}({r.esg_category or ''}) | "
            f"{' > '.join(c for c in [r.category_level_1, r.category_level_2, r.category_level_3, r.category_level_4] if c)} | "
            f"단위: {r.unit or ''}"
            for r in batch
        ]
        metas = [
            {
                "chunk_id":         f"sk_hynix_{r.esg_category}_{r.id}",
                "company":          r.company or "",
                "site":             r.site or "",
                "area":             r.area or "",
                "esg_category":     r.esg_category or "",
                "category_level_1": r.category_level_1 or "",
                "category_level_2": r.category_level_2 or "",
                "category_level_3": r.category_level_3 or "",
                "category_level_4": r.category_level_4 or "",
                "unit":             r.unit or "",
                "source_db":        "sk_hynix_esg_data",
                "value_2019":       float(r.value_2019) if r.value_2019 is not None else -1.0,
                "value_2020":       float(r.value_2020) if r.value_2020 is not None else -1.0,
                "value_2021":       float(r.value_2021) if r.value_2021 is not None else -1.0,
                "value_2022":       float(r.value_2022) if r.value_2022 is not None else -1.0,
                "value_2023":       float(r.value_2023) if r.value_2023 is not None else -1.0,
                "value_2024":       float(r.value_2024) if r.value_2024 is not None else -1.0,
            }
            for r in batch
        ]

        embeds = await asyncio.to_thread(_embed_batch, docs)
        col.upsert(ids=ids, documents=docs, metadatas=metas, embeddings=embeds)
        total += len(batch)

    return total


# ──────────────────────────────────────────────
# 전체 / 상태 조회
# ──────────────────────────────────────────────
async def index_all(db: AsyncSession) -> Dict[str, int]:
    press    = await index_press(db)
    newsroom = await index_newsroom(db)
    report   = await index_report(db)
    esgdata  = await index_esgdata(db)

    return {
        "sk_hynix_press":    press,
        "sk_hynix_newsroom": newsroom,
        "sk_hynix_report":   report,
        "sk_hynix_esg_data": esgdata,
    }


def get_index_status() -> Dict[str, int]:
    client = _get_client()
    status = {}
    for name in ("sk_hynix_press", "sk_hynix_newsroom", "sk_hynix_report", "sk_hynix_esg_data"):
        try:
            status[name] = client.get_collection(name).count()
        except Exception:
            status[name] = 0
    return status
