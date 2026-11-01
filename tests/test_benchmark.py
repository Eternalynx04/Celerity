import copy
import csv
import json

import pytest

from celerity.benchmark.runner import trace_coverage
from celerity.benchmark.statistics import compare_pairs, percentile, summarize
from celerity.benchmark.workloads import read_measurement_headers, validate_response


def sample(latency, *, success=True):
    return {
        "response_ms": latency,
        "success": success,
        "sql_count": 2,
        "sql_ms": 1.0,
        "shipping_calls": 0,
        "shipping_ms": 0.0,
    }


def trial(variant, p95, *, repeat=1, valid=True):
    summary = summarize([sample(p95)] * 100, 10)
    return {
        "workload": "catalogue",
        "repeat": repeat,
        "variant": variant,
        "valid": valid,
        "summary": summary,
        "response_sha256": "same-response",
    }


def test_percentiles_and_throughput_exclude_failed_latency():
    assert percentile(list(range(1, 101)), 95) == 95
    assert percentile([], 95) is None
    summary = summarize([sample(10), sample(30), sample(1, success=False)], 2)
    assert summary["requests"] == 3
    assert summary["failures"] == 1
    assert summary["error_rate"] == pytest.approx(1 / 3)
    assert summary["successful_rps"] == 1
    assert summary["completed_rps"] == 1.5
    assert summary["latency_ms"]["mean"] == 20
    assert summary["latency_ms"]["p95"] == 30


def test_pairwise_comparison_uses_each_repeat_not_pooled_percentiles():
    result = compare_pairs(
        [
            trial("healthy", 10),
            trial("faulty", 20),
            trial("healthy", 100, repeat=2),
            trial("faulty", 110, repeat=2),
        ]
    )
    assert result["valid"]
    assert result["pair_count"] == 2
    assert result["median_paired_p95_change_percent"] == pytest.approx(55)
    assert result["healthy_median_trial_p95_ms"] == 55


def test_invalid_or_mismatched_trials_are_not_successful_comparisons():
    a, b = trial("healthy", 10), trial("faulty", 20)
    assert not compare_pairs([a])["valid"]
    assert not compare_pairs([a, a, b])["valid"]
    wrong = copy.deepcopy(b)
    wrong["response_sha256"] = "changed-data"
    assert not compare_pairs([a, wrong])["valid"]
    wrong = copy.deepcopy(b)
    wrong["summary"]["failures"] = 1
    assert not compare_pairs([a, wrong])["valid"]
    assert not compare_pairs([a, trial("faulty", 20, valid=False)])["valid"]


def test_response_contract_rejects_fast_but_wrong_results():
    validate_response("shipping", {"zone": "national", "shipping_cents": 500, "currency": "USD"})
    with pytest.raises(ValueError):
        validate_response("shipping", {"zone": "national", "shipping_cents": 0, "currency": "USD"})
    with pytest.raises(ValueError):
        validate_response("catalogue", [])


def test_measurement_headers_require_valid_trace_and_finite_nonnegative_values():
    headers = {
        "X-Celerity-Sql-Count": "2",
        "X-Celerity-Sql-Ms": "1.2",
        "X-Celerity-Shipping-Calls": "0",
        "X-Celerity-Shipping-Ms": "0",
        "X-Celerity-Trace-Id": "1" * 32,
    }
    assert read_measurement_headers(headers)["sql_count"] == 2
    for key, bad in (
        ("X-Celerity-Trace-Id", "0" * 32),
        ("X-Celerity-Sql-Ms", "nan"),
        ("X-Celerity-Sql-Count", "-1"),
    ):
        with pytest.raises(ValueError):
            read_measurement_headers(headers | {key: bad})


def test_trace_coverage_detects_missing_evidence(tmp_path):
    csv_path = tmp_path / "samples.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["trace_id", "success"])
        writer.writeheader()
        writer.writerows([{"trace_id": "a", "success": True}, {"trace_id": "b", "success": True}])
    (tmp_path / "api.jsonl").write_text(
        json.dumps(
            {
                "trace_id": "a",
                "service": "celerity-api",
                "kind": "SERVER",
                "attributes": {"celerity.run_id": "run"},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    result = trace_coverage(tmp_path, csv_path, "run")
    assert result["coverage"] == 0.5
    assert result["matched_api_traces"] == 1
