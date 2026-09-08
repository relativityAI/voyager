"""Management sentiment analysis via LiteLLM.

Analyzes management commentary (annual-report text, transcripts, announcements)
for *facts over diplomatic language*: returns verbatim factual claims,
a tone classification, flagged boilerplate/hedging, and a factual-density
score. Results are cached in ``sentiment_results`` keyed by text hash + model
so identical inputs are never re-analyzed.

Provider is configured through env (LiteLLM supports OpenAI, Anthropic, Groq,
Ollama, etc. behind one ``completion`` call):
  LITELLM_MODEL     e.g. "openai/gpt-4o-mini", "groq/llama-3.3-70b-versatile"
  LITELLM_API_KEY   key for the provider (optional for local Ollama)
  LITELLM_API_BASE  optional custom base URL
"""

import asyncio
import hashlib
import json
import math
import re
from datetime import datetime
from typing import Any, Dict, List

from loguru import logger
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from src.db.engine import get_session_factory
from src.db.models import SentimentResult

from ._common import InvalidRequestError, ServiceUnavailableError

LITELLM_MODEL = "openai/gpt-4o-mini"
CHUNK_CHARS = 8000
CHUNK_OVERLAP = 400
MAX_CHUNKS = 20

_SYSTEM_PROMPT = (
    "You analyze management communication from public filings, transcripts, "
    "and announcements. Respond with facts only. Do not use diplomatic, "
    "hedged, or boilerplate language. For each factual claim, quote the "
    "exact wording and name the figure if one is present. Return strict JSON "
    'with keys: "claims" (list of {"quote","claim","sentiment","metric_value"}), '
    '"tone" (one of: negative/neutral/positive/mixed), "tone_score" (int -5..5), '
    '"boilerplate" (list of sentences that are generic, non-informative), '
    '"hedging" (list of hedged/vague statements), '
    '"factual_density" (float 0..1), "summary" (one factual paragraph). '
    "No markdown fences, no commentary outside JSON."
)


def _model() -> str:
    import os

    return os.getenv("LITELLM_MODEL", LITELLM_MODEL)


def _extract_json(text: str) -> Dict[str, Any]:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            return json.loads(text[start : end + 1])
        raise ServiceUnavailableError("LLM returned unparseable JSON")


def chunk_text(text: str) -> List[str]:
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return []
    chunks = []
    start = 0
    while start < len(text) and len(chunks) < MAX_CHUNKS:
        end = start + CHUNK_CHARS
        chunk = text[start:end]
        if end < len(text):
            cut = chunk.rfind(". ")
            if cut > CHUNK_CHARS // 2:
                end = start + cut + 1
                chunk = text[start:end]
        chunks.append(chunk)
        start = max(end - CHUNK_OVERLAP, start + 1)
    return chunks


def _analysis_prompt(text: str) -> List[Dict[str, str]]:
    return [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": f"Analyze this management communication:\n\n{text}"},
    ]


def _call_llm(text: str, model: str) -> Dict[str, Any]:
    import os

    import litellm

    kwargs: Dict[str, Any] = {
        "model": model,
        "messages": _analysis_prompt(text),
        "temperature": 0,
    }
    if os.getenv("LITELLM_API_KEY"):
        kwargs["api_key"] = os.getenv("LITELLM_API_KEY")
    if os.getenv("LITELLM_API_BASE"):
        kwargs["api_base"] = os.getenv("LITELLM_API_BASE")
    try:
        resp = litellm.completion(**kwargs)
        content = resp.choices[0].message.content
    except Exception as exc:
        raise ServiceUnavailableError(f"LLM request failed: {exc}")
    return _extract_json(content)


