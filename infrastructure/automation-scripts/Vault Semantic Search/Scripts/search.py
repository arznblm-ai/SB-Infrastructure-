#!/usr/bin/env python3
"""Semantic search over the Second Brain vault.

Usage:
    search.py "your question in any language" [--top N] [--path SUBSTR] [--json] [--full]

Searches by meaning (vector similarity), not keywords. Read-only.
"""
import sys
import json
import argparse
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as C  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description="Semantic search over the vault.")
    ap.add_argument("query", nargs="+", help="search query (any language)")
    ap.add_argument("--top", type=int, default=None, help="number of results")
    ap.add_argument("--path", default=None, help="only paths containing this substring")
    ap.add_argument("--per-file", type=int, default=2, help="max chunks shown per file")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--full", action="store_true", help="print full chunk text")
    args = ap.parse_args()
    query = " ".join(args.query)

    cfg = C.load_config()
    store = C.load_store(cfg)
    if not store:
        print("No index found. Run build_index.py first.")
        sys.exit(1)

    top_k = args.top or cfg.get("top_k", 8)
    vectors, meta = store["vectors"], store["meta"]
    model_name = store["manifest"].get("model", cfg["model_name"])

    # always query with the same model the index was built with
    cfg_q = dict(cfg)
    cfg_q["model_name"] = model_name
    emb = C.Embedder(cfg_q)
    qv = emb.embed([query], is_query=True)[0]

    sims = vectors @ qv
    order = np.argsort(-sims)

    results = []
    per_file = {}
    for idx in order:
        m = meta[int(idx)]
        if args.path and args.path.lower() not in m["path"].lower():
            continue
        seen = per_file.get(m["path"], 0)
        if seen >= max(1, args.per_file):
            continue
        per_file[m["path"]] = seen + 1
        results.append((float(sims[int(idx)]), m))
        if len(results) >= top_k:
            break

    if args.json:
        out = [{
            "score": round(s, 4),
            "path": m["path"],
            "line": m["start_line"],
            "heading": m["heading"],
            "snippet": m["text"][:300],
        } for s, m in results]
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return

    print(f'\n🔎  "{query}"   ·   {model_name}   ·   {len(meta)} chunks\n')
    if not results:
        print("    (no results)\n")
        return
    for rank, (s, m) in enumerate(results, 1):
        loc = f'{m["path"]}:{m["start_line"]}'
        head = f'  › {m["heading"]}' if m["heading"] else ""
        snippet = m["text"] if args.full else " ".join(m["text"].split())[:240]
        print(f'{rank:>2}. [{s:.3f}]  {loc}{head}')
        print(f'     {snippet}\n')


if __name__ == "__main__":
    main()
