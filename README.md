# Stripe Docs RAG Assistant

A **local** RAG app that answers Stripe questions by retrieving the single most
relevant Stripe documentation **link** from `data/stripe_docs.md`, then writing a
grounded answer with a local **Ollama** model. All activity is logged to **MySQL**.
No cloud APIs.

```
Web UI (frontend/)  --HTTP-->  Flask API  -->  retrieval (keyword + vector)
                                          -->  Ollama (embeddings + generation)
                                          -->  MySQL (docs_lines + activity logs)
```

- **Generation:** `llama3.2:3b`  •  **Embeddings:** `nomic-embed-text` (768-dim)
- **Retrieval:** MySQL FULLTEXT keyword search first, vector cosine fallback (in Python)
- **DB:** MySQL 9.x (native `VECTOR(768)` for embeddings)
- **UI:** a static web frontend (`frontend/index.html` + `app.js`) served by Flask,
  imported from a Claude Design handoff. (Replaced the original Streamlit UI.)

## Quick start (run it on any laptop)

**The only thing you need installed is [Docker Desktop](https://docs.docker.com/get-docker/).**
Everything else — Python, MySQL, Ollama, and the AI models — runs inside containers.

```bash
git clone https://github.com/JoulBaransi/project.git
cd project
./run.sh            # macOS / Linux   (Windows: run.bat, or: docker compose up --build)
```

Then open **http://localhost:5055/** and click **Load Stripe docs** in the footer.

> First run downloads ~2GB of models and needs ~4GB free RAM — give it a few
> minutes (watch progress with `docker compose logs -f ollama-init`). After that
> it runs fully locally, no internet or cloud APIs. Stop with `Ctrl+C`; remove
> containers with `docker compose down` (add `-v` to also wipe the DB + models).

## Build status (phased)
- [x] **(a)** Flask skeleton, project structure, `db.py`, `.env.example`
- [x] **(b)** MySQL schema + `ingest.py`
- [x] **(c)** retrieval wired into `/ask`
- [x] **(d)** Web UI (custom frontend wired to the API)
- [x] **(e)** Docker Compose (Flask + MySQL + Ollama)

---

## Phase (a): run & verify the Flask skeleton

Phase (a) ships the Flask app with a `/health` endpoint that degrades gracefully —
it reports DB status without crashing even though the tables aren't created until
phase (b).

### 1. Prerequisites
- Python **3.12**
- A reachable MySQL server is *optional* for this phase — `/health` reports
  `db: unreachable` cleanly if there isn't one yet.

### 2. Set up the environment
```bash
cd /Users/joulbaransi/Documents/project
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # edit DB_PASSWORD etc. if you have MySQL running
```

### 3. Run the API
```bash
python -m app.api
# serving on http://localhost:5000
```

### 4. Verify
```bash
curl -s http://localhost:5000/health | python -m json.tool
```

Expected shapes depending on your environment:

| Situation | `db` field |
|---|---|
| No MySQL running yet | `"unreachable"` (HTTP 503) |
| MySQL up, database not created yet | `"connected, database missing"` (HTTP 200) |
| MySQL up, database exists but no tables (post-DB, pre phase b) | `"connected, not initialized"` (HTTP 200) |
| After phase (b) loads the schema | `"ok"` (HTTP 200) |

Any of these means the **API itself is healthy** (`"api": "ok"`). The DB line just
tells you how far along the data layer is — an uninitialized DB in phase (a) is
expected, not a failure.

---

## Phase (b): MySQL schema + ingestion

Phase (b) builds the data layer:
- `db/schema.sql` — creates the `stripe_rag` database + `files` and `qa_log` logs.
- `db/docs_lines.sql` — **canonical** `docs_lines` knowledge table (native
  `VECTOR(768)` + FULLTEXT). Defined here and nowhere else.
- `app/ingest.py` — parses `data/stripe_docs.md` into one row per link line,
  embeds each with `nomic-embed-text`, and (re)loads `docs_lines` idempotently
  (`TRUNCATE` + bulk insert), logging the load in `files`.

### Prerequisites
- MySQL **9.x** running and reachable, with credentials matching your `.env`.
- Ollama running with the embedding model pulled:
  ```bash
  ollama pull nomic-embed-text
  ```

### Run the ingest
```bash
source .venv/bin/activate
python -m app.ingest --init-only     # create DB + tables only (optional)
python -m app.ingest                 # parse + embed + load data/stripe_docs.md
# python -m app.ingest path/to/other.md   # load a different .md corpus
```
Re-running is safe — it truncates and reloads, so rows never duplicate. Each run
appends one entry to the `files` log.

### Verify
```bash
curl -s http://localhost:5055/health | python -m json.tool   # -> "db": "ok"
```
Or check the data directly:
```sql
SELECT COUNT(*) AS rows, COUNT(embedding) AS embedded FROM docs_lines;   -- 484 / 484
SELECT name, type, size, num_links FROM files ORDER BY file_id DESC LIMIT 1;
```
Expect **484** link-lines, all with a 768-dim embedding.

> Note: `VECTOR_DIMS()` may not exist on every MySQL 9 build; read vectors back
> with `VECTOR_TO_STRING(embedding)` instead (that's what `retrieval.py` uses).

---

## Phase (c): retrieval wired into the API

`app/retrieval.py` is refactored onto `app/config.py` + `app/db.py` (no second
config/connection system), reuses `app/ingest.py`'s validated embedder, and builds
its prompt with `app/prompt_template.build_prompt()`. Two endpoints expose it:

- `POST /load` — ingest the bundled corpus (same as `python -m app.ingest`).
- `POST /ask` — `{"question": "..."}` → grounded answer + the single best link;
  every Q&A is logged to `qa_log`.

Retrieval is **keyword-first** (MySQL FULLTEXT), falling back to **vector** cosine
(in Python) only when keyword finds nothing; if neither finds anything,
`/ask` says so and does **not** call the model (no hallucination).

### Run
```bash
source .venv/bin/activate
API_PORT=5055 python -m app.api       # (add DB_PASSWORD="" if local MySQL has no root pw)
```

### Verify
```bash
# 1) load the corpus
curl -s -X POST http://localhost:5055/load | python -m json.tool      # links: 484

# 2) ask a question
curl -s -X POST http://localhost:5055/ask \
  -H 'Content-Type: application/json' \
  -d '{"question":"How do I refund or cancel a payment?"}' | python -m json.tool
#   -> top_url: https://docs.stripe.com/refunds.md, retrieved_via: "keyword"

# 3) empty question is rejected
curl -s -X POST http://localhost:5055/ask \
  -H 'Content-Type: application/json' -d '{"question":"  "}'           # HTTP 400
```

You can also drive retrieval straight from the CLI:
```bash
python -m app.retrieval "How do I receive webhook events from Stripe?"
```

Error behavior: Ollama down / model missing → `/ask` and `/load` return **503**
with an actionable message; bad/empty corpus → **400/404**. A `qa_log` write
failure is logged but never breaks the answer.

---

## Phase (d): Web UI

The frontend is a **static web app** (`frontend/index.html` + `frontend/app.js`),
imported from a Claude Design handoff and wired to the API. It's **HTTP only** —
it never touches MySQL or Ollama. Flask **serves it at `/`** (same origin, so no
CORS), so there's just one process.

It provides:
- a chat-style **ask composer** (Enter to send, Shift+Enter for newline) plus
  one-click **example questions**,
- five visible states — empty, loading, **answer with a hero "most relevant doc"
  card** (+ a `via keyword/vector` badge), no-match, and error,
- a **history** sidebar (`GET /history`) — click an item to re-view a past answer,
- a footer **corpus status** that shows `N docs loaded`, or a **Load Stripe docs**
  button (`POST /load`) when the DB is empty.

Two supporting endpoints back it: `GET /status` (links loaded + last load) and
`GET /history?limit=N`.

### Run (single process — Flask serves the UI)
```bash
source .venv/bin/activate
DB_PASSWORD="" API_PORT=5055 python -m app.api     # drop DB_PASSWORD if your root has one
```

### Verify
1. Open **http://localhost:5055/**.
2. Footer shows **484 docs loaded** (or click **Load Stripe docs** if empty).
3. Ask *"How do I refund or cancel a payment?"* → hero card cites `refunds.md`,
   badge reads *matched via keyword*.
4. The **Recent** sidebar lists your questions; click one to re-view it.

> Opening `frontend/index.html` directly as a file won't reach the API. Either use
> the Flask-served URL above, or pass an explicit base, e.g.
> `http://localhost:5055/?api=http://localhost:5055`.

---

## Phase (e): Docker — the whole stack in one command

```bash
docker compose up --build
```

This brings up four services on an internal `ragnet` network:

| Service | Image | Host port | Purpose |
|---|---|---|---|
| `mysql` | `mysql:9.1` (official) | 3307 → 3306 | DB; schema auto-applied on first boot |
| `ollama` | `ollama/ollama` (official) | 11435 → 11434 | local models |
| `ollama-init` | `ollama/ollama` | — | one-shot: pulls `llama3.2:3b` + `nomic-embed-text` |
| `api` | built (`Dockerfile.api`, gunicorn) | 5055 → 5000 | Flask API **+ web UI** |

The API container serves both the JSON endpoints and the web UI, so there's no
separate frontend service. Then open **http://localhost:5055/** (click **Load
Stripe docs** in the footer if the corpus isn't loaded yet).

### First-boot notes
- **Model pull (~2GB) runs once** via `ollama-init`. Until it finishes (a few
  minutes), `/ask` and `/load` return a clear "model not ready" 503 — just wait
  and retry. Watch progress with `docker compose logs -f ollama-init`.
- **Schema** is auto-created on MySQL's first boot from `db/schema.sql` then
  `db/docs_lines.sql` (mounted into `/docker-entrypoint-initdb.d`). `ingest.py`
  also ensures it, so a `/load` works even on a pre-existing volume.
- **Host ports are deliberately non-default** (5055/3307/11435) so they don't
  collide with a local MySQL (3306), local Ollama (11434), or macOS AirPlay (5000).
- The DB password is `stripe_rag_pw` in both `MYSQL_ROOT_PASSWORD` and the API's
  `DB_PASSWORD` — keep them equal if you change it.

### Verify
```bash
curl -s http://localhost:5055/health        # {"api":"ok", ... "db":"ok"}
curl -s -X POST http://localhost:5055/load  # {"links":484,...}
```

### Teardown
```bash
docker compose down          # stop; keeps volumes (DB + models persist)
docker compose down -v       # also wipe volumes (re-pull + re-ingest next time)
```

---

## Configuration
All config is via environment variables; see `.env.example`. Never commit `.env`.
