#!/usr/bin/env python3
"""Build / refresh the vault semantic-search index.

Walks the vault, chunks every eligible markdown file, embeds chunks with a
local ONNX model and writes a vector store outside the vault. Re-embedding is
skipped for chunks whose content is unchanged (per-chunk content cache), so
repeat runs are fast. Never writes inside the vault.
"""
import sys
import time
import json
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as C  # noqa: E402


def main():
    cfg = C.load_config()
    t0 = time.time()

    files = list(C.iter_files(cfg))
    print(f"[index] vault:           {cfg['vault_root']}")
    print(f"[index] eligible files:  {len(files)}")

    # invalidate the embedding cache if the model changed (dims/embeddings differ)
    prev_model = None
    sp = C.store_paths(cfg)
    if sp["manifest"].exists():
        try:
            with open(sp["manifest"], "r", encoding="utf-8") as f:
                prev_model = json.load(f).get("model")
        except Exception:
            prev_model = None
    if prev_model and prev_model != cfg["model_name"]:
        print(f"[index] model changed: {prev_model} -> {cfg['model_name']} (full re-embed)")
        cache = {}
    else:
        cache = C.load_cache(cfg)
    print(f"[index] cached vectors:  {len(cache)}")

    chunk_records = []          # final, ordered list of chunk metadata
    to_embed_texts = []
    to_embed_hashes = []

    for p in files:
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except Exception as e:
            print(f"[skip] {p}: {e}")
            continue
        rel = p.relative_to(cfg["vault_root"]).as_posix()
        mtime = p.stat().st_mtime
        for ch in C.chunk_text(text, cfg):
            h = C.chunk_hash(cfg["model_name"], ch["text"])
            chunk_records.append({
                "path": rel,
                "start_line": ch["start_line"],
                "heading": ch["heading"],
                "text": ch["text"],
                "mtime": mtime,
                "hash": h,
            })
            if h not in cache:
                to_embed_hashes.append(h)
                to_embed_texts.append(ch["text"])

    print(f"[index] total chunks:    {len(chunk_records)}")
    print(f"[index] new to embed:    {len(to_embed_texts)}")

    if to_embed_texts:
        # de-duplicate identical new chunks before embedding
        uniq = {}
        for h, t in zip(to_embed_hashes, to_embed_texts):
            uniq.setdefault(h, t)
        uniq_hashes = list(uniq.keys())
        uniq_texts = list(uniq.values())

        emb = C.Embedder(cfg)
        print(f"[index] model:           {emb.model_name}")
        print("[index] (first run downloads the model once, then fully offline)")

        B = 256
        for i in range(0, len(uniq_texts), B):
            vecs = emb.embed(uniq_texts[i:i + B], is_query=False)
            for h, v in zip(uniq_hashes[i:i + B], vecs):
                cache[h] = v
            done = min(i + B, len(uniq_texts))
            print(f"[index] embedded {done}/{len(uniq_texts)}", end="\r", flush=True)
        print()

    if not cache:
        print("[index] nothing to index — no chunks found.")
        return

    dim = len(next(iter(cache.values())))
    vectors = np.zeros((len(chunk_records), dim), dtype=np.float32)
    meta_out = []
    for i, rec in enumerate(chunk_records):
        vectors[i] = cache[rec["hash"]]
        rec_out = dict(rec)
        rec_out["id"] = i
        meta_out.append(rec_out)

    # prune cache to only what the current vault still uses
    used = {rec["hash"] for rec in chunk_records}
    cache = {h: v for h, v in cache.items() if h in used}

    manifest = {
        "model": cfg["model_name"],
        "dim": dim,
        "n_chunks": len(chunk_records),
        "n_files": len(files),
        "built_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "vault_root": cfg["vault_root"],
    }
    C.save_store(cfg, vectors, meta_out, manifest)
    C.save_cache(cfg, cache)

    print(f"[index] saved store ->   {cfg['store_dir']}")
    print(f"[index] done in {time.time() - t0:.1f}s "
          f"| files={len(files)} chunks={len(chunk_records)} dim={dim}")


if __name__ == "__main__":
    main()
