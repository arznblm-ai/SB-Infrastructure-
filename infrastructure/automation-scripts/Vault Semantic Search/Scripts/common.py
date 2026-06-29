"""Shared helpers for Vault Semantic Search.

Config loading, vault file walking, markdown chunking, local ONNX embeddings
(via fastembed), and on-disk store/cache I/O. No note bodies are ever written.
"""
from __future__ import annotations

import os
import re
import json
import hashlib
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent
DEFAULT_CONFIG = PROJECT / "config" / "config.json"

HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")


# ---------------------------------------------------------------- config ----
def load_config(path=None):
    cfg_path = Path(path) if path else DEFAULT_CONFIG
    with open(cfg_path, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    for key in ("vault_root", "store_dir", "model_cache_dir"):
        cfg[key] = os.path.expanduser(cfg[key])
    return cfg


# ------------------------------------------------------------- file walk ----
def iter_files(cfg):
    root = Path(cfg["vault_root"])
    include_ext = {e.lower() for e in cfg.get("include_ext", [".md"])}
    exclude_dirs = set(cfg.get("exclude_dirs", []))
    exclude_names = set(cfg.get("exclude_names", []))
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        if p.suffix.lower() not in include_ext:
            continue
        rel_parts = p.relative_to(root).parts
        if exclude_dirs & set(rel_parts[:-1]):
            continue
        if p.name in exclude_names:
            continue
        yield p


# -------------------------------------------------------------- chunking ----
def chunk_text(text, cfg):
    """Split a markdown note into overlapping chunks, tracking the nearest
    preceding heading and the start line of each chunk."""
    chunk_chars = cfg.get("chunk_chars", 1200)
    overlap = cfg.get("chunk_overlap", 200)
    min_chars = cfg.get("min_chunk_chars", 80)

    lines = text.splitlines()
    chunks = []
    cur = []
    cur_len = 0
    start_line = 1
    cur_heading = ""
    heading = ""

    def flush(start, head, buf):
        s = "\n".join(buf).strip()
        if len(s) >= min_chars:
            chunks.append({"start_line": start, "heading": head, "text": s})

    for i, line in enumerate(lines, start=1):
        m = HEADING_RE.match(line)
        if m:
            heading = m.group(2).strip()
        if not cur:
            start_line = i
            cur_heading = heading
        cur.append(line)
        cur_len += len(line) + 1
        if cur_len >= chunk_chars:
            flush(start_line, cur_heading, cur)
            if overlap > 0:
                tail = "\n".join(cur)[-overlap:]
                cur = [tail]
                cur_len = len(tail)
                start_line = i
                cur_heading = heading
            else:
                cur = []
                cur_len = 0
    if cur:
        flush(start_line, cur_heading, cur)
    return chunks


def chunk_hash(model_name, text):
    return hashlib.sha1((model_name + " " + text).encode("utf-8")).hexdigest()


# ------------------------------------------------------------- embedding ----
def l2_normalize(arr):
    arr = np.asarray(arr, dtype=np.float32)
    if arr.ndim == 1:
        n = float(np.linalg.norm(arr)) or 1.0
        return arr / n
    norms = np.linalg.norm(arr, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return arr / norms


class Embedder:
    """Thin wrapper over fastembed. e5 models get query:/passage: prefixes."""

    def __init__(self, cfg):
        from fastembed import TextEmbedding

        self.model_name = cfg["model_name"]
        os.makedirs(cfg["model_cache_dir"], exist_ok=True)
        self.model = TextEmbedding(
            model_name=self.model_name, cache_dir=cfg["model_cache_dir"]
        )
        self.is_e5 = "e5" in self.model_name.lower()

    def _prep(self, texts, is_query):
        if self.is_e5:
            prefix = "query: " if is_query else "passage: "
            return [prefix + t for t in texts]
        return list(texts)

    def embed(self, texts, is_query=False):
        prepped = self._prep(list(texts), is_query)
        vecs = list(self.model.embed(prepped))
        return l2_normalize(np.array(vecs, dtype=np.float32))


# ----------------------------------------------------------------- store ----
def store_paths(cfg):
    d = Path(cfg["store_dir"])
    return {
        "dir": d,
        "vectors": d / "vectors.npy",
        "meta": d / "meta.jsonl",
        "manifest": d / "manifest.json",
        "cache": d / "cache.npz",
    }


def save_store(cfg, vectors, meta, manifest):
    sp = store_paths(cfg)
    sp["dir"].mkdir(parents=True, exist_ok=True)
    np.save(sp["vectors"], vectors.astype(np.float32))
    with open(sp["meta"], "w", encoding="utf-8") as f:
        for m in meta:
            f.write(json.dumps(m, ensure_ascii=False) + "\n")
    with open(sp["manifest"], "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)


def load_store(cfg):
    sp = store_paths(cfg)
    if not sp["vectors"].exists():
        return None
    vectors = np.load(sp["vectors"])
    meta = []
    with open(sp["meta"], "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                meta.append(json.loads(line))
    manifest = {}
    if sp["manifest"].exists():
        with open(sp["manifest"], "r", encoding="utf-8") as f:
            manifest = json.load(f)
    return {"vectors": vectors, "meta": meta, "manifest": manifest}


def load_cache(cfg):
    sp = store_paths(cfg)
    if not sp["cache"].exists():
        return {}
    try:
        data = np.load(sp["cache"], allow_pickle=True)
        keys, vecs = data["keys"], data["vecs"]
        return {str(k): vecs[i] for i, k in enumerate(keys)}
    except Exception:
        return {}


def save_cache(cfg, cache):
    sp = store_paths(cfg)
    sp["dir"].mkdir(parents=True, exist_ok=True)
    if not cache:
        return
    keys = np.array(list(cache.keys()), dtype=object)
    vecs = np.array(list(cache.values()), dtype=np.float32)
    np.savez(sp["cache"], keys=keys, vecs=vecs)
