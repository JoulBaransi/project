#!/usr/bin/env python3
"""
link_retrieval_benchmark.py
===========================

Tunes the retrieval for THIS app's real job: given a question, return the single
best Stripe documentation **link**.

The corpus (stripe_docs.md) is a list of links, one per line, each preceded by a
short distinguishing phrase, e.g.:

    - [Refund and cancel payments](https://docs.stripe.com/refunds.md): Learn how
      to cancel or refund a payment.

So the natural unit of retrieval is ONE LINE = one link + its phrase. We do NOT
chunk by word count or by section: a section contains dozens of links, so it
could never return a single answer. Each line is its own chunk.

This script answers the settings you need:

  1. ALGORITHM   - which similarity method returns the right link most often?
  2. CHUNK UNIT  - confirmed as one-line-per-link (with the data to justify it)
  3. # CHUNKS    - = number of links in the doc
  4. K           - how many links to return / send to the model?
  5. THRESHOLD   - what similarity score counts as "relevant enough"?

Scoring: each sample question is labelled with the exact URL that answers it.
A retrieval is "correct" if that URL is the one returned.

Usage:
    python link_retrieval_benchmark.py [path/to/stripe_docs.md] [--verbose]
    pip install scikit-learn rank_bm25 numpy           # lexical methods
    pip install sentence-transformers                  # semantic method
"""

import re
import sys
import time
from collections import Counter
from difflib import SequenceMatcher
from pathlib import Path

# ---- optional deps ----------------------------------------------------------
try:
    import numpy as np
    HAVE_NUMPY = True
except ImportError:
    HAVE_NUMPY = False
try:
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.metrics.pairwise import cosine_similarity
    HAVE_SKLEARN = True
except ImportError:
    HAVE_SKLEARN = False
try:
    from rank_bm25 import BM25Okapi
    HAVE_BM25 = True
except ImportError:
    HAVE_BM25 = False
try:
    from sentence_transformers import SentenceTransformer
    HAVE_ST = True
except ImportError:
    HAVE_ST = False
try:
    import requests
    HAVE_REQUESTS = True
except ImportError:
    HAVE_REQUESTS = False


LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^)]+)\)")

STOPWORDS = set("""
a an the of to for in on at by and or but with without how do does did i my me
you your we our it its is are was were be been being this that these those as
from into about can could should would will what which who whom when where why
using use used want need help guide learn
""".split())


def tokenize(text):
    toks = re.findall(r"[a-z0-9]+", text.lower())
    return [t for t in toks if t not in STOPWORDS and len(t) > 1]


# ----------------------------------------------------------------------------
# Parse the doc two ways: into link entries (our chunks) and into sections
# (only used to show how many links a coarse chunk would lump together).
# ----------------------------------------------------------------------------
def load_link_entries(path):
    """One chunk per link line: (url, searchable_phrase)."""
    seen, entries = set(), []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        m = LINK_RE.search(line)
        if not m:
            continue
        anchor, url = m.group(1).strip(), m.group(2).strip()
        after = line[m.end():].lstrip(" :").strip()        # description, if any
        phrase = f"{anchor}. {after}".strip().strip(".") + "."
        key = (url, phrase)
        if key in seen:
            continue
        seen.add(key)
        entries.append((url, phrase))
    return entries


def links_per_section(path):
    """How many links live under each '##' heading (to justify per-line chunks)."""
    counts, title, n = [], None, 0
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line.startswith("## "):
            if title is not None:
                counts.append((title, n))
            title, n = line[3:].strip(), 0
        elif title is not None and LINK_RE.search(line):
            n += 1
    if title is not None:
        counts.append((title, n))
    return counts


