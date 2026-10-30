#!/usr/bin/env bash
# Detached long run (no containers, no systemd). Start with:
#   setsid nohup bench/longrun.sh full > results/full/longrun.out 2>&1 < /dev/null &
# Each pass runs every planned run without a checkpoint file; completed runs are skipped, so a crash or a
# restart after a reboot resumes where it stopped. Stop cleanly: touch results/full/STOP (takes effect after
# the current run). Status: .venv/bin/python bench/status.py full
set -u
cd "$(dirname "$0")/.."
TAG=${1:-full}; OUT=results/$TAG
echo $$ > "$OUT/longrun.pid"
for pass in $(seq 1 6); do
  [ -e "$OUT/STOP" ] && break
  echo "$(date +%Y-%m-%dT%H:%M:%S) longrun pass $pass" >> "$OUT/progress.log"
  .venv/bin/python bench/runner.py --tag "$TAG" "${@:2}"
  left=$(.venv/bin/python - "$TAG" <<'PY'
import json, sys, pathlib
o = pathlib.Path("results") / sys.argv[1]
plan = [json.loads(l)["id"] for l in open(o / "plan.jsonl")]
done = {p.stem for p in (o / "runs").glob("*.jsonl")}
final = {p.stem for p in (o / "runs").glob("*.failed") if int(p.read_text().split()[0]) >= 3}
print(sum(1 for i in plan if i not in done and i not in final))
PY
)
  [ "$left" = "0" ] && break
  sleep 60
done
echo "$(date +%Y-%m-%dT%H:%M:%S) longrun finished" >> "$OUT/progress.log"
