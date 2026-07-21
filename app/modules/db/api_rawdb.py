# RAW(원천 ERP/GHG) 데이터 조회 API 엔드포인트
# ------------------------------------------------------------------
# page3(modules/db/api.py)와 동일 패턴을 유지하되,
# page2 화면이 필요로 하는 "RAW1~6 뷰"를 서버에서 SQL로 산출해 반환한다.
#
#   RAW1  rawdata_ghg_quantity ⨝ rawdata_ghg_formula  (전체 + 파생컬럼)
#   RAW2  └ item_name = '전력'
#   RAW3  └ item_name = '스팀 (유연탄 (연료용))'
#   RAW4  rawdata_erp  item_name IN (일반/지정 폐기물, 재활용, 매립)
#   RAW5  rawdata_erp  item_name IN (공업용수 취수량, 용수 재이용량)
#   RAW6  rawdata_erp  item_name = '폐수 방류량'
#
# 파생 컬럼(energy_usage, ghg_emissions)은 물리 테이블에 저장되지 않고
# rawdata_ghg_formula 의 계수(gcv/ncv/ef_*/gwp_*)로 SQL 안에서 계산된다.
# 반환 dict 의 "sql" 값이 그대로 화면의 "실행 SQL 쿼리" 패널에 표시된다.
# ------------------------------------------------------------------


### 현재 코드는 schemas/db.py, modules/db/service.py로 나눠져야할 코드 한곳에 모여있는 형식임, TODO: 코드 분리 ###
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from enum import Enum
from pydantic import BaseModel
from typing import Any, Optional
import json
# LLM (그래프 스펙 생성): Anthropic 공식 SDK의 Bedrock 클라이언트.
# config 가 os.environ 에 세팅하는 AWS_BEARER_TOKEN_BEDROCK 로 자동 인증되며,
# 베어러 토큰 경로는 boto3/SigV4 서명을 쓰지 않는다(httpx 만 사용).
from anthropic import AsyncAnthropicBedrock
# local
from app.db.models.database import get_db
from app.db.models.user import User
from app.modules.auth.dependency import get_current_user
from app.core.config import settings


router = APIRouter()


# ==================================================================
# (1) 물리 테이블 직접 조회  ── page3 api.py 와 동일 패턴 (원천 점검용)
#     프런트 화면은 아래 (2) /raw/{raw_type} 를 사용한다.
# ==================================================================
class RawTable(str, Enum):
    rawdata_erp = "rawdata_erp"
    rawdata_ghg_quantity = "rawdata_ghg_quantity"
    rawdata_ghg_formula = "rawdata_ghg_formula"


@router.get("/tables/{table_name}")
async def get_raw_table_data(table_name: RawTable, db: AsyncSession = Depends(get_db), _: User = Depends(get_current_user)):
    try:
        # table_name.value 는 Enum 화이트리스트라 안전한 문자열 → f-string 사용 가능
        query = text(f"SELECT * FROM {table_name.value}")  # 행 수가 적어 LIMIT 없이 전량 조회
        result = await db.execute(query)
        rows = result.mappings().all()
        columns = list(result.keys())
        return {"columns": columns, "rows": [dict(row) for row in rows]}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"RAW 테이블 조회 중 오류 발생: {str(e)}")


# ==================================================================
# (2) RAW 뷰 조회  ── page2 화면이 실제로 호출하는 엔드포인트
# ==================================================================
class RawView(str, Enum):
    RAW1 = "RAW1"
    RAW2 = "RAW2"
    RAW3 = "RAW3"
    RAW4 = "RAW4"
    RAW5 = "RAW5"
    RAW6 = "RAW6"


