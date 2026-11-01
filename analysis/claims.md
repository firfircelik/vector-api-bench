# Claim -> source map for the article

Every number in the article, the LinkedIn teaser and the charts, with the file or key it comes from.

| # | Claim in the article / teaser / charts | Value | Source (repo-relative) | Check |
|---|---|---|---|---|
| 1 | Runs in the plan | 1,543 (E1 21, E0 18, E2 45, CORE 1,035, A1 60, A2 162, A3 27, A4 27, A5 45, A6 18, E3b 27, AA 58) | `bench/plan.py --centre c300k --tag full-mac --scales c100k,c300k --a6-workers 2`; `docs/PROTOCOL.md` plan reductions; `data/analysis/runs_cells.csv` holds 1,342 HTTP runs (= 1,543 minus E1, E0, A2) | recomputed |
| 2 | Main run 88 h | 2026-10-23 12:02 to 2026-10-27 04:20 TR | `data/analysis/runs_cells.csv` run_started range plus last run; host timeline `data/analysis/host_timeline_hourly.csv` | consistent |
| 3 | 24 configs, 69 CORE cells, 9 cells x 5 reps | 23 + centre | `analysis/out/article_numbers.json` [core_configs, core_cells] | recomputed |
| 4 | Seed 20261022, Williams order | | `docs/PROTOCOL.md`, `bench/plan.py` | read |
| 5 | Step 30 s, warm-up 20 s at 50 rps, cool-down 3 s, 10 s client timeout, ladder grid from 25, 3 bisection steps, SLO p99 100 ms | | `data/results/full-mac-rerun/runner_config.json` (same values in main, pgscan, pgrep5 configs); `bench/runner.py` ladder logic | read |
| 6 | Non-centre ladder starts at grid step <= 1/16 of centre MST, walks down if first step fails; stops at first failing step | | `bench/runner.py` (doubling-ladder block) | read |
| 7 | MST definition: sent >= 98%, errors <= 0.1%, p99 <= 100 ms | | `bench/runner.py` saturated(); `analysis/article_numbers.py` docstring | read |
| 8 | Same HNSW build params all stores, M 16, ef_construction 128, cosine; LanceDB IVF_HNSW_FLAT 1 partition | | `stores/build.py`; `bench/runner.py` (`--efc 128`); CORE plan rows all m=16 | read |
| 9 | Python 6 workers for pgvector, 1 for embedded; pools 24 (DuckDB 6 threads) | | `bench/runner.py` process_model(), store_env() | read |
| 10 | Python handler uses orjson, no Pydantic model | | `api/python/app.py` | read |
| 11 | A6: Python MST same ladder step both arms (pgvector 3,200; LanceDB 1,600; DuckDB 400), 100k R0.95 k10 ids, 3 runs each | | `data/analysis/runs_cells.csv` exp == A6 (one DuckDB single run excluded; remaining 2 runs both 400) | recomputed |
| 12 | shared_buffers 1 GB, effective_cache_size 3 GB | | `docs/PROTOCOL.md` deviation 6 | read |
| 13 | Host: M2 4P+4E, 8 GiB, macOS 26.5.2, kern.num_taskthreads 2048 | | `hardware.txt` | read |
| 14 | Calibration on 200 queries | n_calib 200 | `runner_config.json` | read |
| 15 | Held-out recall within -0.011 / +0.044 of target, 36 combinations | min -0.0112 (pgvector c100k k10 0.90), max +0.0443 (pgvector c100k k100 0.90) | `data/analysis/recall_by_store_target.csv` | recomputed |
| 16 | Median recall deviation per store: DuckDB +0.002, pgvector +0.004, LanceDB +0.013; LanceDB 0.971 at 0.95 (c300k k10) | | same | recomputed |
| 17 | pgvector c100k k10 target 0.90: ef 16, calib 0.901, held-out 0.889; k100 grid starts at ef 100 | | `data/analysis/calibration_E0_knobs.csv`; `runner_config.json` ef_grid | recomputed |
| 18 | Variance shares store 69%, language 5%, interaction 7%, residual 12% (medians over 24 configs) | 0.692 / 0.053 / 0.072 / 0.117 | `analysis/out/article_numbers.json` [varshare_*]; method `analysis/article_numbers.py` anova() | recomputed |
| 19 | Store > language in 23 of 24; exception c100k R0.99 k10 ids is a 50/50 tie | | `analysis/out/variance_shares_final.csv` | recomputed |
| 20 | Store share range 33% to 100%; language 0% to 50%; 22 of 24 configs language <= 14% | 0.326 to 1.000; 0.000 to 0.500 | same | recomputed |
| 21 | p50 at ~25% of MST: store 79%, language 5% (main run) | 0.789 / 0.048 | `data/analysis/numbers.json` [varshare_median_p50lo_*] | read |
| 22 | Best/worst store per language median 4x/2x/4x (Py/Go/Rust); best/worst language per store 2x/2x/1x (DuckDB/pgvector/LanceDB) | | `article_numbers.json` [median_best_over_worst_*] | recomputed |
| 23 | Stub floor >= 12,800 rps every language (median of 5, JSON, k100+text included) | | `data/analysis/ablation_A1_stub_api_floor.csv` | recomputed |
| 24 | No real-store run above 6,400 | max 6,400 | `runs_cells.csv` (non-A1), `pgrep5_runs.csv`, `pgscan_vs_full_mac.csv`, `rerun_centre_runs.csv`, `ladder_start_test_runs.csv` | recomputed |
| 25 | Geo-means LanceDB 1,039 / pgvector 816 / DuckDB 388; c100k 1,295 / 1,831 / 544; c300k 817 / 338 / 268 | | `article_numbers.json` [gm_*] | recomputed |
| 26 | Wins incl. ties LanceDB 51, pgvector 48, DuckDB 2; outright 20 / 18 / 0; 31 tied | | `article_numbers.json` [wins_*, tied_cells] | recomputed |
| 27 | c100k k10: pgvector 18/18, 2,743 vs 1,796; c300k k100: LanceDB 18/18, 544 vs 224 vs 121; c300k k10: 1,330 vs 1,158 | | `article_numbers.json` [wins_incl_ties_by_ds_k, gm_by_ds_k] | recomputed |
| 28 | Max 6,400 at c100k R0.90 k10 (ids and doc) pgvector Go/Rust; Python 3,200 | | `analysis/out/core_final_mst_median.csv`; fig5 | recomputed |
| 29 | DuckDB two shared wins are 400 rps ties at c300k k100 | Go c300k R0.95 k100 doc; Rust c300k R0.9 k100 doc | fig5, `core_final_mst_median.csv` | recomputed |
| 30 | LanceDB lost 37% 100k->300k, pgvector 82% | 817/1295, 338/1831 | from row 25 | arithmetic |
| 31 | pgvector c300k index 1.23 GB | 1,228,775,424 B | `data/analysis/builds_E1.csv` | read |
| 32 | DuckDB store floor Go 844 / Rust 615 / Python 573 qps; CPU 7.0 / 11.5 / 11.6 ms (6 concurrent, c300k R0.95) | | `data/analysis/ablation_A2_store_floor.csv` | read |
| 33 | Centre CPU per request at 400 rps, DuckDB: Go 3.7, Rust 5.6, Python 6.6 ms | | `data/analysis/centre_E2_cells.csv` [cpu_ms_per_req_400] | read |
| 34 | LanceDB direct floor about 2,500 to 2,800 qps (6 concurrent) | 2,478 to 2,768 | `ablation_A2_store_floor.csv` | read |
| 35 | Go/LanceDB rerun: 5/5 crash in the 3,200 step, MST 1,600 all 5 | | `data/analysis/rerun_centre_runs.csv`; `analysis/out/rerun_server_threads_by_step.csv` (error_rate 0.99+ at 3,200) | recomputed |
| 36 | Threads 216 to 328 through 1,600; 1,133 / 1,988 / 2,048 / 2,048 at 3,200 in 4 runs, fifth not sampled (crash dump max m 1,501) | | `rerun_server_threads_by_step.csv`; `article_numbers.json` [rerun_go_lancedb_crash_dumps] | recomputed |
| 37 | p99 ~2 ms at 1,600 (2.14 to 2.27), 0 errors | | `rerun_server_threads_by_step.csv` | recomputed |
| 38 | 5 x pthread_create EAGAIN + SIGABRT; complete dumps 1,517 to 1,765 goroutines, all but one in syscall in `_Cfunc_simple_lancedb_table_select_query_ipc`; max m 1,501 to 1,766 | 6 EAGAIN lines, 5 dumps | `article_numbers.json` (computed from the full server.log; `logs/` has markers and an excerpt) | recomputed with full log |
| 39 | Bisection 2,262 / 1,902 / 1,744 at 100% errors | | `rerun_server_threads_by_step.csv` | recomputed |
| 40 | 86% of 117 Go/LanceDB runs > 90% errors at first saturated step; 0% elsewhere | 0.863 | `data/analysis/failure_mode_at_saturation.csv` | read |
| 41 | Rust/LanceDB centre 2,466 | | `data/analysis/centre_E2_cells.csv` | read |
| 42 | GOMAXPROCS quote; debug.SetMaxThreads default 10,000 | | https://pkg.go.dev/runtime , https://pkg.go.dev/runtime/debug#SetMaxThreads | external doc |
| 43 | Go calls lancedb-go synchronous Execute; Python/Rust use async LanceDB API | | `api/go/main.go` (VectorQuery ... Execute), `api/python/app.py` (connect_async), `api/rust/src/main.rs` | read |
| 44 | Centre pgvector first run medians Rust 518, Go 366, Python 616 (reps 435 400 2690 518 672 / 366 153 1037 128 366 / 475 475 2262 616 616) | | `data/analysis/centre_E2_cells.csv`; `article_numbers.json` [centre_pgvector_old_median] | recomputed |
| 45 | Rerun medians Rust 2,933, Go 2,690, Python 2,466 (4 runs, rep 0 = 258 excluded); ratios 5.7 / 7.3 / 4.0 | 5.66 / 7.35 / 4.0 | `rerun_centre_runs.csv`; `article_numbers.json` [centre_final, centre_pgvector_ratio_new_over_old] | recomputed |
| 46 | Rerun runs started 18:46 to 20:50 TR Oct 27; first run swap 2.588 GiB, others <= 0.335 GiB | | `rerun_centre_runs.csv`; `article_numbers.json` [rerun_python_rep0, rerun_other_max_pre_swap_gib] | recomputed |
| 47 | First-run E2 dates Oct 23 | 12:02 to 17:08 | `runs_cells.csv` exp == E2 | recomputed |
| 48 | r = -0.80 (n 45) pgvector c300k; DuckDB -0.14; LanceDB -0.35 | -0.797 / -0.144 / -0.345 | `data/analysis/numbers.json` [corr_p50_100rps_avail_*] | read |
| 49 | A/A 58 runs Rust/pgvector c300k R0.95 k10 ids, Oct 23 to Oct 27; fast 26 (p50 1.89 ms at 388 rps), slow 32 (8.59 ms) | | `numbers.json` [AA_*]; `runs_cells.csv` exp == AA | recomputed |
| 50 | Available memory median 2.73 GiB, min 0.72; swap peak 10.92 GiB | | `numbers.json`; `article_numbers.json` [fullmac_avail_*, fullmac_swap_max_gib] | recomputed |
| 51 | Go/LanceDB control p50 at 100 rps 2.6 vs 3.9 ms | 2.60 / 3.94 | `article_numbers.json` [rerun_go_lancedb, fullmac_go_lancedb_p50_p99_100] | recomputed |
| 52 | pgscan: 21 of 69 moved > 25%, 20 at c300k | | `data/analysis/pgscan_vs_full_mac.csv`; `article_numbers.json` [pgscan_*] | recomputed |
| 53 | pgvector geo-mean 1,039 -> 816 | | `article_numbers.json` [gm_pgvector_old_fullmac, gm_pgvector] | recomputed |
| 54 | c300k k100 pgvector final 25 to 400; first run 400 to 800 | | `article_numbers.json` [c300k_k100_pgvector_range]; `data/analysis/ladder_start_test.csv` | recomputed |
| 55 | Ladder test medians 200/100, 200/200, 100/100, 200/400; geo-mean 168 vs 168; all 12 runs 150 vs 168 | | `article_numbers.json` [ladder_test_*]; `ladder_start_test_runs.csv` | recomputed |
| 56 | pgrep5 10 of 105 flagged, geo-mean 823; corrected 14 flagged, 805 | | `article_numbers.json` [pgrep5_contaminated_runs, gm_pgvector_excl_contaminated]; `analysis/out/pgrep5_contamination_corrected.csv` | read (needs raw JSONL to recompute) |
| 57 | Outliers 63, excluded 45 (CORE 24, E2 2, ...) | | `article_numbers.json` [fullmac_outliers, fullmac_excluded_by_exp] | recomputed |
| 58 | Exclusions change 3 of 216 cell medians | | `numbers.json` [sens_*] | read |
| 59 | Invalid steps 190 of 7,859, all overload drains, 0 host stalls | | `numbers.json` [steps_*] | read |
| 60 | Load generator > 1.5 cores only in 48 A1 steps at 25,600 rps; none in real-store or rerun runs | | `runs_cells.csv` [loadgen_hot] by exp; `pgrep5_runs.csv`, `rerun_centre_runs.csv` | recomputed |
| 61 | Cold ablation: cache dropped in 0 of 45 runs | | `numbers.json` [A5_cache_dropped_true] | read |
| 62 | 1M dropped: 1M DuckDB process would need about 10 GiB | | `docs/PROTOCOL.md` plan reduction 1 | read |
| 63 | Versions (FastAPI 0.159.1, uvicorn 0.54.0, CPython 3.13.12, Go 1.27.3, rustc 1.99.1, axum 0.8, pgvector 0.8.0, PostgreSQL 17.11, DuckDB 1.5.6, LanceDB 0.40.0, lancedb-go fa14ce29 / tag v0.1.2) | | `docs/VERSIONS.md`, `requirements.lock.txt`, `api/rust/Cargo.toml`, `api/go/go.mod` | read |
| 64 | Teaser numbers (69%, 5%, 1,831 vs 1,295, 817 vs 338, 12,800, 6,400, 216 to 328, 2,048, 1,600, 3,200, 4 s, 5 of 5, 4 to 7x, 1,543 runs, 88 h) | | rows 1, 2, 18, 23 to 25, 35 to 37, 45 | same sources |
| 65 | Charts fig1 to fig5 | | `analysis/charts.py` from `data/` + `analysis/out/`; regenerated from the repo data, pixel-identical to the earlier draft PNGs before the label edits of 2026-11-01 | regenerated |