# ----------------------------------------------------------------------------
# Sample questions -> the exact URL that answers them.
# (Filtered at runtime to those actually present in the doc.)
# ----------------------------------------------------------------------------
SAMPLE_QUERIES = [
    ("How do I refund or cancel a payment?",                 "https://docs.stripe.com/refunds.md"),
    ("How do I simulate payments to test my integration?",   "https://docs.stripe.com/testing.md"),
    ("How do I set up my bank account to receive payouts?",  "https://docs.stripe.com/payouts.md"),
    ("How do I build a marketplace or platform?",            "https://docs.stripe.com/connect.md"),
    ("Which currencies does Stripe support?",                "https://docs.stripe.com/currencies.md"),
    ("How do I handle API version upgrades and breaking changes?", "https://docs.stripe.com/upgrades.md"),
    ("How do I receive webhook events from Stripe?",         "https://docs.stripe.com/webhooks.md"),
    ("How do I lower my payment decline rate?",              "https://docs.stripe.com/declines.md"),
    ("How does Stripe handle security?",                     "https://docs.stripe.com/security.md"),
    ("How do I build a Stripe-hosted checkout page?",        "https://docs.stripe.com/checkout/quickstart.md"),
    ("How do I accept a payment online?",                    "https://docs.stripe.com/payments/accept-a-payment.md"),
    ("How do I fix a webhook signature verification error?", "https://docs.stripe.com/webhooks/signature.md"),
    ("How do I accept bank debits?",                         "https://docs.stripe.com/payments/bank-debits.md"),
    ("How do I add Klarna as a payment method?",             "https://docs.stripe.com/payments/klarna.md"),
    ("How do I fulfill orders after checkout?",              "https://docs.stripe.com/checkout/fulfillment.md"),
    ("How do I add discounts to my checkout?",               "https://docs.stripe.com/payments/checkout/discounts.md"),
    ("How do I cancel a subscription?",                      "https://docs.stripe.com/billing/subscriptions/cancel.md"),
    ("How do I add coupons and promotion codes to subscriptions?", "https://docs.stripe.com/billing/subscriptions/coupons.md"),
    ("How do I issue virtual cards?",                        "https://docs.stripe.com/issuing/cards/virtual.md"),
    ("How do I calculate tax on a transaction?",             "https://docs.stripe.com/tax/calculating.md"),
    ("How do I accept stablecoin or crypto payments?",       "https://docs.stripe.com/payments/accept-stablecoin-payments.md"),
    ("How do I invite team members to my account?",          "https://docs.stripe.com/get-started/account/teams.md"),
]


# ----------------------------------------------------------------------------
# Similarity methods (each: query -> [score per chunk]). Chunks here are links.
# ----------------------------------------------------------------------------
def build_methods(entries, lexical_only=False):
    urls = [u for u, _ in entries]
    texts = [t for _, t in entries]
    corpus_tokens = [tokenize(t) for t in texts]
    methods, skipped = {}, {}

    token_sets = [set(toks) for toks in corpus_tokens]

    def jaccard(query):
        q = set(tokenize(query))
        return [len(q & d) / (len(q | d) or 1) for d in token_sets]
    methods["Jaccard overlap"] = jaccard

    if HAVE_SKLEARN:
        vec = TfidfVectorizer(stop_words="english")
        matrix = vec.fit_transform(texts)

        def tfidf(query):
            return cosine_similarity(vec.transform([query]), matrix)[0].tolist()
        methods["TF-IDF cosine"] = tfidf
    else:
        skipped["TF-IDF cosine"] = "pip install scikit-learn"

    if HAVE_BM25:
        bm25 = BM25Okapi(corpus_tokens)

        def bm25_score(query):
            return list(bm25.get_scores(tokenize(query)))
        methods["BM25"] = bm25_score
    else:
        skipped["BM25"] = "pip install rank_bm25"

    lowered = [t.lower() for t in texts]

    def seqmatch(query):
        q = query.lower()
        return [SequenceMatcher(None, q, t).ratio() for t in lowered]
    methods["SequenceMatcher"] = seqmatch

    if HAVE_ST and HAVE_NUMPY and not lexical_only:
        model = SentenceTransformer("all-MiniLM-L6-v2")
        emb = model.encode(texts, normalize_embeddings=True)

        def st_score(query):
            qe = model.encode([query], normalize_embeddings=True)[0]
            return (emb @ qe).tolist()
        methods["MiniLM embeddings"] = st_score
    elif not lexical_only:
        skipped["MiniLM embeddings"] = "pip install sentence-transformers numpy"

    if HAVE_REQUESTS and HAVE_NUMPY and not lexical_only and _ollama_up():
        name = "nomic-embed-text"
        try:
            emb = np.array([_ollama_embed(name, t) for t in texts])
            emb /= (np.linalg.norm(emb, axis=1, keepdims=True) + 1e-9)

            def ollama_score(query):
                qe = np.array(_ollama_embed(name, query))
                qe /= (np.linalg.norm(qe) + 1e-9)
                return (emb @ qe).tolist()
            methods[f"Ollama ({name})"] = ollama_score
        except Exception as e:                       # pragma: no cover
            skipped["Ollama embeddings"] = f"error: {e}"
    elif not lexical_only:
        skipped["Ollama embeddings"] = "start Ollama + `ollama pull nomic-embed-text`"

    return methods, skipped, urls


def _ollama_up(host="http://localhost:11434"):
    if not HAVE_REQUESTS:
        return False
    try:
        requests.get(f"{host}/api/tags", timeout=1.0)
        return True
    except Exception:
        return False


