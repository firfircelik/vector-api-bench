"""Analysis of the vector-api-bench full-mac run (Mac mini M2, 8 GiB, macOS, unpinned).
Reproducible from raw data: python3 analysis.py  (reads ../results/full-mac, writes CSVs, PNGs, numbers.json here).
Definitions follow bench/runner.py and docs/PROTOCOL.md:
  saturated step: sent < 98 % of offered, or error rate > 0.1 %, or p99 > 100 ms (SLO)
  MST (max sustainable throughput) of a run: highest offered rate among its valid, non-saturated steps (runner.centre_mst)
Reclassification made here (documented in FINDINGS.md): a step the runner marked INVALID because its wall time exceeded
duration + 10 s while it also hit the 10 s client timeout is an overload drain (vegeta waits for timed-out requests), not a
host stall, so it is counted as saturated. The Mac runner had no suspend-gap heartbeat; caffeinate kept the host awake."""
import json, math, itertools
from pathlib import Path
import numpy as np, pandas as pd
import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt, matplotlib.ticker
from load import load

OUT = Path(__file__).resolve().parent; RNG = np.random.default_rng(20261022); B = 10000
LANGS = ["python", "go", "rust"]; STORES = ["pgvector", "duckdb", "lancedb"]
CFG = ["exp", "lang", "store", "dataset", "m", "recall", "k", "payload", "enc", "cache", "topology", "py_model", "mode", "py_workers"]
NUM = {}

def put(key, v):
    NUM[key] = None if v is None or (isinstance(v, float) and math.isnan(v)) else (round(float(v), 4) if isinstance(v, (float, np.floating)) else v)
    return v

d = load(); d["py_workers"] = d["py_workers"].fillna(0).astype(int)
plan = pd.read_json(OUT.parent / "results/full-mac/plan.jsonl", lines=True)

# ------------------------------------------------------------------ 1. integrity
runs = d.groupby("run_id").first().reset_index()
cnt = pd.DataFrame({"planned": plan.groupby("exp").size(), "completed": runs.groupby("exp").size()}).fillna(0).astype(int)
cnt.loc["TOTAL"] = cnt.sum(); cnt.to_csv(OUT / "integrity_counts_by_exp.csv")
put("runs_planned", int(len(plan))); put("runs_completed", int(runs.run_id.nunique()))
put("plan_ids_missing", int(len(set(plan.id) - set(runs.run_id)))); put("extra_ids", int(len(set(runs.run_id) - set(plan.id))))
cc = plan[plan.kind == "cell"].groupby(["exp", "lang", "store"]).size().rename("planned").to_frame()
cc["completed"] = runs[runs.kind == "step"].groupby(["exp", "lang", "store"]).size()
cc.fillna(0).astype(int).to_csv(OUT / "integrity_counts_by_exp_cell.csv")

s = d[d.kind == "step"].copy()
s["overload_drain"] = s.invalid & (s.max_ms >= 9000)
s["host_invalid"] = s.invalid & ~s.overload_drain
s["sat_eff"] = s.saturated | s.overload_drain
s["crash"] = (s.error_rate > 0.99) & (s.p99_ms < 100)            # instant connection failures: server process gone
s["loadgen_hot"] = s.loadgen_cpu_util_cores > 1.5
s["swap_growth"] = s.post_swap_used_gib - s.pre_swap_used_gib
s["good"] = ~s.host_invalid & ~s.sat_eff
s["sat_reason"] = np.select([s.crash, s.overload_drain, s.error_rate > 0.001, (s.sent_rate.fillna(0) < 0.98 * s.offered_rps), s.p99_ms > 100],
                            ["crash", "overload_timeouts", "errors", "sent<98%", "p99>SLO"], "")
s.loc[~s.sat_eff, "sat_reason"] = ""
for k in ("invalid", "overload_drain", "host_invalid", "sat_eff", "crash", "loadgen_hot"): put(f"steps_{k}", int(s[k].sum()))
put("steps_total", int(len(s))); put("gate_timeout_runs", int(runs.gate_timeout.fillna(False).sum()))
s[s.sat_eff].groupby(["exp", "sat_reason"]).size().rename("steps").to_csv(OUT / "integrity_saturation_reasons.csv")
inv = s[s.invalid][["exp", "run_id", "lang", "store", "dataset", "recall", "k", "payload", "mode", "rep", "offered_rps", "started",
                    "step_wall_s", "max_ms", "sent_rate", "error_rate", "overload_drain", "host_invalid"]]
inv.to_csv(OUT / "integrity_invalid_steps.csv", index=False)
crash_runs = s[s.crash].groupby("run_id").agg(lang=("lang", "first"), store=("store", "first"), exp=("exp", "first"),
                                              first_crash_rps=("offered_rps", "min"), crash_steps=("crash", "sum"))
crash_runs.to_csv(OUT / "integrity_crashed_runs.csv"); put("crashed_runs", int(len(crash_runs)))
put("crashed_runs_by_cell", {"/".join(k): int(v) for k, v in crash_runs.groupby(["lang", "store"]).size().items()})

