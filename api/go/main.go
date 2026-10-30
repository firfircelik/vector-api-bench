// Semantic search API, Go implementation (net/http, stdlib router).
// Same contract as api/python/app.py. Env: STORE=stub|pgvector|duckdb|lancedb|remote, PG_DSN, PG_TABLE,
// DUCK_PATH, DUCK_THREADS, LANCE_URI, LANCE_TABLE, REMOTE_URL, EF_SEARCH, POOL_SIZE, DIM.
// `vecapi -bench-store queries.f32 -k 10 -concurrency 1 [-doc]` calls the store directly, no HTTP (ablation A2).
package main

import (
	"bytes"
	"context"
	"database/sql"
	"database/sql/driver"
	"encoding/binary"
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"log"
	"math"
	"net/http"
	"os"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	"github.com/apache/arrow/go/v17/arrow/array"
	duckdb "github.com/duckdb/duckdb-go/v2"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/lancedb/lancedb-go/pkg/contracts"
	"github.com/lancedb/lancedb-go/pkg/lancedb"
	pgv "github.com/pgvector/pgvector-go"
	pgxvec "github.com/pgvector/pgvector-go/pgx"
)

var tStart = time.Now()

type resp struct {
	IDs    []int64   `json:"ids"`
	Scores []float64 `json:"scores"`
	Docs   []string  `json:"docs,omitempty"`
}

type store interface {
	Search(ctx context.Context, v []float32, k int, doc bool) (*resp, error)
}

type stub struct{}

var stubDoc = strings.Repeat("x", 1024)

func (stub) Search(_ context.Context, _ []float32, k int, doc bool) (*resp, error) {
	r := &resp{IDs: make([]int64, k), Scores: make([]float64, k)}
	for i := range r.IDs {
		r.IDs[i] = int64(i)
	}
	if doc {
		r.Docs = make([]string, k)
		for i := range r.Docs {
			r.Docs[i] = stubDoc
		}
	}
	return r, nil
}

func getenv(k, d string) string {
	if v := os.Getenv(k); v != "" {
		return v
	}
	return d
}

// ---- pgvector ----
type pgStore struct {
	pool        *pgxpool.Pool
	sql, sqlDoc string
}

func newPG(ctx context.Context, n int, ef string) (*pgStore, error) {
	cfg, err := pgxpool.ParseConfig(os.Getenv("PG_DSN"))
	if err != nil {
		return nil, err
	}
	cfg.MaxConns, cfg.MinConns = int32(n), int32(n)
	cfg.AfterConnect = func(ctx context.Context, c *pgx.Conn) error {
		if err := pgxvec.RegisterTypes(ctx, c); err != nil {
			return err
		}
		_, err := c.Exec(ctx, "SET hnsw.ef_search = "+ef)
		return err
	}
	p, err := pgxpool.NewWithConfig(ctx, cfg)
	if err != nil {
		return nil, err
	}
	if err := p.Ping(ctx); err != nil {
		return nil, err
	}
	t := os.Getenv("PG_TABLE")
	return &pgStore{p, fmt.Sprintf("SELECT id, emb <=> $1 AS d FROM %s ORDER BY d LIMIT $2", t),
		fmt.Sprintf("SELECT id, emb <=> $1 AS d, doc FROM %s ORDER BY d LIMIT $2", t)}, nil
}

func (s *pgStore) Search(ctx context.Context, v []float32, k int, doc bool) (*resp, error) {
	q := s.sql
	if doc {
		q = s.sqlDoc
	}
	rows, err := s.pool.Query(ctx, q, pgv.NewVector(v), k)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	r := &resp{IDs: make([]int64, 0, k), Scores: make([]float64, 0, k)}
	for rows.Next() {
		var id int64
		var d float64
		var txt string
		if doc {
			err = rows.Scan(&id, &d, &txt)
			r.Docs = append(r.Docs, txt)
		} else {
			err = rows.Scan(&id, &d)
		}
		if err != nil {
			return nil, err
		}
		r.IDs, r.Scores = append(r.IDs, id), append(r.Scores, d)
	}
	return r, rows.Err()
}

// ---- DuckDB VSS ----
type duckStore struct{ db *sql.DB }

func vecLiteral(v []float32) string {
	b := make([]byte, 0, len(v)*12+16)
	b = append(b, '[')
	for i, x := range v {
		if i > 0 {
			b = append(b, ',')
		}
		b = strconv.AppendFloat(b, float64(x), 'g', -1, 32)
	}
	return string(append(b, "]::FLOAT[768]"...))
}

