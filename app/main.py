# FastAPI 애플리케이션을 생성하고 라우터를 등록하는 진입점 파일

# global
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse
from pathlib import Path
import uvicorn

# local
from app.db.models.database import lifespan
from app.modules.auth.api import router as auth_router
from app.modules.chat.api import router as chat_router

# Define FastAPI with lifespan
app = FastAPI(title="ESG Summary Platform", lifespan=lifespan)

# Static files and templates
BASE_DIR = Path(__file__).resolve().parents[0] # ...\ESGsummary\app
app.mount("/static", StaticFiles(directory=str(BASE_DIR/"static")), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR/"templates"))

# Include routers
app.include_router(auth_router, prefix="/auth", tags=["auth"])
app.include_router(chat_router, prefix="/chat", tags=["chat"])


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

### page2_db ###
@app.get("/db", response_class=HTMLResponse)
async def chatbot_page(request: Request):
    """MySQL Database page"""
    return templates.TemplateResponse(request=request, name="page2_db.html")

### page3_rag ###
@app.get("/rag", response_class=HTMLResponse)
async def chatbot_page(request: Request):
    """RAG(Retrieval-Augmented Generation) page"""
    return templates.TemplateResponse(request=request, name="page3_rag.html")


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8080, reload=True)