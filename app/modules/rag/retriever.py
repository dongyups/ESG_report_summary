# LangChain 기반 Retriever 모듈
# 변경 요약:
#   - 기존 retrieval.py: 모든 컬렉션에서 무조건 n=3, 임계값 없음, 단순 코사인 거리 정렬
#   - 신규 retriever.py: 컬렉션별 실측 데이터량(11~445) 기반 차등 n/threshold,
#                       쿼리 의도별 프로파일(qa/report_gen) 분리,
#                       E/S/G 메타데이터 자동 필터, MultiQueryRetriever 선택적 적용

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional, Tuple

import requests
# from langchain_anthropic import ChatAnthropic
from langchain_aws import ChatAnthropicBedrock
from langchain_chroma import Chroma
from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from langchain_classic.retrievers.multi_query import MultiQueryRetriever

from app.core.config import settings


# ──────────────────────────────────────────────
# Embeddings: 기존 Ollama 로직을 LangChain 인터페이스로 래핑
# 인덱서(indexer.py)가 생성한 ChromaDB 컬렉션과 동일한 모델을 사용해야 함
# ──────────────────────────────────────────────

class OllamaBgeEmbeddings(Embeddings):
    """Ollama bge-m3를 LangChain Embeddings 인터페이스로 래핑.
    인덱서에서 사용한 것과 동일한 fallback 로직 유지."""

    def _call_ollama(self, texts: List[str]) -> List[List[float]]:
        # 신버전 /api/embed (배치 지원)
        try:
            r = requests.post(
                f"{settings.OLLAMA_BASE_URL}/api/embed",
                json={"model": settings.OLLAMA_EMBED_MODEL, "input": texts},
                timeout=300,
            )
            if r.status_code == 200:
                return r.json()["embeddings"]
        except Exception:
            pass

        # 구버전 /api/embeddings 폴백 (단건)
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

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        return self._call_ollama(texts)

    def embed_query(self, text: str) -> List[float]:
        return self._call_ollama([text])[0]


# 싱글톤: 앱 전체에서 동일 인스턴스 재사용
_EMBEDDINGS = OllamaBgeEmbeddings()


# ──────────────────────────────────────────────
# 컬렉션 매핑
# ──────────────────────────────────────────────

COLLECTION_NAMES: Dict[str, str] = {
    "press":    "sk_hynix_press",
    "newsroom": "sk_hynix_newsroom",
    "report":   "sk_hynix_report",
    "esg_data": "sk_hynix_esg_data",
}

# ──────────────────────────────────────────────
# 컬렉션별 검색 프로파일 (실측 데이터량 기반 확정값)
#
#   실측 청크/행 수: press=11, newsroom=125, report=271, esg_data=445
#
#   profile "qa"         : 챗봇 일반 응답 — 정밀도 우선, 적은 컨텍스트
#   profile "report_gen" : 보고서 섹션 작성 — 재현율 우선
#                          (LLM이 섹션 초안 작성 시 다수 문서 중 선별하므로
#                           threshold를 낮춰 후보를 더 확보)
#
#   press/newsroom의 threshold가 report/esg_data보다 낮은 이유:
#   기사 본문은 표현이 자유롭고 구어체가 섞여 코사인 유사도가 구조화된
#   보고서/수치 데이터보다 낮게 나오는 경향이 있음. 동일 임계값 적용 시
#   관련 기사가 과도하게 걸러질 위험이 있어 컬렉션별로 차등 적용.
# ──────────────────────────────────────────────

COLLECTION_PROFILES: Dict[str, Dict[str, Dict[str, float]]] = {
    "press": {
        "qa":         {"n": 3, "threshold": 0.49},
        "report_gen": {"n": 4, "threshold": 0.44},
    },
    "newsroom": {
        "qa":         {"n": 5, "threshold": 0.49},
        "report_gen": {"n": 8, "threshold": 0.44},
    },
    "report": {
        "qa":         {"n": 8, "threshold": 0.50},
        "report_gen": {"n": 10, "threshold": 0.45},
    },
    "esg_data": {
        "qa":         {"n": 12, "threshold": 0.55},
        "report_gen": {"n": 16, "threshold": 0.50},
    },
}

# 미정의 컬렉션·프로파일 대비 안전 기본값
_DEFAULT_PROFILE_CONF: Dict[str, float] = {"n": 5, "threshold": 0.40}

# E/S/G 카테고리 키워드 (자동 메타데이터 필터 적용용)
_ESG_KEYWORDS: Dict[str, List[str]] = {
    "E": ["환경", "온실가스", "탄소", "에너지", "수자원", "폐기물", "기후변화",
          "생물다양성", "재생에너지", "Net Zero", "Scope"],
    "S": ["사회", "임직원", "안전", "보건", "인권", "다양성", "공급망",
          "지역사회", "교육", "복지", "여성"],
    "G": ["지배구조", "이사회", "윤리", "컴플라이언스", "부패방지",
          "정보보호", "주주", "감사", "공시"],
}


