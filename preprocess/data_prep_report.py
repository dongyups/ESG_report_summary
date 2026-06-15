'''
pip install kiwipiepy
'''
import pandas as pd
from sqlalchemy import create_engine
from kiwipiepy import Kiwi
from tqdm import tqdm
# local
from app.core.config import settings


# async -> sync
engine = create_engine(settings.DATABASE_URL.replace("aiomysql", "pymysql")) # "mysql+pymysql://root:1111@127.0.0.1:3306/esg_db"

# 데이터
# 컬럼명 리스트: company	report_year	source	esg_category	section	page_num	chunk_index	chunk_id	heading_level_1	heading_level_2	heading_level_3	content_type	table_title	content
# SQL에 올리고 나중에 ChromaDB로 id=chunk_id / embeddings / documents=[content 컬럼] / metadatas=[{나머지},{컬럼들},...]
# 그리고 스크랩된 pdf 내용의 경우 문단이 나눠지면서 혹은 기존의 글이 띄어쓰기 문제가 있는 경우 kiwipiepy 라이브러리로 전처리
kiwi = Kiwi()

data_list = {
    "sk_hynix_report": "./datasets/content_db.xlsx",
}

for table_name, file_path in data_list.items():
    df = pd.read_excel(file_path, header=0)
    
    # 현재 chunk_id가 2024_p1_c1 이런 형식이어서 sk_hynix_2024_p1_c1 으로 회사명을 붙여서 ChromaDB id로 활용
    df['chunk_id'] = [f"sk_hynix_{val}" for val in df['chunk_id']]
    
    # content_type이 text인 경우에만 처리
    mask = df['content_type'] == 'text'
    df.loc[mask, 'content'] = list(kiwi.space(df.loc[mask, 'content'], reset_whitespace=True))

    # export
    df.to_sql(name=table_name, con=engine, if_exists='replace', index=False)

print("Done")