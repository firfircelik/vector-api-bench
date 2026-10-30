"""Resumable, checkpointed executor for results/<tag>/plan.jsonl (written by bench/plan.py).

  python bench/runner.py --tag full            # runs every planned run that has no results/<tag>/runs/<id>.jsonl yet
  python bench/runner.py --tag smoke --only-exp CORE --limit 9 --step-s 8 ...   (smoke tests)

Checkpointing: a run's records are written to runs/<id>.jsonl.tmp and renamed to runs/<id>.jsonl when the run ends,
so a run is either complete or absent. On restart completed runs are skipped. A run that raises is logged to
runs/<id>.failed (attempt count); it is retried on later passes up to --max-attempts.
Progress: results/<tag>/progress.log (one line per run), results/<tag>/runner.pid, results/<tag>/STOP (touch to stop
after the current run). Host gate before every run (free memory and idle CPU); host-stall rule per step.
Process layout: the runner and vegeta run on LOADGEN_CPUS (6-7); servers, sidecars and Postgres on SUT_CPUS (0-5).
macOS (Apple silicon) has no CPU affinity API: there nothing is pinned, client and server share all cores, and memory is
measured as phys_footprint (proc_pid_rusage, Activity Monitor's "Memory") plus USS instead of PSS. See docs/PROTOCOL.md.
"""
import argparse, base64, datetime, json, math, os, re, resource, signal, statistics, subprocess, sys, threading, time, traceback, urllib.request
from collections import Counter
from pathlib import Path
import numpy as np, orjson, psutil
import ctypes

IS_MAC = sys.platform == "darwin"

ROOT = Path(__file__).resolve().parent.parent
PY = str(ROOT / ".venv/bin/python")
VEGETA = os.environ.get("VEGETA", str(Path.home() / "go/bin/vegeta"))
PGSOCK = os.environ.get("PGSOCK", str(ROOT / "run/pgsock")); PGPORT = os.environ.get("PGPORT", "5544")
SUT_CPUS = os.environ.get("SUT_CPUS", "0-5"); LOADGEN_CPUS = os.environ.get("LOADGEN_CPUS", "6-7")
GO_BIN = ROOT / "api/go/vecapi"; RUST_BIN = ROOT / "run/cargo-target/release/vecapi"
PORT, SIDECAR_PORT = 8080, 9090
EF_GRID = {10: [10, 16, 24, 32, 48, 64, 96, 128, 192, 256, 384, 512], 100: [100, 128, 160, 192, 256, 320, 384, 512, 768, 1000]}
N_CALIB = 200            # queries 0..199 calibrate the knob; 200..999 are held out for reporting
SLO_P99_MS = 100.0
BODY_CAPTURE_MAX_RPS = 1600   # bodies (truncated to 4 KB; ids come first) are captured and scored up to this rate
MEM_NEED_GIB = {"c100k": 3.0, "c300k": 4.0, "c1m": 7.0}
if IS_MAC:  # 8 GiB Mac mini: psutil "available" (free + inactive + purgeable) is lower and macOS compresses before it swaps
    MEM_NEED_GIB = {"c100k": 0.8, "c300k": 1.0}   # smoke-mac + first full-mac hour: 1.2-2.9 GiB available with the owner's apps open
BUILD_EXTRA_GIB = 0.3 if IS_MAC else 2.0
MIN_DISK_GIB = 3.0   # never start a run below this much free disk (scratch builds need ~3 GiB at c300k); waits, no timeout

def pin(cpus):
    """CPU pinning prefix. Linux: taskset. macOS: none (no affinity API on Apple silicon); see docs/PROTOCOL.md."""
    return [] if IS_MAC else ["taskset", "-c", cpus]
A = None  # args

def now(): return datetime.datetime.now().isoformat(timespec="seconds")
def tag_of(dataset, m): return f"{dataset}_m{m}_efc128"
def log(msg):
    line = f"{now()} {msg}"; print(line, flush=True)
    with open(OUT / "progress.log", "a") as f: f.write(line + "\n")

# ---------------------------------------------------------------- host state
def tree(p):
    try: return [p] + p.children(recursive=True)
    except psutil.NoSuchProcess: return []

def pg_root():
    try: return psutil.Process(int((ROOT / "run/pgdata/postmaster.pid").read_text().split()[0]))
    except Exception: return None

def pg_up(): return subprocess.call([str(ROOT / "bench/pg.sh"), "status"], stdout=subprocess.DEVNULL) == 0
def pg_ensure():
    if not pg_up(): subprocess.check_call([str(ROOT / "bench/pg.sh"), "start"], stdout=subprocess.DEVNULL)

def steal_ticks(): return 0 if IS_MAC else int(open("/proc/stat").readline().split()[8])

def swap_used_gib(): return round(psutil.swap_memory().used / 2**30, 3)

