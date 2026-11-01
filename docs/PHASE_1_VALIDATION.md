# Phase 1 verification

Checked on 2026-11-01 using Windows, Python 3.12.14, and PostgreSQL 17.11.

## Automated checks

- `python -m pytest -q`: **16 passed** against isolated PostgreSQL schemas.
- `python -m ruff check .`: passed.
- `python -m ruff format --check .`: passed (20 Python files).
- `python -m pip check`: no broken requirements.
- Compose and GitHub Actions YAML: parsed and checked for expected services/dependencies.

One upstream Starlette warning reports that HTTPX support in TestClient is deprecated
in favor of HTTPX2. Existing correctness tests pass; the application uses HTTPX for
its own outbound shipping client. No warnings were suppressed.

## Running application

Live HTTP requests returned 200 for health, scenario information, product listing,
customer order history, shipping quotes, OpenAPI, and interactive documentation.
Invalid pagination returned 422.

The native launcher was started and stopped for each faulty scenario. The resulting
flags, actual index presence, and shipping-call header matched:

| Scenario | N+1 enabled | Index absent | Repeated shipping enabled | Result |
| --- | --- | --- | --- | --- |
| n-plus-one | yes | no | no | PASS |
| missing-index | no | yes | no | PASS |
| redundant-shipping | no | no | yes | PASS |
| all | yes | yes | yes | PASS |

## Fault reproduction evidence

The reproduction script used the seeded lab database and real outbound HTTP to the
running local shipping stub. It restored the original index state after checking.

| Fault | Healthy observation | Faulty observation | Functional result |
| --- | --- | --- | --- |
| N+1 images | 2 SELECTs for 20 products | 21 SELECTs | Identical product response |
| Missing index | Index Scan using `ix_orders_customer_created` | Sequential Scan and Sort | Identical order response |
| Repeated shipping | 1 HTTP request | 3 HTTP requests | Identical quote response |

The verification report also includes illustrative elapsed times. Those individual
runs are not a controlled performance benchmark and do not establish a general
speedup. Repeatable statistical performance measurement is Phase 2.

## Scope and limitations

- The API is a synthetic localhost lab without authentication or real payments.
- There are no agents, model calls, or optimization claims in Phase 1.
- Docker is not installed on this machine, so the Docker image/Compose stack was
  not executed. The actual application and tests were exercised natively against
  real PostgreSQL. Container runtime validation remains a separate check.
- GitHub Actions is prepared but has not been run on GitHub; nothing was pushed.
- The portable Windows database lives in `.runtime/` and is not a system service.

Generated evidence lives in `var/phase1-verification.json` and
`var/launcher-verification.json`. These are local run artifacts excluded from Git.