# run-level table for cell runs
def run_row(g):
    g = g.sort_values(["step_phase", "offered_rps"]); good = g[g.good]
    first = g.iloc[0]
    r = {c: first[c] for c in CFG + ["rep", "run_id", "order", "run_started", "gate_timeout", "gate_wait_s", "ef"]}
    r.update(mst=good.offered_rps.max() if len(good) else 0.0, n_steps=len(g), n_sat=int(g.sat_eff.sum()), n_good=len(good),
             crash=bool(g.crash.any()), max_pre_swap=g.pre_swap_used_gib.max(), max_swap_growth=g.swap_growth.max(),
             min_avail=g.pre_avail_gib.min(), max_load1=g.pre_load1.max(), loadgen_hot=bool(g.loadgen_hot.any()),
             recall_heldout=g.recall_heldout.median(), server_fp_mib=g.server_peak_footprint_mib.median(),
             pg_fp_max_mib=g.postgres_peak_footprint_maxproc_mib.median() if "postgres_peak_footprint_maxproc_mib" in g else np.nan,
             p50_first=g.iloc[0].p50_ms, p99_first=g.iloc[0].p99_ms, p99_second=g.iloc[1].p99_ms if len(g) > 1 else np.nan)
    # a good step above a saturated one (non-monotone ladder)
    lad = g[g.step_phase.isin(["ladder", "ladder_down"])].sort_values("offered_rps")
    bad_rates = lad[lad.sat_eff].offered_rps
    r["non_monotone"] = bool(len(bad_rates) and (lad[lad.good].offered_rps > bad_rates.min()).any())
    return pd.Series(r)
R = s.groupby("id").apply(run_row, include_groups=False).reset_index(drop=True)
R["gate_timeout"] = R.gate_timeout.fillna(False).astype(bool)
R["pressure"] = R.gate_timeout | (R.max_pre_swap >= 6.0) | (R.max_swap_growth >= 0.5)
# outlier vs leave-one-out sibling median (same configuration, other reps)
R["cfg"] = R[CFG].astype(str).agg("|".join, axis=1)
def loo(col, _thr):
    """log2 deviation of a run from the median of all reps of the same configuration (itself included, so with
    3 reps one extreme rep cannot drag the reference); 0 MST is coded -10."""
    dev = pd.Series(np.nan, index=R.index)
    for _, g in R.groupby("cfg"):
        if len(g) < 3: continue
        med = g[col].median()
        for i in g.index:
            v = R.at[i, col]
            dev[i] = -10.0 if v == 0 else (math.log2(v / med) if med and med > 0 else np.nan)
    return dev
R["dev_mst_log2"] = loo("mst", 2); R["dev_p99_first_log2"] = loo("p99_first", 2)
ladder = R["mode"].isin(["ladder", "ladder+bisect"])
# one-sided: host contention can only make a run worse (lower MST, higher p99), so only that side is flagged
R["outlier"] = np.where(ladder, R.dev_mst_log2 <= -2, R.dev_p99_first_log2 >= math.log2(5))
# owner-active window (post hoc, from the A/A control and the gate log): Mon 2026-10-26 09:00 to 21:10 TR.
# The A/A control had p99 >= 50 ms at 388 rps in 8 of its 9 runs inside it and the runner logged all 21 gate timeouts there.
W0, W1 = "2026-10-26T09:00", "2026-10-26T21:10"
R["in_window"] = (R.run_started >= W0) & (R.run_started <= W1)
R["exclude"] = R.outlier & (R.pressure | R.crash | R.in_window)
R.to_csv(OUT / "runs_cells.csv", index=False)
put("pressure_runs", int(R.pressure.sum())); put("outlier_runs", int(R.outlier.sum())); put("excluded_runs", int(R.exclude.sum()))
put("non_monotone_ladders", int(R.non_monotone.sum())); put("window_runs", int(R.in_window.sum()))
put("excluded_by_exp", R[R.exclude].groupby("exp").size().to_dict()); put("outliers_kept", int((R.outlier & ~R.exclude).sum()))
R[R.outlier].sort_values("order")[["exp", "lang", "store", "dataset", "recall", "k", "payload", "enc", "topology", "py_model", "mode",
    "rep", "run_id", "run_started", "mst", "dev_mst_log2", "p99_first", "dev_p99_first_log2", "gate_timeout", "max_pre_swap",
    "max_swap_growth", "min_avail", "crash", "pressure", "in_window", "exclude"]].to_csv(OUT / "integrity_outlier_runs.csv", index=False)
EX = set(R[R.exclude].run_id)
R[R.exclude].sort_values("order")[["run_id", "exp", "lang", "store", "dataset", "m", "recall", "k", "payload", "enc", "topology", "py_model", "mode", "rep",
    "run_started", "mst", "dev_mst_log2", "dev_p99_first_log2", "gate_timeout", "max_pre_swap", "max_swap_growth", "crash", "in_window"]].to_csv(OUT / "exclusions_recommended.csv", index=False)
# the disk-low-adjacent step (E3b python/duckdb rep 0, 300 rps, 2026-10-26T14:23:54)
e3 = s[(s.exp == "E3b") & (s.lang == "python") & (s.store == "duckdb")][["rep", "run_id", "offered_rps", "started", "p50_ms", "p99_ms",
       "error_rate", "pre_avail_gib", "pre_swap_used_gib", "post_swap_used_gib", "server_peak_footprint_mib", "sat_eff", "invalid"]]
