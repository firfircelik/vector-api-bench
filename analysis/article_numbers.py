"""Every number in the article, recomputed from the Mac result CSVs and raw logs.
Inputs: the analysis CSVs produced by analysis.py, pgscan_vs_full_mac.py, pgrep5_vs_full_mac.py,
pgrep5_recompute.py, rerun_centre.py, ladder_start_test.py (same MST definition: highest offered
rate with a valid step, sent >= 98 %, errors <= 0.1 %, p99 <= 100 ms).
Usage: python3 analysis/article_numbers.py  -> analysis/out/article_numbers.json + CSVs (DATA defaults to ./data)"""
import os, re, json, glob
from collections import Counter
from pathlib import Path
import numpy as np, pandas as pd

_gist = Path(__file__).resolve().parent.parent / "data"
DATA = Path(os.environ.get("DATA", _gist)); A = DATA / "analysis"
OUT = Path(os.environ.get("OUT", Path(__file__).resolve().parent / "out")); OUT.mkdir(exist_ok=True, parents=True)
N = {}
def put(k, v): N[k] = v.item() if hasattr(v, "item") else v
def cfgcol(df, rk="recall"):
    return df.dataset + "|R" + df[rk].round(4).astype(str) + "|k" + df.k.astype(int).astype(str) + "|" + df.payload
gmean = lambda x: float(np.exp(np.log(np.clip(np.asarray(x, float), 1, None)).mean()))

r = pd.read_csv(A / "runs_cells.csv"); r["config"] = cfgcol(r)
P5 = pd.read_csv(A / "pgrep5_runs.csv")               # 105 runs, config already built
P5["contam"] = (P5.so > 0) | (P5.pi_rate / P5.night_pi_rate > 4)  # rule from pgrep5_recompute.py
moved = set(zip(P5.lang, P5.config))
RR = pd.read_csv(A / "rerun_centre_runs.csv")          # 20 centre reruns
CENTRE = "c300k|R0.95|k10|ids"

# ---------------- integrity / exclusions (full-mac) ----------------
put("fullmac_runs", len(r)); put("fullmac_runs_by_exp", r.exp.value_counts().to_dict())
put("fullmac_outliers", int(r.outlier.sum())); put("fullmac_excluded", int(r.exclude.sum()))
put("fullmac_excluded_by_exp", r[r.exclude].exp.value_counts().to_dict())
nj = json.load(open(A / "numbers.json"))
for k in ["steps_total", "steps_invalid", "steps_overload_drain", "steps_host_invalid", "swap_max_gib", "avail_median_gib",
          "avail_min_gib", "gate_timeout_runs", "sens_cells_total", "sens_no_exclusions_cells_changed", "A5_cache_dropped_true"]:
    put("fullmac_" + k, nj[k])

# ---------------- CORE table: 23 configs x 3 langs x 3 stores ----------------
def core_table(exclude):
    c = r[(r.exp == "CORE") & (~r.exclude if exclude else True)]
    m = c.groupby(["config", "lang", "store"]).mst.median().unstack("store")
    for (cf, l), g in P5.groupby(["config", "lang"]): m.loc[(cf, l), "pgvector"] = g.mst.median()
    return m
M = core_table(False); Mx = core_table(True)
M.to_csv(OUT / "core_final_mst_median.csv")
put("core_cells", len(M)); put("core_configs", M.index.get_level_values(0).nunique()); put("pgvector_cells_replaced_by_pgrep5", len(moved))
def summary(m, tag):
    for st in m: put(f"{tag}gm_{st}", round(gmean(m[st])))
    for ds in ["c100k", "c300k"]:
        sub = m[m.index.get_level_values(0).str.startswith(ds)]
        for st in m: put(f"{tag}gm_{st}_{ds}", round(gmean(sub[st])))
        put(f"{tag}cells_{ds}", len(sub))
    w = m.eq(m.max(axis=1), axis=0); strict = w[w.sum(axis=1) == 1]
    for st in m: put(f"{tag}wins_incl_ties_{st}", int(w[st].sum())); put(f"{tag}wins_outright_{st}", int(strict[st].sum()))
    put(f"{tag}tied_cells", int((w.sum(axis=1) > 1).sum()))
    return w
W = summary(M, ""); summary(Mx, "exclrule_")
# wins by dataset and k
idx = M.index.to_frame(index=False); idx["k"] = idx.config.str.extract(r"\|k(\d+)\|")[0].astype(int); idx["ds"] = idx.config.str[:5]
Wf = W.reset_index(drop=True).join(idx)
put("wins_incl_ties_by_ds_k", {f"{d}_k{k}": g[["duckdb", "lancedb", "pgvector"]].sum().astype(int).to_dict() | {"cells": len(g)}
                               for (d, k), g in Wf.groupby(["ds", "k"])})
