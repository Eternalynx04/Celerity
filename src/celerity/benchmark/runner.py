"""Own isolated benchmark processes and data; never stop the normal port-8000 lab."""

import argparse
import hashlib
import json
import math
import os
import platform
import random
import re
import socket
import subprocess
import sys
import time
import uuid
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path

import httpx
import psutil
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import make_url

from celerity.benchmark.report import write_report
from celerity.benchmark.workloads import WORKLOADS, response_digest, validate_response
from celerity.config import Settings
from celerity.database import Base, configure_order_index, make_engine
from celerity.models import Customer, Order, OrderItem, Product, ProductImage
from celerity.seed import seed_demo

ROOT = Path.cwd().resolve()
QUIET = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
FAULT_ENV = {
    "n-plus-one": "CELERITY_ENABLE_N_PLUS_ONE",
    "missing-index": "CELERITY_ENABLE_MISSING_INDEX",
    "redundant-shipping": "CELERITY_ENABLE_REDUNDANT_SHIPPING",
}


def require_free_port(port: int) -> None:
    with socket.socket() as connection:
        if connection.connect_ex(("127.0.0.1", port)) == 0:
            raise RuntimeError(f"Benchmark port {port} is already occupied; choose another port.")


def fingerprint_dataset(engine) -> str:
    digest = hashlib.sha256()
    with engine.connect() as connection:
        for model in (Product, ProductImage, Customer, Order, OrderItem):
            digest.update(model.__tablename__.encode())
            for row in connection.execute(select(model.__table__).order_by(model.id)):
                digest.update(json.dumps(list(row), default=str, separators=(",", ":")).encode())
                digest.update(b"\n")
    return digest.hexdigest()


def fingerprint_source() -> str:
    digest = hashlib.sha256()
    paths = [
        *ROOT.glob("src/celerity/**/*.py"),
        ROOT / "benchmarks/locustfile.py",
        ROOT / "requirements.lock",
    ]
    for path in sorted(paths):
        digest.update(path.relative_to(ROOT).as_posix().encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def trace_coverage(folder: Path, samples_path: Path, run_id: str) -> dict:
    import csv

    with samples_path.open(newline="", encoding="utf-8") as handle:
        ids = {row["trace_id"] for row in csv.DictReader(handle) if row["success"] == "True"}
    servers = set()
    services = set()
    span_count = 0
    for path in folder.glob("*.jsonl"):
        for line in path.read_text(encoding="utf-8").splitlines():
            span = json.loads(line)
            if span["attributes"].get("celerity.run_id") != run_id:
                continue
            span_count += 1
            services.add(span["service"])
            if span["kind"] == "SERVER" and span["service"] == "celerity-api":
                servers.add(span["trace_id"])
    return {
        "successful_sample_trace_ids": len(ids),
        "matched_api_traces": len(ids & servers),
        "coverage": len(ids & servers) / len(ids) if ids else 0,
        "exported_run_spans": span_count,
        "services": sorted(services),
    }


def start_services(environment: dict, folder: Path, api_port: int, shipping_port: int):
    processes = []
    handles = []
    stops = []
    try:
        for service, port in (("shipping", shipping_port), ("api", api_port)):
            require_free_port(port)
            stop = folder / f"stop-{service}"
            stops.append(stop)
            log = (folder / f"{service}.log").open("w", encoding="utf-8")
            handles.append(log)
            child = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "celerity.serve",
                    "--service",
                    service,
                    "--port",
                    str(port),
                    "--stop-file",
                    str(stop),
                ],
                cwd=ROOT,
                env=environment,
                stdout=log,
                stderr=subprocess.STDOUT,
                creationflags=QUIET,
            )
            processes.append(child)
            with httpx.Client(trust_env=False, timeout=1) as client:
                for _ in range(150):
                    if child.poll() is not None:
                        raise RuntimeError(f"{service} exited; inspect {log.name}.")
                    try:
                        if client.get(f"http://127.0.0.1:{port}/health").status_code == 200:
                            break
                    except httpx.HTTPError:
                        pass
                    time.sleep(0.1)
                else:
                    raise RuntimeError(f"Timed out starting {service}.")
        return processes, handles, stops
    except BaseException:
        stop_services(processes, handles, stops)
        raise


def stop_services(processes, handles, stops) -> bool:
    forced = False
    for stop in stops:
        stop.write_text("stop", encoding="utf-8")
    for process in reversed(processes):
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            forced = True
            process.kill()
            process.wait()
    for handle in handles:
        handle.close()
    for stop in stops:
        stop.unlink(missing_ok=True)
    return forced