def _merge(chunks_results: List[Dict[str, Any]]) -> Dict[str, Any]:
    claims, boilerplate, hedging = [], [], []
    tone_scores, densities = [], []
    for r in chunks_results:
        claims.extend(r.get("claims") or [])
        boilerplate.extend(r.get("boilerplate") or [])
        hedging.extend(r.get("hedging") or [])
        if r.get("tone_score") is not None:
            tone_scores.append(float(r["tone_score"]))
        if r.get("factual_density") is not None:
            densities.append(float(r["factual_density"]))
    avg_tone = (
        int(math.floor(sum(tone_scores) / len(tone_scores) + 0.5))
        if tone_scores
        else 0
    )
    return {
        "claims": claims,
        "tone": "neutral" if avg_tone == 0 else ("positive" if avg_tone > 0 else "negative"),
        "tone_score": avg_tone,
        "boilerplate": boilerplate,
        "hedging": hedging,
        "factual_density": round(
            sum(densities) / len(densities), 3
        ) if densities else None,
        "summary": " ".join(
            r.get("summary") or "" for r in chunks_results
        ).strip() or "No extractable summary.",
        "model": _model(),
    }


async def run_sentiment_analysis(task_args: Dict[str, Any]) -> Dict[str, Any]:
    url = task_args.get("url")
    text = task_args.get("text")
    if url:
        from src.utils.helpers import read_pdf

        try:
            text = await asyncio.to_thread(read_pdf, url)
        except Exception as exc:
            raise ServiceUnavailableError(f"Failed to read PDF text: {exc}")
    text = (text or "").strip()
    if not text:
        raise InvalidRequestError("analysis requires 'text' or a PDF 'url'")

    model = task_args.get("model") or _model()
    text_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()

    factory = get_session_factory()
    async with factory() as session:
        result = await session.execute(
            select(SentimentResult).where(
                SentimentResult.content_hash == text_hash,
                SentimentResult.model == model,
            )
        )
        cached = result.scalar_one_or_none()
        if cached:
            return {"cached": True, "result": cached.result}

    chunks = chunk_text(text)
    if not chunks:
        raise InvalidRequestError("No analyzable text")

    chunk_results = await asyncio.gather(
        *[asyncio.to_thread(_call_llm, c, model) for c in chunks],
        return_exceptions=True,
    )
    results: List[Dict[str, Any]] = []
    for r in chunk_results:
        if isinstance(r, Exception):
            logger.error(f"Sentiment chunk failed: {r}")
            continue
        results.append(r)
    if not results:
        raise ServiceUnavailableError("All sentiment chunks failed")

    merged = _merge(results)
    merged["source"] = {"url": url, "chars": len(text), "chunks": len(results)}

    row = {
        "source_key": url or "text",
        "content_hash": text_hash,
        "model": model,
        "result": merged,
        "analyzed_at": datetime.utcnow(),
    }
    stmt = (
        pg_insert(SentimentResult)
        .values(**row)
        .on_conflict_do_update(
            index_elements=["content_hash", "model"],
            set_={k: v for k, v in row.items() if k not in ("content_hash", "model")},
        )
    )
    async with factory() as session:
        await session.execute(stmt)
        await session.commit()

    return {"cached": False, "result": merged}


def _demo() -> None:
    """Runnable check for chunking + merge logic (no LLM, no DB)."""
    text = "A sentence. " * 3000  # ~24k chars -> multiple chunks
    chunks = chunk_text(text)
    assert 2 <= len(chunks) <= MAX_CHUNKS, len(chunks)
    assert all(0 < len(c) <= CHUNK_CHARS for c in chunks)
    merged = _merge(
        [
            {"claims": [{"quote": "x"}], "boilerplate": [], "hedging": [],
             "tone_score": 2, "factual_density": 0.8, "summary": "S1"},
            {"claims": [], "boilerplate": ["b"], "hedging": ["h"],
             "tone_score": 1, "factual_density": None, "summary": "S2"},
        ]
    )
    assert merged["tone"] == "positive"
    assert len(merged["claims"]) == 1
    assert merged["boilerplate"] == ["b"]
    assert merged["factual_density"] == 0.8
    assert merged["summary"] == "S1 S2"
    print("Sentiment demo OK")


if __name__ == "__main__":
    _demo()
