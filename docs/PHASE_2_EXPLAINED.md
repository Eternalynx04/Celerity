# Celerity Phase 2: measuring before asking an agent to optimize

## 1. What changed

Phase 1 gave us a shopping backend with three controllable performance problems.
Phase 2 adds the measuring instruments: we can send repeated requests, see where
time went, and compare the same workload with and without a fault.

This is the evidence layer for the Phase 3 investigator agent. There are still no
model calls or automatically generated fixes. We are building the tools the agent
will use, and proving that their measurements mean what they say.

## 2. The new pieces, in simple terms

| Piece | Job |
| --- | --- |
| Locust | Pretends to be several users repeatedly reading the API |
| Benchmark runner | Prepares the data, starts processes, runs trials, and cleans up |
| OpenTelemetry | Records connected steps in a request's journey |
| JSONL trace files | Preserve those steps as one JSON object per line |
| Jaeger | Displays the steps as an interactive timeline |
| Report generator | Turns raw request measurements into trial summaries and comparisons |

## 3. Try tracing first

Open PowerShell in the project folder. PostgreSQL must be running:

```powershell
cd C:\Users\sumit\Desktop\Celerity
.\.venv\Scripts\python.exe scripts/bootstrap_postgres.py
.\.venv\Scripts\python.exe scripts/jaeger.py start
```

The first Jaeger start downloads its official Windows binary into `.runtime/`,
checks it against the publisher's binary checksum, and starts it on localhost.
It does not install a system service. The helper remembers only the process it owns.

Stop an existing lab before restarting it with tracing export enabled:

```powershell
.\.venv\Scripts\python.exe scripts/dev.py --stop
.\.venv\Scripts\python.exe scripts/dev.py --scenario healthy --jaeger
```

The second command stays open while the lab runs. In another terminal:

```powershell
$response = Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:8000/api/checkout/quote'
$response.Headers['X-Celerity-Trace-Id']
```

Open <http://127.0.0.1:16686>. Select `celerity-api` and find recent traces. You
can also look up the returned trace ID. For shipping, the timeline includes:

```text
Shopping API receives request             [server span]
  Shopping API asks shipping for a quote  [client span]
    Shipping service handles the quote   [server span]
```

A **trace** is the whole journey of one request. A **span** is one timed step in
that journey. The **trace ID** connects those steps across processes. Each span
also has its own ID and a parent ID, which lets a viewer rebuild the tree.

For product listing, the request span has SQL child spans. Healthy mode should
show two queries; N+1 mode should show twenty-one for a twenty-product page.

Jaeger stores a bounded number of traces in memory. Stopping it clears its UI;
the JSONL evidence in `var/traces/` remains. To stop only Jaeger:

```powershell
.\.venv\Scripts\python.exe scripts/jaeger.py stop
```

## 4. What the response headers mean

HTTP headers are extra information sent alongside a response body. Instrumented
API responses include these headers:

| Header | Meaning |
| --- | --- |
| `X-Celerity-Trace-Id` | The identifier for this request's trace |
| `X-Celerity-Sql-Count` | How many SQL statements this request executed |
| `X-Celerity-Sql-Ms` | Time spent executing those database calls, in milliseconds |
| `X-Celerity-Shipping-Calls` | How many downstream shipping requests it made |
| `X-Celerity-Shipping-Ms` | Time spent in those downstream calls |

The SQL timing is specifically cursor execution time. It is not all ORM work,
connection waiting, serialization, or total database processing. End-to-end
request latency is measured separately by the load generator.

The counts are collected from actual execution, rather than guessed from the
fault flags. Request context follows the API's worker threads, so concurrent
requests keep separate counts. A failed SQL or shipping operation still gets
an error span, and the next request starts with a fresh context.

## 5. Run your first comparison

Keep the normal lab running if you want; the benchmark uses different ports.
In another terminal, run:

```powershell
.\.venv\Scripts\python.exe scripts/benchmark.py --workload catalogue
```

Default settings:

