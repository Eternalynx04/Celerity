# Celerity benchmark report

Suite: `20261101T100352Z-1d1094`

Status: **complete**

## Protocol

- Closed-loop users: 5; wait after each request: 0.05 s.
- Warm-up after all users spawn: 2.0 s.
- Measurement window: 8.0 s; matched repeats: 3.
- Each workload reads a fixed endpoint on an isolated, identically seeded schema.
- Healthy/faulty order is randomized within each pair with a recorded seed.
- Each trial restarts both services; all trace sampling is enabled in both variants.
- Only requests starting and completing inside the measurement window are counted.
- Latency percentiles use successful requests and the nearest-rank rule.
- SQL timings cover cursor execution; they are not total database/ORM processing time.
- These are local warm-cache microbenchmarks, not production capacity estimates.

## Trials

| Workload | Repeat | Variant | Requests | Errors | p50 ms | p95 ms | Success RPS | SQL/request | Shipping/request | Valid |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| catalogue | 1 | faulty | 288 | 0 | 77.35 | 126.49 | 35.98 | 21.00 | 0.00 | yes |
| catalogue | 1 | healthy | 411 | 0 | 38.74 | 47.92 | 51.35 | 2.00 | 0.00 | yes |
| catalogue | 2 | faulty | 292 | 0 | 79.69 | 117.48 | 36.49 | 21.00 | 0.00 | yes |
| catalogue | 2 | healthy | 422 | 0 | 37.80 | 47.01 | 52.70 | 2.00 | 0.00 | yes |
| catalogue | 3 | healthy | 456 | 0 | 29.78 | 45.68 | 56.98 | 2.00 | 0.00 | yes |
| catalogue | 3 | faulty | 286 | 0 | 78.22 | 131.71 | 35.72 | 21.00 | 0.00 | yes |
| order-history | 1 | faulty | 425 | 0 | 36.74 | 51.90 | 53.12 | 3.00 | 0.00 | yes |
| order-history | 1 | healthy | 455 | 0 | 28.17 | 47.69 | 56.86 | 3.00 | 0.00 | yes |
| order-history | 2 | faulty | 443 | 0 | 32.35 | 48.51 | 55.30 | 3.00 | 0.00 | yes |
| order-history | 2 | healthy | 462 | 0 | 27.33 | 47.51 | 57.73 | 3.00 | 0.00 | yes |
| order-history | 3 | faulty | 407 | 0 | 39.34 | 51.34 | 50.78 | 3.00 | 0.00 | yes |
| order-history | 3 | healthy | 449 | 0 | 31.49 | 48.78 | 56.12 | 3.00 | 0.00 | yes |
| shipping | 1 | faulty | 191 | 0 | 146.46 | 161.27 | 23.86 | 0.00 | 3.00 | yes |
| shipping | 1 | healthy | 325 | 0 | 66.23 | 72.70 | 40.61 | 0.00 | 1.00 | yes |
| shipping | 2 | faulty | 191 | 0 | 146.48 | 162.10 | 23.83 | 0.00 | 3.00 | yes |
| shipping | 2 | healthy | 350 | 0 | 56.13 | 69.44 | 43.74 | 0.00 | 1.00 | yes |
| shipping | 3 | healthy | 339 | 0 | 60.77 | 70.72 | 42.37 | 0.00 | 1.00 | yes |
| shipping | 3 | faulty | 184 | 0 | 153.29 | 173.04 | 22.97 | 0.00 | 3.00 | yes |

## Matched comparisons

- **catalogue**: median trial p95 healthy 47.01 ms, faulty 126.49 ms. Median paired p95 change 163.96%. All pairs faulty slower: True.
- **order-history**: median trial p95 healthy 47.69 ms, faulty 51.34 ms. Median paired p95 change 5.26%. All pairs faulty slower: True.
- **shipping**: median trial p95 healthy 70.72 ms, faulty 162.10 ms. Median paired p95 change 133.44%. All pairs faulty slower: True.

A positive change means the faulty variant was slower. We report matched-run
differences descriptively, without significance claims or pooled trial percentiles.

## Validity and limitations

Each valid trial completed its full window, kept the requested users, had at least 20
completed requests, had no request-contract errors, preserved its sentinel response,
and exported API trace evidence for every successful measured request.
With fewer than 100 successes, tail percentiles are coarse; inspect trial warnings.
Shared-machine load, warm database caches, tracing overhead, fixed request pacing,
and this limited endpoint set constrain what these measurements establish.

## Artifacts

`results.json` records configuration, source fingerprint, full dataset fingerprints,
and all comparisons. Each trial directory contains authoritative `samples.csv` and
`summary.json`, secondary Locust CSVs/logs, and correlated JSONL traces.
Locust CSV can include boundary requests; use explicit-window files for comparisons.