def detect_esg_category(query: str) -> Optional[str]:
    """키워드 기반 E/S/G 카테고리 감지.
    복수 카테고리 매칭 시 None 반환 (모호한 필터 방지)."""
    matched = [cat for cat, kws in _ESG_KEYWORDS.items() if any(kw in query for kw in kws)]
    return matched[0] if len(matched) == 1 else None


# ──────────────────────────────────────────────
# ChromaDB 연결 팩토리
# ──────────────────────────────────────────────

def _get_chroma(col_key: str) -> Chroma:
    """기존 ChromaDB 컬렉션(인덱서가 생성)에 LangChain Chroma 객체로 연결.
    collection_metadata는 relevance score 계산 방식(코사인) 지정에 사용됨."""
    return Chroma(
        collection_name=COLLECTION_NAMES[col_key],
        embedding_function=_EMBEDDINGS,
        persist_directory=settings.CHROMA_PATH,
        collection_metadata={"hnsw:space": "cosine"},
    )


# ──────────────────────────────────────────────
# 핵심 검색 함수
# ──────────────────────────────────────────────

def _search_collection(
    query: str,
    col_key: str,
    k: int,
    score_threshold: float,
    metadata_filter: Optional[Dict[str, Any]],
) -> List[Tuple[Document, float]]:
    """단일 컬렉션 검색.
    
    반환: (Document, relevance_score) 리스트.
    relevance_score: 코사인 유사도 = 1 - cosine_distance, 범위 [0, 1].
    높을수록 관련성 높음. score_threshold 미만은 제거.
    
    기존 retrieval.py와의 차이:
    - distance 오름차순 대신 relevance score 내림차순으로 통일
    - 임계값 미만 결과 자동 제거 (기존: 모두 반환)
    """
    chroma = _get_chroma(col_key)
    kwargs: Dict[str, Any] = {"k": k}
    if metadata_filter:
        kwargs["filter"] = metadata_filter
    try:
        pairs = chroma.similarity_search_with_relevance_scores(query, **kwargs)
        return [(doc, score) for doc, score in pairs if score >= score_threshold]
    except Exception:
        return []


def _search_multi_query(
    query: str,
    col_key: str,
    k: int,
    score_threshold: float,
    metadata_filter: Optional[Dict[str, Any]],
) -> List[Tuple[Document, float]]:
    """MultiQueryRetriever: Haiku로 쿼리를 3개로 확장 후 검색, 결과 합집합.
    
    적합한 경우: 쿼리가 광범위하거나 단일 쿼리로 충분한 결과를 못 얻을 때.
    ex) "SK하이닉스 지속가능성 전략" → 확장: "탄소중립 목표", "ESG 경영 방향", "Net Zero 2050"
    
    주의: Haiku API 호출 비용 발생, 속도 저하 (3~5초 추가).
    """
    chroma = _get_chroma(col_key)
    base_retriever = chroma.as_retriever(
        search_type="similarity_score_threshold",
        search_kwargs={
            "k": k,
            "score_threshold": score_threshold,
            **({"filter": metadata_filter} if metadata_filter else {}),
        },
    )
    llm = ChatAnthropicBedrock(
        model=settings.LLM_MODEL,
        # api_key=settings.ANTHROPIC_API_KEY,
        region_name=settings.AWS_REGION,
        temperature=0,
        max_tokens=600,
    )
    mqr = MultiQueryRetriever.from_llm(retriever=base_retriever, llm=llm)
    try:
        docs = mqr.invoke(query)
        # MultiQueryRetriever는 score를 노출하지 않으므로 threshold 값으로 대체
        return [(d, score_threshold) for d in docs]
    except Exception:
        # 폴백: 단순 검색
        return _search_collection(query, col_key, k, score_threshold, metadata_filter)


