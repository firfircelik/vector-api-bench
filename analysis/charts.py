"""Charts for the article. Reads out/*.csv from article_numbers.py plus the raw rerun host_monitor.
Usage: python3 analysis/charts.py  -> charts/*.png (DATA defaults to ./data, CHARTS to ./charts)"""
import os, json, glob
from pathlib import Path
import numpy as np, pandas as pd, matplotlib
matplotlib.use("Agg"); import matplotlib.pyplot as plt
_gist = Path(__file__).resolve().parent.parent / "data"
DATA = Path(os.environ.get("DATA", _gist)); A = DATA / "analysis"
HERE = Path(__file__).resolve().parent; O = HERE / "out"
CH = Path(os.environ.get("CHARTS", HERE.parent / "charts")); CH.mkdir(exist_ok=True, parents=True)
N = json.load(open(O / "article_numbers.json"))
plt.rcParams.update({"font.size": 11, "axes.spines.top": False, "axes.spines.right": False, "figure.dpi": 150})
SC = {"pgvector": "#336791", "lancedb": "#e07b39", "duckdb": "#9e9e2e"}; LANGS = ["python", "go", "rust"]
LN = {"python": "Python / FastAPI", "go": "Go / net/http", "rust": "Rust / axum"}; SN = {"pgvector": "pgvector", "lancedb": "LanceDB", "duckdb": "DuckDB VSS"}
gm = lambda x: float(np.exp(np.log(np.clip(np.asarray(x, float), 1, None)).mean()))
M = pd.read_csv(O / "core_final_mst_median.csv").set_index(["config", "lang"])
FOOT = "Mac mini M2, 8 GiB. MST = highest ladder step with >= 98% sent, <= 0.1% errors and p99 <= 100 ms, at equal recall. Medians of 5 runs per cell."

# 1. geo-mean MST by store x language, c100k vs c300k
fig, axs = plt.subplots(1, 2, figsize=(11, 4.6), sharey=True)
for ax, ds in zip(axs, ["c100k", "c300k"]):
    sub = M[M.index.get_level_values(0).str.startswith(ds)]; ncfg = sub.index.get_level_values(0).nunique(); x = np.arange(3); w = 0.26
    for i, st in enumerate(["pgvector", "lancedb", "duckdb"]):
        v = [gm(sub.xs(l, level="lang")[st]) for l in LANGS]
        b = ax.bar(x + (i - 1) * w, v, w, color=SC[st], label=SN[st])
        ax.bar_label(b, labels=[f"{y:.0f}" for y in v], fontsize=8, padding=2)
    ax.set_xticks(x, [LN[l] for l in LANGS]); ax.set_title(f"{ds[1:4]}k vectors ({ncfg} configs, geo-mean of cell medians)")
axs[0].set_ylabel("max sustainable throughput (rps)"); axs[0].legend(frameon=False)
fig.suptitle("Within each panel, colour (store) moves the bars more than position (language)", fontsize=12)
fig.text(0.01, 0.01, FOOT + " CORE: 23 configs x 9 cells, n = 5 runs per cell.", fontsize=7.5, color="#555")
fig.tight_layout(rect=(0, 0.04, 1, 0.95)); fig.savefig(CH / "fig1_mst_store_x_language_by_scale.png"); plt.close(fig)

# 2. variance shares
V = pd.read_csv(O / "variance_shares_final.csv")
fig, ax = plt.subplots(figsize=(7.5, 4.2))
parts = [("S", "store"), ("L", "language"), ("LxS", "store x language"), ("resid", "run-to-run residual")]
med = [V[c].median() * 100 for c, _ in parts]
for j, (c, _) in enumerate(parts):
    ax.scatter(np.full(len(V), j) + np.random.default_rng(j).uniform(-0.15, 0.15, len(V)), V[c] * 100, s=14, color="#999", zorder=2)
b = ax.bar(range(4), med, color=["#336791", "#e07b39", "#7a7a7a", "#c9c9c9"], alpha=0.85, zorder=1)
ax.bar_label(b, labels=[f"{m:.0f}%" for m in med], padding=3)
ax.set_xticks(range(4), [n for _, n in parts]); ax.set_ylabel("share of variance in log MST (%)")
ax.set_title(f"Store explains most of the throughput variance (median over {len(V)} configs)", fontsize=11)
fig.text(0.01, 0.01, "Two-way ANOVA sums of squares of log MST, per configuration (9 cells x 5 runs). Mac mini M2, 8 GiB.\nBars = medians over 24 configs (medians per term, so they do not sum to 100%); dots = configs.", fontsize=7.5, color="#555")
fig.tight_layout(rect=(0, 0.08, 1, 1)); fig.savefig(CH / "fig2_variance_shares.png"); plt.close(fig)

