"""Generate the full experiment plan: results/<tag>/plan.jsonl, one JSON object per run, in execution order.

Design (see docs/PROTOCOL.md and the protocol page):
  phase 0  setup builds of every index the runs search (E1 rep 0) + E1 scratch build replicates
  phase 1  E0 calibration: store-only recall sweep per store x N x M x k on the 200 calibration queries
  phase 2  E2 centre cells first (their max sustainable throughput sets the A-experiment rates)
  phase 3  CORE: L x S x N x R x k x payload, full crossing, 5 reps (blocks), ladder mode
  phase 4  ablations A1..A6, E3b (M = 32), A/A control interleaved everywhere
Counterbalancing: block b = rep b. Inside a block, secondary configurations come in a seeded random order and the
9 L x S cells of each configuration follow a row of a Williams design for 9 treatments (balanced first-order
carryover), row = (b * 7 + config_index) mod 18. Seed 20261022.
Usage: python bench/plan.py --centre c1m [--tag full] [--reps 5]
       python bench/plan.py --centre c300k --scales c100k,c300k --tag full-mac --a6-workers 2   (Mac mini, 8 GiB; docs/PROTOCOL.md)
"""
import argparse, hashlib, itertools, json, random
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LANGS = ["python", "go", "rust"]; STORES = ["pgvector", "duckdb", "lancedb"]
CELLS = [(l, s) for l in LANGS for s in STORES]
SCALES = ["c100k", "c300k", "c1m"]; RECALLS = [0.90, 0.95, 0.99]; KS = [10, 100]; PAYLOADS = ["ids", "doc"]

def williams(n):
    """Williams design rows for odd n: 2n sequences, each treatment precedes every other equally often."""
    base = [0]; lo, hi = 1, n - 1
    while len(base) < n:
        base.append(lo); lo += 1
        if len(base) < n: base.append(hi); hi -= 1
    rows = [[(x + i) % n for x in base] for i in range(n)]
    return rows + [r[::-1] for r in rows]

W = williams(9)

def rid(spec):
    key = json.dumps({k: v for k, v in spec.items() if k not in ("order", "phase")}, sort_keys=True)
    return hashlib.sha1(key.encode()).hexdigest()[:12]

