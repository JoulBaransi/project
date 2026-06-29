-- ============================================================
--  docs_lines — the RAG knowledge base, one row per link line
--  Author: Joul Baransi
--  CANONICAL definition of docs_lines. This is the single source
--  of truth for the knowledge table (schema.sql owns the database
--  plus files/qa_log, and does NOT redefine docs_lines).
--
--  Each row = a section header + the phrase/description + the link
--  to return, plus the Ollama embedding for the vector fallback.
--  Runnable standalone:  mysql stripe_rag < db/docs_lines.sql
-- ============================================================

USE stripe_rag;

-- Idempotent: indexes are declared inline so re-applying this file is a no-op
-- (no separate ALTER ... ADD INDEX that would error on a second run). Row data
-- is (re)loaded by app/ingest.py, which TRUNCATEs before inserting.
CREATE TABLE IF NOT EXISTS docs_lines (
    line_id        INT AUTO_INCREMENT PRIMARY KEY,
    section_header VARCHAR(255) NOT NULL,   -- which Stripe section the line lives under
    content        TEXT NOT NULL,           -- the distinguishing phrase / description
    url            VARCHAR(512) NOT NULL,    -- the link this line answers with
    embedding      VECTOR(768),             -- Ollama nomic-embed-text vector (768 dims),
                                            --   NULL until ingest.py fills it in
    created_at     DATETIME DEFAULT CURRENT_TIMESTAMP,

    INDEX idx_docs_lines_section (section_header),
    -- FULLTEXT powers the fast KEYWORD search (step 1 of hybrid retrieval):
    --   MATCH(content, section_header) AGAINST ('refund payment')
    FULLTEXT INDEX ft_docs_lines (content, section_header)
) ENGINE=InnoDB;

-- ---------- Reference: how embeddings are stored / read ----------
-- ingest.py inserts with STRING_TO_VECTOR on a bracketed 768-float string:
--   INSERT INTO docs_lines (section_header, content, url, embedding)
--   VALUES (%s, %s, %s, STRING_TO_VECTOR(%s));     -- 4th param = '[0.0123, -0.0456, ...]'
--
-- retrieval.py reads embeddings back out (cosine math happens in Python):
--   SELECT line_id, url, content, VECTOR_TO_STRING(embedding) FROM docs_lines;
