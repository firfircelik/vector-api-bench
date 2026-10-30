#!/usr/bin/env bash
# One-time native setup on macOS (Apple silicon), everything under run/ and ext/ of this repo. No Docker, no Homebrew
# installs, no system services. Steps are idempotent; re-run to resume. Usage: bench/mac_setup.sh [step ...]
#   steps: rust go uv vegeta duckdb pg pgvector pginit   (default: all)
set -euo pipefail
cd "$(dirname "$0")/.."; source bench/macenv.sh; ROOT=$PWD; mkdir -p run ext
J=$(sysctl -n hw.ncpu)
step_rust() {   # private rustup, toolchain from api/rust/rust-toolchain.toml
  [ -x run/cargo/bin/rustup ] || { curl -fsSL https://sh.rustup.rs -o run/rustup-init.sh; sh run/rustup-init.sh -y --no-modify-path --profile minimal --default-toolchain 1.99.1; }
  rustup toolchain install 1.99.1 --profile minimal; (cd api/rust && rustc --version && cargo --version)
}
step_go() { go version; }   # GOTOOLCHAIN=go1.27.3 downloads the exact toolchain into run/gopath/pkg/mod
step_uv() {
  uv python install 3.13; [ -x .venv/bin/python ] || uv venv --python 3.13 .venv
  uv pip install --python .venv/bin/python -r requirements.lock.txt || uv pip install --python .venv/bin/python -r requirements.txt; .venv/bin/python --version
}
step_vegeta() { [ -x "$VEGETA" ] || go install github.com/tsenart/vegeta/v12@v12.13.0; "$VEGETA" -version | head -3; }
step_duckdb() { # official libduckdb v1.5.6 for the Rust crate + VSS extension for v1.5.6 osx_arm64 over https
  if [ ! -f ext/libduckdb-1.5.6/libduckdb.dylib ]; then
    curl -fsSL -o ext/libduckdb-osx-universal-v1.5.6.zip https://github.com/duckdb/duckdb/releases/download/v1.5.6/libduckdb-osx-universal.zip
    mkdir -p ext/libduckdb-1.5.6 && unzip -o -q ext/libduckdb-osx-universal-v1.5.6.zip -d ext/libduckdb-1.5.6
  fi
  shasum -a 256 ext/libduckdb-osx-universal-v1.5.6.zip
  d="$HOME/.duckdb/extensions/v1.5.6/osx_arm64"; mkdir -p "$d"
  [ -s "$d/vss.duckdb_extension" ] || { curl -fsSL -o ext/vss-v1.5.6-osx_arm64.duckdb_extension.gz https://extensions.duckdb.org/v1.5.6/osx_arm64/vss.duckdb_extension.gz; gunzip -c ext/vss-v1.5.6-osx_arm64.duckdb_extension.gz > "$d/vss.duckdb_extension"; }
  shasum -a 256 "$d/vss.duckdb_extension"
  .venv/bin/python -c "import duckdb; c=duckdb.connect(); c.execute('LOAD vss'); print('duckdb', duckdb.__version__, c.execute(\"select extension_version from duckdb_extensions() where extension_name='vss'\").fetchone())"
}
step_pg() {     # PostgreSQL 17.11 from source into run/pginst (the brew postgresql@17 service of the user is not touched)
  [ -x run/pginst/bin/postgres ] && { run/pginst/bin/postgres --version; return; }
  cd run; [ -f postgresql-17.11.tar.bz2 ] || curl -fsSLO https://ftp.postgresql.org/pub/source/v17.11/postgresql-17.11.tar.bz2
  rm -rf postgresql-17.11 && tar xjf postgresql-17.11.tar.bz2 && cd postgresql-17.11
  ./configure --prefix="$ROOT/run/pginst" --without-icu --without-readline > ../pg-configure.log
  make -j"$J" -s world-bin > ../pg-make.log 2>&1 && make -s install-world-bin > ../pg-install.log 2>&1
  cd "$ROOT"; rm -rf run/postgresql-17.11; run/pginst/bin/postgres --version
}
step_pgvector() {
  [ -f run/pginst/share/postgresql/extension/vector.control ] && grep default_version run/pginst/share/postgresql/extension/vector.control && return
  cd run; [ -f pgvector-0.8.0.tar.gz ] || curl -fsSL -o pgvector-0.8.0.tar.gz https://github.com/pgvector/pgvector/archive/refs/tags/v0.8.0.tar.gz
  rm -rf pgvector-0.8.0 && tar xzf pgvector-0.8.0.tar.gz && cd pgvector-0.8.0
  make -s PG_CONFIG="$ROOT/run/pginst/bin/pg_config" > ../pgvector-make.log 2>&1 && make -s install PG_CONFIG="$ROOT/run/pginst/bin/pg_config" >> ../pgvector-make.log 2>&1
  cd "$ROOT"; rm -rf run/pgvector-0.8.0; grep default_version run/pginst/share/postgresql/extension/vector.control
}
step_pginit() { # private cluster: port 5544, unix socket run/pgsock, loopback only. Memory scaled for an 8 GiB host.
  [ -f run/pgdata/PG_VERSION ] || {
    mkdir -p run/pgsock; initdb -D run/pgdata -U bench --auth=trust -E UTF8 --locale=C > run/initdb.log
    cat >> run/pgdata/postgresql.conf <<CONF
port = 5544
listen_addresses = '127.0.0.1'
unix_socket_directories = '$ROOT/run/pgsock'
shared_buffers = 1GB
effective_cache_size = 3GB
maintenance_work_mem = 1536MB
max_parallel_maintenance_workers = 3
max_connections = 200
work_mem = 16MB
jit = off
synchronous_commit = off
CONF
  }
  bench/pg.sh status >/dev/null || bench/pg.sh start
  psql -h "$PGSOCK" -p 5544 -U bench -d postgres -tAc "select 1 from pg_database where datname='vec'" | grep -q 1 || createdb -h "$PGSOCK" -p 5544 -U bench vec
  psql -h "$PGSOCK" -p 5544 -U bench -d vec -tAc "create extension if not exists vector; select version(); select extversion from pg_extension where extname='vector'"
}
for s in ${@:-rust go uv vegeta duckdb pg pgvector pginit}; do echo "== $(date '+%F %T') $s"; step_$s; done