# macOS phys_footprint (what Activity Monitor shows as "Memory"; counts compressed and swapped-out dirty pages)
_libproc = ctypes.CDLL("/usr/lib/libproc.dylib") if IS_MAC else None
class _RUsageV2(ctypes.Structure):
    _fields_ = [("uuid", ctypes.c_uint8 * 16)] + [(n, ctypes.c_uint64) for n in (
        "user_time", "system_time", "pkg_idle_wkups", "interrupt_wkups", "pageins", "wired_size", "resident_size",
        "phys_footprint", "proc_start_abstime", "proc_exit_abstime", "child_user_time", "child_system_time",
        "child_pkg_idle_wkups", "child_interrupt_wkups", "child_pageins", "child_elapsed_abstime",
        "diskio_bytesread", "diskio_byteswritten")]
def phys_footprint(pid):
    if not IS_MAC: return 0
    buf = _RUsageV2()
    return buf.phys_footprint if _libproc.proc_pid_rusage(int(pid), 2, ctypes.byref(buf)) == 0 else 0

def gate(need_gib):
    """Wait for a quiet host: available memory >= need and whole-machine CPU busy < 25 % over 5 s. Max --gate-max-s."""
    t0 = time.time(); waited = False
    import shutil
    while shutil.disk_usage(ROOT).free / 2**30 < MIN_DISK_GIB and not (OUT / "STOP").exists():
        if not waited: log(f"disk low: {shutil.disk_usage(ROOT).free / 2**30:.1f} GiB free (need {MIN_DISK_GIB}); waiting, free space to continue"); waited = True
        time.sleep(60)
    while True:
        avail = psutil.virtual_memory().available / 2**30; busy = psutil.cpu_percent(interval=5.0)
        if avail >= need_gib and busy < 25: break
        if time.time() - t0 > A.gate_max_s:
            log(f"gate timeout: avail {avail:.1f} GiB busy {busy:.0f}% (proceeding, flagged)"); return dict(gate_wait_s=round(time.time() - t0), gate_timeout=True)
        if not waited: log(f"gate wait: avail {avail:.1f} GiB (need {need_gib}) busy {busy:.0f}%"); waited = True
        if (OUT / "STOP").exists(): break
        time.sleep(25)
    return dict(gate_wait_s=round(time.time() - t0), gate_timeout=False)

def drop_caches():
    """Returns True if the OS file cache was actually dropped (needs passwordless sudo; recorded per run)."""
    if IS_MAC: cmd = ["sudo", "-n", "/usr/sbin/purge"]
    else: cmd = ["sudo", "-n", "sh", "-c", "sync; echo 3 > /proc/sys/vm/drop_caches"]
    subprocess.call(["sync"])
    return subprocess.call(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) == 0

class Sampler(threading.Thread):
    """Every 100 ms: RSS and CPU time per process tree. Every 1 s: PSS (Linux; shared pages split) or, on macOS,
    nothing (USS needs task_for_pid, i.e. root, so psutil returns AccessDenied). macOS samples phys_footprint every 100 ms:
    peak_fp = sum over the tree, peak_fpmax = largest single process (for Postgres the sum counts shared_buffers once per
    backend that touched them, so the sum is an upper bound and the largest process a lower bound)."""
    def __init__(self, roots):
        super().__init__(daemon=True); self.roots = roots; self.stop = threading.Event()
        self.peak_rss = {}; self.peak_pss = {}; self.peak_fp = {}; self.peak_fpmax = {}; self.last_fpmax = {}; self.cpu = {}; self.lock = threading.Lock()
    def cpu_now(self):
        out = {}
        for key, fn in self.roots.items():
            c = 0.0; root = fn()
            for p in tree(root) if root else []:
                try: t = p.cpu_times(); c += t.user + t.system
                except psutil.NoSuchProcess: pass
            out[key] = c
        return out
    def run(self):
        last_pss = 0
        while not self.stop.is_set():
            do_pss = time.time() - last_pss >= 1.0
            if do_pss: last_pss = time.time()
            for key, fn in self.roots.items():
                rss = pss = fp = fpmax = 0; root = fn()
                for p in tree(root) if root else []:
                    try:
                        rss += p.memory_info().rss
                        if IS_MAC: f1 = phys_footprint(p.pid); fp += f1; fpmax = max(fpmax, f1)
                        elif do_pss: pss += p.memory_full_info().pss
                    except (psutil.NoSuchProcess, psutil.AccessDenied): pass
                with self.lock:
                    self.peak_rss[key] = max(self.peak_rss.get(key, 0), rss)
                    if IS_MAC: self.peak_fp[key] = max(self.peak_fp.get(key, 0), fp); self.peak_fpmax[key] = max(self.peak_fpmax.get(key, 0), fpmax)
                    if do_pss: self.peak_pss[key] = max(self.peak_pss.get(key, 0), pss)
            time.sleep(0.1)
    def window(self):
        with self.lock:
            r, p, fp = dict(self.peak_rss), dict(self.peak_pss), dict(self.peak_fp); self.peak_rss = {}; self.peak_pss = {}; self.peak_fp = {}
            self.last_fpmax = dict(self.peak_fpmax); self.peak_fpmax = {}
        self.last_fp = fp
        return self.cpu_now(), r, p