# geo-mean by ds x k
gk = {}
for (d, k), g in idx.groupby(["ds", "k"]):
    sub = M.iloc[g.index]
    gk[f"{d}_k{k}"] = {st: round(gmean(sub[st])) for st in M} | {"cells": len(g)}
put("gm_by_ds_k", gk)
# language geo-means per store
lg = {st: {l: round(gmean(M.xs(l, level="lang")[st])) for l in ["python", "go", "rust"]} for st in M}
put("gm_by_store_lang", lg)
# best/worst language ratio per store, median over configs
lr = {}
for st in M:
    t = M[st].unstack("lang"); lr[st] = float((t.max(axis=1) / t.min(axis=1).clip(lower=1)).median())
put("median_best_over_worst_language_ratio", lr)
sr = {}
for l in ["python", "go", "rust"]:
    t = M.xs(l, level="lang"); sr[l] = float((t.max(axis=1) / t.min(axis=1).clip(lower=1)).median())
put("median_best_over_worst_store_ratio", sr)
# c300k k100 pgvector final medians
low = M[M.index.get_level_values(0).str.contains(r"c300k\|R.*\|k100")]
put("c300k_k100_pgvector_range", [float(low.pgvector.min()), float(low.pgvector.max())])
put("c300k_k100_lancedb_range", [float(low.lancedb.min()), float(low.lancedb.max())])
put("pgvector_cells_with_zero_mst", int((M.pgvector == 0).sum()))

# ---------------- centre (E2) final ----------------
e2 = r[r.exp == "E2"].copy()
cent = []
for (l, st), g in e2.groupby(["lang", "store"]):
    g2 = g[~g.exclude]
    if st == "pgvector":
        rr = RR[(RR.lang == l) & (RR.store == st)]
        clean = rr[~((rr.lang == "python") & (rr.rep == 0))]
        cent.append(dict(lang=l, store=st, source="full-mac-rerun", mst_median=clean.mst.median(), reps=" ".join(str(int(x)) for x in rr.sort_values("rep").mst),
                         n_used=len(clean), old_fullmac_median=g.mst.median(), old_reps=" ".join(str(int(x)) for x in g.sort_values("rep").mst)))
    else:
        cent.append(dict(lang=l, store=st, source="full-mac", mst_median=g2.mst.median(), reps=" ".join(str(int(x)) for x in g2.sort_values("rep").mst), n_used=len(g2),
                         old_fullmac_median=g2.mst.median(), old_reps=""))
C = pd.DataFrame(cent); C.to_csv(OUT / "centre_final.csv", index=False)
put("centre_final", {f"{a}/{b}": float(m) for a, b, m in zip(C.lang, C.store, C.mst_median)})
put("centre_pgvector_old_median", {a: float(m) for a, s, m in zip(C.lang, C.store, C.old_fullmac_median) if s == "pgvector"})
put("centre_pgvector_ratio_new_over_old", {a: round(float(n / o), 2) for a, s, n, o in zip(C.lang, C.store, C.mst_median, C.old_fullmac_median) if s == "pgvector"})
py0 = RR[(RR.lang == "python") & (RR.store == "pgvector") & (RR.rep == 0)].iloc[0]
put("rerun_python_rep0", dict(mst=float(py0.mst), max_pre_swap_gib=float(py0.max_pre_swap), min_avail_gib=float(py0.min_avail)))
put("rerun_other_max_pre_swap_gib", float(RR[~((RR.lang == "python") & (RR.rep == 0) & (RR.store == "pgvector"))].max_pre_swap.max()))
gl = RR[RR.store == "lancedb"]; put("rerun_go_lancedb", dict(msts=gl.mst.tolist(), crashes=int(gl.crash.sum()), first_crash_rps=gl.first_crash.unique().tolist(),
    p50_100_median=float(gl.p50_100.median()), p99_100_median=float(gl.p99_100.median())))
oldc = pd.read_csv(A / "centre_E2_cells.csv").set_index(["lang", "store"])
put("fullmac_go_lancedb_p50_p99_100", [float(oldc.loc[("go", "lancedb"), "p50_ms_100"]), float(oldc.loc[("go", "lancedb"), "p99_ms_100"])])
# old full-mac E2 pgvector run conditions (swap, avail) vs rerun
ep = e2[e2.store == "pgvector"]
put("fullmac_E2_pgvector_swap_gib_range", [float(ep.max_pre_swap.min()), float(ep.max_pre_swap.max())])
put("fullmac_E2_pgvector_min_avail_gib_range", [float(ep.min_avail.min()), float(ep.min_avail.max())])
put("rerun_pgvector_min_avail_gib_range", [float(RR[RR.store == "pgvector"].min_avail.min()), float(RR[RR.store == "pgvector"].min_avail.max())])

