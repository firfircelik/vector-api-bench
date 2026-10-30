import json, glob, numpy as np, pandas as pd
from pathlib import Path
RES=Path("../results/full-mac-pgscan")
plan=pd.read_json(RES/"plan.jsonl",lines=True); ids=set(plan.id)
rows=[]
for f in sorted(glob.glob(str(RES/"runs/*.jsonl"))):
    for l in open(f):
        r=json.loads(l)
        for k in list(r):
            if isinstance(r[k],(dict,list)): r.pop(k)
        rows.append(r)
d=pd.DataFrame(rows); s=d[(d.kind=="step")&d.id.isin(ids)].copy()
s["overload_drain"]=s.invalid&(s.max_ms>=9000); s["host_invalid"]=s.invalid&~s.overload_drain
s["sat_eff"]=s.saturated|s.overload_drain; s["crash"]=(s.error_rate>0.99)&(s.p99_ms<100); s["good"]=~s.host_invalid&~s.sat_eff
s["swap_growth"]=s.post_swap_used_gib-s.pre_swap_used_gib
R=[]
for rid,g in s.groupby("id"):
    good=g[g.good]
    R.append(dict(id=rid,mst=good.offered_rps.max() if len(good) else 0,crash=bool(g.crash.any()),host_invalid=int(g.host_invalid.sum()),
      n_steps=len(g),max_pre_swap=g.pre_swap_used_gib.max(),max_swap_growth=g.swap_growth.max(),min_avail=g.pre_avail_gib.min(),
      run_started=g.run_started.iloc[0] if "run_started" in g else None))
R=pd.DataFrame(R).merge(plan,on="id",how="right")
fm=pd.read_csv("runs_cells.csv"); fm=fm[(fm.exp=="CORE")&(fm.store=="pgvector")]
cfgmap=fm[fm.run_id.isin(ids)][["run_id","cfg"]].rename(columns={"run_id":"id"})
R=R.merge(cfgmap,on="id",how="left")
agg=fm.groupby("cfg").agg(old_med=("mst","median"),old_reps=("mst",lambda x:" ".join(str(int(v)) for v in x)),old_n=("mst","size"),old_rep0=("mst",lambda x: np.nan)).reset_index()
rep0=fm[fm.run_id.isin(ids)][["cfg","mst"]].rename(columns={"mst":"old_rep0"})
R=R.merge(agg.drop(columns="old_rep0"),on="cfg",how="left").merge(rep0,on="cfg",how="left")
R["ratio"]=R.mst/R.old_med; R["moved"]=(R.ratio>1.25)|(R.ratio<0.8)
pd.set_option("display.width",300)
cols=["lang","recall","k","payload","enc","m","dataset","cache","mst","old_med","old_reps","ratio","moved","crash","host_invalid","max_pre_swap","min_avail"]
print(R.sort_values(["lang","ratio"])[[c for c in cols if c in R]].to_string(index=False))
print("missing runs:",R.mst.isna().sum(),"missing cfg:",R.cfg.isna().sum())
print("moved:",R.moved.sum(),"of",len(R)); print(R.groupby("lang").agg(n=("moved","size"),moved=("moved","sum"),med_ratio=("ratio","median"),min_r=("ratio","min"),max_r=("ratio","max")))
print("ratio quantiles",R.ratio.describe())
print("crash runs",R.crash.sum(),"host_invalid steps",R.host_invalid.sum())
R.to_csv("pgscan_vs_full_mac.csv",index=False)