e3.sort_values(["rep", "offered_rps"]).to_csv(OUT / "integrity_disklow_step_e3b_python_duckdb.csv", index=False)

# host-state timeline (hourly)
s["hour"] = s.started.str[:13]
hz = s.groupby("hour").agg(steps=("p50_ms", "size"), max_swap_gib=("pre_swap_used_gib", "max"), min_avail_gib=("pre_avail_gib", "min"),
                           median_load1=("pre_load1", "median"), gate_timeouts=("gate_timeout", "sum"))
hz.to_csv(OUT / "host_timeline_hourly.csv")
put("swap_max_gib", s.pre_swap_used_gib.max()); put("swap_final_gib", s.sort_values("started").post_swap_used_gib.iloc[-1])
put("avail_median_gib", s.pre_avail_gib.median()); put("avail_min_gib", s.pre_avail_gib.min())

Rk = R[~R.run_id.isin(EX)]; sk = s[~s.run_id.isin(EX)]

# ------------------------------------------------------------------ 2. centre (E2)
def boot_med(x):
    x = np.asarray(x, float); i = RNG.integers(0, len(x), (B, len(x))); m = np.median(x[i], 1); return np.percentile(m, [2.5, 97.5])
E2 = Rk[Rk.exp == "E2"]; e2s = sk[sk.exp == "E2"]
rows = []
for (l, st), g in E2.groupby(["lang", "store"]):
    gs = e2s[(e2s.lang == l) & (e2s.store == st)]
    r = dict(lang=l, store=st, reps=len(g), mst_median=g.mst.median(), mst_min=g.mst.min(), mst_max=g.mst.max(),
             mst_reps=" ".join(str(int(x)) for x in g.sort_values("rep").mst), ef=g.ef.iloc[0], recall_heldout=gs[gs.body_captured].recall_heldout.median())
    for rate in (100, 400):
        x = gs[gs.offered_rps == rate]
        r[f"p50_ms_{rate}"] = x.p50_ms.median(); r[f"p99_ms_{rate}"] = x.p99_ms.median()
        r[f"p99_ms_{rate}_min"] = x.p99_ms.min(); r[f"p99_ms_{rate}_max"] = x.p99_ms.max(); r[f"sat_{rate}"] = f"{int(x.sat_eff.sum())}/{len(x)}"
        cpu = (x.server_cpu_s.fillna(0) + x.postgres_cpu_s.fillna(0)) / x.ok.replace(0, np.nan) * 1000
        r[f"cpu_ms_per_req_{rate}"] = cpu.median()
    x = gs[gs.offered_rps == 100]
    r["server_footprint_mib"] = x.server_peak_footprint_mib.median()
    r["postgres_footprint_maxproc_mib"] = x.postgres_peak_footprint_maxproc_mib.median() if st == "pgvector" else np.nan
    rows.append(r)
C = pd.DataFrame(rows); C["lang"] = pd.Categorical(C.lang, LANGS); C["store"] = pd.Categorical(C.store, STORES)
C = C.sort_values(["store", "lang"]); C.to_csv(OUT / "centre_E2_cells.csv", index=False)
for _, r in C.iterrows(): put(f"E2_mst_{r.lang}_{r.store}", r.mst_median); put(f"E2_p50_100_{r.lang}_{r.store}", r.p50_ms_100)
# ratios with bootstrap CIs
rat = []
for st in STORES:
    for a, b in [("go", "python"), ("rust", "python"), ("rust", "go")]:
        xa = E2[(E2.lang == a) & (E2.store == st)].mst.values; xb = E2[(E2.lang == b) & (E2.store == st)].mst.values
        ia = RNG.integers(0, len(xa), (B, len(xa))); ib = RNG.integers(0, len(xb), (B, len(xb)))
        bt = np.median(xa[ia], 1) / np.median(xb[ib], 1)
        rat.append(dict(store=st, ratio=f"{a}/{b}", mst_ratio=np.median(xa) / np.median(xb), ci_lo=np.percentile(bt, 2.5), ci_hi=np.percentile(bt, 97.5)))
pd.DataFrame(rat).to_csv(OUT / "centre_E2_mst_ratios_bootstrap.csv", index=False)
# E2 latency vs load curve (median over reps)
curve = e2s[e2s.step_phase == "ladder"].groupby(["lang", "store", "offered_rps"]).agg(p50_ms=("p50_ms", "median"), p99_ms=("p99_ms", "median"),
        n=("p50_ms", "size"), n_sat=("sat_eff", "sum"), server_cpu_cores=("server_cpu_util_cores", "median"),
        postgres_cpu_cores=("postgres_cpu_util_cores", "median")).reset_index()
curve.to_csv(OUT / "centre_E2_latency_vs_load.csv", index=False)

