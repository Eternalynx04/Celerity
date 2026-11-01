"""Headless Locust entry point, loaded in its own monkey-patched process.

Only requests that START and FINISH inside the explicit measurement window enter
samples.csv/summary.json. Locust's own CSV is secondary diagnostic output.
"""

import csv
import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path

import gevent
from locust import HttpUser, constant, events, task

from celerity.benchmark.statistics import summarize
from celerity.benchmark.workloads import WORKLOADS, read_measurement_headers, validate_response

WORKLOAD = os.environ["CELERITY_BENCH_WORKLOAD"]
RUN_ID = os.environ["CELERITY_BENCH_RUN_ID"]
OUTPUT = Path(os.environ["CELERITY_BENCH_OUTPUT"])
WARMUP = float(os.environ["CELERITY_BENCH_WARMUP"])
DURATION = float(os.environ["CELERITY_BENCH_DURATION"])
WAIT = float(os.environ["CELERITY_BENCH_WAIT"])
STATE = {"start": None, "end": None, "active": False, "samples": [], "complete": False}


class ShoppingUser(HttpUser):
    wait_time = constant(WAIT)

    @task
    def read_only_request(self):
        context = {"started": time.perf_counter(), "measurements": {}}
        with self.client.get(
            WORKLOADS[WORKLOAD]["path"],
            name=WORKLOAD,
            headers={"X-Celerity-Run-Id": RUN_ID},
            context=context,
            catch_response=True,
            timeout=5,
        ) as response:
            try:
                if response.status_code != 200:
                    raise ValueError(f"HTTP {response.status_code}")
                validate_response(WORKLOAD, response.json())
                context["measurements"].update(read_measurement_headers(response.headers))
            except (ValueError, KeyError, TypeError) as exc:
                response.failure(f"Response contract failed: {type(exc).__name__}")


@events.request.add_listener
def record_request(response_time, response_length, context, exception, **_kwargs):
    if not STATE["active"] or context.get("started", 0) < STATE["start"]:
        return
    values = context.get("measurements", {})
    STATE["samples"].append(
        {
            "elapsed_s": time.perf_counter() - STATE["start"],
            "response_ms": response_time,
            "bytes": response_length,
            "success": exception is None,
            "error_type": type(exception).__name__ if exception else "",
            "sql_count": values.get("sql_count", 0),
            "sql_ms": values.get("sql_ms", 0.0),
            "shipping_calls": values.get("shipping_calls", 0),
            "shipping_ms": values.get("shipping_ms", 0.0),
            "trace_id": values.get("trace_id", ""),
        }
    )


@events.spawning_complete.add_listener
def start_window(user_count, **_kwargs):
    if STATE.get("scheduled"):
        return
    STATE["scheduled"] = True

    def measure():
        gevent.sleep(WARMUP)
        STATE["environment"].stats.reset_all()
        STATE["start"] = time.perf_counter()
        STATE["started_at"] = datetime.now(UTC).isoformat()
        STATE["users_at_start"] = STATE["environment"].runner.user_count
        STATE["active"] = True
        gevent.sleep(DURATION)
        STATE["active"] = False
        STATE["end"] = time.perf_counter()
        STATE["complete"] = True
        STATE["environment"].runner.quit()

    gevent.spawn(measure)


@events.init.add_listener
def initialize(environment, **_kwargs):
    STATE["environment"] = environment


@events.quitting.add_listener
def save_results(environment, **_kwargs):
    STATE["active"] = False
    OUTPUT.mkdir(parents=True, exist_ok=True)
    samples = STATE["samples"]
    fields = [
        "elapsed_s",
        "response_ms",
        "bytes",
        "success",
        "error_type",
        "sql_count",
        "sql_ms",
        "shipping_calls",
        "shipping_ms",
        "trace_id",
    ]
    with (OUTPUT / "samples.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(samples)
    duration = STATE["end"] - STATE["start"] if STATE["complete"] else 0
    result = {
        "run_id": RUN_ID,
        "workload": WORKLOAD,
        "complete": STATE["complete"],
        "warmup_s": WARMUP,
        "users_at_start": STATE.get("users_at_start"),
        "measurement_started_at": STATE.get("started_at"),
        "measurement": summarize(samples, duration) if duration > 0 else None,
        "window_rule": "Only requests starting and completing inside the measurement window.",
    }
    (OUTPUT / "summary.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    if not STATE["complete"] or not samples or any(not row["success"] for row in samples):
        environment.process_exit_code = 1
