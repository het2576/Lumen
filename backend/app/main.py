import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app import tracing
from app.config import CORS_ORIGINS, INGESTION_WORKER_ENABLED
from app.db import get_client
from app.routers import chat, documents, stats
from app.worker import WORKER

# Without this, every app logger.info (trace lines, ingestion progress) is silently dropped:
# uvicorn configures only its own loggers.
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)  # one line per Supabase/Gemini request is noise
logging.getLogger("google_genai").setLevel(logging.WARNING)  # logs "AFC is enabled" on every call

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI):
    if INGESTION_WORKER_ENABLED:
        WORKER.start()
    yield
    WORKER.stop()
    tracing.flush()


app = FastAPI(title="DocQuery API", lifespan=lifespan)
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
