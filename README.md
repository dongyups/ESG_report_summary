## ESG 보고서 요약 자동화 시스템 개발 프로젝트
ESG 보고서를 업로드하고, RAG 기반으로 요약을 수행하는 웹 서비스


### 프론트 구성
0. 로그인
1. 간단한 챗봇
2. 보고서 전처리 및 DB 업로드 확인
3. 요약하고자 하는 보고서 DB 확인 후 RAG를 통한 결과물 출력


### 진행 사항
2026-04-28: 로그인 및 간단한 챗봇 구현
- 전체적으로 기본 포트번호 사용
- 회원가입 HTML은 구현하지 않았으므로 docs에서 진행 (Pydantic `users` 테이블)
- 로그인은 필요시에 회원가입 진행 혹은 아이디,비밀번호 `dd` & `dd` 로 로그인
- Redis access/refresh 토큰 각각 24시간 4주로 설정, 페이지 우측 최상단에 카운트 다운 및 강제 로그아웃 기능
- 챗봇은 클로드 API 기반으로 최근 5턴의 대화만을 입력으로 사용하여 토큰 비용 이슈 방지 (장기 메모리 설정은 구현되어 있지 않음)
- 모든 회원별 `ForeignKey("users.id")` 와 매칭되어 `conversations` & `messages` (relationship) DB에 비동기식 저장
- 왼쪽 사이드바에서 대화가 기록되어 있는 채팅창 이름 수정 혹은 삭제 가능
- 일반적인 여러 챗봇 웹사이트 UI를 참고하여 구성
<p align="left">
  <img src="assets/screenshots/로그인화면.jpeg" width="49%"/>
  <img src="assets/screenshots/챗봇화면.jpeg" width="49%"/>
</p>
</br>

2026-05-26: RAG사용을 위한 DB 전처리 및 시각화 구현
- `app/preprocess/data_prep_excel.py` 코드를 통해 `datasets` 폴더의 ESG 엑셀파일 MySQL에 업로드
- `app/preprocess/data_prep_craw.py` 코드를 통해 SK하이닉스 홈페이지의 뉴스 및 게시글 크롤링, 클리어링 및 MySQL에 업로드\
  `BeautifulSoup` 패키지로는 크롤링이 되지 않아 `Selenium` 으로 크롤링, 다른 버전의 크롤링 코드는 간단히 참고만
- MySQL 데이터베이스는 `app/datasets/esg_db.sql` 파일을 로드하여 확인 가능하므로 전처리 코드는 따로 실행하지 않아도 됨
- 이 외의 같은 폴더 내 pdf 파일들은 추후 활용되거나 간단히 참고용으로 사용을 고려
- 간단한 시각화 결과는 아래의 스크린샷 이미지와 같음
<p align="left">
  <img src="assets/screenshots/DB화면1.jpeg" width="49%"/>
  <img src="assets/screenshots/DB화면2.jpeg" width="49%"/>
</p>


2026-##-##
- ... (upcoming)



### 기술 스택
requirements.txt 참조 (upcoming)
- HTML/CSS
- jQuery (3.6.4)
- FastAPI (0.136.1)
- Python (3.11)
- MySQL (8.0)
- Redis (7-alpine)
- Docker
- Claude API
- LangChain/LangGraph (upcoming)
- Ollama, Chroma (upcoming)
- AWS EC2 (upcoming)