# ---------------------------------------------------------------- calibration lookup
def calib_table():
    t = {}
    for f in (OUT / "runs").glob("*.jsonl"):
        for l in open(f):
            r = json.loads(l)
            if r.get("kind") == "calib_point":
                t.setdefault((r["store"], r["dataset"], r["m"], r["k"]), []).append(r)
    return t

def knob_for(store, dataset, m, k, target):
    if store == "stub": return dict(ef=0)
    pts = sorted(calib_table().get((store, dataset, m, k), []), key=lambda r: r["ef"])
    if not pts: raise RuntimeError(f"no calibration for {store} {dataset} m{m} k{k}")
    for r in pts:
        if r["recall_calib"] >= target: return dict(ef=r["ef"], calib_recall=r["recall_calib"], target_reached=True)
    return dict(ef=pts[-1]["ef"], calib_recall=pts[-1]["recall_calib"], target_reached=False)

def centre_mst(lang, store):
    vals = []
    for f in (OUT / "runs").glob("*.jsonl"):
        first = orjson.loads(open(f, "rb").readline() or b"{}")
        if first.get("exp") == "E2" and first.get("lang") == lang and first.get("store") == store and first.get("kind") == "step":
            ok = [orjson.loads(l) for l in open(f, "rb")]
            ok = [r["offered_rps"] for r in ok if r.get("kind") == "step" and not r["invalid"] and not r["saturated"]]
            vals.append(max(ok) if ok else 0)
    return statistics.median(vals) if vals else None

# ---------------------------------------------------------------- servers
def store_env(store, dataset, m, ef, pool, topology):
    t = tag_of(dataset, m); e = dict(STORE=store, EF_SEARCH=str(ef), POOL_SIZE=str(pool), DIM="768", DUCK_THREADS="6")
    host = "127.0.0.1" if topology == "tcp" else PGSOCK
    e.update(PG_DSN=f"host={host} port={PGPORT} user=bench dbname=vec", PG_TABLE=f"items_{t}",
             DUCK_PATH=str(ROOT / f"run/stores/duckdb/{t}.duckdb"), LANCE_URI=str(ROOT / "run/stores/lancedb"), LANCE_TABLE=t)
    return e

def server_cmd(lang, workers, port):
    if lang == "python":
        return [str(ROOT / ".venv/bin/uvicorn"), "app:app", "--app-dir", str(ROOT / "api/python"), "--host", "127.0.0.1", "--port", str(port),
                "--workers", str(workers), "--no-access-log", "--log-level", "warning", "--http", "httptools", "--loop", "uvloop"]
    if lang == "go": return [str(GO_BIN), "-port", str(port)]
    if lang == "rust": return [str(RUST_BIN), "--port", str(port)]
    raise ValueError(lang)

def process_model(spec):
    """Idiomatic Python deployment per store (fixed before data): network store -> 6 workers (one per SUT core);
    embedded store -> 1 worker (each worker would load its own copy of the index). A6 swaps the two."""
    store = spec["store"]; lang = spec["lang"]; embedded = store in ("duckdb", "lancedb") and spec["topology"] != "sidecar"
    pool_total = 6 if (store == "duckdb" and spec["topology"] != "sidecar") else 24
    if lang != "python": return 1, pool_total
    multi = not embedded
    if spec.get("py_model") == "multi": multi = True
    if spec.get("py_model") == "single": multi = False
    workers = spec.get("py_workers", 6) if multi else 1   # py_workers < 6 only where the plan reduced A6 to fit memory
    return workers, max(1, pool_total // workers)

def wait_health(port, t0, timeout=300, proc=None):
    while time.time() - t0 < timeout:
        if proc is not None and proc.poll() is not None: return None
        try: return json.loads(urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=1).read())
        except Exception: time.sleep(0.02)
    return None

def start(cmd, env):
    log_f = open(OUT / "server.log", "ab")
    return subprocess.Popen(pin(SUT_CPUS) + cmd, env=dict(os.environ, **env), start_new_session=True, stdout=log_f, stderr=log_f)

def kill(p):
    if p is None: return
    try: os.killpg(p.pid, signal.SIGTERM); p.wait(timeout=30)
    except Exception:
        try: os.killpg(p.pid, signal.SIGKILL)
        except Exception: pass