- Five simulated users.
- Each user waits 0.05 seconds after a request before starting the next.
- Five seconds of warm-up after all users have started.
- Twenty seconds of actual measurement.
- Three matched repeats of healthy and faulty: six trials total.

The runner prints progress and the final report path. To compare all faults:

```powershell
.\.venv\Scripts\python.exe scripts/benchmark.py --workload all
```

This runs eighteen trials. Available workloads are:

| Workload | Fixed read request | Fault compared |
| --- | --- | --- |
| catalogue | First twenty products and images | N+1 queries |
| order-history | Customer 1's latest twenty orders | Missing lookup index |
| shipping | National shipping quote | Repeated downstream requests |

For a shorter pipeline check:

```powershell
.\.venv\Scripts\python.exe scripts/benchmark.py --workload catalogue --duration 2 --warmup 1 --users 2 --repeats 1
```

That checks the machinery. It is too short to support a broad performance claim.

## 6. Why we warm up

The first requests can open connections, load modules, and bring data into caches.
We let those startup effects settle before measuring. A trial waits until all
requested users have spawned, warms up, then starts a separate measurement window.

Only requests that **start and finish inside that window** enter our authoritative
sample file. A request that starts during warm-up is excluded even if it finishes
after measurement begins. A request still running when measurement ends is also
excluded. The denominator for throughput is the actual elapsed window length.

Locust's own CSV remains available as a secondary diagnostic. Because its internal
stats can include boundary-crossing requests, comparisons use our `samples.csv`
and `summary.json`, not that secondary CSV.

## 7. How the comparison stays controlled

The runner creates a private PostgreSQL schema and seeds it with the same generated
data for both variants. A schema is a separate namespace for tables inside the
database. The normal shopping lab's tables and index are not changed.

The benchmark uses ports 8020 and 8021 by default. If those ports are occupied, it
stops with a message instead of killing another process. Both API and shipping
processes restart for each trial. Their stop files request graceful shutdown so
pending traces are flushed before evidence is read.

For each repeat, the runner randomly chooses whether healthy or faulty runs first.
It records the random seed and actual order. This helps avoid always favoring the
variant that happens to run after the machine or database has warmed up further.

The runner records:

- Full content fingerprints of all five benchmark tables before and after the suite.
- A fingerprint of the relevant Python source and dependency lock file.
- Python, package, PostgreSQL, OS, CPU-count, and total-memory information.
- User count, request pacing, warm-up, duration, repeats, and tracing settings.
- Exact sentinel-response hashes before/after a trial and across each matched pair.

It removes its private schema when finished, including on handled failures.
Do not edit source code or run other intensive work during measurements. All
processes still share one machine and PostgreSQL instance; data isolation does
not mean hardware isolation.

## 8. Reading the numbers

**Latency:** how long a request takes to finish, measured in milliseconds.

**p50:** the middle request time after sorting successful requests by duration.
Our nearest-rank rule chooses an observed sample rather than interpolating.

**p95:** with one hundred successful requests sorted fastest to slowest, the
ninety-fifth request's time. It helps show slower experiences hidden by the average.

**p99:** the same idea near the slowest end. Small samples make this number coarse.

**Successful RPS:** successful completed requests divided by measured seconds.
Our users wait after every request, so this is the throughput of the chosen
closed-loop workload, not the application's maximum capacity.

**Error rate:** failed completed requests divided by all completed requests. A
response can fail even with HTTP 200 if its data violates the workload contract.

**SQL/request and shipping/request:** average observed work for successful requests.
These can reveal mechanisms even when shared-machine timing is noisy.

The comparison first computes each trial's p95. It then compares healthy/faulty
p95 values within each pair and reports the median of those paired changes. It
does not pool the requests or pretend an average of percentiles is a global p95.
The report also shows variation and whether every pair was slower in faulty mode.

## 9. When a trial is invalid

A valid trial must finish its requested measurement window, retain the requested
users at measurement start, contain at least twenty completed requests, have no
contract failures, keep its sentinel response unchanged, and export API trace
evidence for every successful measured request. Forced service termination also
makes a trial invalid. Changed source or dataset invalidates the suite comparison.

