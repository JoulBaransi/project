"""api.py — Flask application entrypoint.

Endpoints:
    GET  /health   liveness + DB connectivity (degrades gracefully).
    POST /load     ingest the bundled data/stripe_docs.md into docs_lines.
    POST /ask       answer a question via hybrid retrieval + Ollama; logs to qa_log.

The Flask API owns all logic. The Streamlit UI (phase d) only calls these
endpoints over HTTP — it never touches the DB or Ollama directly.

Run locally:
    python -m app.api
"""

from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory

from app import config, db, retrieval
from app.ingest import DATA_FILE, ingest_file

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"

app = Flask(__name__, static_folder=None)


# --------------------------------------------------------------- frontend ----
@app.get("/")
def index():
    return send_from_directory(FRONTEND_DIR, "index.html")


@app.get("/app.js")
def app_js():
    return send_from_directory(FRONTEND_DIR, "app.js")


# --------------------------------------------------------------- health ------
@app.get("/health")
def health():
    """Liveness + DB connectivity. Degrades gracefully before the schema exists."""
    db_status = db.health()
    status = {
        "api": "ok",
        "models": {"chat": config.CHAT_MODEL, "embed": config.EMBED_MODEL},
        **db_status,
    }
    http_code = 200 if db_status.get("db") != "unreachable" else 503
    return jsonify(status), http_code


# ----------------------------------------------------------------- load ------
@app.post("/load")
def load():
    """Ingest the bundled corpus (parse + embed + reload). Idempotent."""
    try:
        summary = ingest_file(DATA_FILE)
        return jsonify({"status": "loaded", **summary}), 200
    except FileNotFoundError as e:
        return jsonify({"error": str(e)}), 404
    except ValueError as e:                 # bad file / empty corpus
        return jsonify({"error": str(e)}), 400
    except RuntimeError as e:               # Ollama down / model missing
        return jsonify({"error": str(e)}), 503


# ------------------------------------------------------------------ ask -------
def _log_qa(question, answer, retrieved_via, top_url):
    """Record a Q&A in qa_log. Logging failures must not break the answer."""
    try:
        conn = db.get_connection()
        try:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO qa_log (question, answer, retrieved_via, top_url)
                VALUES (%s, %s, %s, %s)
                """,
                (question, answer, retrieved_via, top_url),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception as e:                  # never let logging sink the response
        app.logger.warning("qa_log insert failed: %s", e)


@app.post("/ask")
def ask():
    """Answer a Stripe question, grounded only in retrieved context."""
    data = request.get_json(silent=True) or {}
    question = (data.get("question") or "").strip()
    if not question:
        return jsonify({"error": "Provide a non-empty 'question'."}), 400

    try:
        answer_text, chunks, how = retrieval.answer(question)
    except RuntimeError as e:               # Ollama down / model missing
        return jsonify({"error": str(e)}), 503

    top_url = chunks[0]["url"] if chunks else None
    retrieved_via = how if chunks else "none"
    _log_qa(question, answer_text, retrieved_via, top_url)

    return jsonify({
        "question": question,
        "answer": answer_text,
        "retrieved_via": retrieved_via,
        "top_url": top_url,
        "links": [
            {"url": c["url"], "section": c["section_header"], "content": c["content"]}
            for c in chunks
        ],
    }), 200


# ---------------------------------------------------------------- status -----
@app.get("/status")
def status():
    """Corpus state for the UI: how many links are loaded + the latest load."""
    try:
        conn = db.get_connection()
    except Exception as e:
        return jsonify({"docs_lines": 0, "last_file": None, "note": str(e)}), 200
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT COUNT(*) AS n FROM docs_lines")
        n = cur.fetchone()["n"]
        cur.execute(
            "SELECT name, num_links, uploaded_at FROM files ORDER BY file_id DESC LIMIT 1"
        )
        last = cur.fetchone()
        if last:
            last["uploaded_at"] = str(last["uploaded_at"])
        return jsonify({"docs_lines": n, "last_file": last}), 200
    except Exception:
        # DB reachable but schema not initialized yet (pre-load).
        return jsonify({"docs_lines": 0, "last_file": None,
                        "note": "not initialized"}), 200
    finally:
        conn.close()


# --------------------------------------------------------------- history -----
@app.get("/history")
def history():
    """Most recent Q&As from qa_log (newest first). ?limit=N (1..200, default 20)."""
    try:
        limit = int(request.args.get("limit", 20))
    except (TypeError, ValueError):
        limit = 20
    limit = max(1, min(limit, 200))
    try:
        conn = db.get_connection()
    except Exception as e:
        return jsonify({"error": str(e)}), 503
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(
            """
            SELECT qa_id, question, answer, retrieved_via, top_url, created_at
            FROM qa_log ORDER BY qa_id DESC LIMIT %s
            """,
            (limit,),
        )
        rows = cur.fetchall()
        for r in rows:
            r["created_at"] = str(r["created_at"])
        return jsonify({"history": rows}), 200
    except Exception:
        return jsonify({"history": []}), 200      # qa_log not created yet
    finally:
        conn.close()


if __name__ == "__main__":
    app.run(host=config.API_HOST, port=config.API_PORT, debug=True)
