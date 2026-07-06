import pandas as pd
from sqlalchemy import create_engine
# local
from app.core.config import settings


# async -> sync
engine = create_engine(settings.DATABASE_URL.replace("aiomysql", "pymysql")) # "mysql+pymysql://root:1111@127.0.0.1:3306/esg_db"

# 데이터 목록
data_list = {
    "rawdata_erp": "./datasets/RAWDATA_ERP.csv",
    ### RAWDATA_GHG.xlsx 이 형태로 만들기 ###
    "rawdata_ghg_formula": "./datasets/RAWDATA_GHG_FORMULA.csv",
    "rawdata_ghg_quantity": "./datasets/RAWDATA_GHG_QUANTITY.csv",
}

# 날짜 형태 변환
def to_yyyymm(s):
    """엑셀 표시서식('Jan-24' 등) 포함 여러 형식을 'YYYY-MM'으로 표준화"""
    s = str(s).strip()
    for fmt in ('%b-%y', '%Y-%m', '%y-%b', '%Y-%m-%d'):
        try:
            return pd.to_datetime(s, format=fmt).strftime('%Y-%m')
        except (ValueError, TypeError):
            continue
    return s   # 못 알아보면 원본 유지(회귀 방지)

# 저장
for table_name, file_path in data_list.items():
    df = pd.read_csv(file_path, header=0, encoding="cp949")

    if table_name in ["rawdata_erp", "rawdata_ghg_quantity"]:
        df['collected_qty'] = df['collected_qty'].str.replace(',', '')
        df['collected_qty'] = df['collected_qty'].astype(float)
        df['occur_ym'] = df['occur_ym'].map(to_yyyymm) # 'Jan-24' → '2024-01'

    if table_name in ["rawdata_ghg_formula"]:
        df['ef_co2'] = df['ef_co2'].str.replace(',', '')
        df['ef_co2'] = df['ef_co2'].astype(float)
        df['ef_n2o'] = df['ef_n2o'].str.replace(',', '')
        df['ef_n2o'] = df['ef_n2o'].astype(float)
        
    print(df.head())
    df.to_sql(name=table_name, con=engine, if_exists='replace', index=False)

print("Done")