# ── RAW 별 SQL (화면 "실행 SQL 쿼리" 패널에 그대로 노출 = 단일 소스) ──
RAW_VIEW_SQL: dict = {
    RawView.RAW1: """-- RAW1 · 온실가스 (Scope 1 & 2) 통합
-- rawdata_ghg = rawdata_ghg_quantity ⨝ rawdata_ghg_formula (ON item_name)
--               + 파생 컬럼(energy_usage, ghg_emissions)
WITH joined AS (
    SELECT q.slip_no, q.occur_ym, q.site_name, q.item_name,
           q.collected_qty, q.unit1, q.item_group,
           f.unit2, f.gcv, f.ncv, f.unit3,
           f.ef_co2, f.ef_ch4, f.ef_n2o, f.gwp_co2, f.gwp_ch4, f.gwp_n2o
    FROM   rawdata_ghg_quantity q
    JOIN   rawdata_ghg_formula  f ON q.item_name = f.item_name
),
energy AS (
    SELECT j.*,
           CASE item_name                                  -- 활동량 → 에너지 환산
               WHEN '전력'          THEN collected_qty * POW(10, 6)
               WHEN '도시가스 (LNG)' THEN collected_qty / gcv
               WHEN '휘발유/경유'    THEN collected_qty / gcv
               ELSE collected_qty / 1000
           END AS energy_usage
    FROM   joined j
)
SELECT slip_no, occur_ym, site_name, item_name, collected_qty, unit1, item_group,
       energy_usage, unit2, gcv, ncv, unit3,
       ef_co2, ef_ch4, ef_n2o, gwp_co2, gwp_ch4, gwp_n2o,
       CASE                                                -- Σ(배출계수 × GWP)
           WHEN item_name IN ('도시가스 (LNG)', '휘발유/경유') THEN
                (energy_usage*ncv*ef_co2*gwp_co2
               + energy_usage*ncv*ef_ch4*gwp_ch4
               + energy_usage*ncv*ef_n2o*gwp_n2o) * 1e-6
           WHEN item_name = '전력' THEN
                (energy_usage*gcv*ef_co2*gwp_co2
               + energy_usage*gcv*ef_ch4*gwp_ch4
               + energy_usage*gcv*ef_n2o*gwp_n2o) * 1e-6
           WHEN item_name = '스팀 (유연탄 (연료용))' THEN
                (energy_usage*ef_co2*gwp_co2
               + energy_usage*ef_ch4*gwp_ch4
               + energy_usage*ef_n2o*gwp_n2o) * 1e-6
           ELSE energy_usage * ncv * ef_co2 * ef_n2o       -- SF6, HFCs
       END AS ghg_emissions
FROM   energy
ORDER  BY slip_no;""",
    RawView.RAW2: """-- RAW2 · 전력 사용량 (Scope 2)
-- rawdata_ghg 중 item_name = '전력'
WITH j AS (
    SELECT q.*, f.unit2, f.gcv, f.ncv, f.unit3,
           f.ef_co2, f.ef_ch4, f.ef_n2o, f.gwp_co2, f.gwp_ch4, f.gwp_n2o
    FROM   rawdata_ghg_quantity q
    JOIN   rawdata_ghg_formula  f ON q.item_name = f.item_name
    WHERE  q.item_name = '전력'
)
SELECT slip_no, occur_ym, site_name, item_name, collected_qty, unit1, item_group,
       collected_qty * POW(10, 6) AS energy_usage,
       unit2, gcv, ncv, unit3, ef_co2, ef_ch4, ef_n2o, gwp_co2, gwp_ch4, gwp_n2o,
       ( (collected_qty*POW(10,6))*gcv*ef_co2*gwp_co2
       + (collected_qty*POW(10,6))*gcv*ef_ch4*gwp_ch4
       + (collected_qty*POW(10,6))*gcv*ef_n2o*gwp_n2o ) * 1e-6 AS ghg_emissions
FROM   j
ORDER  BY slip_no;""",
    RawView.RAW3: """-- RAW3 · 스팀 사용량 (Scope 2)
-- rawdata_ghg 중 item_name = '스팀 (유연탄 (연료용))'
WITH j AS (
    SELECT q.*, f.unit2, f.gcv, f.ncv, f.unit3,
           f.ef_co2, f.ef_ch4, f.ef_n2o, f.gwp_co2, f.gwp_ch4, f.gwp_n2o
    FROM   rawdata_ghg_quantity q
    JOIN   rawdata_ghg_formula  f ON q.item_name = f.item_name
    WHERE  q.item_name = '스팀 (유연탄 (연료용))'
)
SELECT slip_no, occur_ym, site_name, item_name, collected_qty, unit1, item_group,
       collected_qty / 1000 AS energy_usage,
       unit2, gcv, ncv, unit3, ef_co2, ef_ch4, ef_n2o, gwp_co2, gwp_ch4, gwp_n2o,
       ( (collected_qty/1000)*ef_co2*gwp_co2
       + (collected_qty/1000)*ef_ch4*gwp_ch4
       + (collected_qty/1000)*ef_n2o*gwp_n2o ) * 1e-6 AS ghg_emissions
FROM   j
ORDER  BY slip_no;""",
    RawView.RAW4: """-- RAW4 · 폐기물 배출 (일반 / 지정 / 재활용 / 매립)
SELECT slip_no, occur_ym, site_name, item_name,
       collected_qty, unit, category, item_group
FROM   rawdata_erp
WHERE  item_name IN ('일반폐기물 발생량', '지정폐기물 발생량',
                     '폐기물 재활용량', '폐기물 매립량')
ORDER  BY slip_no;""",
    RawView.RAW5: """-- RAW5 · 용수 / 수자원 (취수 / 재이용)
SELECT slip_no, occur_ym, site_name, item_name,
       collected_qty, unit, category, item_group
FROM   rawdata_erp
WHERE  item_name IN ('공업용수 취수량', '용수 재이용량')
ORDER  BY slip_no;""",
    RawView.RAW6: """-- RAW6 · 폐수 및 수질 (방류량)
SELECT slip_no, occur_ym, site_name, item_name,
       collected_qty, unit, category, item_group
FROM   rawdata_erp
WHERE  item_name = '폐수 방류량'
ORDER  BY slip_no;""",
}

