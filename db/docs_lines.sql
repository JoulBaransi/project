-- ============================================================
--  docs_lines — the RAG knowledge base, one row per link line
--  Author: Joul Baransi
--  Each row = a section header + the phrase/description + the link
--  to return. Optionally stores the embedding for similarity search.
-- ============================================================

USE shop;   -- or your RAG database; change as needed

DROP TABLE IF EXISTS docs_lines;

CREATE TABLE docs_lines (
    line_id        INT AUTO_INCREMENT PRIMARY KEY,
    section_header VARCHAR(255) NOT NULL,   -- which Stripe section the line lives under
    content        TEXT NOT NULL,           -- the distinguishing phrase / description
    url            VARCHAR(512) NOT NULL,    -- the link this line answers with
    embedding      VECTOR(768),             -- Ollama nomic-embed-text vector (768 dims)
                                            --   NULL until you fill it in from the app
    created_at     DATETIME DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB;

-- Helpful indexes for the non-vector lookups
CREATE INDEX idx_docs_lines_section ON docs_lines (section_header);

-- FULLTEXT index powers the fast KEYWORD search (step 1 of hybrid retrieval).
-- Lets you run: MATCH(content, section_header) AGAINST ('refund payment')
ALTER TABLE docs_lines
    ADD FULLTEXT INDEX ft_docs_lines (content, section_header);

-- ---------- Example inserts ----------
-- Plain text columns:
INSERT INTO docs_lines (section_header, content, url) VALUES
    ('Docs', 'Refund and cancel payments. Learn how to cancel or refund a payment.',
     'https://docs.stripe.com/refunds.md'),
    ('Docs', 'Testing. Simulate payments to test your integration.',
     'https://docs.stripe.com/testing.md');

-- To store an embedding, pass the vector as a bracketed string:
--   UPDATE docs_lines
--   SET embedding = STRING_TO_VECTOR('[0.0123, -0.0456, ...]')   -- 768 floats
--   WHERE line_id = 1;

-- To read embeddings back out (you do the cosine math in Python):
--   SELECT line_id, url, content, VECTOR_TO_STRING(embedding) FROM docs_lines;
