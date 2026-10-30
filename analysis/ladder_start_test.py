# Ladder-start test: start=25 vs start=100 rps, 4 c300k k100 pgvector configs x 3 reps, interleaved (2026-10-28).
# Same step classification / MST definition as pgrep5_vs_full_mac.py. Outputs ladder_start_test_runs.csv, ladder_start_test.csv.
import json, glob, numpy as np, pandas as pd
from pathlib import Path
RES=Path("../results"); A=RES/"full-mac-ladder25"; B=RES/"full-mac-ladder100"
def load(d, only=None):
    rows=[]
    for f in sorted(glob.glob(str(d/"runs/*.jsonl"))):
        for l in open(f):
            r=json.loads(l)
            if r.get("kind")!="step" or (only is not None and r["id"] not in only): continue
            for k in list(r):
                if isinstance(r[k],(dict,list)): r.pop(k)
            rows.append(r)
    s=pd.DataFrame(rows)
    s["overload_drain"]=s.invalid&(s.max_ms>=9000); s["host_invalid"]=s.invalid&~s.overload_drain
    s["sat_eff"]=s.saturated|s.overload_drain; s["good"]=~s.host_invalid&~s.sat_eff
    s["t0"]=pd.to_datetime(s.started); s["t1"]=s.t0+pd.to_timedelta(s.step_wall_s,unit="s"); return s
plans={a:pd.read_json(d/"plan.jsonl",lines=True) for a,d in ((25,A),(100,B))}
s=pd.concat([load(A,set(plans[25].id)).assign(arm=25),load(B,set(plans[100].id)).assign(arm=100)])
hm=pd.read_csv(A/"host_monitor.csv",usecols=range(8)); hm["ts"]=pd.to_datetime(hm.ts); hm=hm.sort_values("ts")
def win(a,b):
    w=hm[(hm.ts>=a)&(hm.ts<=b)]
    if len(w)<2: return dict(so=np.nan,po=np.nan,swap_rise=np.nan)
    return dict(so=w.swapouts.iloc[-1]-w.swapouts.iloc[0],po=w.pageouts.iloc[-1]-w.pageouts.iloc[0],swap_rise=w.swap_used_mb.max()-w.swap_used_mb.iloc[0],swap_max=w.swap_used_mb.max())
R=[]
for (arm,rid),g in s.groupby(["arm","id"]):
    g=g.sort_values("t0"); good=g[g.good]; f=g.iloc[0]; s100=g[g.offered_rps==100]
    R.append(dict(arm=arm,id=rid,lang=f.lang,config=f"c300k|R{round(f.recall,4)}|k{f.k}|{f.payload}",rep=f.rep,run_started=f.run_started,
        first_rps=f.offered_rps,first_p99=f.p99_ms,mst=good.offered_rps.max() if len(good) else 0,n_steps=len(g),
        ladder_down=bool((g.step_phase=="ladder_down").any()),p99_at_100=s100.p99_ms.iloc[0] if len(s100) else np.nan,
        sat_at_100=bool(s100.sat_eff.iloc[0]) if len(s100) else np.nan,host_invalid=int(g.host_invalid.sum()),
        pre_swap_max=g.pre_swap_used_gib.max(),**win(g.t0.min()-pd.Timedelta(seconds=25),g.t1.max())))
R=pd.DataFrame(R); R["contaminated"]=(R.so>0)|(R.swap_rise>=50)|(R.host_invalid>0)
R.sort_values("run_started").to_csv("ladder_start_test_runs.csv",index=False)
# old full-mac 5-rep CORE runs of the same configs (they started at 25 because the depressed centre MSTs gave /16 < 50)
old=load(RES/"full-mac",set(i[:12] for i in plans[25].id)); O=old.groupby("id").apply(lambda g:pd.Series(dict(first=g.sort_values("t0").offered_rps.iloc[0],mst=g[g.good].offered_rps.max() if g.good.any() else 0)))
pg5=pd.read_csv("pgrep5_vs_full_mac.csv")
out=[]
for (lang,cfg),g in R.groupby(["lang","config"]):
    a,b=g[g.arm==25],g[g.arm==100]; bid=g.id.iloc[0][:12]
    o=pg5[(pg5.lang==lang)&(pg5.config==cfg)].iloc[0]
    def rng(x): return f"{int(x.min())}-{int(x.max())}"
    out.append(dict(lang=lang,config=cfg,
        s25_mst_median=a.mst.median(),s25_mst_range=rng(a.mst),s25_msts=" ".join(str(int(v)) for v in a.sort_values("rep").mst),
        s100_mst_median=b.mst.median(),s100_mst_range=rng(b.mst),s100_msts=" ".join(str(int(v)) for v in b.sort_values("rep").mst),
        ratio_25_over_100=a.mst.median()/b.mst.median() if b.mst.median() else np.nan,
        s25_p99_at100_median=a.p99_at_100.median(),s100_p99_at100_median=b.p99_at_100.median(),
        s25_sat_at100=f"{int(a.sat_at_100.fillna(False).sum())}/{len(a)}",s100_sat_at100=f"{int(b.sat_at_100.fillna(False).sum())}/{len(b)}",
        s100_ladder_down=f"{int(b.ladder_down.sum())}/{len(b)}",s25_first=",".join(map(str,sorted(set(a.first_rps)))),s100_first=",".join(map(str,sorted(set(b.first_rps)))),
        contaminated=int(g.contaminated.sum()),
        fullmac_old_msts=o.old_reps,fullmac_old_first=",".join(str(int(v)) for v in sorted(set(O.loc[[bid]].first))) if bid in O.index else "",
        pgrep5_msts=o.new_reps))
T=pd.DataFrame(out); T.to_csv("ladder_start_test.csv",index=False); print(T.to_string(index=False))
print(R.sort_values("run_started")[["run_started","arm","lang","config","rep","first_rps","p99_at_100","mst","n_steps","ladder_down","so","swap_rise","contaminated"]].to_string(index=False))
for arm in (25,100): print("arm",arm,"geo-mean MST (0->1)",float(np.exp(np.log(R[R.arm==arm].mst.clip(lower=1)).mean())))
# Stop-logic check: runner.invalid() flags a step when step_wall_s > duration+10. An overloaded step whose requests hit the
# 10 s client timeout drains for ~10 s after the 30 s step (wall ~40.1 s), so it is flagged INVALID (host-stall rule) instead of
# only SAT, and the ladder `continue`s up to --ladder-max (25600). If the first bad step has no 10 s timeouts (max_ms < 10 s),
# it is plain SAT and the ladder breaks. Same code in both arms; analysis treats invalid & max_ms>=9000 as saturation (overload_drain).
S=[]
for (arm,rid),g in s.groupby(["arm","id"]):
    g=g.sort_values("t0"); bad=g[g.saturated|g.invalid].iloc[0]
    S.append(dict(arm=arm,id=rid,n_steps=len(g),top_rps=g.offered_rps.max(),first_bad_rps=bad.offered_rps,first_bad_wall_s=bad.step_wall_s,
                  first_bad_max_ms=round(bad.max_ms),first_bad_sent_pct=round(100*bad.sent_rate/bad.offered_rps),first_bad_invalid=bool(bad.invalid),
                  climbed_to_max=bool(g.offered_rps.max()==25600)))
S=pd.DataFrame(S); S.to_csv("ladder_start_test_stops.csv",index=False)
print(S.groupby("arm").agg(runs=("id","count"),climbed=("climbed_to_max","sum"),first_bad_invalid=("first_bad_invalid","sum")))