# ---------------- variance decomposition (final data) ----------------
def anova(df, y):  # identical to analysis.py
    df = df.dropna(subset=[y]); gm = df[y].mean(); ss = {}
    ml = df.groupby("lang")[y].mean(); ms = df.groupby("store")[y].mean(); mc = df.groupby(["lang", "store"])[y].mean()
    nl = df.groupby("lang").size(); ns = df.groupby("store").size(); nc = df.groupby(["lang", "store"]).size()
    ss["L"] = (nl * (ml - gm) ** 2).sum(); ss["S"] = (ns * (ms - gm) ** 2).sum()
    inter = mc + gm - ml.reindex(mc.index.get_level_values(0)).values - ms.reindex(mc.index.get_level_values(1)).values
    ss["LxS"] = (nc * inter ** 2).sum(); tot = ((df[y] - gm) ** 2).sum(); ss["resid"] = tot - ss["L"] - ss["S"] - ss["LxS"]
    return {k: v / tot for k, v in ss.items()}
def var_shares(replace_centre):
    v = r[r.exp.isin(["CORE", "E2"]) & ~r.exclude][["config", "lang", "store", "mst"]].copy()
    keep = ~v.apply(lambda x: x.store == "pgvector" and (x.lang, x.config) in moved, axis=1)
    v = pd.concat([v[keep], P5.assign(store="pgvector")[["config", "lang", "store", "mst"]]])
    if replace_centre:
        v = v[~((v.config == CENTRE) & (v.store == "pgvector"))]
        rr = RR[(RR.store == "pgvector") & ~((RR.lang == "python") & (RR.rep == 0))].assign(config=CENTRE)
        v = pd.concat([v, rr[["config", "lang", "store", "mst"]]])
    v["y"] = np.log(v.mst.replace(0, np.nan))
    return pd.DataFrame([dict(config=k, **anova(g, "y")) for k, g in v.groupby("config")])
for tag, rc in [("varshare_", True), ("varshare_oldcentre_", False)]:
    AV = var_shares(rc)
    for c in ["S", "L", "LxS", "resid"]: put(tag + c, round(float(AV[c].median()), 3))
    put(tag + "store_gt_lang", f"{int((AV.S > AV.L).sum())}/{len(AV)}")
    if rc: AV.to_csv(OUT / "variance_shares_final.csv", index=False); put("varshare_centre_config", AV[AV.config == CENTRE].iloc[0].drop("config").round(3).to_dict())
    if rc: put("varshare_configs_lang_ge_store", AV[AV.L >= AV.S].config.tolist())

# ---------------- pgscan / pgrep5 / ladder test ----------------
ps = pd.read_csv(A / "pgscan_vs_full_mac.csv")
put("pgscan_runs", len(ps)); put("pgscan_moved", int(ps.moved.sum())); put("pgscan_moved_c300k", int(ps[ps.moved].dataset.eq("c300k").sum()))
put("pgscan_crash_runs", int(ps.crash.sum())); put("pgscan_host_invalid_steps", int(ps.host_invalid.sum()))
pv = pd.read_csv(A / "pgrep5_vs_full_mac.csv"); put("pgrep5_cells", len(pv)); put("pgrep5_verdicts", pv.verdict.value_counts().to_dict())
put("pgrep5_runs", len(P5)); put("pgrep5_contaminated_runs", int(P5.contam.sum())); put("pgrep5_crash_runs", int(P5.crash.sum()))
put("pgrep5_host_invalid_steps", int(P5.host_invalid.sum()))
put("pgrep5_contam_window", [str(P5[P5.contam].run_started.min()), str(P5[P5.contam].run_started.max())])
Mc = M.copy()
for (cf, l), g in P5[~P5.contam].groupby(["config", "lang"]): Mc.loc[(cf, l), "pgvector"] = g.mst.median()
put("gm_pgvector_excl_contaminated", round(gmean(Mc.pgvector)))
old = r[(r.exp == "CORE")].groupby(["config", "lang", "store"]).mst.median().unstack("store")
put("gm_pgvector_old_fullmac", round(gmean(old.pgvector))); put("gm_pgvector_old_fullmac_c300k", round(gmean(old.pgvector[old.index.get_level_values(0).str.startswith("c300k")])))
lt = pd.read_csv(A / "ladder_start_test_runs.csv")
put("ladder_test_runs", len(lt)); put("ladder_test_contaminated", int(lt.contaminated.sum()))
med = lt.groupby(["arm", "lang", "config"]).mst.median().unstack("arm")
put("ladder_test_gm_start25", round(gmean(med[25]))); put("ladder_test_gm_start100", round(gmean(med[100])))
put("ladder_test_medians", {f"{l} {c}": {"start25": float(a), "start100": float(b)} for (l, c), a, b in zip(med.index, med[25], med[100])})
put("ladder_test_window", [str(lt.run_started.min()), str(lt.run_started.max())])

