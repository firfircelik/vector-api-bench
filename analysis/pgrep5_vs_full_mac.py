import json, glob, numpy as np, pandas as pd
from pathlib import Path
RES=Path("../results/full-mac-pgrep5")
plan=pd.read_json(RES/"plan.jsonl",lines=True); ids=set(plan.id)
rows=[]
for f in sorted(glob.glob(str(RES/"runs/*.jsonl"))):
    for l in open(f):
        r=json.loads(l)
        for k in list(r):
            if isinstance(r[k],(dict,list)): r.pop(k)
        rows.append(r)
d=pd.DataFrame(rows); s=d[(d.kind=="step")&d.id.isin(ids)].copy()
# same step classification / MST definition as analysis.py and pgscan_vs_full_mac.py
s["overload_drain"]=s.invalid&(s.max_ms>=9000); s["host_invalid"]=s.invalid&~s.overload_drain
s["sat_eff"]=s.saturated|s.overload_drain; s["crash"]=(s.error_rate>0.99)&(s.p99_ms<100); s["good"]=~s.host_invalid&~s.sat_eff
s["swap_growth"]=s.post_swap_used_gib-s.pre_swap_used_gib
s["t0"]=pd.to_datetime(s.started); s["t1"]=s.t0+pd.to_timedelta(s.step_wall_s,unit="s")
hm=pd.read_csv(RES/"host_monitor.csv",usecols=range(8)); hm["ts"]=pd.to_datetime(hm.ts)
hm=hm.sort_values("ts").reset_index(drop=True)
PAGE=16384/2**20  # MiB per page on Apple Silicon
def win(a,b):
    w=hm[(hm.ts>=a)&(hm.ts<=b)]
    if len(w)<2: return dict(po=np.nan,so=np.nan,swap_d=np.nan,comp_max=np.nan,comp_d=np.nan,free_min=np.nan)
    return dict(po=w.pageouts.iloc[-1]-w.pageouts.iloc[0], so=w.swapouts.iloc[-1]-w.swapouts.iloc[0], pi=w.pageins.iloc[-1]-w.pageins.iloc[0],
                swap_rise=w.swap_used_mb.max()-w.swap_used_mb.iloc[0],
                swap_d=w.swap_used_mb.iloc[-1]-w.swap_used_mb.iloc[0], swap_max=w.swap_used_mb.max(),
                comp_max=w.compressor_pages.max()*PAGE, comp_d=(w.compressor_pages.max()-w.compressor_pages.iloc[0])*PAGE,
                free_min=w.free_pages.min()*PAGE)
R=[]
for rid,g in s.groupby("id"):
    g=g.sort_values("t0"); good=g[g.good]
    h=win(g.t0.min()-pd.Timedelta(seconds=25),g.t1.max())   # include warmup
    first=g.iloc[0]
    R.append(dict(id=rid,mst=good.offered_rps.max() if len(good) else 0,crash=bool(g.crash.any()),host_invalid=int(g.host_invalid.sum()),
      overload_steps=int(g.overload_drain.sum()),n_steps=len(g),run_started=g.run_started.iloc[0],gate_wait_s=first.gate_wait_s,
      gate_timeout=bool(first.gate_timeout),first_rps=first.offered_rps,first_p50=first.p50_ms,first_p99=first.p99_ms,
      max_pre_swap=g.pre_swap_used_gib.max(),max_swap_growth=g.swap_growth.max(),min_avail=g.pre_avail_gib.min(),
      t_end=g.t1.max(),max_load1=g.pre_load1.max(),loadgen_hot=bool((g.loadgen_cpu_util_cores>1.5).any()),recall=g.recall_heldout.median(),**h))
R=pd.DataFrame(R).merge(plan[["id","lang","dataset","recall","k","payload","rep","order"]].rename(columns={"recall":"R"}),on="id")
R["config"]=R.dataset+"|R"+R.R.round(4).astype(str)+"|k"+R.k.astype(int).astype(str)+"|"+R.payload
# pressure flag: same thresholds as analysis.py plus host_monitor evidence
R["pressure"]=R.gate_timeout|(R.max_pre_swap>=6.0)|(R.max_swap_growth>=0.5)
# overnight baseline: same per-run host_monitor metrics from the pgscan night (same run ids for rep 0)
NS=Path("../results/full-mac-pgscan"); nrows=[]
for f in sorted(glob.glob(str(NS/"runs/*.jsonl"))):
    for l in open(f):
        r=json.loads(l)
        if r.get("kind")=="step": nrows.append(dict(id=r["id"],started=r["started"],w=r["step_wall_s"],lang=r["lang"],store=r["store"],dataset=r["dataset"],R=round(r["recall"],4),k=r["k"],payload=r["payload"],exp=r["exp"]))
