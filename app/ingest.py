"""ingest.py — parse data/stripe_docs.md into docs_lines, with embeddings.

Pipeline:
    1. ensure schema (apply db/schema.sql then db/docs_lines.sql — idempotent)
    2. parse the markdown into one row per link line (anchor + description + url)
    3. embed each line's text with Ollama `nomic-embed-text` (768 dims)
    4. TRUNCATE docs_lines and bulk-insert (idempotent reload — re-running is safe)
    5. log the load in the `files` table

Chunk unit = ONE LINE = one link (fixed by the data; see CLAUDE.md / the benchmark).

Run as a CLI:
    python -m app.ingest                       # load data/stripe_docs.md
    python -m app.ingest path/to/other.md      # load a different file
    python -m app.ingest --init-only           # just create the schema, no data

The same functions are importable so the Flask /load endpoint (phase d) can reuse
them. Uses app.config + app.db — one config/connection system, no duplication.
"""

import argparse
import re
import sys
from pathlib import Path

import requests

from app import config, db

# Repo paths (this file lives in app/).
REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_FILE = REPO_ROOT / "data" / "stripe_docs.md"
SCHEMA_SQL = REPO_ROOT / "db" / "schema.sql"
DOCS_LINES_SQL = REPO_ROOT / "db" / "docs_lines.sql"

EMBED_DIM = 768  # nomic-embed-text

LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^)]+)\)")
SECTION_RE = re.compile(r"^##\s+(.*)$")


# --------------------------------------------------------------- parsing -----
def parse_links(path):
    """Parse the markdown into [{section_header, content, url}], one per link.

    Mirrors the benchmark's parsing so retrieval behaves as measured:
    content = "<anchor>. <trailing description>." ; section = nearest '## ' heading.
    De-duplicates identical (url, content) pairs.
    """
    section = "General"
    seen, rows = set(), []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        sec = SECTION_RE.match(line.strip())
        if sec:
            section = sec.group(1).strip()
            continue
        m = LINK_RE.search(line)
        if not m:
            continue
        anchor, url = m.group(1).strip(), m.group(2).strip()
        after = line[m.end():].lstrip(" :").strip()          # description, if any
        content = (f"{anchor}. {after}".strip().strip(".") + ".")
        key = (url, content)
        if key in seen:
            continue
        seen.add(key)
        rows.append({"section_header": section, "content": content, "url": url})
    return rows


# ------------------------------------------------------------- embeddings ----
def embed(text):
    """Embed `text` with Ollama. Raises a clear error if Ollama/model is unavailable."""
    try:
        r = requests.post(
            f"{config.OLLAMA_HOST}/api/embeddings",
            json={"model": config.EMBED_MODEL, "prompt": text},
            timeout=60,
        )
    except requests.exceptions.RequestException as e:
        raise RuntimeError(
            f"Could not reach Ollama at {config.OLLAMA_HOST}. Is it running? ({e})"
        ) from e
    if r.status_code == 404:
        raise RuntimeError(
            f"Ollama has no model '{config.EMBED_MODEL}'. "
            f"Run: ollama pull {config.EMBED_MODEL}"
        )
    r.raise_for_status()
    vec = r.json().get("embedding")
    if not vec:
        raise RuntimeError(f"Ollama returned an empty embedding for: {text!r}")
    if len(vec) != EMBED_DIM:
        raise RuntimeError(
            f"Expected {EMBED_DIM}-dim embedding from '{config.EMBED_MODEL}', "
            f"got {len(vec)}. Check EMBED_MODEL."
        )
    return vec


def _vec_to_str(vec):
    """Format a float list as the bracketed string STRING_TO_VECTOR expects."""
    return "[" + ",".join(repr(float(x)) for x in vec) + "]"


# ----------------------------------------------------------------- schema ----
def _run_sql_script(cursor, sql_text):
    """Execute a multi-statement .sql script (strips -- comments, splits on ';')."""
    no_comments = "\n".join(
        re.sub(r"--.*$", "", line) for line in sql_text.splitlines()
    )
    for stmt in no_comments.split(";"):
        if stmt.strip():
            cursor.execute(stmt)


def ensure_schema():
    """Create the database + all tables. Idempotent; safe to call every load."""
    # Connect WITHOUT a database selected — schema.sql creates it (CREATE DATABASE).
    conn = db.get_connection(use_database=False)
    try:
        cur = conn.cursor()
        _run_sql_script(cur, SCHEMA_SQL.read_text(encoding="utf-8"))
        _run_sql_script(cur, DOCS_LINES_SQL.read_text(encoding="utf-8"))
        conn.commit()
    finally:
        conn.close()


# ----------------------------------------------------------------- ingest ----
def ingest_file(path=DATA_FILE, progress=print):
    """Parse, embed, and (re)load `path` into docs_lines. Returns a summary dict.

    Idempotent: TRUNCATEs docs_lines before inserting, so re-running never
    duplicates rows. Appends one row to the `files` log per load.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Corpus not found: {path}")
    if path.suffix.lower() != ".md":
        raise ValueError(f"Expected a .md file, got: {path.name}")

    ensure_schema()

    rows = parse_links(path)
    if not rows:
        raise ValueError(f"No links found in {path.name} — nothing to ingest.")
    progress(f"Parsed {len(rows)} link-lines from {path.name}. Embedding...")

    records = []
    for i, row in enumerate(rows, 1):
        vec = embed(row["content"])
        records.append(
            (row["section_header"], row["content"], row["url"], _vec_to_str(vec))
        )
        if i % 50 == 0 or i == len(rows):
            progress(f"  embedded {i}/{len(rows)}")

    conn = db.get_connection()
    try:
        cur = conn.cursor()
        cur.execute("TRUNCATE TABLE docs_lines")
        cur.executemany(
            """
            INSERT INTO docs_lines (section_header, content, url, embedding)
            VALUES (%s, %s, %s, STRING_TO_VECTOR(%s))
            """,
            records,
        )
        cur.execute(
            """
            INSERT INTO files (name, type, size, num_links)
            VALUES (%s, %s, %s, %s)
            """,
            (path.name, path.suffix.lstrip(".").lower(),
             path.stat().st_size, len(records)),
        )
        conn.commit()
    finally:
        conn.close()

    summary = {"file": path.name, "links": len(records), "embed_dim": EMBED_DIM}
    progress(f"Loaded {len(records)} rows into docs_lines. Done.")
    return summary


# -------------------------------------------------------------------- cli ----
def main():
    parser = argparse.ArgumentParser(description="Ingest stripe_docs.md into MySQL.")
    parser.add_argument("path", nargs="?", default=str(DATA_FILE),
                        help="markdown corpus to load (default: data/stripe_docs.md)")
    parser.add_argument("--init-only", action="store_true",
                        help="only create the schema, do not ingest data")
    args = parser.parse_args()

    try:
        if args.init_only:
            ensure_schema()
            print("Schema ensured (database + tables). No data ingested.")
            return
        ingest_file(args.path)
    except (RuntimeError, ValueError, FileNotFoundError) as e:
        sys.exit(f"Ingest failed: {e}")


if __name__ == "__main__":
    main()
