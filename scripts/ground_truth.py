"""Exact top-K neighbors by brute force (cosine on normalized vectors = dot product).

Usage: python scripts/ground_truth.py <name> [K=100] [--check <source_ds>]
Writes data/<name>/gt_ids.npy (row ids into base_ids) and gt_scores.npy.
--check compares our top-10 with the published neighbors.parquet of the source set
(only meaningful when <name> is the full, unsampled source).
"""
import json, sys, time
from pathlib import Path
import numpy as np, pyarrow.parquet as pq

def main():
    name = sys.argv[1]
    K = int(sys.argv[2]) if len(sys.argv) > 2 and not sys.argv[2].startswith("--") else 100
    d = Path(__file__).resolve().parent.parent / "data" / name
    base = np.load(d / "base.npy", mmap_mode="r"); q = np.load(d / "queries.npy")
    ids = np.load(d / "base_ids.npy")
    t0 = time.time()
    best_s = np.full((len(q), K), -np.inf, np.float32); best_i = np.zeros((len(q), K), np.int64)
    step = 200_000
    for s in range(0, len(base), step):
        sc = q @ np.asarray(base[s:s + step]).T
        k = min(K, sc.shape[1])
        part = np.argpartition(-sc, k - 1, axis=1)[:, :k]
        cs = np.take_along_axis(sc, part, 1)
        all_s = np.concatenate([best_s, cs], 1); all_i = np.concatenate([best_i, part + s], 1)
        top = np.argpartition(-all_s, K - 1, axis=1)[:, :K]
        best_s = np.take_along_axis(all_s, top, 1); best_i = np.take_along_axis(all_i, top, 1)
    order = np.argsort(-best_s, axis=1, kind="stable")
    best_s = np.take_along_axis(best_s, order, 1); best_i = np.take_along_axis(best_i, order, 1)
    gt_ids = ids[best_i]
    np.save(d / "gt_ids.npy", gt_ids); np.save(d / "gt_scores.npy", best_s)
    rep = dict(name=name, k=K, seconds=round(time.time() - t0, 2))
    if "--check" in sys.argv:
        src = sys.argv[sys.argv.index("--check") + 1]
        nb = pq.read_table(d.parent / src / "neighbors.parquet").to_pydict()
        qids = np.load(d / "query_ids.npy"); pub = dict(zip(nb["id"], nb["neighbors_id"]))
        ov = [len(set(gt_ids[i, :10]) & set(pub[int(qids[i])][:10])) / 10 for i in range(len(qids))]
        rep["published_top10_overlap_mean"] = float(np.mean(ov)); rep["published_top10_overlap_min"] = float(np.min(ov))
    print(json.dumps(rep)); (d / "gt_meta.json").write_text(json.dumps(rep, indent=2))

if __name__ == "__main__":
    main()