def cell(l, s, **kw):
    d = dict(kind="cell", lang=l, store=s, dataset=kw.pop("dataset"), m=kw.pop("m", 16), recall=kw.pop("recall", 0.95),
             k=kw.pop("k", 10), payload=kw.pop("payload", "ids"), enc=kw.pop("enc", "json"), cache=kw.pop("cache", "warm"),
             topology=kw.pop("topology", "default"), py_model=kw.pop("py_model", "default"), mode=kw.pop("mode", "ladder"))
    d.update(kw); return d

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--centre", default="c1m"); ap.add_argument("--tag", default="full")
    ap.add_argument("--reps", type=int, default=5)
    ap.add_argument("--scales", default=",".join(SCALES), help="comma list; the Mac plan drops c1m")
    ap.add_argument("--a6-workers", type=int, default=6, help="uvicorn workers in A6 'multi' for the embedded stores (each loads its own index copy)")
    a = ap.parse_args(); globals()["SCALES"] = a.scales.split(",")
    C = a.centre; rng = random.Random(20261022); runs = []
    def add(phase, exp, spec): spec.update(phase=phase, exp=exp); runs.append(spec)
    # phase 0: primary builds (searched by every later run) and build-time replicates
    for n in SCALES:
        for s in STORES:
            add(0, "E1", dict(kind="build", store=s, dataset=n, m=16, rep=0, scratch=False))
    for s in STORES:
        add(0, "E1", dict(kind="build", store=s, dataset=C, m=32, rep=0, scratch=False))
    for n in SCALES:
        if n == "c1m": continue
        for s in STORES:
            for r in (1, 2):
                add(0, "E1", dict(kind="build", store=s, dataset=n, m=16, rep=r, scratch=True))
    # phase 1: calibration
    for s in STORES:
        for n, m in [(n, 16) for n in SCALES] + [(C, 32)]:
            for k in KS:
                add(1, "E0", dict(kind="calib", store=s, dataset=n, m=m, k=k, rep=0))
    # phase 2: centre cells, 5 reps, ladder + bisection (sets per-cell MST for later short/cold modes)
    for b in range(a.reps):
        for i in W[(b * 7) % 18]:
            l, s = CELLS[i]
            add(2, "E2", cell(l, s, dataset=C, mode="ladder+bisect", rep=b))
    # phase 3: core full crossing (excluding the centre point already run in phase 2)
    configs = [c for c in itertools.product(SCALES, RECALLS, KS, PAYLOADS) if c != (C, 0.95, 10, "ids")]
    for b in range(a.reps):
        order = configs[:]; rng.shuffle(order)
        for ci, (n, rc, k, p) in enumerate(order):
            for i in W[(b * 7 + ci) % 18]:
                l, s = CELLS[i]
                add(3, "CORE", cell(l, s, dataset=n, recall=rc, k=k, payload=p, rep=b))
    # phase 4: ablations, 3 reps unless noted, interleaved by block
    for b in range(5):
        blk = []
        for l in LANGS:                                   # A1 API floor: stub store
            for enc in ("json", "bin"):
                for k, p in ((10, "ids"), (100, "doc")):
                    blk.append(("A1", cell(l, "stub", dataset=C, enc=enc, k=k, payload=p, rep=b)))
        for i in W[(b * 7) % 18]:                         # A5 cold vs warm, 5 reps
            l, s = CELLS[i]; blk.append(("A5", cell(l, s, dataset=C, cache="cold", mode="cold", rep=b)))
        if b < 3:
            for l in LANGS:                               # A2 store floor, native client per language
                for s in STORES:
                    for rc in RECALLS:
                        for conc in (1, 6):
                            blk.append(("A2", dict(kind="store_only", lang=l, store=s, dataset=C, m=16, recall=rc, k=10, conc=conc, rep=b)))
            for l in LANGS:                               # A3 topology
                blk.append(("A3", cell(l, "pgvector", dataset=C, topology="tcp", rep=b)))
                for s in ("duckdb", "lancedb"):
                    blk.append(("A3", cell(l, s, dataset=C, topology="sidecar", rep=b)))
            for i in W[(b * 7 + 3) % 18]:                 # A4 serialization: binary request (JSON is in E2)
                l, s = CELLS[i]; blk.append(("A4", cell(l, s, dataset=C, enc="bin", mode="short", rep=b)))
            for s in STORES:                              # A6 Python process model, at 100k
                for pm in ("multi", "single"):
                    extra = dict(py_workers=a.a6_workers) if (pm == "multi" and s != "pgvector" and a.a6_workers != 6) else {}
                    blk.append(("A6", cell("python", s, dataset="c100k", py_model=pm, rep=b, **extra)))  # c100k: N embedded index copies must fit
            for i in W[(b * 7 + 5) % 18]:                 # E3b M = 32 at the centre
                l, s = CELLS[i]; blk.append(("E3b", cell(l, s, dataset=C, m=32, mode="short", rep=b)))
        rng.shuffle(blk)
        for e, sp in blk: add(4, e, sp)
    # A/A control: a fixed short cell after every 25th run of phases 2..4
    out = []; n_aa = 0
    for i, r in enumerate(runs):
        out.append(r)
        if r["phase"] >= 2 and i % 25 == 24:
            out.append(dict(cell("rust", "pgvector", dataset=C, mode="short", rep=n_aa), phase=r["phase"], exp="AA")); n_aa += 1
    seen = set()
    d = ROOT / "results" / a.tag; d.mkdir(parents=True, exist_ok=True)
    with open(d / "plan.jsonl", "w") as f:
        for o, r in enumerate(out):
            r["id"] = rid(r); r["order"] = o
            assert r["id"] not in seen, r; seen.add(r["id"])
            f.write(json.dumps(r) + "\n")
    from collections import Counter
    print(json.dumps(dict(total=len(out), by_exp=Counter(r["exp"] for r in out), centre=C, scales=SCALES, a6_workers=a.a6_workers)))

if __name__ == "__main__":
    main()
