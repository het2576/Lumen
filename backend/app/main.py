from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.routers import chat, documents, stats

app = FastAPI(title="DocQuery API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(documents.router)
app.include_router(chat.router)
app.include_router(stats.router)


@app.get("/health")
def health():
    return {"status": "ok"}