# ---------------------------------------------------------------- load
def targets(dataset, enc, k, doc):
    p = OUT / "targets" / f"{dataset}_{enc}_k{k}_{'doc' if doc else 'ids'}.jsonl"
    if p.exists(): return p
    p.parent.mkdir(exist_ok=True); q = np.load(ROOT / "data" / dataset / "queries.npy"); tmp = p.with_suffix(".tmp")
    with open(tmp, "w") as f:
        for i, v in enumerate(q):
            if enc == "json":
                body = orjson.dumps({"vector": v.tolist(), "k": k, "doc": doc}); url = f"http://127.0.0.1:{PORT}/search?qid={i}"; ct = "application/json"
            else:
                body = v.astype("<f4").tobytes(); url = f"http://127.0.0.1:{PORT}/search_bin?qid={i}&k={k}&doc={int(doc)}"; ct = "application/octet-stream"
            f.write(json.dumps({"method": "POST", "url": url, "body": base64.b64encode(body).decode(), "header": {"Content-Type": [ct]}}) + "\n")
    tmp.rename(p); return p

IDS_RE = re.compile(rb'"ids":\[([0-9,]*)\]')

def attack(tg, rate, duration, capture):
    r0 = resource.getrusage(resource.RUSAGE_CHILDREN)
    atk = subprocess.Popen(pin(LOADGEN_CPUS) + [VEGETA, "attack", "-format=json", f"-targets={tg}", f"-rate={rate}/s",
                            f"-duration={duration}s", "-max-workers=4096", "-timeout=10s", "-keepalive=true",
                            f"-max-body={4096 if capture else 0}"], stdout=subprocess.PIPE)
    enc = subprocess.Popen(pin(LOADGEN_CPUS) + [VEGETA, "encode", "-to", "json"], stdin=atk.stdout, stdout=subprocess.PIPE)
    atk.stdout.close(); data = enc.communicate()[0]; atk.wait()
    r1 = resource.getrusage(resource.RUSAGE_CHILDREN)
    lg = (r1.ru_utime - r0.ru_utime) + (r1.ru_stime - r0.ru_stime)
    return [orjson.loads(l) for l in data.splitlines() if l.strip()], lg

def hist(lat_us):
    b = np.floor(np.log2(np.maximum(lat_us, 1)) * 16).astype(int); c = Counter(b.tolist()); return {str(k): v for k, v in sorted(c.items())}

def score(res, gt, k, capture, duration):
    lat = np.array([r["latency"] for r in res], dtype=np.float64) / 1e3  # us
    codes = Counter(r["code"] for r in res); ok = codes.get(200, 0); n = len(res)
    hits = {"calib": [0, 0], "heldout": [0, 0]}; bad_len = 0
    if capture:
        for r in res:
            if r["code"] != 200: continue
            qid = int(r["url"].split("qid=")[1].split("&")[0]); m = IDS_RE.search(base64.b64decode(r["body"]))
            if not m: continue
            ids = [int(x) for x in m.group(1).split(b",") if x]
            if len(ids) != k: bad_len += 1
            g = "calib" if qid < N_CALIB else "heldout"
            hits[g][0] += len(set(ids[:k]) & set(gt[qid, :k].tolist())); hits[g][1] += 1
    ts = sorted(r["timestamp"] for r in res)
    span = (datetime.datetime.fromisoformat(ts[-1]) - datetime.datetime.fromisoformat(ts[0])).total_seconds() if n > 1 else 0
    pc = lambda p: float(np.percentile(lat, p)) / 1e3 if n else None
    out = dict(n=n, ok=ok, codes={str(c): v for c, v in codes.items()}, error_rate=(n - ok) / max(1, n),
               sent_rate=(n - 1) / span if span else None, goodput_rps=ok / duration,
               p50_ms=pc(50), p90_ms=pc(90), p95_ms=pc(95), p99_ms=pc(99), p999_ms=pc(99.9),
               max_ms=float(lat.max()) / 1e3 if n else None, mean_ms=float(lat.mean()) / 1e3 if n else None,
               bytes_in_mean=float(np.mean([r["bytes_in"] for r in res])) if n else None,
               recall_heldout=hits["heldout"][0] / (hits["heldout"][1] * k) if hits["heldout"][1] else None,
               recall_calib=hits["calib"][0] / (hits["calib"][1] * k) if hits["calib"][1] else None,
               scored=hits["heldout"][1] + hits["calib"][1], wrong_len=bad_len, lat_hist_log2x16_us=hist(lat))
    return out

def invalid(rec, duration):
    """Host-stall rule (fixed in the pilot): step wall > duration + 10 s, or a 10 s timeout while sent rate < 50 % of offered."""
    return bool(rec["step_wall_s"] > duration + 10 or ((rec["max_ms"] or 0) > 10000 and (rec["sent_rate"] or 0) < 0.5 * rec["offered_rps"]))

