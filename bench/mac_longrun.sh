#!/usr/bin/env bash
# macOS entry point for the long run, started by the user LaunchAgent com.firat.vectorapibench (bench/launchd/).
# caffeinate -dimsu keeps the Mac awake (display, idle, disk, system on AC) for exactly as long as the run lives.
# Stop cleanly: touch results/<tag>/STOP (after the current run), then bench/mac_agent.sh unload.
cd "$(dirname "$0")/.."; source bench/macenv.sh
TAG=${1:-full-mac}; mkdir -p "results/$TAG"
bench/pg.sh status >/dev/null 2>&1 || bench/pg.sh start >> "results/$TAG/longrun.out" 2>&1
exec /usr/bin/caffeinate -dimsu bench/longrun.sh "$TAG" --gate-max-s "${GATE_MAX_S:-180}"
