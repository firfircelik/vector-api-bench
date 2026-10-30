"""Python counterpart of `vecapi --bench-store`: call the configured store of api/python/app.py directly, no HTTP.
Env as for the server (STORE, PG_DSN, PG_TABLE, DUCK_PATH, LANCE_URI, LANCE_TABLE, EF_SEARCH, POOL_SIZE).
Usage: python bench/py_bench_store.py <queries.f32> [--k 10] [--concurrency 1] [--doc] [--warmup 200]
Prints one JSON object: {lang, wall_s, lat_ms[], ids[][], ready_s}.
"""
import argparse, asyncio, json, os, sys, time
from pathlib import Path
import numpy as np
t_start = time.perf_counter()
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "api/python"))
import app as A  # noqa: E402

async def main():
    ap = argparse.ArgumentParser(); ap.add_argument("queries"); ap.add_argument("--k", type=int, default=10)
    ap.add_argument("--concurrency", type=int, default=1); ap.add_argument("--doc", action="store_true")
    ap.add_argument("--warmup", type=int, default=200); a = ap.parse_args()
    qs = np.fromfile(a.queries, dtype="<f4").reshape(-1, A.DIM)
    st = A.STORES[A.STORE](); await st.open(); ready = time.perf_counter() - t_start
    for i in range(a.warmup): await st.search(qs[i % len(qs)], a.k, a.doc)
    lat = [0.0] * len(qs); ids = [None] * len(qs); nxt = iter(range(len(qs)))
    async def worker():
        for i in nxt:
            s = time.perf_counter_ns(); r = await st.search(qs[i], a.k, a.doc)
            lat[i] = (time.perf_counter_ns() - s) / 1e6; ids[i] = [int(x) for x in r["ids"]]
    t0 = time.perf_counter(); await asyncio.gather(*[worker() for _ in range(a.concurrency)]); wall = time.perf_counter() - t0
    print(json.dumps(dict(lang="python", wall_s=wall, lat_ms=lat, ids=ids, ready_s=ready)))
    os._exit(0)

asyncio.run(main())
