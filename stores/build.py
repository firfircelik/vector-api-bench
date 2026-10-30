"""Load data/<name> into one store and build its ANN index. Prints one JSON line (also appended to --out).

Usage: python stores/build.py <pgvector|duckdb|lancedb> <name> [--m 16] [--efc 128] [--scratch] [--out f.jsonl]
Every store gets the same rows: id BIGINT, emb (768 x f32, L2-normalized), doc (synthetic 1 KB text, see doc_for).
Index: cosine distance, HNSW with M and ef_construction. LanceDB has no plain HNSW; it gets IVF_HNSW_FLAT with
num_partitions = 1 (one HNSW graph over full-precision vectors), the closest equivalent.
Locations: pgvector table items_<name>_m<M>_efc<efc>; DuckDB run/stores/duckdb/<name>_m<M>_efc<efc>.duckdb;
LanceDB run/stores/lancedb, table <name>_m<M>_efc<efc>.
--scratch builds under a throwaway name and drops it afterwards (build-time replicates).
"""
import argparse, datetime, hashlib, json, os, shutil, sys, time, threading
from pathlib import Path
import numpy as np, pyarrow as pa, psutil

ROOT = Path(__file__).resolve().parent.parent
DIM = 768

def doc_for(i):
    """Deterministic 1,024-byte ASCII payload for row id i (same in every store and language)."""
    h = hashlib.sha256(str(int(i)).encode()).hexdigest()
    return (f"doc-{int(i):010d}|" + h * 17)[:1024]

def arrow_batches(name, bs=50_000):
    base = np.load(ROOT / "data" / name / "base.npy", mmap_mode="r"); ids = np.load(ROOT / "data" / name / "base_ids.npy")
    for s in range(0, len(ids), bs):
        x = np.ascontiguousarray(base[s:s + bs]); i = ids[s:s + bs]
        emb = pa.FixedSizeListArray.from_arrays(pa.array(x.reshape(-1), pa.float32()), DIM)
        yield pa.record_batch([pa.array(i, pa.int64()), emb, pa.array([doc_for(v) for v in i], pa.string())], names=["id", "emb", "doc"])

class Peak(threading.Thread):
    """Peak RSS of this process tree plus (for pgvector) the Postgres tree, sampled every 200 ms."""
    def __init__(self, extra=None):
        super().__init__(daemon=True); self.stop = threading.Event(); self.peak = 0; self.extra = extra
    def run(self):
        me = psutil.Process()
        while not self.stop.is_set():
            tot = 0
            for root in [me] + ([self.extra] if self.extra else []):
                for p in [root] + root.children(recursive=True):
                    try: tot += p.memory_info().rss
                    except psutil.NoSuchProcess: pass
            self.peak = max(self.peak, tot); time.sleep(0.2)

def du(path):
    p = Path(path)
    if not p.exists(): return None
    return p.stat().st_size if p.is_file() else sum(f.stat().st_size for f in p.rglob("*") if f.is_file())

def build_pg(name, tag, m, efc):
    import psycopg
    from pgvector.psycopg import register_vector
    tbl = f"items_{tag}"
    pg = psutil.Process(int((ROOT / "run/pgdata/postmaster.pid").read_text().split()[0]))
    pk = Peak(pg); pk.start()
    with psycopg.connect(f"host={ROOT}/run/pgsock port=5544 user=bench dbname=vec", autocommit=True) as c:
        c.execute("CREATE EXTENSION IF NOT EXISTS vector"); register_vector(c)
        c.execute(f"DROP TABLE IF EXISTS {tbl}")
        c.execute(f"CREATE TABLE {tbl} (id bigint PRIMARY KEY, emb vector({DIM}), doc text)")
        t0 = time.time()
        with c.cursor().copy(f"COPY {tbl} (id, emb, doc) FROM STDIN WITH (FORMAT BINARY)") as cp:
            cp.set_types(["int8", "vector", "text"])
            for b in arrow_batches(name):
                ids = b.column(0).to_numpy(); emb = b.column(1).flatten().to_numpy().reshape(-1, DIM); docs = b.column(2).to_pylist()
                for j in range(len(ids)): cp.write_row((int(ids[j]), emb[j], docs[j]))
        load_s = time.time() - t0
        c.execute("SET maintenance_work_mem = '2GB'"); c.execute("SET max_parallel_maintenance_workers = 7")
        t0 = time.time()
        c.execute(f"CREATE INDEX {tbl}_hnsw ON {tbl} USING hnsw (emb vector_cosine_ops) WITH (m = {m}, ef_construction = {efc})")
        build_s = time.time() - t0
        c.execute(f"VACUUM ANALYZE {tbl}")
        tb, ib = c.execute(f"SELECT pg_table_size('{tbl}'), pg_relation_size('{tbl}_hnsw')").fetchone()
        ver = c.execute("SELECT extversion FROM pg_extension WHERE extname='vector'").fetchone()[0]
    pk.stop.set()
    return dict(version=f"pgvector {ver}", location=tbl, load_s=load_s, build_s=build_s, data_bytes=tb, index_bytes=ib,
                peak_rss_gib=pk.peak / 2**30, index_type="hnsw", params="maintenance_work_mem=2GB, 7 parallel workers")