# ------------------------------------------------------------------ 3. CORE: rankings and factor effects
core = Rk[Rk.exp.isin(["CORE", "E2"])].copy()
core["config"] = core.dataset + "|R" + core.recall.astype(str) + "|k" + core.k.astype(int).astype(str) + "|" + core.payload
M = core.groupby(["config", "lang", "store"]).mst.median().unstack(["lang", "store"])
M.to_csv(OUT / "core_mst_median_by_config.csv")
centre_cfg = "c300k|R0.95|k10|ids"
def kendall(a, b):
    a = np.asarray(a); b = np.asarray(b); n = len(a); c = dcount = 0
    for i, j in itertools.combinations(range(n), 2):
        x = np.sign(a[i] - a[j]) * np.sign(b[i] - b[j]); c += x > 0; dcount += x < 0
    return (c - dcount) / (n * (n - 1) / 2)
rk = []
for cfg, row in M.iterrows():
    v = row.values; ref = M.loc[centre_cfg].values
    best_store = {l: row[l].idxmax() if row[l].nunique() > 1 or True else None for l in LANGS}
    rk.append(dict(config=cfg, kendall_tau_vs_centre=kendall(v, ref), top_cell="/".join(row.idxmax()),
                   **{f"best_store_{l}": "|".join(row[l][row[l] == row[l].max()].index) for l in LANGS},
                   **{f"lang_order_{st}": ">".join(row.xs(st, level="store").sort_values(ascending=False).index) for st in STORES},
                   **{f"python_lowest_{st}": bool(row[("python", st)] <= row.xs(st, level="store").min()) for st in STORES}))
RK = pd.DataFrame(rk); RK.to_csv(OUT / "core_rank_stability.csv", index=False)
put("core_configs", int(len(RK))); put("kendall_tau_median", RK.kendall_tau_vs_centre.median()); put("kendall_tau_min", RK.kendall_tau_vs_centre.min())
for st in STORES: put(f"python_lowest_share_{st}", RK[f"python_lowest_{st}"].mean())
for l in LANGS: put(f"best_store_counts_{l}", RK[f"best_store_{l}"].value_counts().to_dict())
put("top_cell_counts", RK.top_cell.value_counts().to_dict())
# per-rep rank stability at centre: in how many reps is each cell's MST rank the same as the median ranking
# two-way variance decomposition per config (balanced 3x3 x reps): log MST and log p50 at ~25 % of the cell's MST
def anova(df, y):
    df = df.dropna(subset=[y]); gm = df[y].mean(); ss = {}
    ml = df.groupby("lang")[y].mean(); ms = df.groupby("store")[y].mean(); mc = df.groupby(["lang", "store"])[y].mean()
    nl = df.groupby("lang").size(); ns = df.groupby("store").size(); nc = df.groupby(["lang", "store"]).size()
    ss["L"] = (nl * (ml - gm) ** 2).sum(); ss["S"] = (ns * (ms - gm) ** 2).sum()
    inter = mc + gm - ml.reindex(mc.index.get_level_values(0)).values - ms.reindex(mc.index.get_level_values(1)).values
    ss["LxS"] = (nc * inter ** 2).sum(); tot = ((df[y] - gm) ** 2).sum(); ss["resid"] = tot - ss["L"] - ss["S"] - ss["LxS"]
    return {k: v / tot for k, v in ss.items()}
# p50 at ~25 % load: per run, the step whose rate is the largest <= 0.25 x the config-cell median MST
cellmst = core.groupby(["config", "lang", "store"]).mst.median()
cs = sk[sk.exp.isin(["CORE", "E2"]) & sk.step_phase.isin(["ladder", "ladder_down"])].copy()
cs["config"] = cs.dataset + "|R" + cs.recall.astype(str) + "|k" + cs.k.astype(int).astype(str) + "|" + cs.payload
cs["cmst"] = [cellmst.get((c, l, st), np.nan) for c, l, st in zip(cs.config, cs.lang, cs.store)]
lo = cs[(cs.offered_rps <= 0.25 * cs.cmst) & cs.good].sort_values("offered_rps").groupby("run_id").tail(1)
core = core.merge(lo[["run_id", "p50_ms", "p99_ms", "offered_rps"]].rename(columns={"p50_ms": "p50_25", "p99_ms": "p99_25", "offered_rps": "rate_25"}), on="run_id", how="left")
core["log_mst"] = np.log(core.mst.replace(0, np.nan)); core["log_p50_25"] = np.log(core.p50_25)
av = []
for cfg, g in core.groupby("config"):
    a1 = anova(g, "log_mst"); a2 = anova(g, "log_p50_25")
    av.append(dict(config=cfg, **{f"mst_{k}": v for k, v in a1.items()}, **{f"p50lo_{k}": v for k, v in a2.items()}))
AV = pd.DataFrame(av); AV.to_csv(OUT / "core_variance_shares.csv", index=False)
for c in AV.columns[1:]: put(f"varshare_median_{c}", AV[c].median())
put("h1_store_gt_lang_share_configs", float((AV.p50lo_S > AV.p50lo_L).mean()))
put("h2_mst_lang_vs_store_share_configs", float((AV.mst_L > AV.mst_S).mean()))
# factor effects: MST ratio per cell, other factors at centre
def eff(name, a, b):
    out = []
    for l in LANGS:
        for st in STORES:
            va, vb = M.loc[a, (l, st)], M.loc[b, (l, st)]
            out.append(dict(factor=name, lang=l, store=st, level_a=a, level_b=b, mst_a=va, mst_b=vb, ratio_a_over_b=va / vb if vb else np.nan))
    return out
