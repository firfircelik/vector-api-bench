import json, glob, pandas as pd
from pathlib import Path
RES = Path(__file__).resolve().parent.parent / "results/full-mac"
def load():
    rows = []
    for f in sorted(glob.glob(str(RES / "runs/*.jsonl"))):
        for l in open(f):
            r = json.loads(l); r.pop("lat_hist_log2x16_us", None)
            for k in ("knob", "health", "codes", "cold_timeline_p99_ms"):
                if k in r and isinstance(r[k], dict): r[k] = json.dumps(r[k])
            r.pop("ids", None); r.pop("lat_ms", None)
            rows.append(r)
    return pd.DataFrame(rows)