# 3. Go/LanceDB thread count across the 1600 -> 3200 rps step (rerun, 5 reps)
hm = pd.read_csv(DATA / "results/full-mac-rerun/host_monitor.csv", usecols=[0, 8], names=["ts", "procs"], header=0); hm["ts"] = pd.to_datetime(hm.ts)
hm["threads"] = hm.procs.str.extract(r"vecapi:\d+:(\d+):")[0].astype(float)
st = []
for f in glob.glob(str(DATA / "results/full-mac-rerun/runs/*.jsonl")):
    for l in open(f, errors="replace"):
        x = json.loads(l)
        if x.get("kind") == "step": st.append({k: x.get(k) for k in ["lang", "store", "rep", "offered_rps", "started", "step_wall_s", "error_rate"]})
S = pd.DataFrame(st); S["t1"] = pd.to_datetime(S.started); S["t0"] = S.t1 - pd.to_timedelta(S.step_wall_s, unit="s")  # started is written at step end
fig, ax = plt.subplots(figsize=(9, 4.4))
for rep in range(5):
    g = S[(S.lang == "go") & (S.store == "lancedb") & (S.rep == rep)]
    t3200 = g[g.offered_rps == 3200].t0.iloc[0]; t800 = g[g.offered_rps == 800].t0.iloc[0]
    w = hm[(hm.ts >= t800) & (hm.ts <= t3200 + pd.Timedelta(seconds=40))].copy()
    w["s"] = (w.ts - t3200).dt.total_seconds(); w.loc[w.threads.isna(), "threads"] = 0
    ax.plot(w.s, w.threads, marker=".", lw=1.2, label=f"run {rep + 1}")
for rep in range(5):
    g = S[(S.lang == "go") & (S.store == "pgvector") & (S.rep == rep)]
    t = g[g.offered_rps == 3200].t0
    if len(t):
        w = hm[(hm.ts >= t.iloc[0] - pd.Timedelta(seconds=30)) & (hm.ts <= t.iloc[0] + pd.Timedelta(seconds=40))].copy(); w["s"] = (w.ts - t.iloc[0]).dt.total_seconds()
        ax.plot(w.s, w.threads, color="#336791", lw=1, alpha=0.6, label="Go / pgvector (same rates)" if rep == 0 else None)
ax.axhline(2048, color="crimson", ls="--", lw=1); ax.text(-23, 2085, "kern.num_taskthreads = 2048 (per-process thread limit, macOS)", color="crimson", fontsize=8.5)
ax.axvspan(-24, 0, color="#f3f3f3", zorder=0)
ax.text(-23, 700, "end of the 1600 rps step\n(p99 ~2 ms, 0 errors)", fontsize=9, color="#555"); ax.text(4.5, 1500, "3200 rps step:\nprocess aborts\nwithin ~4 s", fontsize=9, color="#555")
ax.set_xlim(-24, 20); ax.set_ylim(0, 2300); ax.set_xlabel("seconds from the start of the 3200 rps step"); ax.set_ylabel("OS threads in the API process")
ax.set_title("Go / LanceDB: one OS thread per blocked cgo call, until the kernel says no", fontsize=11); ax.legend(frameon=False, fontsize=8, loc="upper right", bbox_to_anchor=(1.0, 0.62))
fig.text(0.01, 0.01, "Centre cell (c300k, R 0.95, k 10), rerun 2026-10-27, n = 5 runs. Thread count sampled every ~2 s by host_monitor; 0 = process gone (SIGABRT).\nThe 2 s sampler missed the run 1 peak; its Go crash dump shows thread m=1501.", fontsize=7.5, color="#555")
fig.tight_layout(rect=(0, 0.07, 1, 1)); fig.savefig(CH / "fig3_go_lancedb_thread_ceiling.png"); plt.close(fig)