RAW_VIEW_SOURCE: dict = {
    RawView.RAW1: "rawdata_ghg_quantity ⨝ rawdata_ghg_formula",
    RawView.RAW2: "rawdata_ghg_quantity ⨝ rawdata_ghg_formula",
    RawView.RAW3: "rawdata_ghg_quantity ⨝ rawdata_ghg_formula",
    RawView.RAW4: "rawdata_erp",
    RawView.RAW5: "rawdata_erp",
    RawView.RAW6: "rawdata_erp",
}


@router.get("/raw/{raw_type}")
async def get_raw_view_data(raw_type: RawView, db: AsyncSession = Depends(get_db), _: User = Depends(get_current_user)):
    """
    RAW1~6 뷰 데이터를 반환한다.
    - raw_type 은 Enum 화이트리스트, SQL 은 서버 상수(사용자 입력 미포함)라 인젝션 위험 없음.
    - RAW1 은 CTE(WITH)를 사용하므로 MySQL 8.0+ 필요.
    """
    sql_display = RAW_VIEW_SQL[raw_type]              # 패널 표시용 (주석·세미콜론 포함)
    sql_exec = sql_display.rstrip().rstrip(";")       # 실행용 (말미 세미콜론 제거)
    try:
        result = await db.execute(text(sql_exec))
        rows = result.mappings().all()
        columns = list(result.keys())
        return {
            "raw_type": raw_type.value,
            "source": RAW_VIEW_SOURCE[raw_type],
            "sql": sql_display,                       # → 프런트가 그대로 패널에 렌더
            "columns": columns,
            "rows": [dict(row) for row in rows],
        }
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"RAW 뷰({raw_type.value}) 조회 중 오류 발생: {str(e)}",
        )

# ==================================================================
# (3) AI 그래프 스펙 생성  ── page2 "AI 그래프 생성" 패널이 호출
# ------------------------------------------------------------------
# 현재 화면에 표시된 데이터(필터·정렬 적용 행 샘플)를 받아
# config.py 의 RAG_LLM_MODEL(AWS Bedrock)로 "어떤 그래프를 그릴지"
# 스펙(JSON)만 결정한다. 실제 집계·렌더링은 프런트(Chart.js)가 하므로
# LLM 이 수치를 직접 계산하지 않아 정확도 저하가 없다.
# 반환 스펙은 프런트에서 aggregateForChart() → Chart.js 로 사용된다.
# ==================================================================
class ChartRequest(BaseModel):
    raw_type: str                       # RAW1~6 (화면 표기용, 검증엔 미사용)
    columns: list[str]                  # 현재 뷰의 컬럼 목록(화이트리스트)
    rows: list[dict[str, Any]]          # 화면 표시 행 샘플(최대 40행)
    row_count: int                      # 실제 표시 행 총계(참고용)
    site: Optional[str] = None          # 사업장 필터 컨텍스트
    period: Optional[str] = None        # 기간 필터 컨텍스트


