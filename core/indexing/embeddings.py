"""HF Inference API embeddings — google/embeddinggemma-300m.

Per user reference (papers_search pattern):
  HF:  payload {"inputs": formatted_text}
       header  Authorization: Bearer HF_TOKEN
       endpoint HF_EMBEDDING_URL
  document => raw "document" text; query => "task: search result | query: {text}"

No local / sentence-transformers path — HF only.
Graceful fallback: if HF_TOKEN/URL not set, raise and let registry fall back to jaccard/LLM.
"""
import logging
import os
from typing import Literal

import requests
from dotenv import load_dotenv

load_dotenv()

HF_EMBEDDING_URL = os.getenv("HF_EMBEDDING_URL", "https://api-inference.huggingface.co/models/google/embeddinggemma-300m")
HF_TOKEN = os.getenv("HF_TOKEN", "")


def get_embeddings(
    text: str,
    structure: Literal["document", "query"] = "document",
) -> list[float]:
    """Single embedding via HF. Raises if not configured / request fails."""
    if not HF_TOKEN:
        raise ValueError("HF_TOKEN not set (add to .env)")
    if not HF_EMBEDDING_URL:
        raise ValueError("HF_EMBEDDING_URL not set")

    formatted = text if structure == "document" else f"task: search result | query: {text}"
    payload = {"inputs": formatted}
    headers = {"Authorization": f"Bearer {HF_TOKEN}"}

    resp = requests.post(HF_EMBEDDING_URL, headers=headers, json=payload, timeout=60)
    resp.raise_for_status()
    data = resp.json()

    # HF feature-extraction returns list[float] for single input, or nested
    # Normalise to list[float]
    if isinstance(data, list) and data and isinstance(data[0], list):
        # e.g. [[0.1, 0.2]] -> take first
        if isinstance(data[0][0], list):  # [[ [..] ]] batch wrapper
            return data[0][0]
        return data[0]
    if isinstance(data, list) and data and isinstance(data[0], float):
        return data
    if isinstance(data, dict) and "embeddings" in data:
        emb = data["embeddings"]
        return emb[0] if isinstance(emb, list) and len(emb) == 1 else emb
    return data


def is_available() -> bool:
    return bool(HF_TOKEN and HF_EMBEDDING_URL)


def encode_query(text: str):
    return get_embeddings(text, structure="query")


def encode_document(texts: list[str] | str):
    if isinstance(texts, str):
        texts = [texts]
    # Batch: call per item (HF endpoint is single-input); keep simple
    return [get_embeddings(t, structure="document") for t in texts]


def cosine_similarity(a, b) -> float:
    import math

    # handle both list and numpy/torch if ever returned
    try:
        import numpy as np

        if hasattr(a, "cpu"):
            a = a.cpu().numpy()
        if hasattr(b, "cpu"):
            b = b.cpu().numpy()
        a = np.array(a, dtype=float)
        b = np.array(b, dtype=float)
        denom = float(np.linalg.norm(a) * np.linalg.norm(b))
        if denom == 0:
            return 0.0
        return float(np.dot(a, b) / denom)
    except Exception:
        pass
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)
