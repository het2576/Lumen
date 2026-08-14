from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.config import CORS_ORIGINS
from app.db import get_client
from app.routers import chat, documents, stats

app = FastAPI(title="DocQuery API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(documents.router)
app.include_router(chat.router)
app.include_router(stats.router)

@app.get("/health")
def health():
    try:
        result = get_client().table("query_log").select("id").limit(1).execute()
        return {"status": "ok", "db": "reachable"}
    except Exception as e:
        return {"status": "error", "db": str(e)}
