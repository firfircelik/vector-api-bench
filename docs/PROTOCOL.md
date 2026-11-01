# Protocol (repo copy of the protocol page)

Everything here is a **plan** unless it says pilot. Pilot numbers come from `results/pilot/`.

## Question and hypotheses

In a semantic-search HTTP service, is the bottleneck the API layer (language/framework) or the vector store, and
how do they interact?

- **H1 (store dominates at low load).** At or below 25 % of a cell's saturation throughput, the store factor explains
  more variance in log p50 than the API factor. Falsified if the API share is equal or larger.
- **H2 (API matters at high load).** Max sustainable throughput (MST) at the p99 SLO differs by API layer for a fixed
  store, with Python lowest, because the 16 KB JSON body is parsed on the same cores the store uses.
  Falsified if the API ratios' 95 % CIs include 1 for every store.
- **H3 (interaction).** The L x S interaction on log p99 and log MST is non-zero: embedded stores (DuckDB, LanceDB)
  amplify language differences, the out-of-process store (pgvector) damps them. Rank reversal (best store changes
  with language) is tested, not assumed.
- **H4 (serialization).** Binary request bodies cut server CPU per request more for Python than for Go and Rust.

Holm correction across H1 to H4; everything else is descriptive.

## Center point and factors

Center: Cohere 1M (300k if the 1M memory gate fails), recall@10 target 0.95, HNSW M = 16, ef_construction = 128,
k = 10, response = ids + scores, JSON request, warm cache, unix socket for pgvector.

| Factor | Levels | Crossed with all 9 cells? |
|---|---|---|
| API layer L | Python FastAPI, Go net/http, Rust axum | core |
| Store S | pgvector, DuckDB VSS, LanceDB | core |
| Offered load | doubling ladder 100 to 25,600 rps, stop at saturation, plus bisection for MST | yes |
| Scale N | 100k, 300k (seeded subset), 1M | yes |
| Recall target R | 0.90, 0.95, 0.99 (knob tuned per store on 200 calibration queries) | yes |
| HNSW M | 16, 32 | yes, at R = 0.95 |
| top-k | 10, 100 | yes |
| Response payload | ids + scores, ids + scores + ~1 KB text per hit | yes |
| Request encoding | JSON, raw f32 | yes (ablation A4) |
| Cache state | warm, cold (page cache dropped, fresh process) | yes (A5) |

Secondary factors are varied one at a time around the center point, always across all 9 cells, so each secondary
factor's two-way interaction with L and with S is estimable. Higher-order interactions among secondary factors
are not estimated; one confirmatory corner (1M, R 0.99, k 100, text payload) checks the biggest one.

## Ablations

- **A1 API floor:** stub store (returns k fixed ids) per language, JSON and binary, k 10 and k 100 + text.
- **A2 Store floor:** direct client calls, no HTTP, with each language's native client, sequential and 6-way concurrent.
- **A3 Topology:** pgvector unix socket vs TCP loopback; DuckDB and LanceDB in-process vs behind a thin Rust sidecar.
- **A4 Serialization:** JSON vs raw f32 request bodies for all 9 cells.
- **A5 Cold vs warm:** drop page cache, fresh process, first 60 s at 25 % load recorded separately.
- **A6 Python process model:** 6 uvicorn workers vs 1 worker + thread pool, per store.
- **A/A control:** one fixed cell repeated in every block to estimate run-to-run noise.

## Run order and isolation

- Blocks: one block = one replicate of every cell in an experiment. Cell order inside a block follows rows of a
  Williams design for 9 treatments (balanced for first-order carryover), drawn at random per block, seed recorded.
- Fresh server process per cell and rep; 30 s warmup at the lowest step, discarded; step order shuffled per rep.
- CPU pinning: the base design pinned server and Postgres to cores 0-5 and vegeta to 6-7. On the Mac there is no
  affinity API, so nothing is pinned (deviation 1 below). Loadgen CPU is recorded per step.
- Gate before every step: available memory, load average, CPU busy and steal recorded; runs only start on an idle host.

## Statistics

- Replication unit = one fresh-process run. Requests inside a run are not replicates.
- Per run: p50/p95/p99/p99.9 over all requests of the step; recall@10 from every response body.
- Across runs: median, 95 % CI by hierarchical bootstrap (runs, then requests inside runs), B = 10,000, fixed seed.
- Model: log y = mu + L + S + (L x S) + block + e, tested by permutation within blocks (10,000), partial omega squared
  per term, aligned rank transform ANOVA as robustness check.
- Pareto: per cell, p99 at fixed load vs recall@10 over the knob sweep; cells compared at matched recall by
  interpolating along each front at 0.90 / 0.95 / 0.99, on 800 held-out queries.

See README for layout and VERSIONS.md for pins and the client matrix.

## Long run on the Mac mini (tag `full-mac`, from October 23, 2026)

The study's numbers come from one host, the Mac mini.

Host: Mac mini, Apple M2 (4 performance + 4 efficiency cores), 8 GiB RAM, macOS 26.5.2, on AC power, kept awake by
`caffeinate -dimsu` for the life of the run. Started by the user LaunchAgent `com.firat.vectorapibench`
(`bench/mac_agent.sh`, `bench/mac_longrun.sh`), restarted by launchd only after a crash; the runner resumes.

### Deviations from the base protocol (1 to 7 decided before any full-mac data; 8 documented afterwards)