EF = pd.DataFrame(eff("scale", "c100k|R0.95|k10|ids", centre_cfg) + eff("recall0.99", "c300k|R0.99|k10|ids", centre_cfg)
                  + eff("recall0.90", "c300k|R0.9|k10|ids", centre_cfg) + eff("k100", "c300k|R0.95|k100|ids", centre_cfg)
                  + eff("doc_payload", "c300k|R0.95|k10|doc", centre_cfg))
EF.to_csv(OUT / "core_factor_effects_mst.csv", index=False)
for f, g in EF.groupby("factor"): put(f"factor_mst_ratio_median_{f}", g.ratio_a_over_b.median())
# recall through the API per store x target, and knob
rc = sk[sk.exp.isin(["CORE", "E2"]) & sk.body_captured].groupby(["store", "dataset", "k", "recall"]).agg(
    recall_heldout_median=("recall_heldout", "median"), recall_heldout_min=("recall_heldout", "min"), ef=("ef", "median")).reset_index()
rc.to_csv(OUT / "recall_by_store_target.csv", index=False)
put("recall_heldout_min_all", rc.recall_heldout_min.min())

# ------------------------------------------------------------------ 4. ablations and controls
def mst_tab(exp, keys):
    g = Rk[Rk.exp == exp].groupby(keys).agg(reps=("mst", "size"), mst_median=("mst", "median"), mst_min=("mst", "min"), mst_max=("mst", "max"),
                                            p50_first_ms=("p50_first", "median"), p99_first_ms=("p99_first", "median"), server_fp_mib=("server_fp_mib", "median"))
    return g.reset_index()
a1 = mst_tab("A1", ["lang", "enc", "k", "payload"]); a1.to_csv(OUT / "ablation_A1_stub_api_floor.csv", index=False)
a1s = sk[(sk.exp == "A1") & (sk.offered_rps == 1600)].groupby(["lang", "enc", "k", "payload"]).agg(p50_ms_1600=("p50_ms", "median"),
       cpu_us_per_req_1600=("server_cpu_s", lambda x: np.nan)).reset_index()
a1c = sk[(sk.exp == "A1") & (sk.offered_rps == 1600)].assign(cpu_us=lambda x: x.server_cpu_s / x.ok * 1e6).groupby(["lang", "enc", "k", "payload"]).agg(
    p50_ms_1600=("p50_ms", "median"), p99_ms_1600=("p99_ms", "median"), server_cpu_us_per_req_1600=("cpu_us", "median")).reset_index()
a1c.to_csv(OUT / "ablation_A1_stub_at_1600rps.csv", index=False)
a2 = d[d.kind == "store_only_result"].groupby(["lang", "store", "recall", "conc"]).agg(reps=("qps", "size"), qps_median=("qps", "median"),
     qps_min=("qps", "min"), qps_max=("qps", "max"), p50_ms=("p50_ms", "median"), p99_ms=("p99_ms", "median"), cpu_ms_per_query=("cpu_ms_per_query", "median"),
     recall_heldout=("recall_heldout", "median"), ef=("ef", "median")).reset_index()
a2.to_csv(OUT / "ablation_A2_store_floor.csv", index=False)
a3 = mst_tab("A3", ["lang", "store", "topology"]).merge(C[["lang", "store", "mst_median"]].astype({"lang": str, "store": str}).rename(columns={"mst_median": "E2_default_mst"}), on=["lang", "store"])
a3.to_csv(OUT / "ablation_A3_topology.csv", index=False)
# A4 binary vs E2 JSON: server CPU per request and p50 at the same short rates (A4 short rates vs AA-free E2 nearest ladder rate)
a4 = sk[sk.exp == "A4"].assign(cpu_us=lambda x: (x.server_cpu_s + x.postgres_cpu_s.fillna(0)) / x.ok * 1e6, srv_us=lambda x: x.server_cpu_s / x.ok * 1e6)
rows = []
for (l, st, stp), g in a4.groupby(["lang", "store", "step"]):
    rate = g.offered_rps.median(); e = e2s[(e2s.lang == l) & (e2s.store == st) & (e2s.step_phase == "ladder")]
    near = e.offered_rps.unique()[np.argmin(abs(e.offered_rps.unique() - rate))]; e = e[e.offered_rps == near]
    rows.append(dict(lang=l, store=st, step=stp, bin_rate=rate, bin_server_cpu_us_per_req=g.srv_us.median(), bin_p50_ms=g.p50_ms.median(), bin_p99_ms=g.p99_ms.median(),
                     json_rate=near, json_server_cpu_us_per_req=(e.server_cpu_s / e.ok * 1e6).median(), json_p50_ms=e.p50_ms.median(), json_p99_ms=e.p99_ms.median(), reps=len(g)))
