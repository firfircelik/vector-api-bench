"""Load data/<name>/base.npy into Postgres + pgvector and build an HNSW index.

Usage: python stores/load_pgvector.py <name> [m=16] [ef_construction=64]
Env: PGHOST (default <repo>/run/pgsock), PGPORT=5544, PGUSER=bench, PGDATABASE=vec
Prints one JSON line: load_s, build_s, table/index bytes. Table: items_<name>_m<m>_efc<efc>.
"""
import json, os, sys, time
from pathlib import Path
import numpy as np, psycopg
from pgvector.psycopg import register_vector

ROOT = Path(__file__).resolve().parent.parent

def dsn():
    return "host=%s port=%s user=%s dbname=%s" % (
        os.environ.get("PGHOST", str(ROOT / "run/pgsock")), os.environ.get("PGPORT", "5544"),
        os.environ.get("PGUSER", "bench"), os.environ.get("PGDATABASE", "vec"))

def main():
    name = sys.argv[1]; m = int(sys.argv[2]) if len(sys.argv) > 2 else 16
    efc = int(sys.argv[3]) if len(sys.argv) > 3 else 64
    base = np.load(ROOT / "data" / name / "base.npy"); ids = np.load(ROOT / "data" / name / "base_ids.npy")
    tbl = f"items_{name}_m{m}_efc{efc}"
    with psycopg.connect(dsn(), autocommit=True) as c:
        c.execute("CREATE EXTENSION IF NOT EXISTS vector"); register_vector(c)
        c.execute(f"DROP TABLE IF EXISTS {tbl}")
        c.execute(f"CREATE TABLE {tbl} (id bigint PRIMARY KEY, emb vector({base.shape[1]}))")
        t0 = time.time()
        with c.cursor().copy(f"COPY {tbl} (id, emb) FROM STDIN WITH (FORMAT BINARY)") as cp:
            cp.set_types(["int8", "vector"])
            for i in range(len(ids)):
                cp.write_row((int(ids[i]), base[i]))
        load_s = time.time() - t0
        c.execute("SET maintenance_work_mem = '2GB'"); c.execute("SET max_parallel_maintenance_workers = 7")
        t0 = time.time()
        c.execute(f"CREATE INDEX {tbl}_hnsw ON {tbl} USING hnsw (emb vector_cosine_ops) WITH (m = {m}, ef_construction = {efc})")
        build_s = time.time() - t0
        c.execute(f"VACUUM ANALYZE {tbl}")
        tb = c.execute(f"SELECT pg_table_size('{tbl}'), pg_relation_size('{tbl}_hnsw')").fetchone()
        ver = c.execute("SELECT extversion FROM pg_extension WHERE extname='vector'").fetchone()[0]
    print(json.dumps(dict(store="pgvector", version=ver, table=tbl, n=int(len(ids)), m=m, ef_construction=efc,
                          load_s=round(load_s, 2), build_s=round(build_s, 2), table_bytes=tb[0], index_bytes=tb[1],
                          parallel_workers=7, maintenance_work_mem="2GB")))

if __name__ == "__main__":
    main()
