# full-mac-rerun (scheduled 2026-10-28 01:00 TR, one-shot)

Purpose: clean-conditions rerun of the centre cell only. E2 = c300k, R 0.95, k 10, ids, JSON, ladder + bisection.
Cells: pgvector x {python, go, rust} and go x lancedb, 5 reps each = 20 runs. The ids, order and specs are copied
verbatim from results/full-mac/plan.jsonl. Same runner.py, settings, gate (--gate-max-s 180) and disk gate (3 GiB).
No config change (shared_buffers etc. untouched). results/full-mac is only read, never written.

Copied inputs (read-only copies from full-mac):
- runs/f0d5d39bcc5e.jsonl and runs/b322e36ceaf7.jsonl: the E0 calibration for pgvector and lancedb c300k m16 k10. The
  runner reads ef from these: pgvector ef 64, lancedb ef 96, as in full-mac. They are not part of plan.jsonl, which is
  why status.py shows "2/20 complete" before the start.
- targets/c300k_json_k10_ids.jsonl: identical request file, sha256-checked.

Added logging (harness unchanged): host_monitor.csv every 2 s, from bench/rerun_monitor.sh. It records free and
compressor pages, cumulative pageins, pageouts, swapins and swapouts, swap used, thread count and RSS per API server
process, and the bench Postgres tree RSS. Crash output goes to server.log as before; macOS crash reports for vecapi,
if any, are copied to crash_reports/.

Driver: bench/rerun_centre.sh via the LaunchAgent com.firat.vectorapibench.rerun.
