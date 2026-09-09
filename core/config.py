import os
from dotenv import load_dotenv

load_dotenv()

# Bynara (primary) with Groq fallback — all OpenAI-compatible
# .env should contain LLM_API_KEY + LLM_BASE_URL=https://router.bynara.id/v1
LLM_API_KEY = (
    os.getenv("LLM_API_KEY")
    or os.getenv("BYNARA_API_KEY")
    or os.getenv("GROQ_API_KEY", "")
)
LLM_BASE_URL = (
    os.getenv("LLM_BASE_URL")
    or os.getenv("BYNARA_BASE_URL")
    or os.getenv("GROQ_BASE_URL", "https://router.bynara.id/v1")
)
# normalize: if user pasted full /chat/completions path, strip it
if LLM_BASE_URL.endswith("/chat/completions"):
    LLM_BASE_URL = LLM_BASE_URL[: -len("/chat/completions")]
LLM_MODEL = (
    os.getenv("LLM_MODEL")
    or os.getenv("BYNARA_MODEL")
    or os.getenv("GROQ_MODEL", "muse-spark-1.2-contributor-free")
)
LLM_ARBITER_MODEL = os.getenv("LLM_ARBITER_MODEL") or os.getenv(
    "GROQ_ARBITER_MODEL", LLM_MODEL
)

# Back-compat aliases
GROQ_API_KEY = LLM_API_KEY
GROQ_MODEL = LLM_MODEL
GROQ_ARBITER_MODEL = LLM_ARBITER_MODEL
GROQ_BASE_URL = LLM_BASE_URL


def get_llm_client():
    """
    Returns an OpenAI-compatible client (Bynara router by default).
    Fill LLM_API_KEY in .env — no code change needed.
    """
    try:
        from openai import OpenAI
    except ImportError as e:
        raise ImportError(
            "openai package not installed. pip install -r requirements.txt"
        ) from e

    if not LLM_API_KEY:
        raise ValueError(
            "LLM_API_KEY (or BYNARA_API_KEY / GROQ_API_KEY) not set. Create .env from .env.example and fill your key."
        )
    return OpenAI(
        api_key=LLM_API_KEY, base_url=LLM_BASE_URL, timeout=30.0, max_retries=1
    )


def is_llm_configured() -> bool:
    return bool(LLM_API_KEY)


def chat_extra_body() -> dict:
    """Disable thinking for muse-spark everywhere (no thinking tokens, lower cost)."""
    return {"thinking": {"type": "disabled"}}
