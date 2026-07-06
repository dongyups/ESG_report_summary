# FastAPI 애플리케이션을 생성하고 라우터를 등록하는 진입점 파일

# global
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse
from pathlib import Path
import uvicorn
import time

# local
from app.db.models.database import lifespan
from app.modules.auth.api import router as auth_router
from app.modules.chat.api import router as chat_router
from app.modules.db.api_rawdb import router as rawdb_router
from app.modules.db.api import router as db_router
from app.modules.rag.api import router as rag_router
from app.modules.rag.section_api import router as section_router

# Define FastAPI with lifespan
app = FastAPI(
    title="ESG Summary Platform", 
    lifespan=lifespan,
    docs_url=None,      # /docs 비활성화
    redoc_url=None,     # /redoc 비활성화
    openapi_url=None    # /openapi.json 비활성화 (스키마 자체 차단)
)

# Static files and templates
BASE_DIR = Path(__file__).resolve().parents[0] # ...\ESGsummary\app
app.mount("/static", StaticFiles(directory=str(BASE_DIR/"static")), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR/"templates"))
templates.env.globals["static_ver"] = int(time.time()) ### css/js 파일 수정 실시간 적용

# Include routers
app.include_router(auth_router, prefix="/auth", tags=["auth"])
app.include_router(chat_router, prefix="/chat", tags=["chat"])
app.include_router(rawdb_router, prefix="/rawdb", tags=["rawdb"])
app.include_router(db_router, prefix="/db", tags=["db"])
app.include_router(rag_router, prefix="/rag",  tags=["rag"])
app.include_router(section_router, prefix="/rag", tags=["rag-sections"])


### root_page ###
@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    """Redirect to login page"""
    return templates.TemplateResponse(request=request, name="page0_login.html")

### page0_login ###
@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    """Login page"""
    return templates.TemplateResponse(request=request, name="page0_login.html")

### page1_chatbot ###
@app.get("/chatbot", response_class=HTMLResponse)
async def chatbot_page(request: Request):
    """Chatbot page"""
    return templates.TemplateResponse(request=request, name="page1_chatbot.html")

### page2_rawdb ###
@app.get("/rawdb", response_class=HTMLResponse)
async def rawdb_page(request: Request):
    """MySQL RAW Database page"""
    return templates.TemplateResponse(request=request, name="page2_rawdb.html")

### page3_db ###
@app.get("/db", response_class=HTMLResponse)
async def db_page(request: Request):
    """MySQL Database page"""
    return templates.TemplateResponse(request=request, name="page3_db.html")

### page4_rag ###
@app.get("/rag", response_class=HTMLResponse)
async def rag_page(request: Request):
    """RAG(Retrieval-Augmented Generation) page"""
    return templates.TemplateResponse(request=request, name="page4_rag.html")

### page5_report_rag ###
@app.get("/report-gen", response_class=HTMLResponse)
async def report_gen_page(request: Request):
    """Report RAG Auto-generation (REPORT_GRAPH — Map-Reduce) page"""
    return templates.TemplateResponse(request=request, name="page5_report_rag.html")

### page6_section_rag ###
@app.get("/section", response_class=HTMLResponse)
async def section_rag_page(request: Request):
    """Section RAG HITL (SECTION_GRAPH) page"""
    return templates.TemplateResponse(request=request, name="page6_section_rag.html")


if __name__ == "__main__":
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True) # --port 번호가 중복되는 경우 원하는 번호로 수정