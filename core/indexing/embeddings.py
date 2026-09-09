"""HF embeddings via huggingface_hub InferenceClient (google/embeddinggemma-300m).

Per user snippet:
  client = InferenceClient(provider="hf-inference", api_key=HF_TOKEN)
  client.sentence_similarity({"source_sentence": q, "sentences": [...]}, model="google/embeddinggemma-300m")

This directly returns cosine-like similarities, so registry can use it without manual encode/cosine.
Keeps old get_embeddings/encode_* API for back-compat but now wraps InferenceClient.
"""
import os
from typing import List, Literal
from dotenv import load_dotenv

load_dotenv()

HF_TOKEN = os.getenv("HF_TOKEN", "")
HF_MODEL = os.getenv("HF_EMBEDDING_MODEL", "google/embeddinggemma-300m")
# old URL var kept for is_available check, but not used for InferenceClient
HF_EMBEDDING_URL = os.getenv("HF_EMBEDDING_URL", "")

_client = None

def _get_client():
    global _client
    if _client is not None:
        return _client
    if not HF_TOKEN or HF_TOKEN == "your_hf_token_here":
        raise ValueError("HF_TOKEN not set")
    try:
        from huggingface_hub import InferenceClient
    except ImportError as e:
        raise ImportError("huggingface_hub not installed. pip install -r requirements.txt") from e
    _client = InferenceClient(provider="hf-inference", api_key=HF_TOKEN)
    return _client

def is_available() -> bool:
    return bool(HF_TOKEN and HF_TOKEN != "your_hf_token_here")

def get_embeddings(text: str, structure: Literal["document", "query"] = "document") -> List[float]:
    """Fallback: feature extraction via InferenceClient (if needed for direct embedding)."""
    c = _get_client()
    # sentence_similarity is preferred; for raw embedding use feature_extraction
    # huggingface_hub InferenceClient has feature_extraction method
    try:
        # try feature_extraction
        return c.feature_extraction(text, model=HF_MODEL)  # type: ignore
    except Exception:
        # fallback to sentence_similarity self-similarity trick not needed
        raise

def sentence_similarity(source: str, sentences: List[str]) -> List[float]:
    """Direct similarity scores in [0,1] for source vs each sentence."""
    c = _get_client()
    # huggingface_hub API: sentence, other_sentences
    try:
        result = c.sentence_similarity(source, sentences, model=HF_MODEL)  # type: ignore
    except TypeError:
        # fallback to dict form (as in user snippet) for older hub versions
        result = c.sentence_similarity(  # type: ignore
            {"source_sentence": source, "sentences": sentences}, model=HF_MODEL
        )
    if isinstance(result, list) and result and isinstance(result[0], list):
        return result[0]
    return result  # type: ignore

def encode_query(text: str):
    return get_embeddings(text, structure="query")

def encode_document(texts: List[str] | str):
    if isinstance(texts, str):
        texts = [texts]
    return [get_embeddings(t, structure="document") for t in texts]

def cosine_similarity(a, b) -> float:
    import math
    try:
        import numpy as np
        if hasattr(a, "cpu"): a = a.cpu().numpy()
        if hasattr(b, "cpu"): b = b.cpu().numpy()
        a = np.array(a, dtype=float); b = np.array(b, dtype=float)
        denom = float(np.linalg.norm(a) * np.linalg.norm(b))
        return float(np.dot(a, b) / denom) if denom else 0.0
    except Exception:
        pass
    dot = sum(x*y for x,y in zip(a,b))
    na = math.sqrt(sum(x*x for x in a)); nb = math.sqrt(sum(y*y for y in b))
    return dot / (na*nb) if na and nb else 0.0