1. **No CPU pinning.** macOS has no CPU affinity API on Apple silicon, so `taskset` has no equivalent. Servers,
   Postgres, sidecars and vegeta share all 8 cores, and P/E core placement is left to the macOS scheduler.
   Consequences: loadgen CPU and server CPU compete. Both are still recorded per step (`loadgen_cpu_util_cores`,
   `server_cpu_util_cores`). A step whose loadgen uses more than 1.5 cores is flagged in the analysis. Records carry
   `sut_cpus = loadgen_cpus = "all (unpinned)"`. No process QoS is set, because QoS classes only steer work between
   P and E cores and pinning everything to one class would hide the heterogeneity rather than control it.
2. **Memory metric.** PSS does not exist on macOS, and USS needs `task_for_pid` (root). Memory is recorded as
   `phys_footprint` from `proc_pid_rusage` (the "Memory" column in Activity Monitor; it includes compressed and
   swapped dirty pages), sampled every 100 ms: `*_peak_footprint_mib` is the sum over the process tree and
   `*_peak_footprint_maxproc_mib` the largest single process. For Postgres, the sum counts shared_buffers once per
   backend that touched them, so it is an upper bound and the max-process value a lower bound. RSS is kept as well.
3. **Swap instead of OOM.** macOS compresses and swaps instead of killing. Every step records `pre_avail_gib`,
   `pre_swap_used_gib` and `post_swap_used_gib`, and the analysis reports swap growth per cell. The host-stall rule is
   unchanged.
4. **Cold cache (A5).** The page cache can only be dropped with `sudo purge`. The runner tries `sudo -n purge` and
   records `cache_dropped`. Without passwordless sudo it is `false`: A5 then measures a fresh process (and, for
   pgvector, a restarted Postgres) with a possibly warm OS file cache. This is reported as a weaker cold start.
5. **Host gate.** The Mac is a personal machine with the owner's apps open. In smoke-mac and the first full-mac hour, available memory was
   between 1.2 and 2.9 GiB. Gate thresholds are 0.8 GiB (c100k) and 1.0 GiB (c300k), plus 0.3 GiB for builds,
   CPU busy < 25 %, and at most 180 s of waiting before proceeding with `gate_timeout = true`. Gate values are
   recorded per run.
6. **Postgres.** A private PostgreSQL 17.11 built from source into `run/pginst`, pgvector 0.8.0 built from source,
   cluster `run/pgdata`, port 5544, socket `run/pgsock`. Settings are scaled for 8 GiB: shared_buffers 1GB,
   effective_cache_size 3GB, maintenance_work_mem 1536MB, max_parallel_maintenance_workers 3. Everything else is
   as in the base protocol.
7. **Toolchains.** These are private to the repo (`bench/macenv.sh`): rustup and cargo, the Go toolchain, the uv
   Python, vegeta, and the DuckDB extension directory (`HOME=run/home` for benchmark processes). See VERSIONS.md.

8. **Runner settings as actually run (documented on November 1, 2026, after the data, to match the code).** The base
   text above says "30 s warmup at the lowest step; step order shuffled per rep" and a ladder from 100 rps. What
   `bench/runner.py` did in every Mac run (`runner_config.json` of each run): 20 s warm-up at 50 rps, discarded; an
   ascending doubling ladder on the grid 25, 50, 100 ... 25,600 rps with 3 s cool-down, stopping at the first
   saturated step; the centre cell (E2) starts at 25 and adds 3 geometric bisection steps; other cells start at the
   grid step at or below 1/16 of the centre MST of the same language and store, and walk down by halving if that first
   step is already saturated. The permutation tests, bootstrap CIs and omega-squared estimates in "Statistics" were not
   run for the article; it reports a descriptive two-way sum-of-squares decomposition of log MST per configuration.

### Plan reductions (Firat's rule: every reduction justified, in priority order)

1. **1M dropped (Firat's decision, October 23).** The centre is c300k and the scales are c100k and c300k. A 1M DuckDB
   process would need roughly 10 GiB (smoke-mac: 1.2 to 1.45 GiB footprint at 100k, 3.3 GiB at 300k), which is more
   than the machine has. The scale factor now has two levels. Scale effects are estimated over 100k to 300k only,
   and the confirmatory 1M corner is gone.
2. **A6 "multi" for the embedded stores uses 2 uvicorn workers instead of 6.** Each worker loads its own index copy.
   In the pilot, 6 workers on DuckDB took 7 GiB PSS at 100k, which cannot fit in 8 GiB. Smoke-mac at 100k: with
   2 workers, footprint was 2.6 GiB and the cell sustained 400 rps (p99 6.8 ms at 200 rps). With 3 workers, footprint
   was 3.2 GiB, swap grew to 4.1 GiB and the cell saturated at 100 rps (p99 271 ms at 200 rps). The 3-worker run
   measured the pager, not the process model. With 2 workers the comparison is still single process versus
   multi-process with duplicated indexes, but the ratio is 2:1, not 6:1. pgvector A6 multi keeps 6 workers, because
   that is its normal CORE model and it is cheap (about 340 MiB).
3. Everything else is kept. That covers CORE (2 scales x 3 recalls x 2 k x 2 payloads, 5 reps, Williams order),
   E0 to E2, E3b, A1 to A5 and AA. Total: **1,543 runs** (E1 21, E0 18, E2 45, CORE 1,035, A1 60, A2 162, A3 27,
   A4 27, A5 45, A6 18, E3b 27, AA 58). Seed 20261022.
