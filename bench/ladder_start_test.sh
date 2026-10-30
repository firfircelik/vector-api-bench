#!/usr/bin/env bash
# Ladder-start test (approved 2026-10-28 16:09 TR): 4 c300k k100 pgvector CORE configs x 3 reps x 2 arms = 24 runs,
# interleaved per results/full-mac-ladder25/sequence.txt. Arms differ ONLY in the E2 centre runs copied into runs/:
#   full-mac-ladder25  <- full-mac E2 pgvector (depressed centre: py 616, go 366, rust 518 -> /16 -> first step 25 rps)
#   full-mac-ladder100 <- full-mac-rerun E2 pgvector (clean centre: py 2466, go 2690, rust 2933 -> first step 100 rps)
# Same calibration files, targets, runner and settings as full-mac-pgrep5. Measurement logic untouched.
# Guards: refuses to start if another runner is active; 23:30 TR soft stop (STOP files), 23:45 TR SIGINT.
# Cancel: touch results/full-mac-ladder25/STOP
cd "$(dirname "$0")/.."; ROOT=$PWD; A=results/full-mac-ladder25; B=results/full-mac-ladder100
log(){ echo "$(date +%Y-%m-%dT%H:%M:%S) [ladder] $*" >> "$A/driver.log"; }
SOFT=$(date -j -f "%Y-%m-%d %H:%M:%S" "2026-10-28 23:30:00" +%s); HARD=$(date -j -f "%Y-%m-%d %H:%M:%S" "2026-10-28 23:45:00" +%s)
pgrep -fl "bench/runner.py" >/dev/null && { log "another runner is active, not starting"; exit 0; }
/usr/bin/caffeinate -dimsu -w $$ &
log "start: disk free $(df -g "$ROOT" | awk 'NR==2{print $4}') GiB, $(sysctl -n vm.swapusage)"
source bench/macenv.sh
bench/pg.sh status >/dev/null 2>&1 || bench/pg.sh start >> "$A/driver.log" 2>&1
bench/rerun_monitor.sh "$A/host_monitor.csv" & MON=$!
( while true; do t=$(date +%s)
    if [ "$t" -ge "$HARD" ]; then log "23:45 hard stop: SIGINT"; pkill -INT -f "bench/runner.py --tag full-mac-ladder"; sleep 20
      for p in $(lsof -ti tcp:8080 -sTCP:LISTEN 2>/dev/null); do kill "$p" 2>/dev/null; done; exit 0
    elif [ "$t" -ge "$SOFT" ] && [ ! -e "$A/STOP" ]; then log "23:30 soft stop"; touch "$A/STOP" "$B/STOP"; fi
    sleep 20; done ) & WD=$!
for pass in 1 2; do
  while read -r tag id; do
    [ -e "$A/STOP" ] && { log "STOP"; break 2; }
    [ -e "results/$tag/runs/$id.jsonl" ] && continue
    log "pass $pass run $tag $id"
    .venv/bin/python bench/runner.py --tag "$tag" --gate-max-s 180 --filter "s['id']=='$id'" >> "$A/runner.out" 2>&1
    [ -e "results/$tag/runs/$id.jsonl" ] && log "done $id" || log "missing $id after run"
  done < "$A/sequence.txt"
done
kill $WD $MON 2>/dev/null
bench/pg.sh stop >> "$A/driver.log" 2>&1
log "finished: ladder25 $(ls $A/runs | grep -c 'L25r') / 12, ladder100 $(ls $B/runs | grep -c 'L100r') / 12"
