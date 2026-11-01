"""Small, explicit statistics helpers; percentile uses the nearest-rank rule."""

import math
from statistics import mean, median


def percentile(values: list[float], percent: float) -> float | None:
    if not 0 < percent <= 100:
        raise ValueError("Percentile must be in (0, 100].")
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(percent / 100 * len(ordered)) - 1)]


def summarize(samples: list[dict], duration_s: float) -> dict:
    if duration_s <= 0:
        raise ValueError("A measurement window must have positive duration.")
    successful = [sample for sample in samples if sample["success"]]
    latencies = [sample["response_ms"] for sample in successful]
    failures = len(samples) - len(successful)
    return {
        "duration_s": duration_s,
        "requests": len(samples),
        "successful_requests": len(successful),
        "failures": failures,
        "error_rate": failures / len(samples) if samples else None,
        "completed_rps": len(samples) / duration_s,
        "successful_rps": len(successful) / duration_s,
        "latency_ms": {
            "mean": mean(latencies) if latencies else None,
            "p50": percentile(latencies, 50),
            "p95": percentile(latencies, 95),
            "p99": percentile(latencies, 99),
        },
        "mean_sql_count": mean(row["sql_count"] for row in successful) if successful else None,
        "mean_sql_ms": mean(row["sql_ms"] for row in successful) if successful else None,
        "mean_shipping_calls": mean(row["shipping_calls"] for row in successful)
        if successful
        else None,
        "mean_shipping_ms": mean(row["shipping_ms"] for row in successful) if successful else None,
    }


def compare_pairs(trials: list[dict]) -> dict:
    """Summarize matched trial pairs, not a pooled or averaged percentile."""
    pairs = {}
    for trial in trials:
        key = (trial["workload"], trial["repeat"])
        if trial["variant"] in pairs.get(key, {}):
            return {"valid": False, "reason": "Duplicate variant in a matched pair."}
        pairs.setdefault(key, {})[trial["variant"]] = trial
    changes = []
    healthy_p95 = []
    faulty_p95 = []
    for pair in pairs.values():
        if set(pair) != {"healthy", "faulty"}:
            return {"valid": False, "reason": "A matched pair is missing."}
        for trial in pair.values():
            summary = trial["summary"]
            if (
                not trial["valid"]
                or summary["successful_requests"] == 0
                or summary["failures"]
                or summary["latency_ms"]["p95"] is None
            ):
                return {
                    "valid": False,
                    "reason": "A trial failed its measurement/correctness checks.",
                }
        healthy = pair["healthy"]
        faulty = pair["faulty"]
        if healthy["response_sha256"] != faulty["response_sha256"]:
            return {"valid": False, "reason": "Healthy and faulty sentinel responses differ."}
        a = healthy["summary"]["latency_ms"]["p95"]
        b = faulty["summary"]["latency_ms"]["p95"]
        if a <= 0:
            return {"valid": False, "reason": "Nonpositive healthy p95."}
        healthy_p95.append(a)
        faulty_p95.append(b)
        changes.append((b / a - 1) * 100)
    if not changes:
        return {"valid": False, "reason": "No matched trials."}
    return {
        "valid": True,
        "pair_count": len(changes),
        "healthy_median_trial_p95_ms": median(healthy_p95),
        "faulty_median_trial_p95_ms": median(faulty_p95),
        "median_paired_p95_change_percent": median(changes),
        "paired_p95_change_range_percent": [min(changes), max(changes)],
        "all_pairs_faulty_slower": all(change > 0 for change in changes),
        "note": "Descriptive matched-run results, not a statistical significance claim.",
    }