def _ollama_embed(model, text, host="http://localhost:11434"):
    r = requests.post(f"{host}/api/embeddings",
                      json={"model": model, "prompt": text}, timeout=30)
    r.raise_for_status()
    return r.json()["embedding"]


# ----------------------------------------------------------------------------
# Scoring helpers
# ----------------------------------------------------------------------------
def per_query_ranking(scorer, queries):
    out = []
    for question, expected in queries:
        scores = scorer(question)
        order = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
        out.append({"expected": expected, "order": order, "scores": scores})
    return out


def correct(url, expected):
    return url == expected


def evaluate(methods, urls, queries, k=3):
    results = {}
    for name, scorer in methods.items():
        hits1 = hits3 = 0
        elapsed = 0.0
        examples = []
        for question, expected in queries:
            t0 = time.perf_counter()
            scores = scorer(question)
            elapsed += time.perf_counter() - t0
            order = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:k]
            top_urls = [urls[i] for i in order]
            ok1 = correct(top_urls[0], expected)
            ok3 = any(correct(u, expected) for u in top_urls)
            hits1 += ok1
            hits3 += ok3
            examples.append((question, expected, top_urls[0], ok1))
        n = len(queries)
        results[name] = {"top1": hits1 / n, "top3": hits3 / n,
                         "avg_ms": (elapsed / n) * 1000, "examples": examples}
    return results


def choose_cosine_method(methods):
    for pref in ("Ollama (nomic-embed-text)", "MiniLM embeddings", "TF-IDF cosine"):
        if pref in methods:
            return pref
    return None


# ----------------------------------------------------------------------------
# Part 1 — algorithm
# ----------------------------------------------------------------------------
def print_part1(results, skipped, queries, verbose):
    print("=" * 74)
    print("PART 1 - WHICH SIMILARITY ALGORITHM RETURNS THE RIGHT LINK?")
    print("=" * 74)
    print(f"Test set: {len(queries)} questions, each labelled with its exact answer URL\n")

    if verbose:
        for i, (q, exp) in enumerate(queries, 1):
            print(f"\nQ{i}. {q}")
            print(f"    answer: {exp}")
            for name, d in results.items():
                _, _, picked, ok = d["examples"][i - 1]
                print(f"     {'OK ' if ok else '  x'} {name:<22} -> {picked}")
        print()

    header = f"{'Method':<24}{'Top-1':>9}{'Top-3':>9}{'Avg ms/q':>12}"
    print(header)
    print("-" * len(header))
    ranked = sorted(results.items(), key=lambda kv: kv[1]["top1"], reverse=True)
    for name, d in ranked:
        print(f"{name:<24}{d['top1']*100:>8.1f}%{d['top3']*100:>8.1f}%{d['avg_ms']:>12.2f}")
    if skipped:
        print("\nSkipped: " + "; ".join(f"{n} ({r})" for n, r in skipped.items()))
    return ranked[0][0] if ranked else None


# ----------------------------------------------------------------------------
# Part 2 — chunk unit (justify one-line-per-link)
# ----------------------------------------------------------------------------
def print_part2(entries, section_counts):
    print("\n" + "=" * 74)
    print("PART 2 - CHUNK UNIT:  why one line (one link) per chunk")
    print("=" * 74)
    total = sum(c for _, c in section_counts)
    avg = total / max(1, len(section_counts))
    busiest = max(section_counts, key=lambda x: x[1])
    print(f"Links in the doc (= chunks)      : {len(entries)}")
    print(f"Sections (coarse chunks)         : {len(section_counts)}")
    print(f"Avg links per section            : {avg:.1f}")
    print(f"Busiest section                  : '{busiest[0]}' with {busiest[1]} links")
    print()
    print("If you chunked by section, a single 'correct' retrieval would hand back")
    print(f"~{avg:.0f} links on average (up to {busiest[1]}), so it can't answer with ONE link.")
    print("One line per link is the only unit that returns a single answer, so the")
    print("chunk unit is fixed by the data — not a size to tune.")
    return len(entries)


# ----------------------------------------------------------------------------
# Part 3 — K
# ----------------------------------------------------------------------------
def print_part3(methods, method_name, urls, queries, max_k):
    print("\n" + "=" * 74)
    print("PART 3 - K:  how many links should you return / send to the model?")
    print("=" * 74)
    print(f"(best method: {method_name})  recall@K = how often the right link is in the top K\n")
    rankings = per_query_ranking(methods[method_name], queries)
    n = len(queries)
    rows = []
    for k in range(1, max_k + 1):
        hits = sum(any(correct(urls[i], r["expected"]) for i in r["order"][:k])
                   for r in rankings)
        rows.append((k, hits / n))
    print(f"{'K':>3}{'recall@K':>12}")
    print("-" * 17)
    for k, rec in rows:
        print(f"{k:>3}{rec*100:>10.1f}%  {'#' * int(rec * 30)}")
    max_recall = rows[-1][1]
    rec_k = next((k for k, r in rows if r >= max_recall - 0.001), rows[-1][0])
    print(f"\n-> Recommended K: {rec_k}  (recall reaches {max_recall*100:.1f}% by K={rec_k})")
    return rec_k


