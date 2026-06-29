-- ============================================================
--  schema.sql — database bootstrap for the Stripe Docs RAG app
--  Author: Joul Baransi
--
--  Owns: the `stripe_rag` database + the two LOG tables
--        (files, qa_log). It does NOT define docs_lines — that
--        lives canonically in db/docs_lines.sql (single source
--        of truth). Apply order: schema.sql first, then
--        docs_lines.sql. app/ingest.py applies both for you.
--
--  NOTE: the database name `stripe_rag` is hardcoded here and must
--  match DB_NAME in your .env (default: stripe_rag).
-- ============================================================

CREATE DATABASE IF NOT EXISTS stripe_rag
    CHARACTER SET utf8mb4
    COLLATE utf8mb4_unicode_ci;

USE stripe_rag;

-- files — a log of every corpus load (history shown in the UI).
CREATE TABLE IF NOT EXISTS files (
    file_id     INT AUTO_INCREMENT PRIMARY KEY,
    name        VARCHAR(255) NOT NULL,    -- e.g. stripe_docs.md
    type        VARCHAR(50)  NOT NULL,    -- file extension, e.g. md
    size        BIGINT       NOT NULL,    -- bytes
    num_links   INT          NOT NULL,    -- link-lines ingested from this load
    uploaded_at DATETIME DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB;

-- qa_log — every question/answer, for the UI history and auditing.
CREATE TABLE IF NOT EXISTS qa_log (
    qa_id         INT AUTO_INCREMENT PRIMARY KEY,
    question      TEXT NOT NULL,
    answer        TEXT NOT NULL,
    retrieved_via VARCHAR(16) NOT NULL,   -- 'keyword' | 'vector' | 'none'
    top_url       VARCHAR(512),           -- the single best link (NULL if none found)
    created_at    DATETIME DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB;
