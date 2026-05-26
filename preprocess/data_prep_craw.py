#!/usr/bin/env python3
"""
SK Hynix ESG 뉴스 크롤러 (Selenium 버전)
뉴스룸과 보도자료의 ESG 관련 기사를 크롤링하여 MySQL에 저장
"""

from selenium import webdriver
from selenium.webdriver.common.by import By
# from selenium.webdriver.support.ui import WebDriverWait
# from selenium.webdriver.support import expected_conditions as EC
# from selenium.common.exceptions import TimeoutException, NoSuchElementException
from selenium.webdriver.chrome.options import Options
import re, time
from datetime import datetime
from typing import List, Dict
from sqlalchemy import create_engine, text
# local
from app.core.config import settings



class SKHynixESGCrawler:
    def __init__(self, engine):
        self.base_url = "https://news.skhynix.co.kr"
        self.engine = engine
        self.driver = None
        
    def init_driver(self):
        """Selenium 드라이버 초기화"""
        chrome_options = Options()
        chrome_options.add_argument('--headless')  # 브라우저 창 숨기기
        chrome_options.add_argument('--no-sandbox')
        chrome_options.add_argument('--disable-dev-shm-usage')
        chrome_options.add_argument('--disable-gpu')
        chrome_options.add_argument('--window-size=1920,1080')
        chrome_options.add_argument('user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36')
        
        self.driver = webdriver.Chrome(options=chrome_options)
        self.driver.implicitly_wait(10)
        
    def close_driver(self):
        """드라이버 종료"""
        if self.driver:
            self.driver.quit()
            self.driver = None
    
    def get_current_date_params(self) -> tuple:
        """현재 날짜를 기준으로 시작/종료 년월 반환"""
        now = datetime.now()
        start_date = f"{int(now.year)-6}.1" # 대략 6년전
        end_date = f"{now.year}.{now.month}"
        return start_date, end_date
    
    def fetch_page(self, page: int, article_type: str) -> bool:
        """특정 페이지로 이동"""
        start_date, end_date = self.get_current_date_params()
        url = f"{self.base_url}/page/{page}/"
        params = f"?s=ESG&type={article_type}&order=date&start_date={start_date}&end_date={end_date}"
        full_url = url + params
        
        try:
            self.driver.get(full_url)
            # 페이지 로딩 대기
            time.sleep(2)
            return True
        except Exception as e:
            print(f"Error fetching page {page} for {article_type}: {e}")
            return False
    
    def parse_articles(self) -> List[Dict]:
        """현재 페이지에서 기사 정보 추출"""
        articles = []
        
        try:
            # 기사 항목들 찾기 - 여러 가능한 셀렉터 시도
            selectors = [
                # "li.item",
                "article.item",
                # ".content_wrap li",
                # ".content_wrap article",
                # "li[class*='post']",
                # "article[class*='post']"
            ]
            
            article_elements = []
            for selector in selectors:
                try:
                    elements = self.driver.find_elements(By.CSS_SELECTOR, selector)
                    if elements:
                        article_elements = elements
                        # print(f"    ### 셀렉터 '{selector}'로 {len(elements)}개 요소 발견")
                        break
                except:
                    continue
            
            if not article_elements:
                print("    기사 요소를 찾을 수 없습니다")
                return articles
            
            for elem in article_elements:
                try:
                    # URL 추출
                    try:
                        link_elem = elem.find_element(By.TAG_NAME, 'a')
                        article_url = link_elem.get_attribute('href')
                    except:
                        continue
                    
                    if not article_url or 'news.skhynix.co.kr' not in article_url:
                        continue
                    
                    # 제목 추출
                    try:
                        title_elem = elem.find_element(By.CSS_SELECTOR, 'h3, h2, .title, p.title')
                        title = title_elem.text.strip()
                    except:
                        title = elem.text.split('\n')[0].strip()
                    
                    if not title:
                        continue
                    
                    # 날짜 추출
                    try:
                        date_elem = elem.find_element(By.CSS_SELECTOR, 'time, .date, p.date, span.date')
                        date_str = date_elem.text.strip() or date_elem.get_attribute('datetime')
                    except:
                        # 텍스트에서 날짜 패턴 찾기
                        text = elem.text
                        date_match = re.search(r'(\d{4})-(\d{2})-(\d{2})', text)
                        if date_match:
                            date_str = date_match.group(0)
                        else:
                            continue
                    
                    # 날짜 형식 정규화
                    date_match = re.search(r'(\d{4})-(\d{2})-(\d{2})', date_str)
                    if not date_match:
                        continue
                    article_date = f"{date_match.group(1)}-{date_match.group(2)}-{date_match.group(3)}"
                    
                    # 카테고리 추출
                    categories = []
                    try:
                        cat_elements = elem.find_elements(By.CSS_SELECTOR, 'a[href*="/category/"]')
                        for cat_elem in cat_elements:
                            cat_text = cat_elem.text.strip()
                            if cat_text:
                                categories.append(cat_text)
                    except:
                        pass
                    category_str = ','.join(categories)
                    
                    # 해시태그 추출
                    hashtags = []
                    try:
                        tag_elements = elem.find_elements(By.CSS_SELECTOR, 'a[href*="/tag/"]')
                        for tag_elem in tag_elements:
                            tag_text = tag_elem.text.strip()
                            if tag_text:
                                hashtags.append(tag_text)
                    except:
                        pass
                    hashtag_str = ','.join(hashtags)
                    
                    article = {
                        'date': article_date,
                        'title': title,
                        'category': category_str,
                        'hashtag': hashtag_str,
                        'url': article_url
                    }
                    articles.append(article)
                    
                except Exception as e:
                    print(f"    기사 파싱 중 에러: {e}")
                    continue
        
        except Exception as e:
            print(f"    페이지 파싱 에러: {e}")
        
        return articles
    
    def has_next_page(self) -> bool:
        """다음 페이지가 있는지 확인"""
        try:
            # 다음 페이지 버튼 찾기
            next_selectors = [
                'a.next',
                # 'a[rel="next"]',
                # '.pagination a[aria-label*="next"]',
                # '.pagination a:contains("다음")',
                # 'nav a:contains("다음")'
            ]
            
            for selector in next_selectors:
                try:
                    next_btn = self.driver.find_element(By.CSS_SELECTOR, selector)
                    if next_btn and next_btn.is_displayed():
                        # print(f"    ### 버튼 '{selector}' 요소")
                        return True
                except:
                    continue
            
            # 페이지 번호로 확인
            try:
                page_links = self.driver.find_elements(By.CSS_SELECTOR, '.pagination a, nav a')
                current_page_text = self.driver.find_element(By.CSS_SELECTOR, '.current, .active').text
                page_numbers = [int(re.search(r'\d+', a.text).group()) for a in page_links if re.search(r'\d+', a.text)]
                if page_numbers and int(current_page_text) < max(page_numbers):
                    return True
            except:
                pass
            
            return False
        except:
            return False
    
    def fetch_article_content(self, url: str) -> str:
        """개별 기사 페이지에서 본문 내용 추출"""
        try:
            self.driver.get(url)
            time.sleep(2)  # 페이지 로딩 대기
            
            # 본문 영역 찾기 - 여러 셀렉터 시도
            content_selectors = [
                # 'article .content',
                # '.post-content',
                # '.entry-content',
                # 'article .entry',
                # '.article-content',
                'main article',
                # 'article',
                # '.content'
            ]
            
            content_text = ""
            for selector in content_selectors:
                try:
                    content_elem = self.driver.find_element(By.CSS_SELECTOR, selector)
                    if content_elem:
                        # print(f"    ### 내용 '{selector}' 요소")
                        # 스크립트, 스타일 태그 제외하고 텍스트 추출
                        content_text = content_elem.text.strip()

                        ### 간단한 클리어링 ###
                        content_text = re.split(r"공유\s*하기\s*\n?", content_text, 1)[-1] # 앞단 제거
                        content_text = re.split(r"\s*이미지 다운로드\s*", content_text, maxsplit=1)[0].strip() # 뒷단 제거
                        content_text = re.sub(r"^▲.*\n?", "", content_text, flags=re.MULTILINE)
                        # print(f"    ### 내용 '{content_text}'")
                        if len(content_text) > 100:  # 최소 길이 확인
                            break
                except:
                    continue
            
            # 본문을 찾지 못한 경우 body 전체에서 추출
            if not content_text or len(content_text) < 100:
                try:
                    body = self.driver.find_element(By.TAG_NAME, 'body')
                    content_text = body.text.strip()
                except:
                    pass
            
            return content_text
            
        except Exception as e:
            print(f"    본문 추출 실패 ({url}): {e}")
            return ""
    
    def enrich_articles_with_content(self, articles: List[Dict]) -> List[Dict]:
        """기사 리스트에 본문 내용 추가"""
        print("  본문 크롤링 시작...")
        
        for i, article in enumerate(articles, 1):
            if i % 10 == 0:
                print(f"    {i}/{len(articles)} 진행 중...")
            
            content = self.fetch_article_content(article['url'])
            article['content'] = content
            
            time.sleep(0.5)  # 서버 부하 방지
        
        print(f"  본문 크롤링 완료: {len(articles)}개")
        return articles
    
    def crawl_articles(self, article_type: str) -> List[Dict]:
        """특정 타입의 모든 기사 크롤링"""
        all_articles = []
        page = 1
        
        print(f"\n{article_type} 크롤링 시작...")
        
        while page <= 20:  # 최대 20페이지까지
            print(f"  페이지 {page} 크롤링 중...")
            
            if not self.fetch_page(page, article_type):
                break
            
            articles = self.parse_articles()
            if articles:
                all_articles.extend(articles)
                print(f"    {len(articles)}개 기사 파싱 완료")
            else:
                print("    기사가 없습니다")
                # 기사가 없으면 종료
                break
            
            # 다음 페이지 확인
            if not self.has_next_page():
                print("    마지막 페이지입니다")
                break
            
            page += 1
            time.sleep(1)
        
        print(f"{article_type} 목록 크롤링 완료: 총 {len(all_articles)}개 기사")
        
        # 본문 내용 추가
        if all_articles:
            all_articles = self.enrich_articles_with_content(all_articles)
        
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
                    content LONGTEXT,
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
                    content LONGTEXT,
                    url VARCHAR(750) NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    INDEX idx_date (date),
                    UNIQUE KEY unique_url (url(250))
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """
        }
        
        with self.engine.begin() as conn:
            for table_name, create_sql in tables.items():
                conn.execute(text(create_sql))
                print(f"테이블 '{table_name}' 생성/확인 완료")
    
    def insert_articles(self, articles: List[Dict], table_name: str):
        """기사를 MySQL에 저장"""
        if not articles:
            return
        
        insert_sql = f"""
            INSERT INTO {table_name} (date, title, category, hashtag, content, url)
            VALUES (:date, :title, :category, :hashtag, :content, :url)
            ON DUPLICATE KEY UPDATE
                date = VALUES(date),
                title = VALUES(title),
                category = VALUES(category),
                hashtag = VALUES(hashtag),
                content = VALUES(content)
        """
        
        inserted = 0
        updated = 0
        
        with self.engine.begin() as conn:
            for article in articles:
                try:
                    result = conn.execute(text(insert_sql), {
                        'date': article['date'],
                        'title': article['title'],
                        'category': article['category'],
                        'hashtag': article['hashtag'],
                        'content': article.get('content', ''),
                        'url': article['url']
                    })
                    if result.rowcount == 1:
                        inserted += 1
                    elif result.rowcount == 2:
                        updated += 1
                except Exception as e:
                    print(f"Error inserting article: {e}")
        
        print(f"{table_name}: {inserted}개 신규 저장, {updated}개 업데이트")
    
    def run(self):
        """전체 크롤링 실행"""
        print("=" * 60)
        print("SK Hynix ESG 뉴스 크롤러 시작 (Selenium)")
        print("=" * 60)
        
        try:
            # 테이블 생성
            print("\n테이블 생성/확인 중...")
            self.create_tables()
            
            # 드라이버 초기화
            print("\n브라우저 초기화 중...")
            self.init_driver()
            
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
            
        finally:
            # 드라이버 종료
            self.close_driver()


def main():
    # async -> sync
    engine = create_engine(settings.DATABASE_URL.replace("aiomysql", "pymysql")) # "mysql+pymysql://root:1111@127.0.0.1:3306/esg_db"    
    crawler = SKHynixESGCrawler(engine)
    crawler.run()


if __name__ == '__main__':
    main()