"""Turn the downloaded parquet files into L2-normalized float32 arrays, streaming by batch (low memory).

Usage: python scripts/prepare.py <source_ds> <out_name> [n_base] [seed]
  python scripts/prepare.py cohere_small_100k c100k
  python scripts/prepare.py cohere_medium_1m c1m
  python scripts/prepare.py cohere_medium_1m c300k 300000 20261022   # seeded random subset of 1M

Outputs data/<out_name>/{base.npy, base_ids.npy, queries.npy, queries.f32, query_ids.npy, meta.json}.
queries.f32 is the same query matrix as raw little-endian float32 for the Go and Rust store-only benches.
"""
import json, sys, time, hashlib
from pathlib import Path
import numpy as np, pyarrow.parquet as pq

def batches(path, bs=50_000):
    f = pq.ParquetFile(path)
    for b in f.iter_batches(batch_size=bs, columns=["id", "emb"]):
        ids = b.column("id").to_numpy()
        flat = b.column("emb").flatten().to_numpy(zero_copy_only=False).astype(np.float32)
        yield ids, flat.reshape(len(ids), -1)

def norm(x):
    return (x / np.linalg.norm(x, axis=1, keepdims=True)).astype(np.float32)

def main():
    src, out = sys.argv[1], sys.argv[2]
    n = int(sys.argv[3]) if len(sys.argv) > 3 else None
    seed = int(sys.argv[4]) if len(sys.argv) > 4 else 20261022
    root = Path(__file__).resolve().parent.parent / "data"
    t0 = time.time()
    total = pq.ParquetFile(root / src / "train.parquet").metadata.num_rows
    keep = np.ones(total, bool)
    if n is not None and n < total:
        keep[:] = False; keep[np.random.default_rng(seed).choice(total, n, replace=False)] = True
    m = int(keep.sum()); o = root / out; o.mkdir(parents=True, exist_ok=True)
    base = None; ids_out = np.empty(m, np.int64); pos = 0; row = 0; h = hashlib.sha256()
    for ids, x in batches(root / src / "train.parquet"):
        sel = keep[row:row + len(ids)]; row += len(ids)
        x = norm(x[sel]); ids = ids[sel]
        if base is None:
            base = np.lib.format.open_memmap(o / "base.npy", mode="w+", dtype=np.float32, shape=(m, x.shape[1]))
        base[pos:pos + len(ids)] = x; ids_out[pos:pos + len(ids)] = ids; pos += len(ids); h.update(x.tobytes())
    base.flush(); np.save(o / "base_ids.npy", ids_out)
    qids, q = next(batches(root / src / "test.parquet", 10_000)); q = norm(q)
    np.save(o / "queries.npy", q); np.save(o / "query_ids.npy", qids.astype(np.int64)); q.astype("<f4").tofile(o / "queries.f32")
    meta = dict(source=src, n_base=m, n_queries=int(len(qids)), dim=int(base.shape[1]), subset_seed=seed if n else None,
                normalized="l2", base_sha256=h.hexdigest(), seconds=round(time.time() - t0, 2))
    (o / "meta.json").write_text(json.dumps(meta, indent=2)); print(json.dumps(meta))

if __name__ == "__main__":
    main()