# ---------------- Go/LanceDB failure mode ----------------
fm = pd.read_csv(A / "failure_mode_at_saturation.csv").set_index(["lang", "store"])
put("go_lancedb_share_first_sat_err_gt90", round(float(fm.loc[("go", "lancedb"), "share_err_gt90"]), 3)); put("go_lancedb_runs_for_share", int(fm.loc[("go", "lancedb"), "runs"]))
put("other_cells_share_err_gt90_max", float(fm.drop(("go", "lancedb")).share_err_gt90.max()))
slog = DATA / "results/full-mac-rerun/server.log"
if not slog.exists(): slog = Path(__file__).resolve().parent.parent / "logs/go_lancedb_crash_dump_excerpt.txt"  # gist: excerpt only
txt = open(slog, errors="replace").read()
dumps = re.split(r"(?m)^SIGABRT: abort", txt)[1:]
dd = []
for p in dumps:
    gs = re.findall(r"(?ms)^goroutine \d+ gp=\S+ m=(\S+) mp=\S+ \[([^\]]+)\]:\n(.*?)(?=^goroutine |\Z)", p)
    st = Counter(s.split(",")[0] for _, s, _ in gs); fn = Counter(m.group(1) for _, _, b in gs for m in [re.search(r"_Cfunc_(\w+)", b)] if m)
    dd.append(dict(goroutines=len(gs), in_syscall=st.get("syscall", 0), cgo_fn=fn.most_common(1)[0] if fn else None, max_m=max(int(x) for x in re.findall(r"\bm=(\d+)", p))))
put("rerun_server_log_pthread_eagain_lines", txt.count("pthread_create failed: Resource temporarily unavailable"))
put("rerun_go_lancedb_crash_dumps", dd)
if (DATA / "results/full-mac/server.log").exists():   # 1.7 GB raw tree, not in the gist
    full = open(DATA / "results/full-mac/server.log", errors="replace").read()
    put("fullmac_server_log_pthread_eagain", full.count("pthread_create failed: Resource temporarily unavailable"))
    put("fullmac_server_log_sigabrt", len(re.findall(r"(?m)^SIGABRT: abort", full)))
    put("fullmac_server_log_max_m", max(int(x) for x in re.findall(r"\bm=(\d+)", full)))
# thread timeline from host_monitor (vecapi process threads) for Go/LanceDB reruns
hm = pd.read_csv(DATA / "results/full-mac-rerun/host_monitor.csv", usecols=[0, 8], names=["ts", "procs"], header=0)
hm["ts"] = pd.to_datetime(hm.ts)
hm["threads"] = hm.procs.str.extract(r"vecapi:\d+:(\d+):")[0].astype(float)
steps = []
for f in glob.glob(str(DATA / "results/full-mac-rerun/runs/*.jsonl")):
    for l in open(f, errors="replace"):
        x = json.loads(l)
        if x.get("kind") == "step": steps.append({k: x.get(k) for k in ["id", "lang", "store", "rep", "offered_rps", "started", "step_wall_s", "error_rate", "p99_ms", "step_phase", "server_peak_rss_mib"]})
# NB: runner.py writes started=now() AFTER the step finishes, so the step window is [started - wall, started]
S = pd.DataFrame(steps); S["t1"] = pd.to_datetime(S.started); S["t0"] = S.t1 - pd.to_timedelta(S.step_wall_s, unit="s")
tl = []
for _, s in S[(S.lang == "go") | (S.store == "pgvector")].iterrows():
    w = hm[(hm.ts >= s.t0) & (hm.ts <= s.t1)]
    tl.append(dict(lang=s.lang, store=s.store, rep=s.rep, offered_rps=s.offered_rps, phase=s.step_phase, error_rate=s.error_rate, p99_ms=s.p99_ms,
                   max_threads=w.threads.max(), median_threads=w.threads.median(), samples=len(w)))
TL = pd.DataFrame(tl); TL.to_csv(OUT / "rerun_server_threads_by_step.csv", index=False)
g = TL[(TL.lang == "go") & (TL.store == "lancedb")]
put("go_lancedb_threads_max_by_rate", g.groupby("offered_rps").max_threads.max().dropna().astype(int).to_dict())
put("go_lancedb_error_by_rate_median", g.groupby("offered_rps").error_rate.median().round(3).to_dict())
put("pgvector_server_threads_max_overall", {l: int(x) for l, x in TL[TL.store == "pgvector"].groupby("lang").max_threads.max().dropna().items()})
json.dump(N, open(OUT / "article_numbers.json", "w"), indent=1, default=str)
print(json.dumps(N, indent=1, default=str))