def build_duckdb(name, tag, m, efc):
    import duckdb
    d = ROOT / "run/stores/duckdb"; d.mkdir(parents=True, exist_ok=True); f = d / f"{tag}.duckdb"
    for p in (f, Path(str(f) + ".wal")):
        if p.exists(): p.unlink()
    pk = Peak(); pk.start()
    c = duckdb.connect(str(f)); c.execute("LOAD vss"); c.execute("SET hnsw_enable_experimental_persistence = true")
    c.execute(f"SET memory_limit = '{os.environ.get('DUCK_MEMORY_LIMIT', '4GB')}'")
    c.execute(f"CREATE TABLE items (id BIGINT PRIMARY KEY, emb FLOAT[{DIM}], doc VARCHAR)")
    t0 = time.time()
    for b in arrow_batches(name):
        t = pa.Table.from_batches([b])
        c.register("b", t); c.execute("INSERT INTO items SELECT id, emb::FLOAT[768], doc FROM b"); c.unregister("b")
    c.execute("CHECKPOINT"); load_s = time.time() - t0
    t0 = time.time()
    c.execute(f"CREATE INDEX items_hnsw ON items USING HNSW (emb) WITH (metric = 'cosine', M = {m}, ef_construction = {efc})")
    c.execute("CHECKPOINT"); build_s = time.time() - t0
    ver = c.execute("SELECT version()").fetchone()[0]
    vss = c.execute("SELECT extension_version FROM duckdb_extensions() WHERE extension_name = 'vss'").fetchone()[0]
    c.close(); pk.stop.set()
    return dict(version=f"duckdb {ver}, vss {vss}", location=str(f.relative_to(ROOT)), load_s=load_s, build_s=build_s,
                data_bytes=None, index_bytes=None, file_bytes=du(f), peak_rss_gib=pk.peak / 2**30, index_type="hnsw (usearch)",
                params=f"hnsw_enable_experimental_persistence=true, memory_limit={os.environ.get('DUCK_MEMORY_LIMIT', '4GB')}")

def build_lance(name, tag, m, efc):
    import lancedb
    d = ROOT / "run/stores/lancedb"; d.mkdir(parents=True, exist_ok=True)
    db = lancedb.connect(str(d))
    if tag in db.table_names(): db.drop_table(tag)
    pk = Peak(); pk.start(); t0 = time.time()
    schema = pa.schema([("id", pa.int64()), ("emb", pa.list_(pa.float32(), DIM)), ("doc", pa.string())])
    tbl = db.create_table(tag, data=pa.RecordBatchReader.from_batches(schema, (pa.RecordBatch.from_arrays(b.columns, schema=schema) for b in arrow_batches(name))))
    load_s = time.time() - t0; t0 = time.time()
    tbl.create_index(metric="cosine", vector_column_name="emb", index_type="IVF_HNSW_FLAT", num_partitions=1, m=m, ef_construction=efc)
    build_s = time.time() - t0; pk.stop.set()
    return dict(version=f"lancedb {lancedb.__version__}", location=f"run/stores/lancedb/{tag}.lance", load_s=load_s, build_s=build_s,
                data_bytes=None, index_bytes=du(d / f"{tag}.lance" / "_indices"), file_bytes=du(d / f"{tag}.lance"),
                peak_rss_gib=pk.peak / 2**30, index_type="IVF_HNSW_FLAT, num_partitions=1", params="")

def drop(store, tag):
    if store == "pgvector":
        import psycopg
        with psycopg.connect(f"host={ROOT}/run/pgsock port=5544 user=bench dbname=vec", autocommit=True) as c: c.execute(f"DROP TABLE IF EXISTS items_{tag}")
    elif store == "duckdb":
        for p in (ROOT / f"run/stores/duckdb/{tag}.duckdb", ROOT / f"run/stores/duckdb/{tag}.duckdb.wal"):
            if p.exists(): p.unlink()
    else:
        shutil.rmtree(ROOT / f"run/stores/lancedb/{tag}.lance", ignore_errors=True)

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("store"); ap.add_argument("name")
    ap.add_argument("--m", type=int, default=16); ap.add_argument("--efc", type=int, default=128)
    ap.add_argument("--scratch", action="store_true"); ap.add_argument("--out"); ap.add_argument("--rep", type=int, default=0)
    a = ap.parse_args()
    tag = f"{a.name}_m{a.m}_efc{a.efc}" + ("_scratch" if a.scratch else "")
    fn = dict(pgvector=build_pg, duckdb=build_duckdb, lancedb=build_lance)[a.store]
    r = fn(a.name, tag, a.m, a.efc)
    if a.scratch: drop(a.store, tag)
    rec = dict(exp="build", store=a.store, dataset=a.name, m=a.m, ef_construction=a.efc, rep=a.rep, scratch=a.scratch,
               started=datetime.datetime.now().isoformat(timespec="seconds"), **{k: (round(v, 3) if isinstance(v, float) else v) for k, v in r.items()})
    print(json.dumps(rec))
    if a.out:
        with open(a.out, "a") as f: f.write(json.dumps(rec) + "\n")

if __name__ == "__main__":
    main()
