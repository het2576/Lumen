import os

from dotenv import load_dotenv

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

CORS_ORIGINS = [
    origin.strip()
    for origin in os.getenv("CORS_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000").split(",")
    if origin.strip()
]

EMBEDDING_MODEL = "models/gemini-embedding-001"
EMBEDDING_DIM = 768
# Pinned to an exact model, never a "-latest" alias: an alias can move to a new model
# overnight and change answers, latency and cost with no code change. To upgrade, change
# this value, run the evals against the baseline, and ship only if nothing regresses.
# (gemini-flash-lite-latest pointed at gemini-3.5-flash-lite when this was pinned.)
GENERATION_MODEL = os.getenv("GENERATION_MODEL", "gemini-3.5-flash-lite")
GENERATION_TIMEOUT_SECONDS = float(os.getenv("GENERATION_TIMEOUT_SECONDS", "60"))

# Free-tier Gemini enforces a per-minute token ceiling; these keep ingestion
# under it. Raise both if the project moves to a paid tier.
EMBED_BATCH_TOKENS = int(os.getenv("EMBED_BATCH_TOKENS", "6000"))
EMBED_TPM_BUDGET = int(os.getenv("EMBED_TPM_BUDGET", "28000"))

CHUNK_SIZE_TOKENS = 500
CHUNK_OVERLAP_TOKENS = 50

TOP_K = 8
# Sources up to this many chunks (~30k tokens) are sent to the model whole, so broad
# questions see everything instead of a handful of loosely matching chunks.
FULL_CONTEXT_MAX_CHUNKS = 60
SEMANTIC_WEIGHT = 0.7
KEYWORD_WEIGHT = 0.3

CHAT_HISTORY_TURNS = 10


# Durable ingestion queue (ingestion_jobs table + a private Storage bucket for uploads).
UPLOAD_BUCKET = os.getenv("UPLOAD_BUCKET", "lumen-uploads")
INGESTION_WORKER_ENABLED = os.getenv("INGESTION_WORKER_ENABLED", "true").lower() not in {"0", "false", "no"}
INGESTION_WORKER_THREADS = int(os.getenv("INGESTION_WORKER_THREADS", "1"))
INGESTION_POLL_SECONDS = float(os.getenv("INGESTION_POLL_SECONDS", "5"))
# A running job whose heartbeat is older than this is assumed dead (e.g. the instance
# restarted) and is picked up again. The heartbeat fires every third of this interval.
INGESTION_STALE_SECONDS = int(os.getenv("INGESTION_STALE_SECONDS", "120"))
INGESTION_MAX_ATTEMPTS = int(os.getenv("INGESTION_MAX_ATTEMPTS", "3"))

# Tracing: Langfuse is used when both keys are set; per-request timing logs are always on.
LANGFUSE_ENABLED = bool(os.getenv("LANGFUSE_PUBLIC_KEY") and os.getenv("LANGFUSE_SECRET_KEY"))
# Optional USD prices per 1M tokens for cost estimates in logs and traces. Leave unset to
# let Langfuse price known models itself. Check current prices before setting these.
PRICE_INPUT_PER_M = float(os.environ["PRICE_INPUT_PER_M"]) if os.getenv("PRICE_INPUT_PER_M") else None
PRICE_OUTPUT_PER_M = float(os.environ["PRICE_OUTPUT_PER_M"]) if os.getenv("PRICE_OUTPUT_PER_M") else None
PRICE_CACHED_PER_M = float(os.environ["PRICE_CACHED_PER_M"]) if os.getenv("PRICE_CACHED_PER_M") else None
