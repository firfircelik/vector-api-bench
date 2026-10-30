import pandas as pd, numpy as np
r=pd.read_csv("runs_cells.csv")
r["config"]=r.dataset+"|R"+r.recall.round(4).astype(str)+"|k"+r.k.astype(int).astype(str)+"|"+r.payload
N=pd.read_csv("pgrep5_runs.csv"); P=pd.read_csv("pgscan_vs_full_mac.csv")
P["config"]=P.dataset+"|R"+P.recall.round(4).astype(str)+"|k"+P.k.astype(int).astype(str)+"|"+P.payload
STRONG=(N.so>0)|(N.pi_rate/N.night_pi_rate>4)
N["contam"]=STRONG
print("contaminated runs (swapouts>0 or pageins/s >4x overnight same cell):")
print(N[N.contam][["order","lang","config","rep","run_started","mst","so","pi_rate","night_pi_rate","min_avail"]].to_string(index=False))
moved=set(zip(N.lang,N.config))
def anova(df, y):
    df = df.dropna(subset=[y]); gm = df[y].mean(); ss = {}
    ml = df.groupby("lang")[y].mean(); ms = df.groupby("store")[y].mean(); mc = df.groupby(["lang", "store"])[y].mean()
    nl = df.groupby("lang").size(); ns = df.groupby("store").size(); nc = df.groupby(["lang", "store"]).size()
    ss["L"] = (nl * (ml - gm) ** 2).sum(); ss["S"] = (ns * (ms - gm) ** 2).sum()
    inter = mc + gm - ml.reindex(mc.index.get_level_values(0)).values - ms.reindex(mc.index.get_level_values(1)).values
    ss["LxS"] = (nc * inter ** 2).sum(); tot = ((df[y] - gm) ** 2).sum(); ss["resid"] = tot - ss["L"] - ss["S"] - ss["LxS"]
    return {k: v / tot for k, v in ss.items()}
def scenario(name, newruns):
    core=r[r.exp=="CORE"]
    m=core.groupby(["config","lang","store"]).mst.median().unstack("store")
    if newruns is not None:
        if isinstance(newruns,str):   # pgscan single values
            for _,x in P.iterrows(): m.loc[(x.config,x.lang),"pgvector"]=x.mst
        else:
            for (c,l),g in newruns.groupby(["config","lang"]): m.loc[(c,l),"pgvector"]=g.mst.median()
    gm={s:float(np.exp(np.log(m[s].clip(lower=1)).mean())) for s in m}
    mx=m.max(axis=1); w=m.eq(mx,axis=0); strict=w[w.sum(axis=1)==1]
    # variance: CORE+E2 excl. as in analysis.py, pgvector runs of moved cells replaced by new reps
    v=r[r.exp.isin(["CORE","E2"])&~r.exclude][["config","lang","store","mst"]].copy()
    if newruns is not None and not isinstance(newruns,str):
        keep=~v.apply(lambda x:(x.store=="pgvector") and ((x.lang,x.config) in moved),axis=1)
        v=pd.concat([v[keep],newruns.assign(store="pgvector")[["config","lang","store","mst"]]])
    v["log_mst"]=np.log(v.mst.replace(0,np.nan))
    AV=pd.DataFrame([dict(config=k,**anova(g,"log_mst")) for k,g in v.groupby("config")])
    # store ranking by geo-mean, and per-config store ranking of geo-mean over languages
    return dict(scenario=name, gm_duckdb=round(gm["duckdb"]), gm_lancedb=round(gm["lancedb"]), gm_pgvector=round(gm["pgvector"]),
        wins_incl_ties_D=int(w.duckdb.sum()), wins_incl_ties_L=int(w.lancedb.sum()), wins_incl_ties_P=int(w.pgvector.sum()), tied_cells=int((w.sum(axis=1)>1).sum()),
        outright_D=int(strict.duckdb.sum()), outright_L=int(strict.lancedb.sum()), outright_P=int(strict.pgvector.sum()),
        var_S=round(AV.S.median(),3), var_L=round(AV.L.median(),3), var_LxS=round(AV.LxS.median(),3), var_resid=round(AV.resid.median(),3),
        store_gt_lang_configs=f"{int((AV.S>AV.L).sum())}/{len(AV)}", pgv_gm_c100k=round(float(np.exp(np.log(m.pgvector[m.index.get_level_values(0).str.startswith('c100k')].clip(lower=1)).mean()))),
        pgv_gm_c300k=round(float(np.exp(np.log(m.pgvector[m.index.get_level_values(0).str.startswith('c300k')].clip(lower=1)).mean()))),
        lan_gm_c300k=round(float(np.exp(np.log(m.lancedb[m.index.get_level_values(0).str.startswith('c300k')].clip(lower=1)).mean())))),m
rows=[];ms={}
for name,nr in [("old full-mac 5-rep",None),("pgscan 1-rep",'pgscan'),("pgrep5 all reps",N),("pgrep5 excl. contaminated",N[~N.contam])]:
    d,m=scenario(name,nr); rows.append(d); ms[name]=m
S=pd.DataFrame(rows); S.to_csv("pgrep5_recompute_summary.csv",index=False)
pd.set_option("display.width",250); print(S.T.to_string())
m=ms["pgrep5 all reps"]; print("cells where pgvector wins/ties (pgrep5):"); 
w=m.eq(m.max(axis=1),axis=0); print(w.groupby(m.index.get_level_values(0)).sum().to_string())
