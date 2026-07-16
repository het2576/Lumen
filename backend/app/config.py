import os

from dotenv import load_dotenv

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

CORS_ORIGINS = [origin.strip() for origin in os.getenv("CORS_ORIGINS", "http://localhost:3000").split(",") if origin.strip()]

EMBEDDING_MODEL = "models/gemini-embedding-001"
EMBEDDING_DIM = 768
GENERATION_MODEL = "gemini-flash-lite-latest"

CHUNK_SIZE_TOKENS = 500
CHUNK_OVERLAP_TOKENS = 50

TOP_K = 5
SEMANTIC_WEIGHT = 0.7
KEYWORD_WEIGHT = 0.3

CHAT_HISTORY_TURNS = 10