Fewer than one hundred successes adds a warning about coarse tail percentiles.
Passing these checks establishes a usable local experiment, not statistical
significance or a guarantee that a real production workload will behave the same.

## 10. Where the evidence is saved

```text
var/benchmarks/<suite-id>/
  report.md                 readable comparison
  results.json              protocol, trials, comparisons, validity
  catalogue-1-healthy/
    samples.csv             one row per measured completed request
    summary.json            explicit-window statistics
    locust_stats.csv        secondary Locust statistics
    locust.log              load-generator output
    api.log / shipping.log  service output
    traces/*.jsonl          request, SQL, and shipping spans
```

Every measured sample has a trace ID. That is how the future investigator can
connect a slow request to its database and downstream work. JSON is useful for
programmatic tools; the Markdown report is useful for a person reviewing results.

Generated evidence stays out of Git through the existing `var/` ignore rule.
A small documented example can be committed separately as demonstration evidence.

## 11. How instrumentation is implemented

The app owns an OpenTelemetry tracer provider. Middleware starts a server span
around an API request and creates request-local counters. SQLAlchemy execution
events create SQL child spans and update those counters. The shipping gateway
creates an HTTP client span and injects W3C `traceparent` into its outbound request.
The shipping service extracts that context and records its own child server span.

This uses the OpenTelemetry SDK directly, with explicit instrumentation for our
small app. It does not globally monkey-patch libraries or replace the process-wide
tracer provider. That makes separate app instances and test engines easier to isolate.

The default exporter batches JSONL writes. Optional OTLP/HTTP export sends the
same spans to Jaeger. Full sampling is enabled in both benchmark variants, so its
overhead is consistently included. It does not estimate uninstrumented latency.

Bodies, authorization headers, and bound SQL parameter values are not exported.
The recorded SQL uses placeholders; error records use error types and generic
descriptions instead of raw exception messages. Keep the entire lab on localhost.

## 12. Code reading order

1. `src/celerity/benchmark/workloads.py`: which requests are sent and what is checked.
2. `src/celerity/benchmark/statistics.py`: how raw samples become measurements.
3. `benchmarks/locustfile.py`: users, warm-up, window boundaries, and sample capture.
4. `src/celerity/benchmark/runner.py`: isolated data, processes, trial order, cleanup.
5. `src/celerity/telemetry.py`: spans, context, headers, and exporters.
6. `src/celerity/serve.py`: graceful native shutdown.
7. `tests/test_benchmark.py` and `tests/test_telemetry.py`: checks for bad evidence and failures.

## 13. Exercises

1. Find a product request in Jaeger and count its SQL spans.
2. Switch the normal lab to N+1 mode and repeat the inspection.
3. Explain why two responses can be identical while one requires twenty-one queries.
4. Open a `samples.csv` and match one trace ID to its JSONL spans.
5. Explain why a faster HTTP 500 response should not count as an optimization.
6. Read a paired comparison whose repeats disagree and describe it without overclaiming.
7. Explain what would change if we disabled tracing or increased the user count.

## 14. Current boundaries and next phase

Phase 2 supplies measurement and evidence. Phase 3 will give an investigator agent
controlled tools to run experiments, inspect traces, examine queries, and justify
a finding. Automatic patching comes later, followed by the adaptive agent team.
The Celerity product frontend will use the approved Synapse visual direction in
its planned frontend phase. Jaeger is a separate diagnostic viewer.

No Git commit, tag, or remote push is needed to run Phase 2 locally. Publish this
phase when you are ready, after reviewing the changes and checks.

## References

- [Locust headless execution](https://docs.locust.io/en/stable/running-without-web-ui.html)
- [OpenTelemetry Python instrumentation](https://opentelemetry.io/docs/languages/python/instrumentation/)
- [Jaeger downloads and checksums](https://www.jaegertracing.io/download/)
- [Jaeger v2 configuration](https://www.jaegertracing.io/docs/2.22/deployment/configuration/)
