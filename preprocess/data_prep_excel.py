import pandas as pd
from sqlalchemy import create_engine
# local
from app.core.config import settings


### 출처: https://sustainability.skhynix.com/datacenter ###
'''
DB에 업로드 후 활용됨을 고려하여 한국어로 된 다운로드된 데이터의 컬럼명을 영어로 우선적으로 변환하였음
- 대분류 중분류 소분류 세분류 단위 ==> category_level_1 category_level_2 category_level_3 category_level_4 unit
- id컬럼 추가: 1,2,3,4,5,...
- 2019, 2020, ... ==> value_2019, value_2020
'''
# async -> sync
engine = create_engine(settings.DATABASE_URL.replace("aiomysql", "pymysql")) # "mysql+pymysql://root:1111@127.0.0.1:3306/esg_db"

# 데이터 목록
data_list = {
    "sk_hynix_e": "./datasets/SK_하이닉스_성과_및_실적_데이터_환경(E)_2019-2024.xlsx",
    "sk_hynix_s": "./datasets/SK_하이닉스_성과_및_실적_데이터_사회(S)_2019-2024.xlsx",
    "sk_hynix_g": "./datasets/SK_하이닉스_성과_및_실적_데이터_지배구조(G)_2019-2024.xlsx",
}

for table_name, file_path in data_list.items():
    df = pd.read_excel(file_path, header=0)
    # 새 컬럼 추가
    if table_name == "sk_hynix_e":
        df['esg_category'] = "E"
    elif table_name == "sk_hynix_s":
        df['esg_category'] = "S"
    elif table_name == "sk_hynix_g":
        df['esg_category'] = "G"
    df = df[['id', 'company', 'site', 'area', 'esg_category', 
             'category_level_1', 'category_level_2', 'category_level_3', 'category_level_4', 
             'unit', 'value_2019', 'value_2020', 'value_2021', 'value_2022', 'value_2023', 'value_2024']]
    print(df.head())
    df.to_sql(name=table_name, con=engine, if_exists='replace', index=False)

print("Done")