# 4. centre pgvector: memory-pressure runs vs clean rerun
C = pd.read_csv(O / "centre_final.csv"); C = C[C.store == "pgvector"].set_index("lang")
RR = pd.read_csv(A / "rerun_centre_runs.csv"); r = pd.read_csv(A / "runs_cells.csv"); e2 = r[(r.exp == "E2") & (r.store == "pgvector")]
fig, ax = plt.subplots(figsize=(8.5, 4.4))
for i, l in enumerate(LANGS):
    o = e2[e2.lang == l]; n = RR[(RR.lang == l) & (RR.store == "pgvector")]
    ax.scatter(np.full(len(o), i - 0.18) + np.linspace(-0.05, 0.05, len(o)), o.mst, color="#b0b0b0", edgecolor="#555", s=40, zorder=3, label="first run, Oct 23 (swap 0.3 to 2.4 GiB)" if i == 0 else None)
    clean = n[~((n.lang == "python") & (n.rep == 0))]; bad = n[(n.lang == "python") & (n.rep == 0)]
    ax.scatter(np.full(len(clean), i + 0.18) + np.linspace(-0.05, 0.05, len(clean)), clean.mst, color="#336791", s=40, zorder=3, label="clean rerun, Oct 27 (swap 0.34 GiB)" if i == 0 else None)
    if len(bad): ax.scatter([i + 0.18], bad.mst, marker="x", color="crimson", s=60, zorder=4, label="first rerun run, swap 2.6 GiB (excluded)")
    ax.hlines(o.mst.median(), i - 0.3, i - 0.06, color="#555"); ax.hlines(clean.mst.median(), i + 0.06, i + 0.3, color="#336791")
    ax.text(i, 3150, f"{o.mst.median():.0f} -> {clean.mst.median():.0f}", ha="center", fontsize=9)
ax.set_xticks(range(3), [LN[l] for l in LANGS]); ax.set_ylabel("MST (rps), ladder + bisection"); ax.set_ylim(0, 3400)
ax.set_title("Centre cell, pgvector: same code, same data, different free memory", fontsize=11); ax.legend(frameon=False, fontsize=8, loc="center right")
fig.text(0.01, 0.01, "c300k, R 0.95, k 10, ids. n = 5 runs per group (clean rerun Python: 4); lines = medians.\npgvector index 1.23 GB > shared_buffers 1 GB, so part of it has to come from the OS page cache.", fontsize=7.5, color="#555")
fig.tight_layout(rect=(0, 0.08, 1, 1)); fig.savefig(CH / "fig4_centre_pgvector_memory_pressure.png"); plt.close(fig)

# 5. winner map: 23 configs x 3 languages
W = M.copy(); cfgs = sorted(W.index.get_level_values(0).unique(), key=lambda c: (c[:5], int(c.split("|k")[1].split("|")[0]), c))
code = {"pgvector": 0, "lancedb": 1, "duckdb": 2}; grid = np.full((len(cfgs), 3), np.nan); lab = [[""] * 3 for _ in cfgs]
for i, c in enumerate(cfgs):
    for j, l in enumerate(LANGS):
        row = W.loc[(c, l)]; mx = row.max(); win = [s for s in ["pgvector", "lancedb", "duckdb"] if row[s] == mx]
        grid[i, j] = code[win[0]] if len(win) == 1 else 3; lab[i][j] = "/".join(SN[s].split()[0] for s in win) + f"\n{mx:.0f}"
from matplotlib.colors import ListedColormap
fig, ax = plt.subplots(figsize=(7.2, 10))
ax.imshow(grid, cmap=ListedColormap([SC["pgvector"], SC["lancedb"], SC["duckdb"], "#d9d9d9"]), vmin=-0.5, vmax=3.5, aspect="auto")
for i in range(len(cfgs)):
    for j in range(3): ax.text(j, i, lab[i][j], ha="center", va="center", fontsize=6.5, color="white" if grid[i, j] < 3 else "black")
ax.set_xticks(range(3), [LN[l] for l in LANGS]); ax.set_yticks(range(len(cfgs)), [c.replace("|", "  ") for c in cfgs], fontsize=7.5)
ax.set_title("Fastest store per configuration and language (grey = tie on the 2x ladder)", fontsize=10)
fig.text(0.01, 0.005, "CORE, 69 cells, median MST of 5 runs (rps). Ties are common because CORE ladders double.", fontsize=7.5, color="#555")
fig.tight_layout(rect=(0, 0.02, 1, 1)); fig.savefig(CH / "fig5_winner_map.png"); plt.close(fig)
print(sorted(p.name for p in CH.glob("*.png")))