def run_trial(args, metadata, engine, isolated_url, folder, workload, repeat, variant) -> dict:
    run_id = f"{metadata['suite_id']}-{workload}-{repeat}-{variant}"
    folder.mkdir(parents=True)
    missing = variant == "faulty" and WORKLOADS[workload]["fault"] == "missing-index"
    configure_order_index(engine, missing=missing)
    environment = os.environ.copy()
    environment.update({name: "false" for name in FAULT_ENV.values()})
    if variant == "faulty":
        environment[FAULT_ENV[WORKLOADS[workload]["fault"]]] = "true"
    environment.update(
        {
            "CELERITY_DATABASE_URL": isolated_url,
            "CELERITY_SHIPPING_URL": f"http://127.0.0.1:{args.shipping_port}",
            "CELERITY_SHIPPING_DELAY_MS": str(metadata["shipping_delay_ms"]),
            "CELERITY_TELEMETRY_ENABLED": "true",
            "CELERITY_TELEMETRY_SAMPLE_RATIO": "1.0",
            "CELERITY_TELEMETRY_DIR": str(folder / "traces"),
            "CELERITY_OTLP_ENDPOINT": args.otlp_endpoint,
            "CELERITY_BENCH_WORKLOAD": workload,
            "CELERITY_BENCH_RUN_ID": run_id,
            "CELERITY_BENCH_OUTPUT": str(folder),
            "CELERITY_BENCH_WARMUP": str(args.warmup),
            "CELERITY_BENCH_DURATION": str(args.duration),
            "CELERITY_BENCH_WAIT": str(args.wait),
        }
    )
    processes, handles, stops = start_services(
        environment, folder, args.api_port, args.shipping_port
    )
    worker = None
    forced = False
    try:
        with httpx.Client(
            base_url=f"http://127.0.0.1:{args.api_port}", trust_env=False, timeout=5
        ) as client:
            before = client.get(WORKLOADS[workload]["path"])
            before.raise_for_status()
            validate_response(workload, before.json())
            response_sha256 = response_digest(before.json())
            scenario = client.get("/lab/scenario").json()
            with (folder / "locust.log").open("w", encoding="utf-8") as log:
                command = [
                    sys.executable,
                    "-m",
                    "locust",
                    "-f",
                    str(ROOT / "benchmarks/locustfile.py"),
                    "--headless",
                    "--host",
                    f"http://127.0.0.1:{args.api_port}",
                    "--users",
                    str(args.users),
                    "--spawn-rate",
                    str(args.users),
                    "--run-time",
                    str(math.ceil(args.duration + args.warmup + 25)),
                    "--stop-timeout",
                    "3",
                    "--only-summary",
                    "--exit-code-on-error",
                    "1",
                    "--csv",
                    str(folder / "locust"),
                ]
                worker = subprocess.Popen(
                    command,
                    cwd=ROOT,
                    env=environment,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    creationflags=QUIET,
                )
                worker.wait(timeout=args.duration + args.warmup + 45)
            after = client.get(WORKLOADS[workload]["path"])
            after.raise_for_status()
            validate_response(workload, after.json())
            unchanged = response_digest(after.json()) == response_sha256
    finally:
        if worker is not None and worker.poll() is None:
            worker.kill()
            worker.wait()
        forced = stop_services(processes, handles, stops)
    summary_path = folder / "summary.json"
    if not summary_path.exists():
        raise RuntimeError(f"Locust did not produce a summary; inspect {folder / 'locust.log'}.")
    result = json.loads(summary_path.read_text(encoding="utf-8"))
    if result["measurement"] is None:
        raise RuntimeError(f"Measurement window did not complete: {summary_path}")
    summary = result["measurement"]
    coverage = trace_coverage(folder / "traces", folder / "samples.csv", run_id)
    valid = bool(
        worker.returncode == 0
        and result["complete"]
        and unchanged
        and not forced
        and result["users_at_start"] == args.users
        and summary["requests"] >= 20
        and summary["failures"] == 0
        and coverage["coverage"] == 1.0
    )
    warnings = []
    if summary["successful_requests"] < 100:
        warnings.append("Fewer than 100 successes: tail percentiles are coarse.")
    if not valid:
        warnings.append(
            "Invalid trial; inspect errors, sample count, trace coverage, and process shutdown."
        )
    return {
        "run_id": run_id,
        "workload": workload,
        "repeat": repeat,
        "variant": variant,
        "valid": valid,
        "summary": summary,
        "trace_evidence": coverage,
        "response_sha256": response_sha256,
        "sentinel_unchanged": unchanged,
        "observed_scenario": scenario,
        "forced_shutdown": forced,
        "warnings": warnings,
        "artifacts": str(folder.relative_to(folder.parents[0])),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workload", choices=[*WORKLOADS, "all"], default="catalogue")
    parser.add_argument("--users", type=int, default=5)
    parser.add_argument("--duration", type=float, default=20)
    parser.add_argument("--warmup", type=float, default=5)
    parser.add_argument("--wait", type=float, default=0.05)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--api-port", type=int, default=8020)
    parser.add_argument("--shipping-port", type=int, default=8021)
    parser.add_argument("--otlp-endpoint", default="")
    args = parser.parse_args()
    if not (
        1 <= args.users <= 50
        and 1 <= args.duration <= 600
        and 0 <= args.warmup <= 120
        and 0 <= args.wait <= 5
        and 1 <= args.repeats <= 20
    ):
        parser.error(
            "Use 1..50 users, 1..600s duration, 0..120s warmup, 0..5s wait, 1..20 repeats."
        )
    if args.api_port == args.shipping_port or not all(
        1024 <= p <= 65535 for p in (args.api_port, args.shipping_port)
    ):
        parser.error("Use distinct, unprivileged API/shipping ports.")
    if not (ROOT / "benchmarks/locustfile.py").exists():
        parser.error("Run this command from the Celerity project folder.")
    require_free_port(args.api_port)
    require_free_port(args.shipping_port)
    settings = Settings()
    base_url = make_url(settings.database_url)
    if base_url.host not in {"127.0.0.1", "localhost", "db"} or base_url.database != "celerity":
        parser.error("Benchmarks require the local Celerity lab database.")
    suite_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:6]
    folder = ROOT / "var" / "benchmarks" / suite_id
    folder.mkdir(parents=True)
    schema = f"bench_celerity_{uuid.uuid4().hex[:12]}"
    assert re.fullmatch(r"bench_celerity_[0-9a-f]{12}", schema)
    admin = create_engine(base_url)
    isolated_url = base_url.update_query_dict({"options": f"-csearch_path={schema}"})
    engine = make_engine(isolated_url.render_as_string(hide_password=False))
    trials = []
    metadata = {
        "suite_id": suite_id,
        "users": args.users,
        "duration_s": args.duration,
        "warmup_s": args.warmup,
        "wait_s": args.wait,
        "repeats": args.repeats,
        "random_seed": args.seed,
        "trace_sample_ratio": 1.0,
        "shipping_delay_ms": settings.shipping_delay_ms,
        "platform": platform.platform(),
        "python": platform.python_version(),
        "logical_cpus": os.cpu_count(),
        "physical_memory_bytes": psutil.virtual_memory().total,
        "packages": {
            name: version(name) for name in ("locust", "fastapi", "sqlalchemy", "opentelemetry-sdk")
        },
        "source_sha256": fingerprint_source(),
        "schema": schema,
        "status": "running",
        "trial_order": [],
    }
    schema_created = False
    try:
        with admin.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        schema_created = True
        Base.metadata.create_all(engine)
        metadata["dataset_rows"] = seed_demo(engine)
        metadata["dataset_sha256_before"] = fingerprint_dataset(engine)
        with engine.connect() as connection:
            metadata["postgresql"] = connection.scalar(text("SHOW server_version"))
        randomizer = random.Random(args.seed)
        workloads = list(WORKLOADS) if args.workload == "all" else [args.workload]
        print(
            f"Suite {suite_id}: {len(workloads) * args.repeats * 2} trials. Artifacts: {folder}",
            flush=True,
        )
        for workload in workloads:
            for repeat in range(1, args.repeats + 1):
                order = ["healthy", "faulty"]
                randomizer.shuffle(order)
                for variant in order:
                    name = f"{workload}-{repeat}-{variant}"
                    metadata["trial_order"].append(name)
                    print(f"Starting {name}...", flush=True)
                    trial = run_trial(
                        args,
                        metadata,
                        engine,
                        isolated_url.render_as_string(hide_password=False),
                        folder / name,
                        workload,
                        repeat,
                        variant,
                    )
                    trials.append(trial)
                    print(
                        f"  {'PASS' if trial['valid'] else 'INVALID'}: "
                        f"{trial['summary']['requests']} requests, "
                        f"p95={trial['summary']['latency_ms']['p95']} ms",
                        flush=True,
                    )
                    write_report(folder, metadata, trials)
        metadata["dataset_sha256_after"] = fingerprint_dataset(engine)
        metadata["dataset_unchanged"] = (
            metadata["dataset_sha256_before"] == metadata["dataset_sha256_after"]
        )
        metadata["source_sha256_after"] = fingerprint_source()
        metadata["source_unchanged"] = metadata["source_sha256"] == metadata["source_sha256_after"]
        metadata["status"] = (
            "complete"
            if metadata["dataset_unchanged"]
            and metadata["source_unchanged"]
            and all(t["valid"] for t in trials)
            else "invalid"
        )
    except BaseException as exc:
        metadata["status"] = "interrupted" if isinstance(exc, KeyboardInterrupt) else "failed"
        metadata["failure_type"] = type(exc).__name__
        raise
    finally:
        engine.dispose()
        if schema_created:
            with admin.begin() as connection:
                connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()
        metadata["schema_removed"] = schema_created
        write_report(folder, metadata, trials)
    print(f"Report: {folder / 'report.md'}", flush=True)
    if metadata["status"] != "complete":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
