"""Readable comparison report; raw trials remain the source of measurements."""

import json
from pathlib import Path

from celerity.benchmark.statistics import compare_pairs


def number(value) -> str:
    return f"{value:.2f}" if isinstance(value, (int, float)) else "n/a"


def write_report(folder: Path, metadata: dict, trials: list[dict]) -> dict:
    comparisons = {}
    for workload in sorted({trial["workload"] for trial in trials}):
        comparisons[workload] = compare_pairs(
            [trial for trial in trials if trial["workload"] == workload]
        )
    if metadata.get("dataset_unchanged") is False or metadata.get("source_unchanged") is False:
        comparisons = {
            name: {"valid": False, "reason": "Source code or dataset changed during the suite."}
            for name in comparisons
        }
    result = {"protocol": metadata, "trials": trials, "comparisons": comparisons}
    (folder / "results.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    lines = [
        "# Celerity benchmark report",
        "",
        f"Suite: `{metadata['suite_id']}`",
        "",
        f"Status: **{metadata['status']}**",
        "",
        "## Protocol",
        "",
        f"- Closed-loop users: {metadata['users']}; "
        f"wait after each request: {metadata['wait_s']} s.",
        f"- Warm-up after all users spawn: {metadata['warmup_s']} s.",
        f"- Measurement window: {metadata['duration_s']} s; "
        f"matched repeats: {metadata['repeats']}.",
        "- Each workload reads a fixed endpoint on an isolated, identically seeded schema.",
        "- Healthy/faulty order is randomized within each pair with a recorded seed.",
        "- Each trial restarts both services; all trace sampling is enabled in both variants.",
        "- Only requests starting and completing inside the measurement window are counted.",
        "- Latency percentiles use successful requests and the nearest-rank rule.",
        "- SQL timings cover cursor execution; they are not total database/ORM processing time.",
        "- These are local warm-cache microbenchmarks, not production capacity estimates.",
        "",
        "## Trials",
        "",
        "| Workload | Repeat | Variant | Requests | Errors | p50 ms | p95 ms | "
        "Success RPS | SQL/request | Shipping/request | Valid |",
        "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for trial in trials:
        value = trial["summary"]
        lines.append(
            f"| {trial['workload']} | {trial['repeat']} | {trial['variant']} | "
            f"{value['requests']} | {value['failures']} | {number(value['latency_ms']['p50'])} | "
            f"{number(value['latency_ms']['p95'])} | {number(value['successful_rps'])} | "
            f"{number(value['mean_sql_count'])} | {number(value['mean_shipping_calls'])} | "
            f"{'yes' if trial['valid'] else 'NO'} |"
        )
    lines.extend(["", "## Matched comparisons", ""])
    for workload, comparison in comparisons.items():
        if not comparison["valid"]:
            lines.append(f"- **{workload}: invalid comparison**: {comparison['reason']}")
            continue
        lines.append(
            f"- **{workload}**: median trial p95 healthy "
            f"{number(comparison['healthy_median_trial_p95_ms'])} ms, faulty "
            f"{number(comparison['faulty_median_trial_p95_ms'])} ms. Median paired p95 change "
            f"{number(comparison['median_paired_p95_change_percent'])}%. "
            f"All pairs faulty slower: {comparison['all_pairs_faulty_slower']}."
        )
    lines.extend(
        [
            "",
            "A positive change means the faulty variant was slower. We report matched-run",
            "differences descriptively, without significance claims or pooled trial percentiles.",
            "",
            "## Validity and limitations",
            "",
            "Each valid trial completed its full window, kept the requested users, had at least 20",
            "completed requests, had no request-contract errors, preserved its sentinel response,",
            "and exported API trace evidence for every successful measured request.",
            "With fewer than 100 successes, tail percentiles are coarse; inspect trial warnings.",
            "Shared-machine load, warm database caches, tracing overhead, fixed request pacing,",
            "and this limited endpoint set constrain what these measurements establish.",
            "",
            "## Artifacts",
            "",
            "`results.json` records configuration, source fingerprint, full dataset fingerprints,",
            "and all comparisons. Each trial directory contains authoritative `samples.csv` and",
            "`summary.json`, secondary Locust CSVs/logs, and correlated JSONL traces.",
            "Locust CSV can include boundary requests; use explicit-window files for comparisons.",
        ]
    )
    (folder / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return result
