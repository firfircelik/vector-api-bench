#!/usr/bin/env bash
# Host monitor for the full-mac-rerun (read-only sampling, every 2 s): free/compressed pages, cumulative pageins/
# pageouts/swapins/swapouts, swap used, and per benchmark process its thread count and RSS. Output: CSV-ish lines.
# Usage: bench/rerun_monitor.sh <outfile>   (runs until killed)
out=$1
echo "ts,free_pages,compressor_pages,pageins,pageouts,swapins,swapouts,swap_used_mb,procs(name:pid:threads:rss_kb)" >> "$out"
while true; do
  v=$(vm_stat)
  g(){ echo "$v" | awk -F: -v k="$1" '$1==k{gsub(/[ .]/,"",$2); print $2}'; }
  sw=$(sysctl -n vm.swapusage | awk '{for(i=1;i<=NF;i++) if($i=="used") {gsub(/M/,"",$(i+2)); print $(i+2)}}')
  pr=""
  for pid in $(pgrep -f "api/go/vecapi|release/vecapi|uvicorn app:app"); do
    th=$(( $(ps -M -p "$pid" 2>/dev/null | wc -l) - 1 )); rss=$(ps -o rss= -p "$pid" 2>/dev/null | tr -d ' ')
    nm=$(ps -o comm= -p "$pid" 2>/dev/null | awk -F/ '{print $NF}'); [ -n "$rss" ] && pr="$pr $nm:$pid:$th:$rss"
  done
  pm=$(head -1 run/pgdata/postmaster.pid 2>/dev/null); pg="postgres:none"
  if [ -n "$pm" ] && kill -0 "$pm" 2>/dev/null; then pg=$(ps -o rss= -p "$pm,$(pgrep -P "$pm" | paste -sd, -)" 2>/dev/null | awk '{n++; s+=$1} END {printf "postgres:%s:n%d:%d", "'"$pm"'", n, s}'); fi
  echo "$(date +%Y-%m-%dT%H:%M:%S),$(g 'Pages free'),$(g 'Pages occupied by compressor'),$(g 'Pageins'),$(g 'Pageouts'),$(g 'Swapins'),$(g 'Swapouts'),$sw,$pr $pg" >> "$out"
  sleep 2
done