def saturated(rec):
    return bool((rec["sent_rate"] or 0) < 0.98 * rec["offered_rps"] or rec["error_rate"] > 0.001 or (rec["p99_ms"] or 1e9) > SLO_P99_MS)

# ---------------------------------------------------------------- run kinds
def run_build(spec, base):
    cmd = [PY, str(ROOT / "stores/build.py"), spec["store"], spec["dataset"], "--m", str(spec["m"]), "--efc", "128", "--rep", str(spec["rep"])]
    if spec["scratch"]: cmd.append("--scratch")
    t = tag_of(spec["dataset"], spec["m"])
    exists = {"pgvector": lambda: subprocess.run(["psql", "-h", PGSOCK, "-p", PGPORT, "-U", "bench", "-d", "vec", "-tAc",
                                                    f"select to_regclass('items_{t}') is not null and exists(select 1 from pg_indexes where tablename='items_{t}')"],
                                                   capture_output=True, text=True).stdout.strip() == "t",
              "duckdb": lambda: (ROOT / f"run/stores/duckdb/{t}.duckdb").exists() and not (ROOT / f"run/stores/duckdb/{t}.duckdb.wal").exists(),
              "lancedb": lambda: (ROOT / f"run/stores/lancedb/{t}.lance/_indices").exists()}[spec["store"]]
    prior = [json.loads(l) for f in ("results/dev/build.jsonl", "results/setup/build.jsonl") if (ROOT / f).exists() for l in open(ROOT / f)]
    prior = [r for r in prior if r.get("store") == spec["store"] and r.get("name", r.get("dataset")) == spec["dataset"] and r.get("m") == spec["m"] and not r.get("scratch")]
    if not spec["scratch"] and exists() and prior:
        return [dict(base, kind="build", reused_setup_build=True, **{k: v for k, v in prior[-1].items() if k not in base})]
    env = dict(os.environ, DUCK_MEMORY_LIMIT="4GB")
    p = subprocess.run(pin(SUT_CPUS) + cmd, capture_output=True, text=True, env=env)
    if p.returncode != 0: raise RuntimeError(p.stderr[-2000:])
    rec = json.loads([l for l in p.stdout.splitlines() if l.startswith("{")][-1])
    if not spec["scratch"]:
        with open(ROOT / "results/setup/build.jsonl", "a") as f: f.write(json.dumps(rec) + "\n")
    return [dict(base, kind="build", **{k: v for k, v in rec.items() if k not in base})]

def bench_store(lang, env, qfile, k, conc, doc=False, warmup=200):
    if lang == "python": cmd = [PY, str(ROOT / "bench/py_bench_store.py"), str(qfile), "--k", str(k), "--concurrency", str(conc), "--warmup", str(warmup)] + (["--doc"] if doc else [])
    elif lang == "go": cmd = [str(GO_BIN), "-bench-store", str(qfile), "-k", str(k), "-concurrency", str(conc), "-warmup", str(warmup)] + (["-doc"] if doc else [])
    else: cmd = [str(RUST_BIN), "--bench-store", str(qfile), "--k", str(k), "--concurrency", str(conc), "--warmup", str(warmup)] + (["--doc"] if doc else [])
    r0 = resource.getrusage(resource.RUSAGE_CHILDREN); t0 = time.time()
    p = subprocess.run(pin(SUT_CPUS) + cmd, capture_output=True, env=dict(os.environ, **env), timeout=3600)
    r1 = resource.getrusage(resource.RUSAGE_CHILDREN)
    if p.returncode != 0: raise RuntimeError(p.stderr.decode()[-2000:])
    out = orjson.loads(p.stdout.strip().splitlines()[-1])
    out["cpu_s"] = (r1.ru_utime - r0.ru_utime) + (r1.ru_stime - r0.ru_stime); out["proc_wall_s"] = time.time() - t0
    out["maxrss_children_mib"] = r1.ru_maxrss / (2**20 if IS_MAC else 1024)   # ru_maxrss: bytes on macOS, KiB on Linux
    return out

def recall_split(ids, gt, k):
    h = [len(set(ids[i][:k]) & set(gt[i, :k].tolist())) for i in range(len(ids))]
    return sum(h[:N_CALIB]) / (N_CALIB * k), sum(h[N_CALIB:]) / ((len(h) - N_CALIB) * k)

