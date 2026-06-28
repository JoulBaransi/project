# CLAUDE.md — Stripe Docs RAG Assistant

Context for building this project. Read this fully before writing code, and reuse
the provided assets rather than reinventing them.

## What we're building
A **local RAG app** that answers Stripe questions by retrieving the single most
relevant Stripe documentation **link** from a provided markdown file, then writing
the answer with a **local Ollama model**. All activity is logged to **MySQL**, and
the whole thing ships with **Docker**.

## Required behavior
- User loads `stripe_docs.md`, asks a question, gets an answer + the most relevant link.
- Answers must be grounded **only** in retrieved context — never the model's own
  training knowledge (the doc itself warns against outdated Stripe advice).
- If nothing relevant is retrieved, say so. Do not hallucinate.

## Tech stack (do not substitute without asking)
- **GUI:** Streamlit — frontend only (no direct DB/model calls)
- **Backend:** Flask — REST API, owns all logic
- **LLM (local Ollama, never a cloud API):**
  - generation: `llama3.2`  (verify the exact tag/size with `ollama pull llama3.2`
    — Llama 3.2 text models ship as 1B or 3B; pick the small one)
  - embeddings: `nomic-embed-text` (768 dimensions)
- **Database:** MySQL (logging + the knowledge table)
- **Packaging:** Docker + docker-compose
- **Python 3.12** (NOT 3.13+/3.15 — many deps lack wheels for the newest Python)

## Architecture
```
Streamlit UI  --HTTP-->  Flask API  -->  retrieval (keyword + vector)
                                    -->  Ollama (embeddings + generation)
                                    -->  MySQL (docs_lines + activity logs)
```
Streamlit never talks to the DB or model directly — it calls the Flask API.

## Corpus & chunking — ALREADY DECIDED, do not re-derive
- Corpus: `data/stripe_docs.md` — ~484 links, one per line; each line =
  section header + a short distinguishing phrase + a URL.
- **Chunk unit = ONE LINE = one link.** This is fixed by the data: a section holds
  ~19 links on average (up to 40), so any coarser chunk can't return a single link.
  Do NOT chunk by word count or by section.
- The retrieval target / "answer" is the **URL** on the matched line.

## Retrieval design — hybrid: keyword first, vector fallback
1. **Keyword:** MySQL FULLTEXT `MATCH ... AGAINST` on `docs_lines`. Fast and precise.
2. If keyword returns nothing → **Vector:** embed the question with
   `nomic-embed-text`, rank stored embeddings by cosine similarity **in Python**
   (Community MySQL has no vector search), keep results above the floor.
3. Send the top chunks to `llama3.2` using the RAG prompt.

Tuned settings (from `benchmarks/link_retrieval_benchmark.py`; re-measure with the
real embeddings before locking them):
- **K (chunks returned) = 2** — recall hit 100% at K=2 on the test set.
- **Similarity floor ≈ 0.34 cosine** — weak under TF-IDF; expect cleaner
  separation with embeddings, so re-check.
- **Algorithm:** TF-IDF/BM25 scored well here; embeddings are the safest default.

## Data model (MySQL)
- `docs_lines` — the knowledge base (see `db/docs_lines.sql`):
  `line_id` PK, `section_header`, `content`, `url`, `embedding VECTOR(768)`
  (optional; JSON is a fine portable alternative since search runs in Python),
  `created_at`, plus a FULLTEXT index on (`content`, `section_header`).
- `files` — log of loaded files: name, type, size, uploaded_at.
- `qa_log` — every Q&A: question, answer, retrieved_via (keyword|vector),
  top_url, created_at.

## Provided assets — REUSE, don't rewrite
- `data/stripe_docs.md` — the knowledge base.
- `db/docs_lines.sql` — `docs_lines` table + FULLTEXT index + examples.
- `app/retrieval.py` — working hybrid keyword→vector retrieval + Ollama call.
- `app/prompt_template.py` — the grounded RAG prompt + `build_prompt()` helper.
- `benchmarks/link_retrieval_benchmark.py` — how the settings above were derived.

## Build in phases — STOP for review after each
- **(a)** Flask skeleton + project structure + `db.py` + `.env.example`
- **(b)** MySQL schema + `ingest.py` (parse `stripe_docs.md` → `docs_lines`,
  generating embeddings via Ollama)
- **(c)** retrieval wired into a Flask `/ask` endpoint, using `retrieval.py`
  and `prompt_template.py`
- **(d)** Streamlit UI: load file, ask question, show answer + link, show history
- **(e)** Docker + `docker-compose.yml` bringing up Flask + Streamlit + MySQL +
  Ollama with a single `docker compose up`
After each phase, explain exactly how to run and verify that piece.

## Conventions
- All config via env vars; commit `.env.example`; never hardcode secrets.
- Small, single-responsibility modules. Clear errors (Ollama down, bad file type,
  empty retrieval).
- Pin `requirements.txt`. Write a `README.md` with local + Docker run steps.
- If unsure about a library API or version, say so — don't guess.
