"""Write vegeta JSON targets, one per query, so the request order cycles through all queries.
Usage: python bench/make_targets.py <name> <port> <k> <json|bin> <out.jsonl>
The query index travels in the URL (?qid=), so recall can be scored from vegeta's own result stream.
"""
import base64, json, sys
from pathlib import Path
import numpy as np, orjson
ROOT = Path(__file__).resolve().parent.parent
name, port, k, mode, out = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), sys.argv[4], sys.argv[5]
q = np.load(ROOT / "data" / name / "queries.npy")
with open(out, "w") as f:
    for i, v in enumerate(q):
        if mode == "json":
            body = orjson.dumps({"vector": v.tolist(), "k": k}); url = f"http://127.0.0.1:{port}/search?qid={i}"
            hdr = {"Content-Type": ["application/json"]}
        else:
            body = v.astype("<f4").tobytes(); url = f"http://127.0.0.1:{port}/search_bin?qid={i}&k={k}"
            hdr = {"Content-Type": ["application/octet-stream"]}
        f.write(json.dumps({"method": "POST", "url": url, "body": base64.b64encode(body).decode(), "header": hdr}) + "\n")