A4 = pd.DataFrame(rows); A4["server_cpu_ratio_bin_over_json"] = A4.bin_server_cpu_us_per_req / A4.json_server_cpu_us_per_req
A4.to_csv(OUT / "ablation_A4_binary_vs_json.csv", index=False)
for l in LANGS: put(f"A4_cpu_ratio_median_{l}", A4[(A4.lang == l) & (A4.step == 0)].server_cpu_ratio_bin_over_json.median())
a5 = sk[sk.exp == "A5"].groupby(["lang", "store"]).agg(reps=("p50_ms", "size"), cache_dropped_true=("cache_dropped", lambda x: int((x == True).sum())),
     rate=("offered_rps", "median"), cold_start_s=("cold_start_s", "median"), first_request_ms=("first_request_ms", "median"),
     p50_ms=("p50_ms", "median"), p99_ms=("p99_ms", "median"), sat=("sat_eff", "sum")).reset_index()
a5.to_csv(OUT / "ablation_A5_cold.csv", index=False); put("A5_cache_dropped_true", int(a5.cache_dropped_true.sum()))
a6 = mst_tab("A6", ["store", "py_model", "py_workers"]); a6.to_csv(OUT / "ablation_A6_python_process_model.csv", index=False)
e3 = sk[sk.exp == "E3b"].groupby(["lang", "store", "step"]).agg(reps=("p50_ms", "size"), rate=("offered_rps", "median"), ef=("ef", "median"),
     p50_ms=("p50_ms", "median"), p99_ms=("p99_ms", "median"), recall_heldout=("recall_heldout", "median"), sat=("sat_eff", "sum")).reset_index()
e3.to_csv(OUT / "ablation_E3b_M32.csv", index=False)
# A/A control: rust/pgvector c300k short at fixed rates, one run every 25 runs
aa = s[s.exp == "AA"].copy()   # all, including flagged, to show drift
aa.sort_values("started")[["rep", "run_id", "started", "step", "offered_rps", "p50_ms", "p99_ms", "sat_eff", "pre_avail_gib", "pre_swap_used_gib",
                           "postgres_cpu_util_cores", "postgres_peak_footprint_maxproc_mib"]].to_csv(OUT / "control_AA_timeseries.csv", index=False)
for stp, g in aa.groupby("step"):
    put(f"AA_step{stp}_rate", g.offered_rps.median()); put(f"AA_step{stp}_p50_median", g.p50_ms.median())
    put(f"AA_step{stp}_p50_p10", g.p50_ms.quantile(.1)); put(f"AA_step{stp}_p50_p90", g.p50_ms.quantile(.9))
    put(f"AA_step{stp}_p99_median", g.p99_ms.median()); put(f"AA_step{stp}_sat", int(g.sat_eff.sum())); put(f"AA_step{stp}_n", int(len(g)))
    put(f"AA_step{stp}_corr_p50_avail", float(np.corrcoef(g.p50_ms, g.pre_avail_gib)[0, 1]))
# pgvector memory mechanism: low-load p50 vs available memory, across every pgvector c300k warm ladder run at 100 rps
pg = sk[(sk.store == "pgvector") & (sk.dataset == "c300k") & (sk.offered_rps == 100) & (sk.topology == "default") & (sk.cache == "warm") & sk.exp.isin(["E2", "CORE"])]
for st in STORES:
    x = sk[(sk.store == st) & (sk.dataset == "c300k") & (sk.offered_rps == 100) & sk.exp.isin(["E2", "CORE"]) & (sk.k == 10) & (sk.payload == "ids")]
    put(f"corr_p50_100rps_avail_{st}_c300k", float(np.corrcoef(x.p50_ms, x.pre_avail_gib)[0, 1])); put(f"n_corr_{st}", int(len(x)))
    lo_, hi_ = x[x.pre_avail_gib < 2.0], x[x.pre_avail_gib >= 2.5]
    put(f"p50_100rps_{st}_c300k_avail_lt2", lo_.p50_ms.median()); put(f"p50_100rps_{st}_c300k_avail_ge2.5", hi_.p50_ms.median())
    put(f"n_{st}_avail_lt2", int(len(lo_))); put(f"n_{st}_avail_ge2.5", int(len(hi_)))
# builds and calibration
b = d[d.kind == "build"][["store", "dataset", "m", "rep", "scratch", "reused_setup_build", "build_s", "index_bytes", "file_bytes", "peak_rss_gib", "version"]]
b.to_csv(OUT / "builds_E1.csv", index=False)
cal = d[d.kind == "calib_point"]
kn = []
for (st, ds, m, k), g in cal.groupby(["store", "dataset", "m", "k"]):
    g = g.sort_values("ef")
    for t in (0.90, 0.95, 0.99):
        hit = g[g.recall_calib >= t]
        kn.append(dict(store=st, dataset=ds, m=m, k=int(k), target=t, ef=hit.ef.iloc[0] if len(hit) else g.ef.iloc[-1], reached=bool(len(hit)),
                       recall_calib=hit.recall_calib.iloc[0] if len(hit) else g.recall_calib.iloc[-1], recall_heldout=hit.recall_heldout.iloc[0] if len(hit) else g.recall_heldout.iloc[-1]))
pd.DataFrame(kn).to_csv(OUT / "calibration_E0_knobs.csv", index=False)

