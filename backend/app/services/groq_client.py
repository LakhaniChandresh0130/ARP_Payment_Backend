from groq import Groq
from app.config import GROQ_API_KEY, GROQ_MODEL

if GROQ_API_KEY:
    groq_client = Groq(api_key=GROQ_API_KEY)
else:
    groq_client = None

# Groq retired llama-3.3-70b-versatile for free/developer keys (Aug 2026).
# Keep a fallback chain so a stale .env value does not stall invoices.
_FALLBACK_MODELS = (
    GROQ_MODEL,
    "openai/gpt-oss-120b",
    "openai/gpt-oss-20b",
    "qwen/qwen3.8-27b",
)


def _unique_models():
    seen = set()
    models = []
    for model in _FALLBACK_MODELS:
        if model and model not in seen:
            seen.add(model)
            models.append(model)
    return models


def create_chat_completion(**kwargs):
    if not groq_client:
        raise RuntimeError("GROQ_API_KEY is not configured")

    last_error = None
    for model in _unique_models():
        try:
            return groq_client.chat.completions.create(**{**kwargs, "model": model})
        except Exception as error:
            last_error = error
            text = str(error).lower()
            if "model_not_found" in text or "does not exist" in text or "do not have access" in text:
                print(f"[Groq] model `{model}` unavailable, trying next")
                continue
            raise
    raise last_error
