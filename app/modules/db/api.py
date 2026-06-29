# DB 확인 시각화 요청을 처리하는 API 엔드포인트를 정의하는 파일

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from enum import Enum
# local
from app.db.models.database import get_db
from app.db.models.user import User
from app.modules.auth.dependency import get_current_user


router = APIRouter()

# 허용된 테이블 목록
class HynixTable(str, Enum):
    sk_hynix_e = "sk_hynix_e"
    sk_hynix_s = "sk_hynix_s"
    sk_hynix_g = "sk_hynix_g"
    sk_hynix_newsroom = "sk_hynix_newsroom"
    sk_hynix_press = "sk_hynix_press"
    sk_hynix_report = "sk_hynix_report"


@router.get("/tables/{table_name}")
async def get_table_data(table_name: HynixTable, db: AsyncSession = Depends(get_db), _: User = Depends(get_current_user)):
    try:
        # 주의: table_name.value는 Enum으로 100% 안전함이 보장된 문자열이므로 f-string 사용 가능
        query = text(f"SELECT * FROM {table_name.value}") ### LIMIT 10 으로 확인, row수가 많지 않아서 다 가져와도 무방함
        result = await db.execute(query)

        # 결과를 딕셔너리 리스트로 변환, rows 추출
        rows = result.mappings().all()
        # columns 추출
        columns = list(result.keys())

        processed_rows = []

        for row in rows:
            row_dict = dict(row)

            # 특정 테이블만 내용만 truncate, 뉴스기사는 url 링크를 통해 모든 내용 확인 가능
            if table_name in [HynixTable.sk_hynix_newsroom, HynixTable.sk_hynix_press, HynixTable.sk_hynix_report]:
                if row_dict.get("content"):
                    row_dict["content"] = row_dict["content"][:50] + "..."

            processed_rows.append(row_dict)

        return {
            # "status": "success", 
            # "table_selected": table_name.value,
            "columns": columns,
            "rows": processed_rows
        }
                
    except Exception as e:
        # 만약 테이블 구조에 문제가 있거나 DB 에러가 날 경우
        raise HTTPException(status_code=500, detail=f"DB 조회 중 오류 발생: {str(e)}")