# sensitivity: centre MST medians with and without exclusions
sens = R[R.exp == "E2"].groupby(["lang", "store"]).mst.median().rename("mst_all_runs").to_frame().join(E2.groupby(["lang", "store"]).mst.median().rename("mst_after_exclusions"))
sens.to_csv(OUT / "sensitivity_E2_exclusions.csv")

# language ratios per store across the 24 CORE/E2 configurations (MST medians)
lr = []
for cfg, row in M.iterrows():
    for st in STORES:
        x = row.xs(st, level="store")
        lr.append(dict(config=cfg, store=st, go_over_python=x["go"] / x["python"], rust_over_python=x["rust"] / x["python"], rust_over_go=x["rust"] / x["go"],
                       python_strictly_lowest=bool(x["python"] < min(x["go"], x["rust"])), best_over_worst=x.max() / x.min()))
LR = pd.DataFrame(lr); LR.to_csv(OUT / "core_language_ratios_by_store.csv", index=False)
lrs = LR.groupby("store").agg(go_over_python_median=("go_over_python", "median"), rust_over_python_median=("rust_over_python", "median"),
        rust_over_go_median=("rust_over_go", "median"), python_strictly_lowest_share=("python_strictly_lowest", "mean"),
        best_over_worst_lang_median=("best_over_worst", "median")).reset_index()
lrs.to_csv(OUT / "core_language_ratios_summary.csv", index=False)
# store ratio within language (store spread), same configurations
sr = []
for cfg, row in M.iterrows():
    for l in LANGS:
        x = row[l]; sr.append(dict(config=cfg, lang=l, best_over_worst_store=x.max() / x.min(), lancedb_over_duckdb=x["lancedb"] / x["duckdb"], pgvector_over_duckdb=x["pgvector"] / x["duckdb"]))
SR = pd.DataFrame(sr); SR.to_csv(OUT / "core_store_ratios_by_lang.csv", index=False)
SR.groupby("lang").median(numeric_only=True).to_csv(OUT / "core_store_ratios_summary.csv")
# AA control modes (fast = p50 at 388 rps < 2.5 ms)
a388 = aa[aa.step == 1].copy(); a388["fast"] = a388.p50_ms < 2.5
put("AA_fast_runs", int(a388.fast.sum())); put("AA_runs", int(len(a388)))
put("AA_fast_p50_388_median", a388[a388.fast].p50_ms.median()); put("AA_slow_p50_388_median", a388[~a388.fast].p50_ms.median())
a129 = aa[aa.step == 0].set_index("run_id"); a388i = a388.set_index("run_id")
put("AA_fast_p50_129_median", a129.loc[a388i[a388i.fast].index].p50_ms.median()); put("AA_slow_p50_129_median", a129.loc[a388i[~a388i.fast].index].p50_ms.median())
put("AA_fast_pg_cpu_129", a129.loc[a388i[a388i.fast].index].postgres_cpu_util_cores.median()); put("AA_slow_pg_cpu_129", a129.loc[a388i[~a388i.fast].index].postgres_cpu_util_cores.median())
# sensitivity: whole owner-active window dropped
Rw = R[~R.in_window & ~R.exclude]
Mw = Rw[Rw.exp.isin(["CORE", "E2"])].assign(config=lambda x: x.dataset + "|R" + x.recall.astype(str) + "|k" + x.k.astype(int).astype(str) + "|" + x.payload).groupby(["config", "lang", "store"]).mst.median().unstack(["lang", "store"])
put("sens_window_dropped_cells_changed", int((Mw != M).sum().sum())); put("sens_cells_total", int(M.size))
(Mw / M).to_csv(OUT / "sensitivity_core_mst_window_dropped_over_main.csv")
Ma = R[R.exp.isin(["CORE", "E2"])].assign(config=lambda x: x.dataset + "|R" + x.recall.astype(str) + "|k" + x.k.astype(int).astype(str) + "|" + x.payload).groupby(["config", "lang", "store"]).mst.median().unstack(["lang", "store"])
put("sens_no_exclusions_cells_changed", int((Ma != M).sum().sum()))

# failure mode at saturation: the first saturated ladder step of each CORE/E2 run
fs = sk[sk.exp.isin(["CORE", "E2"]) & sk.step_phase.isin(["ladder"]) & sk.sat_eff].sort_values("offered_rps").groupby("run_id").head(1)
fm = fs.groupby(["lang", "store"]).agg(runs=("run_id", "size"), share_err_gt90=("error_rate", lambda x: float((x > 0.9).mean())),
        share_overload_timeouts=("overload_drain", "mean"), median_error_rate=("error_rate", "median"), median_p99_ms=("p99_ms", "median")).reset_index()
fm.to_csv(OUT / "failure_mode_at_saturation.csv", index=False)
# latency spread at a fixed load (200 rps, k 10) by store and scale: pgvector widens at 300k
sp = sk[sk.exp.isin(["CORE", "E2"]) & (sk.offered_rps == 200) & (sk.k == 10)]
spr = sp.groupby(["store", "dataset"]).agg(steps=("p50_ms", "size"), p50_p10=("p50_ms", lambda x: x.quantile(.1)), p50_median=("p50_ms", "median"),
        p50_p90=("p50_ms", lambda x: x.quantile(.9)), corr_p50_avail=("p50_ms", lambda x: float(np.corrcoef(x, sp.loc[x.index, "pre_avail_gib"])[0, 1]))).reset_index()
