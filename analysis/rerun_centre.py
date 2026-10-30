import json, glob, numpy as np, pandas as pd
from pathlib import Path
RES = Path("../results/full-mac-rerun")
plan = pd.read_json(RES/"plan.jsonl", lines=True); ids=set(plan.id)
rows=[]
for f in sorted(glob.glob(str(RES/"runs/*.jsonl"))):
    for l in open(f):
        r=json.loads(l)
        for k in list(r):
            if isinstance(r[k],(dict,list)): r.pop(k)
        rows.append(r)
d=pd.DataFrame(rows); s=d[(d.kind=="step")&d.id.isin(ids)].copy() if "id" in d else None
s["overload_drain"]=s.invalid&(s.max_ms>=9000); s["host_invalid"]=s.invalid&~s.overload_drain
s["sat_eff"]=s.saturated|s.overload_drain; s["crash"]=(s.error_rate>0.99)&(s.p99_ms<100); s["good"]=~s.host_invalid&~s.sat_eff
s["swap_growth"]=s.post_swap_used_gib-s.pre_swap_used_gib
R=[]
for rid,g in s.groupby("id"):
    good=g[g.good]; f=g.iloc[0]
    R.append(dict(lang=f.lang,store=f.store,rep=f.rep,run_started=g.run_started.iloc[0] if "run_started" in g else None,mst=good.offered_rps.max() if len(good) else 0,
      crash=bool(g.crash.any()),first_crash=g[g.crash].offered_rps.min(),host_invalid=int(g.host_invalid.sum()),n_steps=len(g),
      max_pre_swap=g.pre_swap_used_gib.max(),max_swap_growth=g.swap_growth.max(),min_avail=g.pre_avail_gib.min(),loadgen_hot=bool((g.loadgen_cpu_util_cores>1.5).any()),
      p50_100=g[g.offered_rps==100].p50_ms.median(),p99_100=g[g.offered_rps==100].p99_ms.median(),gate_wait=f.get("gate_wait_s"),gate_timeout=f.get("gate_timeout")))
R=pd.DataFrame(R).sort_values(["store","lang","rep"]); pd.set_option("display.width",250); print(R.to_string(index=False))
old=pd.read_csv("centre_E2_cells.csv").set_index(["lang","store"])
out=[]
for (l,st),g in R.groupby(["lang","store"]):
    o=old.loc[(l,st)]
    out.append(dict(lang=l,store=st,new_med=g.mst.median(),new_reps=" ".join(str(int(x)) for x in g.sort_values("rep").mst),old_med=o.mst_median,old_reps=o.mst_reps,
      new_p99_100=g.p99_100.median(),old_p99_100=o.p99_ms_100,new_p50_100=g.p50_100.median(),old_p50_100=o.p50_ms_100,crashes=int(g.crash.sum())))
O=pd.DataFrame(out); print(O.to_string(index=False)); O.to_csv("rerun_centre_vs_full_mac.csv",index=False); R.to_csv("rerun_centre_runs.csv",index=False)