# ----------------------------------------------------------------------------
# Part 4 — threshold
# ----------------------------------------------------------------------------
def print_part4(methods, urls, queries):
    print("\n" + "=" * 74)
    print("PART 4 - SIMILARITY THRESHOLD:  what score is 'relevant enough'?")
    print("=" * 74)
    cos = choose_cosine_method(methods)
    if cos is None:
        print("No cosine (0..1) method available; install scikit-learn or run embeddings.")
        return None
    print(f"(measured on {cos} — the 0..1 cosine scale your Ollama embeddings use)\n")
    rankings = per_query_ranking(methods[cos], queries)
    good, bad = [], []
    for r in rankings:
        top = r["order"][0]
        (good if correct(urls[top], r["expected"]) else bad).append(r["scores"][top])

    def st(xs):
        return (min(xs), sum(xs) / len(xs), max(xs)) if xs else (0, 0, 0)
    gmin, gmean, gmax = st(good)
    bmin, bmean, bmax = st(bad)
    print(f"Top score when CORRECT : min {gmin:.3f}  mean {gmean:.3f}  max {gmax:.3f}  (n={len(good)})")
    print(f"Top score when WRONG   : min {bmin:.3f}  mean {bmean:.3f}  max {bmax:.3f}  (n={len(bad)})")
    if good and bad and gmin > bmax:
        thr, note = round((gmin + bmax) / 2, 3), "clean gap between right and wrong"
    elif good:
        thr, note = round(max(0.05, gmin * 0.8), 3), "some overlap; floor below weakest correct"
    else:
        thr, note = 0.2, "fallback"
    print(f"\n-> Recommended threshold: cosine >= {thr}  ({note})")
    return thr


def print_reco(algo, n_chunks, rec_k, thr):
    print("\n" + "#" * 74)
    print("#  RECOMMENDED RAG SETTINGS  (link-retrieval task)")
    print("#" * 74)
    print(f"#  Algorithm     : {algo}  (embeddings = safest production default)")
    print("#  Chunk unit    : 1 line = 1 link + its phrase  (fixed by the data)")
    print(f"#  # chunks      : {n_chunks}  (one per link in the doc)")
    print(f"#  K (return)    : {rec_k} link(s) per question")
    if thr is not None:
        print(f"#  Similarity floor : cosine >= {thr} to accept a link")
    print("#" * 74)
    print("Re-run with embeddings enabled (MiniLM/Ollama) before locking values.")


# ----------------------------------------------------------------------------
def main():
    argv = sys.argv[1:]
    verbose = any(a in ("-v", "--verbose") for a in argv)
    pos = [a for a in argv if not a.startswith("-")]
    path = pos[0] if pos else "stripe_docs.md"
    if not Path(path).exists():
        sys.exit(f"Could not find '{path}'. Pass the path to stripe_docs.md.")

    entries = load_link_entries(path)
    if not entries:
        sys.exit("No links found in the document.")
    urlset = {u for u, _ in entries}

    # Keep only sample questions whose answer URL is actually in the doc.
    queries = [(q, u) for q, u in SAMPLE_QUERIES if u in urlset]
    dropped = [u for q, u in SAMPLE_QUERIES if u not in urlset]
    print(f"Loaded {len(entries)} link-chunks. Using {len(queries)}/{len(SAMPLE_QUERIES)} "
          f"sample questions whose answer is present.")
    if dropped:
        print("  (dropped — URL not found: " + ", ".join(d.split('/')[-1] for d in dropped) + ")")
    print()

    methods, skipped, urls = build_methods(entries)
    if not methods:
        sys.exit("No similarity methods available.")
    results = evaluate(methods, urls, queries, k=3)
    winner = print_part1(results, skipped, queries, verbose)

    n_chunks = print_part2(entries, links_per_section(path))

    k_method = winner
    max_k = min(10, len(entries))
    rec_k = print_part3(methods, k_method, urls, queries, max_k)

    thr = print_part4(methods, urls, queries)
    print_reco(winner, n_chunks, rec_k, thr)


if __name__ == "__main__":
    main()
