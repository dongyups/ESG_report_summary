#!/usr/bin/env python3
"""
SK Hynix ESG 뉴스 크롤러
뉴스룸과 보도자료의 ESG 관련 기사를 크롤링하여 MySQL에 저장
"""

import requests, time, re
from bs4 import BeautifulSoup
from datetime import datetime
from sqlalchemy import create_engine, text
from typing import List, Dict, Optional
# local
from app.core.config import settings


# async -> sync
engine = create_engine(settings.DATABASE_URL.replace("aiomysql", "pymysql")) # "mysql+pymysql://root:1111@127.0.0.1:3306/esg_db"

class SKHynixESGCrawler:
    def __init__(self):
        self.base_url = "https://news.skhynix.co.kr"
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        })
        self.engine = engine
        
    def get_current_date_params(self) -> tuple:
        """현재 날짜를 기준으로 시작/종료 년월 반환"""
        now = datetime.now()
        start_date = f"{int(now.year)-6}.1" # 대략 6년전
        end_date = f"{now.year}.{now.month}"
        return start_date, end_date
    
    def fetch_page(self, page: int, article_type: str) -> Optional[str]:
        """특정 페이지의 HTML 가져오기"""
        start_date, end_date = self.get_current_date_params()
        url = f"{self.base_url}/page/{page}/"
        params = {
            's': 'ESG',
            'type': article_type,
            'order': 'date',
            'start_date': start_date,
            'end_date': end_date
        }
        
        try:
            response = self.session.get(url, params=params, timeout=30)
            response.raise_for_status()
            return response.text
        except requests.RequestException as e:
            print(f"Error fetching page {page} for {article_type}: {e}")
            return None
    
    def parse_articles(self, html: str) -> List[Dict]:
        """HTML에서 기사 정보 추출"""
        soup = BeautifulSoup(html, 'html.parser')
        articles = []
        
        # 기사 목록 찾기 (li 태그로 구성)
        article_items = soup.select('ul.content_wrap li') or soup.select('div.content_wrap article')
        
        for item in article_items:
            try:
                # URL 추출
                link_tag = item.find('a', href=True)
                if not link_tag:
                    continue
                article_url = link_tag['href']
                if not article_url.startswith('http'):
                    article_url = self.base_url + article_url
                
                # 제목 추출
                title_tag = item.find('h3') or item.find('h2') or item.find('p', class_='title')
                if not title_tag:
                    continue
                title = title_tag.get_text(strip=True)
                
                # 날짜 추출
                date_tag = item.find('time') or item.find('span', class_='date') or item.find('p', class_='date')
                if not date_tag:
                    continue
                date_str = date_tag.get_text(strip=True)
                # 날짜 형식 변환 (YYYY-MM-DD)
                date_match = re.search(r'(\d{4})-(\d{2})-(\d{2})', date_str)
                if not date_match:
                    continue
                article_date = f"{date_match.group(1)}-{date_match.group(2)}-{date_match.group(3)}"
                
                # 카테고리 추출
                categories = []
                category_tags = item.find_all('a', href=re.compile(r'/category/'))
                for cat_tag in category_tags:
                    cat_text = cat_tag.get_text(strip=True)
                    if cat_text:
                        categories.append(cat_text)
                category_str = ','.join(categories) if categories else ''
                
                # 해시태그 추출
                hashtags = []
                hashtag_tags = item.find_all('a', href=re.compile(r'/tag/'))
                for tag in hashtag_tags:
                    tag_text = tag.get_text(strip=True)
                    if tag_text:
                        hashtags.append(tag_text)
                hashtag_str = ','.join(hashtags) if hashtags else ''
                
                article = {
                    'date': article_date,
                    'title': title,
                    'category': category_str,
                    'hashtag': hashtag_str,
                    'url': article_url
                }
                articles.append(article)
                
            except Exception as e:
                print(f"Error parsing article item: {e}")
                continue
        
        return articles
    
    def has_next_page(self, html: str) -> bool:
        """다음 페이지가 있는지 확인"""
        soup = BeautifulSoup(html, 'html.parser')
        # 페이지네이션에서 다음 페이지 링크 확인
        pagination = soup.find('div', class_='pagination') or soup.find('nav', class_='pagination')
        if pagination:
            next_link = pagination.find('a', string=re.compile(r'다음|next', re.I))
            return next_link is not None
        return False
    
    def crawl_articles(self, article_type: str) -> List[Dict]:
        """특정 타입의 모든 기사 크롤링"""
        all_articles = []
        page = 1
        
        print(f"\n{article_type} 크롤링 시작...")
        
        while True:
            print(f"  페이지 {page} 크롤링 중...")
            html = self.fetch_page(page, article_type)
            
            if not html:
                break
            
            articles = self.parse_articles(html)
            if not articles:
                # 기사가 없으면 다음 페이지 확인
                if not self.has_next_page(html):
                    break
            else:
                all_articles.extend(articles)
                print(f"    {len(articles)}개 기사 파싱 완료")
            
            # 다음 페이지 확인
            if not self.has_next_page(html):
                break
            
            page += 1
            time.sleep(1)  # 서버 부하 방지
        
        print(f"{article_type} 크롤링 완료: 총 {len(all_articles)}개 기사")
        return all_articles
    
    def create_tables(self):
        """MySQL 테이블 생성"""        
        tables = {
            'sk_hynix_newsroom': """
                CREATE TABLE IF NOT EXISTS sk_hynix_newsroom (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    date DATE NOT NULL,
                    title TEXT NOT NULL,
                    category VARCHAR(500),
                    hashtag TEXT,
                    url VARCHAR(750) NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    INDEX idx_date (date),
                    UNIQUE KEY unique_url (url(250))
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """,
            'sk_hynix_press': """
                CREATE TABLE IF NOT EXISTS sk_hynix_press (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    date DATE NOT NULL,
                    title TEXT NOT NULL,
                    category VARCHAR(500),
                    hashtag TEXT,
                    url VARCHAR(750) NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    INDEX idx_date (date),
                    UNIQUE KEY unique_url (url(250))
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """
        }
        with self.engine.connect() as conn:
            for table_name, create_sql in tables.items():
                conn.execute(text(create_sql))  # text()로 감싸기
                conn.commit()
                print(f"테이블 '{table_name}' 생성/확인 완료")
                
    
    def insert_articles(self, articles: List[Dict], table_name: str):
        """기사를 MySQL에 저장"""
        if not articles:
            return
        
        insert_sql = f"""
            INSERT INTO {table_name} (date, title, category, hashtag, url)
            VALUES (%s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                date = VALUES(date),
                title = VALUES(title),
                category = VALUES(category),
                hashtag = VALUES(hashtag)
        """
        
        inserted = 0
        updated = 0
        
        with self.engine.connect() as conn:
            for article in articles:
                try:
                    result = conn.execute(text(insert_sql), (
                        article['date'],
                        article['title'],
                        article['category'],
                        article['hashtag'],
                        article['url']
                    ))
                    if result.rowcount == 1:
                        inserted += 1
                    elif result.rowcount == 2:
                        updated += 1
                except Exception as e:
                    print(f"Error inserting article: {e}")
                    print(f"Article: {article}")
            
            conn.commit()
        
        print(f"{table_name}: {inserted}개 신규 저장, {updated}개 업데이트")
    

    def run(self):
        """전체 크롤링 실행"""
        print("=" * 60)
        print("SK Hynix ESG 뉴스 크롤러 시작")
        print("=" * 60)
        
        # 테이블 생성
        print("\n테이블 생성/확인 중...")
        self.create_tables()
        
        # 뉴스룸 기사 크롤링
        newsroom_articles = self.crawl_articles('newsroom')
        if newsroom_articles:
            self.insert_articles(newsroom_articles, 'sk_hynix_newsroom')
        
        # 보도자료 크롤링
        press_articles = self.crawl_articles('press')
        if press_articles:
            self.insert_articles(press_articles, 'sk_hynix_press')
        
        print("\n" + "=" * 60)
        print("크롤링 완료!")
        print(f"뉴스룸: {len(newsroom_articles)}개")
        print(f"보도자료: {len(press_articles)}개")
        print("=" * 60)



# 크롤러 실행
crawler = SKHynixESGCrawler()
crawler.run()