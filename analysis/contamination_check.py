"""Re-check the pgrep5 'contaminated run' flag with corrected step windows.
runner.py records step `started` after the step ends; pgrep5_vs_full_mac.py used [started-25s, last started+wall].
Here the run window is [first started - first wall - 25 s warm-up, last started]. Same rule: swapouts > 0 or
pageins/s > 4x the same cell's overnight (pgscan) rate."""
import os, json, glob
from pathlib import Path
import numpy as np, pandas as pd
DATA = Path(os.environ.get("DATA", "/workspace/vector-api-bench-mac"))
def steps(d):
    rows = []
    for f in glob.glob(str(DATA / f"results/{d}/runs/*.jsonl")):
        for l in open(f, errors="replace"):
            x = json.loads(l)
            if x.get("kind") == "step" and x.get("store") == "pgvector" and x.get("exp") == "CORE":
                rows.append(dict(id=x["id"], lang=x["lang"], cfg=f'{x["dataset"]}|R{round(x["recall"],4)}|k{int(x["k"])}|{x["payload"]}', rep=x["rep"],
                                 t1=pd.Timestamp(x["started"]), wall=x["step_wall_s"]))
    s = pd.DataFrame(rows); s["t0"] = s.t1 - pd.to_timedelta(s.wall, unit="s"); return s
def per_run(d):
    hm = pd.read_csv(DATA / f"results/{d}/host_monitor.csv", usecols=range(8)); hm["ts"] = pd.to_datetime(hm.ts)
    out = []
    for rid, g in steps(d).groupby("id"):
        a, b = g.t0.min() - pd.Timedelta(seconds=25), g.t1.max(); w = hm[(hm.ts >= a) & (hm.ts <= b)]
        out.append(dict(id=rid, lang=g.lang.iloc[0], cfg=g.cfg.iloc[0], rep=g.rep.iloc[0], so=w.swapouts.iloc[-1] - w.swapouts.iloc[0],
                        pi_rate=(w.pageins.iloc[-1] - w.pageins.iloc[0]) / (b - a).total_seconds()))
    return pd.DataFrame(out)
day = per_run("full-mac-pgrep5"); night = per_run("full-mac-pgscan")
day = day.merge(night[["lang", "cfg", "pi_rate"]].rename(columns={"pi_rate": "night_pi_rate"}), on=["lang", "cfg"])
day["contam_corrected"] = (day.so > 0) | (day.pi_rate / day.night_pi_rate > 4)
old = pd.read_csv(DATA / "analysis/pgrep5_runs.csv"); old["contam_old"] = (old.so > 0) | (old.pi_rate / old.night_pi_rate > 4)
m = day.merge(old[["id", "contam_old", "mst"]], on="id")
m.to_csv(Path(__file__).resolve().parent / "out" / "pgrep5_contamination_corrected.csv", index=False)
print("old flag:", int(m.contam_old.sum()), "corrected flag:", int(m.contam_corrected.sum()), "both:", int((m.contam_old & m.contam_corrected).sum()))
print(m[m.contam_old | m.contam_corrected][["lang", "cfg", "rep", "mst", "so", "pi_rate", "night_pi_rate", "contam_old", "contam_corrected"]].to_string(index=False))
# geo-mean impact
r = pd.read_csv(DATA / "analysis/runs_cells.csv"); r["config"] = r.dataset + "|R" + r.recall.round(4).astype(str) + "|k" + r.k.astype(int).astype(str) + "|" + r.payload
M = r[r.exp == "CORE"].groupby(["config", "lang", "store"]).mst.median().unstack("store")
for (c, l), g in m[~m.contam_corrected].groupby(["cfg", "lang"]): M.loc[(c, l), "pgvector"] = g.mst.median()
print("pgvector geo-mean, corrected-window exclusion:", round(float(np.exp(np.log(M.pgvector.clip(lower=1)).mean()))))