func newDuck(n int, ef string) (*duckStore, error) {
	c, err := duckdb.NewConnector(os.Getenv("DUCK_PATH")+"?access_mode=READ_ONLY&threads="+getenv("DUCK_THREADS", "6"),
		func(ex driver.ExecerContext) error {
			for _, q := range []string{"LOAD vss", "SET hnsw_ef_search = " + ef, "SET late_materialization_max_rows = 0"} {
				if _, err := ex.ExecContext(context.Background(), q, nil); err != nil {
					return err
				}
			}
			return nil
		})
	if err != nil {
		return nil, err
	}
	db := sql.OpenDB(c)
	db.SetMaxOpenConns(n)
	db.SetMaxIdleConns(n)
	s := &duckStore{db}
	_, err = s.Search(context.Background(), make([]float32, 768), 1, false) // load the index before healthy
	return s, err
}

func (s *duckStore) Search(ctx context.Context, v []float32, k int, doc bool) (*resp, error) {
	cols := ""
	if doc {
		cols = ", doc"
	}
	rows, err := s.db.QueryContext(ctx, fmt.Sprintf("SELECT id, array_cosine_distance(emb, %s) AS d%s FROM items ORDER BY d LIMIT %d", vecLiteral(v), cols, k))
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	r := &resp{IDs: make([]int64, 0, k), Scores: make([]float64, 0, k)}
	for rows.Next() {
		var id int64
		var d float32
		var txt string
		if doc {
			err = rows.Scan(&id, &d, &txt)
			r.Docs = append(r.Docs, txt)
		} else {
			err = rows.Scan(&id, &d)
		}
		if err != nil {
			return nil, err
		}
		r.IDs, r.Scores = append(r.IDs, id), append(r.Scores, float64(d))
	}
	return r, rows.Err()
}

// ---- LanceDB (lancedb-go, rebuilt against lancedb 0.40.0, see third_party/lancedb-go/UPSTREAM.txt) ----
type lanceStore struct {
	tbl contracts.ITable
	ef  int
}

func newLance(ef int) (*lanceStore, error) {
	ctx := context.Background()
	conn, err := lancedb.Connect(ctx, os.Getenv("LANCE_URI"), nil)
	if err != nil {
		return nil, err
	}
	t, err := conn.OpenTable(ctx, os.Getenv("LANCE_TABLE"))
	if err != nil {
		return nil, err
	}
	return &lanceStore{t, ef}, nil
}

func (s *lanceStore) Search(ctx context.Context, v []float32, k int, doc bool) (*resp, error) {
	cols := []string{"id", "_distance"}
	if doc {
		cols = []string{"id", "doc", "_distance"}
	}
	rec, err := s.tbl.VectorQuery("emb", v).DistanceType(contracts.DistanceTypeCosine).Limit(k).Nprobes(1).Ef(s.ef).Columns(cols).Execute(ctx)
	if err != nil {
		return nil, err
	}
	defer rec.Release()
	r := &resp{}
	sch := rec.Schema()
	idx := func(name string) int { return sch.FieldIndices(name)[0] }
	ids := rec.Column(idx("id")).(*array.Int64)
	ds := rec.Column(idx("_distance")).(*array.Float32)
	for i := 0; i < int(rec.NumRows()); i++ {
		r.IDs = append(r.IDs, ids.Value(i))
		r.Scores = append(r.Scores, float64(ds.Value(i)))
	}
	if doc {
		dc := rec.Column(idx("doc")).(*array.String)
		for i := 0; i < int(rec.NumRows()); i++ {
			r.Docs = append(r.Docs, dc.Value(i))
		}
	}
	return r, nil
}

// ---- remote (ablation A3: store behind a thin sidecar) ----
type remoteStore struct {
	c   *http.Client
	url string
}

func (s *remoteStore) Search(ctx context.Context, v []float32, k int, doc bool) (*resp, error) {
	b := make([]byte, 4*len(v))
	for i, x := range v {
		binary.LittleEndian.PutUint32(b[4*i:], math.Float32bits(x))
	}
	d := 0
	if doc {
		d = 1
	}
	req, _ := http.NewRequestWithContext(ctx, "POST", fmt.Sprintf("%s/search_bin?k=%d&doc=%d", s.url, k, d), bytes.NewReader(b))
	res, err := s.c.Do(req)
	if err != nil {
		return nil, err
	}
	defer res.Body.Close()
	var r resp
	return &r, json.NewDecoder(res.Body).Decode(&r)
}