ns=pd.DataFrame(nrows); ns=ns[(ns.store=="pgvector")&(ns.exp=="CORE")]; ns["t0"]=pd.to_datetime(ns.started); ns["t1"]=ns.t0+pd.to_timedelta(ns.w,unit="s")
hm_day=hm; hm=pd.read_csv(NS/"host_monitor.csv",usecols=range(8)); hm["ts"]=pd.to_datetime(hm.ts); hm=hm.sort_values("ts").reset_index(drop=True)
NB=[]
for rid,g in ns.groupby("id"):
    h=win(g.t0.min()-pd.Timedelta(seconds=25),g.t1.max()); NB.append(dict(id=rid,lang=g.lang.iloc[0],config=f"{g.dataset.iloc[0]}|R{g.R.iloc[0]}|k{int(g.k.iloc[0])}|{g.payload.iloc[0]}",night_pi=h["pi"],night_po=h["po"],night_dur=(g.t1.max()-g.t0.min()).total_seconds()))
NB=pd.DataFrame(NB); hm=hm_day
night_pi_rate=(NB.night_pi/NB.night_dur)
print("overnight pgscan per-run pageins/s: median",night_pi_rate.median(),"p90",night_pi_rate.quantile(.9),"max",night_pi_rate.max(), "pageouts max",NB.night_po.max())
R["dur"]=R.n_steps*0+0
R=R.merge(NB[["lang","config","night_pi","night_dur"]],on=["lang","config"],how="left")
R["pi_rate"]=R.pi/((pd.to_datetime(R.t_end)-pd.to_datetime(R.run_started)).dt.total_seconds())
R["night_pi_rate"]=R.night_pi/R.night_dur
NIGHT_P90=night_pi_rate.quantile(.9)
R["hm_pressure"]=(R.so>0)|(R.swap_rise>=50)|(R.pi_rate>3*max(NIGHT_P90,1))
R.sort_values("order").to_csv("pgrep5_runs.csv",index=False)
fm=pd.read_csv("runs_cells.csv"); fm=fm[(fm.exp=="CORE")&(fm.store=="pgvector")].copy()
fm["config"]=fm.dataset+"|R"+fm.recall.round(4).astype(str)+"|k"+fm.k.astype(int).astype(str)+"|"+fm.payload
P=pd.read_csv("pgscan_vs_full_mac.csv"); P["config"]=P.dataset+"|R"+P.recall.round(4).astype(str)+"|k"+P.k.astype(int).astype(str)+"|"+P.payload
out=[]
for (cfg,lang),g in R.groupby(["config","lang"]):
    o=fm[(fm.config==cfg)&(fm.lang==lang)].sort_values("rep"); p=P[(P.config==cfg)&(P.lang==lang)].iloc[0]
    new=g.sort_values("rep").mst.values; nm=np.median(new); om=o.mst.median(); ps=p.mst
    def close(a,b): return b>0 and 0.8<=a/b<=1.25 if a>0 else b==0
    verdict = "old confirmed" if close(nm,om) and not close(nm,ps) else "pgscan confirmed" if close(nm,ps) and not close(nm,om) else \
              "both (old~pgscan~new)" if close(nm,om) and close(nm,ps) else "neither"
    out.append(dict(lang=lang,config=cfg,new_median=nm,new_min=new.min(),new_max=new.max(),new_reps=" ".join(str(int(v)) for v in new),
        old_median=om,old_reps=" ".join(str(int(v)) for v in o.mst),pgscan=ps,new_over_old=nm/om if om else np.nan,new_over_pgscan=nm/ps if ps else np.nan,
        verdict=verdict,hm_pressure_reps=int(g.hm_pressure.sum()),pressure_reps=int(g.pressure.sum()),crash_reps=int(g.crash.sum()),
        host_invalid_steps=int(g.host_invalid.sum()),new_first_started=g.run_started.min()))
O=pd.DataFrame(out).sort_values(["config","lang"]); O.to_csv("pgrep5_vs_full_mac.csv",index=False)
pd.set_option("display.width",250); pd.set_option("display.max_columns",30)
print(O.drop(columns=["new_first_started"]).to_string(index=False)); print(O.verdict.value_counts())
print("crash runs",R.crash.sum(),"host_invalid steps",R.host_invalid.sum(),"overload steps",R.overload_steps.sum(),"gate_timeout",R.gate_timeout.sum(),"pressure",R.pressure.sum(),"hm_pressure",R.hm_pressure.sum())
print(R.sort_values("order")[["order","lang","config","rep","run_started","mst","first_rps","first_p99","max_pre_swap","swap_d","so","po","pi_rate","night_pi_rate","swap_rise","comp_max","free_min","min_avail","max_load1","hm_pressure"]].to_string(index=False))
