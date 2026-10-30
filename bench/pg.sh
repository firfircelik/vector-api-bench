#!/usr/bin/env bash
# Start/stop/restart the benchmark Postgres (private cluster in run/pgdata, port 5544, socket run/pgsock).
# Linux: pinned to the SUT cores with taskset. macOS: private PostgreSQL build in run/pginst, not pinned (no affinity API).
# Usage: bench/pg.sh start|stop|restart|status
cd "$(dirname "$0")/.."
if [ "$(uname)" = Darwin ]; then PGBIN=${PGBIN:-$PWD/run/pginst/bin}; PIN=(); else PGBIN=${PGBIN:-/usr/lib/postgresql/17/bin}; PIN=(taskset -c "${SUT_CPUS:-0-5}"); fi
case "$1" in
  start)   "${PIN[@]}" $PGBIN/pg_ctl -D run/pgdata -l run/pg.log -w start ;;
  stop)    $PGBIN/pg_ctl -D run/pgdata -m fast -w stop ;;
  restart) $PGBIN/pg_ctl -D run/pgdata -m fast -w stop; "${PIN[@]}" $PGBIN/pg_ctl -D run/pgdata -l run/pg.log -w start ;;
  status)  $PGBIN/pg_ctl -D run/pgdata status ;;
esac
