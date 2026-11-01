# Phase 2 verification

Verified on 2026-11-01 using Windows, Python 3.12.14, and PostgreSQL 17.11.

## Code and integration checks

- `pytest -q`: **30 passed**. The original 16 Phase 1 tests remain passing.
- `ruff check .`: passed.
- `ruff format --check .`: passed (34 Python files).
- `pip check`: no broken requirements.
- `git diff --check`: passed under the repository's normal Windows newline settings.
- Compose and GitHub Actions YAML parsed and checked for expected configuration.
- Dependency lock entries parsed, including a Windows-only marker for `pywin32`.

The existing upstream Starlette TestClient/HTTPX deprecation warning remains;
the test suite passes and no warnings were suppressed.

## Native tracing integration

Jaeger 2.22.0 was downloaded from the official release and its executable was
verified against the publisher's binary checksum. The project-local Jaeger
configuration started successfully on loopback interfaces.

A real shipping quote was exported over OTLP/HTTP. Jaeger's query API returned
one correlated trace containing these three spans across two services:

1. Shopping API server span.
2. Shopping API outgoing shipping client span.
3. Shipping service server span.

Both `celerity-api` and `celerity-shipping` appeared in that trace. Local JSONL
export remained enabled independently of Jaeger.

## Repeated benchmark suite

Suite: `20261101T100352Z-1d1094`

- Three workloads, healthy/faulty variants, three matched repeats: **18 trials**.
- Five users, 0.05-second post-request wait, two-second warm-up after spawning,
  and eight-second measurement windows.
- Variant ordering randomized within pairs using seed 42.
- A fresh private PostgreSQL schema with deterministic seed data.
- 100% tracing in both variants; benchmark trials used local JSONL export.
- **6,376 measured completed requests, zero request-contract failures.**
- All 18 trials passed their validity checks; every measured successful request
  had matching API trace evidence.
- Full data fingerprints and relevant source/dependency fingerprints remained unchanged.
- The temporary benchmark schema was removed; the normal lab's index remained present.

| Workload | Healthy median trial p95 | Faulty median trial p95 | Median paired p95 change |
| --- | ---: | ---: | ---: |
| Catalogue / N+1 | 47.01 ms | 126.49 ms | +163.96% |
| Order history / missing index | 47.69 ms | 51.34 ms | +5.26% |
| Shipping / repeated calls | 70.72 ms | 162.10 ms | +133.44% |

Median trial p95 and median paired percent change are different calculations;
the latter is not derived by dividing the two table medians. All three pairs
were slower in faulty mode for each workload in this particular suite.

These are descriptive local warm-cache measurements, with tracing overhead and
fixed request pacing. The small order-history difference is not evidence of a
statistically significant or universal improvement. This phase compares known
variants; it does not demonstrate agent-generated optimization.

## Artifact audit

A separate audit inspected all 6,376 measured sample rows. It checked window
boundaries, trace IDs, request-level SQL/shipping counts, and parent-child links.
The corresponding evidence included **28,687 SQL child spans** and **2,712 shipping
server spans**, with matching outgoing client spans. The current relevant source
fingerprint matched the source measured by the suite.

The small example report is included at `docs/benchmarks/phase2-local-example.md`.
Raw CSVs, JSONL traces, full results, and audit output remain under ignored `var/`.

## Boundaries

- Native execution was tested. Docker is still unavailable, so the container stack
  was not executed; its configuration was checked structurally.
- The updated GitHub Actions workflow includes a benchmark pipeline smoke test,
  but has not been run remotely for these unpushed changes.
- Jaeger's view uses bounded in-memory storage; persistent raw trace evidence is
  the local JSONL output. The viewer resets when Jaeger restarts.
- This phase adds measurement and tracing, not agents or the planned product frontend.
- No Phase 2 commit, tag, or push was made automatically.
