# MySQL 데이터를 읽어 Ollama bge-m3로 임베딩한 뒤 ChromaDB(로컬 persistent)에 저장하는 파일
import asyncio
from typing import List, Dict
import chromadb
import requests
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
# local
from app.core.config import settings


### 수치데이터 제외 인덱싱할 데이터는 2024년도 데이터만 date BETWEEN '2024-01-01' AND '2024-12-31'###
BATCH = 1  # Ollama 한 번에 처리할 최대 텍스트 수, 최적의 값

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

    for i in range(0, len(rows), BATCH):
        batch = rows[i : i + BATCH]
        ids   = [f"press_{r.id}" for r in batch] ### id가 그냥 1,2,3,.. 이어서 다른 데이터와 중복 방지
        docs  = [r.content for r in batch]
        metas = [{
            "date":         int(r.date.strftime("%Y%m%d")) if r.date else 0,
            "title":        r.title or "",
            "category":     r.category or "",
            "hashtag":      r.hashtag or "",
            "url":          r.url or "",
            "source_db": "sk_hynix_press",
        } for r in batch]

        embeds = await asyncio.to_thread(_embed_batch, docs)
        col.upsert(ids=ids, documents=docs, metadatas=metas, embeddings=embeds)
        total += len(batch)

    return total


async def index_newsroom(db: AsyncSession) -> int:
    rows = (await db.execute(
        text("SELECT id, date, title, category, hashtag, content, url FROM sk_hynix_newsroom "
             "WHERE date BETWEEN '2024-01-01' AND '2024-12-31' "
             "AND content IS NOT NULL AND content != ''")
    )).fetchall()

    col = _get_client().get_or_create_collection("sk_hynix_newsroom", metadata={"hnsw:space": "cosine"})
    total = 0

    for i in range(0, len(rows), BATCH):
        batch = rows[i : i + BATCH]
        ids   = [f"newsroom_{r.id}" for r in batch] ### id가 그냥 1,2,3,.. 이어서 다른 데이터와 중복 방지
        docs  = [r.content for r in batch]
        metas = [{
            "date":         int(r.date.strftime("%Y%m%d")) if r.date else 0,
            "title":        r.title or "",
            "category":     r.category or "",
            "hashtag":      r.hashtag or "",
            "url":          r.url or "",
            "source_db": "sk_hynix_newsroom",
        } for r in batch]

        embeds = await asyncio.to_thread(_embed_batch, docs)
        col.upsert(ids=ids, documents=docs, metadatas=metas, embeddings=embeds)
        total += len(batch)

    return total


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
        docs  = [r.content for r in batch]
        metas = [{
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
