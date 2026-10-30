"""Semantic search API, Python implementation (FastAPI + uvicorn[standard]).

Contract (identical in api/go and api/rust):
  GET  /health                                         -> {"ok": true, "store": ..., "ready_s": ..., "lang": "python"}
  POST /search      JSON {"vector": [f32 x dim], "k": 10, "doc": false}   -> {"ids": [...], "scores": [...], "docs"?: [...]}
  POST /search_bin?k=10&doc=0   body = raw little-endian f32 vector       -> same JSON response
Env: STORE=stub|pgvector|duckdb|lancedb|remote, PG_DSN, PG_TABLE, DUCK_PATH, DUCK_THREADS, LANCE_URI, LANCE_TABLE,
     REMOTE_URL, EF_SEARCH, POOL_SIZE (per worker), DIM.
Embedded stores (duckdb, lancedb) run with one uvicorn worker by default (see protocol, A6): DuckDB calls go to a
thread pool of POOL_SIZE threads (DuckDB releases the GIL while executing), LanceDB uses its native async API.
"""
import asyncio, os, time, threading
from concurrent.futures import ThreadPoolExecutor
import numpy as np
import orjson
from fastapi import FastAPI, Request
from fastapi.responses import Response

T_START = time.time()
STORE = os.environ.get("STORE", "stub")
DIM = int(os.environ.get("DIM", "768"))
EF = int(os.environ.get("EF_SEARCH", "64"))
POOL = int(os.environ.get("POOL_SIZE", "4"))
app = FastAPI()
state = {}

def jresp(obj):
    return Response(orjson.dumps(obj), media_type="application/json")

def vec_literal(vec):
    return "[" + ",".join(repr(float(x)) for x in vec) + "]::FLOAT[768]"

STUB_DOC = "x" * 1024

class StubStore:
    async def open(self): pass
    async def search(self, vec, k, doc):
        r = {"ids": list(range(k)), "scores": [0.0] * k}
        if doc: r["docs"] = [STUB_DOC] * k   # same 1 KB per hit as the real stores
        return r

class PgVectorStore:
    async def open(self):
        from psycopg_pool import AsyncConnectionPool
        from pgvector.psycopg import register_vector_async
        t = os.environ["PG_TABLE"]
        async def configure(conn):
            await register_vector_async(conn)
            await conn.execute(f"SET hnsw.ef_search = {EF}")
        self.pool = AsyncConnectionPool(os.environ["PG_DSN"], min_size=POOL, max_size=POOL, configure=configure, open=False,
                                        kwargs={"autocommit": True})
        self.sql = f"SELECT id, emb <=> %s AS d FROM {t} ORDER BY d LIMIT %s"
        self.sql_doc = f"SELECT id, emb <=> %s AS d, doc FROM {t} ORDER BY d LIMIT %s"
        await self.pool.open(wait=True)
    async def search(self, vec, k, doc):
        async with self.pool.connection() as c:
            rows = await (await c.execute(self.sql_doc if doc else self.sql, (vec, k))).fetchall()
        r = {"ids": [x[0] for x in rows], "scores": [float(x[1]) for x in rows]}
        if doc: r["docs"] = [x[2] for x in rows]
        return r

class DuckStore:
    async def open(self):
        import duckdb
        self.db = duckdb.connect(os.environ["DUCK_PATH"], read_only=True)
        self.db.execute(f"LOAD vss; SET threads = {os.environ.get('DUCK_THREADS', '6')}; SET late_materialization_max_rows = 0")
        self.local = threading.local()
        self.pool = ThreadPoolExecutor(max_workers=POOL)
        self._q(np.zeros(DIM, np.float32), 1, False)  # load the index before reporting healthy
    def _conn(self):
        c = getattr(self.local, "c", None)
        if c is None:
            c = self.db.cursor(); c.execute(f"SET hnsw_ef_search = {EF}"); self.local.c = c
        return c
    def _q(self, vec, k, doc):
        sql = f"SELECT id, array_cosine_distance(emb, {vec_literal(vec)}) AS d{', doc' if doc else ''} FROM items ORDER BY d LIMIT {int(k)}"
        rows = self._conn().execute(sql).fetchall()
        r = {"ids": [x[0] for x in rows], "scores": [float(x[1]) for x in rows]}
        if doc: r["docs"] = [x[2] for x in rows]
        return r
    async def search(self, vec, k, doc):
        return await asyncio.get_running_loop().run_in_executor(self.pool, self._q, vec, k, doc)

class LanceStore:
    async def open(self):
        import lancedb
        db = await lancedb.connect_async(os.environ["LANCE_URI"])
        self.tbl = await db.open_table(os.environ["LANCE_TABLE"])
    async def search(self, vec, k, doc):
        cols = ["id", "doc", "_distance"] if doc else ["id", "_distance"]
        q = (await self.tbl.search(vec, vector_column_name="emb")).distance_type("cosine").limit(k).nprobes(1).ef(EF).select(cols)
        t = await q.to_arrow()
        r = {"ids": t.column("id").to_pylist(), "scores": [float(x) for x in t.column("_distance").to_pylist()]}
        if doc: r["docs"] = t.column("doc").to_pylist()
        return r

class RemoteStore:
    async def open(self):
        import httpx
        self.url = os.environ["REMOTE_URL"]
        self.client = httpx.AsyncClient(limits=httpx.Limits(max_keepalive_connections=POOL, max_connections=POOL))
    async def search(self, vec, k, doc):
        r = await self.client.post(f"{self.url}/search_bin", params={"k": k, "doc": int(doc)},
                                   content=np.asarray(vec, dtype="<f4").tobytes())
        return orjson.loads(r.content)

STORES = dict(stub=StubStore, pgvector=PgVectorStore, duckdb=DuckStore, lancedb=LanceStore, remote=RemoteStore)

@app.on_event("startup")
async def startup():
    s = STORES[STORE](); await s.open(); state["store"] = s
    state["ready_s"] = time.time() - T_START

@app.get("/health")
async def health():
    return jresp({"ok": True, "store": STORE, "ready_s": state.get("ready_s"), "lang": "python"})

@app.post("/search")
async def search(req: Request):
    body = orjson.loads(await req.body())
    vec = np.asarray(body["vector"], dtype=np.float32)
    if vec.shape != (DIM,):
        return Response(status_code=400)
    return jresp(await state["store"].search(vec, int(body.get("k", 10)), bool(body.get("doc", False))))

@app.post("/search_bin")
async def search_bin(req: Request, k: int = 10, doc: int = 0):
    vec = np.frombuffer(await req.body(), dtype="<f4")
    if vec.shape != (DIM,):
        return Response(status_code=400)
    return jresp(await state["store"].search(vec, k, bool(doc)))