def retrieve_sync(
    query: str,
    profile: str = "qa",
    esg_category: Optional[str] = None,
    target_collections: Optional[List[str]] = None,
    use_multi_query: bool = False,
    final_k: Optional[int] = None,
) -> List[Document]:
    """
    멀티 컬렉션 검색 (동기).

    Parameters
    ----------
    query               : 검색 쿼리
    profile              : "qa" | "report_gen" — COLLECTION_PROFILES에서 컬렉션별
                          n/threshold를 조회하는 키. 컬렉션마다 실제 데이터량이
                          크게 달라(11~445) 동일 n을 적용하면 작은 컬렉션은
                          과도하게, 큰 컬렉션은 부족하게 검색되므로 컬렉션별로
                          분리된 값을 사용한다.
    esg_category        : "E"/"S"/"G"/"I" 명시 시 report/esg_data 컬렉션에 메타데이터 필터 적용.
                          None 시 detect_esg_category()로 자동 감지.
    target_collections  : 검색 대상 컬렉션 키 목록 (None = 전체 4개)
    use_multi_query     : True 시 Haiku로 쿼리 확장 후 검색 (품질↑, 속도↓)
    final_k             : 컬렉션 병합·정렬 후 최종적으로 자를 문서 수.
                          None(기본값)이면 기존과 동일하게 컬렉션별 n의 합을
                          그대로 반환한다(하위 호환). report_gen 프로파일처럼
                          컬렉션 4개의 n 합이 30건을 넘는 경우, 호출부에서
                          컨텍스트 크기를 제어하려면 명시적으로 지정한다.

    Returns
    -------
    중복 제거 후 relevance score 내림차순 정렬된 Document 리스트.
    final_k가 주어지면 그 안에서 상위 final_k개만 반환한다.
    """
    cols = target_collections or list(COLLECTION_NAMES.keys())

    # "I"은 호출측(analyze_query/plan_report)이 "특정 pillar에 속하지
    # 않음"을 이미 확정한 상태이므로, 쿼리 텍스트의 키워드 추론을 생략하고
    # 즉시 필터 없는 검색으로 전환한다. 단순 None(미지정)일 때만
    # detect_esg_category()로 보완한다.
    # ── 이 구분이 없으면: "주주들을 위한 CEO의 메시지"처럼 쿼리에 "주주"가
    #    포함된 경우 G 키워드 매칭으로 카테고리가 재할당되어, 실제
    #    esg_category가 비어있는(NULL) CEO 메시지 청크가 검색에서 배제됨.
    if esg_category == "I":
        cat = None
    else:
        cat = esg_category or detect_esg_category(query)

    all_pairs: List[Tuple[Document, float]] = []
    seen: set = set()

    for col_key in cols:
        if col_key not in COLLECTION_NAMES:
            continue

        # 컬렉션별 확정 파라미터 조회 (COLLECTION_PROFILES 참조)
        conf = COLLECTION_PROFILES.get(col_key, {}).get(profile, _DEFAULT_PROFILE_CONF)
        n, threshold = conf["n"], conf["threshold"]

        # E/S/G 메타 필터: report/esg_data 컬렉션에만 적용
        # press/newsroom은 esg_category 메타데이터가 없으므로 필터 제외
        meta_filter: Optional[Dict[str, Any]] = (
            {"esg_category": cat} if cat and col_key in ("report", "esg_data") else None
        )

        search_fn = _search_multi_query if use_multi_query else _search_collection
        pairs = search_fn(query, col_key, n, threshold, meta_filter)

        for doc, score in pairs:
            # 중복 제거: chunk_id 우선, 없으면 내용 해시
            key = doc.metadata.get("chunk_id") or hash(doc.page_content) #[:80]
            if key not in seen:
                seen.add(key)
                all_pairs.append((doc, score))

    # relevance score 내림차순 (기존 distance 오름차순과 동일한 의미)
    all_pairs.sort(key=lambda x: x[1], reverse=True)

    # 최종 top-N 컷. 컬렉션별 n의 합이 그대로 컨텍스트로 들어가던 문제를
    # 호출부에서 선택적으로 제어할 수 있게 한다 (예: report_gen 프로파일은
    # 컬렉션 4개 합이 32건까지 나올 수 있음).
    if final_k is not None:
        all_pairs = all_pairs[:final_k]

    # 프론트엔드(page4_rag.js)가 score = (1 - s.distance) * 100 으로 표시하므로
    # distance 필드를 메타데이터에 복원한다.
    # LangChain Chroma의 relevance_score(코사인) = 1 - distance 이므로 역산.
    result: List[Document] = []
    for doc, score in all_pairs:
        doc.metadata["distance"] = round(1 - score, 4)
        result.append(doc)
    return result


async def retrieve(
    query: str,
    profile: str = "qa",
    esg_category: Optional[str] = None,
    target_collections: Optional[List[str]] = None,
    use_multi_query: bool = False,
    final_k: Optional[int] = None,
) -> List[Document]:
    """비동기 래퍼: event loop를 막지 않도록 threadpool에서 실행."""
    return await asyncio.to_thread(
        retrieve_sync,
        query,
        profile,
        esg_category,
        target_collections,
        use_multi_query,
        final_k,
    )


# ──────────────────────────────────────────────
# Context / Sources 포매터
# (기존 service.py의 _build_context / _format_sources를 Document 기반으로 이관)
# ──────────────────────────────────────────────

