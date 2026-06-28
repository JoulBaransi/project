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

Env vars (with defaults):
    DB_HOST=localhost  DB_USER=root  DB_PASSWORD=  DB_NAME=shop
    OLLAMA_HOST=http://localhost:11434
    EMBED_MODEL=nomic-embed-text   CHAT_MODEL=llama3.5

Deps:
    pip install mysql-connector-python requests
"""

import os
import sys
import math

import requests
import mysql.connector


# ---------------------------------------------------------------- config -----
DB = dict(
    host=os.getenv("DB_HOST", "localhost"),
    user=os.getenv("DB_USER", "root"),
    password=os.getenv("DB_PASSWORD", ""),
    database=os.getenv("DB_NAME", "shop"),
)
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")
EMBED_MODEL = os.getenv("EMBED_MODEL", "nomic-embed-text")
CHAT_MODEL = os.getenv("CHAT_MODEL", "llama3.2")

TOP_K = 2                 # chunks to send to the model (benchmark: recall hits 100% at K=2)
MIN_COSINE = 0.34         # vector-search relevance floor (from your benchmark)


def get_db():
    return mysql.connector.connect(**DB)


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
    conn = get_db()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(sql, (question, question, top_k))
        return cur.fetchall()      # empty list if no keyword overlap
    finally:
        conn.close()


# --------------------------------------------------------- step 2: vector -----
def embed(text):
    """Get an embedding vector for `text` from Ollama."""
    r = requests.post(
        f"{OLLAMA_HOST}/api/embeddings",
        json={"model": EMBED_MODEL, "prompt": text},
        timeout=30,
    )
    r.raise_for_status()
    return r.json()["embedding"]


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
    conn = get_db()
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
    """Keyword first; fall back to vector search if keyword finds nothing."""
    hits = keyword_search(question, top_k)
    if hits:
        return hits, "keyword"
    return vector_search(question, top_k), "vector"


# ----------------------------------------------------------- step 3: LLM -----
def answer(question, top_k=TOP_K):
    chunks, how = retrieve(question, top_k)
    if not chunks:
        return ("I couldn't find anything relevant in the Stripe docs for that.",
                [], how)

    context = "\n".join(f"- {c['content']} (LINK: {c['url']})" for c in chunks)
    prompt = (
        "You are a Stripe documentation assistant. Answer the user's question "
        "USING ONLY the context lines below. Point them to the single most "
        "relevant LINK. If the context doesn't contain the answer, say you don't "
        "know.\n\n"
        f"Context:\n{context}\n\n"
        f"Question: {question}\n\nAnswer:"
    )
    r = requests.post(
        f"{OLLAMA_HOST}/api/generate",
        json={"model": CHAT_MODEL, "prompt": prompt, "stream": False},
        timeout=120,
    )
    r.raise_for_status()
    return r.json()["response"].strip(), chunks, how


# ------------------------------------------------------------------ cli -------
if __name__ == "__main__":
    q = " ".join(sys.argv[1:]) or "How do I refund a payment?"
    text, chunks, how = answer(q)
    print(f"\nQuestion : {q}")
    print(f"Retrieved via: {how}  ({len(chunks)} chunk(s))")
    for c in chunks:
        print(f"   - {c['url']}")
    print(f"\nAnswer:\n{text}")
