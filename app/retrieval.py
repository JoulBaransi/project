#!/usr/bin/env python3
"""
retrieval.py — hybrid retrieval for the Stripe-docs RAG app.

Strategy: KEYWORD first, VECTOR as fallback.

    question
       |
       v
  1. KEYWORD search  ->  MySQL FULLTEXT (MATCH ... AGAINST) on docs_lines.
       |  found good hits?
       |---- yes ---> use them
       |---- no  --->
  2. VECTOR search   ->  embed the question with Ollama, rank stored embeddings
                          by cosine similarity (computed here in Python, because
                          Community MySQL can't search vectors).
       |
       v
  3. SEND the chosen chunks to the Ollama chat model as grounding context,
     and return its answer (with the source links).

Why this order: keyword search is fast and precise when the question shares
words with the docs (which the benchmark showed is common here). Vector search
is the safety net for questions worded differently from the docs.

Refactored for the app: configuration comes from app.config, connections from
app.db, the embedder is reused from app.ingest, and the grounded prompt comes
from app.prompt_template — so there is ONE config/connection/prompt system.

Run as a CLI (after ingesting):
    python -m app.retrieval "How do I refund a payment?"
"""

import math
import sys

import requests

from app import config, db
from app.ingest import embed          # reuse the validated Ollama embedder (768-dim)
from app.prompt_template import build_prompt

# Tuned on the link-retrieval benchmark (re-measure with real embeddings before
# locking — see CLAUDE.md / benchmarks/link_retrieval_benchmark.py).
TOP_K = 2                 # chunks to send to the model (recall hit 100% at K=2)
MIN_COSINE = 0.34         # vector-search relevance floor


# ------------------------------------------------------------- step 1: kw -----
def keyword_search(question, top_k=TOP_K):
    """Fast, precise FULLTEXT keyword search. Returns [] if nothing matches."""
    sql = """
        SELECT line_id, section_header, content, url,
               MATCH(content, section_header) AGAINST (%s IN NATURAL LANGUAGE MODE) AS score
        FROM docs_lines
        WHERE MATCH(content, section_header) AGAINST (%s IN NATURAL LANGUAGE MODE)
        ORDER BY score DESC
        LIMIT %s
    """
    conn = db.get_connection()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(sql, (question, question, top_k))
        return cur.fetchall()      # empty list if no keyword overlap
    finally:
        conn.close()


# --------------------------------------------------------- step 2: vector -----
def cosine(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def _parse_vector(s):
    """Turn MySQL's VECTOR_TO_STRING output '[0.1,-0.2,...]' into [floats]."""
    return [float(x) for x in s.strip().lstrip("[").rstrip("]").split(",") if x.strip()]


def vector_search(question, top_k=TOP_K, min_sim=MIN_COSINE):
    """Semantic fallback: cosine similarity over stored embeddings, in Python."""
    qvec = embed(question)
    conn = db.get_connection()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute("""
            SELECT line_id, section_header, content, url,
                   VECTOR_TO_STRING(embedding) AS vec
            FROM docs_lines
            WHERE embedding IS NOT NULL
        """)
        scored = []
        for row in cur.fetchall():
            sim = cosine(qvec, _parse_vector(row["vec"]))
            if sim >= min_sim:
                row["score"] = sim
                del row["vec"]
                scored.append(row)
        scored.sort(key=lambda r: r["score"], reverse=True)
        return scored[:top_k]
    finally:
        conn.close()


# ----------------------------------------------------------- orchestrate -----
def retrieve(question, top_k=TOP_K):
    """Keyword first; fall back to vector search if keyword finds nothing.

    Returns (chunks, how) where how is 'keyword', 'vector', or 'none'.
    """
    hits = keyword_search(question, top_k)
    if hits:
        return hits, "keyword"
    hits = vector_search(question, top_k)
    return (hits, "vector") if hits else ([], "none")


# ----------------------------------------------------------- step 3: LLM -----
def generate(prompt):
    """Call the Ollama chat model. Clear errors if Ollama/model is unavailable."""
    try:
        r = requests.post(
            f"{config.OLLAMA_HOST}/api/generate",
            json={"model": config.CHAT_MODEL, "prompt": prompt, "stream": False},
            timeout=120,
        )
    except requests.exceptions.RequestException as e:
        raise RuntimeError(
            f"Could not reach Ollama at {config.OLLAMA_HOST}. Is it running? ({e})"
        ) from e
    if r.status_code == 404:
        raise RuntimeError(
            f"Ollama has no model '{config.CHAT_MODEL}'. "
            f"Run: ollama pull {config.CHAT_MODEL}"
        )
    r.raise_for_status()
    return r.json()["response"].strip()


def answer(question, top_k=TOP_K):
    """Full RAG: retrieve grounding chunks, then generate a grounded answer.

    Returns (answer_text, chunks, how). If nothing relevant is retrieved, returns
    a clear "couldn't find it" message and does NOT call the model (no hallucination).
    """
    chunks, how = retrieve(question, top_k)
    if not chunks:
        return ("I couldn't find anything relevant in the Stripe docs for that. "
                "Try rephrasing, or make sure the corpus has been loaded.",
                [], how)
    text = generate(build_prompt(question, chunks))
    return text, chunks, how


# ------------------------------------------------------------------ cli -------
if __name__ == "__main__":
    q = " ".join(sys.argv[1:]) or "How do I refund a payment?"
    text, chunks, how = answer(q)
    print(f"\nQuestion : {q}")
    print(f"Retrieved via: {how}  ({len(chunks)} chunk(s))")
    for c in chunks:
        print(f"   - {c['url']}")
    print(f"\nAnswer:\n{text}")