ALLOWED_CHART_TYPES = {"bar", "line", "pie", "stacked-bar", "grouped-bar"}
ALLOWED_AGG = {"sum", "avg", "count"}

# RAW 유형별 도메인 힌트(프롬프트에 주입 → 더 적절한 그래프 선택 유도)
RAW_HINT: dict = {
    "RAW1": "온실가스(Scope1&2) 통합. 수치 컬럼: ghg_emissions(tCO2eq), energy_usage. 범주: site_name, item_name, occur_ym(월).",
    "RAW2": "전력 사용량(Scope2). 수치: energy_usage, ghg_emissions. 범주: site_name, occur_ym.",
    "RAW3": "스팀 사용량(Scope2). 수치: energy_usage, ghg_emissions. 범주: site_name, occur_ym.",
    "RAW4": "폐기물 배출(일반/지정/재활용/매립). 수치: collected_qty. 범주: site_name, item_name, category, occur_ym.",
    "RAW5": "용수/수자원(취수/재이용). 수치: collected_qty. 범주: site_name, item_name, occur_ym.",
    "RAW6": "폐수/수질(방류량). 수치: collected_qty. 범주: site_name, occur_ym.",
}


def _build_chart_prompt(req: ChartRequest) -> str:
    sample = json.dumps(req.rows[:30], ensure_ascii=False, default=str)
    hint = RAW_HINT.get(req.raw_type, "")
    return f"""ESG 데이터를 시각화 하는 작업이다.
아래는 ESG 대시보드에서 사용자가 현재 보고 있는 '{req.raw_type}' 데이터입니다.
이 데이터를 ESG 보고서에 넣을 그래프 1개로 요약하려 합니다.
어떤 그래프가 가장 적절한지 '구조'만 결정하세요. (실제 수치 집계는 프런트엔드가 수행합니다.)

[데이터 개요]
- RAW 유형: {req.raw_type}
- 도메인 힌트: {hint}
- 사업장 필터: {req.site}
- 기간 필터: {req.period}
- 전체 표시 행 수: {req.row_count}
- 컬럼 목록: {req.columns}
- 샘플 데이터(최대 30행): {sample}

[선택 규칙]
- x_field: 범주형/시간형 컬럼 중 하나 (예: site_name, item_name, occur_ym, category, item_group).
- y_field: 수치형 컬럼 중 하나 (예: ghg_emissions, energy_usage, collected_qty). agg="count" 이면 생략 가능.
- 시계열 추세는 occur_ym 을 x 로 하는 line 을 우선 고려.
- 사업장/품목 비교는 site_name 또는 item_name 을 x 로 하는 bar 를 우선 고려.
- 여러 계열 비교가 유익하면 group_by 에 두 번째 범주 컬럼을 지정하고 stacked-bar/grouped-bar/line 사용.
- 반드시 위 '컬럼 목록'에 존재하는 컬럼명만 사용.
- 제목/축라벨/인사이트는 한국어. y_label 에는 단위 포함(예: '배출량 (tCO2eq)').
- insight 는 이 그래프가 무엇을 보여주는지 2~3문장으로 ESG 보고서 톤으로 작성.

[출력 형식] 아래 JSON 만 출력하세요. 마크다운/설명/코드펜스 금지.
{{
  "chart_type": "bar|line|pie|stacked-bar|grouped-bar",
  "title": "그래프 제목",
  "x_field": "컬럼명",
  "y_field": "컬럼명 또는 null",
  "agg": "sum|avg|count",
  "group_by": "컬럼명 또는 null",
  "x_label": "X축 라벨",
  "y_label": "Y축 라벨(단위 포함)",
  "insight": "2~3문장 요약"
}}"""


