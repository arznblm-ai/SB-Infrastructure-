#!/usr/bin/env python3
"""Hybrid search over the Second Brain vault.

Usage:
    search.py "your question in any language" [--mode hybrid|vector|keyword]
              [--top N] [--path SUBSTR] [--json] [--full]

Modes:
    hybrid  (default) vector top-50 + BM25 top-50, fused with Reciprocal Rank
            Fusion (score = sum 1/(60 + rank)). Finds both meaning and exact
            words (names, order numbers, rare terms).
    vector  meaning only (cosine over e5 embeddings) — the original behaviour.
    keyword BM25 only (exact Unicode word tokens, no stemming). Fast: no model.

Read-only. Everything is computed locally.
"""
import sys
import json
import argparse
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as C  # noqa: E402

RRF_K = 60
CANDIDATES = 50  # top-N taken from each leg before fusion
BM25_K1, BM25_B = 1.5, 0.75


def path_mask(meta, substr):
    if not substr:
        return None
    s = substr.lower()
    return np.array([s in m["path"].lower() for m in meta], dtype=bool)


def vector_ranking(store, cfg, query, mask):
    """Chunk ids by cosine desc (path-filtered) and the similarity array."""
    model_name = store["manifest"].get("model", cfg["model_name"])
    # always query with the same model the index was built with
    cfg_q = dict(cfg)
    cfg_q["model_name"] = model_name
    emb = C.Embedder(cfg_q)
    qv = emb.embed([query], is_query=True)[0]
    sims = store["vectors"] @ qv
    order = np.argsort(-sims)
    if mask is not None:
        order = order[mask[order]]
    return order, sims


def keyword_ranking(cfg, meta, query, mask):
    """Chunk ids with BM25 > 0 by score desc (path-filtered) and the scores."""
    bm = C.BM25.load(cfg, meta, k1=BM25_K1, b=BM25_B)
    sc = bm.scores(query)
    hit = sc > 0
    if mask is not None:
        hit &= mask
    idx = np.nonzero(hit)[0]
    order = idx[np.argsort(-sc[idx], kind="stable")]
    return order, sc


def main():
    ap = argparse.ArgumentParser(description="Hybrid (vector + BM25) search over the vault.")
    ap.add_argument("query", nargs="+", help="search query (any language)")
    ap.add_argument("--mode", choices=("hybrid", "vector", "keyword"), default="hybrid",
                    help="hybrid (default) | vector (meaning only) | keyword (BM25 only)")
    ap.add_argument("--top", type=int, default=None, help="number of results")
    ap.add_argument("--path", default=None, help="only paths containing this substring")
    ap.add_argument("--per-file", type=int, default=2, help="max chunks shown per file")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--full", action="store_true", help="print full chunk text")
    args = ap.parse_args()
    query = " ".join(args.query)

    cfg = C.load_config()
    if args.mode == "keyword":
        meta = C.load_meta(cfg)
        store = {"meta": meta, "manifest": {}} if meta is not None else None
    else:
        store = C.load_store(cfg)
    if not store:
        print("No index found. Run build_index.py first.")
        sys.exit(1)

    top_k = args.top or cfg.get("top_k", 8)
    meta = store["meta"]
    model_name = store["manifest"].get("model", cfg["model_name"])
    mask = path_mask(meta, args.path)

    # ranked: list of (chunk_id, score, sources)
    if args.mode == "vector":
        order, sims = vector_ranking(store, cfg, query, mask)
        ranked = ((int(i), float(sims[i]), ["vector"]) for i in order)
    elif args.mode == "keyword":
        order, sc = keyword_ranking(cfg, meta, query, mask)
        ranked = ((int(i), float(sc[i]), ["keyword"]) for i in order)
    else:
        v_order, _ = vector_ranking(store, cfg, query, mask)
        k_order, _ = keyword_ranking(cfg, meta, query, mask)
        fused, sources, best = {}, {}, {}
        for leg, order in (("vector", v_order[:CANDIDATES]), ("keyword", k_order[:CANDIDATES])):
            for rank, i in enumerate(order, 1):
                i = int(i)
                fused[i] = fused.get(i, 0.0) + 1.0 / (RRF_K + rank)
                sources.setdefault(i, []).append(leg)
                best[i] = min(best.get(i, rank), rank)
        ids = sorted(fused, key=lambda i: (-fused[i], best[i]))
        ranked = ((i, fused[i], sources[i]) for i in ids)

    # group by file exactly as before: at most --per-file chunks per path
    results = []
    per_file = {}
    for idx, score, src in ranked:
        m = meta[idx]
        seen = per_file.get(m["path"], 0)
        if seen >= max(1, args.per_file):
            continue
        per_file[m["path"]] = seen + 1
        results.append((score, m, src))
        if len(results) >= top_k:
            break

    ndigits = 6 if args.mode == "hybrid" else 4
    if args.json:
        out = [{
            "score": round(s, ndigits),
            "path": m["path"],
            "line": m["start_line"],
            "heading": m["heading"],
            "snippet": m["text"][:300],
            "sources": src,
        } for s, m, src in results]
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return

    engine = {"vector": model_name, "keyword": "BM25",
              "hybrid": f"{model_name} + BM25 (RRF)"}[args.mode]
    print(f'\n🔎  "{query}"   ·   {args.mode}: {engine}   ·   {len(meta)} chunks\n')
    if not results:
        print("    (no results)\n")
        return
    fmt = {"vector": "{:.3f}", "keyword": "{:.2f}", "hybrid": "{:.4f}"}[args.mode]
    for rank, (s, m, src) in enumerate(results, 1):
        loc = f'{m["path"]}:{m["start_line"]}'
        head = f'  › {m["heading"]}' if m["heading"] else ""
        tag = "" if args.mode != "hybrid" else "  (" + "+".join(x[0] for x in src) + ")"
        snippet = m["text"] if args.full else " ".join(m["text"].split())[:240]
        print(f'{rank:>2}. [{fmt.format(s)}]{tag}  {loc}{head}')
        print(f'     {snippet}\n')


if __name__ == "__main__":
    main()