func main() {
	port := flag.Int("port", 8080, "port")
	bench := flag.String("bench-store", "", "queries.f32: run the store directly, no HTTP")
	bk := flag.Int("k", 10, "k for -bench-store")
	conc := flag.Int("concurrency", 1, "concurrency for -bench-store")
	bdoc := flag.Bool("doc", false, "fetch docs in -bench-store")
	warm := flag.Int("warmup", 200, "warmup queries for -bench-store")
	flag.Parse()
	dim, _ := strconv.Atoi(getenv("DIM", "768"))
	n, _ := strconv.Atoi(getenv("POOL_SIZE", "4"))
	efs := getenv("EF_SEARCH", "64")
	ef, _ := strconv.Atoi(efs)
	name := getenv("STORE", "stub")
	var st store = stub{}
	var err error
	switch name {
	case "pgvector":
		st, err = newPG(context.Background(), n, efs)
	case "duckdb":
		st, err = newDuck(n, efs)
	case "lancedb":
		st, err = newLance(ef)
	case "remote":
		st = &remoteStore{&http.Client{Transport: &http.Transport{MaxIdleConnsPerHost: n, MaxConnsPerHost: n}}, os.Getenv("REMOTE_URL")}
	}
	if err != nil {
		log.Fatal(err)
	}
	ready := time.Since(tStart).Seconds()
	if *bench != "" {
		raw, err := os.ReadFile(*bench)
		if err != nil {
			log.Fatal(err)
		}
		nq := len(raw) / (4 * dim)
		qs := make([][]float32, nq)
		for i := range qs {
			qs[i] = make([]float32, dim)
			for j := range qs[i] {
				qs[i][j] = math.Float32frombits(binary.LittleEndian.Uint32(raw[4*(i*dim+j):]))
			}
		}
		ctx := context.Background()
		for i := 0; i < *warm; i++ {
			if _, err := st.Search(ctx, qs[i%nq], *bk, *bdoc); err != nil {
				log.Fatal(err)
			}
		}
		lat := make([]float64, nq)
		ids := make([][]int64, nq)
		var next int64 = -1
		var wg sync.WaitGroup
		t0 := time.Now()
		for w := 0; w < *conc; w++ {
			wg.Add(1)
			go func() {
				defer wg.Done()
				for {
					i := int(atomic.AddInt64(&next, 1))
					if i >= nq {
						return
					}
					s := time.Now()
					r, err := st.Search(ctx, qs[i], *bk, *bdoc)
					if err != nil {
						log.Fatal(err)
					}
					lat[i] = float64(time.Since(s).Nanoseconds()) / 1e6
					ids[i] = r.IDs
				}
			}()
		}
		wg.Wait()
		json.NewEncoder(os.Stdout).Encode(map[string]any{"lang": "go", "wall_s": time.Since(t0).Seconds(), "lat_ms": lat, "ids": ids, "ready_s": ready})
		return
	}
	write := func(w http.ResponseWriter, r *resp) {
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(r)
	}
	mux := http.NewServeMux()
	mux.HandleFunc("GET /health", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(map[string]any{"ok": true, "store": name, "ready_s": ready, "lang": "go"})
	})
	mux.HandleFunc("POST /search", func(w http.ResponseWriter, r *http.Request) {
		var q struct {
			Vector []float32 `json:"vector"`
			K      int       `json:"k"`
			Doc    bool      `json:"doc"`
		}
		if err := json.NewDecoder(r.Body).Decode(&q); err != nil || len(q.Vector) != dim {
			http.Error(w, "bad request", 400)
			return
		}
		if q.K == 0 {
			q.K = 10
		}
		res, err := st.Search(r.Context(), q.Vector, q.K, q.Doc)
		if err != nil {
			http.Error(w, err.Error(), 500)
			return
		}
		write(w, res)
	})
	mux.HandleFunc("POST /search_bin", func(w http.ResponseWriter, r *http.Request) {
		b, err := io.ReadAll(r.Body)
		if err != nil || len(b) != 4*dim {
			http.Error(w, "bad request", 400)
			return
		}
		v := make([]float32, dim)
		for i := range v {
			v[i] = math.Float32frombits(binary.LittleEndian.Uint32(b[4*i:]))
		}
		k, _ := strconv.Atoi(r.URL.Query().Get("k"))
		if k == 0 {
			k = 10
		}
		d := r.URL.Query().Get("doc")
		res, err := st.Search(r.Context(), v, k, d == "1" || d == "true")
		if err != nil {
			http.Error(w, err.Error(), 500)
			return
		}
		write(w, res)
	})
	log.Fatal(http.ListenAndServe(fmt.Sprintf("127.0.0.1:%d", *port), mux))
}