def run_calib(spec, base):
    """E0: sweep ef with the Python store client (the index builders' own library), concurrency 6, all 1000 queries."""
    pg_ensure(); recs = []; d = ROOT / "data" / spec["dataset"]; gt = np.load(d / "gt_ids.npy")
    for ef in EF_GRID[spec["k"]]:
        env = store_env(spec["store"], spec["dataset"], spec["m"], ef, 6, "default")
        o = bench_store("python", env, d / "queries.f32", spec["k"], 6, warmup=50)
        rc, rh = recall_split(o["ids"], gt, spec["k"])
        lat = np.array(o["lat_ms"])
        recs.append(dict(base, kind="calib_point", ef=ef, recall_calib=rc, recall_heldout=rh, p50_ms=float(np.median(lat)),
                         p99_ms=float(np.percentile(lat, 99)), wall_s=o["wall_s"]))
        log(f"  calib {spec['store']} {spec['dataset']} m{spec['m']} k{spec['k']} ef={ef}: recall calib {rc:.4f} heldout {rh:.4f} p50 {np.median(lat):.2f} ms")
        if rc >= 0.995: break
    return recs

def run_store_only(spec, base):
    """A2: the store through each language's native client, no HTTP. Sequential (conc 1) and 6-way."""
    if spec["store"] == "pgvector": pg_ensure()
    kn = knob_for(spec["store"], spec["dataset"], spec["m"], spec["k"], spec["recall"]); d = ROOT / "data" / spec["dataset"]
    env = store_env(spec["store"], spec["dataset"], spec["m"], kn["ef"], 6 if spec["store"] == "duckdb" else max(6, spec["conc"]), "default")
    o = bench_store(spec["lang"], env, d / "queries.f32", spec["k"], spec["conc"])
    gt = np.load(d / "gt_ids.npy"); rc, rh = recall_split(o["ids"], gt, spec["k"]); lat = np.array(o["lat_ms"])
    return [dict(base, kind="store_only_result", **kn, recall_calib=rc, recall_heldout=rh, n=len(lat), wall_s=o["wall_s"], qps=len(lat) / o["wall_s"],
                 p50_ms=float(np.percentile(lat, 50)), p95_ms=float(np.percentile(lat, 95)), p99_ms=float(np.percentile(lat, 99)),
                 mean_ms=float(lat.mean()), cpu_s=o["cpu_s"], cpu_ms_per_query=1000 * o["cpu_s"] / len(lat), ready_s=o["ready_s"],
                 maxrss_children_mib=o["maxrss_children_mib"])]

def ladder_rates():
    r = A.ladder_start; out = []
    while r <= A.ladder_max: out.append(r); r *= 2
    return out