spr.to_csv(OUT / "latency_spread_200rps_by_store_scale.csv", index=False)

# ------------------------------------------------------------------ 5. charts
col = {"python": "#3776ab", "go": "#00add8", "rust": "#b7410e"}; mk = {"pgvector": "o", "duckdb": "s", "lancedb": "^"}
fig, ax = plt.subplots(figsize=(8, 4.5))
for i, (st, l) in enumerate(itertools.product(STORES, LANGS)):
    g = E2[(E2.lang == l) & (E2.store == st)]
    ax.scatter([i] * len(g), g.mst.clip(lower=12), color=col[l], marker=mk[st], alpha=.6); ax.hlines(g.mst.median(), i - .3, i + .3, color="k")
ax.set_xticks(range(9)); ax.set_xticklabels([f"{l}\n{st}" for st, l in itertools.product(STORES, LANGS)], fontsize=8); ax.set_yscale("log")
ax.set_ylabel("MST (rps, p99 <= 100 ms)"); ax.set_title("Centre cell (c300k, R 0.95, k 10, ids): MST per rep, bar = median"); fig.tight_layout(); fig.savefig(OUT / "fig1_centre_mst.png", dpi=130)
fig, axs = plt.subplots(1, 3, figsize=(12, 4), sharey=True)
for ax, st in zip(axs, STORES):
    for l in LANGS:
        g = curve[(curve.lang == l) & (curve.store == st) & (curve.n >= 3) & (2 * curve.n_sat < curve.n)]
        ax.plot(g.offered_rps, g.p50_ms, "-o", color=col[l], label=f"{l} p50"); ax.plot(g.offered_rps, g.p99_ms, "--", color=col[l], alpha=.6, label=f"{l} p99")
    ax.set_xscale("log"); ax.set_yscale("log"); ax.set_title(st); ax.set_xlabel("offered rps"); ax.axhline(100, color="grey", lw=.5)
    ax.xaxis.set_minor_formatter(matplotlib.ticker.NullFormatter())
axs[0].set_ylabel("ms (median over reps)"); axs[0].legend(fontsize=7); fig.suptitle("Centre latency vs load, median over reps (rates with >= 3 reps, majority not saturated)"); fig.tight_layout(); fig.savefig(OUT / "fig2_centre_latency_vs_load.png", dpi=130)
fig, ax = plt.subplots(figsize=(10, 4)); t = pd.to_datetime(aa.started)
for stp, m_ in ((0, "o"), (1, "s")):
    g = aa[aa.step == stp]; ax.scatter(pd.to_datetime(g.started), g.p50_ms.clip(upper=99), marker=m_, s=14, label=f"p50 @ {int(g.offered_rps.median())} rps")
ax.set_yscale("log"); ax.set_ylim(1, 100); ax.set_ylabel("p50 ms (clipped at 100)"); ax2 = ax.twinx(); hh = s.sort_values("started"); ax2.plot(pd.to_datetime(hh.started), hh.pre_avail_gib, color="grey", lw=.4, alpha=.6)
ax2.set_ylabel("available memory GiB (grey)"); ax.legend(fontsize=8); ax.set_title("A/A control rust/pgvector c300k over the run"); fig.tight_layout(); fig.savefig(OUT / "fig3_AA_control_drift.png", dpi=130)
fig, axs = plt.subplots(1, 3, figsize=(12, 3.8), sharey=True)
for ax, st in zip(axs, STORES):
    x = sk[(sk.store == st) & (sk.dataset == "c300k") & (sk.offered_rps == 100) & sk.exp.isin(["E2", "CORE"]) & (sk.k == 10) & (sk.payload == "ids")]
    for l in LANGS:
        y = x[x.lang == l]; ax.scatter(y.pre_avail_gib, y.p50_ms, s=10, color=col[l], alpha=.6, label=l)
    ax.set_title(f"{st} c300k, 100 rps, k10 ids"); ax.set_xlabel("available memory before step (GiB)"); ax.set_yscale("log")
axs[0].set_ylabel("p50 ms"); axs[0].legend(fontsize=7); fig.tight_layout(); fig.savefig(OUT / "fig4_p50_vs_available_memory.png", dpi=130)
fig, ax = plt.subplots(figsize=(8, 4)); ax.bar(range(len(RK)), RK.kendall_tau_vs_centre); ax.set_xticks(range(len(RK))); ax.set_xticklabels(RK.config, rotation=90, fontsize=6)
ax.set_ylabel("Kendall tau vs centre ranking"); ax.set_title("Rank stability of the 9 cells (median MST) across CORE configurations"); fig.tight_layout(); fig.savefig(OUT / "fig5_rank_stability.png", dpi=130)

json.dump(NUM, open(OUT / "numbers.json", "w"), indent=1, default=str)
print(json.dumps(NUM, indent=1, default=str))
