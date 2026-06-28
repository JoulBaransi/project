# Kickoff prompt — paste this into Claude Code

Read `CLAUDE.md` first, then skim the files under `data/`, `db/`, `app/`, and
`benchmarks/` so you understand the project and the decisions already made.

This is a local RAG app: it answers Stripe questions by retrieving the most
relevant documentation link from `data/stripe_docs.md` and answering with a local
Ollama model (`llama3.2` for generation, `nomic-embed-text` for embeddings).
Stack: Streamlit UI → Flask API → retrieval (keyword + vector) + MySQL + Ollama,
all shipped with Docker. Target Python 3.12. Never use a cloud API.

Reuse the provided `app/retrieval.py`, `app/prompt_template.py`, and
`db/docs_lines.sql` — adapt, don't rewrite.

Work like this:
1. Ask me any clarifying questions and list the assumptions you're making. Wait
   for my answers before coding.
2. Propose the architecture and the full file tree, and pause for my approval.
3. Then build **Phase (a) only** — Flask skeleton, project structure, `db.py`,
   and `.env.example` — and stop. Tell me exactly how to run and verify it before
   moving on to the next phase.

Do not build all phases at once. After each phase, wait for me to confirm.
