import google.generativeai as genai

from app.config import GEMINI_API_KEY

_configured = False


def get_genai():
    global _configured
    if not _configured:
        if not GEMINI_API_KEY:
            raise RuntimeError(
                "GEMINI_API_KEY is not set. Copy backend/.env.example to backend/.env and fill it in."
            )
        genai.configure(api_key=GEMINI_API_KEY)
        _configured = True
    return genai
