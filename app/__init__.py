"""Stripe Docs RAG Assistant — Flask backend package.

This package owns ALL application logic (retrieval, DB, Ollama). The Streamlit UI
never imports from here; it only talks to the Flask API over HTTP.
"""