def _extract_text(resp: Any) -> str:
    """LLM 응답에서 텍스트만 추출.
    - anthropic SDK: resp.content 는 TextBlock 객체 리스트(.type=='text', .text).
      확장 사고 사용 시 thinking 블록이 앞에 올 수 있어 type=='text' 만 취한다.
    - 문자열 content 나 dict 블록도 방어적으로 처리.
    """
    content = getattr(resp, "content", resp)
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return str(content)
    parts: list[str] = []
    for block in content:
        if isinstance(block, str):
            parts.append(block)
            continue
        # 객체(.type/.text) 우선, 없으면 dict 로 조회
        btype = getattr(block, "type", None)
        text = getattr(block, "text", None)
        if btype is None and isinstance(block, dict):
            btype = block.get("type", "text")
            text = block.get("text")
        if btype == "text" and text is not None:
            parts.append(str(text))
    return "".join(parts)


def _parse_llm_json(text_out: str) -> dict:
    """LLM 응답에서 JSON 본문만 안전하게 추출."""
    s = (text_out or "").strip()
    if s.startswith("```"):                       # 코드펜스 제거
        s = s.strip("`")
        nl = s.find("\n")
        if nl != -1 and s[:nl].strip().lower() in ("json", ""):
            s = s[nl + 1:]
    i, j = s.find("{"), s.rfind("}")              # 첫 { ~ 마지막 }
    if i != -1 and j != -1 and j > i:
        s = s[i:j + 1]
    return json.loads(s)


@router.post("/chart")
async def generate_chart_spec(req: ChartRequest, _: User = Depends(get_current_user)):
    """
    현재 화면 데이터로부터 그래프 스펙(JSON)을 생성한다.
    - RAG_LLM_MODEL(AWS Bedrock) 사용. LLM 은 그래프 '구조'만 결정.
    - 필드는 서버에서 컬럼 화이트리스트로 재검증(프런트 렌더 안정화).
    """
    if not req.rows:
        raise HTTPException(status_code=400, detail="표시된 데이터가 없어 그래프를 생성할 수 없습니다.")

    try:
        # settings.RAG_LLM_MODEL = "us.anthropic.claude-sonnet-4-6" (Bedrock 교차리전 추론 프로파일).
        # 인증: config 가 os.environ["AWS_BEARER_TOKEN_BEDROCK"] 를 세팅 → SDK 가 자동으로 읽어
        #       Authorization: Bearer 헤더로 호출(별도 AWS 자격증명/boto3 불필요).
        # 커넥션 누수 방지를 위해 async with 사용(고성능이 필요하면 모듈 단위 싱글턴으로 승격 가능).
        async with AsyncAnthropicBedrock(aws_region=settings.AWS_REGION) as client:
            msg = await client.messages.create(
                model=settings.RAG_LLM_MODEL,
                max_tokens=800,
                temperature=0,
                messages=[{"role": "user", "content": _build_chart_prompt(req)}],
            )
        raw_text = _extract_text(msg)           # content 는 TextBlock 리스트이므로 text만 추출
        spec = _parse_llm_json(raw_text)
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=502, detail=f"LLM 응답(JSON) 파싱 실패: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"그래프 스펙 생성 실패: {str(e)}")

    # ── 서버측 유효성 검증 ──────────────────────────────────────
    cols = set(req.columns)

    ctype = str(spec.get("chart_type", "bar")).lower()
    spec["chart_type"] = ctype if ctype in ALLOWED_CHART_TYPES else "bar"

    agg = str(spec.get("agg", "sum")).lower()
    spec["agg"] = agg if agg in ALLOWED_AGG else "sum"

    if spec.get("x_field") not in cols:
        raise HTTPException(status_code=422, detail=f"AI가 유효하지 않은 x_field 를 반환했습니다: {spec.get('x_field')}")
    if spec["agg"] != "count" and spec.get("y_field") not in cols:
        raise HTTPException(status_code=422, detail=f"AI가 유효하지 않은 y_field 를 반환했습니다: {spec.get('y_field')}")
    if spec.get("group_by") and spec["group_by"] not in cols:
        spec["group_by"] = None

    return spec
