import os
from dotenv import load_dotenv

load_dotenv()

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
GROQ_ARBITER_MODEL = os.getenv("GROQ_ARBITER_MODEL", "llama-3.1-8b-instant")
GROQ_BASE_URL = os.getenv("GROQ_BASE_URL", "https://api.groq.com/openai/v1")


def get_llm_client():
    """
    Returns an OpenAI-compatible client pointed at Groq.
    Fill GROQ_API_KEY in .env — no code change needed.
    Usage:
        client = get_llm_client()
        resp = client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[...],
            response_format={"type": "json_object"}
        )
    """
    try:
        from openai import OpenAI
    except ImportError as e:
        raise ImportError("openai package not installed. pip install -r requirements.txt") from e

    if not GROQ_API_KEY:
        raise ValueError(
            "GROQ_API_KEY not set. Create .env from .env.example and fill your key. "
            "Get one at https://console.groq.com/keys"
        )
    return OpenAI(api_key=GROQ_API_KEY, base_url=GROQ_BASE_URL)


def is_llm_configured() -> bool:
    return bool(GROQ_API_KEY)
