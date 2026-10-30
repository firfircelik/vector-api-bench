# Versions

## Client availability per language x store (checked October 22, 2026)

| | pgvector | DuckDB VSS | LanceDB |
|---|---|---|---|
| Python | psycopg 3 + `pgvector` (official) | `duckdb` 2.0.0 (official) | `lancedb` 0.40.0 (official) |
| Go | pgx v5 + `pgvector-go` (official pgvector org) | `duckdb-go` v2.20000.0 = DuckDB 2.0.0 (cgo; the Go docs page still lists 1.5.6 as latest stable) | `lancedb-go` (lancedb org, cgo over a prebuilt Rust core; last tag v0.1.2 Sep 2025, then pseudo-versions to May 2026) |
| Rust | tokio-postgres + `pgvector` crate | `duckdb` crate 1.10506.1 = **DuckDB 1.5.6**, no 2.0 crate yet; MSRV 1.88 | `lancedb` crate 0.40.0 (native core); MSRV 1.91 |

Consequences, all open in the protocol:
- DuckDB: one engine version for all three languages means pinning **1.5.6** (Python `duckdb==1.5.6`, `duckdb-go v2.10506.0`, `duckdb` crate `1.10506.x`) until a 2.0 Rust crate exists.
- Rust: a system rustc older than 1.91 is too old for the `duckdb` (1.88) and `lancedb` (1.91) crates. Plan: a user-local rustup toolchain pinned in `rust-toolchain.toml`. No containers.
- LanceDB: Go goes through `lancedb-go`, whose wrapped Lance core version must be recorded; if it lags 0.40.0, build its native library from source against 0.40.0 with cargo.

## Decisions applied for the full study (October 22, 2026, afternoon)

| Component | Version | Notes |
|---|---|---|
| DuckDB, all languages | **1.5.6** | Python `duckdb==1.5.6`; Go `duckdb-go v2.10506.0` (bindings v0.10506.0); Rust `duckdb` crate `=1.10506.1` linked against the official libduckdb v1.5.6 (macOS build: see the macOS section) |
| DuckDB VSS extension | build for v1.5.6, loads as `b833341` | macOS build: see the macOS section |
| Rust toolchain | **1.99.1** via private rustup, pinned in `api/rust/rust-toolchain.toml` | older toolchains are too old (duckdb MSRV 1.88, lancedb MSRV 1.91) |
| LanceDB, Python | lancedb 0.40.0 | |
| LanceDB, Rust | `lancedb` crate `=0.40.0`, `default-features = false, features = ["remote"]` | 0.40.0 does not compile with no features (`job.rs` uses `Error::Http`, gated on `remote`); only local tables are used |
| LanceDB, Go | `lancedb-go` vendored at upstream `fa14ce29` (2026-05-09) in `api/go/third_party/lancedb-go` | upstream wraps lancedb core **0.24.0** (v0.1.2 tag: 0.22.1), so it lagged; its Rust core was rebuilt here against **0.40.0** (arrow 58) |
| LanceDB index | IVF_HNSW_FLAT, num_partitions = 1, M, ef_construction 128 | no plain HNSW in LanceDB; one partition, full-precision vectors = a single HNSW graph |

## macOS / arm64 (Mac mini, long run `full-mac`, October 23, 2026)

Read from the tools on the Mac. Everything lives in `~/vector-api-bench` (`bench/mac_setup.sh`, `bench/macenv.sh`).
No Homebrew packages were installed or changed. Homebrew's protoc 35.0 and the Xcode clang were used as build tools.

| Component | Version | Notes |
|---|---|---|
| OS / HW | macOS 26.5.2 (25F84), Apple M2 4P+4E, 8 GiB, 4 GiB swap file (grows) | `sw_vers`, `sysctl` |
| Python | CPython 3.13.12 (uv-managed, `run/uv-python`) | |
| uv | 0.10.12 (owner's binary, caches redirected to `run/`) | |
| FastAPI / Starlette / Pydantic | 0.159.1 / 1.9.0 / 2.14.1 | from `requirements.lock.txt` |
| uvicorn / uvloop / httptools | 0.54.0 / 0.23.0 / 0.9.0 | `uvicorn[standard]` |
| psycopg / psycopg-pool / pgvector (py) / orjson / psutil / numpy / pyarrow | 3.3.6 / 3.3.3 / 0.5.1 / 3.13.0 / 7.2.2 / 2.5.4 / 26.0.0 | |
| duckdb (Python) | 1.5.6 | VSS `b833341`, `osx_arm64` build from extensions.duckdb.org over https, sha256 `74536af6...c996` |
| duckdb (Rust) | crate `1.10506.1` linked to the official `libduckdb-osx-universal.zip` v1.5.6 (sha256 `e0bc007d...38d6`), rpath via `install_name_tool` | |
| duckdb (Go) | `duckdb-go v2.10506.0` (bindings `darwin-arm64 v0.10506.0`) | |
| lancedb | Python 0.40.0; Rust crate 0.40.0; lancedb-go vendored with its core rebuilt on 0.40.0 | same sources for all three languages |
| Rust | rustc 1.99.1 (58cf8f9c3 2026-10-13), private rustup in `run/rustup` | `api/rust/rust-toolchain.toml` |
| Go | go1.27.3 darwin/arm64 (`GOTOOLCHAIN=go1.27.3`, fetched into `run/gopath`) | the owner's Homebrew Go 1.26.3 is unused |
| PostgreSQL | 17.11, built from source (`--without-icu --without-readline`, Apple clang 21.0.0) | |
| pgvector | 0.8.0, built from source | |
| vegeta | v12.13.0 (`go install`, `run/gopath/bin`) | |
| Dataset | c100k base sha256 `7baf403d...62f4`, c300k `456d06a8...d466` | exact top-100 ground truth by brute force |
