# Ollama bge-m3으로 쿼리를 임베딩한 뒤 ChromaDB에서 유사 문서 검색 기능을 담당하는 파일
import asyncio, chromadb, requests
from typing import List, Dict, Optional
# local
from app.core.config import settings


def _get_client() -> chromadb.PersistentClient:
    return chromadb.PersistentClient(path=settings.CHROMA_PATH)


def _embed_query(query: str) -> List[float]:
    """단일 쿼리 임베딩 (동기)."""
    try:
        resp = requests.post(
            f"{settings.OLLAMA_BASE_URL}/api/embed",
            json={"model": settings.OLLAMA_EMBED_MODEL, "input": query},
            timeout=60,
        )
        if resp.status_code == 200:
            return resp.json()["embeddings"][0]
    except Exception:
        pass

    # 구버전 폴백
    resp = requests.post(
        f"{settings.OLLAMA_BASE_URL}/api/embeddings",
        json={"model": settings.OLLAMA_EMBED_MODEL, "prompt": query},
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()["embedding"]


def _search_one(collection_name: str, embedding: List[float], n: int) -> List[Dict]:
    client = _get_client()
    try:
        col = client.get_collection(collection_name)
    except Exception:
        return []

    count = col.count()
    if count == 0:
        return []

    res = col.query(
        query_embeddings=[embedding],
        n_results=min(n, count),
        include=["documents", "metadatas", "distances"],
    )

    return [
        {
            "id":         res["ids"][0][i],
            "document":   res["documents"][0][i],
            "metadata":   res["metadatas"][0][i],
            "distance":   res["distances"][0][i],
            "collection": collection_name,
        }
        for i in range(len(res["ids"][0]))
    ]


def retrieve_sync(
    query: str,
    collections: Optional[List[str]] = None,
    n_per_collection: int = 3,
) -> List[Dict]:
    if collections is None:
        collections = ["sk_hynix_press", "sk_hynix_newsroom", "sk_hynix_report", "sk_hynix_esg_data"]

    emb = _embed_query(query)

    results = []
    for col in collections:
        results.extend(_search_one(col, emb, n_per_collection))

    # 코사인 거리 오름차순 (낮을수록 유사)
    results.sort(key=lambda x: x["distance"])
    return results


async def retrieve(query: str, n_per_collection: int = 3) -> List[Dict]:
    """비동기 래퍼 — event loop를 막지 않도록 threadpool에서 실행."""
    return await asyncio.to_thread(retrieve_sync, query, None, n_per_collection)