def run_cell(spec, base):
    store = spec["store"]; lang = spec["lang"]; topo = spec["topology"]
    if store == "pgvector" or topo == "tcp": pg_ensure()
    kn = knob_for(store, spec["dataset"], spec["m"], spec["k"], spec["recall"]) if store != "stub" else dict(ef=0)
    workers, pool = process_model(spec)
    gt = np.load(ROOT / "data" / spec["dataset"] / "gt_ids.npy")
    tg = targets(spec["dataset"], spec["enc"], spec["k"], spec["payload"] == "doc")
    sidecar = None
    env = store_env(store, spec["dataset"], spec["m"], kn["ef"], pool, topo)
    if topo == "sidecar":
        senv = store_env(store, spec["dataset"], spec["m"], kn["ef"], 6 if store == "duckdb" else 24, "default")
        t0 = time.time(); sidecar = start(server_cmd("rust", 1, SIDECAR_PORT), senv)
        if wait_health(SIDECAR_PORT, t0, proc=sidecar) is None: kill(sidecar); raise RuntimeError("sidecar not healthy")
        env = dict(env, STORE="remote", REMOTE_URL=f"http://127.0.0.1:{SIDECAR_PORT}", POOL_SIZE=str(pool))
    cache_dropped = None
    if spec["mode"] == "cold":
        if store == "pgvector": subprocess.check_call([str(ROOT / "bench/pg.sh"), "restart"], stdout=subprocess.DEVNULL)
        cache_dropped = drop_caches(); time.sleep(1)
    t0 = time.time(); srv = start(server_cmd(lang, workers, PORT), env); health = wait_health(PORT, t0, proc=srv)
    cold_start_s = time.time() - t0
    if health is None: kill(srv); kill(sidecar); raise RuntimeError("server exited or not healthy in 300 s (see server.log)")
    sp = psutil.Process(srv.pid); roots = {"server": lambda: sp}
    if store == "pgvector" or topo == "tcp": roots["postgres"] = pg_root
    if sidecar: sc = psutil.Process(sidecar.pid); roots["sidecar"] = lambda: sc
    smp = Sampler(roots); smp.start(); recs = []
    common = dict(base, ef=kn["ef"], knob=kn, workers=workers if lang == "python" else None, db_pool_per_proc=pool,
                  cold_start_s=round(cold_start_s, 3), health=health, sut_cpus="all (unpinned)" if IS_MAC else SUT_CPUS,
                  loadgen_cpus="all (unpinned)" if IS_MAC else LOADGEN_CPUS, os=sys.platform, cache_dropped=cache_dropped,
                  mem_metric="phys_footprint" if IS_MAC else "pss")
    def step(rate, duration, idx, phase):
        capture = rate <= BODY_CAPTURE_MAX_RPS
        pre = dict(pre_avail_gib=round(psutil.virtual_memory().available / 2**30, 2), pre_load1=os.getloadavg()[0], pre_swap_used_gib=swap_used_gib())
        st0 = steal_ticks(); c0, _, _ = smp.window(); w0 = time.time()
        res, lg_cpu = attack(tg, rate, duration, capture)
        wall = time.time() - w0; c1, prss, ppss = smp.window(); steal = (steal_ticks() - st0) / os.sysconf("SC_CLK_TCK")
        rec = dict(common, kind="step", step_phase=phase, step=idx, offered_rps=rate, duration_s=duration, started=now(),
                   step_wall_s=round(wall, 2), steal_s=round(steal, 2), body_captured=capture, loadgen_cpu_s=round(lg_cpu, 2),
                   loadgen_cpu_util_cores=round(lg_cpu / wall, 3), **pre, **score(res, gt, spec["k"], capture, duration))
        for key in c1:
            rec[f"{key}_cpu_s"] = round(c1[key] - c0.get(key, 0), 3); rec[f"{key}_cpu_util_cores"] = round((c1[key] - c0.get(key, 0)) / wall, 3)
            rec[f"{key}_peak_rss_mib"] = round(prss.get(key, 0) / 2**20, 1)
            if IS_MAC:
                rec[f"{key}_peak_footprint_maxproc_mib"] = round(smp.last_fpmax.get(key, 0) / 2**20, 1)
                rec[f"{key}_peak_footprint_mib"] = round(smp.last_fp.get(key, 0) / 2**20, 1)
            else: rec[f"{key}_peak_pss_mib"] = round(ppss.get(key, 0) / 2**20, 1)
        rec["post_swap_used_gib"] = swap_used_gib()
        if spec["mode"] == "cold" and res:
            t_first = min(datetime.datetime.fromisoformat(r["timestamp"]) for r in res); bins = {}
            for r in res:
                b = int((datetime.datetime.fromisoformat(r["timestamp"]) - t_first).total_seconds() // 5); bins.setdefault(b, []).append(r["latency"] / 1e6)
            rec["cold_timeline_p99_ms"] = {str(b): float(np.percentile(v, 99)) for b, v in sorted(bins.items())}
            rec["first_request_ms"] = sorted(res, key=lambda r: r["timestamp"])[0]["latency"] / 1e6
        rec["invalid"] = invalid(rec, duration); rec["saturated"] = saturated(rec); recs.append(rec)
        log(f"    {lang}/{store} {phase} {rate} rps: p50 {rec['p50_ms']:.2f} p99 {rec['p99_ms']:.2f} ms err {rec['error_rate']:.3f} "
            f"recall {rec['recall_heldout']} srv {rec.get('server_cpu_util_cores')} lg {rec['loadgen_cpu_util_cores']}{' SAT' if rec['saturated'] else ''}{' INVALID' if rec['invalid'] else ''}")
        return rec
    try:
        if spec["mode"] == "cold":
            mst = centre_mst(lang, store) or 4 * A.ladder_start
            step(max(A.ladder_start // 2, int(0.25 * mst)), A.cold_s, 0, "cold")
        else:
            attack(tg, A.warmup_rps, A.warmup_s, False)   # warmup, discarded
            if spec["mode"] == "short":
                mst = centre_mst(lang, store)
                rates = [max(A.ladder_start // 2, int(f * mst)) for f in (0.25, 0.75)] if mst else ladder_rates()[:2]
                for i, r in enumerate(rates): step(r, A.step_s, i, "short"); time.sleep(A.cooldown_s)
            else:
                # Doubling ladder. Outside E2 it starts near 1/16 of the centre MST of the same (L, S), rounded down to
                # the ladder grid, so each run spends ~5 steps below saturation; if that first step is already
                # saturated the ladder walks down by halving to the floor. E2 always starts at the floor.
                good = None; bad = None; grid = ladder_rates(); i0 = 0
                mst = centre_mst(lang, store) if spec["exp"] != "E2" else None
                if mst: i0 = max([i for i, r in enumerate(grid) if r <= mst / 16] or [0])
                idx = 0
                for i in range(i0, len(grid)):
                    r = grid[i]; rec = step(r, A.step_s, idx, "ladder"); idx += 1; time.sleep(A.cooldown_s)
                    if rec["invalid"]: continue
                    if rec["saturated"]: bad = r; break
                    good = r
                if good is None and i0 > 0:
                    for i in range(i0 - 1, -1, -1):
                        r = grid[i]; rec = step(r, A.step_s, idx, "ladder_down"); idx += 1; time.sleep(A.cooldown_s)
                        if not rec["invalid"] and not rec["saturated"]: good = r; break
                if spec["mode"] == "ladder+bisect" and good and bad:
                    lo, hi = good, bad
                    for j in range(A.bisect_steps):
                        mid = int(math.sqrt(lo * hi)); rec = step(mid, A.step_s, 100 + j, "bisect"); time.sleep(A.cooldown_s)
                        if rec["invalid"]: continue
                        if rec["saturated"]: hi = mid
                        else: lo = mid
    finally:
        smp.stop.set(); kill(srv); kill(sidecar); time.sleep(1)
    return recs

def main():
    global A, OUT
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", default="full"); ap.add_argument("--plan", default=None)
    ap.add_argument("--only-exp", default=None); ap.add_argument("--only-kind", default=None); ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--step-s", type=int, default=30); ap.add_argument("--warmup-s", type=int, default=20); ap.add_argument("--warmup-rps", type=int, default=50)
    ap.add_argument("--cooldown-s", type=int, default=3); ap.add_argument("--cold-s", type=int, default=60); ap.add_argument("--bisect-steps", type=int, default=3)
    ap.add_argument("--ladder-start", type=int, default=25); ap.add_argument("--ladder-max", type=int, default=25600)
    ap.add_argument("--gate-max-s", type=int, default=1800); ap.add_argument("--max-attempts", type=int, default=3)
    ap.add_argument("--filter", default=None, help="python expression over spec, e.g. \"s['dataset']=='c100k'\"")
    A = ap.parse_args(); OUT = ROOT / "results" / A.tag; (OUT / "runs").mkdir(parents=True, exist_ok=True)
    if not IS_MAC: os.sched_setaffinity(0, {int(x) for part in LOADGEN_CPUS.split(",") for x in (range(int(part.split("-")[0]), int(part.split("-")[-1]) + 1))})
    (OUT / "runner.pid").write_text(str(os.getpid()))
    with open(OUT / "runner_config.json", "w") as f: json.dump(dict(vars(A), started=now(), slo_p99_ms=SLO_P99_MS, body_capture_max_rps=BODY_CAPTURE_MAX_RPS,
                                                                  ef_grid=EF_GRID, n_calib=N_CALIB, sut_cpus=SUT_CPUS, loadgen_cpus=LOADGEN_CPUS,
                                                                  os=sys.platform, pinned=not IS_MAC, mem_need_gib=MEM_NEED_GIB), f, indent=1)
    plan = [json.loads(l) for l in open(A.plan or OUT / "plan.jsonl")]
    if A.only_exp: plan = [s for s in plan if s["exp"] in A.only_exp.split(",")]
    if A.only_kind: plan = [s for s in plan if s["kind"] in A.only_kind.split(",")]
    if A.filter: plan = [s for s in plan if eval(A.filter, {}, {"s": s})]
    if A.limit: plan = plan[:A.limit]
    log(f"runner start pid {os.getpid()}: {len(plan)} runs in scope")
    fns = dict(build=run_build, calib=run_calib, store_only=run_store_only, cell=run_cell)
    for spec in plan:
        if (OUT / "STOP").exists(): log("STOP file found, exiting"); break
        done = OUT / "runs" / f"{spec['id']}.jsonl"; failed = OUT / "runs" / f"{spec['id']}.failed"
        if done.exists(): continue
        attempts = int(failed.read_text().split()[0]) if failed.exists() else 0
        if attempts >= A.max_attempts: continue
        need = MEM_NEED_GIB.get(spec.get("dataset"), 3.0) + (BUILD_EXTRA_GIB if spec["kind"] == "build" else 0)
        g = gate(need) if spec["kind"] != "calib" else gate(need)
        base = dict(spec, run_id=spec["id"], run_started=now(), **g); t0 = time.time()
        label = " ".join(str(spec.get(k)) for k in ("exp", "kind", "lang", "store", "dataset", "m", "recall", "k", "payload", "enc", "topology", "cache", "py_model", "mode", "rep") if spec.get(k) is not None)
        log(f"run {spec['order']} {spec['id']} {label}")
        try:
            recs = fns[spec["kind"]](spec, base)
            for r in recs: r["run_wall_s"] = round(time.time() - t0, 1)
            tmp = done.with_suffix(".tmp")
            with open(tmp, "wb") as f:
                for r in recs: f.write(orjson.dumps(r, option=orjson.OPT_SERIALIZE_NUMPY) + b"\n")
            tmp.rename(done)
            if failed.exists(): failed.unlink()
            log(f"done {spec['id']} in {time.time() - t0:.0f} s ({len(recs)} records)")
        except Exception as e:
            failed.write_text(f"{attempts + 1}\n{now()}\n{traceback.format_exc()}")
            log(f"FAILED {spec['id']} attempt {attempts + 1}: {str(e).splitlines()[0][:300] if str(e) else type(e).__name__}")
    log("runner pass finished")

if __name__ == "__main__":
    main()
