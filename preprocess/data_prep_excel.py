import pandas as pd
from sqlalchemy import create_engine
# local
from app.core.config import settings


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
    print(df.head())
    df.to_sql(name=table_name, con=engine, if_exists='append', index=False)

print("Done")