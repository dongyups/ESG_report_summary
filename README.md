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
- Redis access/refresh 토큰 각각 24시간 4주로 설정, 페이지 우측 최상단에 카운트 다운 및 강제 로그아웃
- 챗봇은 클로드 API 기반으로 최근 5턴의 대화만을 입력으로 사용하여 토큰 비용 이슈 방지 (장기 메모리 설정은 구현되어 있지 않음)
- 모든 회원별 `ForeignKey("users.id")` 와 매칭되어 `conversations` & `messages` (relationship) DB에 비동기식 저장
- 왼쪽 사이드바에서 대화가 기록되어 있는 채팅창 이름 수정 혹은 삭제 가능
- 일반적인 여러 챗봇 웹사이트 UI를 참고하여 구성
<p align="left">
  <img src="assets/screenshots/로그인화면.jpeg" width="49%"/>
  <img src="assets/screenshots/챗봇화면.jpeg" width="49%"/>
</p>

2026-##-##
- ... (upcoming)



### 기술 스택
requirements.txt 참조 (upcoming)
- HTML/CSS
- jQuery (3.6.4)
- FastAPI
- Python (3.11)
- MySQL (8.0)
- Redis (7-alpine)
- Claude API
- LangChain/LangGraph (upcoming)
- Ollama, Chroma (upcoming)
- AWS (upcoming)
