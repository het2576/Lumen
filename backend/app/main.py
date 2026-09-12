from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import logging
from app.config import CORS_ORIGINS
from app.db import get_client
from app.routers import chat, documents, stats

logger = logging.getLogger(__name__)

app = FastAPI(title="DocQuery API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.exception("Global exception on %s: %s", request.url.path, exc)
    origin = request.headers.get("origin", "")
    headers = {
        "Access-Control-Allow-Credentials": "true",
        "Access-Control-Allow-Methods": "*",
        "Access-Control-Allow-Headers": "*",
    }
    if origin:
        headers["Access-Control-Allow-Origin"] = origin
    return JSONResponse(
        status_code=500,
        content={"detail": str(exc)},
        headers=headers,
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
