#!/usr/bin/env bash
# One-shot clean-conditions rerun of the centre cell (E2: c300k, R 0.95, k 10, ids, JSON) for pgvector x {python, go,
# rust} and go x lancedb, 5 reps each = 20 runs, same harness and settings as full-mac, tag full-mac-rerun.
# Started by the LaunchAgent com.firat.vectorapibench.rerun at 01:00 on 2026-10-28. Guards:
#   - starts only between 2026-10-28 00:55 and 05:00 TR (if the Mac slept through 01:00, launchd fires on wake: then it
#     does nothing and unloads itself)
#   - 07:45 TR: touches STOP (the runner finishes the current run and exits)
#   - 08:00 TR: SIGINT to the runner (the in-flight run is left as .tmp and does not count; completed runs stay valid),
#     then any leftover API server on :8080 is terminated
#   - at the end: stops the monitor, stops the bench Postgres (it was stopped before), unloads and removes the agent.
cd "$(dirname "$0")/.."; ROOT=$PWD; TAG=full-mac-rerun; OUT=$ROOT/results/$TAG; LABEL=com.firat.vectorapibench.rerun
PL="$HOME/Library/LaunchAgents/$LABEL.plist"; UIDN=$(id -u); REAL_HOME=$HOME
log(){ echo "$(date +%Y-%m-%dT%H:%M:%S) [rerun] $*" >> "$OUT/progress.log"; }
selfunload(){ log "one-shot: removing and unloading $LABEL"; rm -f "$PL"; launchctl bootout "gui/$UIDN/$LABEL" >/dev/null 2>&1; }
now=$(date +%Y%m%d%H%M)
if [ "${RERUN_FORCE_WINDOW:-}" != 1 ] && { [ "$now" -lt 202610280055 ] || [ "$now" -gt 202610280500 ]; }; then
  log "fired at $(date '+%F %T'), outside the 2026-10-28 00:55-05:00 start window: not starting"; selfunload; exit 0
fi
SOFT=$(date -j -f "%Y-%m-%d %H:%M:%S" "2026-10-28 07:45:00" +%s); HARD=$(date -j -f "%Y-%m-%d %H:%M:%S" "2026-10-28 08:00:00" +%s)
/usr/bin/caffeinate -dimsu -w $$ &
log "start: disk free $(df -g "$ROOT" | awk 'NR==2{print $4}') GiB, $(sysctl -n vm.swapusage)"
pgrep -fl "bench/runner.py" >/dev/null && { log "another runner is active, not starting"; selfunload; exit 0; }
source bench/macenv.sh
bench/pg.sh status >/dev/null 2>&1 || bench/pg.sh start >> "$OUT/longrun.out" 2>&1
bench/rerun_monitor.sh "$OUT/host_monitor.csv" & MON=$!
( while true; do
    t=$(date +%s)
    if [ "$t" -ge "$HARD" ]; then
      log "08:00 hard stop: SIGINT to runner"; pkill -INT -f "bench/runner.py --tag $TAG"; sleep 20
      for p in $(lsof -ti tcp:8080 -sTCP:LISTEN 2>/dev/null); do kill "$p" 2>/dev/null; done; exit 0
    elif [ "$t" -ge "$SOFT" ] && [ ! -e "$OUT/STOP" ]; then log "07:45 soft stop: STOP file (finish current run)"; touch "$OUT/STOP"; fi
    sleep 20
  done ) & WD=$!
bench/longrun.sh "$TAG" --gate-max-s 180
kill $WD $MON 2>/dev/null
# crash reports macOS wrote for the API servers during the run (Go aborts also go to server.log)
mkdir -p "$OUT/crash_reports"; find "$REAL_HOME/Library/Logs/DiagnosticReports" -newer "$OUT/plan.jsonl" -iname "*vecapi*" -exec cp -p {} "$OUT/crash_reports/" \; 2>/dev/null
bench/pg.sh stop >> "$OUT/longrun.out" 2>&1
log "done: $(ls "$OUT/runs" | grep -c '\.jsonl$') run files (incl. 2 copied calibration runs)"
selfunload