def docs_to_context(docs: List[Document]) -> str:
    """LangChain Document 리스트 → LLM 프롬프트용 컨텍스트 문자열."""
    if not docs:
        return "관련 문서를 찾을 수 없습니다."

    parts = []
    for i, doc in enumerate(docs, 1):
        meta = doc.metadata
        src  = meta.get("source_db", "")

        if src == "sk_hynix_press":
            hdr = (f"[출처 {i}] 보도자료 | 날짜: {meta.get('date','')} | "
                   f"a.{meta.get('article_num','')} c.{meta.get('chunk_index','')} | "
                   f"해시태그: {meta.get('hashtag','')} | 제목: [{meta.get('title','')}]")
            parts.append(f"{hdr}\n{doc.page_content}")

        elif src == "sk_hynix_newsroom":
            hdr = (f"[출처 {i}] 뉴스룸 | 날짜: {meta.get('date','')} | "
                   f"a.{meta.get('article_num','')} c.{meta.get('chunk_index','')} | "
                   f"해시태그: {meta.get('hashtag','')} | 제목: [{meta.get('title','')}]")
            parts.append(f"{hdr}\n{doc.page_content}")

        elif src == "sk_hynix_report":
            hdr = (f"[출처 {i}] ESG보고서 | "
                   f"p.{meta.get('page_num','')} c.{meta.get('chunk_index','')} | "
                   f"섹션: ")
                ### 이미 page_content가 "[섹션 > 대분류 > 중분류 | 테이블명]\n내용내용" 이므로 그냥 붙이기 ###
                #    f"섹션: {meta.get('section','')} — "
                #    f"{meta.get('heading_level_1','')} > "
                #    f"{meta.get('heading_level_2','')} > "
                #    f"{meta.get('heading_level_3','')} | "
                #    f"{meta.get('table_title','')}")
            parts.append(f"{hdr}{doc.page_content}")

        elif src == "sk_hynix_esg_data":
            hdr = f"[출처 {i}] ESG데이터 | {doc.page_content}"
            data_lines = "\n".join([
                f"2019년: {meta.get('value_2019', '')}",
                f"2020년: {meta.get('value_2020', '')}",
                f"2021년: {meta.get('value_2021', '')}",
                f"2022년: {meta.get('value_2022', '')}",
                f"2023년: {meta.get('value_2023', '')}",
                f"2024년: {meta.get('value_2024', '')}",
            ])
            parts.append(f"{hdr}\n{data_lines}")

        else:
            parts.append(f"[출처 {i}]\n{doc.page_content}")

    return "\n\n---\n\n".join(parts)


def docs_to_sources(docs: List[Document]) -> List[Dict]:
    """LangChain Document 리스트 → 프론트엔드용 출처 메타데이터 리스트."""
    out = []
    for doc in docs:
        meta = doc.metadata
        src  = meta.get("source_db", "")
        # 프론트엔드(page4_rag.js)가 score=(1-distance)*100으로 표시.
        # retrieve_sync()에서 메타데이터에 복원해둔 값을 그대로 전달.
        dist = meta.get("distance", 1.0)

        if src == "sk_hynix_press":
            out.append({"type": "보도자료",
                        "date":        meta.get("date", ""),
                        "title":       meta.get("title", ""),
                        "url":         meta.get("url", ""),
                        "article_num": meta.get("article_num", ""),
                        "chunk_index": meta.get("chunk_index", ""),
                        "distance":    dist})
        elif src == "sk_hynix_newsroom":
            out.append({"type": "뉴스룸",
                        "date":        meta.get("date", ""),
                        "title":       meta.get("title", ""),
                        "url":         meta.get("url", ""),
                        "article_num": meta.get("article_num", ""),
                        "chunk_index": meta.get("chunk_index", ""),
                        "distance":    dist})
        elif src == "sk_hynix_report":
            out.append({"type": "ESG보고서",
                        "esg_category":    meta.get("esg_category", ""),
                        "company":         meta.get("company", ""),
                        # "report_year":     meta.get("report_year", ""), ### 무의미한 변수
                        "section":         meta.get("section", ""),
                        "heading_level_1": meta.get("heading_level_1", ""),
                        "heading_level_2": meta.get("heading_level_2", ""),
                        "heading_level_3": meta.get("heading_level_3", ""),
                        "table_title":     meta.get("table_title", ""),
                        "page_num":        meta.get("page_num", ""),
                        "chunk_index":     meta.get("chunk_index", ""),
                        "distance":        dist})
        elif src == "sk_hynix_esg_data":
            out.append({"type": "ESG데이터",
                        "esg_category":     meta.get("esg_category", ""),
                        "company":          meta.get("company", ""),
                        "site":             meta.get("site", ""),
                        "category_level_1": meta.get("category_level_1", ""),
                        "category_level_2": meta.get("category_level_2", ""),
                        "category_level_3": meta.get("category_level_3", ""),
                        "category_level_4": meta.get("category_level_4", ""),
                        "unit":             meta.get("unit", ""),
                        "distance":         dist})
    return out