# Source me (macOS only). Keeps every toolchain, cache and the DuckDB extension directory inside the repo (run/),
# so nothing outside ~/vector-api-bench is touched: private rustup + cargo, Go toolchain + module cache, uv + Python,
# a private PostgreSQL 17.11 build, and HOME=run/home for the benchmark processes (DuckDB looks for extensions in
# $HOME/.duckdb/extensions).
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export REAL_HOME="${REAL_HOME:-$HOME}"
export RUSTUP_HOME="$ROOT/run/rustup" CARGO_HOME="$ROOT/run/cargo"
export GOPATH="$ROOT/run/gopath" GOMODCACHE="$ROOT/run/gopath/pkg/mod" GOCACHE="$ROOT/run/gocache" GOTOOLCHAIN=go1.27.3 GOTELEMETRY=off
export UV_CACHE_DIR="$ROOT/run/uv-cache" UV_PYTHON_INSTALL_DIR="$ROOT/run/uv-python" UV_TOOL_DIR="$ROOT/run/uv-tools"
export VEGETA="$ROOT/run/gopath/bin/vegeta" PGBIN="$ROOT/run/pginst/bin" PGSOCK="$ROOT/run/pgsock" PGPORT=5544
export PATH="$ROOT/run/pginst/bin:$ROOT/run/cargo/bin:$ROOT/run/gopath/bin:$REAL_HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
export HOME="$ROOT/run/home"; mkdir -p "$HOME"
